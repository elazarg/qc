"""Asking the assistant for sounds: a round of new designs, or a revision of a sound that exists.

The assistant is given its instructions (`designer.md`), the request, the
player's instrument, the models it may use as cards built from the
knowledge tables, and, for a revision, the current chain in the same form it
must answer in. It answers with rows of blocks. `to_chain` checks the answer
against the catalog, the ranges, the processing budget and the layout rules,
and either returns a chain or says what is wrong. A wrong answer gets one
chance to be repaired before it is dropped.

A design states only the values it means; everything else is the unit's own.
"""

from dataclasses import replace
import json
from pathlib import Path
import re

from . import assets, catalog, knowledge
from .chain import Block, Chain, Row, SCENES
from .sounds import Sound, settings as stated

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
ALWAYS = ("Equalizer", "Compressor", "Delay", "Reverb", "Modulation", "Filter", "Wah", "Pitch")
NUMBERS = {"float", "fader"}
SWITCHES = {"switch", "rotarySwitch", "comboBox"}
SKIP = {"MUTE TRIG", "OUTPUT"}                 # the amp's OUTPUT is the app's to set
# The aim, on the scales listening measures (lab/measure.py): distortion from test tones, brightness against
# the dry guitar, and the tail that rings on after the playing stops.
AIM = {"distortion": ("clean", "edge of breakup", "crunch", "overdriven", "saturated"),
       "brightness": ("dark", "balanced", "bright"),
       "tail": ("dry", "a touch of room", "ambient", "wet")}
SCENE_NAME = 12
MULTI_OUT = 19


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


def named_in(text, kind):
    """Models a request points at by maker, model, artist, song or genre, the most specific first,
    with the boosts and cabs the research pairs with the amps found."""
    said = _words(text) - COMMON
    scored = {}
    for model in catalog.models():
        entry = knowledge.gear(model.id)
        if not entry or entry["kind"] not in ("amp", "drive") or not usable(model.id, kind):
            continue
        about = " ".join([model.name, str(entry.get("real", "")), str(entry.get("plugin", "")),
                          " ".join(map(str, entry.get("known_for", []))), " ".join(map(str, entry.get("tags", [])))])
        hits = said & (_words(about) - COMMON)
        if hits:
            scored[model.id] = len(hits)
    ranked = sorted(scored, key=lambda model_id: (-scored[model_id], model_id))[:10]
    extra = []
    for model_id in ranked[:3]:
        entry = knowledge.gear(model_id)
        extra += [item["model"] for item in (entry.get("boosts") or [])[:2] if isinstance(item, dict)]
        extra += [item["model"] for item in (entry.get("cabs") or [])[:2] if isinstance(item, dict)]
    return [model_id for model_id in dict.fromkeys(ranked + extra) if usable(model_id, kind)]


def offered(text, kind, chain=None):
    held = [block.model for _, _, block in chain.blocks] if chain else []
    ids = [*EVERYDAY, *named_in(text, kind), *held]
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
    chosen = [found for found in (assets.bound(block, known) for _, _, block in (chain.blocks if chain else ()))
              if found is not None and found.kind == "ir"]
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


def controls(model_id):
    """The controls a design may set, by name: (indices, low, high, whole numbers only)."""
    info = catalog.model(model_id)
    found = {}
    for param in info.params if info else ():
        if param.name in SKIP and info.category not in ("Guitar Overdrive", "Bass Overdrive", "Equalizer"):
            continue
        if param.kind in NUMBERS or param.kind in SWITCHES:
            if param.low is None or param.high is None:
                continue
            indices, low, high, whole = found.get(param.name, ((), param.low, param.high, param.kind in SWITCHES))
            found[param.name] = (indices + (param.index,), low, high, whole)
    if info and info.category == "Equalizer" and "OUTPUT" in found:
        indices, low, high, whole = found["OUTPUT"]
        found["OUTPUT"] = (indices, max(low, -6.), min(high, 6.), whole)
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
    drive = knowledge.drive_curve(model_id) if info.category.endswith("Amplifier") else None
    if drive:
        found["crest_by_gain"] = drive
    return found


# ---- The form of an answer ----

