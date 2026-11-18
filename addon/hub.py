# The link to KnobDeck's companion hub (companion protocol 1, `knobdeck-protocol::companion` in
# Knobdeck/knobdeck-sdk). Pure Python, no bpy: the network runs on its own thread, requests go to
# the caller's queue and are answered with `respond()` from any thread (Blender's main thread).
#
#   discovery file (port, token) -> ws://127.0.0.1:<port>/companion -> hello -> ready
#   hub: request {id, action, params}   ->   companion: response {id, ok, result | error}
#   companion: event {event, payload}, ping every 20 s (answered pong)
#
# KnobDeck writes a new token on every start, so the discovery file is read again on every
# connection attempt.

import json
import os
import queue
import select
import socket
import sys
import threading
import time

from . import ws

PROTOCOL = 1
DISCOVERY_FILE = "companion-hub.json"
WS_PATH = "/companion"
DEFAULT_PORT = 32150
PORT_RANGE = 5
PING_EVERY = 20.0
RETRY_MIN, RETRY_MAX = 1.0, 30.0


def os_name(platform=None):
    """KnobDeck's name for this OS: the plugin id is `blender-<os>`."""
    p = platform or sys.platform
    if p.startswith("win"):
        return "windows"
    if p == "darwin":
        return "macos"
    return "linux"


def data_dir(platform=None, env=None):
    """KnobDeck's data folder, where it writes the discovery file."""
    p = platform or sys.platform
    env = os.environ if env is None else env
    home = os.path.expanduser("~")
    if p.startswith("win"):
        return os.path.join(env.get("APPDATA") or os.path.join(home, "AppData", "Roaming"), "KnobDeck")
    if p == "darwin":
        return os.path.join(home, "Library", "Application Support", "KnobDeck")
    return os.path.join(env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "KnobDeck")


def discovery_path():
    # KNOBDECK_COMPANION_DISCOVERY: another discovery file (tests' fake hub).
    return os.environ.get("KNOBDECK_COMPANION_DISCOVERY") or os.path.join(data_dir(), DISCOVERY_FILE)


def read_discovery(path=None):
    """{port, token, path} from the discovery file, or None when KnobDeck hasn't written it."""
    try:
        with open(path or discovery_path(), encoding="utf-8") as f:
            d = json.load(f)
        return {"port": int(d["port"]), "token": str(d.get("token") or ""), "path": str(d.get("path") or WS_PATH)}
    except (OSError, ValueError, KeyError, TypeError):
        return None


class Link:
    """Keeps one link to the hub until `stop()`. `requests` receives (id, action, params)."""

    def __init__(self, plugin, instance, app, app_version=None, name=None, version=None, features=(), on_status=None):
        self.hello = {"type": "hello", "protocol": PROTOCOL, "plugin": plugin, "instance": instance, "app": app, "features": list(features)}
        for k, v in (("app_version", app_version), ("name", name), ("version", version)):
            if v:
                self.hello[k] = v
        self.requests = queue.Queue()
        self.status = {"state": "disconnected"}
        self._on_status = on_status
        self._outbox = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._link = None

    # ── public, any thread ──────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="knobdeck-hub-link", daemon=True)
        self._thread.start()

    def stop(self, wait=2.0):
        self._stop.set()
        self._wake()
        if self._thread:
            self._thread.join(wait)
        self._thread = None

    @property
    def connected(self):
        return self._link is not None

    def emit(self, event, payload=None):
        """Live state for the plugin; dropped while not linked."""
        if not self.connected:
            return False
        self._send({"type": "event", "event": event, "payload": payload})
        return True

    def respond(self, req_id, result=None, error=None, unsupported=False):
        if unsupported:
            msg = {"type": "response", "id": req_id, "ok": False, "unsupported": True}
        elif error is not None:
            msg = {"type": "response", "id": req_id, "ok": False, "error": str(error)}
        else:
            msg = {"type": "response", "id": req_id, "ok": True, "result": result}
        self._send(msg)

    # ── the network thread ──────────────────────────────────────────────

    def _send(self, msg):
        self._outbox.put(json.dumps(msg))
        self._wake()

    def _wake(self):
        try:
            self._wake_w.send(b"x")
        except OSError:
            pass

    def _set_status(self, **status):
        self.status = status
        if self._on_status:
            try:
                self._on_status(dict(status))
            except Exception:
                pass

    def _ports(self, d):
        if d:
            return [d["port"]]
        return [DEFAULT_PORT + i for i in range(PORT_RANGE)]

    def _run(self):
        retry = RETRY_MIN
        while not self._stop.is_set():
            d = read_discovery()
            if d is None:
                self._set_status(state="waiting", reason="KnobDeck is not running")
            linked = False
            for port in self._ports(d):
                if self._stop.is_set():
                    return
                hello = dict(self.hello)
                if d and d["token"]:
                    hello["token"] = d["token"]
                outcome = self._session("127.0.0.1", port, (d or {}).get("path", WS_PATH), hello)
                if outcome == "linked":
                    linked = True
                    break
                if outcome == "refused":
                    break
            if linked:
                retry = RETRY_MIN
                continue  # reconnect at once (KnobDeck restarting, the hub dropped us)
            self._stop.wait(retry)
            retry = min(retry * 2, RETRY_MAX)

    def _session(self, host, port, path, hello):
        """One connection: 'linked' (was linked, now closed), 'refused', or 'failed'."""
        self._set_status(state="connecting", port=port)
        try:
            client = ws.Client.connect(host, port, path, timeout=3.0)
        except (OSError, ws.Refused):
            return "failed"
        try:
            client.send_text(json.dumps(hello))
            client.settimeout(5.0)
            first = client.recv_text()
            msg = json.loads(first) if first else {}
            if msg.get("type") == "refused":
                self._set_status(state="refused", reason=str(msg.get("reason", "")))
                return "refused"
            if msg.get("type") != "ready":
                return "failed"
            self._drain_outbox(drop=True)  # nothing from before this link is still meaningful
            self._link = str(msg.get("link", ""))
            self._set_status(state="connected", link=self._link, port=port)
            self._loop(client)
            return "linked"
        except (ws.Closed, OSError, ValueError):
            return "linked" if self._link is not None else "failed"
        finally:
            was = self._link is not None
            self._link = None
            client.close()
            if was:
                self._set_status(state="disconnected")

    def _drain_outbox(self, drop=False, client=None):
        while True:
            try:
                text = self._outbox.get_nowait()
            except queue.Empty:
                return
            if not drop:
                client.send_text(text)

    def _loop(self, client):
        client.settimeout(1.0)
        last_ping = time.monotonic()
        sock = client._sock
        while not self._stop.is_set():
            wait = max(0.0, PING_EVERY - (time.monotonic() - last_ping))
            readable, _, _ = select.select([sock, self._wake_r], [], [], wait) if not client._buf else ([sock], [], [])
            if self._wake_r in readable:
                try:
                    while self._wake_r.recv(4096):
                        pass
                except (BlockingIOError, OSError):
                    pass
            self._drain_outbox(client=client)
            if time.monotonic() - last_ping >= PING_EVERY:
                client.send_text('{"type":"ping"}')
                last_ping = time.monotonic()
            if sock in readable:
                text = client.recv_text()
                if text is None:
                    continue
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "request":
                    params = msg.get("params")
                    self.requests.put((str(msg.get("id", "")), str(msg.get("action", "")), params if isinstance(params, dict) else {}))
                # pong, and frame types from a newer hub: ignored
