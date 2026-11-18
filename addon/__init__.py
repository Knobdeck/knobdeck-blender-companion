# KnobDeck add-on for Blender: links Blender to KnobDeck's companion hub so the KnobDeck `blender`
# plugin can drive it with Blender's own Python API. The network runs on a background thread
# (hub.py); every bpy call runs on Blender's main thread from a timer (actions.py).

import os
import time
import uuid

import bpy

from . import actions, hub

PUMP_EVERY = 0.02  # s: how often queued requests run
STATE_EVERY = 0.2  # s: how often the state is compared and, when changed, sent
UNDO_REST = 0.6  # s: a dial at rest this long gets one undo step

_link = None
_last_state = None
_last_state_check = 0.0
_undo_due = None  # (time, message)
_start_pending = False


def _version():
    try:
        import tomllib

        with open(os.path.join(os.path.dirname(__file__), "blender_manifest.toml"), "rb") as f:
            return tomllib.load(f).get("version")
    except Exception:
        return None


def _instance_id():
    """Stable per Blender install (a file in the extension's user folder), plus this process."""
    base = None
    try:
        folder = bpy.utils.extension_path_user(__package__, create=True)
        path = os.path.join(folder, "instance-id")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                base = f.read().strip()
        if not base:
            base = uuid.uuid4().hex
            with open(path, "w", encoding="utf-8") as f:
                f.write(base)
    except Exception:
        base = base or uuid.uuid4().hex
    return f"{base}-{os.getpid()}"


def _display_name():
    v = bpy.app.version
    name = f"Blender {v[0]}.{v[1]}"
    if bpy.data.filepath:
        name += " — " + os.path.basename(bpy.data.filepath)
    return name


def _redraw_prefs(_status=None):
    # Called from the network thread: only flag; the timer redraws.
    global _status_changed
    _status_changed = True


_status_changed = False


def start_link():
    global _link, _last_state
    if _link is not None:
        return
    _last_state = None
    _link = hub.Link(
        plugin=f"blender-{hub.os_name()}",
        instance=_instance_id(),
        app="blender",
        app_version=bpy.app.version_string,
        name=_display_name(),
        version=_version(),
        features=["state"],
        on_status=_redraw_prefs,
    )
    _link.start()


def stop_link():
    global _link
    if _link is not None:
        _link.stop()
    _link = None


def link_status():
    return dict(_link.status) if _link else {"state": "off"}


def _answer(req_id, action, params):
    global _undo_due, _last_state_check
    try:
        result = actions.run(action, params)
    except actions.Unsupported:
        _link.respond(req_id, unsupported=True)
        return
    except actions.Failed as e:
        _link.respond(req_id, error=str(e))
        return
    except Exception as e:  # a bug here must not stop the pump
        _link.respond(req_id, error=f"{type(e).__name__}: {e}")
        return
    _link.respond(req_id, result=result)
    if action in actions.UNDOABLE:
        _undo_due = (time.monotonic() + UNDO_REST, actions.UNDOABLE[action])
    _last_state_check = 0.0  # report the change at once


def _push_undo():
    global _undo_due
    _, message = _undo_due
    _undo_due = None
    win = actions.main_window()
    if win is None:
        return
    try:
        with bpy.context.temp_override(window=win, screen=win.screen):
            bpy.ops.ed.undo_push(message=message)
    except Exception:
        pass


def _pump():
    """Blender's main thread: run queued requests, push undo steps, send state changes."""
    global _last_state, _last_state_check, _status_changed, _start_pending
    if _start_pending:
        # Not in register(): Blender's data is off limits while add-ons load at startup.
        _start_pending = False
        prefs = _prefs()
        if prefs is None or prefs.enabled:
            start_link()
    if _link is None:
        return PUMP_EVERY
    for _ in range(64):
        try:
            req_id, action, params = _link.requests.get_nowait()
        except Exception:
            break
        _answer(req_id, action, params)
    now = time.monotonic()
    if _undo_due and now >= _undo_due[0]:
        _push_undo()
    if not _link.connected:
        _last_state = None  # send the whole state again on the next link
    elif now - _last_state_check >= STATE_EVERY:
        _last_state_check = now
        try:
            s = actions.snapshot()
        except Exception:
            s = None
        if s is not None and s != _last_state:
            if _link.emit("state", s):
                _last_state = s
    if _status_changed:
        _status_changed = False
        for win in bpy.context.window_manager.windows if bpy.context.window_manager else []:
            for area in win.screen.areas:
                if area.type == "PREFERENCES":
                    area.tag_redraw()
    return PUMP_EVERY


# ── preferences ──────────────────────────────────────────────────────────────


def _on_enabled(self, _context):
    if self.enabled:
        start_link()
    else:
        stop_link()


class KnobDeckPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    enabled: bpy.props.BoolProperty(
        name="Link to KnobDeck",
        description="Let KnobDeck on this computer control Blender",
        default=True,
        update=_on_enabled,
    )

    def draw(self, _context):
        col = self.layout.column()
        col.prop(self, "enabled")
        s = link_status()
        state = s.get("state")
        if state == "connected":
            col.label(text="Linked to KnobDeck", icon="CHECKMARK")
        elif state == "refused":
            col.label(text=f"KnobDeck refused the link: {s.get('reason', '')}", icon="ERROR")
            col.label(text="Install and enable the Blender plugin in KnobDeck (Get Plugins).")
        elif state == "off":
            col.label(text="Off", icon="PAUSE")
        else:
            col.label(text="Waiting for KnobDeck to start", icon="TIME")
        col.label(text="Connects only to KnobDeck on this computer (127.0.0.1).", icon="INFO")


def _prefs():
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


def register():
    global _start_pending
    bpy.utils.register_class(KnobDeckPreferences)
    _start_pending = True
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=PUMP_EVERY, persistent=True)


def unregister():
    global _start_pending
    _start_pending = False
    stop_link()
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    bpy.utils.unregister_class(KnobDeckPreferences)
