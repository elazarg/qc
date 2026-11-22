"""Asking the assistant for sounds: a round of new designs, or a revision of a sound that exists.

The assistant is given its instructions (`designer.md`), the request, the
player's instrument, the models it may use as cards built from the
knowledge tables, and, for a revision, the current chain in the same form it
must answer in (`as_form`). It answers with rows of blocks. `to_chain` is the
one place an answer becomes a chain: it checks the answer against the
catalog, the ranges, the processing budget and the layout rules, and either
returns a chain or says what is wrong. A wrong answer gets one chance to be
repaired before it is dropped.

A design states only the values it means; everything else is the unit's own.
A revision says the whole chain again, and whatever it says as `as_form`
showed it stays exactly as it was, down to values the assistant never sees:
`as_form` never shows a chain in a form `to_chain` would refuse.
"""

from dataclasses import replace
import json
from pathlib import Path
import re

from lab.qc import assets
from lab.sound import catalog
from lab import knowledge
from lab.sound.assistant import Cancelled
from lab.sound.chain import Block, COLUMNS, Chain, MULTI_OUT, Row, SCENES, TOLERANCE, in_scene
from lab.sound.sounds import Sound

INSTRUCTIONS = Path(__file__).with_name("designer.md")
EVERYDAY = (16003, 5005, 1, 4, 5, 10, 11, 15, 18, 22, 25, 29, 1011, 1018, 1045, 1079, 1081, 1084, 1093, 1095, 1100,
            1120, 1126, 1155, 12001, 12004, 12006, 12009, 12013, 12017, 12018, 12022, 12026, 12032, 12044, 12048,
            4005, 7000, 7002, 7004, 7006, 7008, 6004, 6005, 6007, 6008, 6011, 8000, 8001, 8012, 8013)
CATEGORIES = {
    "electric": ("Guitar Amplifier", "Guitar Overdrive", "Cabsim Guitar (M)"),
    "bass": ("Bass Amplifier", "Bass Overdrive", "Cabsim Bass (M)", "Guitar Overdrive"),
    "acoustic": (),
    "classical": (),
}
# The amps, cabs and drives made for an instrument other than the electric guitar. Every one the unit runs is
# offered: `EVERYDAY` is a guitarist's list, and the research `named_in` searches describes guitar gear only.
OWN = {"bass": ("Bass Amplifier", "Bass Overdrive", "Cabsim Bass (M)")}
ALWAYS = ("Equalizer", "Compressor", "Delay", "Reverb", "Modulation", "Filter", "Wah", "Pitch")
NUMBERS = {"float", "fader"}
SWITCHES = {"switch", "rotarySwitch", "comboBox"}
DRIVES = ("Guitar Overdrive", "Bass Overdrive")
SKIP = {"MUTE TRIG", "OUTPUT"}                 # the amp's OUTPUT is the app's to set
# A stock cab's own switches and pans for each microphone: not a design's to set (its `bypass` silences a
# microphone, and both microphones share each name, so one value would go to both).
CAB_INTERNAL = {"bypass", "phi", "GRID MODE", "PAN"}
LEVEL_REACH = 6.                               # dB a design may move a clean level control from where the unit puts it
# The aim, on the scales listening measures (lab/hearing/measure.py): distortion from test tones, brightness against
# the dry guitar, and the tail that rings on after the playing stops.
AIM = {"distortion": ("clean", "edge of breakup", "crunch", "overdriven", "saturated"),
       "brightness": ("dark", "balanced", "bright"),
       "tail": ("dry", "a touch of room", "ambient", "wet")}
SCENE_NAME = 12
DESIGNS = 3                                    # a round is three designs


def sections(*names):
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    parts = dict(re.findall(r"^## (\w+)\n\n(.*?)(?=^## |\Z)", text, flags=re.S | re.M))
    return "\n\n".join(parts[name].strip() for name in names)


def craft_for(kind):
    """The section on how sounds are made for this kind of instrument: the guitar's craft, or its own."""
    return kind if kind in ("bass", "acoustic", "classical") else "craft"


# ---- What the assistant may use ----

def usable(model_id, kind):
    info = catalog.model(model_id)
    if not info or model_id in knowledge.locked():
        return False
    if info.category.startswith("Cabsim") and not info.is_stock_cab:
        return False
    if model_id == 16003:
        return True
    return info.category in CATEGORIES.get(kind, ()) + ALWAYS


def _words(text):
    return set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))


COMMON = _words("""the and for with from that this tone sound like amp amps guitar pedal style type based clean lead
rhythm crunch gain high low mid mids drive channel classic modern vintage british american rock early late more less
normal bright boost master volume custom series brown blue red green black white power tube tubes watt head combo
cab speaker heavy tight warm big full hard soft into over open edge back front all one two three very much any some
bit little touch way too not enough make something special standard original version model models hot solo song""")


# Names made only of words too common or too short to point at anything one at a time, matched whole. "The Edge"
# is a guitarist; "the edge of breakup" is not.
NAMES = (("the edge", r"\bthe edge\b(?! of\b)"), ("u2", r"\bu2\b"))


