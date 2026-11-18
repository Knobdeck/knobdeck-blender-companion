# What each KnobDeck request does in Blender, with Blender's own Python API. Runs on Blender's
# main thread only (from the add-on's timer). Action ids and parameters are a contract with the
# `blender` plugin in Knobdeck/knobdeck-plugins: add, never rename.

import math
import os

import bpy


class Unsupported(Exception):
    """Blender has no API for this here (answered `unsupported`)."""


class Failed(Exception):
    """A message for the user."""


# ── context ──────────────────────────────────────────────────────────────────


def main_window():
    wm = bpy.context.window_manager
    if bpy.context.window:
        return bpy.context.window
    return wm.windows[0] if wm and len(wm.windows) else None


def _override(area_type=None):
    """A context for operators run from a timer (which has no window)."""
    win = main_window()
    if win is None:
        raise Failed("Blender has no window open")
    kw = {"window": win, "screen": win.screen}
    if area_type:
        for area in win.screen.areas:
            if area.type == area_type:
                kw["area"] = area
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region:
                    kw["region"] = region
                break
    return bpy.context.temp_override(**kw)


def _op(fn, *args, area_type=None, **kw):
    with _override(area_type):
        if not fn.poll():
            raise Failed("Blender can't do that right now")
        result = fn(*args, **kw)
    if "CANCELLED" in result:
        raise Failed("Blender cancelled it")
    return result


def view3d_spaces():
    win = main_window()
    if win is None:
        return []
    return [a.spaces.active for a in win.screen.areas if a.type == "VIEW_3D"]


def _need_views():
    spaces = view3d_spaces()
    if not spaces:
        raise Failed("This workspace has no 3D Viewport")
    return spaces


def _flag(params, current):
    """`value` from the plugin: toggle (default), on/off, or a bool."""
    v = params.get("value", "toggle")
    if isinstance(v, bool):
        return v
    v = str(v).lower()
    if v in ("on", "true", "1"):
        return True
    if v in ("off", "false", "0"):
        return False
    return not current


def _number(params, key):
    v = params.get(key)
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise Failed(f"{key} must be a number") from None
    if math.isnan(v) or math.isinf(v):
        raise Failed(f"{key} must be a number")
    return v


# ── state (the `state` event and request) ────────────────────────────────────

MODE_LABELS = {
    "OBJECT": "Object",
    "EDIT_MESH": "Edit",
    "EDIT_CURVE": "Edit",
    "EDIT_CURVES": "Edit",
    "EDIT_SURFACE": "Edit",
    "EDIT_TEXT": "Edit",
    "EDIT_ARMATURE": "Edit",
    "EDIT_METABALL": "Edit",
    "EDIT_LATTICE": "Edit",
    "EDIT_GREASE_PENCIL": "Edit",
    "EDIT_POINT_CLOUD": "Edit",
    "POSE": "Pose",
    "SCULPT": "Sculpt",
    "SCULPT_CURVES": "Sculpt",
    "SCULPT_GREASE_PENCIL": "Sculpt",
    "SCULPT_GPENCIL": "Sculpt",
    "PAINT_WEIGHT": "Weight Paint",
    "WEIGHT_GREASE_PENCIL": "Weight Paint",
    "WEIGHT_GPENCIL": "Weight Paint",
    "PAINT_VERTEX": "Vertex Paint",
    "VERTEX_GREASE_PENCIL": "Vertex Paint",
    "VERTEX_GPENCIL": "Vertex Paint",
    "PAINT_TEXTURE": "Texture Paint",
    "PAINT_GREASE_PENCIL": "Draw",
    "PAINT_GPENCIL": "Draw",
    "PARTICLE": "Particle Edit",
}

