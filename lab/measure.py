"""One measurement of a piece of audio, used everywhere the app judges sound.

`measure` gives level, loudness, peaks, an absolute third-octave spectrum,
brightness and stereo width. Channels are added as power, never as
waveforms, so two channels in opposite phase are measured, not cancelled.

`heard` says what a chain did to a phrase in the words the designer and the
player use: how driven, how bright, how much space. It needs the chain's
return at the phrase's own level, the same 12 dB quieter, and what rang on
after the phrase ended. A chain that only follows the playing returns the
quieter pass 12 dB quieter; a saturated one returns it almost as loud. That
measure does not depend on the phrase, on equalization, or on delay and
reverb. Its thresholds were set from chains measured on the unit
(TONE_KNOWLEDGE.md, "Listening to a draft").

`match` plans one common loudness for several sounds, only ever cutting
unless a lift was asked for, and never past a peak ceiling.
"""

from dataclasses import asdict, dataclass
import math

import numpy as np
import pyloudnorm
from scipy import signal

from .audio import RATE, db

CENTRES = tuple(round(1000 * 2 ** (step / 3), 1) for step in range(-13, 12))   # 50 Hz to 12.7 kHz
STEP_DB = 12.
SOFTER = 10 ** (-STEP_DB / 20)
EARLY, LATE = (.04, .40), (.40, 1.5)     # seconds after the phrase stops
DRIVE = ((.82, "saturated"), (.62, "overdriven"), (.40, "crunch"), (.18, "edge of breakup"), (0., "clean"))
PEAKS_INTACT = 15.     # dB of peak over average: above this a chain squeezes level without clipping
BRIGHTNESS = ((.45, "bright"), (-.25, "balanced"), (-math.inf, "dark"))
AMBIENCE = ((-22., "wet"), (-34., "ambient"), (-42., "a touch of room"), (-math.inf, "dry"))
WORDS = {"drive": tuple(word for _, word in DRIVE[::-1]),
         "tone": tuple(word for _, word in BRIGHTNESS[::-1]),
         "space": tuple(word for _, word in AMBIENCE[::-1])}


def _channels(samples):
    samples = np.asarray(samples, dtype=np.float64)
    return samples[:, None] if samples.ndim == 1 else samples


def level(samples):
    """RMS level in dBFS of all channels together."""
    return db(float(np.sqrt(np.mean(np.square(_channels(samples))))) + 1e-12)


@dataclass(frozen=True)
class Measurement:
    seconds: float
    rms_db: float
    peak_db: float
    true_peak_db: float
    crest_db: float
    lufs: float | None
    bands_db: tuple       # absolute level in each third-octave band of CENTRES, channels added as power
    centroid_hz: float
    width: float          # 0 for mono, 1 for two unrelated channels
    clipped: int

    def to_record(self):
        return {key: (list(value) if isinstance(value, tuple) else value) for key, value in asdict(self).items()}


def measure(samples):
    sides = _channels(samples)
    peak = float(np.max(np.abs(sides))) if sides.size else 0.
    upsampled = signal.resample_poly(sides, 4, 1, axis=0) if len(sides) > 64 else sides
    true_peak = float(np.max(np.abs(upsampled))) if upsampled.size else 0.
    try:
        loudness = float(pyloudnorm.Meter(RATE).integrated_loudness(sides if sides.shape[1] > 1 else sides[:, 0]))
    except ValueError:
        loudness = float("-inf")
    freq, power = signal.welch(sides, RATE, nperseg=min(8192, max(256, len(sides))), axis=0)
    total = power.sum(axis=1)
    df = float(freq[1] - freq[0]) if len(freq) > 1 else 1.
    bands = []
    for centre in CENTRES:
        low, high = centre / 2 ** (1 / 6), centre * 2 ** (1 / 6)
        bands.append(round(10 * math.log10(float(total[(freq >= low) & (freq < high)].sum()) * df + 1e-20), 2))
    audible = (freq >= 60) & (freq <= 12000)
    centroid = float((freq[audible] * total[audible]).sum() / max(float(total[audible].sum()), 1e-30))
    width = 0.
    if sides.shape[1] == 2:
        left, right = sides[:, 0], sides[:, 1]
        scale = math.sqrt(float(np.dot(left, left) * np.dot(right, right)))
        width = 1 - abs(float(np.dot(left, right)) / scale) if scale else 0.
    rms = level(sides)
    return Measurement(len(sides) / RATE, rms, db(peak), db(true_peak), db(peak) - rms,
                       loudness if math.isfinite(loudness) else None, tuple(bands), centroid, width,
                       int(np.count_nonzero(np.abs(sides) >= .999)))