def named_in(text, kind):
    """Models a request points at by maker, model, artist, song or genre, the most specific first,
    with the boosts and cabs the research pairs with the amps found."""
    said = _words(text) - COMMON
    names = [name for name, pattern in NAMES if re.search(pattern, (text or "").lower())]
    scored = {}
    for model in catalog.models():
        entry = knowledge.gear(model.id)
        if not entry or entry["kind"] not in ("amp", "drive") or not usable(model.id, kind):
            continue
        about = " ".join([model.name, str(entry.get("real", "")), str(entry.get("plugin", "")),
                          " ".join(map(str, entry.get("known_for", []))), " ".join(map(str, entry.get("tags", [])))])
        hits = said & (_words(about) - COMMON)
        whole = [name for name in names if name in about.lower()]
        if hits or whole:
            scored[model.id] = len(hits) + 2 * len(whole)
    ranked = sorted(scored, key=lambda model_id: (-scored[model_id], model_id))[:10]
    extra = []
    for model_id in ranked[:3]:
        entry = knowledge.gear(model_id)
        extra += [item["model"] for item in (entry.get("boosts") or [])[:2] if isinstance(item, dict)]
        extra += [item["model"] for item in (entry.get("cabs") or [])[:2] if isinstance(item, dict)]
    return [model_id for model_id in dict.fromkeys(ranked + extra) if usable(model_id, kind)]


def offered(text, kind, chain=None):
    """The models a request may use: the everyday ones, the instrument's own amps, cabs and drives when it is
    not an electric guitar, those the request points at, and whatever the sound being revised holds."""
    held = [block.model for _, _, block in chain.blocks] if chain else []
    own = [model.id for model in catalog.models() if model.category in OWN.get(kind, ())]
    ids = [*EVERYDAY, *own, *named_in(text, kind), *held]
    return [model_id for model_id in dict.fromkeys(ids) if model_id in held or usable(model_id, kind)]


# ---- The player's own impulse responses ----
#
# An IR on the unit is not a model: every one loads into the same IR loader. A design names one by an id of its
# own, from ASSET_IDS up, which stands for the IR loader holding that IR; the ids are numbered afresh for each
# request, and `to_chain` is given the same numbering (`irs`, id -> assets.Asset).

ASSET_IDS = 900001
IR_CONTROLS = ("LEVEL", "HI PASS", "LOW PASS")      # of the loader's first IR; the second is left empty


