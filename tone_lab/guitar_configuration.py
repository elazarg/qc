"""Infer a pickup and approximate knob positions from mixed DI examples."""

from collections import defaultdict
from itertools import product
import json
import math
from pathlib import Path

import numpy as np
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import QhullError

from . import guitar_state, session

SCHEMA = 1
SIGNATURE_SIZE = guitar_state.HARMONICS + 1
CATEGORY_KEYS = ("pickup", "wiring")
METADATA_KEYS = {"wav", "start", "end", "source_id", "role", "id"}
LEVEL_WEIGHT = .4
MAX_DISTANCE = 2.1
NEAR_BEST = .66


def _configuration(example):
    config = example.get("configuration")
    if config is None:
        config = {key: value for key, value in example.items() if key not in METADATA_KEYS}
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


def _signatures(dry, start=None, end=None):
    grouped = defaultdict(list)
    for note, features in guitar_state._observations(guitar_state._samples(dry, start, end), "configuration"):
        grouped[str(note)].append(features)
    signatures = {}
    for note, frames in grouped.items():
        if len(frames) >= 3:
            values = np.asarray(frames)
            center = np.median(values, axis=0)
            spread = 1.4826 * np.median(np.abs(values - center), axis=0)
            signatures[note] = center, spread
    return signatures


def usable_notes(dry):
    """Report sustained pitches suitable for a labeled example."""
    return sorted(map(int, _signatures(dry)))


def _active_knobs(pickup, knobs):
    # Independent Les Paul knobs on the disconnected pickup are inaudible.
    if set(knobs) == {"bridge_tone", "bridge_volume", "neck_tone", "neck_volume"} and pickup in (1, 3):
        connected = "bridge_" if pickup == 1 else "neck_"
        return [key for key in knobs if key.startswith(connected)]
    return list(knobs)


def calibrate(guitar, examples):
    """Fit note-dependent templates on the observed part of each pickup's knob ranges."""
    guitar = guitar.strip()
    if not guitar:
        raise ValueError("Choose the guitar these examples came from.")
    knots = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    noise = defaultdict(list)
    knobs = None
    source_ids = set()
    count = 0
    for example in examples:
        if example.get("role") not in (None, "example"):
            raise ValueError("Keep test performances out of the calibration examples.")
        pickup, wiring, labelled = _configuration(example)
        controls = sorted(labelled)
        if knobs is not None and knobs != controls:
            raise ValueError("Use examples for one guitar control layout at a time.")
        knobs = controls
        active = _active_knobs(pickup, knobs)
        position = tuple(float(labelled[key]) for key in active)
        start, end = (None, None) if "configuration" in example else (example.get("start"), example.get("end"))
        notes = _signatures(example["wav"], start, end)
        if not notes:
            raise ValueError(f"Example {count + 1} has no sustained single notes. Record a few clear notes again.")
        group = knots[pickup, wiring]
        for note, (center, spread) in notes.items():
            group[note][position].append(center)
            noise[note].append(spread)
        if source_id := example.get("source_id"):
            source_ids.add(source_id)
        count += 1
    if count < 2:
        raise ValueError("Record at least two labeled examples of this guitar.")

    setups = []
    for (pickup, wiring), notes in knots.items():
        setups.append({"pickup": pickup, "wiring": wiring, "knobs": _active_knobs(pickup, knobs),
                       "notes": {note: {"positions": [list(position) for position in rows],
                                        "centers": [np.median(windows, axis=0).tolist() for windows in rows.values()]}
                                 for note, rows in notes.items()}})
    floor = np.r_[np.full(guitar_state.HARMONICS, 2.4), 2.0]
    scales = {note: np.maximum(floor, np.median(values, axis=0)).tolist() for note, values in noise.items()}
    return {"schema": SCHEMA, "guitar": guitar, "knobs": knobs, "setups": setups, "scales": scales,
            "example_count": count, "source_ids": sorted(source_ids)}


