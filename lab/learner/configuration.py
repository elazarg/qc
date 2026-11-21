"""Tell a guitar's pickup setting and roughly where its knobs are from dry audio, after labelled examples.

`fit(guitar, examples)` keeps, for each taught pickup setting and each note, the harmonic shape and level at
every knob position taught. `estimate(profile, dry)` compares a clip with those, note by note, and names a
control only where the examples can tell it apart; otherwise it says why not.

What the answer means: "match" is how close the clip is to the nearest taught example ("close", "near",
"far", or "none"): a distance, not a chance of being right. A knob comes back as the range of taught
positions about as close as the best. Volume is read mostly from the level of the dry signal, so it holds
only for picking as hard as in the examples, at the same input gain.
"""

from collections import defaultdict
from itertools import combinations, product
import math

import numpy as np
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import QhullError

from lab.learner import state as guitar_state
from lab.learner.profiles import CATEGORY_KEYS, SCHEMA, SIGNATURE_SIZE, setup

METADATA_KEYS = {"wav", "start", "end", "source_id", "role", "id"}
LEVEL_WEIGHT = .4
MAX_DISTANCE = 2.1
NEAR_BEST = .66
CLOSE, NEAR = .55, 1.25         # distances under which a match is called close, and near
MIN_NOTE_COVERAGE = .35


def _configuration(example):
    config = example.get("configuration")
    if config is None:
        config = {key: value for key, value in example.items() if key not in METADATA_KEYS}
    return setup(config)


def _signatures(dry, start=None, end=None):
    observations = guitar_state._observations(guitar_state._samples(dry, start, end), "configuration")
    return _summarize(observations)


def _summarize(observations):
    grouped = defaultdict(list)
    for note, features in observations:
        grouped[str(note)].append(features)
    signatures = {}
    for note, frames in grouped.items():
        if len(frames) >= 3:
            values = np.asarray(frames)
            center = np.median(values, axis=0)
            spread = 1.4826 * np.median(np.abs(values - center), axis=0)
            signatures[note] = center, spread, len(frames)
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


def fit(guitar, examples):
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
            raise ValueError("Keep the checks out of the teaching examples.")
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
        for note, (center, spread, _) in notes.items():
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


def _positions(setup, observations):
    anchors = [setup["notes"][note] for note in observations]
    return np.unique(np.concatenate([record["positions"] for record in anchors]), axis=0)


def _ranked(setup, observations, scales, queries=None):
    points = _positions(setup, observations)
    if queries is None:
        queries = _grid(points)
    distance = np.zeros(len(queries))
    for note, (center, _, _) in observations.items():
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


def _categorical(plausible, comparable, key):
    plausible_setups = [setup for setup, *_ in plausible]
    selected = {setup[key] for setup in plausible_setups}
    if len(selected) != 1:
        return None
    if not all((setup["pickup"], setup["wiring"]) in comparable[key] for setup in plausible_setups):
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


def _coverage(notes, total_frames):
    return sum(frames for _, _, frames in notes.values()) / total_frames


def _overlap(observations, *setups):
    return {note: signature for note, signature in observations.items() if all(note in setup["notes"] for setup in setups)}


def _evidence(notes, total_frames):
    return {"notes_used": sorted(map(int, notes)), "coverage": round(_coverage(notes, total_frames), 3)}


def _nearest(setups, observations, scales, total_frames, min_notes):
    nearest, best = None, MAX_DISTANCE
    for setup in setups:
        notes = _overlap(observations, setup)
        if len(notes) < min_notes or _coverage(notes, total_frames) < .5:
            continue
        points, scores, _ = _ranked(setup, notes, scales)
        if not np.isfinite(scores).any():
            continue
        index = int(np.nanargmin(scores))
        distance = scores[index]
        if distance < best:
            best = distance
            nearest = {key: setup[key] for key in CATEGORY_KEYS}
            nearest["settings"] = {key: round(float(value), 1) for key, value in zip(setup["knobs"], points[index])}
            nearest.update(_evidence(notes, total_frames))
    return nearest


