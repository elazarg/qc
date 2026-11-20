"""Offline test of whether a small EQ correction can offset a rolled-back tone pot.

`compare(reference, rolled)` takes two mono dry clips of the same playing, open
tone and rolled back, fits a high shelf on the first half of each, and keeps
it only if it brings the second halves closer. `high_shelf(center, gain)` is
that filter's (b, a) coefficients.
"""

import math

import numpy as np
from scipy import signal

from lab.hearing import audio

CENTERS = np.array([280, 400, 560, 800, 1120, 1600, 2240, 3200, 4480, 6400])


def _band_levels(samples):
    frequencies, power = signal.welch(samples[:, 0], audio.RATE, nperseg=4096)
    bands = np.array([power[(frequencies >= c / 1.17) & (frequencies < c * 1.17)].sum()
                      for c in CENTERS])
    return 10 * np.log10(np.maximum(bands, 1e-15))


def _mismatch(a, b):
    # Ignore bands that the open-tone performance hardly excited.
    valid = a > max(a.max() - 32, -115)
    if valid.sum() < 4 or not any(valid[CENTERS >= 1600]):
        raise ValueError("Record a clearer example with enough upper harmonics to compare.")
    diff = (a - b)[valid]
    return float(np.sqrt(np.mean(np.square(diff - np.median(diff)))))


def high_shelf(center, gain):
    amplitude = 10 ** (gain / 40)
    angle = 2 * math.pi * center / audio.RATE
    cosine = math.cos(angle)
    root = math.sqrt(2 * amplitude) * math.sin(angle)
    b = amplitude * np.array([
        amplitude + 1 + (amplitude - 1) * cosine + root,
        -2 * (amplitude - 1 + (amplitude + 1) * cosine),
        amplitude + 1 + (amplitude - 1) * cosine - root,
    ])
    a = np.array([
        amplitude + 1 - (amplitude - 1) * cosine + root,
        2 * (amplitude - 1 - (amplitude + 1) * cosine),
        amplitude + 1 - (amplitude - 1) * cosine - root,
    ])
    return b, a


def compare(reference, rolled):
    if any(x.ndim != 2 or x.shape[1] != 1 or len(x) < 2 * audio.RATE
           or not np.isfinite(x).all() or np.max(np.abs(x)) >= .999 for x in (reference, rolled)):
        raise ValueError("Use two unclipped mono dry clips, at least two seconds each.")
    ref_fit, ref_held = np.array_split(reference, 2)
    rolled_fit, rolled_held = np.array_split(rolled, 2)
    target = _band_levels(ref_fit)
    settings = [(1800, 0)] + [(center, gain) for center in (900, 1400, 2000, 3000) for gain in (1, 2, 3, 4, 5, 6)]

    def score(params):
        b, a = high_shelf(*params)
        return _mismatch(target, _band_levels(signal.lfilter(b, a, rolled_fit, axis=0)))

    center, gain = min(settings, key=score)
    b, a = high_shelf(center, gain)
    held = _band_levels(ref_held)
    before = _mismatch(held, _band_levels(rolled_held))
    after = _mismatch(held, _band_levels(signal.lfilter(b, a, rolled_held, axis=0)))
    accepted = gain != 0 and before - after >= .2
    output = signal.lfilter(b, a, rolled, axis=0).astype(np.float32) if accepted else rolled.copy()
    peak = float(np.max(np.abs(output)))
    if peak > .95:
        output *= .95 / peak
    return output, {"center_hz": center, "fitted_gain_db": gain, "enabled": accepted,
                    "held_out_error_before_db": round(before, 2), "held_out_error_after_db": round(after, 2)}