def _fields(**properties):
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def design_schema(model_ids):
    text, number, whole = {"type": "string"}, {"type": "number"}, {"type": "integer"}
    block = _fields(column=whole, model_id={"type": "integer", "enum": sorted(set(model_ids))},
                    settings={"type": "array", "items": _fields(name=text, value=number)},
                    scene_settings={"type": "array", "items": _fields(scene=whole, name=text, value=number)},
                    off_in_scenes={"type": "array", "items": whole})
    row = _fields(row=whole, split_at=whole, join_at=whole, blocks={"type": "array", "items": block})
    return _fields(name=text, target=text, explanation=text, differs=text,
                   scenes={"type": "array", "items": text},
                   rows={"type": "array", "items": row},
                   blend=_fields(level_db=number, spread=number),
                   sources={"type": "array", "items": _fields(title=text, url=text)})


def round_schema(model_ids):
    word = lambda kind: {"type": "string", "enum": list(AIM[kind])}
    return _fields(aim=_fields(distortion=word("distortion"), brightness=word("brightness"), tail=word("tail"),
                               note={"type": "string"}),
                   designs={"type": "array", "items": design_schema(model_ids)})


# ---- From a chain to the form, and back ----

def as_form(chain, irs=None):
    """A chain in the form the assistant answers in, with every control it may set. A block holding one of the
    player's IRs in `irs` is shown by that IR's id and name."""
    rows = []
    held = _ir_ids(chain, irs)
    for r, row in enumerate(chain.rows[:2]):
        blocks = []
        for column, block in row.blocks:
            if (r, column) in held:
                asset_id = held[(r, column)]
                shown = {name: round(catalog.to_display(block.model, indices[0], block.get(indices[0])), 2)
                         for name, (indices, *_rest) in _ir_controls().items()
                         if isinstance(block.get(indices[0]), float)}
                blocks.append({"column": column, "model_id": asset_id, "name": irs[asset_id].name,
                               "settings": [{"name": name, "value": value} for name, value in shown.items()],
                               "scene_settings": [], "off_in_scenes": [scene for scene, flag in enumerate(block.bypass)
                                                                       if flag] if isinstance(block.bypass, tuple)
                               else [], "off": block.bypass is True})
                continue
            names = controls(block.model)
            shown = stated(block, every=True)
            per_scene = []
            for index, value in block.params:
                param = next((item for item in block.info.params if item.index == index), None) if block.info else None
                if isinstance(value, tuple) and param is not None and param.name in names:
                    per_scene += [{"scene": scene, "name": param.name,
                                   "value": round(catalog.to_display(block.model, index, value[scene]), 2)}
                                  for scene in range(1, SCENES) if abs(value[scene] - value[0]) > .006]
            off = [scene for scene, flag in enumerate(block.bypass) if flag] if isinstance(block.bypass, tuple) else []
            blocks.append({"column": column, "model_id": block.model, "name": block.name,
                           "settings": [{"name": name, "value": value} for name, value in shown.items() if name in names],
                           "scene_settings": per_scene, "off_in_scenes": off, "off": block.bypass is True})
        if blocks or row.split:
            rows.append({"row": r, "split_at": row.split[0] if row.split else -1,
                         "join_at": row.split[1] if row.split else -1, "blocks": blocks})
    return {"rows": rows, "scenes": [label for label in chain.labels if label]}


