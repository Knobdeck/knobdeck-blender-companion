# The hub link (addon/hub.py, addon/ws.py) against the fake hub, with plain Python (no Blender).
#   python3 -m unittest discover -s tests -p "test_*.py"

import os
import sys
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Load the add-on's pure modules without its __init__ (which needs bpy).
pkg = types.ModuleType("kdaddon")
pkg.__path__ = [os.path.join(HERE, "..", "addon")]
sys.modules["kdaddon"] = pkg

from fake_hub import FakeHub  # noqa: E402
from kdaddon import hub  # noqa: E402


def wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class LinkTest(unittest.TestCase):
    def setUp(self):
        self.hub = FakeHub(accepts=lambda p: p == "blender-test")
        os.environ["KNOBDECK_COMPANION_DISCOVERY"] = self.hub.discovery
        self.statuses = []
        self.link = hub.Link(
            "blender-test",
            "inst-1",
            "blender",
            app_version="5.3.0",
            name="Blender 5.3",
            version="1.0.0",
            features=["state"],
            on_status=self.statuses.append,
        )

    def tearDown(self):
        self.link.stop()
        self.hub.close()
        os.environ.pop("KNOBDECK_COMPANION_DISCOVERY", None)

    def serve(self, handler):
        """Answer requests on a thread, as Blender's timer does."""
        import threading

        def loop():
            while not self._stop:
                try:
                    rid, action, params = self.link.requests.get(timeout=0.05)
                except Exception:
                    continue
                handler(rid, action, params)

        self._stop = False
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        self.addCleanup(lambda: setattr(self, "_stop", True))

    def test_hello_carries_the_token_and_who_we_are(self):
        self.link.start()
        self.hub.wait_link()
        hello = self.hub.frames[0]
        self.assertEqual(hello["type"], "hello")
        self.assertEqual(hello["protocol"], 1)
        self.assertEqual(hello["plugin"], "blender-test")
        self.assertEqual(hello["instance"], "inst-1")
        self.assertEqual(hello["app"], "blender")
        self.assertEqual(hello["app_version"], "5.3.0")
        self.assertEqual(hello["token"], self.hub.token)
        self.assertEqual(hello["features"], ["state"])
        self.assertTrue(wait(lambda: self.link.connected))
        self.assertEqual(self.link.status["state"], "connected")

    def test_requests_are_answered_and_events_reach_the_hub(self):
        events = []
        self.hub.on_event = lambda link, e, p: events.append((e, p))

        def handler(rid, action, params):
            if action == "echo":
                self.link.respond(rid, result={"got": params})
            elif action == "fail":
                self.link.respond(rid, error="No document")
            else:
                self.link.respond(rid, unsupported=True)

        self.serve(handler)
        self.link.start()
        self.hub.wait_link()
        self.assertTrue(wait(lambda: self.link.connected))
        self.assertEqual(self.hub.request("echo", {"a": 1}), {"type": "response", "id": "2", "ok": True, "result": {"got": {"a": 1}}})
        r = self.hub.request("fail")
        self.assertEqual((r["ok"], r["error"]), (False, "No document"))
        r = self.hub.request("nope")
        self.assertEqual((r["ok"], r["unsupported"]), (False, True))
        # A big frame (16-bit and 64-bit lengths) both ways.
        big = "x" * 70000
        self.assertEqual(self.hub.request("echo", {"s": big})["result"]["got"]["s"], big)
        self.assertTrue(self.link.emit("state", {"frame": 3}))
        self.assertTrue(wait(lambda: events == [("state", {"frame": 3})]))

    def test_ping(self):
        old = hub.PING_EVERY
        hub.PING_EVERY = 0.2
        try:
            self.link.start()
            self.hub.wait_link()
            self.assertTrue(wait(lambda: any(f.get("type") == "ping" for f in self.hub.frames)))
        finally:
            hub.PING_EVERY = old

    def test_a_refusal_is_reported_and_retried(self):
        self.hub.accepts = lambda p: False
        old = hub.RETRY_MIN
        hub.RETRY_MIN = 0.1
        try:
            self.link.start()
            self.assertTrue(wait(lambda: self.link.status.get("state") == "refused"))
            self.assertIn("is not running", self.link.status["reason"])
            self.hub.accepts = lambda p: True
            self.assertTrue(wait(lambda: self.link.connected, 5))
        finally:
            hub.RETRY_MIN = old

    def test_reconnects_to_a_restarted_hub_with_its_new_token(self):
        old = hub.RETRY_MIN
        hub.RETRY_MIN = 0.1
        try:
            self.link.start()
            self.hub.wait_link()
            self.assertTrue(wait(lambda: self.link.connected))
            self.hub.close()
            self.assertTrue(wait(lambda: not self.link.connected))
            self.hub = FakeHub()  # new port, new token, same discovery path
            os.environ["KNOBDECK_COMPANION_DISCOVERY"] = self.hub.discovery
            self.hub.wait_link(10)
            self.assertEqual(self.hub.frames[0]["token"], self.hub.token)
        finally:
            hub.RETRY_MIN = old

    def test_waits_while_knobdeck_is_not_running(self):
        os.environ["KNOBDECK_COMPANION_DISCOVERY"] = os.path.join(self.hub.dir, "missing.json")
        old_ports = hub.Link._ports
        hub.Link._ports = lambda self, d: [1] if d is None else [d["port"]]  # nothing listens on port 1
        try:
            self.link.start()
            self.assertTrue(wait(lambda: self.statuses and self.statuses[0]["state"] == "waiting"))
            self.assertFalse(self.link.connected)
        finally:
            hub.Link._ports = old_ports


class PathsTest(unittest.TestCase):
    def test_data_dir_matches_knobdeck(self):
        self.assertEqual(
            hub.data_dir("win32", {"APPDATA": r"C:\Users\a\AppData\Roaming"}), os.path.join(r"C:\Users\a\AppData\Roaming", "KnobDeck")
        )
        self.assertTrue(hub.data_dir("darwin", {}).endswith(os.path.join("Library", "Application Support", "KnobDeck")))
        self.assertEqual(hub.data_dir("linux", {"XDG_CONFIG_HOME": "/x"}), os.path.join("/x", "KnobDeck"))
        self.assertEqual([hub.os_name(p) for p in ("win32", "darwin", "linux")], ["windows", "macos", "linux"])


if __name__ == "__main__":
    unittest.main()
