"""Measure which amps, cabs and drives come out upside down against the others.

Run from the repository root:
    .venv\\Scripts\\python.exe research\\measure_polarity.py [amps] [cabs] [mics] [model ids] [--limit N]
                                                           [--phrase WAV] [--instrument NAME] [--out FOLDER]
                                                           [--simulated]

WITHOUT --simulated THIS DRIVES THE REAL QUAD CORTEX. Run it with the desk closed (the desk holds the unit, and
the run is refused while it is open) and with the preset on the unit saved. Nothing is heard in the room: the
phrase is replayed through the app's build slot over USB. The preset that was selected is given back at the
end. Ctrl+C stops at the next safe point, and so does choosing another preset on the unit.

Two amps blended on parallel paths cancel when one inverts the signal and the
other does not, and the mixer's PHASE switch puts that right. This measures it.
Amps are measured against each other, and so are cabs: which camp is the
upright one is not known, and for blending it does not matter. `mics` tries
six microphones on each of a dozen cabs.

Drives are not measured. A path with no mono block on it carries the USB input
on its left channel only, so a dry path cannot serve as the reference here.

The model under test sits on the first row and a fixed reference on the second,
panned hard apart, so one pass over USB records both and the unit's own delay
compensation has aligned them. The reference channel then lines up every pass
with every other, which gives the correlation between any two models.

Raw results go to research/measurements/polarity.json and the table the app
uses to lab/tables/polarity.json (with --out, to polarity.json and
tables/polarity.json in that folder). The
table is written when a group of its own, amps or cabs, was finished in this
run, and keeps the other group as it was.

A group needs every pass to be judged, so while a group is unfinished its
passes are kept in the data folder (polarity-passes/, about 0.6 MB a pass: the
player's own playing, so not in the repository). A run that was stopped, or
ended by --limit, goes on from them when it is run again on the same phrase
and firmware; they are removed when the group is written. Model ids measure
those models alone, for a trial: the group then holds them alone, so --out
must name a scratch folder.

With --simulated the simulated unit is driven instead, to try the mechanics.
Its numbers mean nothing, so --out must name a scratch folder for them.
"""
import json
import os
from pathlib import Path
import shutil
import sys

import numpy as np
from scipy import signal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import catalog_rows as models  # noqa: E402
from measure_controls import (GAIN_BLOCK, MICS, OUT, TRIM_DB, ask, checked, options, phrase, told, unit,  # noqa: E402
                              write_whole)

from lab import cli, knowledge  # noqa: E402
from lab.hearing.audio import RATE  # noqa: E402
from lab.sound import catalog  # noqa: E402
from lab.sound.chain import Block, Chain, Row  # noqa: E402

TABLE = knowledge.SHARED / "polarity.json"
REFERENCE_AMP, REFERENCE_CAB = 1008, 12001  # Rols Jazz CH120: light and clean
SECONDS, SLACK, TAIL = 1.5, 2400, .3
PAN_A, PAN_B = 1, 3  # of the mixer that joins the two rows
EARLIER_FIRMWARE = "4.1.1"  # what a group measured before the record said its firmware was measured on
WEAK = .1  # below this the two barely interact, whichever way the switch is set
# How clearly a model must take a side to be listed. Cabs are linear, so one that is only upside down against
# the reference correlates strongly with it. Distorting amps correlate less with a clean reference and are
# also judged by how they sit with all the others.
SURE = {"amps": .15, "cabs": .5, "typical": .2}
# name: (which cell of the first row is exchanged, the category of the models that take it)
GROUPS = {"amps": (0, "Guitar Amplifier"), "cabs": (1, "Cabsim Guitar (M)")}
KINDS = ("amps", "cabs", "mics")


def candidates(group):
    locked = models.unavailable()
    return [model["id"] for model in models.all_models()
            if model["id"] not in locked and model["category"] == GROUPS[group][1]]


def chain():
    """The reference amp and cab on both rows, the first row panned hard left and the second hard right, with
    the level trim after they join. One cell of the first row is exchanged for the model under test."""
    first = Row((Block(REFERENCE_AMP), Block(REFERENCE_CAB), Block(GAIN_BLOCK).set_display(0, TRIM_DB)),
                split=(0, 2), mixer=((PAN_A, 0.), (PAN_B, 1.)))
    return Chain((first, Row((Block(REFERENCE_AMP), Block(REFERENCE_CAB)))))


def lag(reference, other):
    """How many samples `other` is behind `reference`, from their shared reference channel."""
    found = signal.correlate(other, reference, "full", "fft")
    return int(np.argmax(found)) - (len(reference) - 1)


