"""Which slots on the unit are the app's, and the cache of chains already built.

One table says which slots the app may write. Everything that writes asks it.

- The build slot (My Presets 255, "Tone Lab Preview") is where a chain is
  assembled. Nothing is kept there.
- The cache is the last 32 slots of the Tone Lab setlist, below the slot the
  old app uses (Tone Lab 255). A chain built once is stored there under a name
  made from its digest, and recalled after that: a recall takes 1 to 3 s, a
  build 11 to 17 s. A cache slot is reused oldest first.
- A saved preset goes to the lowest free slot of the Tone Lab setlist below
  the cache.

A slot is the app's to write only if the directory shows it empty or holding
a name the app gave it. The old app's working slots (My Presets 254, Tone Lab
255) are left to it while both exist.

The player's preset is borrowed with `borrowed`: what was selected is noted
on disk before anything else is loaded, and selected again afterwards, also
after a crash, by `recover` when the app next starts.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path

from lab.qc.unit import Moved, same_slot

MY_PRESETS = "/media/p4/Presets/My Presets"
TONE_LAB_NAME = "Tone Lab"
BUILD = {"folder_key": MY_PRESETS, "position": 255, "is_factory": False}
BUILD_NAME = "Tone Lab Preview"
OLD_APP = ({"folder_key": MY_PRESETS, "position": 254}, {"folder_key": "/media/p4/Presets/Tone Lab", "position": 255})
CACHE_SLOTS = range(222, 254)
CACHE_PREFIX = "TL "


UNSAVED = ("The preset on the Quad Cortex has unsaved changes (or the unit did not say). Save or discard them on "
           "the unit; Tone Lab leaves it alone until then.")


class NotOurs(RuntimeError):
    """The slot holds something the app did not put there. It was left untouched."""


def cache_name(digest):
    return f"{CACHE_PREFIX}{digest[:12]}"


class Slots:
    """The app's slots on one unit, and the record of what is cached in them."""

    def __init__(self, unit, folder):
        self.unit = unit
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.index_file = self.folder / "cache.json"
        self.marker = self.folder / "borrowed.json"
        self.directory = None
        self.tone_lab = None

    # ---- the directory ----

    def refresh(self):
        """Read the unit's directory, and find the Tone Lab setlist."""
        listing = self.unit.device.list_directory()
        self.directory = {row["key"]: row for row in listing.get("presets", [])}
        found = [row for row in self.directory.values()
                 if row.get("name", "").casefold() == TONE_LAB_NAME.casefold() and not row.get("is_factory")]
        if len(found) != 1:
            raise RuntimeError(f'Create a setlist named "{TONE_LAB_NAME}" on the Quad Cortex. The app keeps its '
                               "sounds there.")
        self.tone_lab = found[0]["key"]
        return self.directory

    def held(self, slot):
        """The name the directory shows in a slot, or "" for an empty one."""
        if self.directory is None:
            self.refresh()
        folder = self.directory.get(slot["folder_key"])
        if folder is None:
            raise RuntimeError("The Quad Cortex did not list that setlist.")
        files = {row["index"]: row for row in folder.get("files", [])}
        # The unit lists every slot of a setlist, empty ones too. A slot missing from the listing is not known to
        # be empty, so nothing is written there.
        if slot["position"] not in files:
            raise RuntimeError("The Quad Cortex listed the setlist incompletely. Nothing was written; try again.")
        return files[slot["position"]].get("name", "") or ""

    def claim(self, slot, name):
        """Refuse unless the slot is empty or already holds a preset the app named `name`."""
        if any(same_slot(slot, other) for other in OLD_APP):
            raise NotOurs("That slot belongs to the old Tone Lab.")
        held = self.held(slot)
        mine = held == name or (held.startswith(CACHE_PREFIX) and name.startswith(CACHE_PREFIX)
                                and slot.get("position") in CACHE_SLOTS)
        if held and not mine:
            raise NotOurs(f"The slot holds {held!r}. Tone Lab left it untouched.")

    def tone_lab_slot(self, position):
        if self.tone_lab is None:
            self.refresh()
        return {"folder_key": self.tone_lab, "position": position, "is_factory": False}

    def free_for_saving(self):
        """The lowest empty slot of the Tone Lab setlist below the cache."""
        self.refresh()
        for position in range(min(CACHE_SLOTS)):
            slot = self.tone_lab_slot(position)
            if not self.held(slot):
                return slot
        raise RuntimeError('The "Tone Lab" setlist is full.')

    # ---- the cache ----

    def index(self):
        try:
            return json.loads(self.index_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_index(self, index):
        self.index_file.write_text(json.dumps(index, indent=1), encoding="utf-8")

    def cached(self, digest):
        """The slot holding a chain with this digest, or None."""
        entry = self.index().get(digest)
        return self.tone_lab_slot(entry["position"]) if entry else None

    def place_for(self, digest):
        """A cache slot for a new chain: an empty one, or the one used longest ago."""
        index = self.index()
        taken = {entry["position"]: key for key, entry in index.items()}
        for position in CACHE_SLOTS:
            held = self.held(self.tone_lab_slot(position))
            # Empty, or holding a chain the app cached and has since lost track of.
            if position not in taken and (not held or held.startswith(CACHE_PREFIX)):
                return self.tone_lab_slot(position)
        oldest = min(index, key=lambda key: index[key]["used"], default=None)
        if oldest is None:
            raise RuntimeError("No cache slot is free: the Tone Lab setlist's last slots hold other presets.")
        return self.tone_lab_slot(index[oldest]["position"])

    def remember(self, digest, slot):
        index = {key: entry for key, entry in self.index().items() if entry["position"] != slot["position"]}
        index[digest] = {"position": slot["position"], "used": datetime.now(timezone.utc).isoformat()}
        self._write_index(index)

    def touch(self, digest):
        index = self.index()
        if digest in index:
            index[digest]["used"] = datetime.now(timezone.utc).isoformat()
            self._write_index(index)

    def forget(self, digest):
        index = self.index()
        if index.pop(digest, None) is not None:
            self._write_index(index)

    # ---- borrowing the player's preset ----

    def note_borrowed(self):
        position, scene = self.unit.position(), self.unit.scene()
        record = {"position": position, "scene": scene, "name": self.unit.name()}
        self.marker.write_text(json.dumps(record), encoding="utf-8")
        return record

    def may_leave(self):
        """Refuse to move away from a preset of the player's that has unsaved edits, or whose state the unit did
        not tell: recalling another preset would discard them."""
        if self.ours(self.unit.position()):
            return
        if self.unit.dirty() is not False:
            raise RuntimeError(UNSAVED)

    def ours(self, slot):
        """Whether a slot is one the app works in: the build slot, or any slot of the Tone Lab setlist,
        which is the app's (the player said so on 2026-11-19), saved sounds included."""
        if same_slot(slot, BUILD):
            return True
        return bool(self.tone_lab and slot and slot.get("folder_key") == self.tone_lab)

    def scratch(self, slot):
        """The build slot or a cache slot: the app's working slots, never a preset anybody saved."""
        return same_slot(slot, BUILD) or bool(self.tone_lab and slot and slot.get("folder_key") == self.tone_lab
                                              and slot.get("position") in CACHE_SLOTS)

    def give_back(self, record, also=None):
        """Select the player's preset and scene again, if the unit is still where the app left it: a working
        slot, the exact slot the app last put up (unless the player has edited it since), or `also`, a preset of
        the player's the app itself moved to (to read it). A preset the player chose on the unit meanwhile is left
        alone, saved Tone Lab presets included."""
        position = self.unit.position()
        left_here = same_slot(position, self.unit.slot) and self.unit.dirty() is False
        if self.scratch(position) or left_here or (also is not None and same_slot(position, also)):
            self.unit.slot = None
            self.unit.recall(record["position"])
            if self.unit.scene() != record["scene"]:
                self.unit.select_scene(record["scene"])
        self.marker.unlink(missing_ok=True)

    def recover(self):
        """After a crash: give back the preset that was borrowed, if a record of it was left."""
        try:
            record = json.loads(self.marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if self.tone_lab is None:
            try:
                self.refresh()
            except RuntimeError:
                pass
        self.give_back(record)
        return record

    @contextmanager
    def borrowed(self):
        """Work on the app's slots, and leave the player's preset selected afterwards."""
        self.may_leave()
        record = self.note_borrowed()
        try:
            yield record
        finally:
            try:
                self.give_back(record)
            except Moved:
                self.marker.unlink(missing_ok=True)
