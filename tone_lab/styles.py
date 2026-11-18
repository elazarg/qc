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
    "drive": ("Drive", -1, [("Guitar Overdrive", "DISTORTION", 3.0),
                            ("Guitar Amplifier", "GAIN", 2.5), ("Neural Capture", "GAIN", 4.0)]),
    "warmth": ("Warmth", -1, STYLES["darker"]),
    "space": ("Space", -1, [("Delay", "MIX", 18.0), ("Delay", "FEEDBACK", 12.0),
                            ("Reverb", "MIX", 12.0)]),
}


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
    return apply_rules(preset, scene, rules, allow_neutral=True)[1]


def apply_rules(preset, scene, rules, *, allow_neutral=False):
    result = type(preset)()
    result.CopyFrom(preset)
    changes = []
    for row, chain in enumerate(result.chains):
        for column, model in enumerate(chain.models):
            info = catalog.lookup(model.hash)
            if not info:
                continue
            if row < len(result.bypass) and column < len(result.bypass[row].colBypass):
                block = result.bypass[row].colBypass[column]
                bypass_index = scene if block.sceneMode else 0
                if len(block.sceneBypass) > bypass_index and block.sceneBypass[bypass_index].bypass:
                    continue
            for index, (setting, param) in enumerate(zip(model.params, info["params"])):
                delta = rules.get((info["category"], param["name"]))
                if (delta is None or param["type"] != "float" or setting.expression
                        or setting.scene_mode or not setting.param_values):
                    continue
                value = setting.param_values[0]
                if value.WhichOneof("value") != "float_value":
                    continue
                before = catalog.to_display(model.hash, index, value.float_value)
                after = min(float(param["max"]), max(float(param["min"]), before + delta))
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
