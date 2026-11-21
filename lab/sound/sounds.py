"""Sounds, songs and instruments, and where they are kept.

- A **sound** is a chain with a name and a history: where it came from
  (designed from a request, read from a preset on the unit, revised from
  another sound, imported), what was measured of it, what the player said,
  its knob positions, and where it is saved.
- A **song** has an instrument, a phrase, and ordered **parts** (rhythm,
  lead, clean...). A part is a sound, or one scene of a sound.
- An **instrument** is a guitar or a bass the player owns: which input it is
  plugged into, its pickup settings, its notes for the designer.

Each is a small JSON file under `knowledge.home()`, written whole beside the
old one and then put in its place, so a write that is cut short leaves the
old file. A file that cannot be read is never written over: it is set aside
with `.bad` after its name. A record may hold fields this version does not
know (a later version wrote it); they are left out, not a reason to lose the
sound. Recorded phrases live in the `phrases` folder made here; the desk
reads and writes them.
"""

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from lab.sound import catalog
from lab.sound.chain import Chain, TOLERANCE
from lab.sound.knobs import normalize     # knobs once named for a mechanism are carried to their quality

INSTRUMENTS = (
    {"name": "Charvel", "kind": "electric", "input": 1,
     "settings": ["Bridge", "Outer coils", "Both humbuckers", "Inner coils", "Neck"], "wiring": ["Series", "Parallel"],
     "about": "Charvel Pro-Mod DK24 HH: Seymour Duncan Full Shred bridge, Alnico II Pro neck, a 5-way blade and a "
              "series/parallel mini-toggle, no-load tone."},
    {"name": "Les Paul", "kind": "electric", "input": 1, "settings": ["Bridge", "Both", "Neck"], "wiring": [],
     "about": "Gibson Les Paul Studio, two humbuckers, a three-way switch, a volume and a tone for each pickup."},
    {"name": "Bass", "kind": "bass", "input": 1, "settings": [], "wiring": [], "about": "Electric bass."},
    {"name": "Acoustic", "kind": "acoustic", "input": 1, "settings": [], "wiring": [], "about": "Acoustic guitar with a pickup."},
    {"name": "Classical", "kind": "classical", "input": 1, "settings": [], "wiring": [], "about": "Classical (nylon) guitar with a pickup."},
)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id():
    return uuid4().hex[:12]


@dataclass
class Sound:
    name: str
    chain: Chain
    origin: dict                          # {"kind": "designed" | "preset" | "revised" | "imported", ...}
    id: str = field(default_factory=new_id)
    created: str = field(default_factory=now)
    parent: str | None = None
    explanation: str = ""
    sources: list = field(default_factory=list)
    heard: dict | None = None             # what the app measured: {"phrase", "digest", "knobs", "words", ...}
    knobs: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)   # [{"at", "text"}], what the player said
    tags: list = field(default_factory=list)   # the app's: aim, what listening heard
    marks: list = field(default_factory=list)  # the player's: "keeper", "not this", or any word
    played: bool = False
    saved: dict | None = None             # {"slot", "name"} once written to the unit for keeps

    def to_record(self):
        record = asdict(self)
        record["chain"] = self.chain.to_record()
        return record

    @classmethod
    def from_record(cls, record):
        """A sound from its record. Fields this version does not know are left out; a record that is not a
        sound's (no chain, not a mapping) raises ValueError."""
        try:
            known = {item.name for item in fields(cls)}
            kept = {key: value for key, value in record.items() if key in known}
            return cls(**{"name": "Untitled", "origin": {}, **kept, "chain": Chain.from_record(record["chain"]),
                          "knobs": normalize(record.get("knobs") or {})})
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"Not a sound's record: {error!r}") from error


