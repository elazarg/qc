"""What the app knows about gear, from research and from the player's own unit, with where each part came from.

Two kinds of table:

- Shared knowledge, true of any Quad Cortex: what each model is and how
  players use it (`gear.json`, from `research/build_knowledge.py`).
- What was measured or read on this player's unit: the processing each model
  costs (`costs.json`), how each amp's controls act (`controls.json`), which
  models turn the signal upside down (`polarity.json`), and the unit's own
  named settings and which models it will not run (`device-presets.json`).

Every table on the unit's side records the firmware it was made on, and
`stale` lists the ones that do not match the unit, so that a firmware update
is noticed and not silently trusted.
"""

from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path

from lab.sound import catalog

REPO = Path(__file__).resolve().parents[1]
SHARED = REPO / "lab" / "tables"       # the tables that ship with the app
DEVICE = "device-presets.json"         # the unit's named settings, read from the player's unit: in the data folder


def home():
    """Where the app keeps the player's data: `%LOCALAPPDATA%\\Tone Lab`. TONE_LAB_HOME overrides it.

    Local, not roaming: the data is tied to this computer and its unit (the USB delay, which cache slot holds
    what, the note of a borrowed preset to give back after a crash), and recordings are too large to roam.
    The first time, what an earlier version kept in the program folder is copied here (`setup.move_old_home`).
    """
    chosen = os.environ.get("TONE_LAB_HOME")
    if chosen:
        folder = Path(chosen)
    else:
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        folder = Path(base) / "Tone Lab"
        if not folder.exists():
            from lab.setup import move_old_home

            move_old_home(REPO / "tone_sessions" / "lab", folder)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def device_path():
    """Where the unit's named settings are kept: the data folder. What the earlier app read into the program
    folder is copied there once, so the half hour it took is not spent again."""
    path = home() / DEVICE
    old = REPO / "tone_sessions" / DEVICE
    if not path.exists() and old.is_file():
        try:
            spare = path.with_suffix(".copying")
            spare.write_bytes(old.read_bytes())
            os.replace(spare, path)
        except OSError:
            return old
    return path


class MissingTable(RuntimeError):
    """A table that ships with the app is not there or cannot be read. Nothing is designed on a guess."""


def _load(path, shipped=False):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        if shipped:
            raise MissingTable(f"{Path(path).name} is missing from {Path(path).parent} or cannot be read. "
                               "Restore it from the repository (git checkout -- lab/tables).") from error
        return {}


@dataclass(frozen=True)
class Source:
    name: str
    file: str
    made_by: str
    firmware: str | None


@lru_cache(maxsize=None)
def table(name):
    if name == "device":
        return _load(device_path())      # not there until the unit's named settings have been read
    return _load(SHARED / f"{name}.json", shipped=True)


SHIPPED = ("gear", "costs", "controls", "polarity")
SOURCES = (
    Source("gear", "gear.json", "research/build_knowledge.py", None),
    Source("costs", "costs.json", "python -m lab budget", "firmware"),
    Source("controls", "controls.json", "research/measure_amps.py, research/analyse_amps.py", "firmware"),
    Source("polarity", "polarity.json", "research/measure_polarity.py", "firmware"),
    Source("device", DEVICE, "python -m lab named-settings", "firmware"),
)


def missing():
    """The shipped tables that are not there or cannot be read, by file name. `doctor` reports them."""
    found = []
    for name in SHIPPED:
        try:
            _load(SHARED / f"{name}.json", shipped=True)
        except MissingTable:
            found.append(f"{name}.json")
    return found


def stale(firmware):
    """The unit's tables made on another firmware than the unit's, as (name, their firmware, how to remake)."""
    found = []
    for source in SOURCES:
        if source.firmware is None:
            continue
        theirs = table(source.name).get(source.firmware)
        if theirs and theirs != firmware:
            found.append((source.name, theirs, source.made_by))
    return found


# ---- What each model is ----

@lru_cache(maxsize=1)
def _gear_index():
    data, index = table("gear"), {}
    for key, family in data.get("amps", {}).items():
        for model_id in family.get("models", ()):
            index[int(model_id)] = ("amp", {**family, "family": key,
                                            "note": family.get("model_notes", {}).get(str(model_id), "")})
    for section, kind in (("drives", "drive"), ("cabs", "cab"), ("effects", "effect")):
        for model_id, entry in data.get(section, {}).items():
            index[int(model_id)] = (kind, entry)
    return index


def gear(model_id):
    """What players and research say about one model, or None."""
    found = _gear_index().get(int(model_id))
    return {"kind": found[0], **found[1]} if found else None


def guitar_notes(name):
    lowered = (name or "").lower()
    for key, notes in table("gear").get("guitars", {}).items():
        if key in lowered:
            return notes
    return None


# ---- What the unit can run ----

CPU = (91., 87.96)       # each of the two processors a pair of rows runs on, found by filling them
MEMORY = 85.             # delay memory, one pool for the pair
PER_CORE = ("cpu", "dm", "pm", "dmda", "pmda")


