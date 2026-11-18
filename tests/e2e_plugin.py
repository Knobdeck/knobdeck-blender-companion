# End to end: the real KnobDeck `blender` plugin (from Knobdeck/knobdeck-plugins) and this add-on
# in a headless Blender, joined by the fake hub. This script plays KnobDeck: it speaks the plugin's
# stdio protocol (hello, sync, invoke, request), relays the plugin's `companion_request` /
# `companion_links` host calls to the hub and the hub's link events to the plugin, as
# Knobdeck/app does.
#
#   python3 tests/e2e_plugin.py <plugin exe> <blender exe> dist/knobdeck-<version>.zip

import json
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fake_hub import FakeHub  # noqa: E402


class Plugin:
    def __init__(self, exe):
        meta = json.loads(subprocess.run([exe], env={**os.environ, "KNOBDECK_CODEGEN": "1"}, capture_output=True, check=True).stdout)
        self.id = meta["manifest"]["id"]
        # short id -> full id: "<manifest id>-<short>-<8 hex>"
        self.ids = {a["id"][len(self.id) + 1 : -9]: a["id"] for a in meta["actions"]["actions"]}
        self.data = tempfile.mkdtemp(prefix="kd-plugin-")
        self.proc = subprocess.Popen(
            [exe],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={**os.environ, "KNOBDECK_PLUGIN_DATA": self.data},
        )
        self.frames, self.waiters, self.lock = [], {}, threading.Lock()
        self.subscribed = set()
        self.ready = threading.Event()
        self.hub = None
        self.seq = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: [None for _ in self.proc.stderr], daemon=True).start()

    def send(self, msg):
        with self.lock:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()

    def _read(self):
        for line in self.proc.stdout:
            f = json.loads(line)
            self.frames.append(f)
            t = f.get("type")
            if t == "ready":
                self.subscribed.update(f.get("subscribe", []))
                self.ready.set()
            elif t == "subscribe":
                self.subscribed = set(f.get("events", []))
            elif t == "host_call":
                threading.Thread(target=self._host_call, args=(f,), daemon=True).start()
            if t in ("done", "result") and f.get("id") in self.waiters:
                self.waiters.pop(f["id"]).append(f)

    def _host_call(self, f):
        method, params = f["method"], f.get("params") or {}
        if method == "companion_request":
            r = self.hub.request(
                params["action"], params.get("params"), link=params.get("link"), timeout=min(params.get("timeout_ms") or 5000, 30000) / 1000
            )
            if "type" not in r:  # the hub's own error
                self.send({"type": "result", "id": f["id"], "ok": False, "error": r["error"]})
                return
            r = {k: v for k, v in r.items() if k != "type"}
            self.send({"type": "result", "id": f["id"], "ok": True, "value": r})
        elif method == "companion_links":
            links = [
                {"link": l, "instance": c.hello["instance"], "app": c.hello["app"], "features": c.hello.get("features", [])}
                for l, c in self.hub.links.items()
            ]
            self.send({"type": "result", "id": f["id"], "ok": True, "value": links})
        else:
            self.send({"type": "result", "id": f["id"], "ok": False, "error": f"{method} is not available here"})

    def hello(self, instances):
        self.send(
            {
                "type": "hello",
                "protocol": 2,
                "plugin_id": self.id,
                "data_dir": self.data,
                "host_version": "e2e",
                "features": ["state_match", "events", "notify", "app_state", "companion_hub"],
                "host_pid": os.getpid(),
            }
        )
        assert self.ready.wait(10), "plugin sent no ready"
        self.send(
            {
                "type": "sync",
                "instances": [{"instance_id": i, "action_id": self.ids[a], "inputs": inputs} for i, (a, inputs) in instances.items()],
            }
        )

    def _wait(self, fid, timeout):
        box = []
        self.waiters[fid] = box
        end = time.monotonic() + timeout
        while not box and time.monotonic() < end:
            time.sleep(0.01)
        assert box, f"no reply to {fid}"
        return box[0]

    def invoke(self, instance, action, inputs=None, trigger="press", child=None):
        self.seq += 1
        fid = f"i{self.seq}"
        msg = {
            "type": "invoke",
            "id": fid,
            "instance_id": instance,
            "action_id": self.ids[action],
            "trigger": trigger,
            "inputs": inputs or {},
        }
        if child is not None:
            msg["child_id"] = child
        start = len(self.frames)
        self.waiters[fid] = box = []
        self.send(msg)
        end = time.monotonic() + 15
        while not box and time.monotonic() < end:
            time.sleep(0.01)
        assert box, f"no done for {action}"
        errors = [f for f in self.frames[start:] if f.get("type") == "error" and f.get("instance_id") == instance]
        return errors

    def request(self, method, params):
        self.seq += 1
        fid = f"r{self.seq}"
        self.waiters[fid] = box = []
        self.send({"type": "request", "id": fid, "method": method, "params": params})
        end = time.monotonic() + 15
        while not box and time.monotonic() < end:
            time.sleep(0.01)
        assert box, f"no result for {method}"
        f = box[0]
        assert f["ok"], f
        return f["value"]

    def event(self, name, payload):
        if name in self.subscribed:
            self.send({"type": "event", "event": name, "payload": payload})

    def wait_frame(self, pred, timeout=10.0, start=0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for f in self.frames[start:]:
                if pred(f):
                    return f
            time.sleep(0.02)
        raise AssertionError("expected frame never came")

    def stop(self):
        try:
            self.send({"type": "shutdown"})
            self.proc.wait(3)
        except Exception:
            self.proc.kill()


def main():
    plugin_exe, blender_exe, zip_path = sys.argv[1:4]
    plugin = Plugin(plugin_exe)
    hub = FakeHub(
        accepts=lambda p: p == plugin.id and "companion_connected" in plugin.subscribed,
        on_connect=lambda link, h: plugin.event(
            "companion_connected",
            {
                "link": link,
                "instance": h["instance"],
                "app": h["app"],
                "app_version": h.get("app_version"),
                "name": h.get("name"),
                "version": h.get("version"),
                "features": h.get("features", []),
            },
        ),
        on_disconnect=lambda link: plugin.event("companion_disconnected", {"link": link}),
        on_event=lambda link, e, p: plugin.event("companion_event", {"link": link, "event": e, "payload": p}),
    )
    plugin.hub = hub
    plugin.hello(
        {
            "s1": ("status", {}),
            "f1": ("frame", {"step": "1"}),
            "p1": ("play-pause", {}),
            "t1": ("transform", {"property": "location", "axis": "x", "step": "0.5"}),
            "x1": ("xray", {}),
        }
    )

    # Not linked yet: a clear error.
    errors = plugin.invoke("j0", "jump-end")
    assert errors and "isn't linked" in errors[0]["message"], errors

    stop = os.path.join(hub.dir, "stop")
    env = {
        **os.environ,
        "KNOBDECK_COMPANION_DISCOVERY": hub.discovery,
        "KNOBDECK_SERVE_STOP": stop,
        "BLENDER_USER_RESOURCES": tempfile.mkdtemp(prefix="kd-blender-"),
    }
    blender = subprocess.Popen(
        [
            blender_exe,
            "--background",
            "--factory-startup",
            "--python",
            os.path.join(HERE, "blender_serve.py"),
            "--",
            os.path.abspath(zip_path),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    failures = []

    def check(name, fn):
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:
            failures.append(name)
            print(f"FAIL {name}: {e!r}")

    try:
        hub.wait_link(60)
        full = lambda a: plugin.ids[a]  # noqa: E731

        def status_label():
            plugin.wait_frame(
                lambda f: f.get("type") == "state" and f.get("action_id") == full("status") and f.get("label") == "Object · Cube", 15
            )

        def frame_dial():
            start = len(plugin.frames)
            assert not plugin.invoke("f1", "frame", {"step": "1", "delta": 4}, trigger="adjust")
            plugin.wait_frame(lambda f: f.get("type") == "state" and f.get("instance_id") == "f1" and f.get("state") == "5", 5, start)

        def jumps_and_live_state():
            start = len(plugin.frames)
            assert not plugin.invoke("j1", "jump-end")
            # The add-on's state event reaches every frame key.
            plugin.wait_frame(
                lambda f: f.get("type") == "state" and f.get("action_id") == full("frame") and f.get("state") == "250", 5, start
            )
            assert not plugin.invoke("j2", "jump-start")

        def transform_dial():
            start = len(plugin.frames)
            assert not plugin.invoke("t1", "transform", {"property": "location", "axis": "x", "step": "0.5", "delta": 3}, trigger="adjust")
            plugin.wait_frame(
                lambda f: (
                    f.get("type") == "state"
                    and f.get("state") == "1.5"
                    and (f.get("instance_id") == "t1" or f.get("action_id") == full("transform"))
                ),
                5,
                start,
            )
            assert hub.request("state")["result"]["transform"]["location"][0] == 1.5

        def workspaces():
            items = plugin.request("query", {"query_id": "workspaces", "inputs": {}})
            assert {"value": "Shading", "label": "Shading"} in items, items
            slices = plugin.request("wheel", {"action_id": full("workspace-wheel"), "instance_id": "w1", "inputs": {}})
            assert any(s.get("id") == "Sculpting" for s in (slices if isinstance(slices, list) else slices.get("slices", []))), slices
            assert not plugin.invoke("w2", "workspace", {"workspace": "Shading"})

        def toggles():
            start = len(plugin.frames)
            assert not plugin.invoke("x1", "xray")
            plugin.wait_frame(lambda f: f.get("type") == "state" and f.get("instance_id") == "x1" and f.get("state") == "on", 5, start)

        def brush_needs_paint_mode():
            errors = plugin.invoke("b1", "brush-size", {"step": "5", "delta": 1}, trigger="adjust")
            assert errors and "sculpt or paint" in errors[0]["message"], errors
            assert not plugin.invoke("m1", "object-mode", {"mode": "SCULPT"})
            assert not plugin.invoke("b1", "brush-size", {"step": "5", "delta": 2}, trigger="adjust")
            assert not plugin.invoke("m2", "object-mode", {"mode": "OBJECT"})

        def disconnect():
            start = len(plugin.frames)
            open(stop, "w").close()
            blender.wait(30)
            plugin.wait_frame(
                lambda f: f.get("type") == "state" and f.get("action_id") == full("status") and f.get("label") == "Blender offline",
                10,
                start,
            )

        check("status key shows mode and object", status_label)
        check("frame dial", frame_dial)
        check("jumps, live frame state", jumps_and_live_state)
        check("transform dial", transform_dial)
        check("workspaces query, wheel and button", workspaces)
        check("X-ray toggle", toggles)
        check("brush dial in sculpt mode", brush_needs_paint_mode)
        check("Blender quitting shows offline", disconnect)
    finally:
        open(stop, "w").close()
        try:
            blender.wait(30)
        except subprocess.TimeoutExpired:
            blender.kill()
        plugin.stop()
        hub.close()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        sys.exit(1)
    print("end-to-end passed")


if __name__ == "__main__":
    main()