def write_whole(path, text):
    """Write a file whole: beside the old one under a name of its own, then into its place. Another program
    reading the old file at that moment (a virus scanner, the command line) makes Windows refuse the swap for
    an instant, so it is tried a few times."""
    path = Path(path)
    part = path.with_name(f"{path.name}.{uuid4().hex[:8]}.part")
    try:
        part.write_text(text, encoding="utf-8")
        for attempt in range(6):
            try:
                os.replace(part, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.05 * (attempt + 1))
    finally:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass


def set_aside(path):
    """Move a file that cannot be read out of the way, as `<name>.bad`, and return where it went (None when it
    could not be moved). What it held stays on disk for the player to mend."""
    path = Path(path)
    spare = path.with_name(path.name + ".bad")
    try:
        os.replace(path, spare)
    except OSError:
        return None
    return spare


class Store:
    """The player's sounds, songs and instruments on disk. `aside` lists the files found unreadable and set
    aside since this store was opened, for the desk to say."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.aside = []
        for name in ("sounds", "songs", "phrases"):
            (self.folder / name).mkdir(parents=True, exist_ok=True)

    # ---- sounds ----

    def save(self, sound):
        write_whole(self.folder / "sounds" / f"{sound.id}.json", json.dumps(sound.to_record(), indent=1))
        return sound

    def sound(self, sound_id):
        """The sound with this id. Raises OSError when there is none and ValueError when its file is not a
        sound's."""
        return Sound.from_record(json.loads((self.folder / "sounds" / f"{sound_id}.json").read_text(encoding="utf-8")))

    def sounds(self):
        found = []
        for path in (self.folder / "sounds").glob("*.json"):
            try:
                found.append(Sound.from_record(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
        return sorted(found, key=lambda sound: sound.created)

    # ---- instruments ----

    def instruments(self):
        """The player's instruments. The first time, the list the app starts with is written for the player to
        edit. A list that cannot be read is set aside (`instruments.json.bad`) and the starting list is used,
        without being written over it."""
        path = self.folder / "instruments.json"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if not path.with_name(path.name + ".bad").exists():
                write_whole(path, json.dumps(list(INSTRUMENTS), indent=1))
            return list(INSTRUMENTS)
        except OSError:
            return list(INSTRUMENTS)
        try:
            found = json.loads(text)
            if not isinstance(found, list) or not all(isinstance(item, dict) and item.get("name") for item in found):
                raise ValueError("not a list of instruments")
        except ValueError:
            spare = set_aside(path)
            if spare is not None:
                self.aside.append(spare)
            return list(INSTRUMENTS)
        return found

    def instrument(self, name):
        return next((item for item in self.instruments() if item["name"].casefold() == (name or "").casefold()), None)

    # ---- songs ----

    def _song_file(self, name):
        """Where a song of this name is kept: under its slug, or under a mark of its own when the name has no
        letters or digits, is too long for a file name, or is one Windows keeps for itself."""
        base = slug(name)
        if base and len(base) <= SLUG and base not in RESERVED:
            return self.folder / "songs" / f"{base}.json"
        mark = sha256(_key(name).encode("utf-8")).hexdigest()[:8]
        return self.folder / "songs" / f"{base[:SLUG - 20].strip('-') or 'song'}-{mark}.json"

    def _song_at(self, path, name):
        """The song kept in a file when it is this one, None when the file is absent, unreadable or another's."""
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) and _key(str(record.get("name", ""))) == _key(name) else None

    def song(self, name):
        """The song of this name, or a new one. Names that differ only in case, spacing or punctuation are one
        song. A song kept where earlier versions kept it (`_old_slug`) is still found."""
        for path in (self._song_file(name), self.folder / "songs" / f"{_old_slug(name)}.json"):
            record = self._song_at(path, name)
            if record is not None:
                return {"instrument": None, "phrase": None, **record, "parts": list(record.get("parts") or [])}
        return {"name": name, "instrument": None, "phrase": None, "parts": []}

    def save_song(self, song):
        """Keep a song in its own file. A file in its place that holds another song (earlier versions kept every
        name without Latin letters in one file) is moved to that song's own place first; one that cannot be
        read is set aside."""
        name = song["name"]
        target = self._song_file(name)
        if target.exists() and self._song_at(target, name) is None:
            try:
                held = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                held = None
            own = self._song_file(held["name"]) if isinstance(held, dict) and isinstance(held.get("name"), str) \
                and held["name"] else None
            if own is None:
                spare = set_aside(target)
                if spare is not None:
                    self.aside.append(spare)
            elif own != target and not own.exists():
                write_whole(own, json.dumps(held, indent=1))
        write_whole(target, json.dumps(song, indent=1))
        old = self.folder / "songs" / f"{_old_slug(name)}.json"
        if old != target and self._song_at(old, name) is not None:
            try:
                old.unlink()                        # where an earlier version kept this same song
            except OSError:
                pass
        return song

    def songs(self):
        found = []
        for path in sorted((self.folder / "songs").glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and record.get("name"):
                found.append(record)
        return found


RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}
SLUG = 80       # the longest slug used as a file name


def slug(name):
    """A song's name as a file name: its letters and digits in any script, lower case, joined by hyphens.
    Empty when the name has none."""
    return re.sub(r"[\W_]+", "-", str(name).casefold()).strip("-")


def _old_slug(name):
    """The file name earlier versions gave a song: Latin letters and digits only, so every name without any
    was "untitled"."""
    return re.sub(r"[^a-z0-9]+", "-", str(name).casefold()).strip("-") or "untitled"


def _key(name):
    """What makes two names the same song's: their letters and digits, whatever the case, spacing or
    punctuation ("AC/DC - Back in Black" is "ac dc back in black"). A name with none is only itself."""
    return slug(name) or "=" + " ".join(str(name).casefold().split())


# ---- Reading a chain in words ----

def settings(block, *, every=False):
    """A block's stated values by parameter name, in display units. Unnamed or textual values are skipped
    unless `every`."""
    info = block.info
    found = {}
    if not info:
        return found
    names = {param.index: param for param in info.params}
    defaults = catalog.defaults(block.model)
    for index, value in block.params:
        param = names.get(index)
        if param is None or isinstance(value, str):
            continue
        if isinstance(value, tuple):
            # A value that differs by scene: its first scene's, then each scene where it differs.
            if every:
                continue
            first = catalog.to_display(block.model, index, value[0])
            others = [f"{'ABCDEFGH'[scene]} {catalog.to_display(block.model, index, item):.3g}"
                      for scene, item in enumerate(value) if abs(item - value[0]) > TOLERANCE]
            if others:
                found[param.name] = f"{first:.3g} ({', '.join(others)})"
            continue
        if not every and index in defaults and abs(defaults[index] - value) < TOLERANCE:
            continue
        key = param.name if sum(1 for p in info.params if p.name == param.name) == 1 else f"{param.name} {index}"
        found[key] = round(catalog.to_display(block.model, index, value), 2)
    return found


def describe(chain, *, every=False):
    """The chain as lines of text, one per row in use: blocks in order with their stated settings."""
    lines = []
    for r, row in enumerate(chain.rows):
        if not row.blocks:
            continue
        parts = []
        for column, block in row.blocks:
            shown = settings(block, every=every)
            text = block.name + (" (" + ", ".join(f"{key} {value:g}" if isinstance(value, float) else f"{key} {value}"
                                                  for key, value in list(shown.items())[:8]) + ")"
                                 if shown else "")
            if block.bypass is True:
                text += " [off]"
            elif isinstance(block.bypass, tuple):
                text += " [off in scenes " + ",".join("ABCDEFGH"[i] for i, off in enumerate(block.bypass) if off) + "]"
            parts.append(text)
        where = f"Row {r + 1}"
        if row.split:
            where += f", split at {row.split[0] + 1}, joined at {row.split[1] + 1}"
        lines.append(f"{where}: " + " → ".join(parts))
    named = [label for label in chain.labels if label]
    if named:
        lines.append("Scenes: " + ", ".join(named))
    return lines