# context.mode → the ToolSettings attribute holding that mode's Paint settings.
PAINT_SETTINGS = {
    "SCULPT": "sculpt",
    "PAINT_WEIGHT": "weight_paint",
    "PAINT_VERTEX": "vertex_paint",
    "PAINT_TEXTURE": "image_paint",
    "SCULPT_CURVES": "curves_sculpt",
    "PAINT_GREASE_PENCIL": "gpencil_paint",
    "PAINT_GPENCIL": "gpencil_paint",
    "SCULPT_GREASE_PENCIL": "gpencil_sculpt_paint",
    "SCULPT_GPENCIL": "gpencil_sculpt_paint",
    "VERTEX_GREASE_PENCIL": "gpencil_vertex_paint",
    "VERTEX_GPENCIL": "gpencil_vertex_paint",
    "WEIGHT_GREASE_PENCIL": "gpencil_weight_paint",
    "WEIGHT_GPENCIL": "gpencil_weight_paint",
}


def _paint():
    attr = PAINT_SETTINGS.get(bpy.context.mode)
    if not attr:
        return None
    return getattr(bpy.context.scene.tool_settings, attr, None)


def _unified(paint):
    # Blender 5.0 moved the unified settings onto each mode's Paint; 4.x keeps one on ToolSettings.
    ups = getattr(paint, "unified_paint_settings", None)
    if ups is None:
        ups = getattr(bpy.context.scene.tool_settings, "unified_paint_settings", None)
    return ups


def _brush_holder(prop):
    """(object holding `prop`, the brush) for the current paint mode."""
    paint = _paint()
    if paint is None:
        raise Failed("Not in a sculpt or paint mode")
    brush = paint.brush
    ups = _unified(paint)
    use_unified = ups is not None and getattr(ups, f"use_unified_{prop}", False)
    holder = ups if use_unified else brush
    if holder is None or not hasattr(holder, prop):
        raise Failed("No brush is active")
    return holder, brush


def brush_state():
    try:
        size_holder, _ = _brush_holder("size")
        strength_holder, _ = _brush_holder("strength")
    except Failed:
        return None
    return {"size": int(size_holder.size), "strength": round(float(strength_holder.strength), 4)}


def _rotation_degrees(ob):
    return [round(math.degrees(a), 4) for a in _euler(ob)]


def _euler(ob):
    if ob.rotation_mode == "QUATERNION":
        return ob.rotation_quaternion.to_euler("XYZ")
    if ob.rotation_mode == "AXIS_ANGLE":
        from mathutils import Quaternion

        w, x, y, z = ob.rotation_axis_angle
        return Quaternion((x, y, z), w).to_euler("XYZ")
    return ob.rotation_euler


def transform_state(ob):
    if ob is None:
        return None
    return {
        "location": [round(v, 5) for v in ob.location],
        "rotation": _rotation_degrees(ob),
        "scale": [round(v, 5) for v in ob.scale],
    }


def is_playing():
    wm = bpy.context.window_manager
    return any(w.screen and w.screen.is_animation_playing for w in wm.windows) if wm else False


def snapshot():
    scene = bpy.context.scene
    ob = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
    win = main_window()
    spaces = view3d_spaces()
    shading = spaces[0].shading if spaces else None
    mode = bpy.context.mode
    return {
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, mode.replace("_", " ").title()),
        "object": ob.name if ob else None,
        "object_type": ob.type if ob else None,
        "frame": scene.frame_current,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "playing": is_playing(),
        "workspace": win.workspace.name if win and win.workspace else None,
        "shading": shading.type if shading else None,
        "overlays": bool(spaces and spaces[0].overlay.show_overlays),
        "xray": bool(shading and _xray_on(shading)),
        "file": os.path.basename(bpy.data.filepath) if bpy.data.filepath else None,
        "dirty": bool(bpy.data.is_dirty),
        "brush": brush_state(),
        "transform": transform_state(ob),
    }


# ── actions ──────────────────────────────────────────────────────────────────


def a_state(_):
    return snapshot()


def a_playback(params):
    playing = is_playing()
    want = _flag(params, playing)
    if want and not playing:
        _op(bpy.ops.screen.animation_play)
    elif not want and playing:
        _op(bpy.ops.screen.animation_cancel, restore_frame=False)
    return {"value": want}


