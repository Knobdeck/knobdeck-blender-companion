# Smoke test inside a headless Blender: installs the built extension zip, links it to the fake
# hub and runs every action that works without a window on screen.
#
#   BLENDER_USER_RESOURCES=$(mktemp -d) blender --background --factory-startup \
#       --python tests/blender_smoke.py -- dist/knobdeck-<version>.zip
#
# BLENDER_USER_RESOURCES keeps the install out of your own Blender settings. Exits non-zero on
# failure. Background Blender has no undo stack, no playback and no render window, so undo / redo,
# play / pause and render are checked for a clean error only.

import os
import sys
import tempfile
import threading
import time
import traceback

import addon_utils
import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fake_hub import FakeHub  # noqa: E402

MODULE = "bl_ext.user_default.knobdeck"


def install(zip_path):
    if not os.environ.get("BLENDER_USER_RESOURCES"):
        sys.exit("Set BLENDER_USER_RESOURCES to a temporary folder (keeps your own Blender settings untouched).")
    bpy.ops.extensions.package_install_files(filepath=os.path.abspath(zip_path), repo="user_default", enable_on_install=False)
    addon_utils.enable(MODULE, default_set=True, handle_error=None)
    mod = sys.modules.get(MODULE)
    if mod is None:
        sys.exit(f"{MODULE} did not load")
    return mod


