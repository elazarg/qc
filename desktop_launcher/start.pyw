"""Open Tone Lab and report startup failures even under pythonw.

Two desks can be opened: the old one (`tone_lab`, the default for now) and the
rewritten one (`python -m lab`). The first of these that says which wins:

1. `--new` or `--old` on the command line (Tone Lab.exe passes its own on);
2. the environment variable TONE_LAB_DESK set to `new` or `old`;
3. `{"desk": "new"}` in `launcher.json` in the data folder
   (`%LOCALAPPDATA%\\Tone Lab`, or TONE_LAB_HOME when that is set).

Other arguments go to the new desk, such as `--simulated`.
"""

import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback


ROOT = Path(__file__).resolve().parents[1]
VISIBLE_ERROR = 0x10 | 0x10000 | 0x40000  # MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST
DESKS = ("old", "new")


def write_log(message):
    for path in (ROOT / "tone_sessions" / "startup.log", Path(tempfile.gettempdir()) / "Tone Lab startup.log"):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as log:
                log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{os.getpid()}] {message}\n")
            return path
        except OSError:
            continue
    return None


def setting_file(environ=os.environ):
    """Where the choice of desk is kept, found without creating the data folder (as `lab.knowledge.home`)."""
    chosen = environ.get("TONE_LAB_HOME")
    if chosen:
        return Path(chosen) / "launcher.json"
    base = environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    return Path(base) / "Tone Lab" / "launcher.json"


def chosen_desk(arguments, environ=os.environ):
    """"old" or "new": the command line first, then TONE_LAB_DESK, then the setting; "old" when none says."""
    for desk in DESKS:
        if f"--{desk}" in arguments:
            return desk
    value = environ.get("TONE_LAB_DESK", "").strip().lower()
    if value in DESKS:
        return value
    try:
        value = json.loads(setting_file(environ).read_text(encoding="utf-8")).get("desk", "")
    except (OSError, ValueError, AttributeError):
        return "old"
    return value if value in DESKS else "old"


def open_new_desk(arguments):
    from lab.desk import main as desk

    desk([argument for argument in arguments if argument not in ("--new", "--old")])


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    desk = chosen_desk(arguments)
    write_log("Opening Tone Lab" + (" (the new desk)" if desk == "new" else ""))
    sys.path.insert(0, str(ROOT))
    try:
        if desk == "new":
            open_new_desk(arguments)
        else:
            from tone_lab import desktop

            desktop.open_desk()
    except Exception as error:
        log = write_log(traceback.format_exc())
        message = f"Tone Lab couldn’t open.\n\n{error}"
        if log:
            message += f"\n\nStartup log:\n{log}"
        ctypes.windll.user32.MessageBoxW(None, message, "Tone Lab startup", VISIBLE_ERROR)
        return 2
    write_log("Launch finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