def _paired_categories(setups, observations, scales, total_frames, min_notes):
    """Compare each factor using notes shared by setups with the other factor held fixed."""
    excluded = {key: set() for key in CATEGORY_KEYS}
    matches = {key: [] for key in CATEGORY_KEYS}
    comparable = {key: set() for key in CATEGORY_KEYS}
    for (first, a), (second, b) in combinations(enumerate(setups), 2):
        changed = [key for key in CATEGORY_KEYS if a[key] != b[key]]
        if len(changed) != 1:
            continue
        notes = _overlap(observations, a, b)
        if len(notes) < min_notes or _coverage(notes, total_frames) < MIN_NOTE_COVERAGE:
            continue
        same_knobs = a["knobs"] == b["knobs"]
        queries = _grid(np.vstack([_positions(a, notes), _positions(b, notes)])) if same_knobs else None
        first_scores = _ranked(a, notes, scales, queries)[1]
        second_scores = _ranked(b, notes, scales, queries)[1]
        if same_knobs:
            # Compare pickup/wiring where both settings were taught over a common knob range.
            available = np.isfinite(first_scores) & np.isfinite(second_scores)
            first_scores, second_scores = first_scores[available], second_scores[available]
        options = ((first, first_scores), (second, second_scores))
        if not all(np.isfinite(scores).any() for _, scores in options):
            continue
        key = changed[0]
        comparable[key].update({(a["pickup"], a["wiring"]), (b["pickup"], b["wiring"])})
        minima = [(index, float(np.nanmin(scores))) for index, scores in options]
        (winner, closest), (loser, runner_up) = sorted(minima, key=lambda option: option[1])
        if closest > MAX_DISTANCE or runner_up - closest <= NEAR_BEST:
            continue
        excluded[key].add(loser)
        matches[key].append((winner, notes))

    outcome = {}
    for key in CATEGORY_KEYS:
        remaining = {index for index in range(len(setups)) if index not in excluded[key]}
        choices = {setups[index][key] for index in remaining}
        if len(choices) == 1:
            wins = [notes for index, notes in matches[key] if index in remaining]
            if wins:
                strongest = max(wins, key=lambda notes: _coverage(notes, total_frames))
                outcome[key] = next(iter(choices)), _evidence(strongest, total_frames)
    return outcome, comparable


def estimate(profile, dry, *, start=None, end=None, source_id=None):
    """Return plausible settings in the taught ranges, or abstain when cues overlap."""
    if source_id and source_id in profile.get("source_ids", ()):
        raise ValueError("Choose a fresh performance. This recording was used to teach.")
    return _estimate_from_signatures(profile, _signatures(dry, start, end))


def _estimate_from_signatures(profile, observed):
    knobs, setups = profile["knobs"], profile["setups"]
    result = {"pickup": None, "wiring": None, "tone": None, "volume": None, **dict.fromkeys(knobs),
              "notes_used": [], "match": "none", "evidence": {}, "nearest": None}
    if not observed:
        return {**result, "reason": "No sustained single notes detected."}
    scales = profile["scales"]
    total_frames = sum(frames for _, _, frames in observed.values())
    taught = {note: signature for note, signature in observed.items() if note in scales}
    stable = {note: signature for note, signature in taught.items()
              if _squared_distance(signature[1] / scales[note]) <= 1.9 ** 2}
    if _coverage(stable, total_frames) < .5 * _coverage(taught, total_frames):
        return {**result, "reason": "The notes changed too much within the clip. Hold one configuration and pick steadily."}
    min_notes = min(len(observed), 2)
    result["nearest"] = _nearest(setups, stable, scales, total_frames, min_notes)
    paired, comparable = _paired_categories(setups, stable, scales, total_frames, min_notes)
    common = _overlap(stable, *setups)
    combined = _coverage(common, total_frames) >= .5 and len(common) >= min_notes
    ranked = [(row, *_ranked(row, common, scales)) for row in setups] if combined else []
    ranked = [(row, points, scores, independent) for row, points, scores, independent in ranked if np.isfinite(scores).any()]
    best = float("inf")
    if len(ranked) == len(setups):
        best_setup, points, scores, _ = min(ranked, key=lambda row: np.nanmin(row[2]))
        best_index = int(np.nanargmin(scores))
        best = float(scores[best_index])
        if best <= MAX_DISTANCE:
            plausible = [(row, candidates[distances <= best + NEAR_BEST], independent)
                         for row, candidates, distances, independent in ranked if np.any(distances <= best + NEAR_BEST)]
            result.update({key: _categorical(plausible, comparable, key) for key in CATEGORY_KEYS})
            result.update(_ranges(knobs, plausible, best_setup, points[best_index]))
            result["evidence"] = {key: _evidence(common, total_frames) for key in (*CATEGORY_KEYS, *knobs)
                                  if result[key] is not None}

    for key, (value, evidence) in paired.items():
        if result[key] is None:
            result[key], result["evidence"][key] = value, evidence
        elif result[key] != value:
            result[key] = None
            result["evidence"].pop(key, None)

    if result["evidence"]:
        result["notes_used"] = sorted({note for row in result["evidence"].values() for note in row["notes_used"]})
        complete = all(result[key] is not None for key in knobs) and result["pickup"] is not None
        match = "close" if best < CLOSE and complete else "near" if best < NEAR else "far"
        return {**result, "match": match, "reason": "Estimate from comparable notes. Picking and input gain affect the result."}
    if result["nearest"]:
        return {**result, "reason": "The nearest example is inconclusive. Teach the same notes at more distinct settings."}
    if _coverage(stable, total_frames) < .5:
        return {**result, "reason": "Too few notes match the examples. Play the same notes you taught."}
    return {**result, "reason": "The dry signal falls outside the learned examples."}