def cost(model_id):
    known = table("costs").get("models", {})
    found = known.get(str(model_id))
    if found is None:
        info = catalog.model(model_id)
        layout = catalog.CAB_LAYOUTS.get(info.category) if info and info.is_stock_cab else None
        found = known.get(str(layout)) if layout else None
    return found


@dataclass(frozen=True)
class Load:
    fits: bool
    percent: int
    memory: int
    refused: int | None = None
    unknown: tuple = ()      # models with no known cost, which were not counted


def weigh(model_ids):
    """Whether blocks fit one pair of rows: shared out largest first between two processors, as the unit must."""
    core = table("costs").get("core", {})
    limits = [{**{key: core.get(key, float("inf")) for key in PER_CORE}, "cpu": cpu} for cpu in CPU]
    used = [dict.fromkeys(PER_CORE, 0.), dict.fromkeys(PER_CORE, 0.)]
    costs = [(model_id, cost(model_id)) for model_id in model_ids if model_id]
    unknown = tuple(model_id for model_id, found in costs if found is None)
    costs = [(model_id, found or {}) for model_id, found in costs]
    memory = sum(found.get("memory", 0.) for _, found in costs)
    refused = None
    for model_id, found in sorted(costs, key=lambda item: -item[1].get("cpu", 0.)):
        room = [side for side, limit in sorted(zip(used, limits), key=lambda pair: pair[0]["cpu"])
                if all(side[key] + found.get(key, 0.) <= limit[key] for key in PER_CORE)]
        if not room:
            refused = refused or model_id
            continue
        for key in PER_CORE:
            room[0][key] += found.get(key, 0.)
    if memory > MEMORY and refused is None:
        refused = max(costs, key=lambda item: item[1].get("memory", 0.))[0]
    total = sum(found.get("cpu", 0.) for _, found in costs)
    return Load(refused is None, round(100 * total / sum(CPU)), round(100 * memory / MEMORY), refused, unknown)


@lru_cache(maxsize=1)
def locked():
    """Models this unit cannot use: refused by it, belonging to a plugin it refused, or loading and doing nothing.

    The last kind was found by measuring every amp: the amps of six plugins
    load and pass the guitar through untouched, which is what the unit does
    with a plugin it holds no licence for. Every model of such a plugin is
    locked with them.
    """
    record = table("device")
    refused = {int(model_id) for model_id, entry in record.get("models", {}).items() if not entry.get("available")}
    inert = set(table("controls").get("inert", ()))
    plugins = {catalog.model(model_id).plugin for model_id in refused | inert if catalog.model(model_id)} - {0}
    owned = record.get("plugins")
    found = set(refused | inert)
    for model in catalog.models():
        if model.plugin and (model.plugin in plugins or owned is not None and model.plugin not in owned):
            found.add(model.id)
    return frozenset(found)


def named_settings(model_id):
    """The unit's own named settings for a model, as {name: {parameter: display value}}."""
    entry = table("device").get("models", {}).get(str(model_id))
    if not entry or not entry.get("available"):
        return {}
    return {preset["name"]: {name: value for name, value in preset["values"] if isinstance(value, (int, float))}
            for preset in entry.get("presets", []) if preset["name"] != "Default"}


# ---- What was measured ----

BREAKUP = ((17., "clean"), (14., "edge of breakup"), (12., "crunch"), (10.5, "overdriven"), (0., "saturated"))


def drive_curve(model_id):
    """How distorted an amp measured at three settings of its gain, as "GAIN 2 clean, 5 crunch, 8 saturated"."""
    amp = table("controls").get("amps", {}).get(str(model_id))
    info = catalog.model(model_id)
    if not amp or not info:
        return None
    for name in ("GAIN", "INPUT GAIN", "GAIN 1", "GAIN NORMAL", "DRIVE", "VOLUME", "VOLUME BRIGHT", "VOLUME NORMAL"):
        knob = amp.get("knobs", {}).get(name)
        if not knob:
            continue
        param = info.named(name)[0]
        step = ((param.high or 10) - (param.low or 0)) / 10
        start, default = amp["default"]["crest"], param.default or 0
        words = []
        for side in ("low", None, "high"):
            if side is None:
                moved, crest = 0., start
            elif side in knob:
                moved = knob[side]["moved"]
                crest = None if knob[side].get("silent") else start + moved * knob[side]["crest"]
            else:
                continue
            word = "silent" if crest is None else next(w for floor, w in BREAKUP if crest >= floor)
            words.append(f"{default + moved * step:g} {word}")
        return f"{name} " + ", ".join(words)
    return None


def opposed(first, second):
    """Whether two parallel paths, given as model ids, come out upside down against each other."""
    found = table("polarity")
    turned = 0
    for kind in ("amps", "cabs"):
        measured, inverted = set(found.get(kind, {}).get("measured", ())), set(found.get(kind, {}).get("inverted", ()))
        mine, theirs = ([model for model in path if model in measured] for path in (first, second))
        if len(mine) == len(theirs):
            turned += sum(model in inverted for model in mine + theirs)
    return turned % 2 == 1
