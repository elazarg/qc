"""Bounded, directional changes relative to the selected guitar tone."""

import math
import re

from qc_mcp import catalog

# Each entry is (catalog category, parameter, maximum move in display units).
STYLES = {
    "more jazz": [("Guitar Overdrive", "DISTORTION", -4), ("Guitar Overdrive", "TONE", -2),
                  ("Guitar Amplifier", "GAIN", -3), ("Guitar Amplifier", "TREBLE", -2),
                  ("Neural Capture", "GAIN", -5), ("Neural Capture", "TREBLE", -2.5),
                  ("Neural Capture", "MID", 1.2), ("Delay", "MIX", -18),
                  ("Delay", "FEEDBACK", -12), ("Reverb", "MIX", -8)],
    "less delay": [("Delay", "MIX", -20), ("Delay", "FEEDBACK", -15)],
    "darker": [("Guitar Overdrive", "TONE", -2), ("Guitar Amplifier", "TREBLE", -2),
               ("Guitar Amplifier", "PRESENCE", -2), ("Neural Capture", "TREBLE", -3)],
}

CABS = ("Cabsim Guitar (M)", "Cabsim Guitar (ST)")
LOW_CUT = [("Equalizer", "HPF", 70), ("Equalizer", "HPF FREQ", 70), ("IRLoaders", "HI PASS", 70),
           *((category, "HPF", 70) for category in CABS)]
HIGH_CUT = [("Equalizer", "LPF", -3500), ("Equalizer", "LPF FREQ", -3500), ("IRLoaders", "LOW PASS", -3500),
            *((category, "LPF", -3500) for category in CABS)]

KNOBS = {
    "jazz": ("Jazz", 0, STYLES["more jazz"]),
    "drive": ("Drive", -1, []),  # The negative side moves toward the available gain floor.
    "warmth": ("Warmth", -1, STYLES["darker"]),
    "space": ("Space", -1, [("Delay", "MIX", 18.0), ("Delay", "FEEDBACK", 12.0),
                            ("Reverb", "MIX", 12.0), ("IRLoaders", "ROOM MIX", 10.0)]),
    "tightness": ("Tightness", -1, [("Guitar Amplifier", "BASS", -2.0),
                                      ("Neural Capture", "BASS", -3.0),
                                      ("Guitar Overdrive", "BASS", -1.8)]),
    "attack": ("Attack", -1, [("Compressor", "ATTACK", 18.0),
                                ("Compressor", "MIX", -15.0)]),
    "sustain": ("Sustain", -1, [("Compressor", "THRESHOLD", -10.0),
                                  ("Compressor", "MIX", 20.0),
                                  ("Guitar Amplifier", "GAIN", 1.2),
                                  ("Neural Capture", "GAIN", 2.5)]),
    "gate": ("Gate", -1, [("Utility", "THRESHOLD", 15.0)]),
    "body": ("Body", -1, [("Guitar Amplifier", "BASS", 2.0), ("Neural Capture", "BASS", 3.0)]),
    "mids": ("Mids", -1, [("Guitar Amplifier", "MID", 2.0), ("Neural Capture", "MID", 3.0)]),
    "presence": ("Presence", -1, [("Guitar Amplifier", "PRESENCE", 2.5),
                                    ("Neural Capture", "TREBLE", 2.0),
                                    ("Guitar Overdrive", "TONE", 1.7)]),
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
    "punch": ("Pick bite", -1, [("Guitar Amplifier", "PRESENCE", 1.8),
                                 ("Guitar Amplifier", "TREBLE", .9), ("Neural Capture", "MID", .8),
                                 ("Neural Capture", "TREBLE", 1.3), ("Equalizer", "2kHz", 1.8)]),
    "low_cut": ("Low cut", -1, LOW_CUT),
    "soft_top": ("Soft top", -1, HIGH_CUT),
    "tail": ("Tail length", -1, [("Reverb", "DECAY", .75), ("Delay", "FEEDBACK", 20.0)]),
    "motion": ("Motion rate", -1, [("Modulation", "RATE", 1.2), ("Modulation", "CHR RATE", .6),
                                    ("Modulation", "VIB RATE", 1.7)]),
    "bloom": ("Bloom", 0, [("Compressor", "THRESHOLD", -5.0), ("Compressor", "MIX", 12.0),
                          ("Compressor", "ATTACK", -5.0), ("Delay", "FEEDBACK", 8.0),
                          ("Reverb", "MIX", 9.0), ("Guitar Amplifier", "GAIN", .7)]),
    "band_fit": ("Band fit", 0, [("Guitar Amplifier", "BASS", -1.6), ("Guitar Amplifier", "MID", 1.2),
                                ("Neural Capture", "BASS", -1.8), ("Neural Capture", "MID", 1.4),
                                ("Delay", "MIX", -6.0), ("Reverb", "MIX", -7.0),
                                ("Equalizer", "125Hz", -1.6), ("Equalizer", "1kHz", 1.4)]),
}