def correlation(a, b):
    scale = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
    return float(np.dot(a, b) / scale) if scale else 0.


class Passes:
    """The passes of a group's run, each lined up with the first by its reference channel, kept in the data
    folder until the group is written so that a stopped run can go on.

    One file a model, holding a pass for each of its settings as (tested channel, reference channel). `run`
    says what the passes were made with (the phrase, the firmware, the models asked for): passes of another
    run are removed, since they cannot be lined up or compared with this one's."""

    def __init__(self, folder, run):
        self.folder, self.anchor = Path(folder), None
        try:
            kept = json.loads((self.folder / "run.json").read_text(encoding="utf-8"))
            anchor = np.load(self.folder / "anchor.npy")
        except (OSError, ValueError):
            kept = anchor = None
        if kept != run or anchor is None:
            shutil.rmtree(self.folder, ignore_errors=True)
        else:
            self.anchor = anchor.astype(np.float64)
        self.folder.mkdir(parents=True, exist_ok=True)
        write_whole(self.folder / "run.json", json.dumps(run))

    def held(self):
        return {int(path.stem) for path in self.folder.glob("*.npz")}

    def add(self, model_id, rows):
        """Keep a model's passes: `rows` as `lab.tools.sweeps.sweep_models` reports them, with their audio."""
        labels, bodies = [], []
        for row in rows:
            raw = np.asarray(row["audio"], dtype=np.float64)
            if self.anchor is None:
                self.anchor = raw[:, 1].copy()
                with (self.folder / "anchor.npy.part").open("wb") as file:
                    np.save(file, self.anchor.astype(np.float32))
                os.replace(self.folder / "anchor.npy.part", self.folder / "anchor.npy")
            shift = lag(self.anchor, raw[:, 1])
            labels.append(row["label"])
            bodies.append(np.roll(raw, -shift, axis=0)[SLACK:-SLACK].astype(np.float32))
        spare = self.folder / f"{model_id}.part"
        with spare.open("wb") as file:
            np.savez(file, labels=np.array(labels, dtype=str), **{f"pass{at}": body for at, body in enumerate(bodies)})
        os.replace(spare, self.folder / f"{model_id}.npz")

    def takes(self, order):
        """{key: (tested channel, reference channel)} of the models in `order` that were measured."""
        found, held = {}, self.held()
        for model_id in order:
            if model_id in held:
                with np.load(self.folder / f"{model_id}.npz") as kept:
                    for at, label in enumerate(kept["labels"]):
                        body = kept[f"pass{at}"].astype(np.float64)
                        found[f"{model_id}{label}"] = (body[:, 0].copy(), body[:, 1].copy())
        return found

    def clear(self):
        shutil.rmtree(self.folder, ignore_errors=True)


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
    """Each of the six microphones as a cab's first microphone, at its default position, the second microphone
    left as it is. A cab that has no microphone of that name does not take it, and that pass is skipped."""
    prefix = models.microphone_prefix(model_id)
    if not prefix:
        return [("", [])]
    selector = catalog.model(model_id).index(catalog.MIC_SELECTOR)
    return [(f"|mic {mic}", [(selector, prefix + mic)]) for mic in MICS]


def table(record, kept=None):
    """The models that turn the signal upside down, for `lab.knowledge.opposed`.

    A group the record does not hold is taken from `kept`, the table as it was. The firmware is the one every
    group was measured on; groups of different firmware give a mark that names both, which
    `lab.knowledge.stale` then reports whatever the unit runs."""
    kept = kept or {}
    found, firmwares = {"firmware": None}, set()
    for group in ("amps", "cabs"):
        if group not in record and isinstance(kept.get(group), dict):
            found[group] = kept[group]
            firmwares.add(kept.get("firmware") or EARLIER_FIRMWARE)
            continue
        result = record.get(group, {})
        if result:
            firmwares.add(result.get("firmware") or EARLIER_FIRMWARE)
        against, typical = result.get("against_reference", {}), result.get("typical_strength", {})
        # A model that hardly correlates with the reference or with the rest has no polarity worth acting on.
        sure = {key for key in result.get("signs", {})
                if abs(against[key]) >= SURE[group] or group == "amps" and typical.get(key, 0) >= SURE["typical"]}
        found[group] = {"measured": sorted(int(key) for key in sure),
                        "inverted": sorted(int(key) for key in sure if result["signs"][key] < 0)}
    found["firmware"] = " and ".join(sorted(firmwares)) or EARLIER_FIRMWARE
    return found


