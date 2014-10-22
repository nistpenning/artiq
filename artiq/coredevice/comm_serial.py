import os
import termios
import struct
import zlib
from enum import Enum
from fractions import Fraction
import logging

from artiq.language import core as core_language
from artiq.coredevice.runtime import Environment
from artiq.coredevice import runtime_exceptions


logger = logging.getLogger(__name__)


class UnsupportedDevice(Exception):
    pass


class _H2DMsgType(Enum):
    REQUEST_IDENT = 1
    LOAD_OBJECT = 2
    RUN_KERNEL = 3


class _D2HMsgType(Enum):
    LOG = 1
    MESSAGE_UNRECOGNIZED = 2
    IDENT = 3
    OBJECT_LOADED = 4
    INCORRECT_LENGTH = 5
    CRC_FAILED = 6
    OBJECT_UNRECOGNIZED = 7
    KERNEL_FINISHED = 8
    KERNEL_EXCEPTION = 9
    KERNEL_STARTUP_FAILED = 10
    RPC_REQUEST = 11


def _write_exactly(f, data):
    remaining = len(data)
    pos = 0
    while remaining:
        written = f.write(data[pos:])
        remaining -= written
        pos += written


def _read_exactly(f, n):
    r = bytes()
    while(len(r) < n):
        r += f.read(n - len(r))
    return r


class Comm:
    def __init__(self, dev="/dev/ttyUSB1", baud=115200):
        self._fd = os.open(dev, os.O_RDWR | os.O_NOCTTY)
        self.port = os.fdopen(self._fd, "r+b", buffering=0)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = \
            termios.tcgetattr(self._fd)
        iflag = termios.IGNBRK | termios.IGNPAR
        oflag = 0
        cflag |= termios.CLOCAL | termios.CREAD | termios.CS8
        lflag = 0
        ispeed = ospeed = getattr(termios, "B"+str(baud))
        cc[termios.VMIN] = 1
        cc[termios.VTIME] = 0
        termios.tcsetattr(self._fd, termios.TCSANOW, [
            iflag, oflag, cflag, lflag, ispeed, ospeed, cc])
        termios.tcdrain(self._fd)
        termios.tcflush(self._fd, termios.TCOFLUSH)
        termios.tcflush(self._fd, termios.TCIFLUSH)
        logger.debug("connected to {} at {} baud".format(dev, baud))

    def close(self):
        self.port.close()

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()

    def _get_device_msg(self):
        while True:
            # FIXME: when loading immediately after a board reset,
            # we erroneously get some zeros back.
            # Ignore them with a warning for now.
            spurious_zero_count = 0
            while True:
                (reply, ) = struct.unpack("B", _read_exactly(self.port, 1))
                if reply == 0:
                    spurious_zero_count += 1
                else:
                    break
            if spurious_zero_count:
                logger.warning("received {} spurious zeros".format(
                    spurious_zero_count))
            msg = _D2HMsgType(reply)
            if msg == _D2HMsgType.LOG:
                (length, ) = struct.unpack(">h", _read_exactly(self.port, 2))
                log_message = ""
                for i in range(length):
                    (c, ) = struct.unpack("B", _read_exactly(self.port, 1))
                    log_message += chr(c)
                logger.info("DEVICE LOG: " + log_message)
            else:
                logger.debug("message received: {!r}".format(msg))
                return msg

    def get_runtime_env(self):
        _write_exactly(self.port, struct.pack(
            ">lb", 0x5a5a5a5a, _H2DMsgType.REQUEST_IDENT.value))
        msg = self._get_device_msg()
        if msg != _D2HMsgType.IDENT:
            raise IOError("Incorrect reply from device: "+str(msg))
        (reply, ) = struct.unpack("B", _read_exactly(self.port, 1))
        runtime_id = chr(reply)
        for i in range(3):
            (reply, ) = struct.unpack("B", _read_exactly(self.port, 1))
            runtime_id += chr(reply)
        if runtime_id != "AROR":
            raise UnsupportedDevice("Unsupported runtime ID: "+runtime_id)
        (ref_freq_i, ref_freq_fn, ref_freq_fd) = struct.unpack(
            ">lBB", _read_exactly(self.port, 6))
        ref_period = 1/(ref_freq_i + Fraction(ref_freq_fn, ref_freq_fd))
        logger.debug("environment ref_period: {}".format(ref_period))
        return Environment(ref_period)

    def load(self, kcode):
        _write_exactly(self.port, struct.pack(
            ">lblL",
            0x5a5a5a5a, _H2DMsgType.LOAD_OBJECT.value,
            len(kcode), zlib.crc32(kcode)))
        _write_exactly(self.port, kcode)
        msg = self._get_device_msg()
        if msg != _D2HMsgType.OBJECT_LOADED:
            raise IOError("Incorrect reply from device: "+str(msg))

    def run(self, kname):
        _write_exactly(self.port, struct.pack(
            ">lbl", 0x5a5a5a5a, _H2DMsgType.RUN_KERNEL.value, len(kname)))
        for c in kname:
            _write_exactly(self.port, struct.pack("B", ord(c)))
        logger.debug("running kernel: {}".format(kname))

    def serve(self, rpc_map, user_exception_map):
        while True:
            msg = self._get_device_msg()
            if msg == _D2HMsgType.RPC_REQUEST:
                rpc_num, n_args = struct.unpack(">hB",
                                                _read_exactly(self.port, 3))
                args = []
                for i in range(n_args):
                    args.append(*struct.unpack(">l",
                                               _read_exactly(self.port, 4)))
                logger.debug("rpc service: {} ({})".format(rpc_num, args))
                r = rpc_map[rpc_num](*args)
                if r is None:
                    r = 0
                _write_exactly(self.port, struct.pack(">l", r))
                logger.debug("rpc service: {} ({}) == {}".format(
                    rpc_num, args, r))
            elif msg == _D2HMsgType.KERNEL_EXCEPTION:
                (eid, ) = struct.unpack(">l", _read_exactly(self.port, 4))
                if eid < core_language.first_user_eid:
                    exception = runtime_exceptions.exception_map[eid]
                else:
                    exception = user_exception_map[eid]
                raise exception
            elif msg == _D2HMsgType.KERNEL_FINISHED:
                return
            else:
                raise IOError("Incorrect request from device: "+str(msg))