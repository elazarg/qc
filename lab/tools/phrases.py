"""Keeping a recorded take as a phrase: the one way a take becomes a phrase on disk.

A take is what `Engine.record` returns: the dry guitar as it reaches the unit in the first column, and the
processed stereo return in the next two. A phrase is its dry column, kept under `phrases/<id>/` in the data
folder with what was played (`phrase.json`), and beside it the processed return when there was one. A take in
which nothing came in, or which clipped at the input, is not a phrase: it is refused with the reason.

The desk (`Studio.record`) and `python -m lab record` both keep their takes through `keep`. The desk says
what was played in the take (`gesture`), so that pickup settings are compared on takes of the same thing.
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path

import numpy as np

SILENT, CLIPPED = .003, .999      # peaks of the dry input below and above which a take is not kept


class NotKept(ValueError):
    """The take is not worth keeping, for a reason the player can put right."""


def dry_of(take):
    """The dry guitar of a take, as one column, and its peak in dBFS. Refused when nothing came in or it clipped."""
    take = np.asarray(take, dtype=np.float32)
    dry = take[:, :1] if take.ndim == 2 else take[:, None]
    peak = float(np.abs(dry).max()) if len(dry) else 0.
    level = 20 * math.log10(max(peak, 1e-9))
    if peak < SILENT:
        raise NotKept(f"Nothing came in on the guitar input (peak {level:.0f} dBFS). The take was not kept.")
    if peak >= CLIPPED:
        raise NotKept("The take clipped at the input. Turn the input gain down and record again; it was not kept.")
    return dry, level


def keep(folder, take, *, instrument, setting, heard_through=None, gesture=None):
    """Keep a take as a phrase in the data folder `folder`. Returns (its record, the dry audio, its peak in dBFS).

    `heard_through` is the sound the player heard while playing, if the take's processed return is that sound's
    (an object with `id` and `name`); without it a return that is not silent is the preset on the unit's.
    `gesture` says what was played ("open strings strummed once…": `lab.hearing.profile.GESTURES`, or the
    player's own words) and is kept in the record when given.
    """
    from lab.hearing.audio import RATE, write_wave
    from lab.sound.sounds import new_id

    dry, level = dry_of(take)
    take = np.asarray(take, dtype=np.float32)
    phrase_id = new_id()
    target = Path(folder) / "phrases" / phrase_id
    target.mkdir(parents=True)
    write_wave(target / "dry.wav", dry)
    info = {"id": phrase_id, "instrument": instrument, "setting": setting,
            "recorded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seconds": round(len(dry) / RATE, 2), **({"gesture": gesture} if gesture else {})}
    wet = take[:, 1:3] if take.ndim == 2 else take[:, :0]
    if wet.shape[1] == 2 and float(np.abs(wet).max()) > 1e-4:
        write_wave(target / "wet.wav", wet)
        info["wet"] = ({"sound": heard_through.id, "name": heard_through.name} if heard_through is not None
                       else {"sound": None, "name": "the preset on the unit"})
    (target / "phrase.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return info, dry, level
