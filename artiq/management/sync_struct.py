import asyncio

from artiq.management import pyon
from artiq.management.tools import AsyncioServer


_init_string = b"ARTIQ sync_struct\n"


class Subscriber:
    def __init__(self, notifier_name, target_builder, notify_cb=None):
        self.notifier_name = notifier_name
        self.target_builder = target_builder
        self.notify_cb = notify_cb

    @asyncio.coroutine
    def connect(self, host, port):
        self._reader, self._writer = \
            yield from asyncio.open_connection(host, port)
        try:
            self._writer.write(_init_string)
            self._writer.write((self.notifier_name + "\n").encode())
            self.receive_task = asyncio.Task(self._receive_cr())
        except:
            self._writer.close()
            del self._reader
            del self._writer
            raise

    @asyncio.coroutine
    def close(self):
        try:
            self.receive_task.cancel()
            try:
                yield from asyncio.wait_for(self.receive_task, None)
            except asyncio.CancelledError:
                pass
        finally:
            self._writer.close()
            del self._reader
            del self._writer

    @asyncio.coroutine
    def _receive_cr(self):
        target = None
        while True:
            line = yield from self._reader.readline()
            if not line:
                return
            obj = pyon.decode(line.decode())
            action = obj["action"]

            if action == "init":
                target = self.target_builder(obj["struct"])
            elif action == "append":
                target.append(obj["x"])
            elif action == "insert":
                target.insert(obj["i"], obj["x"])
            elif action == "pop":
                target.pop(obj["i"])
            elif action == "setitem":
                target.__setitem__(obj["key"], obj["value"])
            elif action == "delitem":
                target.__delitem__(obj["key"])

            if self.notify_cb is not None:
                self.notify_cb()


class Notifier:
    def __init__(self, backing_struct):
        self.backing_struct = backing_struct
        self.publisher = None

    # Backing struct modification methods.
    # All modifications must go through them!

    def append(self, x):
        self.backing_struct.append(x)
        if self.publisher is not None:
            self.publisher.publish(self, {"action": "append", "x": x})

    def insert(self, i, x):
        self.backing_struct.insert(i, x)
        if self.publisher is not None:
            self.publisher.publish(self, {"action": "insert", "i": i, "x": x})

    def pop(self, i=-1):
        r = self.backing_struct.pop(i)
        if self.publisher is not None:
            self.publisher.publish(self, {"action": "pop", "i": i})
        return r

    def __setitem__(self, key, value):
        self.backing_struct.__setitem__(key, value)
        if self.publisher is not None:
            self.publisher.publish(self, {"action": "setitem",
                                          "key": key,
                                          "value": value})

    def __delitem__(self, key):
        self.backing_struct.__delitem__(key)
        if self.publisher is not None:
            self.publisher.publish(self, {"action": "delitem", "key": key})


class Publisher(AsyncioServer):
    def __init__(self, notifiers):
        AsyncioServer.__init__(self)
        self.notifiers = notifiers
        self._recipients = {k: set() for k in notifiers.keys()}
        self._notifier_names = {id(v): k for k, v in notifiers.items()}

        for notifier in notifiers.values():
            notifier.publisher = self

    @asyncio.coroutine
    def _handle_connection_cr(self, reader, writer):
        try:
            line = yield from reader.readline()
            if line != _init_string:
                return

            line = yield from reader.readline()
            if not line:
                return
            notifier_name = line.decode()[:-1]

            try:
                notifier = self.notifiers[notifier_name]
            except KeyError:
                return

            obj = {"action": "init", "struct": notifier.backing_struct}
            line = pyon.encode(obj) + "\n"
            writer.write(line.encode())

            queue = asyncio.Queue()
            self._recipients[notifier_name].add(queue)
            try:
                while True:
                    line = yield from queue.get()
                    writer.write(line)
                    # raise exception on connection error
                    yield from writer.drain()
            finally:
                self._recipients[notifier_name].remove(queue)
        except ConnectionResetError:
            # subscribers disconnecting are a normal occurence
            pass
        finally:
            writer.close()

    def publish(self, notifier, obj):
        line = pyon.encode(obj) + "\n"
        line = line.encode()
        notifier_name = self._notifier_names[id(notifier)]
        for recipient in self._recipients[notifier_name]:
            recipient.put_nowait(line)