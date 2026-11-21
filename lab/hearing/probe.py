"""Test signals through a chain, and what they show directly.

A phrase of real playing says how a chain treats the guitar; test signals say
what the chain is. One pass of about eleven seconds, played from the computer
and returned over USB like any listening, holds, in this order:

1. one click, then two seconds of silence (first, so that no echo of a tone
   can be taken for an echo of the click);
2. a pure tone (196 Hz, the G string) 12 dB below the player's level for a
   firmly held note. The softer tone comes first: what still rings of it when
   the next tone sounds is 12 dB weaker still, where a firm tone before a soft
   one would leave its echoes, harmonics and all, inside the soft tone;
3. the same tone at the player's level, held 3.6 seconds;
4. a higher pure tone (740 Hz) at the player's level;
5. two tones together (196 and 262 Hz, a fourth apart), each 6 dB down.

What each part shows, by what is measured:

- distortion_db, distortion_soft_db: power at the harmonics of the 196 Hz tone
  against the tone itself, in dB, played firmly and 12 dB softer. Equalization,
  cabs, delay and reverb return a pure tone as a pure tone; only a non-linear
  stage adds harmonics. The harmonics are measured where they leave the chain,
  so everything after the non-linear stage (tone stack, cab, a delay's comb)
  shapes the figure.
- cleans_up_db: distortion_db minus distortion_soft_db, how many dB the
  harmonics fall when the tone is played 12 dB softer. What comes after the
  non-linear stage acts on both tones alike and cancels in the difference.
- distortion_740hz_db: the same as distortion_db for the 740 Hz tone. A stage
  that cuts lows before it clips (many overdrive pedals) distorts a high note
  far more than a low one. Not yet calibrated on the unit: a number, no word.
- even_odd_db: even harmonics against odd ones, within 40 dB either way; None
  when there are no harmonics to compare.
- intermod_db: power at the sum and difference tones two notes make (f2-f1,
  2f1-f2, 2f2-f1, f1+f2) against the two notes, in dB. This is what makes
  chords grind under distortion. None of the four falls on a multiple of
  50 Hz, so mains hum is not counted.
- gated: whether the softer tone came back at all.
- level_wobble and pitch_wobble: how much the held tone's level (percent, peak
  to peak of a sine-shaped swing) and pitch (cents, standard deviation) swing
  back and forth, as tremolo, chorus or vibrato do, over WOBBLE_S seconds with
  a straight-line drift taken off. Swings from 0.5 Hz to 12 Hz are read within
  a tenth of their size; at 0.3 Hz and at 30 Hz they read a fifth low.
- click_ms: when the click came back, against when it was due by the measured
  round trip: the chain's own delay. A click that comes back early says the
  round trip has changed since it was measured.
- echo_ms, echo_db: the first repeat of the click standing out above the smooth
  decay, its delay after the click's arrival and its level against the click.
  last_echo_ms is the last repeat within 30 dB of the click.
- late_energy_db: the energy arriving later than 40 ms after the click against
  the energy in those first 40 ms (the direct sound, through the cab), in dB:
  how much of the sound is echo and reverb. -70 is the floor.
- decay_rt60_s: how long what rings after the direct sound takes to fall
  60 dB, from its fall between -5 and -25 dB (Schroeder's backward sum of the
  energy after the first 40 ms, noise taken off). None when nothing rings, when
  it falls at once (one repeat has no rate of decay), or when it is still
  ringing two seconds on (rings_on); decays longer than about 5 s read as
  still ringing.

Behind a repeat, a tone's level steps when the repeat arrives, and behind a
reverb it creeps while the reverb builds up; both would be read as
intermodulation and as wobble. Those two are measured only after the last
repeat found from the click has arrived and a third of the decay time has
passed; when the tone is too short for that, or the chain rings on, they are
None: not measurable behind this echo.

Only harmonics are counted, not noise, so a noisy high-gain amp's hiss does
not read as distortion; a harmonic lost in the noise counts as the noise
floor, which limits the smallest distortion measured to about -70 dB.

How the harmonics are spread, all from the 196 Hz tone at the player's level.
P_n is the power at the n-th harmonic (P_1 the tone itself), with the noise
floor taken off each harmonic above the first:

- harmonics_db: each of the 2nd to 10th harmonics against the tone, in dB,
  floor -70.
- low_order_db: the 2nd and 3rd together against the tone; high_order_db: the
  5th to 10th together against the tone. The ear hears energy in the high
  orders far more than the same energy in the 2nd and 3rd, which the tone
  masks (Moore and Tan, JAES 2003/04; Geddes and Lee, AES 2003; docs/TONE_RESEARCH.md
  finding 7).
- weighted_distortion_db: the harmonics weighted by order before they are
  added, sum of P_n * n^2 / 4 over n = 2..10, against the tone. The weighting
  is Shorter's (D. E. L. Shorter, "The influence of high-order products in
  non-linear distortion", Electronic Engineering 22, 1950), which counts the
  2nd as it is and the 10th 25 times over. A square wave reads 0 dB.
- harmonic_centroid: the normalised harmonic centroid, the power-weighted mean
  harmonic number over n = 1..10, sum n P_n / sum P_n; 1.0 is a pure tone.
- tristimulus3: the power in harmonics 5..10 against the power in 1..10, as a
  ratio from 0 to 1 (the third tristimulus value of Pollard and Jansson, Acustica
  51, 1982, here on power). A square wave reads about 0.06.
- hnr_db: the power in harmonics 1..10 against the noise in 60 Hz to 4 kHz
  (the median power per bin away from any harmonic, divided by ln 2 to give the
  mean of noise, over the whole band), in dB, at most 120.

The centroid and tristimulus 3 follow how harsh a sound is heard, separately
from how bright it is (Zacharakis, Pastiadis and Reiss, Music Perception 2014
and 2015; docs/TONE_RESEARCH.md finding 2).

Records made with the first layout (no "version": a firm tone before the soft
one, two notes a third apart, and decay_s, three times the fall from -5 to
-25 dB of the click with its direct sound counted in) still load everywhere;
their distortion figures are the same quantities, their decay_s is not
decay_rt60_s and is shown under its own name.
"""

