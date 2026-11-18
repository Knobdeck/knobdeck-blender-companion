# Changelog

## 1.0.0

- First release, for Blender 4.2 and newer on Windows, macOS and Linux.
- Links Blender to KnobDeck's companion hub on `127.0.0.1` (no port to set, no password: the
  connection details come from KnobDeck's own discovery file) and runs the KnobDeck `blender`
  plugin's actions with Blender's Python API: play / pause, frame dial, jump to start / end /
  keyframes, workspaces, viewport shading, overlays, X-ray, render image / animation, save,
  undo / redo, interaction mode, brush size and strength dials, and the selected objects'
  location / rotation / scale on a dial (one undo step per turn).
- Sends the current mode, active object, frame, playback, shading, overlays, brush and transform
  to KnobDeck, so keys show live values.
