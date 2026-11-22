"""One measurement of a piece of audio, used everywhere the app judges sound.

`measure` gives level, loudness, peaks, an absolute third-octave spectrum,
the spectral centre and stereo width. Channels are never added as waveforms,
so two channels in opposite phase are measured, not cancelled. One convention
for several channels: level, crest and the spectrum are those of the average
channel (the mean of the channels' powers), with any DC offset left out of the
level. Loudness alone follows its standard (ITU-R BS.1770), which adds the
channels' powers: the same sound on two channels reads 3 LU above one.

`heard` says what a chain did to a phrase, each value named for what it
measures. From the phrase, played as recorded and again 12 dB softer:

- compression: how much of a 12 dB drop in the playing fails to come back: 0
  when it comes back as 12 dB, 1 when it does not come back at all, below 0
  when the chain widens the drop (an expander, a gate closing): -1 is a drop
  of 24 dB or more. A phrase whose softer pass does not come through at all is
  marked soft_gated.
- brightness_db: the energy above 1 kHz (to 12 kHz) against the energy from
  200 Hz to 1 kHz, and that against the same in the dry phrase, in dB. The
  brightness word comes from this. brightness_oct is another quantity, kept
  under its own name: how many octaves the spectral centre (the power-weighted
  mean frequency, 60 Hz to 12 kHz) sits above the dry phrase's. The centre
  moves as much with the low end as with the highs and mis-ranks distorted
  guitar (docs/TONE_RESEARCH.md finding 3), so no word is taken from it when
  brightness_db was measured.
- band_levels_db: the level of each band of BANDS against the 200 Hz to 1 kHz
  band, and that against the same in the dry phrase, in dB. A level, not a
  share: lifting the lows leaves the bands above them where they were.
- low_end_db: the share of the energy in the third-octave bands centred 50 to
  200 Hz (44 to 223 Hz) against the dry phrase's share.
- tail_db: what still sounds 40 ms to 1.5 s after the phrase's last sample,
  against the phrase. It depends on how the phrase ends (a phrase cut while a
  note rings leaves more than one that ends in silence), so it compares two
  listenings of one phrase and nothing else. The tail word comes from the
  click of the test signals (`lab.hearing.probe`: late_energy_db), which does
  not depend on the phrase.
- attack_ms: the median time the peak envelope (the running peak of the
  waveform over 12.5 ms, a period of the lowest guitar note) takes from 10% to
  90% of the way from just before a note to its highest peak within 250 ms of
  the note's start. On a sustaining sound, or behind an echo or a reverb, that
  peak comes late, so this is the time to the highest peak, not the pick's
  attack. first_rise_ms is the pick's: the same 10% to 90% time, to the first
  peak the envelope holds (it does not rise another 0.25 dB in the next 15 ms).
  The notes are found once, in the dry phrase (the peak envelope rising 6 dB
  within 20 ms, within 35 dB of the loudest moment), so the loud and soft
  passes are timed on the same notes. An instant onset reads 0 to a few ms.
  The _soft_ values are the same on the pass played 12 dB softer. Attack time
  is one of the robust correlates of timbre (McAdams 2017; Zacharakis et al.
  2015; docs/TONE_RESEARCH.md findings 4 and 5).

Distortion is not inferred from compression: it is measured with test tones
(`lab.hearing.probe`), whose record `heard` carries.

The words are inferences from these measurements, each by a stated rule.
Distortion's and texture's thresholds were set from a ladder of sixteen chains
measured on the unit (research/measurements/distortion-ladder.md); more chains
and other phrases would make them firmer. The thresholds of the compression,
brightness and tail words are provisional: chosen by ear and arithmetic, not
yet calibrated on the unit.

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
# The one table of named bands: (name by its edges, from Hz, to Hz, the word the guitar profile uses for it).
BANDS = (("60-200 Hz", 60., 200., "lows"), ("200 Hz-1 kHz", 200., 1000., "low mids"), ("1-3 kHz", 1000., 3000., "mids"),
         ("3-6 kHz", 3000., 6000., "presence"), ("6-12 kHz", 6000., 12000., "air"))
REFERENCE_BAND = "200 Hz-1 kHz"       # the body of a guitar's sound: other bands are levelled against it
HIGHS_HZ = (1000., 12000.)            # brightness_db: this against the reference band
LOW_END_BELOW_HZ = 200.               # low_end_db: third-octave bands centred below this (their edges: 44 to 223 Hz)
STEP_DB = 12.
SOFTER = 10 ** (-STEP_DB / 20)
EARLY, LATE = (.04, .40), (.40, 1.5)     # seconds after the phrase stops
LIFT_CEILING_DB = 6.                     # `match` never lifts a sound by more (the unit's own ceiling, unit.CEILING_DB)
# Distortion words, from the harmonics a pure tone gains (lab.hearing.probe), in dB against the tone. Set from a ladder of
# chains measured on the unit on 2026-11-20 (research/measurements/distortion-ladder.md). Played firmly, a driven
# amp's harmonics level off near -18 to -20 dB, so how far in it is shows in the same tone 12 dB softer: a crunchy
# amp cleans up, a saturated one stays distorted. The figures are of the harmonics as they leave the chain, at
# 196 Hz: a tone stack or cab that lifts or cuts the tone against its harmonics moves them, and the border between
# overdriven and saturated lies inside that plateau, so a chain within a dB or two of a border could be either
# word. The probe's cleans_up_db and distortion_740hz_db are shown beside the word for that reason.
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


# Texture words, from how the test tone's harmonics are spread (lab.hearing.probe): round, warm, even, edged, rough.
# Energy in the high orders (5th to 10th) is heard as rough far more than the same energy in the 2nd and 3rd
# (docs/TONE_RESEARCH.md findings 2 and 7). Set from the ladder measured on the unit on 2026-11-20
# (research/measurements/distortion-ladder.md): driven amps through a cab put 0.3 to 0.7 % of the power in the 5th
# to 10th harmonics, far from hard clipping (a square wave reads tristimulus 3 = 0.06), so the share alone does not
# tell them apart; the balance between high orders (5th-10th) and low orders (2nd-3rd) does, from -6 dB (a Plexi at
# gain 2 or 5) to +4 dB (a Soldano lead). Only the rough-by-share border was set from synthetic hard clipping.
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
FIRST_PEAK_HOLD_S, FIRST_PEAK_WITHIN = .015, 1.03   # the first peak: no more than 0.25 dB higher in the next 15 ms
# The thresholds of the next three scales are PROVISIONAL: chosen by ear and arithmetic, not calibrated on the unit.
# Compression: how much of a 12 dB drop in the playing fails to come back (0 none, 1 all; below 0 the drop widens).
COMPRESSION = ((.82, "squashed"), (.62, "heavy"), (.40, "moderate"), (.18, "light"), (0., "none"))
EXPANDING_BELOW = -.18                   # the drop comes back more than 2 dB wider
# Brightness, from brightness_db: 3 dB more or less energy above 1 kHz, against the body, than the dry guitar has.
BRIGHTNESS_DB = ((3., "bright"), (-3., "balanced"), (-math.inf, "dark"))
# Brightness of records without brightness_db, from the spectral centre (brightness_oct) alone.
BRIGHTNESS = ((.45, "bright"), (-.25, "balanced"), (-math.inf, "dark"))
# Tail, from the click (probe late_energy_db): the energy later than 40 ms against the direct sound. A reverb mixed
# at a third and up is at -9 dB or more, at a tenth near -19 dB, at a twentieth near -26 dB.
TAIL_LATE = ((-9., "wet"), (-18., "ambient"), (-30., "a touch of room"), (-math.inf, "dry"))
# Tail of records without the click's figure, from tail_db (after the phrase's end, against the phrase).
TAIL = ((-22., "wet"), (-34., "ambient"), (-42., "a touch of room"), (-math.inf, "dry"))
WORDS = {"distortion": DISTORTION, "texture": TEXTURE,
         "compression": tuple(word for _, word in COMPRESSION[::-1]),
         "brightness": tuple(word for _, word in BRIGHTNESS[::-1]),
         "tail": tuple(word for _, word in TAIL[::-1])}


def _channels(samples):
    samples = np.asarray(samples, dtype=np.float64)
    return samples[:, None] if samples.ndim == 1 else samples


def level(samples):
    """RMS level in dBFS of the average channel (the mean of the channels' powers), any DC offset left out."""
    sides = _channels(samples)
    if not sides.size:
        return db(0.)
    return db(float(np.sqrt(np.mean(np.square(sides - sides.mean(axis=0))))) + 1e-12)


def _louder(sides):
    return sides[:, int(np.argmax(np.mean(np.square(sides), axis=0)))]


def crest(samples):
    """Peak over RMS, in dB, of the louder channel: a sine reads 3.01 on one channel or on several."""
    sides = _channels(samples)
    if not sides.size:
        return 0.
    loudest = _louder(sides)
    return db(float(np.max(np.abs(loudest)))) - level(loudest)


def _spectrum(samples):
    """Frequencies and the power density at each, of the average channel (Welch, 8192-sample Hann windows: bins
    5.9 Hz apart, so a band narrower than about 25 Hz is coarse)."""
    sides = _channels(samples)
    freq, power = signal.welch(sides, RATE, nperseg=min(8192, max(256, len(sides))), axis=0)
    return freq, power.mean(axis=1)


def _band(freq, power, low, high):
    return float(power[(freq >= low) & (freq < high)].sum())


@dataclass(frozen=True)
class Measurement:
    seconds: float
    rms_db: float         # of the average channel, DC left out
    peak_db: float
    true_peak_db: float
    crest_db: float       # peak over RMS of the louder channel
    lufs: float | None    # ITU-R BS.1770 integrated loudness (which adds the channels); None under 0.4 s or silent
    bands_db: tuple       # absolute level in each third-octave band of CENTRES, of the average channel
    centroid_hz: float    # power-weighted mean frequency, 60 Hz to 12 kHz
    width: float          # 1 - |correlation of the two channels|: 0 for mono (or opposite phase), 1 for unrelated
    clipped: int

    def to_record(self):
        return {key: (list(value) if isinstance(value, tuple) else value) for key, value in asdict(self).items()}


def _true_peak(sides):
    upsampled = signal.resample_poly(sides, 4, 1, axis=0) if len(sides) > 64 else sides
    return db(float(np.max(np.abs(upsampled), initial=0.)))


def measure(samples):
    sides = _channels(samples)
    if not sides.size:
        return Measurement(0., db(0.), db(0.), db(0.), 0., None, tuple(-200. for _ in CENTRES), 0., 0., 0)
    peak = float(np.max(np.abs(sides)))
    try:
        loudness = float(pyloudnorm.Meter(RATE).integrated_loudness(sides if sides.shape[1] > 1 else sides[:, 0]))
    except ValueError:
        loudness = float("-inf")
    freq, power = _spectrum(sides)
    df = float(freq[1] - freq[0]) if len(freq) > 1 else 1.
    bands = []
    for centre in CENTRES:
        low, high = centre / 2 ** (1 / 6), centre * 2 ** (1 / 6)
        bands.append(round(10 * math.log10(_band(freq, power, low, high) * df + 1e-20), 2))
    audible = (freq >= 60) & (freq <= 12000)
    centroid = float((freq[audible] * power[audible]).sum() / max(float(power[audible].sum()), 1e-30))
    width = 0.
    if sides.shape[1] == 2:
        left, right = sides[:, 0], sides[:, 1]
        scale = math.sqrt(float(np.dot(left, left) * np.dot(right, right)))
        width = 1 - abs(float(np.dot(left, right)) / scale) if scale else 0.
    return Measurement(len(sides) / RATE, level(sides), db(peak), _true_peak(sides), crest(sides),
                       loudness if math.isfinite(loudness) else None, tuple(bands), centroid, width,
                       int(np.count_nonzero(np.abs(sides) >= .999)))


def gap(first, second):
    """Second minus first, per third-octave band, with the difference in overall level removed. Each is a
    `Measurement` or its bands_db."""
    a, b = (np.array(getattr(item, "bands_db", item), dtype=float) for item in (first, second))
    heard = (a > a.max() - 60) & (b > b.max() - 60)
    shift = b - a
    shift = shift - (shift[heard].mean() if heard.any() else 0.)
    return tuple(round(float(value), 2) if audible else 0. for value, audible in zip(shift, heard))


def band_gaps(first, second):
    """`gap` gathered into the named bands of BANDS: {name: mean of the third-octave differences in that band}."""
    found = gap(first, second)
    out = {}
    for name, low, high, _ in BANDS:
        inside = [value for value, centre in zip(found, CENTRES) if low <= centre < high]
        out[name] = round(float(np.mean(inside)), 1) if inside else 0.
    return out


@dataclass(frozen=True)
class Heard:
    """What listening found, each value named for what was measured (see the module docstring).

    From the phrase: compression, crest, brightness and low end against the dry phrase, what still sounds after
    the phrase stops, the level, the rise of the notes and the level by band against the dry phrase. From the
    test signals (`lab.hearing.probe`): distortion, the harmonic spread, echoes and decay, in `probe`. Fields
    added later default to None, so records made before them still load.

    The words are the scales the designer and the player use, each an inference by a stated rule. Distortion's
    words (clean to saturated) come from the measured harmonics, not from compression; where no test signals
    were played they are None.
    """
    compression: float  # 0 when a 12 dB drop in the playing comes back as 12 dB, 1 as 0 dB, -1 as 24 dB or more
    crest_db: float     # peak over RMS of the louder channel of the return
    brightness_oct: float  # octaves the spectral centre sits above the dry phrase's
    low_end_db: float   # share of energy in the third-octave bands from 50 to 200 Hz against the dry phrase's
    tail_db: float      # what still sounds after the phrase's last sample, against the phrase
    level_db: float
    probe: dict | None = None
    attack_ms: float | None = None       # median 10%-90% rise to the highest peak within 250 ms of a note
    attack_soft_ms: float | None = None  # the same, played 12 dB softer
    brightness_bands_db: dict | None = None  # in records made before band_levels_db: each band's share of the energy
    loudness_lufs: float | None = None  # integrated loudness of the return (ITU-R BS.1770), for matching A and B
    brightness_db: float | None = None  # energy above 1 kHz against 200 Hz-1 kHz, against the same in the dry phrase
    band_levels_db: dict | None = None  # each band of BANDS against the reference band, against the dry phrase's
    first_rise_ms: float | None = None       # median 10%-90% rise to the first peak of a note: the pick's attack
    first_rise_soft_ms: float | None = None  # the same, played 12 dB softer
    soft_gated: bool | None = None      # the phrase played 12 dB softer did not come through at all
    true_peak_db: float | None = None   # USB return, including the processed tail
    clipped: bool | None = None

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
        if self.compression < EXPANDING_BELOW:
            return "expanding"
        return next((word for floor, word in COMPRESSION if self.compression >= floor), "none")

    @property
    def tone(self):
        """The brightness word: from brightness_db, or, in a record without it, from the spectral centre."""
        if self.brightness_db is not None:
            return next(word for floor, word in BRIGHTNESS_DB if self.brightness_db >= floor)
        return next(word for floor, word in BRIGHTNESS if self.brightness_oct >= floor)

    @property
    def tail(self):
        """The tail word: from the click's late energy, or, without test signals, from what follows the phrase."""
        late = (self.probe or {}).get("late_energy_db")
        if late is not None:
            return next(word for floor, word in TAIL_LATE if late >= floor)
        return next(word for floor, word in TAIL if self.tail_db >= floor)

    def words(self):
        return {"distortion": self.distortion, "compressed": self.compressed, "brightness": self.tone,
                "tail": self.tail, "texture": self.texture}

    def said(self):
        return summary(self.words())

    def to_record(self):
        values = {key: round(value, 3) for key, value in asdict(self).items() if isinstance(value, float)}
        return {**values, "attack_ms": self.attack_ms, "attack_soft_ms": self.attack_soft_ms,
                "first_rise_ms": self.first_rise_ms, "first_rise_soft_ms": self.first_rise_soft_ms,
                "brightness_db": None if self.brightness_db is None else round(self.brightness_db, 2),
                "band_levels_db": dict(self.band_levels_db) if self.band_levels_db else None,
                "brightness_bands_db": dict(self.brightness_bands_db) if self.brightness_bands_db else None,
                "soft_gated": self.soft_gated, "clipped": self.clipped, "probe": self.probe, **self.words()}


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

    first = word("distortion", "drive") if "distortion" not in found or isinstance(found.get("distortion"), str) else ""
    return ", ".join(item for item in (first, word("brightness", "tone"), word("tail", "space")) if item)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def measured_lines(record):
    """What listening measured, one line each, named for what was measured, with the word it gives. Reads
    records of every age: a value that was not measured, or could not be, is left out or said to be so."""
    if not record or "probe" not in record:
        return ["Not measured with the current tests yet."]
    probe, lines = record["probe"] or {}, []
    if record.get("clipped"):
        lines.append("USB return reached full scale. Lower clean level inside the preset and measure again.")
    current = probe.get("version", 1) >= 2        # the test signal's second layout (lab.hearing.probe)
    if _number(probe.get("distortion_db")) and _number(probe.get("distortion_soft_db")):
        line = (f"Distortion: harmonics {-probe['distortion_db']:.0f} dB below the tone played firmly, "
                f"{-probe['distortion_soft_db']:.0f} dB below it 12 dB softer: {record.get('distortion')}.")
        if _number(probe.get("cleans_up_db")):
            line += f" Played softer the harmonics fall {probe['cleans_up_db']:.0f} dB."
        lines.append(line)
        if _number(probe.get("distortion_740hz_db")):
            lines.append(f"Distortion of a higher tone (740 Hz): harmonics {-probe['distortion_740hz_db']:.0f} dB "
                         "below it (not yet calibrated on the unit; no word).")
        if _number(probe.get("low_order_db")) and _number(probe.get("high_order_db")):
            lines.append(f"Harmonic orders: low 2nd-3rd {probe['low_order_db']:.0f} dB, high 5th-10th "
                         f"{probe['high_order_db']:.0f} dB against the tone.")
            word = record.get("texture") or texture_of(probe)
            lines.append(f"Harmonic spread: weighted by order (n²/4) {probe['weighted_distortion_db']:.0f} dB "
                         f"against the tone, {100 * probe['tristimulus3']:.1f}% of the power in the 5th-10th, "
                         f"centre at harmonic {probe['harmonic_centroid']:.2f}: texture {word}.")
            lines.append(f"Harmonics against noise: {probe['hnr_db']:.0f} dB.")
        if _number(probe.get("intermod_db")):
            lines.append(f"Intermodulation (two notes together): {-probe['intermod_db']:.0f} dB below the notes.")
        elif current:
            lines.append("Intermodulation (two notes together): not measurable behind this echo or reverb.")
    if _number(record.get("compression")):
        back = 12 * (1 - record["compression"])
        lines.append(f"Compression: of a 12 dB drop in your playing, {back:.0f} dB"
                     f"{' or more' if record['compression'] <= -1 else ''} comes back: {record.get('compressed')}.")
    if record.get("soft_gated"):
        lines.append("Gate: the phrase played 12 dB softer did not come through.")
    if _number(record.get("first_rise_ms")):
        softer = (f", {record['first_rise_soft_ms']:.0f} ms played softer" if _number(record.get("first_rise_soft_ms"))
                  else "")
        lines.append(f"Pick attack: notes rise to their first peak in {record['first_rise_ms']:.0f} ms{softer}.")
    if _number(record.get("attack_ms")):
        softer = (f", {record['attack_soft_ms']:.0f} ms played softer" if _number(record.get("attack_soft_ms"))
                  else "")
        lines.append(f"Time to the highest peak within 250 ms of a note: {record['attack_ms']:.0f} ms{softer}.")
    if _number(record.get("brightness_db")):
        lines.append(f"Brightness: energy above 1 kHz against 200 Hz to 1 kHz, {record['brightness_db']:+.1f} dB "
                     f"against your dry guitar: {record.get('brightness')}.")
        if _number(record.get("brightness_oct")):
            lines.append(f"Spectral centre: {record['brightness_oct']:+.1f} octave against your dry guitar.")
    elif _number(record.get("brightness_oct")):
        lines.append(f"Spectral centre: {record['brightness_oct']:+.1f} octave against your dry guitar: "
                     f"{record.get('brightness')}.")
    if record.get("band_levels_db"):
        bands = ", ".join(f"{name} {value:+.1f} dB" for name, value in record["band_levels_db"].items())
        lines.append(f"Level by band against your dry guitar, with 200 Hz to 1 kHz as the reference: {bands}.")
    elif record.get("brightness_bands_db"):
        bands = ", ".join(f"{name} {value:+.1f} dB" for name, value in record["brightness_bands_db"].items())
        lines.append(f"Share of the energy by band against your dry guitar: {bands}.")
    if _number(probe.get("late_energy_db")):
        line = (f"Echo and reverb: after a click, the energy later than 40 ms is {-probe['late_energy_db']:.0f} dB "
                f"below the direct sound: {record.get('tail')}.")
        if _number(probe.get("decay_rt60_s")):
            line += f" It takes about {probe['decay_rt60_s']:.1f} s to fall 60 dB."
        elif probe.get("rings_on"):
            line += " It was still ringing two seconds on."
        lines.append(line)
        if _number(record.get("tail_db")):
            lines.append(f"After this phrase ends: {-record['tail_db']:.0f} dB below the playing still sounds "
                         "(this depends on how the phrase ends).")
    elif _number(record.get("tail_db")):
        lines.append(f"Tail: {-record['tail_db']:.0f} dB below the playing still sounds after the phrase ends: "
                     f"{record.get('tail')}.")
    if _number(probe.get("echo_ms")) and _number(probe.get("echo_db")):
        lines.append(f"Echo: a repeat {probe['echo_ms']:.0f} ms after a note, {-probe['echo_db']:.0f} dB down.")
    if not current and _number(probe.get("decay_s")) and probe["decay_s"] >= .3:
        # The first layout's figure: three times the fall from -5 to -25 dB of the click, direct sound counted in.
        lines.append("Decay (the earlier measure, direct sound counted in): " +
                     (f"about {probe['decay_s']:.1f} s." if probe["decay_s"] < 6 else "still ringing after 2 s."))
    level_wobble, pitch_wobble = probe.get("level_wobble"), probe.get("pitch_wobble")
    if _number(level_wobble) and _number(pitch_wobble):
        if level_wobble >= 10 or pitch_wobble >= 3:
            lines.append(f"Movement on a held note: level {level_wobble:.0f}%, pitch {pitch_wobble:.0f} cents.")
    elif current and probe:
        lines.append("Movement on a held note: not measurable behind this echo or reverb.")
    if probe.get("gated"):
        lines.append("Gate: a note 12 dB softer than firm playing did not come through.")
    return lines


def _lows(measured):
    """The share, in dB, of the energy in the third-octave bands centred below LOW_END_BELOW_HZ."""
    bands = np.array(measured.bands_db)
    total = 10 * np.log10(np.sum(10 ** (bands / 10)) + 1e-30)
    lows = 10 * np.log10(np.sum(10 ** (bands[np.array(CENTRES) < LOW_END_BELOW_HZ] / 10)) + 1e-30)
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
    found, apart = [], round(ONSET_GAP_S / ENVELOPE_HOP_S)
    for i in np.flatnonzero((rise >= ONSET_RISE_DB) & loud_enough):
        if not found or i - found[-1] >= apart:
            found.append(int(i))
    return found


def _rise_ms(samples, starts, first_peak):
    """The median 10%-90% rise time, in ms, of the peak envelope from just before each note (its lowest point in
    the ONSET_LOOKBACK_S before the onset) to the note's peak: the first peak it holds, or the highest before the
    next note within ATTACK_SPAN_S. None when no note gives a clear rise."""
    envelope = _peak_envelope(samples)
    back, span = round(ONSET_LOOKBACK_S / ENVELOPE_HOP_S), round(ATTACK_SPAN_S / ENVELOPE_HOP_S)
    hold = round(FIRST_PEAK_HOLD_S / ENVELOPE_HOP_S)
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
        if first_peak:
            # The first value, at least twice the base, that the envelope does not top by 0.25 dB in the next 15 ms.
            for i in range(len(after)):
                if after[i] >= 2 * base and after[i] > 1e-6 and \
                        float(after[i + 1:i + 1 + hold].max(initial=0.)) <= after[i] * FIRST_PEAK_WITHIN:
                    after, peak = after[:i + 1], float(after[i])
                    break
        ten = int(np.argmax(after >= base + .1 * (peak - base)))
        ninety = int(np.argmax(after >= base + .9 * (peak - base)))
        rises.append((ninety - ten) * ENVELOPE_HOP_S * 1000)
    return round(float(np.median(rises)), 1) if rises else None


def attack_ms(samples, starts):
    """The median time, in ms, the peak envelope takes from 10% to 90% of the way from just before each note to
    the note's highest peak (before the next note, within ATTACK_SPAN_S). On a sustaining sound or behind an echo
    that peak comes late: this is the time to the highest peak, not the pick's attack (`first_rise_ms`)."""
    return _rise_ms(samples, starts, first_peak=False)


def first_rise_ms(samples, starts):
    """The pick's attack: the median 10%-90% time, in ms, of each note's rise to the first peak the envelope
    holds. None when no note gives a clear rise."""
    return _rise_ms(samples, starts, first_peak=True)


def band_levels_db(samples):
    """The level, in dB, of each band of BANDS and of the highs (HIGHS_HZ, under "highs"), of the average channel."""
    freq, power = _spectrum(samples)
    df = float(freq[1] - freq[0]) if len(freq) > 1 else 1.
    found = {name: 10 * math.log10(_band(freq, power, low, high) * df + 1e-20) for name, low, high, _ in BANDS}
    found["highs"] = 10 * math.log10(_band(freq, power, *HIGHS_HZ) * df + 1e-20)
    return found


def against_reference(levels, dry_levels):
    """Each band's level against the reference band, and that against the same in the dry sound: (brightness_db,
    {band: dB}). A band the dry sound has next to nothing in (70 dB under its reference band) is left out, and
    brightness_db is None when that is so of everything above 1 kHz."""
    def relative(found, name):
        return found[name] - found[REFERENCE_BAND]

    bands = {name: round(relative(levels, name) - relative(dry_levels, name), 2) for name, _, _, _ in BANDS
             if name != REFERENCE_BAND and relative(dry_levels, name) > -70.}
    bright = None
    if relative(dry_levels, "highs") > -70.:
        bright = relative(levels, "highs") - relative(dry_levels, "highs")
    return bright, bands


def heard(dry, loud, soft, tail, probe=None, soft_gated=False):
    """What a chain did to a phrase. `tail` starts where the phrase stopped; `probe` is `lab.hearing.probe`'s record;
    `soft_gated` says the softer pass did not come through, so nothing is timed on it."""
    loud_m, dry_m = measure(loud), measure(dry)
    starts = onsets(dry)
    step = level(loud) - level(soft)
    tail = _channels(tail)
    windows = [tail[round(start * RATE):round(end * RATE)] for start, end in (EARLY, LATE)]
    rings = max((level(window) for window in windows if len(window)), default=-200.)
    bright, bands = against_reference(band_levels_db(loud), band_levels_db(dry))
    return Heard(compression=float(min(1., max(-1., 1 - step / STEP_DB))), crest_db=crest(loud),
                 brightness_oct=float(np.log2(max(loud_m.centroid_hz, 1.) / max(dry_m.centroid_hz, 1.))),
                 low_end_db=float(_lows(loud_m) - _lows(dry_m)), tail_db=rings - level(loud),
                 level_db=level(loud), probe=probe, loudness_lufs=loud_m.lufs, attack_ms=attack_ms(loud, starts),
                 attack_soft_ms=None if soft_gated else attack_ms(soft, starts),
                 brightness_db=None if bright is None else float(bright), band_levels_db=bands,
                 first_rise_ms=first_rise_ms(loud, starts),
                 first_rise_soft_ms=None if soft_gated else first_rise_ms(soft, starts), soft_gated=bool(soft_gated),
                 true_peak_db=max(loud_m.true_peak_db, _true_peak(tail)),
                 clipped=bool(loud_m.clipped or (len(tail) and np.max(np.abs(tail)) >= .999)))


def match(measurements, *, target_lufs=-23., offset_db=0., ceiling_db=-3., max_boost_db=0.):
    """Trims, in dB, that bring every sound to one loudness.

    The common loudness is the quietest sound's, or the target if lower, and
    never so high that a sound's true peak would pass the ceiling. A lift
    above it is made only when asked for, by at most `max_boost_db` and
    LIFT_CEILING_DB.
    """
    if not measurements:
        return []
    lufs = [found.lufs for found in measurements]
    if any(value is None for value in lufs):
        raise ValueError("A sound was too quiet to measure.")
    headroom = min(value + ceiling_db - found.true_peak_db for value, found in zip(lufs, measurements))
    reference = min(target_lufs, min(lufs), headroom)
    lift = max(0., min(offset_db, max_boost_db, LIFT_CEILING_DB))
    target = min(reference + offset_db, headroom, reference + lift if offset_db > 0 else reference + offset_db)
    return [target - value for value in lufs]