from dataclasses import asdict, dataclass, field
import math

import numpy as np
from scipy import signal

from lab.hearing.audio import RATE, power_db

VERSION = 2                          # the layout of the test signal; records without it were made with the first
F1, F2, F_HIGH = 196., 261.63, 739.99
LEAD_S, TAIL_S, GAP_S = .3, 2.0, .3
SOFT_S, LOUD_S, HIGH_S, PAIR_S = 1.0, 3.6, 1.0, 1.5
SOFTER_DB = 12.
SETTLE_S, WINDOW_S = .3, .6          # analyse this part of each tone, after compressors and gates settle
WOBBLE_FROM_S, WOBBLE_S, WOBBLE_LEAST_S = .5, 3.0, 2.0   # the held tone's level and pitch are followed this long
BIN_HZ = 6.                          # half width around a frequency that counts as that frequency
NOISE_S = .1                         # the start of the lead-in, where nothing of the click can be: the noise
DIRECT_S = .04                       # the click's direct sound, through any cab; later is echo and reverb
ECHO_WITHIN_DB, LAST_ECHO_WITHIN_DB = 40., 30.
AFTER_ECHO_S = .05                   # a tone is steady this long after the last repeat arrived
FLOOR_DB = -70.


def layout():
    """Where each part starts, in samples: click, softer tone, tone, higher tone, two tones; and where it all
    ends."""
    click = round(LEAD_S * RATE)
    soft = click + round(TAIL_S * RATE)
    tone = soft + round((SOFT_S + GAP_S) * RATE)
    high = tone + round((LOUD_S + GAP_S) * RATE)
    pair = high + round((HIGH_S + GAP_S) * RATE)
    return {"click": click, "soft": soft, "tone": tone, "high": high, "pair": pair,
            "end": pair + round((PAIR_S + GAP_S) * RATE)}


