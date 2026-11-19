"""Measure what a control does on the Quad Cortex, with no sound in the room.

The chain is built once in the working slot with USB-only routing. Each
condition then sets a few parameters and replays the same dry phrase, so one
measurement costs the length of the phrase plus about two seconds.
"""

import math
import time

import numpy as np
from scipy import signal

from . import audio, device, draft_render, iterate, models

# Third-octave centres from 50 Hz to 12.5 kHz.
CENTRES = tuple(round(1000 * 2 ** (step / 3), 1) for step in range(-13, 12))


def spectrum(samples):
    """Third-octave levels in dB, without normalising the overall level."""
    freq, power = signal.welch(samples.mean(axis=1), audio.RATE, nperseg=8192)
    bands = []
    for centre in CENTRES:
        low, high = centre * 2 ** (-1 / 6), centre * 2 ** (1 / 6)
        bands.append(10 * math.log10(max(float(power[(freq >= low) & (freq < high)].sum()), 1e-20)))
    return bands


def describe(samples):
    mono = samples.mean(axis=1)
    rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
    peak = float(np.max(np.abs(mono)))
    freq, power = signal.welch(mono, audio.RATE, nperseg=8192)
    audible = (freq >= 60) & (freq <= 12000)
    centroid = float(np.sum(freq[audible] * power[audible]) / max(np.sum(power[audible]), 1e-20))
    return {"rms_db": round(audio.db(rms), 2), "crest_db": round(audio.db(peak) - audio.db(rms), 2),
            "centroid_hz": round(centroid), "bands_db": [round(value, 2) for value in spectrum(samples)]}


def _write(qc, column, index, value, model_id):
    if isinstance(value, str):
        qc.set_param_typed(0, column, index, [[None, None, value]])
    else:
        qc.set_param(0, column, index, models.to_norm(model_id, index, value))
    time.sleep(.07)


def sweep(chain, conditions, dry, *, draft=None, report=print):
    """Measure each condition on one built chain.

    `chain` is [(model_id, [(index, display value), ...]), ...] as the draft
    renderer compiles it. `conditions` is [(label, [(column, index, value), ...]), ...]
    where a value is a display number or a string such as a microphone name.
    Every touched parameter returns to its built value before the next condition.
    """
    results = []
    with device.connect(write=True) as qc:
        offset = iterate.calibration_offset(qc)
        with draft_render.working_grid(qc) as scratch:
            base = draft_render.build_grid(qc, scratch, chain, draft)
            blocks = base.chains[0].models
            for label, settings in conditions:
                if device.live_position(qc) != draft_render.WORK_SLOT:
                    raise RuntimeError("The selected preset changed on the Quad Cortex.")
                started = time.monotonic()
                restore = []
                for column, index, value in settings:
                    stored = blocks[column].params[index].param_values[0]
                    kind = stored.WhichOneof("value")
                    restore.append((column, index, getattr(stored, kind), kind))
                    _write(qc, column, index, value, blocks[column].hash)
                time.sleep(.25)
                samples = audio.reamp(dry)[offset:offset + len(dry)].copy()
                for column, index, value, kind in restore:
                    if kind == "string_value":
                        qc.set_param_typed(0, column, index, [[None, None, value]])
                    else:
                        qc.set_param(0, column, index, value)
                    time.sleep(.07)
                facts = {"label": label, **describe(samples), "seconds": round(time.monotonic() - started, 1)}
                results.append(facts)
                if report:
                    report(f"{label}: {facts['rms_db']} dB, centroid {facts['centroid_hz']} Hz, {facts['seconds']} s")
    return results


def gap(first, second):
    """Second minus first, per band, with the overall level difference removed."""
    bands = np.subtract(second["bands_db"], first["bands_db"])
    return [round(float(value), 2) for value in bands - np.mean(bands)]


def sweep_models(column, candidates, chain, dry, done, *, tail=.4, report=print, stop=None):
    """Measure many models in one cell of a chain that is built once.

    `candidates` is [(model_id, conditions)], with conditions as in `sweep` but
    without the column: [(label, [(index, display value), ...]), ...]. After
    each model `done(model_id, rows)` is called, so a long run can be resumed.
    A model the unit will not load is reported to `done` with None.
    """
    with device.connect(write=True) as qc:
        offset = iterate.calibration_offset(qc)
        with draft_render.working_grid(qc) as scratch:
            draft_render.build_grid(qc, scratch, chain)
            for model_id, conditions in candidates:
                if stop is not None and stop():
                    break
                if device.live_position(qc) != draft_render.WORK_SLOT:
                    raise RuntimeError("The selected preset changed on the Quad Cortex.")
                qc.delete_block(0, column)
                time.sleep(.12)
                qc.add_block(model_id, row=0, column=column)
                time.sleep(.45)
                grid = device.read_preset(qc)
                placed = [model.hash for model in grid.chains[0].models]
                expected = [model_id if index == column else chain[index][0] for index in range(len(chain))]
                if placed[:len(chain)] != expected:
                    done(model_id, None)
                    # Put the cell back so the next model starts from a known grid.
                    if placed[column]:
                        qc.delete_block(0, column)
                        time.sleep(.12)
                    qc.add_block(chain[column][0], row=0, column=column)
                    time.sleep(.3)
                    continue
                block = grid.chains[0].models[column]
                rows = []
                for label, settings in conditions:
                    started = time.monotonic()
                    stored = [block.params[index].param_values[0] for index, _ in settings]
                    if any(value.WhichOneof("value") != "float_value" for value in stored):
                        continue  # Not a plain number on this unit, so it could not be put back.
                    restore = []
                    for (index, value), before in zip(settings, stored):
                        restore.append((index, before.float_value))
                        _write(qc, column, index, value, model_id)
                    time.sleep(.2)
                    samples = audio.reamp(dry, tail=tail)[offset:offset + len(dry)].copy()
                    for index, value in restore:
                        qc.set_param(0, column, index, value)
                        time.sleep(.07)
                    rows.append({"label": label, **describe(samples), "seconds": round(time.monotonic() - started, 1)})
                if rows and rows[0]["rms_db"] < -90:
                    raise RuntimeError("The Quad Cortex returned silence. Check that it passes audio over USB.")
                done(model_id, rows)
                if report:
                    report(f"{models.lookup(model_id)['name']}: {len(rows)} conditions, "
                           f"{sum(row['seconds'] for row in rows):.0f} s")