def to_chain(design, *, offered_ids, base=None, physical_input=1, irs=None):
    """The chain an answer describes, and the problems that stop it being used (empty when it can be).

    `irs` numbers the player's IRs the answer may name (see `player_irs`); each becomes an IR loader holding it.
    """
    problems = []
    irs = irs or {}
    scenes = [str(name).strip()[:SCENE_NAME] for name in design.get("scenes") or [] if str(name).strip()]
    if len(scenes) == 1 or len(scenes) > SCENES:
        problems.append("Name no scenes, or two to eight.")
    # A new design starts from an empty grid with the instrument on row 0; a revision from the sound it revises,
    # whose routing, levels and rows 2 and 3 are kept.
    rows = list(base.rows) if base else [Row(inlet=physical_input, outlet=MULTI_OUT), Row(), Row(), Row()]
    given = {item.get("row"): item for item in design.get("rows") or []}
    if set(given) - {0, 1}:
        problems.append("Use rows 0 and 1 only.")
    for r in (0, 1):
        item = given.get(r)
        if item is None:
            continue
        cells = [None] * 8
        for spec in item.get("blocks") or []:
            column, model_id = spec.get("column"), spec.get("model_id")
            if not isinstance(column, int) or not 0 <= column < 8:
                problems.append(f"Row {r}: column {column} is not on the grid (0 to 7).")
                continue
            if cells[column] is not None:
                problems.append(f"Row {r}: two blocks in column {column}.")
                continue
            if model_id in irs and model_id in offered_ids:
                block, wrong = _ir_block(spec, irs[model_id], len(scenes) or (SCENES if base else 0),
                                         base.block(r, column) if base else None)
                problems += wrong
                cells[column] = block
                continue
            if model_id not in offered_ids or catalog.model(model_id) is None:
                problems.append(f"Model {model_id} is not one of the available models.")
                continue
            kept = base.block(r, column) if base else None
            kept = kept if kept is not None and kept.model == model_id else None
            if model_id in assets.IR_MODELS and kept is None:
                problems.append("An IR loader is used only to hold one of the player's IRs: name the IR by its id.")
                continue
            block, wrong = _block(spec, len(scenes) or (SCENES if base else 0), kept)
            problems += wrong
            cells[column] = block
        split = (item.get("split_at", -1), item.get("join_at", -1))
        split = None if split == (-1, -1) else split
        if base is not None and split == base.rows[r].split:
            pass                                    # A revision may keep a layout the rules would not allow anew.
        elif split and (r != 0 or not 0 <= split[0] < split[1] <= 7):
            problems.append("Only row 0 can split, at a column before the one where it joins.")
            split = None
        rows[r] = replace(rows[r], cells=tuple(cells), split=split)
    # The layout rules hold for new designs. A revision keeps whatever layout the sound it revises already has.
    if rows[0].split and base is None:
        low, high = rows[0].split
        stray = [column for column, _ in rows[1].blocks if not low <= column < high]
        if stray:
            problems.append(f"Row 1 blocks must sit between the split and the join (columns {low} to {high - 1}).")
        second = [block.model for _, block in rows[1].blocks]
        if any(catalog.model(m).category.endswith("Amplifier") for m in second) and \
                not any(catalog.model(m).category.startswith("Cabsim") or m in assets.IR_MODELS for m in second):
            problems.append("The amp on the second path needs its own cab.")
        blend = design.get("blend") or {}
        level = min(max(float(blend.get("level_db", 0) or 0), -20.), 6.)
        spread = min(max(float(blend.get("spread", 0) or 0), 0.), 100.)
        first = [block.model for column, block in rows[0].blocks if low <= column < high]
        mixer = {1: catalog.to_norm(catalog.MIXER, 1, 5 - spread / 20),
                 2: catalog.to_norm(catalog.MIXER, 2, level),
                 3: catalog.to_norm(catalog.MIXER, 3, 5 + spread / 20),
                 4: float(knowledge.opposed(first, second))}
        rows[0] = replace(rows[0], mixer=tuple(mixer.items()))
    elif rows[1].blocks and not base:
        problems.append("Row 1 is used only as the second path of a split on row 0.")
    chain = Chain(tuple(rows), tuple(scenes) + ("",) * (SCENES - len(scenes)) if scenes else
                  (base.labels if base else ("",) * SCENES))
    for pair in ((0, 1), (2, 3)):
        load = knowledge.weigh([block.model for r in pair for _, block in chain.rows[r].blocks])
        if not load.fits:
            problems.append(f"The chain needs more processing than the unit has: "
                            f"{catalog.model(load.refused).name} does not fit.")
    if not chain.blocks:
        problems.append("The chain has no blocks.")
    return chain, problems


def _ir_block(spec, asset, count, kept=None):
    """An IR loader holding one of the player's IRs, from an entry of an answer that names the IR by its id.
    The loader in the same place in the sound being revised is kept when it holds the same IR."""
    held = assets.bound(kept)
    kept = kept if held is not None and held.kind == "ir" and held.key.casefold() == asset.key.casefold() else None
    loader = kept.model if kept is not None else catalog.IR_LOADER
    block, problems = _block({**spec, "model_id": loader}, count, kept, names=_ir_controls(), label=asset.name)
    binding = assets.block_for(asset, stereo=loader == assets.IR_STEREO)
    for index, value in binding.params:
        block = block.set(index, value)
    return block, problems


