"""One measurement of a piece of audio, used everywhere the app judges sound.

`measure` gives level, loudness, peaks, an absolute third-octave spectrum,
brightness and stereo width. Channels are added as power, never as
waveforms, so two channels in opposite phase are measured, not cancelled.

`heard` says what a chain did to a phrase, each value named for what it
measures: compression (the phrase played again 12 dB quieter: a chain that
follows the playing returns it 12 dB quieter, a compressing one less), how
bright and how much low end against the dry phrase, and the tail that rings
on after the phrase ends. Distortion is not inferred from compression: it is
measured with test tones (`lab.hearing.probe`), whose record `heard` carries.

Also from the phrase:

- attack_ms: how fast notes reach full level, as the median time the peak
  envelope (the running peak of the waveform over 12.5 ms, a period of the
  lowest guitar note) takes from 10% to 90% of its rise, from just before a
  note to the note's peak. The notes are found once, in the dry phrase (the
  peak envelope rising 6 dB within 20 ms, within 35 dB of the loudest
  moment), so the loud and soft passes are timed on the same notes. An
  instant onset reads 0 to a few ms (the wave's first peak). attack_soft_ms is the same on the pass
  played 12 dB softer, which shows whether the chain softens or sharpens a
  lighter pick. Attack time is one of the robust correlates of timbre
  (McAdams 2017; Zacharakis et al. 2015; docs/TONE_RESEARCH.md findings 4 and 5).
- brightness_bands_db: each of 1-3, 3-6 and 6-12 kHz's share of the energy
  against the dry phrase's share, in dB, keyed "1-3 kHz", "3-6 kHz",
  "6-12 kHz". Brightness is measured from band energy, never from the spectral
  centre alone, which mis-ranks distorted guitar (docs/TONE_RESEARCH.md finding 3).

Texture (round, warm, even, edged, rough) is an inference, a word from the
probe's harmonics: how much there is (order-weighted distortion), and the
balance between high orders (5th-10th) and low orders (2nd-3rd), with
tristimulus 3 for hard clipping. Its thresholds were set from a ladder of
sixteen chains measured on the unit (research/measurements/distortion-ladder.md);
more chains and other phrases would make them firmer.

`match` plans one common loudness for several sounds, only ever cutting
unless a lift was asked for, and never past a peak ceiling.
"""

from dataclasses import asdict, dataclass
import math

import numpy as np
import pyloudnorm
from scipy import ndimage, signal

from lab.hearing.audio import RATE, db

CENTRES = tuple(round(1000 * 2 ** (step / 3), 1) for step in range(-13, 12))   # 50 Hz to 12.7 kHz
STEP_DB = 12.
SOFTER = 10 ** (-STEP_DB / 20)
EARLY, LATE = (.04, .40), (.40, 1.5)     # seconds after the phrase stops
# Distortion words, from the harmonics a pure tone gains (lab.hearing.probe), in dB against the tone. Set from a ladder of
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


# Texture words, from how the test tone's harmonics are spread (lab.hearing.probe): round, even, edged, rough. Energy in the
# high orders (5th to 10th) is heard as rough far more than the same energy in the 2nd and 3rd (docs/TONE_RESEARCH.md
# findings 2 and 7), so the words follow tristimulus 3 (the share of the power in harmonics 5..10) and the
# order-weighted distortion (Shorter's n^2/4). PROVISIONAL: set from synthetic chains only (tanh and hard clipping
# of a pure tone; a square wave reads tristimulus 3 = 0.06 and weighted 0 dB), not yet calibrated on the unit.
# Calibrate with a ladder of chains measured there, as research/measurements/distortion-ladder.md did for distortion.
# Texture, from the harmonic spread of the test tone. Set from the ladder measured on the unit on 2026-11-20
# (research/measurements/distortion-ladder.md): driven amps through a cab put 0.3 to 0.7 % of the power in the 5th
# to 10th harmonics, far from hard clipping, so the share alone does not tell them apart; the balance between high
# orders (5th-10th) and low orders (2nd-3rd) does, from -6 dB (a Plexi at gain 2 or 5) to +4 dB (a Soldano lead).
ROUND_WEIGHTED_BELOW = -30.     # so few harmonics, weighted by order, that the tone stays round
WARM_BALANCE_BELOW = -3.        # low orders clearly stronger than high ones
EVEN_BALANCE_BELOW = 1.5        # about level
EDGED_BALANCE_BELOW = 4.        # high orders somewhat stronger; above this, rough
ROUGH_TRISTIMULUS3 = .03        # a share of high orders only hard clipping reaches: rough whatever the balance
TEXTURE = ("round", "warm", "even", "edged", "rough")


def texture_word(tristimulus3, weighted_distortion_db, low_order_db=None, high_order_db=None):
    """An inference from measurements of the harmonics: round (few), warm (low orders lead), even, edged, rough
    (high orders lead). Without the order levels (records from before they were measured), only round and rough
    can be told."""
    if tristimulus3 >= ROUGH_TRISTIMULUS3:
        return "rough"
    if weighted_distortion_db < ROUND_WEIGHTED_BELOW:
        return "round"
    if low_order_db is None or high_order_db is None:
        return "even"
    balance = high_order_db - low_order_db
    if balance < WARM_BALANCE_BELOW:
        return "warm"
    if balance < EVEN_BALANCE_BELOW:
        return "even"
    return "edged" if balance < EDGED_BALANCE_BELOW else "rough"


