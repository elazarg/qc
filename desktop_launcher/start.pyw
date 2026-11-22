"""Open Tone Lab and report startup failures even under pythonw.

Arguments go to the desk, such as `--simulated`. `--new` is accepted and means nothing (shortcuts made while
there were two desks pass it); `--old` asked for the earlier app, which is no longer part of the program, and
says so.

Each start is noted in `startup.log` in the data folder, and a failure to open
is written there with its trace and shown. This process, not the launcher that
started it, owns the window, so it is this process that takes Tone Lab's
taskbar identity: the window then sits on the pinned shortcut.

Nothing of the app is imported before the log's place is known, so that whatever
goes wrong importing it is caught and said.
"""

import ctypes
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile
import traceback


ROOT = Path(__file__).resolve().parents[1]
VISIBLE_ERROR = 0x10 | 0x10000 | 0x40000  # MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST
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


def take_identity():
    """Give this process Tone Lab's taskbar identity. The launcher has it too, but it is gone seconds after it
    starts this process, and a window belongs on the taskbar to the process that made it."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass                    # not Windows, or a Windows without it: the window opens all the same


def open_desk(arguments):
    if "--old" in arguments:
        raise RuntimeError("The earlier app (--old) is no longer part of Tone Lab. Start it without --old. "
                           "The earlier app is in the repository's history, at the tag with-tone_lab.")
    from lab.desk.window import main as desk

    desk([argument for argument in arguments if argument != "--new"])


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    write_log("Opening Tone Lab")
    sys.path.insert(0, str(ROOT))
    try:
        take_identity()
        open_desk(arguments)
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
