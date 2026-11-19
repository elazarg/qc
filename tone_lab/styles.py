"""Bounded, directional changes relative to the selected guitar tone."""

import math
import re

from . import measured, models as catalog

AMP, PEDAL, CAPTURE = "Guitar Amplifier", "Guitar Overdrive", "Neural Capture"
# What a control does, by its name, on any model that has it. A negative sign marks a control
# that works the other way round: a Vox Tone Cut, a Matchless Cut and a RAT Filter all darken
# as they rise, and a Tight control removes lows. Depth, Resonance and Deep are the power amp's
# low-end resonance. TONE_KNOWLEDGE.md records how each name was checked.
ROLES = {
    (AMP, "treble"): {"TREBLE": 1, "BRILLIANCE": 1, "TONE": 1, "HIGHS": 1},
    (AMP, "presence"): {"PRESENCE": 1, "TONE CUT": -1, "CUT": -1},
    (AMP, "bass"): {"BASS": 1, "LOWS": 1},
    (AMP, "thump"): {"DEPTH": 1, "RESONANCE": 1, "DEEP": 1, "TIGHT": -1},
    (AMP, "mid"): {"MID": 1, "MIDDLE": 1, "MIDS": 1},
    (PEDAL, "treble"): {"TONE": 1, "TREBLE": 1, "HIGH": 1, "SPECTRUM": 1, "PRESENCE": 1, "FILTER": -1, "CUT": -1},
    (PEDAL, "bass"): {"BASS": 1, "LOW": 1, "TIGHT": -1},
    (PEDAL, "gain"): {"GAIN": 1, "DRIVE": 1, "DISTORTION": 1, "OVERDRIVE": 1, "DIST": 1, "DRV": 1,
                      "FUZZ": 1, "SUSTAIN": 1},
}


def role(category, name, amount):
    return [(category, parameter, amount * sign) for parameter, sign in ROLES[category, name].items()]


# Each entry is (catalog category, parameter, maximum move). Amp and pedal moves are written for a
# 0–10 knob and scale to the control's own range. Every other move is in the control's display units.
STYLES = {
    "more jazz": [*role(PEDAL, "gain", -4), *role(PEDAL, "treble", -2),
                  (AMP, "GAIN", -3), *role(AMP, "treble", -2),
                  (CAPTURE, "GAIN", -5), (CAPTURE, "TREBLE", -2.5),
                  (CAPTURE, "MID", 1.2), ("Delay", "MIX", -18),
                  ("Delay", "FEEDBACK", -12), ("Reverb", "MIX", -8)],
    "less delay": [("Delay", "MIX", -20), ("Delay", "FEEDBACK", -15)],
    "darker": [*role(PEDAL, "treble", -2), *role(AMP, "treble", -2),
               *role(AMP, "presence", -2), (CAPTURE, "TREBLE", -3)],
}

CABS = ("Cabsim Guitar (M)", "Cabsim Guitar (ST)")
# Filter moves are in octaves. In each row one stage takes the move: the one whose filter is doing
# the work, which is the lowest low-pass or the highest high-pass, and the first stage when they
# are level. A cab and an equalizer after it never stack two slopes at one frequency.
LOW_CUT = [*((category, "HPF", 1.6) for category in CABS), ("IRLoaders", "HI PASS", 1.6),
           ("Equalizer", "HPF", 1.6), ("Equalizer", "HPF FREQ", 1.6)]
HIGH_CUT = [*((category, "LPF", -1.5) for category in CABS), ("IRLoaders", "LOW PASS", -1.5),
            ("Equalizer", "LPF", -1.5), ("Equalizer", "LPF FREQ", -1.5)]
FILTER_STAGE = {**{rule[:2]: "low" for rule in LOW_CUT}, **{rule[:2]: "high" for rule in HIGH_CUT}}
# A guitar cab passes little below 45 Hz or above 14 kHz, so a filter parked
# outside that span starts its travel from the edge of it.
AUDIBLE_SPAN = (45., 14000.)

