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

CPU = (91., 87.96)       # each of the two processors a pair of rows runs on, found by filling them (CorOS 4.1.1)
MEMORY = 85.             # delay memory, one pool for the pair
PER_CORE = ("cpu", "dm", "pm", "dmda", "pmda")


def limits():
    """What a pair of rows can carry: the processing each of its two processors holds, and the delay memory
    they share. The costs table's `core` decides when it says (`processors`, a pair; `memory`); otherwise the
    figures found by filling the unit stand. The unit's own `core.cpu` is not used for this: it reports one
    figure for both processors, and filling them showed the second holds less."""
    core = table("costs").get("core", {})
    found = core.get("processors")
    if not (isinstance(found, (list, tuple)) and len(found) == 2
            and all(isinstance(each, (int, float)) and not isinstance(each, bool) and each > 0 for each in found)):
        found = CPU
    memory = core.get("memory")
    return tuple(float(each) for each in found), float(memory) if isinstance(memory, (int, float)) and memory > 0 \
        else MEMORY


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
    """What `weigh` worked out. `fits` is the app's estimate from the costs table, not the unit's word: the unit
    decides when the chain is built."""
    fits: bool
    refused: int | None = None      # the first model there was no room for
    unknown: tuple = ()             # models with no known cost, which were not counted


def weigh(model_ids):
    """Whether blocks fit one pair of rows: shared out largest first between two processors, as the unit must."""
    core = table("costs").get("core", {})
    processors, pool = limits()
    caps = [{**{key: core.get(key, float("inf")) for key in PER_CORE}, "cpu": cpu} for cpu in processors]
    used = [dict.fromkeys(PER_CORE, 0.), dict.fromkeys(PER_CORE, 0.)]
    costs = [(model_id, cost(model_id)) for model_id in model_ids if model_id]
    unknown = tuple(model_id for model_id, found in costs if found is None)
    costs = [(model_id, found or {}) for model_id, found in costs]
    memory = sum(found.get("memory", 0.) for _, found in costs)
    refused = None
    for model_id, found in sorted(costs, key=lambda item: -item[1].get("cpu", 0.)):
        room = [side for side, limit in sorted(zip(used, caps), key=lambda pair: pair[0]["cpu"])
                if all(side[key] + found.get(key, 0.) <= limit[key] for key in PER_CORE)]
        if not room:
            refused = refused or model_id
            continue
        for key in PER_CORE:
            room[0][key] += found.get(key, 0.)
    if memory > pool and refused is None:
        refused = max(costs, key=lambda item: item[1].get("memory", 0.))[0]
    return Load(refused is None, refused, unknown)


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

# The word of the distortion scale a crest factor (peak over average, dB) suggests. An inference: compression
# lowers the crest factor as clipping does, so the word is a reading of the figure, not a measured distortion.
CREST_WORDS = ((17., "clean"), (14., "edge of breakup"), (12., "crunch"), (10.5, "overdriven"), (0., "saturated"))
GAIN_CONTROLS = ("GAIN", "INPUT GAIN", "GAIN 1", "GAIN NORMAL", "DRIVE", "VOLUME", "VOLUME BRIGHT", "VOLUME NORMAL")


def crest_word(crest):
    """The distortion word a crest factor in dB suggests (see `CREST_WORDS`)."""
    return next((word for floor, word in CREST_WORDS if crest >= floor), CREST_WORDS[-1][1])


def crest_by_gain(model_id):
    """The crest factor an amp's output measured at three settings of its gain, each with the distortion word
    that figure suggests, as "GAIN 2: 18 dB (suggests clean), 5: 13 dB (suggests crunch), 8: 10 dB (suggests
    saturated)". None for an amp that was not measured. What was measured is the crest factor; the word is
    inferred from it."""
    amp = table("controls").get("amps", {}).get(str(model_id))
    info = catalog.model(model_id)
    if not amp or not info:
        return None
    for name in GAIN_CONTROLS:
        knob = amp.get("knobs", {}).get(name)
        if not knob or not info.named(name):
            continue
        param = info.named(name)[0]
        step = ((param.high or 10) - (param.low or 0)) / 10
        start, default = amp["default"]["crest"], param.default or 0
        said = []
        for side in ("low", None, "high"):
            if side is None:
                moved, crest = 0., start
            elif side in knob:
                moved = knob[side]["moved"]
                crest = None if knob[side].get("silent") else start + moved * knob[side]["crest"]
            else:
                continue
            said.append(f"{default + moved * step:g}: " + ("silent" if crest is None else
                                                          f"{crest:.0f} dB (suggests {crest_word(crest)})"))
        return f"{name} " + ", ".join(said)
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
