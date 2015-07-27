# RUN: %python -m artiq.compiler.testbench.module +diag %s >%t
# RUN: OutputCheck %s --file-to-check=%t

x = 1
if x > 10:
    y = 1
# CHECK-L: ${LINE:+1}: error: variable 'y' is not always initialized
x + y

for z in [1]:
    pass
# CHECK-L: ${LINE:+1}: error: variable 'z' is not always initialized
-z

if True:
    pass
else:
    t = 1
# CHECK-L: ${LINE:+1}: error: variable 't' is not always initialized
-t

# CHECK-L: ${LINE:+1}: error: variable 't' can be captured in a closure uninitialized
l = lambda: t

# CHECK-L: ${LINE:+1}: error: variable 't' can be captured in a closure uninitialized
def f():
    return t