KNOBS = {
    "jazz": ("Jazz", 0, STYLES["more jazz"]),
    "drive": ("Drive", -1, []),  # The negative side moves toward the available gain floor.
    "warmth": ("Warmth", -1, STYLES["darker"]),
    "space": ("Space", -1, [("Delay", "MIX", 18.0), ("Delay", "FEEDBACK", 12.0),
                            ("Reverb", "MIX", 12.0), ("IRLoaders", "ROOM MIX", 10.0)]),
    "tightness": ("Tightness", -1, [*role(AMP, "bass", -2.0), *role(AMP, "thump", -2.5),
                                      (CAPTURE, "BASS", -3.0), *role(PEDAL, "bass", -1.8)]),
    "attack": ("Attack", -1, [("Compressor", "ATTACK", 18.0),
                                ("Compressor", "MIX", -15.0)]),
    "sustain": ("Sustain", -1, [("Compressor", "THRESHOLD", -10.0),
                                  ("Compressor", "MIX", 20.0),
                                  (AMP, "GAIN", 1.2),
                                  (CAPTURE, "GAIN", 2.5)]),
    "gate": ("Gate", -1, [("Utility", "THRESHOLD", 15.0)]),
    "body": ("Body", -1, [*role(AMP, "bass", 2.0), *role(AMP, "thump", 1.5), (CAPTURE, "BASS", 3.0)]),
    "mids": ("Mids", -1, [*role(AMP, "mid", 2.0), (CAPTURE, "MID", 3.0)]),
    "presence": ("Presence", -1, [*role(AMP, "presence", 2.5), (CAPTURE, "TREBLE", 2.0),
                                    *role(PEDAL, "treble", 1.7)]),
    "delay": ("Delay mix", -1, [("Delay", "MIX", 30.0)]),
    "repeats": ("Repeats", -1, [("Delay", "FEEDBACK", 25.0)]),
    "delay_time": ("Delay time", -1, [("Delay", "DELAY TIME", 150.0)]),
    "reverb": ("Reverb", -1, [("Reverb", "MIX", 25.0)]),
    "modulation": ("Modulation", -1, [("Modulation", "MIX", 25.0),
                                         ("Modulation", "DEPTH", 20.0)]),
    "width": ("Width", -1, [("Modulation", "WIDTH", 30.0), ("Delay", "WIDTH", 25.0),
                              ("Delay", "STEREO WIDTH", 25.0), ("Reverb", "WIDTH", 25.0)]),
    "compression": ("Evenness", -1, [("Compressor", "THRESHOLD", -9.0), ("Compressor", "MIX", 19.0),
                                      ("Compressor", "RATIO", 1.4)]),
    "punch": ("Pick bite", -1, [*role(AMP, "presence", 1.8), *role(AMP, "treble", .9), (CAPTURE, "MID", .8),
                                 (CAPTURE, "TREBLE", 1.3), ("Equalizer", "2kHz", 1.8)]),
    "mic": ("Mic position", -1, [(category, "POSITION", 5.0) for category in CABS]),
    "low_cut": ("Low cut", -1, LOW_CUT),
    "soft_top": ("Soft top", -1, HIGH_CUT),
    "tail": ("Tail length", -1, [("Reverb", "DECAY", .75), ("Delay", "FEEDBACK", 20.0)]),
    "motion": ("Motion rate", -1, [("Modulation", "RATE", 1.2), ("Modulation", "CHR RATE", .6),
                                    ("Modulation", "VIB RATE", 1.7)]),
    "bloom": ("Bloom", 0, [("Compressor", "THRESHOLD", -5.0), ("Compressor", "MIX", 12.0),
                          ("Compressor", "ATTACK", -5.0), ("Delay", "FEEDBACK", 8.0),
                          ("Reverb", "MIX", 9.0), (AMP, "GAIN", .7)]),
    "band_fit": ("Band fit", 0, [*role(AMP, "bass", -1.6), *role(AMP, "mid", 1.2),
                                (CAPTURE, "BASS", -1.8), (CAPTURE, "MID", 1.4),
                                ("Delay", "MIX", -6.0), ("Reverb", "MIX", -7.0),
                                ("Equalizer", "125Hz", -1.6), ("Equalizer", "1kHz", 1.4)]),
}

