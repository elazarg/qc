"""Open Tone Lab and report startup failures even under pythonw.

Two desks can be opened: the rewritten one (`python -m lab`, the default) and
the earlier one (`tone_lab`), for as long as it is still in the program folder.
The first of these that says which wins:

1. `--new` or `--old` on the command line (Tone Lab.exe passes its own on);
2. the environment variable TONE_LAB_DESK set to `new` or `old`;
3. `{"desk": "old"}` (or "new") in `launcher.json` in the data folder
   (`%LOCALAPPDATA%\\Tone Lab`, or TONE_LAB_HOME when that is set).

Other arguments go to the new desk, such as `--simulated`.

Each start is noted in `startup.log` in the data folder, and a failure to open
is written there with its trace and shown. This process, not the launcher that
started it, owns the window, so it is this process that takes Tone Lab's
taskbar identity: the window then sits on the pinned shortcut.

Nothing of the app is imported before the desk is chosen and the log's place is
known, so that whatever goes wrong importing it is caught and said.
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
APP_ID = "ToneLab.QuadCortex.Desktop"     # as in Program.cs and the shortcut `python -m lab shortcut` makes
LOG = "startup.log"
LOG_LIMIT = 200_000                       # bytes; past it the older half is dropped


def data_folder(environ=os.environ):
    """The data folder, found without creating it (as `lab.knowledge.home` finds it)."""
    chosen = environ.get("TONE_LAB_HOME")
    if chosen:
        return Path(chosen)
    base = environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    return Path(base) / "Tone Lab"


def log_files(environ=os.environ):
    """Where a start is noted: the data folder, and the temporary folder when that cannot be written.

    The data folder is not created for the log: the app makes it on its first start, and then moves in what an
    earlier version kept elsewhere, which it does only while the folder is not there yet."""
    folder = data_folder(environ)
    return ([folder / LOG] if folder.is_dir() else []) + [Path(tempfile.gettempdir()) / "Tone Lab startup.log"]


def write_log(message, environ=os.environ):
    for path in log_files(environ):
        try:
            if path.is_file() and path.stat().st_size > LOG_LIMIT:
                kept = path.read_bytes()[-LOG_LIMIT // 2:]
                path.write_bytes(kept[kept.find(b"\n") + 1:])
            with path.open("a", encoding="utf-8") as log:
                log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{os.getpid()}] {message}\n")
            return path
        except OSError:
            continue
    return None


def setting_file(environ=os.environ):
    """Where the choice of desk is kept, found without creating the data folder."""
    return data_folder(environ) / "launcher.json"


def chosen_desk(arguments, environ=os.environ):
    """"old" or "new": the command line first, then TONE_LAB_DESK, then the setting; "new" when none says."""
    for desk in DESKS:
        if f"--{desk}" in arguments:
            return desk
    value = environ.get("TONE_LAB_DESK", "").strip().lower()
    if value in DESKS:
        return value
    try:
        value = json.loads(setting_file(environ).read_text(encoding="utf-8")).get("desk", "")
    except (OSError, ValueError, AttributeError):
        return "new"
    return value if value in DESKS else "new"


def take_identity():
    """Give this process Tone Lab's taskbar identity. The launcher has it too, but it is gone seconds after it
    starts this process, and a window belongs on the taskbar to the process that made it."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass                    # not Windows, or a Windows without it: the window opens all the same


def open_new_desk(arguments):
    from lab.desk.window import main as desk

    desk([argument for argument in arguments if argument not in ("--new", "--old")])


def open_old_desk():
    if not (ROOT / "tone_lab").is_dir():
        raise RuntimeError("The earlier app is no longer in the program folder, so there is nothing for --old to "
                           "open. Start Tone Lab without --old; if `launcher.json` in the data folder or "
                           "TONE_LAB_DESK says \"old\", remove that.")
    from tone_lab import desktop

    desktop.open_desk()


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    desk = chosen_desk(arguments)
    write_log("Opening Tone Lab" + ("" if desk == "new" else " (the earlier desk)"))
    sys.path.insert(0, str(ROOT))
    try:
        take_identity()
        if desk == "new":
            open_new_desk(arguments)
        else:
            open_old_desk()
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
