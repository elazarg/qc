"""Measure which amps, cabs and drives come out upside down against the others.

Run from the repository root: python research/measure_polarity.py [amps] [cabs] [mics]
Two amps blended on parallel paths cancel when one inverts the signal and the
other does not, and the mixer's PHASE switch puts that right. This measures it.
Amps are measured against each other, and so are cabs: which camp is the
upright one is not known, and for blending it does not matter. `mics` tries
every microphone of a dozen cabs.

Drives are not measured. A path with no mono block on it carries the USB input
on its left channel only, so a dry path cannot serve as the reference here.

The model under test sits on the first row and a fixed reference on the second,
panned hard apart, so one pass over USB records both and the unit's own delay
compensation has aligned them. The reference channel then lines up every pass
with every other, which gives the correlation between any two models.

Raw results go to research/measurements/polarity.json and the table the app
uses to lab/tables/polarity.json. Silent in the room; selecting another
preset on the unit stops the run.
"""
import json
from pathlib import Path
import sys
import time

import numpy as np
from scipy import signal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from measure_controls import phrase  # noqa: E402
from tone_lab import audio, device, device_presets, draft_render, iterate, models  # noqa: E402

OUT = ROOT / "research" / "measurements" / "polarity.json"
TABLE = ROOT / "lab" / "tables" / "polarity.json"
REFERENCE_AMP, REFERENCE_CAB, GAIN_BLOCK = 1008, 12001, 16005  # Rols Jazz CH120: light and clean
SECONDS, SLACK = 1.5, 2400
WEAK = .1  # below this the two barely interact, whichever way the switch is set
# How clearly a model must take a side to be listed. Cabs are linear, so one that is only upside down against
# the reference correlates strongly with it. Distorting amps correlate less with a clean reference and are
# also judged by how they sit with all the others.
SURE = {"amps": .15, "cabs": .5, "typical": .2}
GROUPS = {
    # name: (first row, tested cell, second row)
    "amps": ([REFERENCE_AMP, REFERENCE_CAB], 0, [REFERENCE_AMP, REFERENCE_CAB]),
    "cabs": ([REFERENCE_AMP, REFERENCE_CAB], 1, [REFERENCE_AMP, REFERENCE_CAB]),
}


def candidates(group):
    locked = device_presets.unavailable()
    found = [model for model in models.all_models() if model["id"] not in locked]
    if group == "amps":
        return [model["id"] for model in found if model["category"] == "Guitar Amplifier"]
    return [model["id"] for model in found if model["category"] == "Cabsim Guitar (M)"]


def build(qc, upper, lower):
    draft_render.build_grid(qc, device.read_preset(qc), [])
    for column, model in enumerate(upper):
        qc.add_block(model, row=0, column=column)
        time.sleep(.15)
    width = len(upper)
    qc.add_block(GAIN_BLOCK, row=0, column=width)
    time.sleep(.15)
    qc.set_param(0, width, 0, models.to_norm(GAIN_BLOCK, 0, -12.))
    qc.set_split_points(0, 0, width)
    time.sleep(.3)
    for column, model in enumerate(lower):
        qc.add_block(model, row=1, column=column)
        time.sleep(.15)
    qc.set_lane_param(0, "mixer", draft_render.MIXER_PAN_A, 0.)
    qc.set_lane_param(0, "mixer", draft_render.MIXER_PAN_B, 1.)
    time.sleep(.4)


def lag(reference, other):
    """How many samples `other` is behind `reference`, from their shared reference channel."""
    found = signal.correlate(other, reference, "full", "fft")
    return int(np.argmax(found)) - (len(reference) - 1)


def correlation(a, b):
    scale = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
    return float(np.dot(a, b) / scale) if scale else 0.


