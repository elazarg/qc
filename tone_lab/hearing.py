"""Listen to a draft before the player does: build it silently and say what came out.

A designer writes settings without hearing them. This replays a recorded dry
phrase through the built chain over USB, twice, and reduces the result to a
few facts that can be compared with what the request implies.

The phrase is played at its own level and again 12 dB quieter. A chain that
adds no distortion or compression returns the second pass 12 dB quieter too.
A saturated one returns it almost as loud, because it was already flat out.
That difference is independent of the phrase, of equalization, and of delay
and reverb, which follow level exactly.

Run from the repository root: python -m tone_lab.hearing <draft id> [...]
"""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys

import numpy as np
from scipy import signal

from . import audio, device, draft_render, iterate, session

STEP_DB = 12.
SOFTER = 10 ** (-STEP_DB / 20)
TAIL = 1.5
# Where the words change, in the share of the 12 dB step that the chain swallowed. Set from chains measured on
# the unit: see TONE_KNOWLEDGE.md, "Listening to a draft".
DRIVE = ((.82, "saturated"), (.62, "overdriven"), (.40, "crunch"), (.18, "edge of breakup"), (0., "clean"))
BRIGHTNESS = ((.45, "bright"), (-.25, "balanced"), (-9., "dark"))  # Octaves against the dry guitar.
# The tail against the phrase, dB. A 9% room measured -57, light delay and plate -46, a lead delay at 21% -26.
AMBIENCE = ((-24., "wet"), (-40., "ambient"), (-70., "a touch of room"), (-999., "dry"))


@dataclass(frozen=True)
class Heard:
    squash: float      # 0 when the output follows the playing level exactly, 1 when it does not follow it at all
    crest_db: float    # peaks over average: low when the tops are clipped flat
    brightness: float  # octaves the spectral centre sits above the dry guitar's
    low_end_db: float  # the share of energy below 200 Hz, against the dry guitar's
    ambience_db: float  # what still sounds after the phrase stops, against the phrase
    level_db: float

    @property
    def drive(self):
        return next(word for floor, word in DRIVE if self.squash >= floor)

    @property
    def tone(self):
        return next(word for floor, word in BRIGHTNESS if self.brightness >= floor)

    @property
    def space(self):
        return next(word for floor, word in AMBIENCE if self.ambience_db >= floor)

    def said(self):
        return f"{self.drive}, {self.tone}, {self.space}"

    def to_record(self):
        return {**{key: round(value, 2) for key, value in asdict(self).items()},
                "drive": self.drive, "tone": self.tone, "space": self.space}


def _level(samples):
    return audio.db(float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) + 1e-10)


def _spectrum(samples):
    mono = np.asarray(samples, dtype=np.float64)
    mono = mono.mean(axis=1) if mono.ndim == 2 else mono
    freq, power = signal.welch(mono, audio.RATE, nperseg=8192)
    heard = (freq >= 60) & (freq <= 12000)
    total = max(float(power[heard].sum()), 1e-30)
    centre = float((freq[heard] * power[heard]).sum() / total)
    lows = 10 * np.log10(max(float(power[heard & (freq < 200)].sum()), 1e-30) / total)
    return centre, lows


def describe(dry, loud, soft, tail):
    """What a chain did to a phrase, from its return at full level, 12 dB quieter, and after the phrase ended."""
    mono = loud.mean(axis=1) if loud.ndim == 2 else loud
    step = _level(loud) - _level(soft)
    centre, lows = _spectrum(loud)
    dry_centre, dry_lows = _spectrum(dry)
    peak = audio.db(float(np.max(np.abs(mono))) + 1e-10)
    return Heard(squash=float(min(1., max(0., 1 - step / STEP_DB))), crest_db=peak - _level(mono),
                 brightness=float(np.log2(max(centre, 1.) / max(dry_centre, 1.))), low_end_db=lows - dry_lows,
                 ambience_db=_level(tail) - _level(loud), level_db=_level(loud))


def proof(qc, dry, offset):
    """Listen to whatever chain is on the unit's USB path now."""
    if np.max(np.abs(dry)) < 1e-4:
        raise ValueError("The phrase is silent.")
    raw = audio.reamp(dry, tail=TAIL)
    loud = raw[offset:offset + len(dry)].copy()
    # Let the repeats of the phrase's last note pass, then listen to what is left.
    tail = raw[offset + len(dry) + audio.RATE // 4:].copy()
    soft = audio.reamp(dry * SOFTER, tail=.3)[offset:offset + len(dry)].copy()
    if _level(loud) < -90:
        raise RuntimeError("The draft returned silence.")
    return describe(dry, loud, soft, tail)


def phrase(seconds=3.5):
    """The loudest stretch of the newest take, as mono dry audio."""
    length = round(seconds * audio.RATE)
    for take in sorted((session.SESSIONS / "recordings").glob("clip-*"), reverse=True):
        try:
            dry = audio.read_wave(take / "dry.wav")
        except (OSError, ValueError):
            continue
        if len(dry) <= length:
            continue
        energy = np.concatenate(([0.], np.cumsum(np.square(dry[:, 0], dtype=np.float64))))
        start = int(np.argmax(energy[length:] - energy[:-length]))
        return take.name, dry[start:start + length].copy()
    raise RuntimeError("Record a phrase first.")


def listen(drafts, dry=None, *, values=None, report=None):
    """Build each draft in the working slot, silently, and return what was heard of it, in order.

    `values` are Shape knob settings to apply to every draft before listening.
    """
    from . import live

    dry = phrase()[1] if dry is None else dry
    found = []
    with device.connect(write=True) as qc:
        offset = iterate.calibration_offset(qc)
        with draft_render.working_grid(qc):
            for draft in drafts:
                chain = draft_render.compile_chain(draft, draft_render.PREVIEW_GAIN)
                base = draft_render.build_grid(qc, device.read_preset(qc), chain, draft)
                if values:
                    live.update(qc, base, 0, values, {}, position=draft_render.WORK_SLOT)
                found.append(proof(qc, dry, offset))
                if report:
                    report(draft, found[-1])
    return found


def main(arguments):
    drafts = [json.loads((session.SESSIONS / "drafts" / f"{Path(name).stem}.json").read_text(encoding="utf-8"))
              for name in arguments]
    listen(drafts, report=lambda draft, heard: print(
        f"{draft['name']:22} {heard.said():42} squash {heard.squash:.2f}  crest {heard.crest_db:5.1f} dB  "
        f"brightness {heard.brightness:+.2f} oct  lows {heard.low_end_db:+5.1f} dB  tail {heard.ambience_db:6.1f} dB",
        flush=True))


if __name__ == "__main__":
    main(sys.argv[1:])