def _block(spec, count, kept=None, *, names=None, label=None):
    """A block from one entry of an answer. `kept` is the same model in the same place in the sound being
    revised: everything the answer does not say stays as it was there, down to values the assistant never sees.
    `names` limits the controls it may set, and `label` is what the block is called in problems."""
    model_id = spec["model_id"]
    names = controls(model_id) if names is None else names
    label = label or catalog.model(model_id).name
    values, problems = (dict(kept.values) if kept is not None else {}), []
    for setting in spec.get("settings") or []:
        name, value = setting.get("name"), setting.get("value")
        if name not in names:
            problems.append(f"{label} has no control {name!r}.")
            continue
        indices, low, high, whole = names[name]
        if not isinstance(value, (int, float)) or not low <= value <= high or (whole and float(value) != int(value)):
            problems.append(f"{label} {name} {value} is outside {low:g} to {high:g}"
                            + (" in whole steps." if whole else "."))
            continue
        for index in indices:
            values[index] = catalog.to_norm(model_id, index, value)
    for setting in spec.get("scene_settings") or []:
        scene, name, value = setting.get("scene"), setting.get("name"), setting.get("value")
        if not isinstance(scene, int) or not 0 <= scene < count:
            problems.append(f"A scene setting names scene {scene}, which the design does not have.")
            continue
        if name not in names:
            problems.append(f"{label} has no control {name!r}.")
            continue
        indices, low, high, whole = names[name]
        if not isinstance(value, (int, float)) or not low <= value <= high:
            problems.append(f"{label} {name} {value} is outside {low:g} to {high:g}.")
            continue
        for index in indices:
            base = values.get(index)
            if base is None:
                default = catalog.defaults(model_id).get(index)
                base = default if default is not None else catalog.to_norm(model_id, index, low)
            current = list(base) if isinstance(base, tuple) else [base] * SCENES
            current[scene] = catalog.to_norm(model_id, index, value)
            values[index] = tuple(current)
    off = spec.get("off_in_scenes") or []
    bypass = kept.bypass if kept is not None and not off and spec.get("off") is None else False
    if off:
        if any(not isinstance(scene, int) or not 0 <= scene < count for scene in off):
            problems.append("A block is switched off in a scene the design does not have.")
        else:
            bypass = tuple(scene in off for scene in range(SCENES))
    if spec.get("off") is True:
        bypass = True
    return Block(model_id, tuple(values.items()), bypass), problems


# ---- Asking ----

def facts(text, instrument, *, phrase=None, chain=None, earlier=None, models=(), irs=None, guitar=None,
          listening=None):
    notes = knowledge.guitar_notes(instrument.get("name")) if instrument else None
    recipe = _recipe(text) if chain is None else None
    irs = irs or {}
    return {"request": text,
            "instrument": {key: instrument.get(key) for key in ("name", "kind", "about")} if instrument else None,
            **({"instrument_notes": notes} if notes else {}),
            **({"guitar_profile": guitar} if guitar else {}),
            **({"player_listening": listening} if listening else {}),
            **({"phrase": phrase} if phrase else {}),
            **({"community_recipe": recipe} if recipe else {}),
            **({"current_chain": as_form(chain, irs)} if chain is not None else {}),
            **({"earlier": earlier} if earlier else {}),
            "available_models": [ir_card(model_id, irs[model_id]) if model_id in irs else card(model_id)
                                 for model_id in models]}


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


def _ask_checked(assistant, kind, prompt, schema, check, *, by, web, seconds, cancel):
    """Ask, check, and if the answer cannot be used, ask once more with the problems listed."""
    reply = assistant.ask(kind, prompt, schema, by=by, web=web, seconds=seconds, cancel=cancel)
    result, problems = check(reply)
    if not problems:
        return result
    again = (prompt + "\n\n" + sections("repair") + "\n"
             + json.dumps({"problems": problems[:12], "previous_reply": reply}, ensure_ascii=False))
    reply = assistant.ask(kind + " repair", again, schema, by=by, web=False, seconds=seconds, cancel=cancel)
    result, problems = check(reply)
    if problems:
        raise ValueError("The assistant's answer could not be used: " + "; ".join(problems[:3]))
    return result


def _sound(item, chain, origin):
    return Sound(name=str(item.get("name") or "Untitled")[:24], chain=chain, origin=origin,
                 explanation=str(item.get("explanation") or "")[:600],
                 sources=[{"title": str(s.get("title", ""))[:120], "url": s.get("url")}
                          for s in item.get("sources") or [] if str(s.get("url", "")).startswith("https://")][:6])


