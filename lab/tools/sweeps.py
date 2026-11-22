"""Measuring what controls and models do on the unit, with no sound in the room.

A chain is put in the build slot (My Presets 255, see `slots.py`) once, fed
from the computer and returned to it over USB, so nothing reaches the unit's
outputs. Then, for each condition, a few values are written in place, a
recorded phrase is replayed and what comes back is measured, and the values
are put back. One measurement costs the length of the phrase plus a second or
two. Building a chain for every condition (`common.render`, which keeps each
chain in a cache slot) costs a quarter of a minute each, too slow for the
thousands of conditions behind the app's tables.

- `sweep` measures conditions on one chain.
- `sweep_models` measures many models in one cell of a chain: the cell's
  model is exchanged, the rest of the chain stays. A model the unit will not
  load is reported as refused and the run goes on.

What is measured is `measurement`: the level, the crest factor, the level in
each third-octave band and the spectral centre of the return, lined up with
the phrase by the USB round trip, which is measured anew before each run.

Every write goes through `Unit.apply`, so the unit's rules hold here as
everywhere: the selected preset is checked before each write, and each value
is read back. A condition whose values the unit did not take is not measured,
and is reported as skipped. After each condition the unit must hold the chain
as it was built; if it does not, the blocks are added anew once, and the run
fails if that does not put it right.

The player's preset is given back as `named.harvest` gives it back: by the
session when one is open, otherwise by borrowing it for the run. A stop is
honoured between two conditions and during a replay. However a run ends, the
inputs are taken off the build slot first.

These are operations, run where the unit is held:
`host.call("lab.tools.sweeps.sweep_models", chain, cell, candidates, dry)`.
`research/measure_amps.py`, `measure_controls.py` and `measure_polarity.py`
are made of them.
"""

from contextlib import contextmanager, nullcontext
from dataclasses import replace
import time

import numpy as np

from lab.hearing.audio import RATE, Cancelled
from lab.hearing.measure import measure
from lab.qc.rig import USB, held_silent
from lab.qc.slots import BUILD, BUILD_NAME
from lab.qc.unit import Mismatch, Refused, Stopped, same_slot
from lab.sound import catalog
from lab.sound.chain import INTERNAL, ROWS, Block, routed

GLIDE_S = .2          # between the last value written and the replay: the unit glides to a new value
MUTED_S = .45         # after a block is exchanged: adding or removing one mutes all audio for a moment
SILENCE_DB = -90.     # a first return under this is no measurement: the chain is not passing USB audio
EVERY_ROW = tuple(range(ROWS))      # all on USB or joined inside the preset, so blocks may change while connected


def measurement(samples):
    """What the analyses read of a return, as `lab.hearing.measure.measure` measures it: the RMS level, the crest
    factor (peak over RMS), the spectral centre, and the level in each third-octave band of `measure.CENTRES`."""
    found = measure(samples)
    return {"rms_db": round(found.rms_db, 2), "crest_db": round(found.crest_db, 2),
            "centroid_hz": round(found.centroid_hz), "bands_db": list(found.bands_db)}


# ---- the chain on the build slot ----

def _leave(engine):
    """Take every input off what the run put on the unit, if the unit is still there. A player who chose another
    preset meanwhile has discarded the build with its routes, and that preset is not touched."""
    unit = engine.unit
    engine.shown = None
    if unit.slot is not None and engine.slots.scratch(unit.slot) and same_slot(unit.position(), unit.slot):
        held = unit.read()
        if any(row.inlet not in INTERNAL for row in held.rows):
            unit.apply(held.with_routes([(row.inlet if row.inlet in INTERNAL else 0, row.outlet)
                                         for row in held.rows]))
    engine.connected = False


@contextmanager
def _built(engine, chain, stop):
    """`chain` on the build slot, added afresh, fed from USB and returned to USB: what the unit then holds, every
    value stated. On the way out the inputs are taken off, and the player's preset is given back unless an open
    session will do that."""
    unit, slots = engine.unit, engine.slots
    engine.quiet()
    if engine.borrowed is not None:
        slots.may_leave()               # `borrowed` asks the same itself
    with slots.borrowed() if engine.borrowed is None else nullcontext():
        failed = True
        try:
            engine.measure_round_trip()         # through an empty row: it also shows that USB audio passes
            slots.refresh()                     # the player may have saved a preset into the build slot since
            slots.claim(BUILD, BUILD_NAME)
            unit.slot = None
            unit.recall(BUILD)
            unit.slot = BUILD
            held = unit.apply(held_silent(chain), stop=stop, fresh=True)
            engine.connected = True             # from the first route set: `Engine.quiet` then knows of it too
            yield unit.apply(held.with_routes(routed(chain, *USB).routes), stop=stop)
            failed = False
        finally:
            try:
                _leave(engine)
            except Exception:  # noqa: BLE001
                if not failed:
                    raise                       # after a failure, that failure is the one to tell
            # The player's preset is given back next, by `borrowed` here or by the session when it closes.