def held_note_peak(dry):
    """The peak of a firmly held note in the phrase: the 90th percentile of its 50 ms peaks. An inference from
    the phrase, used to set the level of the test tones."""
    mono = np.abs(np.asarray(dry, dtype=np.float64).reshape(len(dry), -1)[:, 0])
    frame = round(.05 * RATE)
    peaks = [mono[i:i + frame].max() for i in range(0, len(mono) - frame, frame)]
    peaks = [p for p in peaks if p > 1e-4]
    return float(np.percentile(peaks, 90)) if peaks else .1


def _tone(seconds, *pitches):
    """Pure tones added together, each of peak 1, faded in and out over 10 ms."""
    t = np.arange(round(seconds * RATE)) / RATE
    fade = np.minimum(1, np.minimum(t, seconds - t) / .01)
    return sum(np.sin(2 * np.pi * pitch * t) for pitch in pitches) * fade


def test_signal(peak):
    """The test signal for a player whose held notes peak at `peak` (linear, 0 to 1)."""
    places = layout()
    out = np.zeros(places["end"])

    def put(name, sound):
        out[places[name]:places[name] + len(sound)] = sound

    out[places["click"]] = peak
    put("soft", peak * 10 ** (-SOFTER_DB / 20) * _tone(SOFT_S, F1))
    put("tone", peak * _tone(LOUD_S, F1))
    put("high", peak * _tone(HIGH_S, F_HIGH))
    put("pair", peak / 2 * _tone(PAIR_S, F1, F2))
    return out.astype(np.float32)[:, None]


@dataclass(frozen=True)
class Probe:
    distortion_db: float        # harmonics of the 196 Hz tone against it, at playing level; -70 is the floor
    distortion_soft_db: float   # the same, 12 dB softer
    gated: bool                 # the softer tone did not come back
    cleans_up_db: float | None = None          # distortion_db - distortion_soft_db; None when gated or no harmonics
    distortion_740hz_db: float | None = None   # harmonics of the 740 Hz tone against it; not calibrated on the unit
    even_odd_db: float | None = None   # even harmonics against odd ones, -40 to 40; None without harmonics
    intermod_db: float | None = None   # sum and difference tones against the two notes; None behind an echo
    level_wobble: float | None = None  # percent the held tone's level swings; None behind an echo
    pitch_wobble: float | None = None  # cents the held tone's pitch swings (standard deviation)
    wobble_s: float | None = None      # how long the held tone was followed for the two above
    click_ms: float | None = None      # the click's arrival against when it was due; None when it was not found
    echo_ms: float | None = None       # delay of the first repeat of the click, if one stands out
    echo_db: float | None = None       # its level against the click
    last_echo_ms: float | None = None  # delay of the last repeat within 30 dB of the click
    late_energy_db: float | None = None    # energy later than 40 ms after the click against the first 40 ms
    decay_rt60_s: float | None = None      # time for what rings after the direct sound to fall 60 dB
    rings_on: bool | None = None           # still ringing two seconds after the click
    # How the harmonics of the tone at playing level are spread (see the module docstring).
    harmonics_db: list = field(default_factory=list)   # 2nd to 10th harmonic, each against the tone; floor -70
    low_order_db: float = FLOOR_DB        # 2nd and 3rd against the tone
    high_order_db: float = FLOOR_DB       # 5th to 10th against the tone
    weighted_distortion_db: float = FLOOR_DB  # harmonics weighted by n^2/4 (Shorter), against the tone
    harmonic_centroid: float = 1.         # power-weighted mean harmonic number over 1..10; 1.0 is a pure tone
    tristimulus3: float = 0.              # power in harmonics 5..10 against power in 1..10 (0 to 1)
    hnr_db: float = 0.                    # harmonics 1..10 against the noise in 60 Hz to 4 kHz
    version: int = VERSION

    def to_record(self):
        def rounded(value):
            if isinstance(value, float):
                return round(value, 4 if abs(value) < 1 else 3)
            return [rounded(item) for item in value] if isinstance(value, list) else value
        return {key: rounded(value) for key, value in asdict(self).items()}


