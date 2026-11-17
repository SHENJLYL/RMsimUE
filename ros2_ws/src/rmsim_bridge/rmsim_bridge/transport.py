"""Nonblocking loopback-only transport; incomplete packets are never treated as snapshots."""
from collections import deque
import errno
import select
import socket
import time
from .protocol import PacketReader, ProtocolError


class LoopbackClient:
    def __init__(self, port=9451):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('port must be 1024..65535')
        self.port = port
        self.socket = None
        self.connected = False
        self.reader = PacketReader()
        self.outbound = deque()
        self.sent = 0
        self.retry_at = 0.
        self.started_at = 0.
        self.connection_number = 0
        self.last_error = ''

    def close(self, error=''):
        if self.socket is not None:
            self.socket.close()
        self.socket = None
        self.connected = False
        self.reader = PacketReader()
        self.outbound.clear()
        self.sent = 0
        self.retry_at = time.monotonic() + 1.
        self.last_error = error

    def queue(self, raw):
        if not self.connected:
            return False
        if not isinstance(raw, bytes) or len(raw) > 4096 or not raw.endswith(b'\n'):
            raise ValueError('invalid command encoding')
        if len(self.outbound) >= 8:
            self.close('command queue stalled')
            return False
        self.outbound.append(raw)
        return True

    def poll(self):
        now = time.monotonic()
        try:
            if self.socket is None:
                if now < self.retry_at:
                    return []
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.setblocking(False)
                self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                result = self.socket.connect_ex(('127.0.0.1', self.port))
                if result not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, 10035, 10036, 10037):
                    raise OSError(result, 'loopback connection failed')
                self.started_at = now
            if not self.connected:
                _, ready, exceptional = select.select([], [self.socket], [self.socket], 0)
                if exceptional or now - self.started_at > 2:
                    raise OSError('loopback connection timed out or failed')
                if not ready:
                    return []
                error = self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error:
                    raise OSError(error, 'loopback connection failed')
                self.connected = True
                self.connection_number += 1
                self.last_error = ''
            packets = []
            # Bounded work per node tick, without discarding an incomplete RGB frame.
            for _ in range(64):
                try:
                    data = self.socket.recv(65536)
                except BlockingIOError:
                    break
                if not data:
                    self.close('server disconnected')
                    return []
                packets.extend(self.reader.feed(data))
            while self.outbound:
                try:
                    count = self.socket.send(self.outbound[0][self.sent:])
                except BlockingIOError:
                    break
                if count == 0:
                    raise OSError('server disconnected during send')
                self.sent += count
                if self.sent == len(self.outbound[0]):
                    self.outbound.popleft()
                    self.sent = 0
            return packets
        except (OSError, ProtocolError) as error:
            self.close(str(error))
            return []
