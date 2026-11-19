"""Listen to a draft before the player does: build it silently and say what came out.

A designer writes settings without hearing them. This replays a recorded dry
phrase through the built chain over USB, twice, and reduces the result to a
few facts that can be compared with what the request implies.

The phrase is played at its own level and again 12 dB quieter. A chain that
adds no distortion or compression returns the second pass 12 dB quieter too.
A saturated one returns it almost as loud, because it was already flat out.
That difference is independent of the phrase, of equalization, and of delay
and reverb, which follow level exactly. A compressor swallows level as well,
without flattening the peaks, so a chain whose peaks still stand tall is never
called more than the edge of breakup.

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
EARLY = (.04, .40)   # Seconds after the phrase stops: a short echo or a small room sounds here.
LATE = (.40, 1.5)    # A long delay or reverb still sounds here.
# Where the words change, in the share of the 12 dB step that the chain swallowed. Set from chains measured on
# the unit: see TONE_KNOWLEDGE.md, "Listening to a draft".
DRIVE = ((.82, "saturated"), (.62, "overdriven"), (.40, "crunch"), (.18, "edge of breakup"), (0., "clean"))
PEAKS_INTACT = 15.   # dB of peak over average. Above this the chain is squeezing level, not clipping.
BRIGHTNESS = ((.45, "bright"), (-.25, "balanced"), (-9., "dark"))  # Octaves against the dry guitar.
# The louder of the two tails against the phrase, dB. Measured on the unit: chains with no delay or reverb -45
# to -72, a room at 9 to 11% -35 to -37, light delay and plate -32, a lead delay at 21% -15 to -17.
AMBIENCE = ((-22., "wet"), (-34., "ambient"), (-42., "a touch of room"), (-999., "dry"))


class Moved(RuntimeError):
    """The unit is no longer on the chain being listened to."""


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
        word = next(word for floor, word in DRIVE if self.squash >= floor)
        if self.crest_db >= PEAKS_INTACT and word not in ("clean", "edge of breakup"):
            return "edge of breakup"  # Level is being held down, but nothing is clipping.
        return word

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


def _channels(samples):
    samples = np.asarray(samples, dtype=np.float64)
    return samples[:, None] if samples.ndim == 1 else samples


def _level(samples):
    """The level of all channels together. Channels are never added to each other, which could cancel them."""
    return audio.db(float(np.sqrt(np.mean(np.square(_channels(samples))))) + 1e-10)


def _crest(samples):
    sides = _channels(samples)
    loudest = sides[:, int(np.argmax(np.mean(np.square(sides), axis=0)))]
    return audio.db(float(np.max(np.abs(loudest))) + 1e-10) - _level(loudest)


def _spectrum(samples):
    sides = _channels(samples)
    freq, power = signal.welch(sides, audio.RATE, nperseg=8192, axis=0)
    power = power.sum(axis=1)
    heard = (freq >= 60) & (freq <= 12000)
    total = max(float(power[heard].sum()), 1e-30)
    centre = float((freq[heard] * power[heard]).sum() / total)
    lows = 10 * np.log10(max(float(power[heard & (freq < 200)].sum()), 1e-30) / total)
    return centre, lows


def describe(dry, loud, soft, tail):
    """What a chain did to a phrase, from its return at full level, 12 dB quieter, and after the phrase ended.

    `tail` starts where the phrase stops.
    """
    step = _level(loud) - _level(soft)
    centre, lows = _spectrum(loud)
    dry_centre, dry_lows = _spectrum(dry)
    tail = _channels(tail)
    windows = [tail[round(start * audio.RATE):round(end * audio.RATE)] for start, end in (EARLY, LATE)]
    rings = max((_level(window) for window in windows if len(window)), default=-200.)
    return Heard(squash=float(min(1., max(0., 1 - step / STEP_DB))), crest_db=_crest(loud),
                 brightness=float(np.log2(max(centre, 1.) / max(dry_centre, 1.))), low_end_db=lows - dry_lows,
                 ambience_db=rings - _level(loud), level_db=_level(loud))


def on_the_draft(qc):
    """Refuse to go on if the unit has left the working slot, its first scene or its USB routing."""
    if (device.live_position(qc) != draft_render.WORK_SLOT or device.active_scene(qc) != 0
            or iterate.routes(device.read_preset(qc)) != iterate.USB_ROUTES):
        raise Moved("The Quad Cortex left the draft that was being listened to.")


def proof(qc, dry, offset, *, cancel=None):
    """Listen to whatever chain is on the unit's USB path now.

    The unit is checked before each pass and before the result is accepted, so
    that nothing is played into a preset the player has switched to and
    nothing measured there is believed. `cancel` ends a pass at once.
    """
    if np.max(np.abs(dry)) < 1e-4:
        raise ValueError("The phrase is silent.")
    on_the_draft(qc)
    raw = audio.reamp(dry, tail=TAIL, cancel=cancel)
    on_the_draft(qc)
    loud = raw[offset:offset + len(dry)].copy()
    tail = raw[offset + len(dry):].copy()
    soft = audio.reamp(dry * SOFTER, tail=.3, cancel=cancel)[offset:offset + len(dry)].copy()
    on_the_draft(qc)
    if _level(loud) < -90:
        raise RuntimeError("The draft returned silence.")
    return describe(dry, loud, soft, tail)


def phrase(take=None, seconds=3.5):
    """The loudest stretch of a take, as (take name, mono dry audio). Without a name, the newest take long enough."""
    length = round(seconds * audio.RATE)
    folder = session.SESSIONS / "recordings"
    takes = [folder / take] if take else sorted(folder.glob("clip-*"), reverse=True)
    for path in takes:
        try:
            dry = audio.read_wave(path / "dry.wav")
        except (OSError, ValueError):
            continue
        if len(dry) <= length:
            continue
        energy = np.concatenate(([0.], np.cumsum(np.square(dry[:, 0], dtype=np.float64))))
        start = int(np.argmax(energy[length:] - energy[:-length]))
        return path.name, dry[start:start + length].copy()
    raise RuntimeError(f"The phrase {take} cannot be used for listening." if take else "Record a phrase first.")


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