def _mono(returned):
    """The louder channel of the return. Averaging the channels would cancel a sound whose sides are in opposite
    phase (a wide stereo effect) and measure it as clean."""
    returned = np.asarray(returned, dtype=np.float64)
    sides = returned.reshape(len(returned), -1)
    return sides[:, int(np.argmax(np.mean(np.square(sides), axis=0)))]


def _spectrum(part):
    window = signal.windows.hann(len(part))
    spectrum = np.abs(np.fft.rfft(part * window)) ** 2
    return np.fft.rfftfreq(len(part), 1 / RATE), spectrum


def _power(freq, spectrum, at):
    return float(spectrum[(freq > at - BIN_HZ) & (freq < at + BIN_HZ)].sum())


def _floor(freq, spectrum, pitch=F1):
    """The noise floor: the median power per bin between 60 Hz and 4 kHz, away from any harmonic of `pitch`."""
    keep = (freq > 60) & (freq < 4000)
    for k in range(1, 21):
        keep &= np.abs(freq - k * pitch) > 2 * BIN_HZ
    return float(np.median(spectrum[keep])) * (2 * BIN_HZ / (freq[1] - freq[0])) if keep.any() else 0.


def _harmonics(part, pitch=F1):
    """The power at harmonics 1..10 of `pitch` (index 0 is the tone, not floored; the rest with the noise floor
    taken off), and the noise per harmonic's width."""
    freq, spectrum = _spectrum(part)
    floor = _floor(freq, spectrum, pitch)
    powers = [_power(freq, spectrum, pitch)] + [max(_power(freq, spectrum, k * pitch) - floor, 0.)
                                                for k in range(2, 11)]
    return powers, floor


def _spread(powers, floor):
    """How the harmonics are spread: the fields of `Probe` from harmonics_db on (see the module docstring)."""
    tone = max(powers[0], 1e-20)
    each = [round(power_db(power / tone), 2) for power in powers[1:]]
    low = power_db(sum(powers[1:3]) / tone)
    high = power_db(sum(powers[4:10]) / tone)
    weighted = power_db(sum(power * n * n / 4 for n, power in enumerate(powers[1:], start=2)) / tone)
    total = max(sum(powers), 1e-20)
    centroid = sum(n * power for n, power in enumerate(powers, start=1)) / total
    tristimulus3 = sum(powers[4:10]) / total
    # The floor per harmonic's width, over 60 Hz to 4 kHz; a median of noise power per bin is ln 2 of its mean.
    noise = floor / (2 * BIN_HZ) * (4000 - 60) / math.log(2)
    hnr = min(10 * math.log10(total / noise), 120.) if noise > 0 else 120.
    return {"harmonics_db": each, "low_order_db": round(low, 2), "high_order_db": round(high, 2),
            "weighted_distortion_db": round(weighted, 2), "harmonic_centroid": round(centroid, 4),
            "tristimulus3": round(tristimulus3, 5), "hnr_db": round(hnr, 2)}