def a_frame(params):
    scene = bpy.context.scene
    value, delta = _number(params, "value"), _number(params, "delta")
    if value is not None:
        target = int(round(value))
    elif delta is not None:
        target = scene.frame_current + int(round(delta))
    else:
        raise Failed("frame needs a value or a delta")
    scene.frame_set(max(-1048574, min(1048574, target)))
    return {"value": scene.frame_current}


def a_jump(params):
    scene = bpy.context.scene
    to = str(params.get("to", ""))
    if to == "start":
        scene.frame_set(scene.frame_preview_start if scene.use_preview_range else scene.frame_start)
    elif to == "end":
        scene.frame_set(scene.frame_preview_end if scene.use_preview_range else scene.frame_end)
    elif to in ("next_keyframe", "previous_keyframe"):
        before = scene.frame_current
        with _override():
            result = bpy.ops.screen.keyframe_jump(next=(to == "next_keyframe"))
        if "CANCELLED" in result or scene.frame_current == before:
            raise Failed("No more keyframes that way")
    else:
        raise Failed(f'Unknown jump "{to}"')
    return {"value": scene.frame_current}


def a_workspaces(_):
    return {"items": [{"value": w.name, "label": w.name} for w in sorted(bpy.data.workspaces, key=lambda w: w.name.lower())]}


def a_workspace(params):
    name = str(params.get("name", ""))
    ws = bpy.data.workspaces.get(name)
    if ws is None:
        raise Failed(f'No workspace named "{name}"')
    win = main_window()
    if win is None:
        raise Failed("Blender has no window open")
    win.workspace = ws
    return {"value": name}


SHADING = ["WIREFRAME", "SOLID", "MATERIAL", "RENDERED"]


def a_shading(params):
    spaces = _need_views()
    kind = str(params.get("type", "next"))
    if kind in ("next", "previous"):
        i = SHADING.index(spaces[0].shading.type) if spaces[0].shading.type in SHADING else 0
        kind = SHADING[(i + (1 if kind == "next" else -1)) % len(SHADING)]
    if kind not in SHADING:
        raise Failed(f'Unknown shading "{kind}"')
    for s in spaces:
        s.shading.type = kind
    return {"value": kind}


def a_overlays(params):
    spaces = _need_views()
    on = _flag(params, spaces[0].overlay.show_overlays)
    for s in spaces:
        s.overlay.show_overlays = on
    return {"value": on}


def _xray_on(shading):
    return shading.show_xray_wireframe if shading.type == "WIREFRAME" else shading.show_xray


def a_xray(params):
    spaces = _need_views()
    on = _flag(params, _xray_on(spaces[0].shading))
    for s in spaces:
        # Wireframe keeps its own X-ray setting, as Blender's own toggle does.
        if s.shading.type == "WIREFRAME":
            s.shading.show_xray_wireframe = on
        else:
            s.shading.show_xray = on
    return {"value": on}


def a_render(params):
    animation = bool(params.get("animation", False))
    # INVOKE_DEFAULT: renders as F12 / Ctrl F12 do, in the render window, without blocking.
    _op(bpy.ops.render.render, "INVOKE_DEFAULT", animation=animation)
    return {"rendering": True, "animation": animation}


def a_save(_):
    if bpy.data.filepath:
        _op(bpy.ops.wm.save_mainfile)
        return {"saved": True, "file": os.path.basename(bpy.data.filepath)}
    # Never saved: Blender's own Save As file browser.
    _op(bpy.ops.wm.save_as_mainfile, "INVOKE_DEFAULT")
    return {"saved": False, "dialog": True}


def a_undo(_):
    _op(bpy.ops.ed.undo)
    return {"done": True}


def a_redo(_):
    _op(bpy.ops.ed.redo)
    return {"done": True}


BRUSH_LIMITS = {"size": (1, 10000), "strength": (0.0, 10.0)}


def a_brush(params):
    prop = str(params.get("property", "size"))
    if prop not in BRUSH_LIMITS:
        raise Failed(f'Unknown brush property "{prop}"')
    holder, _ = _brush_holder(prop)
    value, delta = _number(params, "value"), _number(params, "delta")
    current = getattr(holder, prop)
    if value is not None:
        target = value
    elif delta is not None:
        target = current + delta
    else:
        raise Failed("brush needs a value or a delta")
    lo, hi = BRUSH_LIMITS[prop]
    target = max(lo, min(hi, target))
    if prop == "size":
        target = int(round(target))
    setattr(holder, prop, target)
    return {"value": getattr(holder, prop)}


