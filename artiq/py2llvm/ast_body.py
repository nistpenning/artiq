import ast

from artiq.py2llvm import values, base_types, fractions
from artiq.py2llvm.tools import is_terminated


class Visitor:
    def __init__(self, env, ns, builder=None):
        self.env = env
        self.ns = ns
        self.builder = builder

    # builder can be None for visit_expression
    def visit_expression(self, node):
        method = "_visit_expr_" + node.__class__.__name__
        try:
            visitor = getattr(self, method)
        except AttributeError:
            raise NotImplementedError("Unsupported node '{}' in expression"
                                      .format(node.__class__.__name__))
        return visitor(node)

    def _visit_expr_Name(self, node):
        try:
            r = self.ns[node.id]
        except KeyError:
            raise NameError("Name '{}' is not defined".format(node.id))
        return r

    def _visit_expr_NameConstant(self, node):
        v = node.value
        if v is None:
            r = base_types.VNone()
        elif isinstance(v, bool):
            r = base_types.VBool()
        else:
            raise NotImplementedError
        if self.builder is not None:
            r.set_const_value(self.builder, v)
        return r

    def _visit_expr_Num(self, node):
        n = node.n
        if isinstance(n, int):
            if abs(n) < 2**31:
                r = base_types.VInt()
            else:
                r = base_types.VInt(64)
        else:
            raise NotImplementedError
        if self.builder is not None:
            r.set_const_value(self.builder, n)
        return r

    def _visit_expr_UnaryOp(self, node):
        ast_unops = {
            ast.Invert: "o_inv",
            ast.Not: "o_not",
            ast.UAdd: "o_pos",
            ast.USub: "o_neg"
        }
        value = self.visit_expression(node.operand)
        return getattr(value, ast_unops[type(node.op)])(self.builder)

    def _visit_expr_BinOp(self, node):
        ast_binops = {
            ast.Add: values.operators.add,
            ast.Sub: values.operators.sub,
            ast.Mult: values.operators.mul,
            ast.Div: values.operators.truediv,
            ast.FloorDiv: values.operators.floordiv,
            ast.Mod: values.operators.mod,
            ast.Pow: values.operators.pow,
            ast.LShift: values.operators.lshift,
            ast.RShift: values.operators.rshift,
            ast.BitOr: values.operators.or_,
            ast.BitXor: values.operators.xor,
            ast.BitAnd: values.operators.and_
        }
        return ast_binops[type(node.op)](self.visit_expression(node.left),
                                         self.visit_expression(node.right),
                                         self.builder)

    def _visit_expr_Compare(self, node):
        ast_cmps = {
            ast.Eq: values.operators.eq,
            ast.NotEq: values.operators.ne,
            ast.Lt: values.operators.lt,
            ast.LtE: values.operators.le,
            ast.Gt: values.operators.gt,
            ast.GtE: values.operators.ge
        }
        comparisons = []
        old_comparator = self.visit_expression(node.left)
        for op, comparator_a in zip(node.ops, node.comparators):
            comparator = self.visit_expression(comparator_a)
            comparison = ast_cmps[type(op)](old_comparator, comparator,
                                            self.builder)
            comparisons.append(comparison)
            old_comparator = comparator
        r = comparisons[0]
        for comparison in comparisons[1:]:
            r = values.operators.and_(r, comparison)
        return r

    def _visit_expr_Call(self, node):
        fn = node.func.id
        if fn in {"bool", "int", "int64", "round", "round64"}:
            value = self.visit_expression(node.args[0])
            return getattr(value, "o_"+fn)(self.builder)
        elif fn == "Fraction":
            r = fractions.VFraction()
            if self.builder is not None:
                numerator = self.visit_expression(node.args[0])
                denominator = self.visit_expression(node.args[1])
                r.set_value_nd(self.builder, numerator, denominator)
            return r
        elif fn == "syscall":
            return self.env.syscall(
                node.args[0].s,
                [self.visit_expression(expr) for expr in node.args[1:]],
                self.builder)
        else:
            raise NameError("Function '{}' is not defined".format(fn))

    def _visit_expr_Attribute(self, node):
        value = self.visit_expression(node.value)
        return value.o_getattr(node.attr, self.builder)

    def visit_statements(self, stmts):
        for node in stmts:
            node_type = node.__class__.__name__
            method = "_visit_stmt_" + node_type
            try:
                visitor = getattr(self, method)
            except AttributeError:
                raise NotImplementedError("Unsupported node '{}' in statement"
                                          .format(node_type))
            visitor(node)
            if node_type == "Return":
                break

    def _visit_stmt_Assign(self, node):
        val = self.visit_expression(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.ns[target.id].set_value(self.builder, val)
            else:
                raise NotImplementedError

    def _visit_stmt_AugAssign(self, node):
        val = self.visit_expression(ast.BinOp(op=node.op, left=node.target,
                                              right=node.value))
        if isinstance(node.target, ast.Name):
            self.ns[node.target.id].set_value(self.builder, val)
        else:
            raise NotImplementedError

    def _visit_stmt_Expr(self, node):
        self.visit_expression(node.value)

    def _visit_stmt_If(self, node):
        function = self.builder.basic_block.function
        then_block = function.append_basic_block("i_then")
        else_block = function.append_basic_block("i_else")
        merge_block = function.append_basic_block("i_merge")

        condition = self.visit_expression(node.test).o_bool(self.builder)
        self.builder.cbranch(condition.get_ssa_value(self.builder),
                             then_block, else_block)

        self.builder.position_at_end(then_block)
        self.visit_statements(node.body)
        if not is_terminated(self.builder.basic_block):
            self.builder.branch(merge_block)

        self.builder.position_at_end(else_block)
        self.visit_statements(node.orelse)
        if not is_terminated(self.builder.basic_block):
            self.builder.branch(merge_block)

        self.builder.position_at_end(merge_block)

    def _visit_stmt_While(self, node):
        function = self.builder.basic_block.function
        body_block = function.append_basic_block("w_body")
        else_block = function.append_basic_block("w_else")
        merge_block = function.append_basic_block("w_merge")

        condition = self.visit_expression(node.test).o_bool(self.builder)
        self.builder.cbranch(
            condition.get_ssa_value(self.builder), body_block, else_block)

        self.builder.position_at_end(body_block)
        self.visit_statements(node.body)
        if not is_terminated(self.builder.basic_block):
            condition = self.visit_expression(node.test).o_bool(self.builder)
            self.builder.cbranch(
                condition.get_ssa_value(self.builder), body_block, merge_block)

        self.builder.position_at_end(else_block)
        self.visit_statements(node.orelse)
        if not is_terminated(self.builder.basic_block):
            self.builder.branch(merge_block)

        self.builder.position_at_end(merge_block)

    def _visit_stmt_Return(self, node):
        if node.value is None:
            val = base_types.VNone()
        else:
            val = self.visit_expression(node.value)
        if isinstance(val, base_types.VNone):
            self.builder.ret_void()
        else:
            self.builder.ret(val.get_ssa_value(self.builder))