def _click(mono, places):
    """What the click shows: when it came back, its repeats, how much energy comes after the direct sound and how
    fast that dies away. Everything is None when the click cannot be told from what was already sounding."""
    found = {"click_ms": None, "echo_ms": None, "echo_db": None, "last_echo_ms": None, "late_energy_db": None,
             "decay_rt60_s": None, "rings_on": None}
    lead, end = round(NOISE_S * RATE), places["soft"]
    noise = float(np.mean(mono[:lead] ** 2))            # power per sample of what sounds before the click
    body = np.abs(mono[lead:end])
    peak = float(body.max()) if body.size else 0.
    if peak < 1e-6 or peak < 10 * math.sqrt(noise):
        return found
    at = lead + int(np.argmax(body >= max(.2 * peak, 6 * math.sqrt(noise))))
    found["click_ms"] = round((at - places["click"]) / RATE * 1000, 2)
    energy = mono[at:min(at + round(TAIL_S * RATE), end)] ** 2
    first = round(DIRECT_S * RATE)
    if len(energy) < 4 * first:
        return found
    excess = energy[first:] - noise
    direct = float(energy[:first].sum()) - noise * first
    late = float(excess.sum())
    doubt = 3 * math.sqrt(2 * len(excess)) * noise      # three standard deviations of that much summed noise
    if direct <= 0:
        return found
    heard = late > max(doubt, direct * 10 ** (FLOOR_DB / 10))
    found["late_energy_db"] = round(min(power_db(late / direct), 30.), 2) if heard else FLOOR_DB

    # Repeats: peaks of the energy (smoothed over 5 ms) that stand 10 dB above the smooth decay around them.
    smooth = np.convolve(energy, np.ones(240) / 240, mode="same")
    loudest = float(smooth[:round(.02 * RATE)].max())
    after = smooth[first::48]                                             # 1 ms steps
    trend = signal.medfilt(after, 51)
    peaks, _ = signal.find_peaks(after, distance=30)
    for p in peaks:
        if after[p] <= 10 * max(trend[p], noise, 1e-20):
            continue
        if found["echo_ms"] is None and after[p] > loudest * 10 ** (-ECHO_WITHIN_DB / 10):
            found["echo_ms"] = float(DIRECT_S * 1000 + p)
            found["echo_db"] = round(power_db(after[p] / loudest), 1)
        if after[p] > loudest * 10 ** (-LAST_ECHO_WITHIN_DB / 10):
            found["last_echo_ms"] = float(DIRECT_S * 1000 + p)

    # Decay: only of what comes after the direct sound. Schroeder's curve (the energy still to come, in dB) is
    # followed from -5 dB down to -25 dB, or to -15 dB, whichever it reaches within the first 60 % of the two
    # seconds: later than that the curve falls because the window ends, not because the sound does. The fall
    # between those two points over the time between them is the rate (the curve's own values there, so that the
    # steps of a train of repeats count in full). A curve that reaches neither in time is still ringing.
    found["rings_on"] = False
    if heard and found["late_energy_db"] > -45.:
        remaining = np.cumsum(excess[::-1])[::-1]
        curve = 10 * np.log10(np.maximum(remaining / remaining[0], 1e-12))
        measurable = 10 * math.log10(max(doubt, 1e-30) / remaining[0]) + 3.     # the curve means nothing under this
        start = int(np.argmax(curve <= -5.))
        for lowest in (-25., -15.):
            finish = int(np.argmax(curve <= lowest))
            if lowest < measurable or finish > .6 * len(curve):
                continue
            if finish - start >= round(.005 * RATE):                            # else it falls at once: no rate
                fall = float(curve[start] - curve[finish])                      # dB, over (finish - start) samples
                found["decay_rt60_s"] = round(60. / fall * (finish - start) / RATE, 3)
            break
        else:
            found["rings_on"] = True if measurable <= -15. else None            # None: the noise hides which
    return found


def _steady_from(click, earliest_s, least_s, tone_s):
    """Where in a tone of `tone_s` seconds a window of at least `least_s` may start (seconds after the tone's
    start) so that the tone's level has stopped changing: every repeat of its start has arrived, and a reverb has
    built up to within a tenth (a third of its decay time). None when the tone is too short for that, or when
    the chain was still ringing two seconds after the click."""
    if click["rings_on"]:
        return None
    start = earliest_s
    if click["last_echo_ms"] is not None:
        start = max(start, click["last_echo_ms"] / 1000 + AFTER_ECHO_S)
    if click["decay_rt60_s"] is not None:
        start = max(start, click["decay_rt60_s"] / 3)
    return start if start + least_s <= tone_s - .02 else None