def texture_of(probe):
    return texture_word(probe["tristimulus3"], probe["weighted_distortion_db"], probe.get("low_order_db"),
                        probe.get("high_order_db"))


# Attack: from the running peak of the waveform (PEAK_WINDOW_S covers a period of the lowest guitar note, so a held
# note's peaks do not ripple), sampled every ENVELOPE_HOP_S. An instant onset reads 0 to a few ms (the first peak).
PEAK_WINDOW_S, ENVELOPE_HOP_S = .0125, .0005
ONSET_LOOKBACK_S, ONSET_RISE_DB, ONSET_RANGE_DB, ONSET_GAP_S = .02, 6., 35., .08
ATTACK_SPAN_S = .25                      # a note's peak is looked for this long after its onset
# Brightness by band: each band's share of the energy against the dry phrase's share.
BANDS = {"1-3 kHz": (1000., 3000.), "3-6 kHz": (3000., 6000.), "6-12 kHz": (6000., 12000.)}
# Compression: how much of a 12 dB drop in the playing fails to come back (0 none, 1 all).
COMPRESSION = ((.82, "squashed"), (.62, "heavy"), (.40, "moderate"), (.18, "light"), (0., "none"))
BRIGHTNESS = ((.45, "bright"), (-.25, "balanced"), (-math.inf, "dark"))
TAIL = ((-22., "wet"), (-34., "ambient"), (-42., "a touch of room"), (-math.inf, "dry"))
WORDS = {"distortion": DISTORTION, "texture": TEXTURE,
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
    stops), the level, the attack (loud and soft passes) and the energy by
    band against the dry phrase. From the test signals (`lab.hearing.probe`):
    distortion, the harmonic spread and the rest, in `probe`. Fields added
    later default to None, so records made before them still load.

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
    attack_ms: float | None = None       # median 10%-90% rise of the notes' peak envelope, played as recorded
    attack_soft_ms: float | None = None  # the same, played 12 dB softer
    brightness_bands_db: dict | None = None  # {"1-3 kHz", "3-6 kHz", "6-12 kHz"}: share of energy against the dry's
    loudness_lufs: float | None = None  # integrated loudness of the return (ITU-R BS.1770), for matching A and B

    @property
    def distortion(self):
        if not self.probe:
            return None
        return distortion_word(self.probe["distortion_db"], self.probe["distortion_soft_db"])

    @property
    def texture(self):
        """Round to rough, from the probe's harmonic spread; None without a probe, or with one recorded before
        the spread was measured."""
        if not self.probe or "tristimulus3" not in self.probe:
            return None
        return texture_of(self.probe)

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
                "tail": self.tail, "texture": self.texture}

    def said(self):
        return summary(self.words())

    def to_record(self):
        values = {key: round(value, 3) for key, value in asdict(self).items() if isinstance(value, float)}
        return {**values, "attack_ms": self.attack_ms, "attack_soft_ms": self.attack_soft_ms,
                "brightness_bands_db": dict(self.brightness_bands_db) if self.brightness_bands_db else None,
                "probe": self.probe, **self.words()}


def summary(found):
    """"crunch, bright, ambient" from heard words or an aim, written with either the current names or the
    earlier ones (drive, tone, space)."""
    if not found:
        return ""
    def word(key, earlier):
        # Records from before the renaming used some of these names for numbers (brightness was the octave
        # figure): only a word counts.
        for value in (found.get(key), found.get(earlier)):
            if isinstance(value, str) and value:
                return value
        return ""

    first = word("distortion", "drive") if "distortion" not in found or isinstance(found.get("distortion"), str)         else ""
    return ", ".join(item for item in (first, word("brightness", "tone"), word("tail", "space")) if item)


def measured_lines(record):
    """What listening measured, one line each, named for what was measured, with the word it gives."""
    if not record or "probe" not in record:
        return ["Not measured with the current tests yet; that happens by itself while the unit is free."]
    probe, lines = record["probe"] or {}, []
    if probe:
        lines.append(f"Distortion: harmonics {-probe['distortion_db']:.0f} dB below the tone played firmly, "
                     f"{-probe['distortion_soft_db']:.0f} dB below it 12 dB softer: {record.get('distortion')}.")
        if "low_order_db" in probe:
            lines.append(f"Harmonic orders: low 2nd-3rd {probe['low_order_db']:.0f} dB, high 5th-10th "
                         f"{probe['high_order_db']:.0f} dB against the tone.")
            word = record.get("texture") or texture_of(probe)
            lines.append(f"Harmonic spread: weighted by order (n²/4) {probe['weighted_distortion_db']:.0f} dB "
                         f"against the tone, {100 * probe['tristimulus3']:.1f}% of the power in the 5th-10th, "
                         f"centre at harmonic {probe['harmonic_centroid']:.2f}: texture {word}.")
            lines.append(f"Harmonics against noise: {probe['hnr_db']:.0f} dB.")
        lines.append(f"Intermodulation (two notes together): {-probe['intermod_db']:.0f} dB below the notes.")
    lines.append(f"Compression: of a 12 dB drop in your playing, {12 * (1 - record['compression']):.0f} dB comes back: "
                 f"{record.get('compressed')}.")
    if record.get("attack_ms") is not None:
        softer = (f", {record['attack_soft_ms']:.0f} ms played softer" if record.get("attack_soft_ms") is not None
                  else "")
        lines.append(f"Attack: notes reach full level in {record['attack_ms']:.0f} ms{softer}.")
    lines.append(f"Brightness: {record['brightness_oct']:+.1f} octave against your dry guitar: {record.get('brightness')}.")
    if record.get("brightness_bands_db"):
        bands = ", ".join(f"{name} {value:+.1f} dB" for name, value in record["brightness_bands_db"].items())
        lines.append(f"Energy by band against your dry guitar: {bands}.")
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


