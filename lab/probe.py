"""Test signals through a chain, and what they show directly.

A phrase of real playing says how a chain treats the guitar; test signals say
what the chain is. One pass of about five seconds, played from the computer and
returned over USB like any listening, holds:

1. one click, then two seconds of silence (first, so that no echo of a tone
   can be taken for an echo of the click);
2. a pure tone (196 Hz, the G string) at the player's level for a firmly held
   note, then the same tone 12 dB softer;
3. two tones together (196 and 247 Hz, a third apart), each 6 dB down.

What each part shows, by what is measured:

- distortion: power at the tone's harmonics against the tone itself, in dB.
  Equalization, cabs, delay and reverb return a pure tone as a pure tone;
  only a non-linear stage adds harmonics. Measured at both levels, so it
  also says whether the sound cleans up when played softer.
- even against odd harmonics: the balance between the two families.
- intermodulation: power at the sum and difference tones two notes make
  (f2-f1, 2f1-f2, 2f2-f1, f1+f2) against the two notes, in dB. This is what
  makes chords grind under distortion.
- gate: whether the softer tone came back at all.
- level wobble and pitch wobble: how much the held tone's level (percent)
  and pitch (cents) swing back and forth, as tremolo, chorus or vibrato do;
  a slow rise or fall, such as a delay or reverb building up, is not counted.
- echoes: a repeat of the click standing out above the smooth decay, with
  its delay in milliseconds and its level against the click.
- decay: how long the click's energy takes to fall 60 dB (estimated from
  the first 20 dB), as reverb does.

Only harmonics are counted, not noise, so a noisy high-gain amp's hiss does
not read as distortion; a harmonic lost in the noise counts as the noise
floor, which limits the smallest distortion measured to about -70 dB.
"""

from dataclasses import asdict, dataclass
import math

import numpy as np
from scipy import signal

from .audio import RATE

F1, F2 = 196., 246.94
TONE_S, GAP_S, LEAD_S, TAIL_S = 1.0, .2, .3, 2.0
SOFTER_DB = 12.
SETTLE_S, WINDOW_S = .3, .6          # analyse the middle of each tone, after compressors and gates settle
BIN_HZ = 6.                          # half width around a frequency that counts as that frequency


def layout():
    """Where each part starts, in samples: click, tone, softer tone, two tones; and where it all ends."""
    step = round((TONE_S + GAP_S) * RATE)
    click = round(LEAD_S * RATE)
    tone = click + round(TAIL_S * RATE)
    return {"click": click, "tone": tone, "soft": tone + step, "pair": tone + 2 * step, "end": tone + 3 * step}


def held_note_peak(dry):
    """The peak of a firmly held note in the phrase: the 90th percentile of its 50 ms peaks."""
    mono = np.abs(np.asarray(dry, dtype=np.float64).reshape(len(dry), -1)[:, 0])
    frame = round(.05 * RATE)
    peaks = [mono[i:i + frame].max() for i in range(0, len(mono) - frame, frame)]
    peaks = [p for p in peaks if p > 1e-4]
    return float(np.percentile(peaks, 90)) if peaks else .1


def test_signal(peak):
    """The test signal for a player whose held notes peak at `peak` (linear, 0 to 1)."""
    places = layout()
    total = places["end"]
    out = np.zeros(total)
    t = np.arange(round(TONE_S * RATE)) / RATE
    fade = np.minimum(1, np.minimum(t, TONE_S - t) / .01)
    out[places["tone"]:places["tone"] + len(t)] = peak * np.sin(2 * np.pi * F1 * t) * fade
    soft = peak * 10 ** (-SOFTER_DB / 20)
    out[places["soft"]:places["soft"] + len(t)] = soft * np.sin(2 * np.pi * F1 * t) * fade
    half = peak / 2
    out[places["pair"]:places["pair"] + len(t)] = half * (np.sin(2 * np.pi * F1 * t) + np.sin(2 * np.pi * F2 * t)) * fade
    out[places["click"]] = peak
    return out.astype(np.float32)[:, None]


@dataclass(frozen=True)
class Probe:
    distortion_db: float        # harmonics against the tone at playing level; -70 is the floor
    distortion_soft_db: float   # the same, 12 dB softer
    even_odd_db: float          # even harmonics against odd ones
    intermod_db: float          # sum and difference tones against the two notes
    gated: bool                 # the softer tone did not come back
    level_wobble: float         # percent the held tone's level moves
    pitch_wobble: float         # cents the held tone's pitch moves (standard deviation)
    echo_ms: float | None       # delay of the first repeat of the click, if one stands out
    echo_db: float | None       # its level against the click
    decay_s: float              # time to fall 60 dB after the click, estimated from the first 20 dB

    def to_record(self):
        return {key: (round(value, 3) if isinstance(value, float) else value) for key, value in asdict(self).items()}


def _mono(returned):
    returned = np.asarray(returned, dtype=np.float64)
    return returned.reshape(len(returned), -1).mean(axis=1)


def _spectrum(part):
    window = signal.windows.hann(len(part))
    spectrum = np.abs(np.fft.rfft(part * window)) ** 2
    return np.fft.rfftfreq(len(part), 1 / RATE), spectrum


def _power(freq, spectrum, at):
    return float(spectrum[(freq > at - BIN_HZ) & (freq < at + BIN_HZ)].sum())