class Smoke:
    def __init__(self, mod, hub):
        self.mod, self.hub, self.failures = mod, hub, []

    def pump_until(self, cond, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.mod._pump()
            if cond():
                return True
            time.sleep(0.01)
        return False

    def call(self, action, params=None):
        box = []
        t = threading.Thread(target=lambda: box.append(self.hub.request(action, params)), daemon=True)
        t.start()
        if not self.pump_until(lambda: box, 10):
            raise AssertionError(f"{action}: no answer")
        return box[0]

    def ok(self, action, params=None):
        r = self.call(action, params)
        if not r.get("ok"):
            raise AssertionError(f"{action} {params}: {r}")
        return r.get("result")

    def err(self, action, params=None):
        r = self.call(action, params)
        if r.get("ok"):
            raise AssertionError(f"{action} {params} should fail: {r}")
        return r

    def check(self, name, fn):
        try:
            fn()
            print(f"ok   {name}")
        except Exception:
            self.failures.append(name)
            print(f"FAIL {name}")
            traceback.print_exc()


def main():
    zip_path = sys.argv[sys.argv.index("--") + 1]
    events = []
    hub = FakeHub(accepts=lambda p: p.startswith("blender-"), on_event=lambda link, e, p: events.append((e, p)))
    os.environ["KNOBDECK_COMPANION_DISCOVERY"] = hub.discovery
    mod = install(zip_path)
    s = Smoke(mod, hub)
    scene = bpy.context.scene
    cube = bpy.data.objects["Cube"]

    def linked():
        assert s.pump_until(lambda: hub.order, 10), "no link"
        hello = hub.frames[0]
        assert hello["plugin"] == "blender-" + {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux"), hello
        assert hello["app"] == "blender" and hello["version"] == "1.0.0" and hello["token"] == hub.token, hello
        assert s.pump_until(lambda: any(e == "state" for e, _ in events), 5), "no state event"
        st = [p for e, p in events if e == "state"][-1]
        assert st["mode"] == "OBJECT" and st["object"] == "Cube" and st["frame"] == 1, st

    def state():
        st = s.ok("state")
        assert st["workspace"] == "Layout" and st["shading"] == "SOLID" and st["transform"]["location"] == [0, 0, 0], st

    def frames():
        assert s.ok("frame", {"delta": 5})["value"] == 6
        assert s.ok("frame", {"value": 40})["value"] == 40
        assert s.ok("jump", {"to": "end"})["value"] == scene.frame_end
        assert s.ok("jump", {"to": "start"})["value"] == scene.frame_start
        n = len(events)
        s.ok("frame", {"delta": 3})
        assert s.pump_until(lambda: any(e == "state" and p["frame"] == scene.frame_start + 3 for e, p in events[n:]), 3), (
            "frame change not reported"
        )

    def keyframes():
        cube.keyframe_insert("location", frame=10)
        cube.keyframe_insert("location", frame=20)
        scene.frame_set(1)
        assert s.ok("jump", {"to": "next_keyframe"})["value"] == 10
        assert s.ok("jump", {"to": "next_keyframe"})["value"] == 20
        assert s.ok("jump", {"to": "previous_keyframe"})["value"] == 10
        assert "No more keyframes" in s.err("jump", {"to": "previous_keyframe"})["error"]
        cube.animation_data_clear()
        scene.frame_set(1)

    def workspaces():
        items = s.ok("workspaces")["items"]
        assert {"value": "Sculpting", "label": "Sculpting"} in items, items
        s.ok("workspace", {"name": "Shading"})
        # The switch itself happens in Blender's event loop, which background mode doesn't run.
        assert "No workspace" in s.err("workspace", {"name": "Nope"})["error"]
        s.ok("workspace", {"name": "Layout"})

    def viewport():
        assert s.ok("shading", {"type": "WIREFRAME"})["value"] == "WIREFRAME"
        assert s.ok("shading", {"type": "next"})["value"] == "SOLID"
        assert s.ok("shading", {"type": "previous"})["value"] == "WIREFRAME"
        on = s.ok("xray", {})["value"]
        assert s.ok("xray", {})["value"] == (not on)
        s.ok("shading", {"type": "SOLID"})
        assert s.ok("overlays", {"value": "off"})["value"] is False
        assert s.ok("overlays", {})["value"] is True

    def transform():
        assert s.ok("transform", {"property": "location", "axis": "x", "delta": 0.5})["value"] == 0.5
        assert s.ok("transform", {"property": "location", "axis": "x", "value": 2})["value"] == 2
        assert abs(cube.location.x - 2) < 1e-6
        assert abs(s.ok("transform", {"property": "rotation", "axis": "z", "delta": 90})["value"] - 90) < 1e-3
        cube.rotation_mode = "QUATERNION"
        assert abs(s.ok("transform", {"property": "rotation", "axis": "z", "delta": -45})["value"] - 45) < 1e-3
        cube.rotation_mode = "XYZ"
        assert s.ok("transform", {"property": "scale", "axis": "y", "delta": 0.25})["value"] == 1.25
        assert "property" in s.err("transform", {"property": "size", "axis": "x", "delta": 1})["error"]

    def brush():
        assert "sculpt or paint" in s.err("brush", {"property": "size", "delta": 5})["error"]
        assert s.ok("mode", {"mode": "SCULPT"})["value"] == "SCULPT"
        size = s.ok("state")["brush"]["size"]
        assert s.ok("brush", {"property": "size", "delta": 10})["value"] == size + 10
        assert s.ok("brush", {"property": "size", "value": 0})["value"] == 1
        assert abs(s.ok("brush", {"property": "strength", "value": 0.3})["value"] - 0.3) < 1e-6
        assert "Object Mode" in s.err("transform", {"property": "location", "axis": "x", "delta": 1})["error"]
        assert s.ok("mode", {"mode": "OBJECT"})["value"] == "OBJECT"

    def save():
        path = os.path.join(tempfile.mkdtemp(), "smoke.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        cube.location.y = 3
        assert s.ok("save")["saved"] is True
        assert os.path.getmtime(path) > 0

    def no_window_errors():
        # No undo stack / playback / render window in background: a clear error, never a crash.
        for action in ("undo", "redo"):
            r = s.call(action)
            assert r.get("ok") is False and r.get("error"), r
        assert s.call("nope")["unsupported"] is True

    s.check("links to the hub and reports state", linked)
    s.check("state request", state)
    s.check("frame dial and jumps", frames)
    s.check("keyframe jumps", keyframes)
    s.check("workspaces", workspaces)
    s.check("shading, X-ray, overlays", viewport)
    s.check("transform dial", transform)
    s.check("mode and brush dials", brush)
    s.check("save", save)
    s.check("clean errors without a window", no_window_errors)
    mod.stop_link()
    hub.close()
    if s.failures:
        print(f"{len(s.failures)} failed: {', '.join(s.failures)}")
        sys.exit(1)
    print("all smoke checks passed")


try:
    main()
except SystemExit:
    raise
except BaseException:
    traceback.print_exc()
    sys.exit(1)