def played_on(instrument, setting=None, guitar=None):
    """What a sound was made for: the instrument, and the pickup setting of the phrase used when it is known."""
    if not instrument:
        return None
    setting = setting or (guitar or {}).get("recorded_on")
    return {"instrument": instrument.get("name"), **({"setting": setting} if setting else {})}


def round_of_three(assistant, text, instrument, *, phrase=None, earlier=None, research=False, by="player",
                   cancel=None, assets_known=None, guitar=None, setting=None, listening=None):
    """An aim and up to three sounds that reach it by different routes.

    `listening` (`lab.listening.prior`) says what guitar music the player listens to most; the assistant uses
    it only for requests that name no artist, song or sound (designer.md, "listening").

    `assets_known` are the IRs and captures read from the unit (None: those last read, `assets.cached`); the
    player's IRs the request names are offered as models. `guitar` is what the instrument puts out
    (`profile.facts`), given to the assistant as guitar_profile; `setting` is the pickup setting of the phrase in
    use. Each sound's origin carries the input it was made for under "input" (`played_on`)."""
    kind = (instrument or {}).get("kind", "electric")
    irs = player_irs(text, known=assets_known)
    models = _with_irs(offered(text, kind), irs)
    names = ("task", "chain", craft_for(kind), *(("input",) if guitar else ()),
             *(("listening",) if listening else ()), "scenes", "paths", "budget", "round")
    prompt = sections(*names) + "\n\n" + json.dumps(
        facts(text, instrument, phrase=phrase, earlier=earlier, models=models, irs=irs, guitar=guitar,
              listening=listening),
        ensure_ascii=False)
    source = played_on(instrument, setting, guitar)
    origin = {"kind": "designed", "request": text, **({"input": source} if source else {})}
    physical = (instrument or {}).get("input", 1)

    def check(reply):
        try:
            aim = {key: reply["aim"][key] for key in AIM}
            aim["note"] = str(reply["aim"].get("note", ""))[:300]
        except (KeyError, TypeError):
            return None, ["The aim is missing."]
        designs, problems, seen = [], [], set()
        for number, item in enumerate(reply.get("designs") or [], 1):
            chain, wrong = to_chain(item, offered_ids=set(models), physical_input=physical, irs=irs)
            if wrong:
                problems += [f"Design {number}: {problem}" for problem in wrong]
                continue
            if chain.digest() in seen:
                problems.append(f"Design {number} is the same chain as an earlier one.")
                continue
            seen.add(chain.digest())
            designs.append((_sound(item, chain, dict(origin)),
                            str(item.get("differs") or "")[:160]))
        # Two good designs are worth keeping; a repair is asked for only when fewer came back.
        return (aim, designs), (problems if len(designs) < 2 else [])

    return _ask_checked(assistant, "round", prompt, round_schema(models), check, by=by, web=research,
                        seconds=240 if research else 180, cancel=cancel)


def revise(assistant, sound, text, instrument, *, phrase=None, by="player", cancel=None, assets_known=None,
           guitar=None, setting=None):
    """A new sound: `sound` changed as the request asks, everything else kept. The player's IRs the sound
    holds or the request names are offered as models (`assets_known`, `guitar` and `setting` as for
    `round_of_three`)."""
    kind = (instrument or {}).get("kind", "electric")
    irs = player_irs(text, sound.chain, known=assets_known)
    models = _with_irs(offered(text, kind, sound.chain), irs, sound.chain)
    names = ("task", "chain", "revise", craft_for(kind), *(("input",) if guitar else ()), "scenes", "paths", "budget")
    prompt = sections(*names) + "\n\n" + json.dumps(
        facts(text, instrument, phrase=phrase, chain=sound.chain, models=models, irs=irs, guitar=guitar),
        ensure_ascii=False)
    source = played_on(instrument, setting, guitar)

    def check(reply):
        chain, problems = to_chain(reply, offered_ids=set(models), base=sound.chain,
                                   physical_input=(instrument or {}).get("input", 1), irs=irs)
        return chain, problems

    chain = _ask_checked(assistant, "revise", prompt, design_schema(models), check, by=by, web=False, seconds=120,
                         cancel=cancel)
    revised = _sound({"name": sound.name, "explanation": ""}, chain,
                     {"kind": "revised", "request": text, "from": sound.id, **({"input": source} if source else {})})
    revised.parent = sound.id
    return revised