def _validate(profile):
    try:
        knobs, setups, scales = profile["knobs"], profile["setups"], profile["scales"]
        if (profile["schema"] != SCHEMA or not profile["guitar"].strip()
                or not 1 <= len(knobs) <= 4 or len(knobs) != len(set(knobs)) or not setups or not scales):
            raise ValueError
        for values in scales.values():
            scale = np.asarray(values, dtype=float)
            if scale.shape != (SIGNATURE_SIZE,) or not np.isfinite(scale).all() or np.min(scale) <= 0:
                raise ValueError
        for setup in setups:
            _configuration({"configuration": {"pickup": setup["pickup"], "wiring": setup["wiring"],
                                               **dict.fromkeys(setup["knobs"], 0)}})
            if not set(setup["knobs"]) <= set(knobs) or not setup["notes"]:
                raise ValueError
            for note, anchors in setup["notes"].items():
                positions = np.asarray(anchors["positions"], dtype=float)
                centers = np.asarray(anchors["centers"], dtype=float)
                if (note not in scales or positions.shape != (len(centers), len(setup["knobs"]))
                        or centers.shape != (len(centers), SIGNATURE_SIZE) or not len(centers)
                        or not np.isfinite(centers).all() or not np.isfinite(positions).all()
                        or positions.min() < 0 or positions.max() > 10):
                    raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ValueError("This guitar configuration profile could not be read.") from error
    return profile


def save_profile(path, profile):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    session.write_json(path, _validate(profile))
    return path


def load_profile(path):
    return _validate(json.loads(Path(path).read_text(encoding="utf-8")))


def _grid(points):
    low, high = points.min(axis=0), points.max(axis=0)
    varying = high - low > 1e-6
    axes = [np.linspace(first, last, math.ceil((last - first) / (.5 if varying.sum() <= 2 else 1.5)) + 1)
            for first, last in zip(low, high)]
    grid = np.asarray(list(product(*axes)))
    return np.unique(np.vstack([grid, points]), axis=0)


def _interpolate(anchors, queries):
    """Predict signatures within the measured label hull only."""
    points, centers = np.asarray(anchors["positions"]), np.asarray(anchors["centers"])
    varying = np.ptp(points, axis=0) > 1e-6
    fixed_match = np.all(np.isclose(queries[:, ~varying], points[0, ~varying]), axis=1)
    answer = np.full((len(queries), SIGNATURE_SIZE), np.nan)
    samples, requested = points[:, varying], queries[fixed_match][:, varying]
    dimension = int(varying.sum())
    if dimension == 1:
        order = np.argsort(samples[:, 0])
        answer[fixed_match] = np.column_stack([np.interp(requested[:, 0], samples[order, 0], column[order],
                                                        left=np.nan, right=np.nan) for column in centers.T])
    elif dimension > 1 and np.linalg.matrix_rank(samples - samples[0]) == dimension:
        try:
            answer[fixed_match] = LinearNDInterpolator(samples, centers)(requested)
        except QhullError:
            pass
    # Keep exact anchors, including fixed or physically confounded controls.
    for point, center in zip(points, centers):
        answer[np.all(np.isclose(queries, point), axis=1)] = center
    return answer


def _ranked(setup, observations, scales):
    anchors = [setup["notes"][note] for note in observations]
    points = np.unique(np.concatenate([record["positions"] for record in anchors]), axis=0)
    queries = _grid(points)
    distance = np.zeros(len(queries))
    for note, (center, _) in observations.items():
        # A noisy check cannot manufacture a close match by inflating its own radius.
        scale = np.asarray(scales[note])
        differences = (_interpolate(setup["notes"][note], queries) - center) / scale
        distance += _squared_distance(differences)
    span = np.ptp(points, axis=0)
    independent = (dict(zip(setup["knobs"], span >= .5))
                   if np.linalg.matrix_rank(points - points[0], tol=.15) == np.count_nonzero(span > .15)
                   else {})
    return queries, np.sqrt(distance / len(observations)), independent