PRIMARY_KNOBS = ("drive", "warmth", "punch", "space", "jazz")
KNOB_GROUPS = {
    "Response": ("tightness", "attack", "sustain", "compression", "gate"),
    "EQ": ("body", "mids", "presence", "mic", "low_cut", "soft_top"),
    "Effects": ("delay", "repeats", "delay_time", "reverb", "tail", "modulation", "motion", "width"),
    "Character": ("jazz", "bloom", "band_fit"),
}
KNOB_HINTS = {
    "drive": "Changes pedal and amp input drive. Full right is every gain control at its top. "
             "Full left bypasses active drive pedals.",
    "warmth": "Softens the upper end of the amp, capture and drive tone controls, whichever way each one runs.",
    "space": "Changes how much ambience and how many repeats you hear.",
    "jazz": "A rounded direction with less gain and ambience and more midrange.",
    "tightness": "Leans out the lows in the drive pedal, the amp's bass and its low-end resonance.",
    "attack": "Changes pick response through a compressor, bringing it into the mix if parked dry.",
    "sustain": "Adds compression or drive where the preset supports it.",
    "gate": "Raises the threshold of the Simple Gate block.",
    "body": "Adds low end in the amp or capture.",
    "mids": "Changes midrange emphasis in the amp or capture.",
    "presence": "Changes upper-end bite and definition.",
    "delay": "Changes the level of the delay without changing its repeat count.",
    "repeats": "Changes how quickly the delay fades away and brings a dry delay into the mix.",
    "delay_time": "Lengthens or shortens unsynced delay times and brings a dry delay into the mix.",
    "reverb": "Changes the level of the existing reverb.",
    "modulation": "Changes the amount of the existing modulation effect.",
    "width": "Changes stereo width on effects with a width control.",
    "compression": "Evens out the difference between soft notes and hard picking through an existing compressor.",
    "punch": "Emphasizes the bite of the pick through upper mids and amp or capture brightness.",
    "mic": "Moves both cab microphones from the centre of the speaker, the brightest spot, toward its edge, "
           "which is darker and fuller. Measured on this unit: the edge has about 5 dB less above 6 kHz.",
    "low_cut": "Raises the high-pass filter that is doing the work, on the cab or an equalizer, to clear the lows.",
    "soft_top": "Lowers the low-pass filter that is doing the work, on the cab or an equalizer, to soften fizz.",
    "tail": "Lengthens the ambience and repeats while keeping the basic wet mix.",
    "motion": "Changes the speed of an active unsynced modulation effect.",
    "bloom": "Softens and lengthens the sound with available compression, gain and ambience.",
    "band_fit": "Looks for midrange definition, less low-end overlap and shorter or quieter effects.",
}

DRIVE_CATEGORIES = {"Guitar Overdrive", "Bass Overdrive", "Guitar Amplifier", "Bass Amplifier", "Neural Capture"}
PEDAL_CATEGORIES = {"Guitar Overdrive", "Bass Overdrive"}
GAIN_NAMES = {"PREAMP", "PREAMP GAIN", *ROLES[PEDAL, "gain"]}
# Amp controls that follow a rule written for GAIN.
AMP_GAIN_ALIASES = {"GAIN 1", "GAIN 2", "GAIN I", "GAIN II", "INPUT GAIN", "GAIN NORMAL", "GAIN OD"}
AMP_INPUT_GAIN_NAMES = {"GAIN", *AMP_GAIN_ALIASES}
AMP_CATEGORIES = {"Guitar Amplifier", "Bass Amplifier"}


