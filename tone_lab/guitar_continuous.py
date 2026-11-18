"""Observe audible guitar changes from bounded rolling dry audio."""

from dataclasses import dataclass
import math

import numpy as np
from scipy import signal

from . import audio, guitar_configuration, guitar_state

WINDOW_SECONDS = 3.0
ESTIMATE_SECONDS = 1.15
ESTIMATE_INTERVAL = .50
ACTIVITY_SECONDS = .28
NOTE_SIDE = .36
SEPARATION = .085
CHANGE_RECENCY = 1.05
MIN_SHIFT = 2.55
MIN_UNLABELED_SHIFT = 3.05
CHANGE_COOLDOWN = .80


@dataclass(frozen=True)
class _Frame:
    at: float
    note: int
    signature: np.ndarray


@dataclass(frozen=True)
class _Shift:
    at: float
    strength: float
    note: int
    before: list[_Frame]
    after: list[_Frame]
    before_level: float
    after_level: float


def _estimate(profile, frames):
    if profile is None:
        return None
    summary = guitar_configuration._summarize((frame.note, frame.signature) for frame in frames)
    return guitar_configuration._estimate_from_signatures(profile, summary) if summary else None


def _level(samples):
    return 20 * np.log10(np.maximum(1e-8, np.sqrt(np.mean(samples * samples, axis=-1))))


def _attack_or_gap(samples, offset, before, after):
    """Check for the short attack transient or gap that can accompany a new pluck."""
    half = round(.144 * audio.RATE)
    edge = round(offset * audio.RATE)
    window = samples[edge - half:edge + half]
    block = round(.014 * audio.RATE)
    if len(window) < 3 * block:
        return True
    blocks = np.lib.stride_tricks.sliding_window_view(window, block)[::round(.004 * audio.RATE)]
    levels = _level(blocks)
    high = max(before, after)
    low = min(before, after)
    return np.max(levels) > high + 4.5 or np.min(levels) < low - 11.0


def _continuous_vibration(samples, offset):
    """Check that both sides agree on the pitch and approximate fundamental phase."""
    edge = round(offset * audio.RATE)
    inner, outer = round(.034 * audio.RATE), round(.108 * audio.RATE)
    segments = samples[edge - outer:edge - inner], samples[edge + inner:edge + outer]
    pitches = [guitar_state._pitch(signal.resample_poly(segment, 1, 4)) for segment in segments]
    if None in pitches or max(pitches) / min(pitches) > 1.015:
        return False
    pitch = sum(pitches) / 2
    periodic = []
    for start, segment in zip((-outer, inner), segments):
        t = (start + np.arange(len(segment))) / audio.RATE
        periodic.append(np.dot(segment * np.hanning(len(segment)), np.exp(-2j * np.pi * pitch * t)))
    return abs(np.angle(periodic[1] * periodic[0].conjugate())) <= 1.35


def _compare(frames, split, scales):
    around = [frame for frame in frames if split - NOTE_SIDE <= frame.at <= split + NOTE_SIDE]
    if len({frame.note for frame in around}) != 1:
        return None
    before = [frame for frame in around if frame.at <= split - SEPARATION]
    after = [frame for frame in around if frame.at >= split + SEPARATION]
    if (len(before) < 3 or len(after) < 3 or split - before[-1].at > .165 or after[0].at - split > .165):
        return None
    note = before[0].note
    scale = np.maximum(np.asarray(scales.get(str(note), np.zeros(guitar_configuration.SIGNATURE_SIZE)))[:-1], 2.5)
    left, right = (np.array([frame.signature for frame in side]) for side in (before, after))
    left_center, right_center = np.median(left, axis=0), np.median(right, axis=0)
    # Compare harmonic shapes without absolute level. Harder picking can mimic a volume change.
    delta = (right_center[:-1] - left_center[:-1]) / scale
    shift = float(np.sqrt(np.mean(delta ** 2)))
    variation = max(np.sqrt(np.mean(((values[:, :-1] - center[:-1]) / scale) ** 2))
                    for values, center in ((left, left_center), (right, right_center)))
    if shift < max(MIN_SHIFT, 2.9 * variation):
        return None

    # A fast step can be separated from the natural gradual decay of upper harmonics.
    power = np.dot(delta, delta)
    projection = [np.dot((frame.signature[:-1] - left_center[:-1]) / scale, delta) / power
                  for frame in around]
    approaching = [frame.at for frame, value in zip(around, projection) if frame.at <= split and value < .2]
    settled = [frame.at for frame, value in zip(around, projection) if frame.at >= split and value > .8]
    if not approaching or not settled or settled[0] - approaching[-1] > .23:
        return None
    boundary = (approaching[-1] + settled[0]) / 2
    return _Shift(boundary, shift, note, before, after, float(left_center[-1]), float(right_center[-1]))


def _supported(before, after, knobs):
    if before is None or after is None:
        return []
    changed = [key for key in guitar_configuration.CATEGORY_KEYS
               if before.get(key) is not None and after.get(key) is not None and before[key] != after[key]]
    for key in knobs:
        a, b = before.get(key), after.get(key)
        if "volume" in key or a is None or b is None:
            continue
        if a["high"] + .5 < b["low"] or b["high"] + .5 < a["low"]:
            changed.append(key)
    return changed


