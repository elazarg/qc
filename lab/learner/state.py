"""Tell one guitar control's setting from mono dry audio, after being taught examples of each setting.

`fit(guitar, control, examples)` makes a profile from labelled dry excerpts recorded with the other controls
held fixed. `estimate(profile, dry)` names the taught setting a clip is closest to, or none, with a closeness
between 0 and 1 (a heuristic distance to what was taught, not a probability of being right). `track` does that
window by window, at the cadence live listening uses (`WINDOW_SECONDS`, `HOP_SECONDS`), so a rehearsal on a
recorded take sees what listening live would.

What is read for each control: for pickup and tone, the shape of the harmonics of each note; for volume,
the level of the dry signal, which picking strength and input gain change as much as the knob does.
"""

from collections import Counter, defaultdict
import math
import os

import numpy as np
from scipy import signal

from lab.hearing import audio
from lab.learner.profiles import CONTROLS, HARMONICS, SCHEMA

MIN_SECONDS = .24
WINDOW_SECONDS = .48        # what one estimate is made from, live and in a rehearsal
HOP_SECONDS = .14           # how often an estimate is made
PITCH_RATE = audio.RATE // 4
FRAME = round(.12 * audio.RATE)
HOP = FRAME // 2
MIN_RMS = 10 ** (-70 / 20)
WINDOW = signal.windows.hann(FRAME, sym=False)
FFT_SIZE = 8192
MIN_LAG, MAX_LAG = math.ceil(PITCH_RATE / 1700), math.floor(PITCH_RATE / 55)


def _samples(dry, start=None, end=None):
    if isinstance(dry, (str, os.PathLike)):
        dry = audio.read_wave(dry)
    values = np.asarray(dry, dtype=np.float64)
    if values.ndim == 2 and values.shape[1] == 1:
        values = values[:, 0]
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError("Guitar-state estimation requires finite mono dry audio.")
    length = len(values) / audio.RATE
    first = 0.0 if start is None else float(start)
    last = length if end is None else float(end)
    if not 0 <= first < last <= length + .5 / audio.RATE:
        raise ValueError("Choose a start and end within the dry recording.")
    return values[round(first * audio.RATE):round(last * audio.RATE)]


def _pitch(frame):
    frame = frame - frame.mean()
    energy = np.r_[0, np.cumsum(frame * frame)]
    if energy[-1] <= 1e-12:
        return None
    lags = np.arange(MAX_LAG + 2)
    corr = signal.correlate(frame, frame, mode="full", method="fft")[len(frame)-1:len(frame)+len(lags)-1]
    norm = np.sqrt(energy[len(frame) - lags] * (energy[-1] - energy[lags]))
    corr /= np.maximum(norm, 1e-12)
    peaks = signal.find_peaks(corr)[0]
    peaks = peaks[(peaks >= MIN_LAG) & (peaks <= MAX_LAG)]
    if not len(peaks):
        return None
    best = float(np.max(corr[peaks]))
    if best < .77:
        return None
    lag = int(peaks[corr[peaks] >= max(.77, best - .06)][0])
    left, mid, right = corr[lag - 1:lag + 2]
    offset = .5 * (left - right) / (left - 2 * mid + right) if left - 2 * mid + right else 0
    return PITCH_RATE / (lag + np.clip(offset, -.5, .5))