def _volume_is_the_drive():
    """Amps with a Volume and no gain control, such as a Vox, a blackface Fender or a Dumble.

    Turning those up is how they break up, so Volume takes the place of Gain.
    """
    found = set()
    for model in catalog.all_models():
        if model["category"] == AMP:
            names = {param["name"] for param in model["params"] if param["type"] in ("float", "fader")}
            if "VOLUME" in names and not names & (AMP_INPUT_GAIN_NAMES | {"DRIVE", "VOLUME NORMAL", "VOLUME BRIGHT"}):
                found.add(model["id"])
    return frozenset(found)


AMP_VOLUME_AS_DRIVE = _volume_is_the_drive()
SCALED_CATEGORIES = AMP_CATEGORIES | {"Guitar Overdrive", "Bass Overdrive"}
PEDAL_LEVEL_NAMES = {"LEVEL", "VOLUME", "OUTPUT"}
DRIVE_BYPASS = -0.98
DRY_EFFECT_MIX = {"Compressor": ("compression", 25), "Delay": ("delay", 16),
                  "Reverb": ("reverb", 16), "Modulation": ("modulation", 16)}
DRY_EFFECT_DIRECTIONS = {"attack", "delay_time", "repeats", "tail", "motion", "width"}


def intent(text):
    """Recognize deliberately narrow style requests. Free feedback stays as notes."""
    normalized = " ".join(re.findall(r"[a-z]+", text.lower())).replace("moar ", "more ")
    return normalized if normalized in STYLES else None


AMOUNTS = {"a touch": .5, "a tad": .5, "a bit": .5, "a little bit": .5, "a little": .5, "slightly": .5,
           "just a bit": .5, "somewhat": .7, "a lot": 1.7, "much": 1.7, "way": 2., "far": 1.7, "really": 1.5,
           "very": 1.5, "so much": 1.7}
COMPARATIVES = {
    "brighter": ("warmth", -.3), "darker": ("warmth", .35), "warmer": ("warmth", .3), "duller": ("warmth", .3),
    "tighter": ("tightness", .3), "looser": ("tightness", -.3),
    "dirtier": ("drive", .3), "heavier": ("drive", .3), "crunchier": ("drive", .25), "cleaner": ("drive", -.3),
    "drier": ("space", -.3), "dryer": ("space", -.3), "wetter": ("space", .3), "roomier": ("space", .3),
    "fuller": ("body", .3), "thicker": ("body", .3), "fatter": ("body", .3), "thinner": ("body", -.3),
    "punchier": ("punch", .3), "wider": ("width", .3), "narrower": ("width", -.3), "jazzier": ("jazz", .4),
}
QUANTITIES = {
    "gain": "drive", "drive": "drive", "distortion": "drive", "dirt": "drive", "saturation": "drive",
    "crunch": "drive", "reverb": "reverb", "delay": "delay", "echo": "delay", "repeats": "repeats",
    "sustain": "sustain", "mids": "mids", "midrange": "mids", "body": "body", "bass": "body", "low end": "body",
    "lows": "body", "bite": "punch", "punch": "punch", "attack": "attack", "presence": "presence",
    "treble": "presence", "highs": "presence", "top end": "presence", "space": "space", "room": "space",
    "ambience": "space", "compression": "compression", "modulation": "modulation", "tremolo": "modulation",
    "chorus": "modulation", "width": "width", "jazz": "jazz", "warmth": "warmth", "tightness": "tightness",
    "gate": "gate",
}
COMPLAINTS = {
    "bright": ("warmth", .3), "harsh": ("warmth", .3), "shrill": ("warmth", .3), "fizzy": ("soft_top", .35),
    "dark": ("warmth", -.3), "dull": ("warmth", -.3), "muddy": ("tightness", .3), "boomy": ("tightness", .3),
    "thin": ("body", .3), "dry": ("space", .3), "wet": ("space", -.3), "washy": ("space", -.3),
    "dirty": ("drive", -.3), "distorted": ("drive", -.3), "clean": ("drive", .3), "noisy": ("gate", .3),
    "compressed": ("compression", -.3),
}
STEPS = {"jazz": .4, "delay": .3}
_AMOUNT = "|".join(sorted(map(re.escape, AMOUNTS), key=len, reverse=True))
_QUANTITY = "|".join(sorted(map(re.escape, QUANTITIES), key=len, reverse=True))
_LEAD = r"(?:(?:can you |could you |please )?(?:make (?:it|this|that|the tone|the sound) |it(?:'s| is| sounds| feels) |sounds |needs |i need |i want |give (?:me|it) )?)"