def measure(host, group, take, dry, firmware, folder, *, only=None, limit=None):
    """Measure one group, going on from the passes an unfinished run kept. Returns (its result, whether the run
    was stopped); the result is None while the group is not finished (stopped, or ended by `limit`)."""
    kind = "cabs" if group == "mics" else group
    wanted = candidates(kind)
    if only is not None:
        wanted = [model_id for model_id in wanted if model_id in only]
    elif group == "mics":
        wanted = wanted[::max(len(wanted) // 12, 1)]
    settings = mic_settings if group == "mics" else (lambda model_id: [("", [])])
    passes = Passes(Path(folder) / "polarity-passes" / group,
                    {"group": group, "take": take, "seconds": len(dry) / RATE, "firmware": firmware,
                     "reference": [REFERENCE_AMP, REFERENCE_CAB], "models": wanted})
    left = [model_id for model_id in wanted if model_id not in passes.held()]
    todo = left if limit is None else left[:max(limit, 0)]
    print(f"{group}: models to measure on {take}: {len(todo)}; kept from an earlier run: {len(wanted) - len(left)}"
          + (f"; more after this run: {len(left) - len(todo)}" if len(todo) < len(left) else ""), flush=True)
    refused = []

    def done(event):
        if event["rows"] is None:
            refused.append(event["model"])
            print(f"{event['name']}: the unit would not load it here", flush=True)
            return
        passes.add(event["model"], event["rows"])
        if event["skipped"]:
            print(f"{event['name']}: the unit did not take {', '.join(event['skipped'])}", flush=True)
        if len(passes.held()) % 10 == 0:
            print(f"{group}: {len(passes.held())} of {len(wanted)}", flush=True)

    found = {"stopped": False}
    if todo:
        found = ask(host, "lab.tools.sweeps.sweep_models", chain(), (0, GROUPS[kind][0]),
                    [(model_id, settings(model_id)) for model_id in todo], dry, tail=TAIL, margin=SLACK,
                    audio=True, each=done)
    unfinished = [model_id for model_id in wanted if model_id not in passes.held() and model_id not in refused]
    if found["stopped"] or unfinished:
        print(f"{group}: models left: {len(unfinished)}. Run it again to go on", flush=True)
        return None, found["stopped"]
    result = {"take": take, "seconds": len(dry) / RATE, "firmware": firmware, **summary(passes.takes(wanted))}
    passes.clear()
    return result, False


def read(path):
    """A record or a table as it is on disk: {} when there is none. One that cannot be read is not replaced."""
    if not path.exists():
        return {}
    found = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(found, dict):
        raise ValueError(f"{path} does not hold a record.")
    return found


def main(argv=None):
    parser = options("Which amps and cabs come out upside down against the others, measured silently.")
    parser.add_argument("words", nargs="*", metavar="amps|cabs|mics|ID",
                        help="the groups to measure (amps and cabs when none is named); ids: these models alone")
    parser.add_argument("--limit", type=int, metavar="N", help="measure at most N models of a group in this run")
    args = checked(parser.parse_intermixed_args(argv))      # words and options in any order
    unknown = [word for word in args.words if word not in KINDS and not word.isdigit()]
    if unknown:
        raise ValueError(f"{unknown[0]!r} is not amps, cabs, mics or a model id.")
    groups = [word for word in args.words if word in KINDS] or ["amps", "cabs"]
    only = {int(word) for word in args.words if word.isdigit()} or None
    if only and args.out is None:
        raise ValueError("Measuring a few models alone is a trial: the group would then hold them alone. Name a "
                         "scratch folder for it with --out.")
    out = (args.out or OUT) / "polarity.json"
    target = args.out / "tables" / TABLE.name if args.out else TABLE
    folder = cli.data_folder(args.simulated)
    take, dry = phrase(SECONDS, folder, wav=args.phrase, instrument=args.instrument)
    record, finished, stopped = read(out), [], False
    with unit(args.simulated) as (host, firmware):
        for group in groups:
            result, stopped = measure(host, group, take, dry, firmware, folder, only=only, limit=args.limit)
            if result is None:
                break
            record[group] = result
            finished.append(group)
            write_whole(out, json.dumps(record, indent=1) + "\n")
            signs = result["signs"]
            print(f"{group}: {len(signs)} measured, {sum(sign < 0 for sign in signs.values())} inverted, "
                  f"{result['pairs_explained']} of {result['pairs']} pairs explained, "
                  f"{result['weak_pairs']} weak", flush=True)
    if any(group in ("amps", "cabs") for group in finished):
        write_whole(target, json.dumps(table(record, read(target))) + "\n")
        knowledge.table.cache_clear()
    print("finished" if len(finished) == len(groups) else "not finished: run it again to go on", flush=True)
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(told(main))
