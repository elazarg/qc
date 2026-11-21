"""Where the Guitar Learner keeps its recordings and labels. Every path the learner uses starts here.

The learner's data lives in `learner` inside the app's data folder
(`lab.knowledge.home()`, or the desk's own folder when the desk is simulated:
the seat says which with `use`). It is resolved each time it is asked for,
never at import.

    learner/
      recordings/clip-…/            takes recorded with Record clip
      guitar_learner/…              labelled excerpts per guitar and layout, clips kept from live listening
      guitar_input/<guitar>/…       what was taught about one control at a time, and the gesture mappings
      live-driver.log               where a hung audio driver leaves its stack

Until the app had a data folder, all of this was kept in `tone_sessions`
inside the program folder. `bring_old` copies it here once; nothing there is
changed or deleted. Paths written into records are relative to the data
folder (`stored`), and an old absolute path into a `tone_sessions` folder is
read as its copy here (`found`).
"""

from datetime import datetime
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
import shutil
import traceback
from uuid import uuid4

from lab.knowledge import REPO

OLD = REPO / "tone_sessions"
BROUGHT = ("guitar_learner", "recordings", "guitar_input")       # what `bring_old` copies
MOVED_NOTE = "MOVED.txt"
CHOSEN = None           # the folder the seat chose (`use`); None is `home() / "learner"`
LOG = logging.getLogger("lab.learner")
LOG.addHandler(logging.NullHandler())   # `log_to` adds the handler that writes


def use(folder):
    """Keep the learner's data in `folder` from now on (the desk's data folder, simulated or not)."""
    global CHOSEN
    CHOSEN = Path(folder) if folder is not None else None


def root():
    if CHOSEN is not None:
        return Path(CHOSEN)
    from lab.knowledge import home

    return home() / "learner"


def learner():
    """Labelled excerpts, one folder per guitar and layout, and the remembered layout."""
    return root() / "guitar_learner"


def recordings():
    """Takes recorded with Record clip, here and in the earlier app."""
    return root() / "recordings"


def live_recordings():
    """Clips kept from live listening."""
    return learner() / "live_recordings"


def layout_choice():
    return learner() / "charvel-layout.json"


def slug(instrument):
    """The folder name for one instrument: readable, and distinct for names that differ only in punctuation."""
    key = instrument.strip().casefold()
    prefix = re.sub(r"[^a-z0-9]+", "-", key).strip("-")[:35] or "guitar"
    return f"{prefix}-{sha256(key.encode()).hexdigest()[:8]}"


def taught(instrument):
    """What was taught about one guitar's controls, one at a time: examples, profiles and gesture mappings.

    The folder is still called `guitar_input`, as the earlier app named it, so that what it saved is found."""
    return taught_instruments() / slug(instrument)


def taught_instruments():
    return root() / "guitar_input"


def crash_log():
    return root() / "live-driver.log"


# ---- paths in records ----

def stored(path):
    """How a path is written into a record: relative to the data folder when it is inside it."""
    path = Path(path)
    try:
        return path.resolve().relative_to(root().resolve()).as_posix()
    except ValueError:
        return str(path)


def found(text):
    """The file a stored path names. A relative path is inside the data folder. An absolute path written by the
    earlier app into its `tone_sessions` folder is read as the copy here when the original is gone or the copy
    exists, so records made before the move keep pointing at their audio."""
    path = Path(str(text))
    if not path.is_absolute():
        return root() / path
    parts = [part.casefold() for part in path.parts]
    if OLD.name in parts:
        inside = Path(*path.parts[len(parts) - parts[::-1].index(OLD.name):]) if parts[-1] != OLD.name else Path()
        copy = root() / inside
        if copy.exists() or not path.exists():
            return copy
    return path


# ---- the move out of the program folder ----

def bring_old(old=None, new=None):
    """Copy the learner's data from where the earlier app kept it (`old`, the program folder's `tone_sessions`)
    into the data folder (`new`), once: only when `new` holds none of it yet and `old` holds some of it. An
    empty `new` (made by something that only asked where it is) does not stop the copy; a `new` that already
    holds any of the learner's folders does, since nothing is ever merged or written over.

    The copy is made in a temporary folder beside `new` and renamed into place when complete, so an interrupted
    copy is never mistaken for the data. `old` is left exactly as it was, with a note saying where the copy is.
    Returns True when data was copied."""
    old, new = Path(old or OLD), Path(new or root())
    if any((new / name).exists() for name in BROUGHT) or not any((old / name).is_dir() for name in BROUGHT):
        return False
    new.parent.mkdir(parents=True, exist_ok=True)
    staging = new.with_name(new.name + ".copying")
    if staging.exists():
        shutil.rmtree(staging)          # left by an interrupted copy; it is ours, and `old` is intact
    staging.mkdir()
    for name in BROUGHT:
        if (old / name).is_dir():
            shutil.copytree(old / name, staging / name)
    if new.exists():
        for name in BROUGHT:            # `new` is there but holds none of these: each goes in whole
            if (staging / name).is_dir():
                (staging / name).replace(new / name)
        shutil.rmtree(staging)
    else:
        staging.replace(new)
    try:
        note = old / MOVED_NOTE
        if not note.exists():
            note.write_text(
                f"The Guitar Learner's data ({', '.join(BROUGHT)}) was copied to {new} on "
                f"{datetime.now():%Y-%m-%d %H:%M}.\n"
                "The folders here are kept as they were. The new app no longer reads or writes them.\n",
                encoding="utf-8")
    except OSError:
        pass
    return True


# ---- reading and writing ----

def read_json(path):
    """A JSON object from a file, or {} when it is missing or unreadable."""
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


# ---- faults ----

class _ErrorsLog(logging.Handler):
    """Warnings and faults, with their traces, appended to the app's errors.log. The file is opened for each
    entry and closed again, so it is never held."""

    def __init__(self, path):
        super().__init__(logging.WARNING)
        self.path = Path(path)

    def emit(self, record):
        try:
            trace = "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else ""
            with open(self.path, "a", encoding="utf-8") as log:
                log.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} Guitar Learner: {record.getMessage()}\n{trace}")
        except Exception:  # noqa: BLE001 - a log that cannot be written must not stop the work
            pass


def log_path():
    """Where the learner's faults are being written, or None."""
    return next((str(handler.path) for handler in LOG.handlers if isinstance(handler, _ErrorsLog)), None)


def log_to(path):
    """Write the learner's warnings and faults to `path` (the data folder's errors.log) from now on."""
    for handler in [handler for handler in LOG.handlers if isinstance(handler, _ErrorsLog)]:
        LOG.removeHandler(handler)
    if path is not None:
        LOG.addHandler(_ErrorsLog(path))