def _floor(freq, spectrum):
    """The noise floor: the median power per bin between the tone and 4 kHz, away from any harmonic."""
    keep = (freq > 60) & (freq < 4000)
    for k in range(1, 21):
        keep &= np.abs(freq - k * F1) > 2 * BIN_HZ
    return float(np.median(spectrum[keep])) * (2 * BIN_HZ / (freq[1] - freq[0])) if keep.any() else 0.


def _db(ratio):
    return 10 * math.log10(max(ratio, 1e-7))


def _harmonics(part):
    freq, spectrum = _spectrum(part)
    floor = _floor(freq, spectrum)
    fundamental = _power(freq, spectrum, F1)
    even = sum(max(_power(freq, spectrum, k * F1) - floor, 0.) for k in range(2, 11, 2))
    odd = sum(max(_power(freq, spectrum, k * F1) - floor, 0.) for k in range(3, 11, 2))
    return fundamental, even, odd


def analyse(returned):
    """What a chain's return of `test_signal` shows. `returned` is lined up with what was sent."""
    mono = _mono(returned)
    places = layout()
    width, settle = round(WINDOW_S * RATE), round(SETTLE_S * RATE)

    def part(name):
        start = places[name] + settle
        return mono[start:start + width]

    loud_f, loud_even, loud_odd = _harmonics(part("tone"))
    soft_f, soft_even, soft_odd = _harmonics(part("soft"))
    gated = soft_f < loud_f * 10 ** (-(SOFTER_DB + 30) / 10)
    distortion = _db((loud_even + loud_odd) / max(loud_f, 1e-20))
    soft = _db((soft_even + soft_odd) / max(soft_f, 1e-20)) if not gated else -70.
    even_odd = _db(max(loud_even, 1e-20) / max(loud_odd, 1e-20)) if loud_even + loud_odd > loud_f * 1e-7 else 0.

    freq, spectrum = _spectrum(part("pair"))
    floor = _floor(freq, spectrum)
    notes = _power(freq, spectrum, F1) + _power(freq, spectrum, F2)
    products = sum(max(_power(freq, spectrum, at) - floor, 0.) for at in (F2 - F1, 2 * F1 - F2, 2 * F2 - F1, F1 + F2))
    intermod = _db(products / max(notes, 1e-20))

    # Wobble: the held tone's level and pitch in 10 ms frames, on the fundamental alone. Only what swings back
    # and forth counts: a slow rise or fall (a delay or reverb building up) is fitted and taken away first.
    held = part("tone")
    band = signal.sosfiltfilt(signal.butter(4, (F1 * .8, F1 * 1.25), "bandpass", fs=RATE, output="sos"), held)
    analytic = signal.hilbert(band)
    edge = round(.05 * RATE)
    envelope = np.abs(analytic)[edge:-edge]
    level_wobble = pitch_wobble = 0.
    if envelope.size > 4800 and envelope.max() > 1e-6:
        def swing(values):
            frames = values[:len(values) // 480 * 480].reshape(-1, 480).mean(axis=1)
            x = np.arange(len(frames))
            return frames - np.polyval(np.polyfit(x, frames, 2), x)
        level_wobble = float(np.std(swing(envelope)) * 2 * math.sqrt(2) / envelope.mean() * 100)
        inst = (np.diff(np.unwrap(np.angle(analytic))) * RATE / (2 * np.pi))[edge:-edge]
        pitch_wobble = float(np.std(swing(1200 * np.log2(np.clip(inst, 1, None) / F1))))

    # Echoes and decay from the click.
    tail = mono[places["click"]:places["click"] + round(TAIL_S * RATE)]
    energy = tail ** 2
    smooth = np.convolve(energy, np.ones(240) / 240, mode="same")      # 5 ms
    direct = float(smooth[:round(.02 * RATE)].max()) if len(smooth) else 0.
    echo_ms = echo_db = None
    if direct > 0:
        after = smooth[round(.04 * RATE):]
        trend = signal.medfilt(after[::48], 51)                          # the smooth decay, at 1 ms steps
        peaks, _ = signal.find_peaks(after[::48], distance=30)
        for p in peaks:
            # A repeat stands 10 dB above the smooth decay around it, and within 40 dB of the click.
            if after[p * 48] > 10 * max(trend[p], 1e-20) and after[p * 48] > direct * 10 ** (-40 / 10):
                echo_ms = float(40 + p)
                echo_db = round(float(_db(after[p * 48] / direct)), 1)
                break
    schroeder = np.cumsum(energy[::-1])[::-1]
    decay = 0.
    if schroeder[0] > 0:
        curve = 10 * np.log10(np.maximum(schroeder / schroeder[0], 1e-12))
        below5, below25 = np.argmax(curve <= -5), np.argmax(curve <= -25)
        if curve.min() <= -25 and below25 > below5:
            decay = 3 * (below25 - below5) / RATE
        elif curve.min() > -25:
            decay = TAIL_S * 3                                            # still ringing at the end
    return Probe(round(distortion, 2), round(soft, 2), round(even_odd, 2), round(intermod, 2), bool(gated),
                 round(level_wobble, 2), round(pitch_wobble, 2), echo_ms, echo_db, round(decay, 3))
