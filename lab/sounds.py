"""Sounds, songs, instruments and phrases, and where they are kept.

- A **sound** is a chain with a name and a history: where it came from
  (designed from a request, read from a preset on the unit, revised from
  another sound, imported), what was measured of it, what the player said,
  its knob positions, and where it is saved.
- A **song** has an instrument, a phrase, and ordered **parts** (rhythm,
  lead, clean...). A part is a sound, or one scene of a sound.
- An **instrument** is a guitar or a bass the player owns: which input it is
  plugged into, its pickup settings, its notes for the designer.
- A **phrase** is a recorded take of an instrument, with the pickup setting it
  was played on, noted when it was recorded.

Everything is a small JSON file under `knowledge.home()`.
"""

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from . import catalog
from .chain import Chain

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
        from .knobs import normalize      # knobs once named for a mechanism are carried to their quality

        return cls(**{**record, "chain": Chain.from_record(record["chain"]),
                      "knobs": normalize(record.get("knobs") or {})})


class Store:
    """The player's sounds, songs and phrases on disk."""

    def __init__(self, folder):
        self.folder = Path(folder)
        for name in ("sounds", "songs", "phrases"):
            (self.folder / name).mkdir(parents=True, exist_ok=True)

    # ---- sounds ----

    def save(self, sound):
        path = self.folder / "sounds" / f"{sound.id}.json"
        part = path.with_suffix(".part")
        part.write_text(json.dumps(sound.to_record(), indent=1), encoding="utf-8")
        part.replace(path)
        return sound

    def sound(self, sound_id):
        return Sound.from_record(json.loads((self.folder / "sounds" / f"{sound_id}.json").read_text(encoding="utf-8")))

    def sounds(self):
        found = []
        for path in (self.folder / "sounds").glob("*.json"):
            try:
                found.append(Sound.from_record(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(found, key=lambda sound: sound.created)

    # ---- instruments ----

    def instruments(self):
        path = self.folder / "instruments.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            path.write_text(json.dumps(list(INSTRUMENTS), indent=1), encoding="utf-8")
            return list(INSTRUMENTS)

    def instrument(self, name):
        return next((item for item in self.instruments() if item["name"].casefold() == (name or "").casefold()), None)

    # ---- songs ----

    def song(self, name):
        path = self.folder / "songs" / f"{slug(name)}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"name": name, "instrument": None, "phrase": None, "parts": []}

    def save_song(self, song):
        (self.folder / "songs" / f"{slug(song['name'])}.json").write_text(json.dumps(song, indent=1), encoding="utf-8")
        return song

    def songs(self):
        found = []
        for path in sorted((self.folder / "songs").glob("*.json")):
            try:
                found.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return found


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "untitled"


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
                      for scene, item in enumerate(value) if abs(item - value[0]) > .006]
            if others:
                found[param.name] = f"{first:.3g} ({', '.join(others)})"
            continue
        if not every and index in defaults and abs(defaults[index] - value) < .006:
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