def direction(text):
    """Read a plain request for more or less of one quality as a bounded knob move.

    Only whole, unambiguous phrases are recognized, such as “a bit tighter”,
    “much less reverb” or “too bright”. Anything else is left to the assistant.
    """
    words = " ".join(re.findall(r"[a-z']+", text.lower().replace("’", "'"))).replace("moar ", "more ")
    words = re.sub(r"\s*(?:please|thanks|thank you)$", "", words)
    simple = re.fullmatch(rf"{_LEAD}(?:({_AMOUNT}) )?(\w+)", words)
    if simple and simple[2] in COMPARATIVES:
        key, step = COMPARATIVES[simple[2]]
        return {key: round(step * AMOUNTS.get(simple[1], 1.), 2)}
    measured = re.fullmatch(rf"{_LEAD}(?:({_AMOUNT}) )?(more|less) ({_QUANTITY})", words)
    if measured:
        key = QUANTITIES[measured[3]]
        step = STEPS.get(key, .3) * AMOUNTS.get(measured[1], 1.)
        return {key: round(step if measured[2] == "more" else -step, 2)}
    lacking = re.fullmatch(rf"{_LEAD}(too much|not enough|too little) ({_QUANTITY})", words)
    if lacking:
        key = QUANTITIES[lacking[2]]
        return {key: round(STEPS.get(key, .3) * (-1 if lacking[1] == "too much" else 1), 2)}
    complaint = re.fullmatch(rf"{_LEAD}(?:({_AMOUNT}) )?too (\w+)", words)
    if complaint and complaint[2] in COMPLAINTS:
        key, step = COMPLAINTS[complaint[2]]
        return {key: round(step * AMOUNTS.get(complaint[1], 1.), 2)}
    return None


def apply(preset, scene, style, amount):
    if style not in STYLES or not 0 < amount <= 1:
        raise ValueError("Choose a supported style and an amount above zero and up to one.")
    return apply_rules(preset, scene, {(category, name): amount * delta for category, name, delta in STYLES[style]})


def knobs(preset, scene, values):
    rules = {}
    previews = {}
    for name, value in values.items():
        _, low, edits = KNOBS[name]
        if not low <= value <= 1 or not math.isfinite(value):
            raise ValueError(f"Invalid amount for {name}.")
        for category, parameter, delta in edits:
            key = (category, parameter)
            rules[key] = rules.get(key, 0.0) + value * delta
            if name in DRY_EFFECT_DIRECTIONS and category in DRY_EFFECT_MIX:
                mix_knob, mix_percent = DRY_EFFECT_MIX[category]
                explicit_cut = values.get(mix_knob, 0) < 0 or (
                    category in {"Delay", "Reverb"} and values.get("space", 0) < 0)
                if value and parameter != "MIX" and not explicit_cut:
                    previews[key] = max(previews.get(key, 0), abs(value) * mix_percent)
    return apply_rules(preset, scene, rules, allow_neutral=True, drive=values.get("drive", 0.0),
                       active_only=True, previews=previews)[1]


def available_knobs(preset, scene):
    return [key for key, (_, lower, _) in KNOBS.items()
            if knobs(preset, scene, {key: 1}) or (lower < 0 and knobs(preset, scene, {key: lower}))]