def _go_on(stop):
    if stop is not None and stop.is_set():
        raise Stopped("Stopped.")


def _phrase(dry):
    dry = np.asarray(dry, dtype=np.float32)
    dry = dry[:, None] if dry.ndim == 1 else dry[:, :1]
    if not len(dry):
        raise ValueError("The phrase is empty.")
    return dry


# ---- one condition ----

def _with_values(held, settings):
    """`held` with each block named in `settings` stating those values alone, as the unit stores them.

    `settings` is [(row, column, parameter index, value)], a value in display units or text. Stating only what
    the condition changes makes `Unit.apply` write and confirm those values and pass over any other value the
    unit moves along with them; putting the whole chain back afterwards covers those."""
    stated = {}
    for row, column, index, value in settings:
        block = held.block(row, column)
        if block is None:
            raise ValueError(f"Row {row + 1}, column {column + 1} holds no block to set a value on.")
        stated.setdefault((row, column), {})[int(index)] = (
            value if isinstance(value, str) else catalog.to_norm(block.model, int(index), value))
    wanted = held
    for (row, column), values in stated.items():
        wanted = wanted.with_block(row, column, replace(held.block(row, column), params=values))
    return wanted


def _put_back(unit, base, now):
    """Make the unit hold `base` again, every value of it. `now` is what it holds, or None when that is not
    known. When writing the values back does not do it, every block is added anew, once."""
    try:
        unit.apply(base, current=now)
    except Mismatch:
        unit.guard()
        unit.apply(base, fresh=True, sounding=EVERY_ROW)
        unit.sleep(MUTED_S)


def _replay(engine, dry, tail, margin, stop):
    """Play the phrase through what the unit holds and return what came back, lined up with the phrase, with
    `margin` samples kept before and after it (silence where the recording does not reach back that far)."""
    unit, offset = engine.unit, engine.offset
    unit.guard()
    # Long enough after the phrase for the round trip's delay and the margin to be taken off.
    raw = engine.audio.reamp(dry, tail=max(tail, (offset + margin) / RATE + .05), cancel=stop)
    unit.guard()            # another preset chosen during the replay: what came back is not this chain's
    start, wanted = offset - margin, len(dry) + 2 * margin
    body = np.asarray(raw[max(start, 0):offset + len(dry) + margin], dtype=np.float32)
    if start < 0:
        body = np.concatenate([np.zeros((-start, body.shape[1]), dtype=np.float32), body])
    if len(body) < wanted:
        raise RuntimeError("The unit returned less audio than was sent.")
    return body


def _measured(engine, base, label, settings, dry, *, tail, margin, audio, stop):
    """One condition on the chain the unit holds (`base`): its values written in place, the phrase replayed, the
    chain put back. Returns the condition's row, or None when the unit did not take the values."""
    unit = engine.unit
    started, now, samples = time.monotonic(), base, None
    try:
        if settings:
            try:
                now = unit.apply(_with_values(base, settings), current=base)
            except Mismatch:
                unit.guard()            # another preset was chosen: that is not a value the unit would not take
                now = None
        if now is not None:
            unit.sleep(GLIDE_S)
            samples = _replay(engine, dry, tail, margin, stop)
    finally:
        if settings:
            _put_back(unit, base, now)
    if samples is None:
        return None
    row = {"label": label, **measurement(samples[margin:len(samples) - margin]),
           "seconds": round(time.monotonic() - started, 1)}
    if audio:
        row["audio"] = samples
    return row


def _not_silent(row, what):
    """Refuse to go on when the first return of a chain is silence: every measurement after it would be too."""
    if row["rms_db"] < SILENCE_DB:
        raise RuntimeError(f"The Quad Cortex returned silence for {what}. Check that it passes audio over USB; "
                           "nothing more was measured.")


# ---- the operations ----