AXES = {"x": 0, "y": 1, "z": 2}


def _targets():
    ctx = bpy.context
    obs = list(ctx.selected_objects) if ctx.view_layer else []
    active = ctx.view_layer.objects.active if ctx.view_layer else None
    if active is not None and active not in obs:
        obs.insert(0, active)
    if not obs:
        raise Failed("No object is selected")
    return active or obs[0], obs


def _set_rotation(ob, axis, degrees):
    e = _euler(ob).copy()
    e[axis] = math.radians(degrees)
    if ob.rotation_mode == "QUATERNION":
        ob.rotation_quaternion = e.to_quaternion()
    elif ob.rotation_mode == "AXIS_ANGLE":
        axis_v, angle = e.to_quaternion().to_axis_angle()
        ob.rotation_axis_angle = (angle, *axis_v)
    else:
        ob.rotation_euler[axis] = math.radians(degrees)


def a_transform(params):
    if bpy.context.mode != "OBJECT":
        raise Failed("Transform works in Object Mode")
    prop = str(params.get("property", "location"))
    axis = AXES.get(str(params.get("axis", "x")).lower())
    if prop not in ("location", "rotation", "scale") or axis is None:
        raise Failed("transform needs property location / rotation / scale and axis x / y / z")
    value, delta = _number(params, "value"), _number(params, "delta")
    if value is None and delta is None:
        raise Failed("transform needs a value or a delta")
    active, obs = _targets()

    def read(ob):
        if prop == "rotation":
            return math.degrees(_euler(ob)[axis])
        return getattr(ob, prop)[axis]

    for ob in obs:
        target = value if value is not None else read(ob) + delta
        if prop == "rotation":
            _set_rotation(ob, axis, target)
        else:
            getattr(ob, prop)[axis] = target
    return {"value": round(read(active), 5), "objects": len(obs)}


MODES = {"OBJECT", "EDIT", "SCULPT", "VERTEX_PAINT", "WEIGHT_PAINT", "TEXTURE_PAINT", "POSE"}


def a_mode(params):
    mode = str(params.get("mode", "OBJECT")).upper()
    if mode not in MODES:
        raise Failed(f'Unknown mode "{mode}"')
    ob = bpy.context.view_layer.objects.active
    if ob is None:
        raise Failed("No active object")
    with bpy.context.temp_override(window=main_window(), active_object=ob, object=ob):
        if not bpy.ops.object.mode_set.poll():
            raise Failed("Blender can't change mode right now")
        try:
            bpy.ops.object.mode_set(mode=mode)
        except TypeError:
            raise Failed(f"{ob.name} has no {mode.replace('_', ' ').title()} mode") from None
        except RuntimeError as e:
            raise Failed(str(e).strip().splitlines()[-1]) from None
    return {"value": ob.mode}


ACTIONS = {
    "state": a_state,
    "playback": a_playback,
    "frame": a_frame,
    "jump": a_jump,
    "workspaces": a_workspaces,
    "workspace": a_workspace,
    "shading": a_shading,
    "overlays": a_overlays,
    "xray": a_xray,
    "render": a_render,
    "save": a_save,
    "undo": a_undo,
    "redo": a_redo,
    "brush": a_brush,
    "transform": a_transform,
    "mode": a_mode,
}

# Changes made by turning a dial; one undo step is pushed once the dial rests.
UNDOABLE = {"transform": "KnobDeck Transform"}


def run(action, params):
    fn = ACTIONS.get(action)
    if fn is None:
        raise Unsupported(action)
    try:
        return fn(params or {})
    except (Failed, Unsupported):
        raise
    except RuntimeError as e:
        # bpy operator errors: "Error: …" lines; keep the last.
        lines = [l for l in str(e).strip().splitlines() if l.strip()]
        raise Failed(lines[-1] if lines else "Blender reported an error") from None