def draft_controls(draft):
    """Estimate which directions a draft's chain can move, as (model, parameter) pairs.

    This reads the recipe only, so it is available before the chain is on the
    Quad Cortex. The live monitor's own report replaces it once the tone loads.
    """
    blocks, best = [], {}
    # Each path of a draft is a row on the unit, and a filter move has one owner per row.
    placed = [(path, place, block) for path in ("chain", "parallel")
              for place, block in enumerate(draft.get(path) or [])]
    for path, place, block in placed:
        info = catalog.lookup(block.get("model_id"))
        if info:
            names = [param["name"] for param in info["params"] if param["type"] in ("float", "fader")]
            blocks.append(((path, place), block["model_id"], info["category"], info["name"], names))
            chosen = {setting["name"]: setting["value"] for setting in block.get("settings", [])}
            for name in names:
                stage = FILTER_STAGE.get((info["category"], name))
                if stage:
                    rank = chosen.get(name, 20000.) if stage == "high" else -chosen.get(name, 20.)
                    if (path, stage) not in best or rank < best[path, stage][0]:
                        best[path, stage] = (rank, (path, place))
    controls = {}
    for key, (_, _, edits) in KNOBS.items():
        moves = []
        for column, model_id, category, model, names in blocks:
            wanted = [parameter for rule_category, parameter, _ in edits if rule_category == category
                      and best.get((column[0], FILTER_STAGE.get((category, parameter))), (0, column))[1] == column]
            if category == "Utility" and model_id != 16003:
                wanted = []
            if category in AMP_CATEGORIES and "GAIN" in wanted:
                wanted += [*AMP_GAIN_ALIASES, "VOLUME BRIGHT", "VOLUME NORMAL"]
                if model_id in AMP_VOLUME_AS_DRIVE:
                    wanted.append("VOLUME")
            if key == "drive" and category in DRIVE_CATEGORIES:
                wanted = [name for name in names if name in GAIN_NAMES | AMP_INPUT_GAIN_NAMES
                          or name in ("VOLUME BRIGHT", "VOLUME NORMAL")
                          or name == "VOLUME" and model_id in AMP_VOLUME_AS_DRIVE]
            moves += [(model, name.title() if name.isupper() else name) for name in names if name in wanted]
        if moves:
            controls[key] = tuple(dict.fromkeys(moves))
    return controls


def _drive_delta(category, name, drive, before, low, high, units, *, amp_volume):
    gain_control = name in GAIN_NAMES or amp_volume or (category in AMP_CATEGORIES and name in AMP_INPUT_GAIN_NAMES)
    if category in DRIVE_CATEGORIES and gain_control:
        if drive < 0:
            return (before - low) * drive
        # Full right is the top of the control, as full left is its floor. The square keeps small turns small:
        # a third of the way adds about a tenth of what is left. A fuzz does most of its work in the last point,
        # so a knob that stopped short of the top never reached it.
        return (high - before) * drive * drive
    if drive < 0 and category in PEDAL_CATEGORIES and name in PEDAL_LEVEL_NAMES:
        unity = 0 if units == "dB" and low <= 0 <= high else low + .55 * (high - low)
        return max(0, before - unity) * drive
    return 0.0


def _hold_level(model, info, scene, lift, active_only, row, column, changes):
    """Take back, at the amp's clean Output, the level its gain was measured to add.

    Drive asks for more distortion, not more volume. On an amp that stays
    clean, gain is mostly volume: a Hiwatt taken from 5 to 10 measured 8 dB
    louder. The figure is the amp's own, measured with nothing in front of it,
    so with a pedal driving it the result errs towards quieter.
    """
    for index, (setting, param) in enumerate(zip(model.params, info["params"])):
        slot = scene if setting.scene_mode else 0
        if (param["name"] != "OUTPUT" or param["units"] != "dB" or setting.expression
                or setting.scene_mode and not active_only or slot >= len(setting.param_values)
                or setting.param_values[slot].WhichOneof("value") != "float_value"):
            continue
        before = catalog.to_display(model.hash, index, setting.param_values[slot].float_value)
        after = max(float(param["min"]), before - lift)
        raw = catalog.to_norm(model.hash, index, after)
        for entry in (setting.param_values[slot],) if setting.scene_mode else setting.param_values:
            if entry.WhichOneof("value") == "float_value":
                entry.float_value = raw
        changes.append({"row": row + 1, "column": column + 1, "model": info["name"],
                        "index": index, "name": "OUTPUT", "from": before, "to": after})
        return


