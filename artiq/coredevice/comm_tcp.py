import logging
import socket
import sys

from artiq.coredevice.comm_generic import CommGeneric


logger = logging.getLogger(__name__)


def set_keepalive(sock, after_idle, interval, max_fails):
    if sys.platform.startswith("linux"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, after_idle)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, max_fails)
    elif sys.platform.startswith("win") or sys.platform.startswith("cygwin"):
        # setting max_fails is not supported, typically ends up being 5 or 10
        # depending on Windows version
        sock.ioctl(socket.SIO_KEEPALIVE_VALS,
                   (1, after_idle*1000, interval*1000))
    else:
        logger.warning("TCP keepalive not supported on platform '%s', ignored",
                       sys.platform)


class Comm(CommGeneric):
    def __init__(self, dmgr, host, port=1381):
        self.host = host
        self.port = port

    def open(self):
        if hasattr(self, "socket"):
            return
        self.socket = socket.create_connection((self.host, self.port))
        set_keepalive(self.socket, 3, 2, 3)
        logger.debug("connected to host %s on port %d", self.host, self.port)
        self.write(b"ARTIQ coredev\n")

    def close(self):
        if not hasattr(self, "socket"):
            return
        self.socket.close()
        del self.socket
        logger.debug("disconnected")

    def read(self, length):
        r = bytes()
        while len(r) < length:
            rn = self.socket.recv(min(8192, length - len(r)))
            if not rn:
                raise IOError("Connection closed")
            r += rn
        return r

    def write(self, data):
        self.socket.sendall(data)
