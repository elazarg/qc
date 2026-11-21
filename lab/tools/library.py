"""What the desk's Sounds panel does with a preset saved on the Quad Cortex, beside working on it.

The panel lists the presets (`lab.qc.library`) and filters them with `filtered`. For the one chosen:

- **Use as reference** plays the desk's phrase through it silently, in a chosen scene (`capture`), and keeps the
  return under `references/` in the data folder (`keep_reference`), for Tone search to match against.
- **Leave it on the unit** selects it and makes it the preset the app gives back when it closes (`leave`). The
  engine borrows the player's preset for the session and selects it again at the end, so leaving another one
  selected means changing what is given back. A preset the player is editing on the unit is not recalled over.

`capture` and `leave` are operations, run where the unit is held.
"""

from datetime import datetime, timezone
import json
from uuid import uuid4

from lab.qc.library import place
from lab.tools.common import render


def text_of(row):
    return f"{row['name']} {row.get('setlist', '')} {place(int(row['slot']['position']))}"


def filtered(rows, query):
    """Rows whose name, setlist and slot (as the unit shows it, "12B") hold every word of the query."""
    words = query.casefold().split()
    return [row for row in rows if all(word in text_of(row).casefold() for word in words)]


# ---- operations, run where the unit is held ----

def capture(engine, slot, dry, scene=None, *, stop=None):
    """A saved preset's return for the phrase, in a scene or its own default, played silently."""
    chain, name = engine.read(slot)
    wet = render(engine, chain, dry, scene=scene, tail=1., stop=stop)
    return {"name": name, "wet": wet, "digest": chain.digest()}


def leave(engine, slot, *, stop=None):
    """Select a saved preset and make it the one given back when the app closes.

    As everywhere the app moves the unit: a preset with unsaved edits (one the player chose and changed on the
    unit meanwhile) is not recalled over."""
    engine.quiet()
    engine.slots.may_leave()
    engine.unit.slot = None
    engine.unit.recall(slot)
    record = {"position": engine.unit.position(), "scene": engine.unit.scene(), "name": engine.unit.name()}
    engine.borrowed = record
    engine.slots.marker.write_text(json.dumps(record), encoding="utf-8")
    engine.shown = None
    return record


# ---- on the desk's side ----

def keep_reference(folder, row, scene, captured, phrase_name):
    """Keep a captured return, with what it is of, where Tone search finds references."""
    from lab.hearing.audio import write_wave

    stamp = datetime.now(timezone.utc)
    target = folder / "references" / f"{stamp:%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
    target.mkdir(parents=True)
    write_wave(target / "wet.wav", captured["wet"])
    record = {"name": captured["name"], "setlist": row.get("setlist", ""), "slot": row["slot"],
              "scene": scene, "phrase": phrase_name, "digest": captured["digest"],
              "created": stamp.isoformat(timespec="seconds")}
    (target / "reference.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return target