def _settled(previous, current, knobs):
    """Only display primary axes that survive a second look at the live window."""
    if current is None:
        return None
    previous = previous or {}
    output = current.copy()
    for key in guitar_configuration.CATEGORY_KEYS:
        if previous.get(key) != current.get(key):
            output[key] = None
    for key in knobs:
        a, b = previous.get(key), current.get(key)
        if a is None or b is None or max(a["low"], b["low"]) > min(a["high"], b["high"]):
            output[key] = None
    keys = (*guitar_configuration.CATEGORY_KEYS, *knobs)
    output["evidence"] = {key: value for key, value in current["evidence"].items() if output[key] is not None}
    output["notes_used"] = sorted({note for item in output["evidence"].values() for note in item["notes_used"]})
    if not any(output.get(key) is not None for key in keys):
        output["confidence"] = "uncertain"
        if current["evidence"]:
            output["reason"] = "The estimate is still settling."
    return output


class Tracker:
    """Accept successive views of the dry ring; return observations, never commands."""

    def __init__(self, profile=None):
        self.profile = profile
        self.reset()

    def reset(self):
        self.last_audio = float("-inf")
        self.last_estimate = float("-inf")
        self.last_change = float("-inf")
        self.estimate = None
        self.previous_estimate = None

    def set_profile(self, profile):
        self.profile = profile
        self.last_estimate = float("-inf")
        self.estimate = self.previous_estimate = None

    def update(self, dry, *, at):
        """Process the latest samples off the audio callback, using its sample clock."""
        samples = np.asarray(dry, dtype=np.float64)
        if samples.ndim == 2 and samples.shape[1] == 1:
            samples = samples[:, 0]
        if samples.ndim != 1 or not np.isfinite(samples).all() or not math.isfinite(at):
            raise ValueError("Continuous guitar analysis requires finite mono dry audio and a timestamp.")
        if at < self.last_audio:
            self.reset()
        new_audio = at > self.last_audio
        self.last_audio = at
        samples = samples[-round(WINDOW_SECONDS * audio.RATE):]
        start = at - len(samples) / audio.RATE
        analysis = samples[-round((CHANGE_RECENCY + NOTE_SIDE + guitar_state.FRAME / audio.RATE) * audio.RATE):]
        analysis_start = at - len(analysis) / audio.RATE
        note_readings = guitar_state._observations(analysis, "configuration", include_position=True)
        frames = [_Frame(analysis_start + (offset + guitar_state.FRAME / 2) / audio.RATE, note, signature)
                  for offset, note, signature in note_readings]
        current = [frame for frame in frames if frame.at >= at - ACTIVITY_SECONDS]
        if not current:
            self.estimate = self.previous_estimate = None
            self.last_estimate = float("-inf")
            tail = samples[-round(ACTIVITY_SECONDS * audio.RATE):]
            quiet = not len(tail) or _level(tail) < -64
            return {"mode": "silent" if quiet else "listening", "estimate": None, "change": None,
                    "reason": "Waiting for dry guitar." if quiet else "Listening for a steady note."}

        if self.profile is not None and at - self.last_estimate >= ESTIMATE_INTERVAL:
            recent = [frame for frame in frames if frame.at >= at - ESTIMATE_SECONDS]
            current_estimate = _estimate(self.profile, recent)
            self.estimate = _settled(self.previous_estimate, current_estimate, self.profile["knobs"])
            self.previous_estimate = current_estimate
            self.last_estimate = at
        change = self._change(samples, frames, start, at) if new_audio else None
        if change:
            self.last_change = change["at"]
            if change["confirmation"] == "supported":
                self.estimate = self.previous_estimate = change["after"]
            return {"mode": "change", "estimate": self.estimate, "change": change,
                    "reason": "The dry signal supports a change between learned settings." if change["confirmation"] == "supported"
                    else "Possible audible change on a sustained note."}
        return {"mode": "estimating" if self.estimate else "listening", "estimate": self.estimate, "change": None,
                "reason": self.estimate["reason"] if self.estimate else "Listening for a repeatable change."}

    def _change(self, samples, frames, start, now):
        scales = self.profile["scales"] if self.profile is not None else {}
        best = None
        for frame in frames:
            split = frame.at
            if not max(start + NOTE_SIDE, now - CHANGE_RECENCY, self.last_change + CHANGE_COOLDOWN) <= split <= now - .28:
                continue
            candidate = _compare(frames, split, scales)
            if not candidate or (best and candidate.strength <= best.strength):
                continue
            boundary = candidate.at
            if boundary <= self.last_change + CHANGE_COOLDOWN or now - boundary < .32:
                continue
            if (_attack_or_gap(samples, boundary - start, candidate.before_level, candidate.after_level)
                    or not _continuous_vibration(samples, boundary - start)):
                continue
            best = candidate
        if best is None:
            return None

        split = best.at
        pre = _estimate(self.profile, best.before)
        post = _estimate(self.profile, best.after)
        changed = _supported(pre, post, self.profile["knobs"]) if self.profile is not None else []
        if not changed:
            # A large simultaneous level jump can come from a fresh pick or a mute.
            if best.strength < MIN_UNLABELED_SHIFT or abs(best.after_level - best.before_level) > 5.5:
                return None
        first, last = max(start, split - 1.25), min(now, split + 1.0)
        pre_seconds, post_seconds = split - first, last - split
        return {"at": split, "start": first, "end": last,
                "pre_seconds": pre_seconds, "post_seconds": post_seconds,
                "complete": pre_seconds >= .70 and post_seconds >= .32,
                "confirmation": "supported" if changed else "possible", "kind": "spectrum",
                "note": best.note, "before": pre, "after": post, "changed": changed}