def _peak_envelope(samples):
    """The running peak of all channels over the last PEAK_WINDOW_S, one value every ENVELOPE_HOP_S."""
    rectified = np.max(np.abs(_channels(samples)), axis=1)
    width = max(1, round(PEAK_WINDOW_S * RATE))
    held = ndimage.maximum_filter1d(rectified, width, origin=(width - 1) // 2) if len(rectified) else rectified
    return held[::round(ENVELOPE_HOP_S * RATE)]


def onsets(dry):
    """Where notes start in the dry phrase, as indices into `_peak_envelope`: the peak envelope rises ONSET_RISE_DB
    within ONSET_LOOKBACK_S, within ONSET_RANGE_DB of the phrase's loudest moment, at least ONSET_GAP_S apart."""
    envelope = _peak_envelope(dry)
    if not envelope.size or envelope.max() <= 1e-6:
        return []
    level_db = 20 * np.log10(np.maximum(envelope, 1e-9))
    back = round(ONSET_LOOKBACK_S / ENVELOPE_HOP_S)
    lowest_before = ndimage.minimum_filter1d(level_db, back, origin=(back - 1) // 2)   # over the `back` values before
    rise = np.full(len(level_db), -np.inf)
    rise[1:] = level_db[1:] - lowest_before[:-1]
    loud_enough = level_db > level_db.max() - ONSET_RANGE_DB
    found, gap = [], round(ONSET_GAP_S / ENVELOPE_HOP_S)
    for i in np.flatnonzero((rise >= ONSET_RISE_DB) & loud_enough):
        if not found or i - found[-1] >= gap:
            found.append(int(i))
    return found


def attack_ms(samples, starts):
    """The median time, in ms, the peak envelope takes from 10% to 90% of the way from just before each note
    (its lowest point in the ONSET_LOOKBACK_S before the onset) to the note's peak (before the next note, within
    ATTACK_SPAN_S). None when no note gives a clear rise."""
    envelope = _peak_envelope(samples)
    back, span = round(ONSET_LOOKBACK_S / ENVELOPE_HOP_S), round(ATTACK_SPAN_S / ENVELOPE_HOP_S)
    rises = []
    for k, start in enumerate(starts):
        end = min(start + span, starts[k + 1] if k + 1 < len(starts) else len(envelope), len(envelope))
        first = max(0, start - back)
        if end - start < 2 or start >= len(envelope):
            continue
        low_at = first + int(np.argmin(envelope[first:start + 1]))
        base, peak = float(envelope[low_at]), float(envelope[start:end].max())
        if peak < 2 * base or peak <= 1e-6:
            continue
        after = envelope[low_at:end]
        ten = int(np.argmax(after >= base + .1 * (peak - base)))
        ninety = int(np.argmax(after >= base + .9 * (peak - base)))
        rises.append((ninety - ten) * ENVELOPE_HOP_S * 1000)
    return round(float(np.median(rises)), 1) if rises else None


def band_energy_db(samples):
    """Each band of BANDS's share of the sound's energy, in dB; channels added as power."""
    sides = _channels(samples)
    freq, power = signal.welch(sides, RATE, nperseg=min(8192, max(256, len(sides))), axis=0)
    total = power.sum(axis=1)
    whole = max(float(total.sum()), 1e-30)
    return {name: 10 * math.log10(max(float(total[(freq >= low) & (freq < high)].sum()) / whole, 1e-9))
            for name, (low, high) in BANDS.items()}


def heard(dry, loud, soft, tail, probe=None):
    """What a chain did to a phrase. `tail` starts where the phrase stopped; `probe` is `lab.hearing.probe`'s record."""
    loud_m, dry_m = measure(loud), measure(dry)
    starts = onsets(dry)
    dry_bands, loud_bands = band_energy_db(dry), band_energy_db(loud)
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
                 level_db=level(loud), probe=probe, loudness_lufs=loud_m.lufs, attack_ms=attack_ms(loud, starts),
                 attack_soft_ms=attack_ms(soft, starts),
                 brightness_bands_db={name: round(loud_bands[name] - dry_bands[name], 2) for name in BANDS})


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
