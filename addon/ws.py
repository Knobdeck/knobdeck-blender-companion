# A minimal WebSocket client (RFC 6455) on the standard library: text frames, ping/pong, close.
# Blender bundles no WebSocket module and extensions must not pip-install, so this is all the
# add-on needs to reach KnobDeck's companion hub on 127.0.0.1. No TLS: loopback only.

import base64
import hashlib
import os
import socket
import struct

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA

MAX_MESSAGE = 1 << 20


class Closed(Exception):
    """The connection is gone."""


class Refused(Exception):
    """The server answered the upgrade with something other than 101."""


class Client:
    def __init__(self, sock):
        self._sock = sock
        self._buf = b""
        self._parts = []

    @classmethod
    def connect(cls, host, port, path, timeout=3.0):
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            # No Origin header: the hub tells native companions from web pages by it.
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(req.encode())
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = sock.recv(4096)
                if not chunk:
                    raise Refused("connection closed during the handshake")
                head += chunk
                if len(head) > 65536:
                    raise Refused("handshake answer too long")
            head, rest = head.split(b"\r\n\r\n", 1)
            lines = head.decode("latin-1").split("\r\n")
            status = lines[0].split(" ", 2)
            if len(status) < 2 or status[1] != "101":
                raise Refused(lines[0])
            headers = {}
            for line in lines[1:]:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
            want = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
            if headers.get("sec-websocket-accept") != want:
                raise Refused("bad Sec-WebSocket-Accept")
        except BaseException:
            sock.close()
            raise
        client = cls(sock)
        client._buf = rest
        return client

    def settimeout(self, t):
        self._sock.settimeout(t)

    def _send_frame(self, opcode, payload):
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        try:
            self._sock.sendall(head + mask + masked)
        except OSError as e:
            raise Closed(str(e)) from e

    def send_text(self, text):
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def _frame(self):
        """One complete frame from the buffer, or None."""
        b = self._buf
        if len(b) < 2:
            return None
        fin, opcode = b[0] & 0x80, b[0] & 0x0F
        masked, n = b[1] & 0x80, b[1] & 0x7F
        i = 2
        if n == 126:
            if len(b) < 4:
                return None
            n = struct.unpack("!H", b[2:4])[0]
            i = 4
        elif n == 127:
            if len(b) < 10:
                return None
            n = struct.unpack("!Q", b[2:10])[0]
            i = 10
        if n > MAX_MESSAGE:
            raise Closed("frame too large")
        mask = b""
        if masked:
            if len(b) < i + 4:
                return None
            mask = b[i : i + 4]
            i += 4
        if len(b) < i + n:
            return None
        payload = b[i : i + n]
        if mask:
            payload = bytes(c ^ mask[j & 3] for j, c in enumerate(payload))
        self._buf = b[i + n :]
        return bool(fin), opcode, payload

    def recv_text(self):
        """The next text message; None when the socket timeout passes first. Raises Closed."""
        while True:
            frame = self._frame()
            if frame is None:
                try:
                    chunk = self._sock.recv(65536)
                except socket.timeout:
                    return None
                except OSError as e:
                    raise Closed(str(e)) from e
                if not chunk:
                    raise Closed("connection closed")
                self._buf += chunk
                continue
            fin, opcode, payload = frame
            if opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
            elif opcode == OP_PONG:
                pass
            elif opcode == OP_CLOSE:
                try:
                    self._send_frame(OP_CLOSE, payload[:2])
                except Closed:
                    pass
                raise Closed("closed by the server")
            elif opcode in (OP_TEXT, OP_BINARY, OP_CONT):
                if opcode != OP_CONT:
                    self._parts = [(opcode, payload)]
                else:
                    self._parts.append((opcode, payload))
                if sum(len(p) for _, p in self._parts) > MAX_MESSAGE:
                    raise Closed("message too large")
                if fin:
                    first, data = self._parts[0][0], b"".join(p for _, p in self._parts)
                    self._parts = []
                    if first == OP_TEXT:
                        return data.decode("utf-8", "replace")

    def close(self):
        try:
            self._send_frame(OP_CLOSE, struct.pack("!H", 1000))
        except Closed:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
