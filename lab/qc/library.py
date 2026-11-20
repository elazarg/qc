"""The presets saved on the unit: which setlist, which slot, under what name.

`directory(engine)` reads the unit's directory where the unit is held (it is
a host command: `host.call("lab.qc.library.directory")`), keeps what the player
saved, and writes it to `directory.json` in the app's home for `cached` to
read without the unit.

What the unit's directory looks like (CorOS 4.1):

- Each setlist is a table of 256 slots, empty ones included with a blank
  name; a slot's place in the table is its position for recalling it.
- The player's setlists have keys under `/media/p4/Presets/` or short ids
  such as `1_q`. Factory folders are flagged `is_factory`, cloud downloads
  `is_downloads`; plug-in banks live under `/opt/neuraldsp/` without either
  flag, and are not the player's either.
- A slot can be read-only, and cannot then be the player's own preset.

The slots the app works in are left out: the build slot, the cache, and the
old app's working slots (see `slots.py`).
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from lab import knowledge
from lab.qc.slots import BUILD, BUILD_NAME, CACHE_PREFIX, CACHE_SLOTS, MY_PRESETS, OLD_APP, TONE_LAB_NAME

FILE = "directory.json"
SCENES = "ABCDEFGH"
OLD_NAMES = ("Tone Lab ",)   # names the old app gave its working slots: "Tone Lab Style", "Tone Lab Live B", ...


def user_setlist(folder):
    """Whether a folder of the directory is one of the player's setlists."""
    key = folder.get("key", "")
    if folder.get("is_factory") or folder.get("is_downloads"):
        return False
    return key.startswith("/media/p4/Presets/") or bool(re.fullmatch(r"\d+_q", key))


def setlist_name(folder):
    return folder.get("name") or folder.get("key", "").rsplit("/", 1)[-1]


def working_slot(slot, name, setlist):
    """Whether a slot is one the app (old or new) works in, and not a sound the player keeps."""
    position = slot["position"]
    if (any(slot["folder_key"] == other["folder_key"] and position == other["position"] for other in (BUILD, *OLD_APP))
            and (name == BUILD_NAME or name.startswith(OLD_NAMES))):
        return True
    return setlist.casefold() == TONE_LAB_NAME.casefold() and position in CACHE_SLOTS and name.startswith(CACHE_PREFIX)


def entries(listing, *, factory=False):
    """The named presets in a directory listing, in the order the desk shows them.

    Each is {"slot": {"folder_key", "position", "is_factory"}, "name", "setlist"}.
    Factory presets are left out unless `factory`, and are then flagged by
    the slot's "is_factory".
    """
    rows = []
    for folder in (listing or {}).get("presets", []):
        is_factory = bool(folder.get("is_factory"))
        if not user_setlist(folder) and not (factory and is_factory):
            continue
        setlist = setlist_name(folder)
        for file in folder.get("files", []):
            name, position = (file.get("name") or "").strip(), file.get("index")
            if not name or not isinstance(position, int) or not 0 <= position < 256:
                continue
            if file.get("is_readonly") and not is_factory:
                continue
            slot = {"folder_key": folder["key"], "position": position, "is_factory": is_factory}
            if not is_factory and working_slot(slot, name, setlist):
                continue
            rows.append({"slot": slot, "name": name, "setlist": setlist})
    rows.sort(key=lambda row: (row["slot"]["is_factory"], row["slot"]["folder_key"] != MY_PRESETS,
                               row["setlist"].casefold(), row["slot"]["folder_key"], row["slot"]["position"]))
    return rows


def place(position):
    """Where a slot is on the unit's footswitches: bank and switch, "1A" for the first."""
    bank, switch = divmod(position, len(SCENES))
    return f"{bank + 1}{SCENES[switch]}"


def _write(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    spare = path.with_suffix(".tmp")
    spare.write_text(json.dumps(record, indent=1), encoding="utf-8")
    os.replace(spare, path)


def directory(engine, *, stop=None, factory=False, folder=None):
    """Read the unit's presets where the unit is held, remember them, and return them (see `entries`).

    A listing without My Presets is a read that failed: it raises and the
    remembered directory is kept.
    """
    if stop is not None and stop.is_set():
        raise RuntimeError("Stopped.")
    device = engine.unit.device
    listing = device.list_directory()
    if not listing or not any(row.get("key") == MY_PRESETS for row in listing.get("presets", [])
                              if user_setlist(row)):
        raise RuntimeError("The Quad Cortex did not list its setlists. The presets read before were kept.")
    rows = entries(listing, factory=factory)
    _write(Path(folder) / FILE if folder is not None else knowledge.home() / FILE,
           {"read": datetime.now(timezone.utc).isoformat(), "firmware": getattr(device, "firmware", None),
            "presets": rows})
    return rows


def cached(folder=None):
    """The presets from the last directory read, or None when none has been read. `folder` defaults to the app's home."""
    path = Path(folder) / FILE if folder is not None else knowledge.home() / FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        rows = record["presets"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return rows if isinstance(rows, list) else None