def sweep(engine, chain, conditions, dry, *, tail=2., margin=0, audio=False, stop=None, progress=None):
    """Measure each condition on one chain, built once in the build slot and heard over USB only.

    `chain` is a `lab.sound.chain.Chain`; its routing is replaced by USB in and out. `conditions` is
    [(label, [(row, column, parameter index, value), ...])], rows and columns from 0, a value in display
    units or text such as a microphone's name; a condition without values measures the chain as built. `dry`
    is the mono phrase and `tail` the seconds recorded after it.

    Returns {"firmware", "rows", "skipped", "stopped"}. A row is {"label", **`measurement`, "seconds"}, the
    seconds the condition took; with `audio` it also holds the return itself under "audio", stereo, lined up
    with the phrase, with `margin` samples kept either side. "skipped" are the labels of the conditions whose
    values the unit did not take. `progress` hears of each as {"row": ...} or {"skipped": label}.
    """
    say = progress or (lambda event: None)
    dry = _phrase(dry)
    rows, skipped, stopped = [], [], False
    try:
        with _built(engine, chain, stop) as base:
            for label, settings in conditions:
                _go_on(stop)
                row = _measured(engine, base, label, settings, dry, tail=tail, margin=margin, audio=audio,
                                stop=stop)
                if row is None:
                    skipped.append(label)
                    say({"skipped": label})
                    continue
                if not rows:
                    _not_silent(row, "the chain")
                rows.append(row)
                say({"row": row})
    except (Stopped, Cancelled):
        stopped = True
    return {"firmware": engine.unit.firmware, "rows": rows, "skipped": skipped, "stopped": stopped}


def _exchanged(unit, held, cell, model_id, in_cell, stop):
    """What the unit holds once `cell` holds `model_id`, added anew with the unit's own values, or None when the
    unit will not load it. Asked twice: a slow answer is not a refusal, and a refusal is put on record. A cell
    that holds the model already (`in_cell`) is emptied first, so that no value the chain gave its stand-in is
    measured as the model's own."""
    if in_cell == model_id:
        unit.apply(held.with_block(*cell, None), stop=stop, sounding=EVERY_ROW)
    wanted = held.with_block(*cell, Block(model_id))
    for _ in range(2):
        try:
            return unit.apply(wanted, stop=stop, sounding=EVERY_ROW)
        except Refused:
            continue
    return None


def sweep_models(engine, chain, cell, candidates, dry, *, tail=.4, margin=0, audio=False, stop=None,
                 progress=None):
    """Measure many models in one cell of a chain that is built once in the build slot and heard over USB only.

    `cell` is (row, column) of the block of `chain` that stands in for the models. `candidates` is
    [(model id, conditions)], with conditions as in `sweep` but on the model itself:
    [(label, [(parameter index, value), ...])]. Each model is added anew, measured in each condition, and
    exchanged for the next.

    `progress` hears of each model as it is finished, so that a long run can keep what it has:
    {"model", "name", "rows", "skipped", "firmware"}, with rows as in `sweep`, or None for a model the unit
    would not load. A model whose conditions were cut short by a stop is not reported. Returns
    {"firmware", "measured", "refused", "left", "stopped"}: the model ids measured and refused, and how many
    were not reached.
    """
    say = progress or (lambda event: None)
    dry = _phrase(dry)
    row, column = cell
    if chain.block(row, column) is None:
        raise ValueError("The cell to exchange holds no block in the chain.")
    unit = engine.unit
    measured, refused, stopped = [], [], False
    try:
        with _built(engine, chain, stop) as held:
            in_cell = chain.block(row, column).model    # the model the unit's cell holds, None when unknown
            for model_id, conditions in candidates:
                _go_on(stop)
                info = catalog.model(model_id)
                name = info.name if info else f"model {model_id}"
                base = _exchanged(unit, held, (row, column), model_id, in_cell, stop)
                in_cell = model_id if base is not None else None
                if base is None:
                    refused.append(model_id)
                    say({"model": model_id, "name": name, "rows": None, "skipped": [], "firmware": unit.firmware})
                    continue
                unit.sleep(MUTED_S)
                rows, skipped = [], []
                for label, settings in conditions:
                    _go_on(stop)
                    found = _measured(engine, base, label, [(row, column, index, value) for index, value in settings],
                                      dry, tail=tail, margin=margin, audio=audio, stop=stop)
                    if found is None:
                        skipped.append(label)
                        continue
                    if not rows:
                        _not_silent(found, name)
                    rows.append(found)
                measured.append(model_id)
                say({"model": model_id, "name": name, "rows": rows, "skipped": skipped, "firmware": unit.firmware})
    except (Stopped, Cancelled):
        stopped = True
    return {"firmware": unit.firmware, "measured": measured, "refused": refused,
            "left": len(candidates) - len(measured) - len(refused), "stopped": stopped}
