# KnobDeck add-on for Blender

A Blender add-on (extension) that lets the [KnobDeck](https://knobdeck.harshalkudale.com) Blender
plugin control Blender through Blender's own Python API, instead of sending keyboard shortcuts.
Turn a dial to scrub the timeline, size the brush or move the selected objects; press a key to
switch workspace, change the viewport shading or render. Keys show live values: the frame, the
brush size, the mode and active object.

Works with Blender 4.2 and newer on Windows, macOS and Linux, and KnobDeck with the companion hub.

## Install

1. In KnobDeck, install the **Blender** plugin (Get Plugins).
2. In Blender: **Edit ▸ Preferences ▸ Get Extensions**, search for **KnobDeck**, *Install*.
   Or download `knobdeck-<version>.zip` from a
   [release](https://github.com/Knobdeck/knobdeck-blender-companion/releases) and use
   **Get Extensions ▸ ⌄ ▸ Install from Disk…**.

That's all: there is no port or password to set. The add-on finds KnobDeck by itself and
reconnects when KnobDeck or Blender restarts. Its preferences (Edit ▸ Preferences ▸ Add-ons ▸
KnobDeck) show whether Blender is linked and let you turn the link off.

## What it does

| KnobDeck action | In Blender |
|---|---|
| Play / Pause | starts or stops timeline playback (the key shows which) |
| Frame (dial) | steps the current frame; drag to go to a frame |
| Jump to Start / End, Previous / Next Keyframe | the scene's (or preview) range ends; keyframes of what is shown |
| Workspace, Workspaces wheel | switches the window's workspace |
| Object Interaction Mode | Object, Edit, Sculpt, Vertex / Weight / Texture Paint, Pose |
| Mode and Object | shows the current mode and active object ("Sculpt · Cube") |
| Viewport Shading | Wireframe, Solid, Material Preview, Rendered, or the next / previous one |
| Overlays, X-Ray | toggles them in the workspace's 3D Viewports |
| Render Image, Render Animation | renders as F12 / Ctrl F12 do, in Blender's render window |
| Save | saves the file (Save As for a file that was never saved) |
| Undo, Redo | Blender's undo history |
| Brush Size, Brush Strength (dials) | the active brush in sculpt and paint modes (unified settings when on) |
| Object Transform (dial) | the selected objects' location, rotation (degrees) or scale on one axis, in Object Mode; one undo step per turn |

Actions go to the Blender you used last when more than one is open.

## Privacy

It connects only to KnobDeck on your own computer and collects nothing: see
[PRIVACY.md](PRIVACY.md).

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). This is one of the
[KnobDeck companions](https://github.com/Knobdeck/knobdeck-companion); the plugin it talks to is
[`blender`](https://github.com/Knobdeck/knobdeck-plugins/tree/master/blender) in knobdeck-plugins.