def measure(qc, offset, dry, group, chosen=None, settings=None):
    """Return {key: (tested channel, reference channel)}, every pass aligned to the first."""
    upper, cell, lower = GROUPS[group]
    build(qc, upper, lower)
    takes, anchor = {}, None
    wanted = chosen or candidates(group)
    for count, model_id in enumerate(wanted):
        if device.live_position(qc) != draft_render.WORK_SLOT:
            raise RuntimeError("The selected preset changed on the Quad Cortex.")
        qc.delete_block(0, cell)
        time.sleep(.12)
        qc.add_block(model_id, row=0, column=cell)
        time.sleep(.45)
        placed = device.read_preset(qc)
        rows = [[model.hash for model in placed.chains[row].models] for row in (0, 1)]
        expected = [model_id if index == cell else model for index, model in enumerate(upper)]
        if rows[0][:len(upper)] != expected or rows[1][:len(lower)] != lower:
            print(f"{models.lookup(model_id)['name']}: the unit would not load it here", flush=True)
            qc.delete_block(0, cell) if rows[0][cell] else None
            time.sleep(.12)
            qc.add_block(upper[cell], row=0, column=cell)
            time.sleep(.3)
            continue
        for label, writes in (settings(model_id) if settings else [("", [])]):
            for index, value in writes:
                qc.set_param(0, cell, index, models.to_norm(model_id, index, value))
                time.sleep(.1)
            time.sleep(.15 if writes else 0)
            raw = audio.reamp(dry, tail=.3)[offset - SLACK:offset + len(dry) + SLACK].astype(np.float64)
            if anchor is None:
                anchor = raw[:, 1].copy()
            shift = lag(anchor, raw[:, 1])
            body = np.roll(raw, -shift, axis=0)[SLACK:-SLACK]
            takes[f"{model_id}{label}"] = (body[:, 0].copy(), body[:, 1].copy())
        if count % 10 == 9:
            print(f"{group}: {count + 1} of {len(wanted)}", flush=True)
    return takes


def summary(takes):
    """Signs that best explain every pair, and how well they do.

    Only the models' polarity against each other is known, and the larger camp
    is called upright.
    """
    keys = list(takes)
    against = {key: round(correlation(*takes[key]), 3) for key in keys}
    matrix = np.array([[correlation(takes[a][0], takes[b][0]) for b in keys] for a in keys])
    vector = np.linalg.eigh(matrix)[1][:, -1]
    signs = np.where(vector < 0, -1, 1)
    if signs.sum() < 0:
        signs = -signs
    strong = np.abs(matrix) >= WEAK
    np.fill_diagonal(strong, False)
    agree = (np.sign(matrix) == np.outer(signs, signs)) & strong
    weakest = {key: round(float(np.median(np.abs(np.delete(matrix[index], index)))), 3)
               for index, key in enumerate(keys)} if len(keys) > 1 else {}
    return {"signs": {key: int(sign) for key, sign in zip(keys, signs)}, "against_reference": against,
            "typical_strength": weakest, "pairs": int(strong.sum() // 2),
            "pairs_explained": int(agree.sum() // 2),
            "weak_pairs": int((~strong).sum() - len(keys)) // 2}


def mic_settings(model_id):
    """Every microphone of a cab at its default position."""
    params = models.lookup(model_id)["params"]
    index = next((i for i, param in enumerate(params) if param["name"].lower() == models.MIC_SELECTOR), None)
    if index is None:
        return [("", [])]
    low, high = int(float(params[index]["min"])), int(float(params[index]["max"]))
    return [(f"|mic {value}", [(index, float(value))]) for value in range(low, high + 1)]


def main(groups):
    take, dry = phrase(SECONDS)
    record = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    with device.connect(write=True) as qc:
        offset = iterate.calibration_offset(qc)
        with draft_render.working_grid(qc):
            for group in groups:
                if group == "mics":
                    cabs = candidates("cabs")
                    takes = measure(qc, offset, dry, "cabs", cabs[::max(len(cabs) // 12, 1)], mic_settings)
                else:
                    takes = measure(qc, offset, dry, group)
                result = summary(takes)
                record[group] = {"take": take, "seconds": SECONDS, **result}
                OUT.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
                signs = result["signs"]
                print(f"{group}: {len(signs)} measured, {sum(sign < 0 for sign in signs.values())} inverted, "
                      f"{result['pairs_explained']} of {result['pairs']} pairs explained, "
                      f"{result['weak_pairs']} weak", flush=True)
    TABLE.write_text(json.dumps(table(record)) + "\n", encoding="utf-8")
    print("finished", flush=True)


def table(record):
    """The models that turn the signal upside down, for tone_lab/measured.py."""
    found = {"firmware": "4.1.1"}
    for group in ("amps", "cabs"):
        result = record.get(group, {})
        against, typical = result.get("against_reference", {}), result.get("typical_strength", {})
        # A model that hardly correlates with the reference or with the rest has no polarity worth acting on.
        sure = {key for key in result.get("signs", {})
                if abs(against[key]) >= SURE[group] or group == "amps" and typical.get(key, 0) >= SURE["typical"]}
        found[group] = {"measured": sorted(int(key) for key in sure),
                        "inverted": sorted(int(key) for key in sure if result["signs"][key] < 0)}
    return found


if __name__ == "__main__":
    main(sys.argv[1:] or ["amps", "cabs"])
