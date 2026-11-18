# A stand-in for KnobDeck's companion hub (Knobdeck/app/src-tauri/src/companion_hub.rs), on the
# standard library: the same discovery file, Host / Origin checks, hello admission (protocol,
# token, plugin), ready / refused, pong, request / response and events. Tests only.

import base64
import hashlib
import json
import os
import secrets
import socket
import struct
import tempfile
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _recv_frame(sock, buf):
    """(opcode, payload, rest) for one complete client frame; reads as needed."""
    while True:
        if len(buf) >= 2:
            n = buf[1] & 0x7F
            i = 2
            if n == 126 and len(buf) >= 4:
                n, i = struct.unpack("!H", buf[2:4])[0], 4
            elif n == 127 and len(buf) >= 10:
                n, i = struct.unpack("!Q", buf[2:10])[0], 10
            elif n >= 126:
                n = None
            if n is not None and buf[1] & 0x80 and len(buf) >= i + 4 + n:
                mask = buf[i : i + 4]
                payload = bytes(c ^ mask[j & 3] for j, c in enumerate(buf[i + 4 : i + 4 + n]))
                return buf[0] & 0x0F, payload, buf[i + 4 + n :]
            if n is not None and not buf[1] & 0x80:
                raise ConnectionError("client frame not masked")
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk


def _send_text(sock, text):
    data = text.encode()
    n = len(data)
    head = bytes([0x81]) + (
        bytes([n]) if n < 126 else bytes([126]) + struct.pack("!H", n) if n < 65536 else bytes([127]) + struct.pack("!Q", n)
    )
    sock.sendall(head + data)


class Conn:
    def __init__(self, sock, link, hello):
        self.sock, self.link, self.hello = sock, link, hello
        self.lock = threading.Lock()

    def send(self, msg):
        with self.lock:
            _send_text(self.sock, json.dumps(msg))


class FakeHub:
    def __init__(self, accepts=lambda plugin: True, on_connect=None, on_disconnect=None, on_event=None):
        self.accepts = accepts
        self.on_connect, self.on_disconnect, self.on_event = on_connect, on_disconnect, on_event
        self.token = secrets.token_hex(24)
        self.links = {}  # link id -> Conn
        self.order = []
        self.frames = []  # every frame companions sent
        self.refusals = []
        self._pending = {}
        self._next = 1
        self._lock = threading.Lock()
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self.port = self._server.getsockname()[1]
        self.dir = tempfile.mkdtemp(prefix="kd-hub-")
        self.discovery = os.path.join(self.dir, "companion-hub.json")
        self.write_discovery()
        self._closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    def write_discovery(self):
        with open(self.discovery, "w", encoding="utf-8") as f:
            json.dump({"protocol": 1, "port": self.port, "path": "/companion", "token": self.token}, f)

    def close(self):
        self._closed = True
        try:
            self._server.shutdown(socket.SHUT_RDWR)  # wakes accept()
        except OSError:
            pass
        try:
            self._server.close()
        except OSError:
            pass
        for c in list(self.links.values()):
            try:
                c.sock.shutdown(socket.SHUT_RDWR)
                c.sock.close()
            except OSError:
                pass

    def _accept(self):
        while not self._closed:
            try:
                sock, _ = self._server.accept()
            except OSError:
                return
            if self._closed:
                sock.close()
                return
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                head += chunk
            head, buf = head.split(b"\r\n\r\n", 1)
            lines = head.decode().split("\r\n")
            headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
            if not lines[0].startswith("GET /companion ") or headers.get("host") not in (
                f"127.0.0.1:{self.port}",
                f"localhost:{self.port}",
            ):
                sock.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            if "origin" in headers:
                sock.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            accept = base64.b64encode(hashlib.sha1((headers["sec-websocket-key"] + GUID).encode()).digest()).decode()
            sock.sendall(
                f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode()
            )
            op, payload, buf = _recv_frame(sock, buf)
            hello = json.loads(payload)
            self.frames.append(hello)
            reason = self._admit(hello)
            if reason:
                self.refusals.append(reason)
                _send_text(sock, json.dumps({"type": "refused", "reason": reason}))
                return
            with self._lock:
                link = next((l for l, c in self.links.items() if c.hello["instance"] == hello["instance"]), None)
                if link is None:
                    link = f"{hello['app']}-{self._next}"
                    self._next += 1
                conn = Conn(sock, link, hello)
                self.links[link] = conn
                if link in self.order:
                    self.order.remove(link)
                self.order.append(link)
            conn.send({"type": "ready", "protocol": 1, "link": link, "host_version": "test"})
            if self.on_connect:
                self.on_connect(link, hello)
            while True:
                op, payload, buf = _recv_frame(sock, buf)
                if op == 0x8:
                    break
                if op != 0x1:
                    continue
                msg = json.loads(payload)
                self.frames.append(msg)
                t = msg.get("type")
                if t == "ping":
                    conn.send({"type": "pong"})
                elif t == "response":
                    waiter = self._pending.pop(msg.get("id"), None)
                    if waiter:
                        waiter[1].append(msg)
                        waiter[0].set()
                elif t == "event" and self.on_event:
                    self.on_event(link, msg.get("event"), msg.get("payload"))
        except (ConnectionError, OSError, ValueError, KeyError):
            pass
        finally:
            with self._lock:
                gone = [l for l, c in self.links.items() if c.sock is sock]
                for l in gone:
                    del self.links[l]
                    self.order.remove(l)
            for l in gone:
                if self.on_disconnect:
                    self.on_disconnect(l)
            try:
                sock.close()
            except OSError:
                pass

    def _admit(self, hello):
        if hello.get("type") != "hello":
            return "the first frame must be hello"
        if hello.get("protocol") != 1:
            return "companion protocol not supported"
        if not hello.get("plugin") or not hello.get("instance") or not hello.get("app"):
            return "hello needs plugin, instance and app"
        if hello.get("token") is None:
            return "a token is required (read it from the discovery file)"
        if hello.get("token") != self.token:
            return "wrong token"
        if not self.accepts(hello["plugin"]):
            return f'the KnobDeck plugin "{hello["plugin"]}" is not running or does not accept companions'
        return None

    def wait_link(self, timeout=10.0):
        import time

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.order:
                return self.order[-1]
            time.sleep(0.02)
        raise TimeoutError("no companion linked")

    def request(self, action, params=None, link=None, timeout=5.0):
        """Send a request and wait for the response frame."""
        with self._lock:
            link = link or (self.order[-1] if self.order else None)
            conn = self.links.get(link)
            rid = str(self._next)
            self._next += 1
        if conn is None:
            return {"ok": False, "error": "no companion is connected"}
        done, box = threading.Event(), []
        self._pending[rid] = (done, box)
        conn.send({"type": "request", "id": rid, "action": action, "params": params or {}})
        if not done.wait(timeout):
            self._pending.pop(rid, None)
            return {"ok": False, "error": f"the companion did not answer within {int(timeout * 1000)} ms"}
        return box[0]
