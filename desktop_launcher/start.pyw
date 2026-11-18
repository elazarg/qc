"""Open Tone Lab and report startup failures even under pythonw."""

import ctypes
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile
import traceback


ROOT = Path(__file__).resolve().parents[1]
VISIBLE_ERROR = 0x10 | 0x10000 | 0x40000  # MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST


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


def main():
    write_log("Opening Tone Lab")
    sys.path.insert(0, str(ROOT))
    try:
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