def _squared_distance(differences):
    return np.mean(differences[..., :-1] ** 2, axis=-1) + LEVEL_WEIGHT * differences[..., -1] ** 2


def _categorical(plausible, setups, key):
    plausible_setups = [setup for setup, *_ in plausible]
    selected = {setup[key] for setup in plausible_setups}
    if len(selected) != 1:
        return None
    other = "wiring" if key == "pickup" else "pickup"
    # A pickup and a wiring switch that always moved together cannot validate one another.
    if not all(any(reference[other] == setup[other] and reference[key] != setup[key] for reference in setups)
               for setup in plausible_setups):
        return None
    return next(iter(selected))


def _ranges(knobs, plausible, best_setup, best_point):
    ranges = {}
    for key in knobs:
        if not all(independent.get(key) for _, _, independent in plausible):
            ranges[key] = None
            continue
        choices = np.concatenate([points[:, setup["knobs"].index(key)] for setup, points, _ in plausible])
        low, high = float(choices.min()), float(choices.max())
        if high - low > 5:
            ranges[key] = None
            continue
        value = float(best_point[best_setup["knobs"].index(key)])
        ranges[key] = {"value": round(value, 1), "low": round(low, 1), "high": round(high, 1)}
    return ranges


def estimate(profile, dry, *, start=None, end=None, source_id=None):
    """Return plausible settings in the taught ranges, or abstain when cues overlap."""
    if source_id and source_id in profile.get("source_ids", ()):
        raise ValueError("Choose a fresh performance. This recording was used for calibration.")
    knobs, setups = profile["knobs"], profile["setups"]
    result = {"pickup": None, "wiring": None, "tone": None, "volume": None, **dict.fromkeys(knobs),
              "notes_used": [], "confidence": "uncertain"}
    observed = _signatures(dry, start, end)
    if not observed:
        return {**result, "reason": "No sustained single notes detected."}
    common = {note: features for note, features in observed.items() if all(note in row["notes"] for row in setups)}
    if len(common) * 2 < len(observed):
        return {**result, "reason": "Play the same notes you taught across the pickup settings."}
    result["notes_used"] = sorted(map(int, common))
    scales = profile["scales"]
    if any(_squared_distance(spread / scales[note]) > 1.9 ** 2 for note, (_, spread) in common.items()):
        return {**result, "reason": "The notes changed too much within the clip. Hold one configuration and pick steadily."}
    ranked = [(row, *_ranked(row, common, scales)) for row in setups]
    ranked = [(row, points, scores, independent) for row, points, scores, independent in ranked if np.isfinite(scores).any()]
    if len(ranked) < len(setups):
        return {**result, "reason": "The calibration has no shared knob settings for these notes."}
    best_setup, points, scores, _ = min(ranked, key=lambda row: np.nanmin(row[2]))
    best_index = int(np.nanargmin(scores))
    best = float(scores[best_index])
    if best > MAX_DISTANCE:
        return {**result, "reason": "The dry signal falls outside the learned examples."}
    plausible = [(row, candidates[distances <= best + NEAR_BEST], independent)
                 for row, candidates, distances, independent in ranked if np.any(distances <= best + NEAR_BEST)]
    result.update({key: _categorical(plausible, setups, key) for key in CATEGORY_KEYS})
    ranges = _ranges(knobs, plausible, best_setup, points[best_index])
    result.update(ranges)
    if result["pickup"] is None and result["wiring"] is None and not any(ranges.values()):
        return {**result, "reason": "Several settings explain this clip. Teach each control at more distinct combinations."}
    complete = all(ranges.values()) and result["pickup"] is not None
    confidence = "strong" if best < .55 and complete else "good" if best < 1.25 else "tentative"
    return {**result, "confidence": confidence, "reason": "Estimate from the taught range. Picking and input gain affect the result."}
