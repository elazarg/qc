"""Listening to a chain on the unit before anyone hears it, and measuring the USB round trip.

Both are silent in the room: the chain is played from the computer and back
over USB, with nothing routed to the unit's outputs.

`calibrate` measures how many samples the unit's return lags behind what was
sent. Every comparison that lines up a dry phrase with its processed return
needs it. It changes with firmware, so it is kept with the firmware it was
measured on and measured again when that changes.
"""

import json
from pathlib import Path

from lab.hearing.audio import RATE, click, onset
from lab.sound.chain import Chain, Row, routed
from lab.hearing import probe
from lab.hearing.measure import SOFTER, heard
from lab.qc.rig import USB

TAIL = 1.5


def calibration(folder, firmware):
    """The measured delay for this firmware, or None if it has not been measured."""
    try:
        found = json.loads((Path(folder) / "calibration.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found["offset"] if found.get("firmware") == firmware else None


def calibrate(rig, audio, folder):
    """Measure the round trip through an empty row and keep it. Returns the delay in samples."""
    empty = Chain((Row(),))
    slot, held = rig.ready(empty)
    rig.connect(held, [USB, (0, 0), (0, 0), (0, 0)])
    delays = [onset(audio.reamp(click(), tail=.3)) for _ in range(3)]
    if max(delays) - min(delays) > 2:
        raise RuntimeError(f"The round trip was not steady: {delays} samples.")
    offset = sorted(delays)[1]
    (Path(folder) / "calibration.json").write_text(
        json.dumps({"firmware": rig.unit.firmware, "offset": offset}), encoding="utf-8")
    return offset


def listen(rig, audio, chain, dry, offset, *, stop=None):
    """What `chain` does to the mono phrase `dry`, and what test signals show of it (`measure.Heard`)."""
    slot, held = rig.ready(chain, stop=stop)
    # The chain's own routing, fed from the computer and returned to it: a preset spread over several rows is
    # heard whole.
    held = rig.connect(held, routed(chain, *USB).routes)
    rig.unit.guard()
    raw = audio.reamp(dry, tail=TAIL, cancel=stop)
    loud = raw[offset:offset + len(dry)]
    tail = raw[offset + len(dry):]
    rig.unit.guard()
    soft = audio.reamp(dry * SOFTER, tail=.3, cancel=stop)[offset:offset + len(dry)]
    rig.unit.guard()
    # Test signals at the level of a firmly held note in this phrase: distortion and the rest, measured directly.
    tones = probe.test_signal(probe.held_note_peak(dry))
    returned = audio.reamp(tones, tail=.2, cancel=stop)[offset:offset + len(tones)]
    rig.unit.guard()
    if len(loud) < len(dry) or len(soft) < len(dry) or len(returned) < len(tones):
        raise RuntimeError("The unit returned less audio than was sent.")
    return heard(dry, loud, soft, tail, probe=probe.analyse(returned).to_record())
