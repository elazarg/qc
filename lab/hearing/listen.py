"""Listening to a chain on the unit before anyone hears it, and measuring the USB round trip.

Both are silent in the room: the chain is played from the computer and back
over USB, with nothing routed to the unit's outputs.

`calibrate` measures how many samples the unit's return lags behind what was
sent. Every comparison that lines up a dry phrase with its processed return
needs it. It changes with firmware, so it is kept with the firmware it was
measured on and measured again when that changes. It also changes with the
driver's buffer size, which nothing announces: so every listening checks it,
by when the click of the test signals comes back. No chain returns a click
before an empty row does; one that comes back early says the round trip was
measured under other conditions, and the listening is refused
(`RoundTripChanged`) until it is measured again.

One listening is three passes: the phrase, the phrase 12 dB softer, and the
test signals (`lab.hearing.probe`, 10.6 s). Before the second and the third
the chain is given time to fall silent, so that a long delay or reverb does
not ring from one pass into the next: the passes go on when the return is far
enough under the phrase's peak (50 dB before the softer phrase; 75 dB before
the test signals, whose click is one sample and carries little energy), or
has stopped falling (a steady noise), or after MOST_WAIT_S.
"""

import json
from pathlib import Path

import numpy as np

from lab.hearing.audio import RATE, click, onset
from lab.sound.chain import Chain, Row, routed
from lab.hearing import probe
from lab.hearing.measure import SOFTER, heard
from lab.qc.rig import USB

TAIL = 1.5
SOFT_TAIL = .5
SILENT = 1e-4      # a return quieter than this at its peak carried nothing
CLIPPED = .999
EARLY_MS = 1.      # a click this much earlier than the measured round trip allows: the round trip has changed
# Against the phrase's peak: louder than this at the end of a pass, the chain still rings. Before the softer
# phrase, what rings must not add to its level; before the test signals it must stay well under the click's echoes.
RINGS_ABOVE_DB, RINGS_ABOVE_CLICK_DB = -50., -75.
MOST_WAIT_S = 8.


class RoundTripChanged(RuntimeError):
    """The USB round trip is no longer what was measured: measure it again (`calibrate`) and listen again."""


def _peak(audio):
    return float(np.max(np.abs(audio))) if len(audio) else 0.


def usable(audio, what):
    """Refuse a return that cannot be measured: silent (nothing came back) or clipped (it hit full scale). Measured
    anyway it would teach the app, and the player's taste model, something untrue."""
    peak = _peak(audio)
    if peak < SILENT:
        raise RuntimeError(f"Nothing came back from the Quad Cortex for {what}. Check that the chain passes USB 5/6 "
                           "through; nothing was measured.")
    if peak >= CLIPPED:
        raise RuntimeError(f"The return of {what} clipped, so it was not measured. Turn the sound's level down.")


def calibration(folder, firmware):
    """The measured delay for this firmware, or None if it has not been measured (or the file cannot be used)."""
    try:
        found = json.loads((Path(folder) / "calibration.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(found, dict) or found.get("firmware") != firmware:
        return None
    offset = found.get("offset")
    return offset if isinstance(offset, int) and not isinstance(offset, bool) and offset >= 0 else None


def calibrate(rig, audio, folder):
    """Measure the round trip through an empty row and keep it. Returns the delay in samples."""
    empty = Chain((Row(),))
    slot, held = rig.ready(empty)
    rig.connect(held, [USB, (0, 0), (0, 0), (0, 0)])
    delays = [onset(audio.reamp(click(), tail=.3)) for _ in range(3)]
    if max(delays) - min(delays) > 2:
        raise RuntimeError(f"The round trip was not steady: {delays} samples.")
    offset = sorted(delays)[1]
    path = Path(folder) / "calibration.json"
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps({"firmware": rig.unit.firmware, "offset": offset}), encoding="utf-8")
    part.replace(path)
    return offset


def _fall_silent(audio, ending, reference, stop, under_db=RINGS_ABOVE_DB):
    """Give the chain time to stop ringing. `ending` is the end of the last return and `reference` the phrase's
    peak in it. Silence is sent, a second at a time, until the return is `under_db` under the reference, or is
    no longer falling (it is the chain's noise, not a tail), or MOST_WAIT_S has passed. Returns the seconds
    waited."""
    last = round(.1 * RATE)
    floor = max(reference * 10 ** (under_db / 20), SILENT / 100)
    level, waited = _peak(ending[-last:]), 0.
    while level > floor and waited < MOST_WAIT_S:
        returned = audio.reamp(np.zeros((RATE, 1), dtype=np.float32), tail=0., cancel=stop)
        waited += 1.
        now = _peak(returned[-last:])
        if now > level * 10 ** (-3 / 20):      # not even 3 dB lower after a second: steady noise
            break
        level = now
    return waited


def listen(rig, audio, chain, dry, offset, *, stop=None, scene=0):
    """What `chain` does to the mono phrase `dry` in `scene`, and what test signals show of it (`measure.Heard`)."""
    slot, held = rig.ready(chain, stop=stop)
    if scene and chain.scenes_in_use() > 1:
        rig.unit.select_scene(scene)        # recalling starts at the first scene
    # The chain's own routing, fed from the computer and returned to it: a preset spread over several rows is
    # heard whole.
    held = rig.connect(held, routed(chain, *USB).routes)
    rig.unit.guard()
    raw = audio.reamp(dry, tail=TAIL, cancel=stop)
    loud = raw[offset:offset + len(dry)]
    tail = raw[offset + len(dry):]
    rig.unit.guard()
    if len(loud) < len(dry):
        raise RuntimeError("The unit returned less audio than was sent.")
    usable(loud, "the phrase")
    _fall_silent(audio, raw, _peak(loud), stop)
    rig.unit.guard()
    raw = audio.reamp(dry * SOFTER, tail=SOFT_TAIL, cancel=stop)
    soft = raw[offset:offset + len(dry)]
    rig.unit.guard()
    if len(soft) < len(dry):
        raise RuntimeError("The unit returned less audio than was sent.")
    # The phrase came through and the softer one did not: a gate closed on it. That is a finding, not a fault.
    soft_gated = _peak(soft) < SILENT
    if not soft_gated:
        usable(soft, "the softer phrase")
    _fall_silent(audio, raw, _peak(loud), stop, RINGS_ABOVE_CLICK_DB)
    rig.unit.guard()
    # Test signals at the level of a firmly held note in this phrase: distortion and the rest, measured directly.
    tones = probe.test_signal(probe.held_note_peak(dry))
    returned = audio.reamp(tones, tail=.2, cancel=stop)[offset:offset + len(tones)]
    rig.unit.guard()
    if len(returned) < len(tones):
        raise RuntimeError("The unit returned less audio than was sent.")
    usable(returned, "the test tones")
    found = probe.analyse(returned)
    if found.click_ms is not None and found.click_ms < -EARLY_MS:
        raise RoundTripChanged(
            f"The test click came back {-found.click_ms:.1f} ms before the measured USB round trip allows, so the "
            "round trip has changed (another buffer size or driver) and nothing was measured. Measure the USB "
            "round trip again.")
    return heard(dry, loud, soft, tail, probe=found.to_record(), soft_gated=soft_gated)