def _sync_enabled(model, parameters, scene):
    for setting, param in zip(model.params, parameters):
        if param["name"] == "SYNC" and setting.param_values:
            index = scene if setting.scene_mode and scene < len(setting.param_values) else 0
            value = setting.param_values[index]
            return value.WhichOneof("value") in ("float_value", "int_value") and bool(value.int_value or value.float_value >= .5)
    return False


def _filter_owners(preset, scene):
    """In each row, the column whose filter is doing the work: the lowest low-pass and the highest high-pass."""
    owners = {}
    for row, chain in enumerate(preset.chains):
        best = {}
        for column, model in enumerate(chain.models):
            info = catalog.lookup(model.hash)
            if not info or not catalog.stored_as_listed(model, info):
                continue
            if row < len(preset.bypass) and column < len(preset.bypass[row].colBypass):
                block = preset.bypass[row].colBypass[column]
                slot = scene if block.sceneMode else 0
                if len(block.sceneBypass) > slot and block.sceneBypass[slot].bypass:
                    continue
            for index, (setting, param) in enumerate(zip(model.params, info["params"])):
                stage = FILTER_STAGE.get((info["category"], param["name"]))
                slot = scene if setting.scene_mode else 0
                if (not stage or slot >= len(setting.param_values)
                        or setting.param_values[slot].WhichOneof("value") != "float_value"):
                    continue
                value = catalog.to_display(model.hash, index, setting.param_values[slot].float_value)
                rank = value if stage == "high" else -value
                if stage not in best or rank < best[stage][0] - 1e-6:
                    best[stage] = (rank, column)
        owners.update({(row, stage): column for stage, (_, column) in best.items()})
    return owners