def _tokens(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").casefold()))


def player_irs(text, chain=None, known=None):
    """The player's IRs a request may use, numbered from ASSET_IDS: each one the sound being revised holds,
    then each the request names (its whole name, or most of its name's words along with "IR" or "impulse").
    `known` are the IRs read from the unit; None reads them from what was last read (`assets.cached`)."""
    if known is None:
        try:
            known = assets.cached() or []
        except OSError:
            known = []
    known = [asset for asset in known if asset.kind == "ir" and asset.source == "user" and asset.name]
    chosen = [found for _, _, block in (chain.blocks if chain else ()) for found in assets.references(block, known)
              if found.kind == "ir" and found.name]
    said, lowered = _tokens(text), (text or "").casefold()
    asks = bool(said & {"ir", "irs", "impulse"})
    scored = []
    for asset in known:
        words = _tokens(asset.name)
        hits = len(words & said)
        if asset.name.casefold() in lowered or (asks and words and hits >= max(2, (len(words) + 1) // 2)):
            scored.append((asset.name.casefold() not in lowered, -hits, asset.name.casefold(), asset))
    chosen += [row[-1] for row in sorted(scored, key=lambda row: row[:3])]
    seen, unique = set(), []
    for asset in chosen:
        if asset.key.casefold() not in seen:
            seen.add(asset.key.casefold())
            unique.append(asset)
    return {ASSET_IDS + number: asset for number, asset in enumerate(unique[:8])}


def _ir_controls():
    names = controls(catalog.IR_LOADER)
    return {name: ((names[name][0][0],), *names[name][1:]) for name in IR_CONTROLS if name in names}


def ir_card(asset_id, asset):
    names = _ir_controls()
    found = {"id": asset_id, "name": f"{asset.name} (the player's IR)", "category": "Cab: the player's own IR",
             "controls": {name: [low, high] for name, (_, low, high, _w) in names.items()}, "switches": {},
             "about": "An impulse response the player loaded on the unit, in an IR loader. It takes the place of "
                      "a cab, after the amp; do not add a stock cab next to it."}
    cost = knowledge.cost(catalog.IR_LOADER)
    if cost:
        found["cpu"] = round(cost.get("cpu", 0))
    return found


def _ir_ids(chain, irs):
    """Where the sound being revised holds one of the player's IRs: (row, column) -> its id in `irs`."""
    by_key = {asset.key.casefold(): asset_id for asset_id, asset in (irs or {}).items()}
    found = {}
    for r, column, block in chain.blocks:
        held = assets.bound(block)
        if held is not None and held.kind == "ir" and held.key.casefold() in by_key:
            found[(r, column)] = by_key[held.key.casefold()]
    return found


def _level_control(info):
    """The control of a model that is its clean output level and a design's to move, by name, or None: a stock
    cab's OUTPUT VOLUME, an equalizer's OUTPUT, an IR loader's LEVEL. (An amp's OUTPUT is the app's.)"""
    if info.is_stock_cab:
        return "OUTPUT VOLUME"
    if info.category == "Equalizer":
        return "OUTPUT"
    return "LEVEL" if info.id in assets.IR_MODELS else None


def controls(model_id):
    """The controls a design may set, by name: (indices, low, high, whole numbers only).

    A name several parameters share (a cab's two microphones) is one control that sets them all. A clean level
    control is held to `LEVEL_REACH` dB either side of where the unit puts it."""
    info = catalog.model(model_id)
    found = {}
    for param in info.params if info else ():
        if param.name in SKIP and info.category not in (*DRIVES, "Equalizer"):
            continue
        if info.is_stock_cab and param.name in CAB_INTERNAL:
            continue
        if param.kind in NUMBERS or param.kind in SWITCHES:
            if param.low is None or param.high is None:
                continue
            indices, low, high, whole = found.get(param.name, ((), param.low, param.high, param.kind in SWITCHES))
            found[param.name] = (indices + (param.index,), low, high, whole)
    level = _level_control(info) if info else None
    if level in found:
        indices, low, high, whole = found[level]
        middle = next((param.default for param in info.params if param.index == indices[0]), None) or 0.
        found[level] = (indices, max(low, middle - LEVEL_REACH), min(high, middle + LEVEL_REACH), whole)
    return found


def card(model_id):
    info = catalog.model(model_id)
    names = controls(model_id)
    found = {"id": model_id, "name": info.name, "category": info.category,
             "controls": {name: [low, high] for name, (_, low, high, whole) in names.items() if not whole},
             "switches": {name: [int(low), int(high)] for name, (_, low, high, whole) in names.items() if whole}}
    entry = knowledge.gear(model_id)
    if entry:
        found["about"] = " ".join(filter(None, (entry.get("real"), entry.get("voice") or entry.get("signature")
                                                or entry.get("character"))))[:300]
        if entry.get("watch_out"):
            found["watch_out"] = entry["watch_out"]
        notes = {name: item.get("does") for name, item in (entry.get("controls") or {}).items() if item.get("does")}
        if notes:
            found["control_notes"] = notes
    cost = knowledge.cost(model_id)
    if cost:
        found["cpu"] = round(cost.get("cpu", 0))
        if cost.get("memory"):
            found["memory"] = round(cost["memory"])
    named = {name: {key: value for key, value in values.items() if key in names}
             for name, values in list(knowledge.named_settings(model_id).items())[:7]}
    named = {name: values for name, values in named.items() if values}
    if named:
        found["named_settings"] = named
    crest = knowledge.crest_by_gain(model_id) if info.category.endswith("Amplifier") else None
    if crest:
        found["crest_by_gain"] = crest      # the measured crest factor, with the distortion word it suggests
    return found


# ---- The form of an answer ----

def _fields(**properties):
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def design_schema(model_ids):
    text, number, whole, flag = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}
    block = _fields(column=whole, model_id={"type": "integer", "enum": sorted(set(model_ids))},
                    settings={"type": "array", "items": _fields(name=text, value=number)},
                    scene_settings={"type": "array", "items": _fields(scene=whole, name=text, value=number)},
                    off_in_scenes={"type": "array", "items": whole}, off=flag)
    row = _fields(row=whole, split_at=whole, join_at=whole, blocks={"type": "array", "items": block})
    return _fields(name=text, target=text, explanation=text, differs=text,
                   scenes={"type": "array", "items": text},
                   rows={"type": "array", "items": row},
                   blend=_fields(level_db=number, spread=number),
                   sources={"type": "array", "items": _fields(title=text, url=text)})


def round_schema(model_ids):
    word = lambda kind: {"type": "string", "enum": list(AIM[kind])}
    text = {"type": "string"}
    return _fields(aim=_fields(distortion=word("distortion"), brightness=word("brightness"), tail=word("tail"),
                               note=text),
                   song=_fields(artist=text, title=text, part=text),
                   designs={"type": "array", "items": design_schema(model_ids)})


# ---- From a chain to the form, and back ----

def _display(model_id, index, stored):
    return round(catalog.to_display(model_id, index, stored), 2)


def _scene_names(chain):
    """A chain's scene names in their places, up to the last one named, as the form gives them."""
    return [label.strip()[:SCENE_NAME] for label in chain.labels[:chain.scenes]]


def _blend(row):
    """The blend a split row's mixer holds, in the form's terms (`level_db`, `spread`), or None when the row has
    no split or its mixer cannot be said that way."""
    mixer = dict(row.mixer)
    if not row.split or not all(isinstance(mixer.get(index), float) for index in (1, 2, 3)):
        return None
    left, level, right = (catalog.to_display(catalog.MIXER, index, mixer[index]) for index in (1, 2, 3))
    return {"level_db": round(level, 1), "spread": round((right - left) * 10)}


def _block_form(column, block, names, shown_id, shown_name):
    """One block in the form: every control it states, at its first scene's value, and each scene where a
    control differs from that."""
    settings, per_scene = [], []
    for name, (indices, *_rest) in names.items():
        stored = block.values.get(indices[0])
        if stored is None or not isinstance(in_scene(stored, 0), float):
            continue
        settings.append({"name": name, "value": _display(block.model, indices[0], in_scene(stored, 0))})
        if isinstance(stored, tuple):
            per_scene += [{"scene": scene, "name": name, "value": _display(block.model, indices[0], stored[scene])}
                          for scene in range(1, SCENES)
                          if isinstance(stored[scene], float) and abs(stored[scene] - stored[0]) > TOLERANCE]
    off = [scene for scene, flag in enumerate(block.bypass) if flag] if isinstance(block.bypass, tuple) else []
    return {"column": column, "model_id": shown_id, "name": shown_name, "settings": settings,
            "scene_settings": per_scene, "off_in_scenes": off, "off": block.bypass is True}


def as_form(chain, irs=None):
    """A chain in the form the assistant answers in, with every control it may set. A block holding one of the
    player's IRs in `irs` is shown by that IR's id and name. Given back unchanged, `to_chain` returns the chain
    as it is."""
    rows = []
    held = _ir_ids(chain, irs)
    for r, row in enumerate(chain.rows[:2]):
        blocks = []
        for column, block in row.blocks:
            if (r, column) in held:
                asset_id = held[(r, column)]
                blocks.append(_block_form(column, block, _ir_controls(), asset_id, irs[asset_id].name))
            else:
                blocks.append(_block_form(column, block, controls(block.model), block.model, block.name))
        if blocks or row.split:
            rows.append({"row": r, "split_at": row.split[0] if row.split else -1,
                         "join_at": row.split[1] if row.split else -1, "blocks": blocks})
    blend = _blend(chain.rows[0])
    return {"rows": rows, "scenes": _scene_names(chain), **({"blend": blend} if blend else {})}


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _listed(found, what, problems):
    """A part of an answer that must be a list, or an empty one with the problem said."""
    if found is None:
        return []
    if not isinstance(found, list):
        problems.append(f"{what} must be a list.")
        return []
    return found


def _identity(block):
    """What makes a block of the sound being revised the same block in an answer: the IR it holds, or its model."""
    held = assets.bound(block)
    return ("ir", held.key.casefold()) if held is not None and held.kind == "ir" else ("model", block.model)


def _continued(base, given, irs):
    """For each block of an answer, the block of the sound being revised it continues, as {(row, column): Block}.

    A block in the same place comes first. A block the answer moved (to make room for another, or onto the other
    path) is matched to the next block of the same model, or holding the same IR, that nothing else continues:
    in its own row in order, then in the other row. Whatever the answer does not say about a block then stays
    as it was, wherever the block went."""
    wanted = []
    for r, item in given.items():
        for spec in item.get("blocks") if isinstance(item.get("blocks"), list) else []:
            if isinstance(spec, dict) and _whole(spec.get("column")) and _whole(spec.get("model_id")):
                model_id = spec["model_id"]
                identity = ("ir", irs[model_id].key.casefold()) if model_id in irs else ("model", model_id)
                wanted.append(((r, spec["column"]), identity))
    left = {(r, column): block for r in (0, 1) for column, block in base.rows[r].blocks}
    found = {}
    for place, identity in wanted:                              # in the same place
        if place in left and place not in found and _identity(left[place]) == identity:
            found[place] = left.pop(place)
    for same_row in (True, False):                              # moved along its row, then to the other row
        for place, identity in sorted(wanted):
            if place in found:
                continue
            for other in sorted(left):
                if (other[0] == place[0]) == same_row and _identity(left[other]) == identity:
                    found[place] = left.pop(other)
                    break
    return found


def _scenes(design, base, problems):
    """The chain's scene names and how many scenes an answer may speak of."""
    given = _listed(design.get("scenes"), "scenes", problems)
    if not all(isinstance(name, str) for name in given):
        problems.append("scenes is a list of names.")
        given = []
    names = [name.strip()[:SCENE_NAME] for name in given]
    if base is not None and names == _scene_names(base):
        return base.labels, SCENES                              # as they were, in their places
    named = [name for name in names if name]
    if len(named) == 1 or len(named) > SCENES:
        problems.append("Name no scenes, or two to eight.")
    labels = tuple(named[:SCENES]) + ("",) * (SCENES - len(named)) if named else None
    if base is not None:
        return labels or base.labels, SCENES                    # the unit holds eight scenes, named or not
    return labels or ("",) * SCENES, len(named)


def _paths(rows):
    """The amps and cabs on each of a split's two paths, as two tuples of model ids, or None without a split."""
    if not rows[0].split:
        return None
    low, high = rows[0].split
    part = lambda blocks: tuple(block.model for block in blocks if assets.is_amp(block) or assets.is_cab(block))
    return (part(block for column, block in rows[0].blocks if low <= column < high),
            part(block for _, block in rows[1].blocks))


def to_chain(design, *, offered_ids, base=None, physical_input=1, irs=None):
    """The chain an answer describes, and the problems that stop it being used (empty when it can be).

    `base` is the sound a revision revises: its routing, levels and rows 2 and 3 are kept, and so is everything
    about a block the answer gives back as `as_form(base)` showed it. `irs` numbers the player's IRs the answer
    may name (see `player_irs`); each becomes an IR loader holding it.
    """
    problems = []
    irs = irs or {}
    if not isinstance(design, dict):
        return base or Chain(), ["The answer is not in the form asked for."]
    labels, count = _scenes(design, base, problems)
    # A new design starts from an empty grid with the instrument on row 0; a revision from the sound it revises.
    rows = list(base.rows) if base else [Row(inlet=physical_input, outlet=MULTI_OUT), Row(), Row(), Row()]
    given = {}
    for item in _listed(design.get("rows"), "rows", problems):
        r = item.get("row") if isinstance(item, dict) else None
        if not _whole(r) or r not in (0, 1):
            problems.append("Use rows 0 and 1 only, each given with its number, split and blocks.")
        elif r in given:
            problems.append(f"Row {r} is given twice.")
        else:
            given[r] = item
    kept = _continued(base, given, irs) if base else {}
    for r in (0, 1):
        item = given.get(r)
        if item is None:
            continue
        cells = [None] * COLUMNS
        for spec in _listed(item.get("blocks"), f"Row {r}'s blocks", problems):
            if not isinstance(spec, dict):
                problems.append(f"Row {r}: a block is given with its column, model and settings.")
                continue
            column, model_id = spec.get("column"), spec.get("model_id")
            if not _whole(column) or not 0 <= column < COLUMNS:
                problems.append(f"Row {r}: column {column} is not on the grid (0 to {COLUMNS - 1}).")
                continue
            if cells[column] is not None:
                problems.append(f"Row {r}: two blocks in column {column}.")
                continue
            was = kept.get((r, column))
            if _whole(model_id) and model_id in irs and model_id in offered_ids:
                block, wrong = _ir_block(spec, irs[model_id], count, was)
            elif _whole(model_id) and was is not None and was.model == model_id and catalog.model(model_id) is None:
                block, wrong = replace(was, bypass=_switching(spec, count, was, was.name, problems)), []
            elif not _whole(model_id) or model_id not in offered_ids or catalog.model(model_id) is None:
                problems.append(f"Model {model_id} is not one of the available models.")
                continue
            elif model_id in assets.IR_MODELS and was is None:
                problems.append("An IR loader is used only to hold one of the player's IRs: name the IR by its id.")
                continue
            else:
                block, wrong = _block(spec, count, was)
                if model_id in catalog.CAPTURE_MODELS and assets.bound(block) is None \
                        and (was is None or assets.bound(was) is not None):
                    wrong.append(f"The {block.name} block in row {r} column {column} holds no capture. A capture "
                                 "block can only be one the current chain already has: keep it, or leave it out.")
            problems += wrong
            cells[column] = block
        split_at, join_at = item.get("split_at", -1), item.get("join_at", -1)
        if not _whole(split_at) or not _whole(join_at):
            problems.append(f"Row {r}: split_at and join_at are column numbers, or -1 for no split.")
            split = rows[r].split
        else:
            split = None if (split_at, join_at) == (-1, -1) else (split_at, join_at)
        if base is not None and split == base.rows[r].split:
            pass                                    # A revision may keep a layout the rules would not allow anew.
        elif split and (r != 0 or not 0 <= split[0] < split[1] <= COLUMNS - 1):
            problems.append("Only row 0 can split, at a column before the one where it joins.")
            split = None
        rows[r] = replace(rows[r], cells=tuple(cells), split=split)
    rows[0] = _joined(rows, design, base, problems)
    chain = Chain(tuple(rows), labels)
    for pair in ((0, 1), (2, 3)):
        models = [block.model for r in pair for _, block in chain.rows[r].blocks]
        if base is not None and sorted(models) == sorted(block.model for r in pair for _, block in base.rows[r].blocks):
            continue                                # the unit already runs these: the estimate is not asked
        load = knowledge.weigh(models)
        if not load.fits:
            problems.append(f"The chain needs more processing than the unit has: "
                            f"{Block(load.refused).name} does not fit.")
    if not chain.blocks:
        problems.append("The chain has no blocks.")
    return chain, problems


def _joined(rows, design, base, problems):
    """Row 0 with the mixer that joins its two paths, and the layout rules of a split said as problems.

    The rules and the polarity of the join are worked out for a new design, and for a revision whenever its
    split or the amps and cabs on either path differ from the sound it revises; a layout a revision leaves as
    it was is kept, whatever the rules. The blend is written when the layout is new or the answer changes it."""
    first = rows[0]
    if not first.split:
        if rows[1].blocks and (base is None or base.rows[0].split):
            problems.append("Row 1 is used only as the second path of a split on row 0." if base is None else
                            "Row 1 still holds blocks, and without a split on row 0 nothing reaches them.")
        return first
    low, high = first.split
    new = base is None or first.split != base.rows[0].split or _paths(rows) != _paths(base.rows)
    if new:
        stray = [column for column, _ in rows[1].blocks if not low <= column < high]
        if stray:
            problems.append(f"Row 1 blocks must sit between the split and the join (columns {low} to {high - 1}).")
        if not rows[1].blocks:
            problems.append("A split needs at least one block on the second path (row 1): an empty path is the "
                            "bare guitar mixed in. For one path, leave split_at and join_at at -1.")
        second = [block for _, block in rows[1].blocks]
        shared_cab = any(assets.is_cab(block) for column, block in first.blocks if column >= high)
        if any(assets.is_amp(block) for block in second) and not any(assets.is_cab(block) for block in second) \
                and not shared_cab:
            problems.append("The amp on the second path needs its own cab, or a cab on row 0 after the join.")
    mixer = dict(first.mixer) if base is not None else {}
    blend = design.get("blend")
    asked = None
    if isinstance(blend, dict) and all(_number(blend.get(key)) for key in ("level_db", "spread")):
        asked = {"level_db": round(min(max(float(blend["level_db"]), -20.), 6.), 1),
                 "spread": round(min(max(float(blend["spread"]), 0.), 100.))}
    elif blend is not None:
        problems.append("blend is given as level_db and spread, both numbers.")
    shown = _blend(base.rows[0]) if base is not None else None
    if new or (asked is not None and shown is not None and asked != shown):
        level, spread = (asked or shown or {"level_db": 0., "spread": 0}).values()
        mixer.update({1: catalog.to_norm(catalog.MIXER, 1, 5 - spread / 20),
                      2: catalog.to_norm(catalog.MIXER, 2, level),
                      3: catalog.to_norm(catalog.MIXER, 3, 5 + spread / 20)})
    if new:
        mixer[4] = float(knowledge.opposed([block.model for column, block in first.blocks if low <= column < high],
                                           [block.model for _, block in rows[1].blocks]))
    return replace(first, mixer=tuple(mixer.items()))


def _ir_block(spec, asset, count, kept=None):
    """An IR loader holding one of the player's IRs, from an entry of an answer that names the IR by its id.
    `kept` is the loader of the sound being revised that holds the same IR, which it continues."""
    loader = kept.model if kept is not None else catalog.IR_LOADER
    block, problems = _block({**spec, "model_id": loader}, count, kept, names=_ir_controls(), label=asset.name)
    binding = assets.block_for(asset, stereo=loader == assets.IR_STEREO)
    for index, value in binding.params:
        block = block.set(index, value)
    return block, problems


def _switching(spec, count, kept, label, problems):
    """Whether a block is on, from an answer's `off` and `off_in_scenes`. Said, they decide: `off` true is off
    in every scene, a list of scenes is off in those, and an empty list with `off` false is on in every scene.
    An answer that says neither leaves the block as the sound being revised has it."""
    off, scenes = spec.get("off"), spec.get("off_in_scenes")
    held = kept.bypass if kept is not None else False
    if off is None and scenes is None:
        return held
    if off is True:
        return True
    if off not in (None, False) or not isinstance(scenes or [], list):
        problems.append(f"{label}: off is true or false, and off_in_scenes a list of scene numbers.")
        return held
    if any(not _whole(scene) or not 0 <= scene < count for scene in scenes or []):
        problems.append("A block is switched off in a scene the design does not have.")
        return held
    return tuple(scene in scenes for scene in range(SCENES)) if scenes else False


def _block(spec, count, kept=None, *, names=None, label=None):
    """A block from one entry of an answer. `kept` is the block of the sound being revised it continues:
    everything the answer does not say stays as it was there, down to values the assistant never sees, and so
    does every value the answer gives back as the form showed it (so a value the unit holds outside what a new
    design may ask for is not a problem until the answer changes it).

    A control's `settings` value holds in every scene; `scene_settings` give it another value in one scene.
    `names` limits the controls it may set, and `label` is what the block is called in problems."""
    model_id = spec["model_id"]
    names = controls(model_id) if names is None else names
    label = label or catalog.model(model_id).name
    values, problems = (dict(kept.values) if kept is not None else {}), []
    everywhere, by_scene = {}, {}
    for setting in _listed(spec.get("settings"), f"{label}'s settings", problems):
        name = setting.get("name") if isinstance(setting, dict) else None
        if not isinstance(name, str) or name not in names:
            problems.append(f"{label} has no control {name!r}.")
            continue
        everywhere[name] = setting.get("value")
    for setting in _listed(spec.get("scene_settings"), f"{label}'s scene_settings", problems):
        scene, name = (setting.get("scene"), setting.get("name")) if isinstance(setting, dict) else (None, None)
        if not _whole(scene) or not 0 <= scene < count:
            problems.append(f"A scene setting names scene {scene}, which the design does not have.")
            continue
        if not isinstance(name, str) or name not in names:
            problems.append(f"{label} has no control {name!r}.")
            continue
        by_scene.setdefault(name, {})[scene] = setting.get("value")
    for name in dict.fromkeys([*everywhere, *by_scene]):
        indices, low, high, whole = names[name]
        lead = values.get(indices[0])
        shown = [_display(model_id, indices[0], in_scene(lead, scene)) if isinstance(in_scene(lead, scene), float)
                 else None for scene in range(SCENES)] if lead is not None else [None] * SCENES
        # What is asked in each scene, in display units; None where the answer says nothing new for that scene.
        asked = [None] * SCENES
        for scene in range(SCENES):
            if scene in by_scene.get(name, {}):
                value = by_scene[name][scene]
            elif name in everywhere:
                value = everywhere[name]
            else:
                continue                            # nothing said for this scene
            if shown[scene] is not None and _number(value) and abs(value - shown[scene]) < .005:
                continue                            # said as it was: every parameter keeps its own
            if not _number(value) or not low <= value <= high or (whole and float(value) != int(value)):
                problems.append(f"{label} {name} {value} is outside {low:g} to {high:g}"
                                + (" in whole steps." if whole else "."))
                asked = None
                break
            asked[scene] = value
        if asked is None or all(value is None for value in asked):
            continue
        for index in indices:
            held = values.get(index)
            if held is None:
                # A control the block leaves to the unit, given a value in some scenes only: the others take
                # the catalog's default, the nearest thing known to what the unit will hold.
                default = catalog.defaults(model_id).get(index)
                held = default if default is not None else catalog.to_norm(model_id, index, low)
            each = [catalog.to_norm(model_id, index, asked[scene]) if asked[scene] is not None
                    else in_scene(held, scene) for scene in range(SCENES)]
            values[index] = each[0] if all(value == each[0] for value in each) else tuple(each)
    bypass = _switching(spec, count, kept, label, problems)
    return replace(kept or Block(model_id), params=values, bypass=bypass), problems


# ---- Asking ----

def facts(text, instrument, *, chain=None, earlier=None, models=(), irs=None, guitar=None, listening=None):
    notes = knowledge.guitar_notes(instrument.get("name")) if instrument else None
    recipe = _recipe(text) if chain is None else None
    irs = irs or {}
    return {"request": text,
            "instrument": {key: instrument.get(key) for key in ("name", "kind", "about")} if instrument else None,
            **({"instrument_notes": notes} if notes else {}),
            **({"guitar_profile": guitar} if guitar else {}),
            **({"player_listening": listening} if listening else {}),
            **({"community_recipe": recipe} if recipe else {}),
            **({"current_chain": as_form(chain, irs)} if chain is not None else {}),
            **({"earlier": earlier} if earlier else {}),
            "available_models": [ir_card(model_id, irs[model_id]) if model_id in irs else card(model_id)
                                 for model_id in models if model_id in irs or catalog.model(model_id)]}


def _with_irs(models, irs, chain=None):
    """The models offered with the player's IRs added. A bare IR loader the sound holds is left out when each
    one holds an IR shown by its id, so that an empty loader is never offered."""
    loaders = [(r, column) for r, column, block in (chain.blocks if chain else ()) if block.model in assets.IR_MODELS]
    covered = set(_ir_ids(chain, irs)) if chain else set()
    if loaders and all(place in covered for place in loaders):
        models = [model_id for model_id in models if model_id not in assets.IR_MODELS]
    return [*models, *irs]


def _recipe(text):
    said = _words(text) - COMMON
    best = None
    for recipe in knowledge.table("gear").get("recipes", []):
        words = (_words(recipe.get("target", "")) | _words(" ".join(recipe.get("aliases", [])))) - COMMON
        score = len(said & words)
        if score and (best is None or score > best[0]):
            best = (score, recipe)
    return best[1] if best else None


def _checked(check, reply, **options):
    """An answer checked. One that is not in the form asked for at all is a problem like any other."""
    try:
        return check(reply, **options)
    except (TypeError, ValueError, KeyError, AttributeError, IndexError) as error:
        return None, [f"The answer is not in the form asked for ({type(error).__name__}: {error})."]


def _ask_checked(assistant, kind, prompt, schema, check, *, by, web, seconds, cancel):
    """Ask, check, and if the answer cannot be used, ask once more with the problems listed.

    `check(reply, final=False)` returns (result, problems). On the last word (`final`) it may settle for less:
    the repaired answer, or the first one again when no repair could be had."""
    reply = assistant.ask(kind, prompt, schema, by=by, web=web, seconds=seconds, cancel=cancel)
    result, problems = _checked(check, reply)
    if not problems:
        return result
    again = (prompt + "\n\n" + sections("repair") + "\n"
             + json.dumps({"problems": problems[:12], "previous_reply": reply}, ensure_ascii=False, default=str))
    try:
        repaired = assistant.ask(kind + " repair", again, schema, by=by, web=False, seconds=seconds, cancel=cancel)
    except Cancelled:
        raise
    except Exception:
        result, still = _checked(check, reply, final=True)       # no repair to be had: what the first held
        if still:
            raise
        return result
    result, problems = _checked(check, repaired, final=True)
    if problems:
        raise ValueError("The assistant's answer could not be used: " + "; ".join(problems[:3]))
    return result


def _sound(item, chain, origin):
    sources = item.get("sources") if isinstance(item.get("sources"), list) else []
    return Sound(name=str(item.get("name") or "Untitled")[:24], chain=chain, origin=origin,
                 explanation=str(item.get("explanation") or "")[:600],
                 sources=[{"title": str(s.get("title", ""))[:120], "url": s.get("url")}
                          for s in sources if isinstance(s, dict) and str(s.get("url", "")).startswith("https://")][:6])


def played_on(instrument, setting=None, guitar=None):
    """What a sound was made for: the instrument, and the pickup setting of the phrase used when it is known."""
    if not instrument:
        return None
    setting = setting or (guitar or {}).get("recorded_on")
    return {"instrument": instrument.get("name"), **({"setting": setting} if setting else {})}


def round_of_three(assistant, text, instrument, *, earlier=None, by="player", cancel=None, assets_known=None,
                   guitar=None, setting=None, listening=None):
    """An aim and up to three sounds that reach it by different routes.

    `listening` (`lab.player.listening.prior`) says what guitar music the player listens to most; the assistant uses
    it only for requests that name no artist, song or sound (designer.md, "listening").

    `assets_known` are the IRs and captures read from the unit (None: those last read, `assets.cached`); the
    player's IRs the request names are offered as models. `guitar` is what the instrument puts out
    (`profile.facts`), given to the assistant as guitar_profile; `setting` is the pickup setting of the phrase in
    use. Each sound's origin carries the input it was made for under "input" (`played_on`).

    Two usable designs are enough; fewer get one repair. After it, even one design is a round: what could be
    used is never thrown away with what could not."""
    kind = (instrument or {}).get("kind", "electric")
    irs = player_irs(text, known=assets_known)
    models = _with_irs(offered(text, kind), irs)
    names = ("task", "chain", craft_for(kind), *(("input",) if guitar else ()),
             *(("listening",) if listening else ()), "scenes", "paths", "budget", "round", "song")
    prompt = sections(*names) + "\n\n" + json.dumps(
        facts(text, instrument, earlier=earlier, models=models, irs=irs, guitar=guitar, listening=listening),
        ensure_ascii=False)
    source = played_on(instrument, setting, guitar)
    origin = {"kind": "designed", "request": text, **({"input": source} if source else {})}
    physical = (instrument or {}).get("input", 1)
    best = []                                           # the most usable designs any answer held, with their aim

    def check(reply, final=False):
        given = reply.get("aim") if isinstance(reply, dict) else None
        if not isinstance(given, dict) or any(given.get(key) not in AIM[key] for key in AIM):
            return None, ["The aim is missing, or not in the words of its scales."]
        aim = {key: given[key] for key in AIM}
        aim["note"] = str(given.get("note", ""))[:300]
        song = reply.get("song") if isinstance(reply.get("song"), dict) else {}
        if str(song.get("title") or "").strip():
            # The song and part the request is for, as the assistant understood it.
            aim["song"] = {key: str(song.get(key) or "").strip()[:60] for key in ("artist", "title", "part")}
        designs, problems, seen = [], [], set()
        for number, item in enumerate(_listed(reply.get("designs"), "designs", problems), 1):
            if len(designs) == DESIGNS:
                break                                   # a round is three: more than that was not asked for
            chain, wrong = to_chain(item, offered_ids=set(models), physical_input=physical, irs=irs)
            if wrong:
                problems += [f"Design {number}: {problem}" for problem in wrong]
                continue
            if chain.digest() in seen:
                problems.append(f"Design {number} is the same chain as an earlier one.")
                continue
            seen.add(chain.digest())
            designs.append((_sound(item, chain, dict(origin)), str(item.get("differs") or "")[:160]))
        if not best or len(designs) > len(best[0][1]):
            best[:] = [(aim, designs)]
        if len(designs) >= 2 or (designs and not problems):
            return (aim, designs), []
        if final and best[0][1]:
            return best[0], []
        return (aim, designs), problems or ["The answer holds no designs."]

    return _ask_checked(assistant, "round", prompt, round_schema(models), check, by=by, web=False, seconds=180,
                        cancel=cancel)


def revise(assistant, sound, text, instrument, *, by="player", cancel=None, assets_known=None, guitar=None,
           setting=None):
    """A new sound: `sound` changed as the request asks, everything else kept, with the assistant's account of
    what it changed as its explanation. The player's IRs the sound holds or the request names are offered as
    models (`assets_known`, `guitar` and `setting` as for `round_of_three`)."""
    kind = (instrument or {}).get("kind", "electric")
    irs = player_irs(text, sound.chain, known=assets_known)
    models = _with_irs(offered(text, kind, sound.chain), irs, sound.chain)
    names = ("task", "chain", "revise", craft_for(kind), *(("input",) if guitar else ()), "scenes", "paths", "budget")
    prompt = sections(*names) + "\n\n" + json.dumps(
        facts(text, instrument, chain=sound.chain, models=models, irs=irs, guitar=guitar), ensure_ascii=False)
    source = played_on(instrument, setting, guitar)

    def check(reply, final=False):
        chain, problems = to_chain(reply, offered_ids=set(models), base=sound.chain,
                                   physical_input=(instrument or {}).get("input", 1), irs=irs)
        said = reply if isinstance(reply, dict) else {}
        return (chain, said.get("explanation"), said.get("sources")), problems

    chain, explanation, sources = _ask_checked(assistant, "revise", prompt, design_schema(models), check, by=by,
                                               web=False, seconds=120, cancel=cancel)
    revised = _sound({"name": sound.name, "explanation": explanation, "sources": sources}, chain,
                     {"kind": "revised", "request": text, "from": sound.id, **({"input": source} if source else {})})
    revised.parent = sound.id
    return revised