def _observations(dry, control, *, include_position=False):
    if len(dry) < FRAME:
        return []
    reduced = signal.resample_poly(dry, 1, 4)
    observations = []
    for start in range(0, len(dry) - FRAME + 1, HOP):
        samples = dry[start:start + FRAME]
        samples = samples - samples.mean()
        rms = float(np.sqrt(np.mean(samples * samples)))
        if rms < MIN_RMS or np.max(np.abs(dry[start:start + FRAME])) >= .999:
            continue
        frequency = _pitch(reduced[start // 4:(start + FRAME) // 4])
        if frequency is None:
            continue
        midi = 69 + 12 * math.log2(frequency / 440)
        note = round(midi)
        if abs(note - midi) > .4:
            continue
        if control == "volume":
            values = np.array([20 * math.log10(rms)])
        else:
            power = np.abs(np.fft.rfft(samples * WINDOW, n=FFT_SIZE)) ** 2
            bins = np.rint(frequency * np.arange(1, HARMONICS + 1) * FFT_SIZE / audio.RATE).astype(int)
            energy = power[bins[:, None] + np.arange(-2, 3)].sum(axis=1)
            if energy.sum() < 1e-10:
                continue
            values = 10 * np.log10(np.clip(energy / energy.sum(), 1e-4, None))
            if control == "configuration":
                values = np.r_[values, 20 * math.log10(rms)]
        observations.append((start, note, values) if include_position else (note, values))
    return observations


def _distance(value, center, scale):
    return np.sqrt(np.mean(((value - center) / scale) ** 2, axis=-1))


def fit(guitar, control, examples):
    """Build one profile from labeled DI windows, recorded with other controls held fixed.

    Each example has label and wav keys (or an in-memory 48 kHz dry array in place
    of wav), plus optional start/end seconds relative to that example.
    """
    guitar = guitar.strip()
    if not guitar or control not in CONTROLS:
        raise ValueError("Choose a guitar name and pickup, tone or volume as the control.")
    labels = {}
    readings = defaultdict(lambda: defaultdict(list))
    for example in examples:
        label = example["label"].strip()
        if not label:
            raise ValueError("Name the setting of every example.")
        label = labels.setdefault(label.casefold(), label)
        clip = _samples(example["wav"], example.get("start"), example.get("end"))
        if len(clip) < round(MIN_SECONDS * audio.RATE):
            raise ValueError(f"Record at least {MIN_SECONDS:.2f} seconds for {label}.")
        for note, values in _observations(clip, control):
            readings[note][label].append(values)
    state_labels = list(labels.values())
    if len(state_labels) < 2:
        raise ValueError("Provide at least two states of the same guitar control.")

    notes = {}
    for note, states in readings.items():
        if not all(len(states[label]) >= 3 for label in state_labels):
            continue
        features = [np.asarray(states[label]) for label in state_labels]
        centers = np.array([np.median(values, axis=0) for values in features])
        errors = np.concatenate([np.abs(values - center) for values, center in zip(features, centers)])
        scale = np.maximum(1.4826 * np.median(errors, axis=0), 1.5 if control == "volume" else 2.0)
        separation = min(float(_distance(a, b, scale))
                         for index, a in enumerate(centers) for b in centers[index + 1:])
        if separation < .85:
            continue
        radii = [max(1.0, float(np.percentile(_distance(values, center, scale), 90)) + .9)
                 for values, center in zip(features, centers)]
        notes[str(note)] = {"centers": centers.tolist(), "scale": scale.tolist(), "radii": radii}

    if not notes:
        raise ValueError("No common, distinguishable notes. Repeat the same sustained single notes at settings farther apart.")
    return {"schema": SCHEMA, "guitar": guitar, "control": control, "labels": state_labels, "notes": notes}


def _estimate(profile, observations):
    labels = profile["labels"]
    votes = Counter()
    closeness = defaultdict(list)
    notes_used = set()
    matched = 0
    for note, value in observations:
        taught = profile["notes"].get(str(note))
        if taught is None:
            continue
        matched += 1
        distances = _distance(value, np.asarray(taught["centers"]), np.asarray(taught["scale"]))
        closest, runner_up = np.argsort(distances)[:2]
        best, second = distances[closest], distances[runner_up]
        margin = (second - best) / max(.5, second)
        radius = taught["radii"][closest]
        if best > radius or margin < .25:
            continue
        label = labels[closest]
        votes[label] += 1
        closeness[label].append(.7 * margin + .3 * max(0.0, 1 - best / radius))
        notes_used.add(note)
    result = {"label": None, "closeness": 0.0, "votes": {label: votes[label] for label in labels},
              "notes_used": sorted(notes_used)}
    if not observations:
        return {**result, "reason": "No stable single note detected."}
    if not matched:
        return {**result, "reason": "No detected note is one that was taught."}
    if matched < math.ceil(len(observations) / 2):
        return {**result, "reason": "Most of the detected notes are not ones that were taught."}
    accepted = sum(votes.values())
    if accepted < max(2, math.ceil(matched / 2)):
        return {**result, "reason": "Not enough frames separated the taught settings."}
    winner, count = votes.most_common(1)[0]
    share = count / accepted
    if share < .67:
        return {**result, "reason": "The clip contains mixed or ambiguous settings."}
    score = float(np.median(closeness[winner])) * share
    return {**result, "label": winner, "closeness": round(score, 3), "reason": "Matches the taught examples."}


def estimate(profile, dry, *, start=None, end=None):
    """The taught setting the clip is closest to, with how close (0 to 1: a heuristic, not a probability)."""
    clip = _samples(dry, start, end)
    if len(clip) < round(MIN_SECONDS * audio.RATE):
        raise ValueError(f"Use at least {MIN_SECONDS:.2f} seconds of dry guitar.")
    return _estimate(profile, _observations(clip, profile["control"]))


def track(profile, dry, *, window=WINDOW_SECONDS, hop=HOP_SECONDS):
    """Return window-end times and estimates, including abstentions during silence."""
    if (not math.isfinite(window) or not math.isfinite(hop) or window < MIN_SECONDS
            or not .02 <= hop <= window):
        raise ValueError(f"Use a window of at least {MIN_SECONDS:.2f} seconds and a valid hop.")
    clips = _samples(dry)
    size = round(window * audio.RATE)
    step = round(hop * audio.RATE)
    return [{"time": round(end / audio.RATE, 4), **estimate(profile, clips[end-size:end])}
            for end in range(size, len(clips) + 1, step)]
