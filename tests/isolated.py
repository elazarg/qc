"""Import this before anything of `lab` in a test module: the tests then cannot reach the player's things.

Importing it does two things, once for the process:

- The app's data folder (`lab.knowledge.home()`, `TONE_LAB_HOME`) is a new temporary folder, removed when the
  process ends. A test that forgets to pass a folder of its own writes there, not into the player's data.
  The folder starts with an empty table of the unit's named settings: without one the app would copy the
  player's own table in from the program folder (`knowledge.device_path`).
- `TONE_LAB_TESTING` is set. With it the three doors to the real world raise instead of opening: the real
  Quad Cortex (`lab.qc.device._library`), its audio driver (`lab.hearing.audio.Audio`) and the real Codex
  (`lab.sound.assistant.Assistant._codex`, unless `TONE_LAB_CODEX` names a stand-in).

Both are environment variables, so the engine's own process, which `Host` starts, has them too.

`tests/test_isolation.py` checks that every test module of the new app imports this first.
"""

import atexit
import os
from pathlib import Path
import shutil
import tempfile

FLAG = "TONE_LAB_TESTING"

if not (os.environ.get(FLAG) and os.environ.get("TONE_LAB_HOME")):    # a process a test started has both
    HOME = Path(tempfile.mkdtemp(prefix="tone-lab-tests-"))
    (HOME / "device-presets.json").write_text("{}", encoding="utf-8")
    os.environ["TONE_LAB_HOME"] = str(HOME)
    os.environ[FLAG] = "1"
    atexit.register(shutil.rmtree, HOME, ignore_errors=True)
else:
    HOME = Path(os.environ["TONE_LAB_HOME"])
