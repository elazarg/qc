"""The learner's fitted profiles as files: one check of their shape, one way to save and one to read.

Two kinds are fitted, and told apart by what they hold:

- one control's settings (`state.fit`): "labels" and, per note, a centre for each label;
- a guitar's configuration (`configuration.fit`): "setups" (pickup, wiring and the knob positions taught)
  and, per note, the spread.

    profiles.save(path, profile)
    profile = profiles.load(path)
    profiles.validate(profile)        # returns it, or raises ValueError
"""

import json
import math
from pathlib import Path

import numpy as np

from lab.learner.folders import write_json

SCHEMA = 1
CONTROLS = ("pickup", "tone", "volume")
HARMONICS = 12
SIGNATURE_SIZE = HARMONICS + 1          # the harmonics and the level
CATEGORY_KEYS = ("pickup", "wiring")


def setup(config):
    """(pickup, wiring, knobs) of one labelled configuration, checked."""
    if not isinstance(config, dict):
        raise ValueError("Enter a pickup and knob settings for each example.")
    pickup, wiring = config.get("pickup"), config.get("wiring")
    if isinstance(pickup, str):
        pickup = pickup.strip()
    if type(pickup) is not int and not (isinstance(pickup, str) and pickup):
        raise ValueError("Choose a pickup position for each example.")
    if wiring is not None and (not isinstance(wiring, str) or not wiring.strip()):
        raise ValueError("Choose the wiring switch position for each example.")
    knobs = {key: value for key, value in config.items() if key not in CATEGORY_KEYS}
    if (not 1 <= len(knobs) <= 4 or any(not isinstance(key, str) or not key for key in knobs)
            or any(type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 10
                   for value in knobs.values())):
        raise ValueError("Enter knob settings between 0 and 10.")
    return pickup, wiring.strip() if wiring is not None else None, knobs


def _one_control(profile):
    labels, notes, control = profile["labels"], profile["notes"], profile["control"]
    if (control not in CONTROLS or not isinstance(labels, list) or len(labels) < 2
            or any(not isinstance(name, str) or not name.strip() for name in labels)
            or len(set(name.casefold() for name in labels)) != len(labels) or not isinstance(notes, dict)
            or not notes):
        raise ValueError
    width = 1 if control == "volume" else HARMONICS
    for note, record in notes.items():
        shapes = {"centers": (len(labels), width), "scale": (width,), "radii": (len(labels),)}
        if not isinstance(note, str) or not note.isdigit() or not 32 <= int(note) <= 95:
            raise ValueError
        for key, shape in shapes.items():
            values = np.asarray(record[key], dtype=float)
            if values.shape != shape or not np.isfinite(values).all() or (key != "centers" and np.min(values) <= 0):
                raise ValueError


def _configuration(profile):
    knobs, setups, scales = profile["knobs"], profile["setups"], profile["scales"]
    if not 1 <= len(knobs) <= 4 or len(knobs) != len(set(knobs)) or not setups or not scales:
        raise ValueError
    for values in scales.values():
        scale = np.asarray(values, dtype=float)
        if scale.shape != (SIGNATURE_SIZE,) or not np.isfinite(scale).all() or np.min(scale) <= 0:
            raise ValueError
    for row in setups:
        setup({"pickup": row["pickup"], "wiring": row["wiring"], **dict.fromkeys(row["knobs"], 0)})
        if not set(row["knobs"]) <= set(knobs) or not row["notes"]:
            raise ValueError
        for note, anchors in row["notes"].items():
            positions = np.asarray(anchors["positions"], dtype=float)
            centers = np.asarray(anchors["centers"], dtype=float)
            if (note not in scales or positions.shape != (len(centers), len(row["knobs"]))
                    or centers.shape != (len(centers), SIGNATURE_SIZE) or not len(centers)
                    or not np.isfinite(centers).all() or not np.isfinite(positions).all()
                    or positions.min() < 0 or positions.max() > 10):
                raise ValueError


def validate(profile):
    """The profile, when it has the shape one of the two kinds must have."""
    try:
        if profile["schema"] != SCHEMA or not isinstance(profile["guitar"], str) or not profile["guitar"].strip():
            raise ValueError
        (_configuration if "setups" in profile else _one_control)(profile)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ValueError("This learned profile could not be read: it is damaged or from another version.") from error
    return profile


def save(path, profile):
    path = Path(path)
    validate(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, profile)
    return path


def load(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))
