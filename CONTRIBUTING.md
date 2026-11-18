# Developing the KnobDeck add-on for Blender

One of the [KnobDeck companions](https://github.com/Knobdeck/knobdeck-companion). It speaks the
companion protocol (layer 2 of `knobdeck-protocol` in
[knobdeck-sdk](https://github.com/Knobdeck/knobdeck-sdk/tree/master/knobdeck-protocol): fixtures in
`fixtures/companion-v1.jsonl`, reference in `REFERENCE.md`) to KnobDeck's companion hub. Action ids
and their parameters are a contract with the `blender` plugin in
[knobdeck-plugins](https://github.com/Knobdeck/knobdeck-plugins/tree/master/blender): add, never
rename.

## Layout

- `addon/`: the extension (`blender_manifest.toml`, Blender 4.2+). No dependencies: Blender bundles
  no WebSocket module and extensions must be self-contained, so `ws.py` is a small RFC 6455 client
  on the standard library.
  - `hub.py`: discovery file, hello, ping, reconnect. Runs on its own thread and never touches
    `bpy`; requests go to a queue.
  - `__init__.py`: preferences, and a persistent `bpy.app.timers` timer that runs queued requests
    on Blender's main thread, sends a `state` event when what the keys show changes, and pushes
    one undo step after a transform dial rests.
  - `actions.py`: what each request does, with `bpy` only (never key events).
- `tests/fake_hub.py`: a stand-in for KnobDeck's hub (same discovery file, Host / Origin / token
  checks, refusals). `test_link.py` tests the link with plain Python; `blender_smoke.py` installs
  the built zip in a headless Blender and runs the actions; `e2e_plugin.py` plays KnobDeck between
  the real `blender` plugin and the add-on in a headless Blender.

## Build and test

Python 3.11+ and Blender 4.2+ on `PATH`:

```sh
ruff check . && ruff format --check .
python3 -m unittest discover -s tests -p "test_*.py"
blender --command extension validate addon
blender --command extension build --source-dir addon --output-dir dist

# headless; BLENDER_USER_RESOURCES keeps the test install out of your own Blender settings
BLENDER_USER_RESOURCES=$(mktemp -d) blender --background --factory-startup --python-exit-code 1 \
  --python tests/blender_smoke.py -- dist/knobdeck-1.0.0.zip

# with the real plugin: cargo build -p blender in knobdeck-plugins
python3 tests/e2e_plugin.py ../knobdeck-plugins/target/debug/blender blender dist/knobdeck-1.0.0.zip
```

Background Blender has no undo stack, no playback, no render window and doesn't run the event
loop that applies a workspace switch, so those are checked for a clean answer only; try them in a
real Blender. To run the add-on from this folder while developing, link `addon/` into a local
extensions repository (Preferences ▸ Get Extensions ▸ Repositories ▸ add a local one) or use
*Install from Disk* with a built zip. `KNOBDECK_COMPANION_DISCOVERY` points the add-on at another
discovery file.

CI (`.github/workflows/ci.yml`) runs the lint and link tests, then on Blender 4.2 LTS and the newest
release (downloaded from download.blender.org and cached) validates and builds the extension, runs
the smoke test and the end-to-end test with the `blender` plugin built from knobdeck-plugins master.

## Releasing

1. Bump `version` in `addon/blender_manifest.toml` and add a `CHANGELOG.md` entry. Merge to `master`.
2. **Actions → Release → Run workflow** on `master`.

`.github/workflows/release.yml` runs CI, creates the tag `v<version>` and a GitHub release with
`knobdeck-<version>.zip`, then uploads it to
[extensions.blender.org](https://extensions.blender.org/) as a new version of the `knobdeck`
extension (`POST /api/v1/extensions/knobdeck/versions/upload/`, see
[CI/CD](https://developer.blender.org/docs/features/extensions/ci_cd/)). Re-running it for a
released version only retries the upload.

The upload needs the secret `BLENDER_EXTENSIONS_TOKEN` in the `release` environment; without it
the job is skipped with a warning. To set it up:

1. Sign in to extensions.blender.org with the Blender ID that owns the listing.
2. The **first** version is uploaded by hand: **Upload Extension**, pick the zip from the first
   GitHub release, fill in the listing (description, icon, screenshots) and submit it for review.
   The API only adds versions to an extension that exists.
3. **Settings ▸ API Tokens** (extensions.blender.org/settings/tokens/) → create a token, and save
   it as `BLENDER_EXTENSIONS_TOKEN` in this repo's `release` environment
   (Settings ▸ Environments ▸ release).