PRIMARY_KNOBS = ("drive", "warmth", "punch", "space", "jazz")
KNOB_GROUPS = {
    "Response": ("tightness", "attack", "sustain", "compression", "gate"),
    "EQ": ("body", "mids", "presence", "low_cut", "soft_top"),
    "Effects": ("delay", "repeats", "delay_time", "reverb", "tail", "modulation", "motion", "width"),
    "Character": ("jazz", "bloom", "band_fit"),
}
KNOB_HINTS = {
    "drive": "Changes pedal and amp input drive. Full left bypasses active drive pedals.",
    "warmth": "Softens the upper end of the amp, capture and drive tone controls.",
    "space": "Changes how much ambience and how many repeats you hear.",
    "jazz": "A rounded direction with less gain and ambience and more midrange.",
    "tightness": "Leans out the bass in the drive and amp path.",
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
    "low_cut": "Moves an available cab or EQ high-pass filter to make space for bass.",
    "soft_top": "Lowers an existing cab or EQ low-pass filter to soften the high end.",
    "tail": "Lengthens the ambience and repeats while keeping the basic wet mix.",
    "motion": "Changes the speed of an active unsynced modulation effect.",
    "bloom": "Softens and lengthens the sound with available compression, gain and ambience.",
    "band_fit": "Looks for midrange definition, less low-end overlap and shorter or quieter effects.",
}

DRIVE_CATEGORIES = {"Guitar Overdrive", "Bass Overdrive", "Guitar Amplifier", "Bass Amplifier", "Neural Capture"}
PEDAL_CATEGORIES = {"Guitar Overdrive", "Bass Overdrive"}
GAIN_NAMES = {"GAIN", "DRIVE", "DISTORTION", "FUZZ", "PREAMP", "PREAMP GAIN"}
AMP_INPUT_GAIN_NAMES = {"GAIN", "GAIN 1", "GAIN 2"}
AMP_CATEGORIES = {"Guitar Amplifier", "Bass Amplifier"}
AMP_VOLUME_AS_DRIVE = {1009, 1011, 1083, 1084, 1094, 1095, 1096, 1097, 1120, 1127, 1128,
                       1154, 1155, 1156, 1166, 1167}
PEDAL_LEVEL_NAMES = {"LEVEL", "VOLUME", "OUTPUT"}
DRIVE_BYPASS = -0.98
DRY_EFFECT_MIX = {"Compressor": ("compression", 25), "Delay": ("delay", 16),
                  "Reverb": ("reverb", 16), "Modulation": ("modulation", 16)}
DRY_EFFECT_DIRECTIONS = {"attack", "delay_time", "repeats", "tail", "motion", "width"}


def intent(text):
    """Recognize deliberately narrow style requests. Free feedback stays as notes."""
    normalized = " ".join(re.findall(r"[a-z]+", text.lower())).replace("moar ", "more ")
    return normalized if normalized in STYLES else None


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


def _drive_delta(category, name, drive, before, low, high, units, *, amp_volume):
    gain_control = name in GAIN_NAMES or amp_volume or (category in AMP_CATEGORIES and name in AMP_INPUT_GAIN_NAMES)
    if category in DRIVE_CATEGORIES and gain_control:
        if drive < 0:
            return (before - low) * drive
        return (high - low) * .15 * drive
    if drive < 0 and category in PEDAL_CATEGORIES and name in PEDAL_LEVEL_NAMES:
        unity = 0 if units == "dB" and low <= 0 <= high else low + .55 * (high - low)
        return max(0, before - unity) * drive
    return 0.0


def _sync_enabled(model, parameters, scene):
    for setting, param in zip(model.params, parameters):
        if param["name"] == "SYNC" and setting.param_values:
            index = scene if setting.scene_mode and scene < len(setting.param_values) else 0
            value = setting.param_values[index]
            return value.WhichOneof("value") in ("float_value", "int_value") and bool(value.int_value or value.float_value >= .5)
    return False


def apply_rules(preset, scene, rules, *, allow_neutral=False, drive=0.0, active_only=False, previews=None):
    result = type(preset)()
    result.CopyFrom(preset)
    changes = []
    previews = previews or {}
    preview_categories = {category for category, _ in previews}
    for row, chain in enumerate(result.chains):
        for column, model in enumerate(chain.models):
            info = catalog.lookup(model.hash)
            if not info:
                continue
            category = info["category"]
            dry_mix, preview = None, 0.
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
                if amp_volume or (category == "Guitar Amplifier" and name in {"GAIN 1", "GAIN 2"}):
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
                if category == "Compressor" and name == "ATTACK" and param["units"] != "ms":
                    delta *= (high - low) * .2 / 18
                if category == "Reverb" and name == "DECAY" and param["units"] != "s":
                    delta *= (high - low) * .22 / .75
                before = catalog.to_display(model.hash, index, value.float_value)
                # The bottom of a conventional 0–10 preamp input knob can mute the
                # amp. Keep low-drive macros audible even when their offsets stack.
                amp_input_gain = category in AMP_CATEGORIES and name in AMP_INPUT_GAIN_NAMES and (low, high) == (0, 10)
                floor = min(before, low + .15 * (high - low)) if amp_volume or amp_input_gain else low
                delta += _drive_delta(category, name, drive, before, floor, high, param["units"], amp_volume=amp_volume)
                after = min(high, max(floor, before + delta))
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
    if not changes and not allow_neutral:
        raise RuntimeError("The current preset has no compatible parameters for that style.")
    return result, changes