def apply_rules(preset, scene, rules, *, allow_neutral=False, drive=0.0, active_only=False, previews=None):
    result = type(preset)()
    result.CopyFrom(preset)
    changes = []
    previews = previews or {}
    preview_categories = {category for category, _ in previews}
    owners = _filter_owners(result, scene)
    for row, chain in enumerate(result.chains):
        for column, model in enumerate(chain.models):
            info = catalog.lookup(model.hash)
            if not info or not catalog.stored_as_listed(model, info):
                continue
            category = info["category"]
            dry_mix, preview = None, 0.
            lift = 0.  # dB the amp gets louder from the gain the Drive knob adds.
            if row < len(result.bypass) and column < len(result.bypass[row].colBypass):
                block = result.bypass[row].colBypass[column]
                bypass_index = scene if block.sceneMode else 0
                if len(block.sceneBypass) > bypass_index:
                    current_bypass = block.sceneBypass[bypass_index]
                    if current_bypass.bypass:
                        continue
                    if drive <= DRIVE_BYPASS and category in PEDAL_CATEGORIES:
                        current_bypass.bypass = True
                        changes.append({"kind": "bypass", "row": row + 1, "column": column + 1,
                                        "model": info["name"], "from": False, "to": True})
            for index, (setting, param) in enumerate(zip(model.params, info["params"])):
                name = param["name"]
                amp_volume = category == "Guitar Amplifier" and (
                    name in {"VOLUME BRIGHT", "VOLUME NORMAL"} or name == "VOLUME" and model.hash in AMP_VOLUME_AS_DRIVE)
                delta = rules.get((category, name), 0.0)
                if amp_volume or (category == "Guitar Amplifier" and name in AMP_GAIN_ALIASES):
                    delta += rules.get((category, "GAIN"), 0.0)
                if (category == "Utility" and name == "THRESHOLD" and model.hash != 16003):
                    continue
                index_in_scene = scene if setting.scene_mode else 0
                if (param["type"] not in {"float", "fader"} or setting.expression
                        or setting.scene_mode and not active_only or index_in_scene >= len(setting.param_values)):
                    continue
                if not delta and not (drive and category in DRIVE_CATEGORIES) and not (
                        name == "MIX" and category in preview_categories):
                    continue
                if category == "Delay" and name == "DELAY TIME" and _sync_enabled(model, info["params"], scene):
                    continue
                value = setting.param_values[index_in_scene]
                if value.WhichOneof("value") != "float_value":
                    continue
                try:
                    low, high = float(param["min"]), float(param["max"])
                except (TypeError, ValueError):
                    continue
                if category in SCALED_CATEGORIES:
                    delta *= (high - low) / 10
                if category == AMP and delta:
                    # Amps differ widely in how much a tone control does, so the same turn is made to mean the same.
                    delta *= measured.scale(model.hash, name, rising=delta > 0)
                if category == "Compressor" and name == "ATTACK" and param["units"] != "ms":
                    delta *= (high - low) * .2 / 18
                if category == "Reverb" and name == "DECAY" and param["units"] != "s":
                    delta *= (high - low) * .22 / .75
                before = catalog.to_display(model.hash, index, value.float_value)
                stage = FILTER_STAGE.get((category, name))
                if stage and delta:
                    if owners.get((row, stage)) != column:
                        continue
                    start = (max(before, AUDIBLE_SPAN[0]) if stage == "low" and delta > 0
                             else min(before, AUDIBLE_SPAN[1]) if stage == "high" and delta < 0 else before)
                    delta = start * 2 ** delta - before
                # The bottom of a conventional 0–10 preamp input knob can mute the
                # amp. Keep low-drive macros audible even when their offsets stack.
                amp_input_gain = category in AMP_CATEGORIES and name in AMP_INPUT_GAIN_NAMES and (low, high) == (0, 10)
                floor = min(before, low + .15 * (high - low)) if amp_volume or amp_input_gain else low
                pushed = _drive_delta(category, name, drive, before, floor, high, param["units"], amp_volume=amp_volume)
                delta += pushed
                after = min(high, max(floor, before + delta))
                if pushed > 0 and category == AMP and after > before:
                    louder = ((measured.effect(model.hash, name) or {}).get("high") or {}).get("level") or 0.
                    lift += max(0., louder) * min(pushed, after - before) / ((high - low) / 10)
                if name == "MIX" and param["units"] == "%" and low == 0 and before == after == 0:
                    dry_mix = index, setting, value, high
                if math.isclose(before, after, abs_tol=0.001):
                    continue
                preview = max(preview, previews.get((category, name), 0.))
                raw = catalog.to_norm(model.hash, index, after)
                targets = (value,) if setting.scene_mode else setting.param_values
                for entry in targets:
                    if entry.WhichOneof("value") == "float_value":
                        entry.float_value = raw
                changes.append({"row": row + 1, "column": column + 1, "model": info["name"],
                                "index": index, "name": param["name"], "from": before, "to": after})
            if dry_mix and preview:
                index, setting, value, high = dry_mix
                after = min(high, preview)
                raw = catalog.to_norm(model.hash, index, after)
                for entry in (value,) if setting.scene_mode else setting.param_values:
                    if entry.WhichOneof("value") == "float_value":
                        entry.float_value = raw
                changes.append({"row": row + 1, "column": column + 1, "model": info["name"],
                                "index": index, "name": "MIX", "from": 0., "to": after})
            if lift > .05:
                _hold_level(model, info, scene, lift, active_only, row, column, changes)
    if not changes and not allow_neutral:
        raise RuntimeError("The current preset has no compatible parameters for that style.")
    return result, changes
