"""One measurement of a piece of audio, used everywhere the app judges sound.

`measure` gives level, loudness, peaks, an absolute third-octave spectrum,
brightness and stereo width. Channels are added as power, never as
waveforms, so two channels in opposite phase are measured, not cancelled.

`heard` says what a chain did to a phrase, each value named for what it
measures: compression (the phrase played again 12 dB quieter: a chain that
follows the playing returns it 12 dB quieter, a compressing one less), how
bright and how much low end against the dry phrase, and the tail that rings
on after the phrase ends. Distortion is not inferred from compression: it is
measured with test tones (`lab.probe`), whose record `heard` carries.

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
# Distortion words, from the harmonics a pure tone gains (lab.probe), in dB against the tone. Set from a ladder of
# chains measured on the unit on 2026-11-20 (research/measurements/distortion-ladder.md). Played firmly, a driven
# amp's harmonics level off near -18 to -20 dB, so how far in it is shows in the same tone 12 dB softer: a crunchy
# amp cleans up, a saturated one stays distorted.
CLEAN_BELOW, EDGE_BELOW = -38., -26.             # at the player's level
CRUNCH_SOFT_BELOW, OVERDRIVEN_SOFT_BELOW = -30., -20.5    # 12 dB softer
DISTORTION = ("clean", "edge of breakup", "crunch", "overdriven", "saturated")


def distortion_word(distortion_db, soft_db):
    if distortion_db < CLEAN_BELOW:
        return "clean"
    if distortion_db < EDGE_BELOW:
        return "edge of breakup"
    if soft_db < CRUNCH_SOFT_BELOW:
        return "crunch"
    return "overdriven" if soft_db < OVERDRIVEN_SOFT_BELOW else "saturated"
# Compression: how much of a 12 dB drop in the playing fails to come back (0 none, 1 all).
COMPRESSION = ((.82, "squashed"), (.62, "heavy"), (.40, "moderate"), (.18, "light"), (0., "none"))
BRIGHTNESS = ((.45, "bright"), (-.25, "balanced"), (-math.inf, "dark"))
TAIL = ((-22., "wet"), (-34., "ambient"), (-42., "a touch of room"), (-math.inf, "dry"))
WORDS = {"distortion": DISTORTION,
         "compression": tuple(word for _, word in COMPRESSION[::-1]),
         "brightness": tuple(word for _, word in BRIGHTNESS[::-1]),
         "tail": tuple(word for _, word in TAIL[::-1])}


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
    """What listening found, each value named for what was measured.

    From the phrase: compression (how much of a 12 dB drop in the playing
    came back), crest (peak over average of the return), brightness and low
    end against the dry phrase, the tail (what still sounds after the phrase
    stops) and the level. From the test signals (`lab.probe`): distortion and
    the rest, in `probe`.

    The words are the scales the designer and the player use. Distortion's
    words (clean to saturated) come from the measured harmonics, not from
    compression; where no test signals were played they are None.
    """
    compression: float  # 0 when a 12 dB drop in the playing comes back as 12 dB, 1 when it comes back as 0
    crest_db: float
    brightness_oct: float  # octaves the spectral centre sits above the dry phrase's
    low_end_db: float   # share of energy below 200 Hz against the dry phrase's
    tail_db: float      # what still sounds after the phrase stops, against the phrase
    level_db: float
    probe: dict | None = None

    @property
    def distortion(self):
        if not self.probe:
            return None
        return distortion_word(self.probe["distortion_db"], self.probe["distortion_soft_db"])

    @property
    def compressed(self):
        return next(word for floor, word in COMPRESSION if self.compression >= floor)

    @property
    def tone(self):
        return next(word for floor, word in BRIGHTNESS if self.brightness_oct >= floor)

    @property
    def tail(self):
        return next(word for floor, word in TAIL if self.tail_db >= floor)

    def words(self):
        return {"distortion": self.distortion, "compressed": self.compressed, "brightness": self.tone,
                "tail": self.tail}

    def said(self):
        return summary(self.words())

    def to_record(self):
        values = {key: round(value, 3) for key, value in asdict(self).items() if isinstance(value, float)}
        return {**values, "probe": self.probe, **self.words()}


def summary(found):
    """"crunch, bright, ambient" from heard words or an aim, written with either the current names or the
    earlier ones (drive, tone, space)."""
    if not found:
        return ""
    first = found.get("distortion") or ("" if "distortion" in found else found.get("drive", ""))
    return ", ".join(word for word in (first, found.get("brightness") or found.get("tone", ""),
                                       found.get("tail") or found.get("space", "")) if word)


def measured_lines(record):
    """What listening measured, one line each, named for what was measured, with the word it gives."""
    if not record or "probe" not in record:
        return ["Not measured with the current tests yet; that happens by itself while the unit is free."]
    probe, lines = record["probe"] or {}, []
    if probe:
        lines.append(f"Distortion: harmonics {-probe['distortion_db']:.0f} dB below the tone played firmly, "
                     f"{-probe['distortion_soft_db']:.0f} dB below it 12 dB softer: {record.get('distortion')}.")
        lines.append(f"Intermodulation (two notes together): {-probe['intermod_db']:.0f} dB below the notes.")
    lines.append(f"Compression: of a 12 dB drop in your playing, {12 * (1 - record['compression']):.0f} dB comes back: "
                 f"{record.get('compressed')}.")
    lines.append(f"Brightness: {record['brightness_oct']:+.1f} octave against your dry guitar: {record.get('brightness')}.")
    lines.append(f"Tail: {-record['tail_db']:.0f} dB below the playing still sounds after you stop: {record.get('tail')}.")
    if probe.get("echo_ms"):
        lines.append(f"Echo: a repeat {probe['echo_ms']:.0f} ms after a note, {-probe['echo_db']:.0f} dB down.")
    if probe.get("decay_s", 0) >= .3:
        lines.append(f"Decay: a short sound takes about {probe['decay_s']:.1f} s to die away.")
    if probe.get("level_wobble", 0) >= 10 or probe.get("pitch_wobble", 0) >= 3:
        lines.append(f"Movement on a held note: level {probe['level_wobble']:.0f}%, pitch {probe['pitch_wobble']:.0f} cents.")
    if probe.get("gated"):
        lines.append("Gate: a note 12 dB softer than firm playing did not come through.")
    return lines


def _lows(measured):
    bands = np.array(measured.bands_db)
    total = 10 * np.log10(np.sum(10 ** (bands / 10)) + 1e-30)
    lows = 10 * np.log10(np.sum(10 ** (bands[np.array(CENTRES) < 200] / 10)) + 1e-30)
    return lows - total


def heard(dry, loud, soft, tail, probe=None):
    """What a chain did to a phrase. `tail` starts where the phrase stopped; `probe` is `lab.probe`'s record."""
    loud_m, dry_m = measure(loud), measure(dry)
    step = level(loud) - level(soft)
    tail = _channels(tail)
    windows = [tail[round(start * RATE):round(end * RATE)] for start, end in (EARLY, LATE)]
    rings = max((level(window) for window in windows if len(window)), default=-200.)
    sides = _channels(loud)
    loudest = sides[:, int(np.argmax(np.mean(np.square(sides), axis=0)))]
    crest = db(float(np.max(np.abs(loudest)))) - level(loudest)
    return Heard(compression=float(min(1., max(0., 1 - step / STEP_DB))), crest_db=crest,
                 brightness_oct=float(np.log2(max(loud_m.centroid_hz, 1.) / max(dry_m.centroid_hz, 1.))),
                 low_end_db=float(_lows(loud_m) - _lows(dry_m)), tail_db=rings - level(loud),
                 level_db=level(loud), probe=probe)


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