def _wobble(mono, begin, start_s):
    """Level wobble (percent), pitch wobble (cents) and the seconds followed, on the fundamental alone, in 10 ms
    frames. Only what swings back and forth counts: a straight-line rise or fall is fitted and taken away."""
    seconds = min(WOBBLE_S, LOUD_S - .05 - start_s)
    edge = round(.05 * RATE)
    first = begin + round(start_s * RATE)
    held = mono[first - edge:first + round(seconds * RATE) + edge]
    band = signal.sosfiltfilt(signal.butter(4, (F1 * .8, F1 * 1.25), "bandpass", fs=RATE, output="sos"), held)
    analytic = signal.hilbert(band)
    envelope = np.abs(analytic)[edge:-edge]
    if envelope.size < RATE or envelope.max() <= 1e-6:
        return None, None, None

    def swing(values):
        frames = values[:len(values) // 480 * 480].reshape(-1, 480).mean(axis=1)
        x = np.arange(len(frames))
        return frames - np.polyval(np.polyfit(x, frames, 1), x)

    level = float(np.std(swing(envelope)) * 2 * math.sqrt(2) / envelope.mean() * 100)
    inst = (np.diff(np.unwrap(np.angle(analytic))) * RATE / (2 * np.pi))[edge:-edge]
    pitch = float(np.std(swing(1200 * np.log2(np.clip(inst, 1, None) / F1))))
    return round(level, 2), round(pitch, 2), round(seconds, 2)


def analyse(returned):
    """What a chain's return of `test_signal` shows. `returned` is lined up with what was sent."""
    mono = _mono(returned)
    places = layout()
    width = round(WINDOW_S * RATE)

    def part(name, start_s=SETTLE_S):
        start = places[name] + round(start_s * RATE)
        return mono[start:start + width]

    loud_powers, loud_floor = _harmonics(part("tone"))
    soft_powers, _ = _harmonics(part("soft"))
    loud_f, loud_even, loud_odd = loud_powers[0], sum(loud_powers[1::2]), sum(loud_powers[2::2])
    soft_f = soft_powers[0]
    gated = soft_f < loud_f * 10 ** (-(SOFTER_DB + 30) / 10)
    distortion = power_db((loud_even + loud_odd) / max(loud_f, 1e-20))
    soft = power_db(sum(soft_powers[1:]) / max(soft_f, 1e-20)) if not gated else FLOOR_DB
    even_odd = None
    if loud_even + loud_odd > loud_f * 10 ** (FLOOR_DB / 10):
        even_odd = round(min(40., max(-40., power_db(max(loud_even, 1e-30) / max(loud_odd, 1e-30), 1e-4))), 2)

    high_powers, _ = _harmonics(part("high"), F_HIGH)
    high = round(power_db(sum(high_powers[1:]) / high_powers[0]), 2) if high_powers[0] > 1e-12 else None

    click = _click(mono, places)

    # Two notes together, once every repeat of their start has arrived.
    intermod = None
    steady = _steady_from(click, SETTLE_S, WINDOW_S, PAIR_S)
    if steady is not None:
        freq, spectrum = _spectrum(part("pair", steady))
        floor = _floor(freq, spectrum)
        notes = _power(freq, spectrum, F1) + _power(freq, spectrum, F2)
        products = sum(max(_power(freq, spectrum, at) - floor, 0.)
                       for at in (F2 - F1, 2 * F1 - F2, 2 * F2 - F1, F1 + F2))
        intermod = round(power_db(products / max(notes, 1e-20)), 2)

    level_wobble = pitch_wobble = wobble_s = None
    steady = _steady_from(click, WOBBLE_FROM_S, WOBBLE_LEAST_S, LOUD_S)
    if steady is not None:
        level_wobble, pitch_wobble, wobble_s = _wobble(mono, places["tone"], steady)

    return Probe(distortion_db=round(distortion, 2), distortion_soft_db=round(soft, 2), gated=bool(gated),
                 cleans_up_db=None if gated or distortion <= FLOOR_DB else round(distortion - soft, 2),
                 distortion_740hz_db=high,
                 even_odd_db=even_odd, intermod_db=intermod, level_wobble=level_wobble, pitch_wobble=pitch_wobble,
                 wobble_s=wobble_s, **click, **_spread(loud_powers, loud_floor))
