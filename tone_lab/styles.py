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

KNOBS = {
    "jazz": ("Jazz", 0, STYLES["more jazz"]),
    "drive": ("Drive", -1, []),  # The negative side moves toward the available gain floor.
    "warmth": ("Warmth", -1, STYLES["darker"]),
    "space": ("Space", -1, [("Delay", "MIX", 18.0), ("Delay", "FEEDBACK", 12.0),
                            ("Reverb", "MIX", 12.0)]),
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
}

PRIMARY_KNOBS = ("drive", "warmth", "space", "jazz")
KNOB_GROUPS = {
    "Response": ("tightness", "attack", "sustain", "gate"),
    "EQ": ("body", "mids", "presence"),
    "Effects": ("delay", "repeats", "delay_time", "reverb", "modulation", "width"),
}
KNOB_HINTS = {
    "drive": "Changes pedal and amp input drive. Full left bypasses active drive pedals.",
    "warmth": "Softens the upper end of the amp, capture and drive tone controls.",
    "space": "Changes how much ambience and how many repeats you hear.",
    "jazz": "A rounded direction with less gain and ambience and more midrange.",
    "tightness": "Leans out the bass in the drive and amp path.",
    "attack": "Lets more of the transient through active compressors.",
    "sustain": "Adds compression or drive where the preset supports it.",
    "gate": "Raises the threshold of the Simple Gate block.",
    "body": "Adds low end in the amp or capture.",
    "mids": "Changes midrange emphasis in the amp or capture.",
    "presence": "Changes upper-end bite and definition.",
    "delay": "Changes the level of the delay without changing its repeat count.",
    "repeats": "Changes how quickly the delay fades away.",
    "delay_time": "Lengthens or shortens unsynced delay times.",
    "reverb": "Changes the level of the existing reverb.",
    "modulation": "Changes the amount of the existing modulation effect.",
    "width": "Changes stereo width on effects with a width control.",
}

DRIVE_CATEGORIES = {"Guitar Overdrive", "Bass Overdrive", "Guitar Amplifier", "Bass Amplifier", "Neural Capture"}
PEDAL_CATEGORIES = {"Guitar Overdrive", "Bass Overdrive"}
GAIN_NAMES = {"GAIN", "DRIVE", "DISTORTION", "FUZZ", "PREAMP", "PREAMP GAIN"}
PEDAL_LEVEL_NAMES = {"LEVEL", "VOLUME", "OUTPUT"}
DRIVE_BYPASS = -0.98


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
    for name, value in values.items():
        _, low, edits = KNOBS[name]
        if not low <= value <= 1 or not math.isfinite(value):
            raise ValueError(f"Invalid amount for {name}.")
        for category, parameter, delta in edits:
            key = (category, parameter)
            rules[key] = rules.get(key, 0.0) + value * delta
    return apply_rules(preset, scene, rules, allow_neutral=True, drive=values.get("drive", 0.0))[1]


def available_knobs(preset, scene):
    return [key for key, (_, lower, _) in KNOBS.items()
            if knobs(preset, scene, {key: 1}) or (lower < 0 and knobs(preset, scene, {key: lower}))]


def _drive_delta(category, name, drive, before, low, high, units):
    if category in DRIVE_CATEGORIES and name in GAIN_NAMES:
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


def apply_rules(preset, scene, rules, *, allow_neutral=False, drive=0.0):
    result = type(preset)()
    result.CopyFrom(preset)
    changes = []
    for row, chain in enumerate(result.chains):
        for column, model in enumerate(chain.models):
            info = catalog.lookup(model.hash)
            if not info:
                continue
            category = info["category"]
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
                delta = rules.get((category, name), 0.0)
                if (category == "Utility" and name == "THRESHOLD" and model.hash != 16003):
                    continue
                if (param["type"] != "float" or setting.expression
                        or setting.scene_mode or not setting.param_values):
                    continue
                if not delta and not (drive and category in DRIVE_CATEGORIES):
                    continue
                if category == "Delay" and name == "DELAY TIME" and _sync_enabled(model, info["params"], scene):
                    continue
                value = setting.param_values[0]
                if value.WhichOneof("value") != "float_value":
                    continue
                try:
                    low, high = float(param["min"]), float(param["max"])
                except (TypeError, ValueError):
                    continue
                if category == "Compressor" and name == "ATTACK" and param["units"] != "ms":
                    delta *= (high - low) * .2 / 18
                before = catalog.to_display(model.hash, index, value.float_value)
                delta += _drive_delta(category, name, drive, before, low, high, param["units"])
                after = min(high, max(low, before + delta))
                if math.isclose(before, after, abs_tol=0.001):
                    continue
                raw = catalog.to_norm(model.hash, index, after)
                for entry in setting.param_values:
                    if entry.WhichOneof("value") == "float_value":
                        entry.float_value = raw
                changes.append({"row": row + 1, "column": column + 1, "model": info["name"],
                                "index": index, "name": param["name"], "from": before, "to": after})
    if not changes and not allow_neutral:
        raise RuntimeError("The current preset has no compatible parameters for that style.")
    return result, changes