def gap(first, second):
    """Second minus first, per third-octave band, with the difference in overall level removed."""
    a, b = np.array(first.bands_db), np.array(second.bands_db)
    heard = (a > a.max() - 60) & (b > b.max() - 60)
    shift = b - a
    shift = shift - (shift[heard].mean() if heard.any() else 0.)
    return tuple(round(float(value), 2) if audible else 0. for value, audible in zip(shift, heard))


@dataclass(frozen=True)
class Heard:
    squash: float       # 0 when the output follows the playing level exactly, 1 when it does not follow it at all
    crest_db: float
    brightness: float   # octaves the spectral centre sits above the dry phrase's
    low_end_db: float   # share of energy below 200 Hz against the dry phrase's
    ambience_db: float  # what still sounds after the phrase stops, against the phrase
    level_db: float

    @property
    def drive(self):
        word = next(word for floor, word in DRIVE if self.squash >= floor)
        if self.crest_db >= PEAKS_INTACT and word not in ("clean", "edge of breakup"):
            return "edge of breakup"
        return word

    @property
    def tone(self):
        return next(word for floor, word in BRIGHTNESS if self.brightness >= floor)

    @property
    def space(self):
        return next(word for floor, word in AMBIENCE if self.ambience_db >= floor)

    def words(self):
        return {"drive": self.drive, "tone": self.tone, "space": self.space}

    def said(self):
        return f"{self.drive}, {self.tone}, {self.space}"

    def to_record(self):
        return {**{key: round(value, 3) for key, value in asdict(self).items()}, **self.words()}


def _lows(measured):
    bands = np.array(measured.bands_db)
    total = 10 * np.log10(np.sum(10 ** (bands / 10)) + 1e-30)
    lows = 10 * np.log10(np.sum(10 ** (bands[np.array(CENTRES) < 200] / 10)) + 1e-30)
    return lows - total


def heard(dry, loud, soft, tail):
    """What a chain did to a phrase. `tail` starts where the phrase stopped."""
    loud_m, dry_m = measure(loud), measure(dry)
    step = level(loud) - level(soft)
    tail = _channels(tail)
    windows = [tail[round(start * RATE):round(end * RATE)] for start, end in (EARLY, LATE)]
    rings = max((level(window) for window in windows if len(window)), default=-200.)
    sides = _channels(loud)
    loudest = sides[:, int(np.argmax(np.mean(np.square(sides), axis=0)))]
    crest = db(float(np.max(np.abs(loudest)))) - level(loudest)
    return Heard(squash=float(min(1., max(0., 1 - step / STEP_DB))), crest_db=crest,
                 brightness=float(np.log2(max(loud_m.centroid_hz, 1.) / max(dry_m.centroid_hz, 1.))),
                 low_end_db=float(_lows(loud_m) - _lows(dry_m)), ambience_db=rings - level(loud),
                 level_db=level(loud))


def match(measurements, *, target_lufs=-23., offset_db=0., ceiling_db=-3., max_boost_db=0.):
    """Trims, in dB, that bring every sound to one loudness.

    The common loudness is the quietest sound's, or the target if lower, and
    never so high that a sound's true peak would pass the ceiling. A lift
    above it is made only when asked for, by at most `max_boost_db` and 6 dB.
    """
    if not measurements:
        return []
    lufs = [found.lufs for found in measurements]
    if any(value is None for value in lufs):
        raise ValueError("A sound was too quiet to measure.")
    headroom = min(value + ceiling_db - found.true_peak_db for value, found in zip(lufs, measurements))
    reference = min(target_lufs, min(lufs), headroom)
    lift = max(0., min(offset_db, max_boost_db, 6.))
    target = min(reference + offset_db, headroom, reference + lift if offset_db > 0 else reference + offset_db)
    return [target - value for value in lufs]
