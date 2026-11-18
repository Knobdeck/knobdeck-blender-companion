# Runs the installed add-on in a headless Blender for the end-to-end test (tests/e2e_plugin.py):
# installs the zip, enables it and pumps its timer until the file in KNOBDECK_SERVE_STOP exists
# (or 5 minutes pass). The discovery file comes
# from KNOBDECK_COMPANION_DISCOVERY.
#
#   BLENDER_USER_RESOURCES=<tmp> blender --background --factory-startup --python tests/blender_serve.py -- <zip>

import os
import sys
import time

import addon_utils
import bpy

MODULE = "bl_ext.user_default.knobdeck"

if not os.environ.get("BLENDER_USER_RESOURCES"):
    sys.exit("Set BLENDER_USER_RESOURCES to a temporary folder.")
zip_path = sys.argv[sys.argv.index("--") + 1]
bpy.ops.extensions.package_install_files(filepath=os.path.abspath(zip_path), repo="user_default", enable_on_install=False)
addon_utils.enable(MODULE, default_set=True, handle_error=None)
mod = sys.modules[MODULE]
print("SERVE ready", flush=True)
stop = os.environ.get("KNOBDECK_SERVE_STOP", "")
end = time.monotonic() + 300
while time.monotonic() < end and not (stop and os.path.exists(stop)):
    mod._pump()
    time.sleep(0.01)
mod.stop_link()
