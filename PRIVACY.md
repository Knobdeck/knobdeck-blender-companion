# Privacy

The KnobDeck add-on for Blender:

- **collects nothing and sends nothing to the internet.** It has no analytics, no telemetry and no
  crash reporting. Its only connection is to the KnobDeck app on your own computer
  (`127.0.0.1`), so it works with Blender's *Allow Online Access* turned off.
- **reads one file outside Blender:** the connection file KnobDeck writes in its own settings
  folder (`companion-hub.json`: a port number and a token that changes every time KnobDeck
  starts; readable only by your user account).
- **answers only KnobDeck**, which asks it to run Blender actions and to report what its keys
  show: the current mode, the active object's name and transform, the frame, playback, viewport
  shading, overlays, brush size and strength, the open file's name and the list of workspaces.
  That information goes no further than KnobDeck on the same computer.
- **stores only** a random install id in its own extension folder (so KnobDeck can tell your
  Blender apart from another one) and its on/off setting in Blender's preferences.

For KnobDeck itself see the [KnobDeck privacy policy](https://knobdeck.harshalkudale.com/legal/privacy).
Questions: support@harshalkudale.com.
