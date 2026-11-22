"""Measure on the unit what the cab's microphone controls and an amp's Master and Output do.

Run from the repository root:
    .venv\\Scripts\\python.exe research\\measure_controls.py [cab] [amp] [--phrase WAV] [--instrument NAME]
                                                           [--out FOLDER] [--simulated]

WITHOUT --simulated THIS DRIVES THE REAL QUAD CORTEX. Run it with the desk closed (the desk holds the unit, and
the run is refused while it is open) and with the preset on the unit saved. A recorded dry phrase is replayed
through the app's build slot over USB, so nothing is heard in the room; the preset that was selected is given
back at the end. Ctrl+C stops at the next safe point, and so does choosing another preset on the unit.

The phrase is the loudest 7 s of the newest phrase recorded on the desk that is long enough (of the instrument
named with --instrument, when one is), or of the WAV file named with --phrase (all of it when it is shorter).
Results are written to research/measurements/cab-microphones.json and amp-master-output.json, or under --out.
Both are measured again each time: a sweep is a few minutes, and its conditions are compared with the
"default" of the same pass.

With --simulated the simulated unit is driven instead, to try the mechanics. Its numbers mean nothing, so
--out must name a scratch folder for them.

This module also holds what the three measuring scripts share: their options, finding the phrase, opening
the unit as the app's own commands do, and writing a file whole.
"""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import catalog_rows as models  # noqa: E402

from lab import cli  # noqa: E402
from lab.hearing.audio import RATE, read_wave  # noqa: E402
from lab.hearing.measure import CENTRES, gap  # noqa: E402
from lab.sound import catalog  # noqa: E402
from lab.sound.chain import Block, Chain, Row  # noqa: E402

OUT = Path(__file__).resolve().parent / "measurements"
PLEXI, GREENBACK, RECTIFIER, V30, GAIN_BLOCK = 1081, 12006, 1018, 12001, 16005
TRIM_DB = -12.      # the level trim at the end of every measured chain, so that no amp clips the return
MICS = ("Dynamic 57", "Dynamic 421", "Ribbon 121", "Ribbon 160", "Condenser 414", "Condenser 184")
SECONDS = 7.


# ---- what the measuring scripts share ----

def options(description):
    """The options every measuring script takes."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--simulated", action="store_true",
                        help="drive the simulated unit, not the real one (needs --out)")
    parser.add_argument("--phrase", type=Path, metavar="WAV",
                        help="a dry mono recording to replay, in place of a phrase recorded on the desk")
    parser.add_argument("--instrument", help="use a phrase recorded on this instrument")
    parser.add_argument("--out", type=Path, metavar="FOLDER",
                        help="where to write the results, in place of research/measurements")
    return parser


def checked(args):
    if args.simulated and args.out is None:
        raise ValueError("The simulated unit's numbers mean nothing. Name a scratch folder for them with --out.")
    return args


def takes(folder, instrument=None):
    """The phrases recorded in the data folder `folder`, newest first, as (record, the dry take's file): those
    of one instrument when it is named."""
    found = []
    for path in (Path(folder) / "phrases").glob("*/phrase.json"):
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        dry = path.parent / "dry.wav"
        if not isinstance(info, dict) or not dry.is_file():
            continue
        if instrument is None or str(info.get("instrument", "")).casefold() == instrument.casefold():
            found.append((str(info.get("recorded", "")), path.stat().st_mtime_ns,
                          {**info, "id": path.parent.name}, dry))
    found.sort(key=lambda item: item[:2], reverse=True)
    return [(info, dry) for _, _, info, dry in found]


def loudest(samples, length):
    """Where the `length` samples with the most energy start in a run of samples, and that energy."""
    energy = np.concatenate(([0.], np.cumsum(np.square(samples, dtype=np.float64))))
    window = energy[length:] - energy[:-length]
    start = int(np.argmax(window))
    return start, float(window[start])


def mono(path):
    dry = read_wave(path)[:, :1]
    if not len(dry):
        raise ValueError(f"{Path(path).name} holds no audio.")
    return dry


def no_phrase(seconds, folder, instrument):
    return RuntimeError(f"No recorded phrase of {seconds:g} s or more"
                        + (f" on {instrument}" if instrument else "") + f" was found in {Path(folder) / 'phrases'}. "
                        "Record one on the desk, or name a dry WAV file with --phrase.")


def phrase(seconds, folder, *, wav=None, instrument=None):
    """The loudest stretch of the newest take that is long enough, as (its name, mono dry audio).

    The takes are the phrases recorded on the desk, in the data folder `folder`; `instrument` keeps to those
    of one instrument. With `wav` it is that file's loudest stretch, or all of it when it is shorter."""
    length = round(seconds * RATE)

    def cut(dry):
        start, _ = loudest(dry[:, 0], length)
        return dry[start:start + length].copy()

    if wav is not None:
        dry = mono(wav)
        return Path(wav).name, cut(dry) if len(dry) > length else dry
    for info, path in takes(folder, instrument):
        if isinstance(info.get("seconds"), (int, float)) and info["seconds"] < seconds:
            continue                    # too short by its own record: not read
        try:
            dry = mono(path)
        except (OSError, ValueError):
            continue
        if len(dry) >= length:
            return info["id"], cut(dry)
    raise no_phrase(seconds, folder, instrument)


@contextmanager
def unit(simulated):
    """The unit for one run, held as the app's own commands hold it (`lab.cli.session`): (the host to send
    operations to, the unit's firmware). The player's preset is noted first and given back when the run ends.
    Refused while the desk is open."""
    with cli.session(simulated) as host:
        opened = cli.wait(host, host.call("open"))
        yield host, opened["firmware"]


def ask(host, name, *args, each=None, **settings):
    """The result of an operation run where the unit is held. Ctrl+C stops it at its next safe point.

    `each(event)` hears what the operation reports while it runs. It is called on the host's own thread, so a
    failure in it (a file that could not be written) stops the operation and is raised here afterwards."""
    failed = []

    def hear(event):
        if not failed:
            try:
                each(event)
            except Exception as error:  # noqa: BLE001
                failed.append(error)
                host.stop()

    found = cli.wait(host, host.call(name, *args, **settings, **({"progress": hear} if each else {})))
    if failed:
        raise failed[0]
    return found


def write_whole(path, text):
    """Write a file beside itself and then put it in place, so that a failure leaves the one that was there."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    spare = path.with_name(path.name + ".part")
    spare.write_text(text, encoding="utf-8")
    os.replace(spare, path)


def told(main, argv=None):
    """Run a script's `main` and say a failure in one line: the exit code."""
    try:
        return main(argv) or 0
    except KeyboardInterrupt:
        print("Left.", file=sys.stderr)
    except Exception as error:  # noqa: BLE001  one line, and the trace of what was not expected
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        if getattr(error, "trace", "") and not getattr(error, "expected", False):
            print(error.trace, file=sys.stderr)
    return 1


# ---- the two sweeps ----

def index(model_id, name, nth=0):
    return catalog.model(model_id).index(name, nth)


def trimmed(*blocks):
    """A chain of these blocks on one row, with the level trim after them."""
    return Chain((Row((*blocks, Block(GAIN_BLOCK).set_display(0, TRIM_DB))),))


def both(name, value):
    return [(0, 1, index(GREENBACK, name, 0), value), (0, 1, index(GREENBACK, name, 1), value)]


def summary(rows, reference):
    centres = np.array(CENTRES)
    base = next(row for row in rows if row["label"] == reference)
    table = []
    for row in rows:
        change = np.array(gap(base["bands_db"], row["bands_db"]))
        table.append({"label": row["label"], "level_db": round(row["rms_db"] - base["rms_db"], 2),
                      "centroid_hz": row["centroid_hz"], "crest_db": row["crest_db"],
                      "below_200_db": round(float(change[centres < 200].mean()), 2),
                      "200_to_800_db": round(float(change[(centres >= 200) & (centres < 800)].mean()), 2),
                      "800_to_3k_db": round(float(change[(centres >= 800) & (centres < 3000)].mean()), 2),
                      "3k_to_6k_db": round(float(change[(centres >= 3000) & (centres < 6000)].mean()), 2),
                      "above_6k_db": round(float(change[centres >= 6000].mean()), 2)})
    return table


def cab():
    chain = trimmed(Block(PLEXI).set_display(index(PLEXI, "GAIN"), 6.), Block(GREENBACK))
    prefix = models.microphone_prefix(GREENBACK)
    solo = [(0, 1, index(GREENBACK, "bypass", 1), 1.)]
    conditions = [("default", []), ("default again", [])]
    conditions += [(f"position {value:g}", both("POSITION", value)) for value in (0., 2., 6., 8., 10.)]
    conditions += [(f"distance {value:g}", both("DISTANCE", value)) for value in (3., 6., 10.)]
    conditions += [("high-pass 100 Hz", [(0, 1, index(GREENBACK, "HPF"), 100.)]),
                   ("low-pass 6 kHz", [(0, 1, index(GREENBACK, "LPF"), 6000.)])]
    conditions += [(f"mic 1 alone: {mic}", [*solo, (0, 1, index(GREENBACK, "ir selector", 0), prefix + mic)])
                   for mic in MICS]
    return "cab-microphones", chain, conditions


def amp():
    chain = trimmed(Block(RECTIFIER).set_display(index(RECTIFIER, "GAIN"), 5.), Block(V30))
    conditions = [("default", []), ("default again", [])]
    conditions += [(f"master {value:g}", [(0, 0, index(RECTIFIER, "MASTER"), value)]) for value in (1., 5., 7., 9.)]
    conditions += [(f"output {value:+g} dB", [(0, 0, index(RECTIFIER, "OUTPUT"), value)]) for value in (-6., 6.)]
    conditions += [(f"{name.lower()} {value:g}", [(0, 0, index(RECTIFIER, name), value)])
                   for name in ("PRESENCE", "BASS", "GAIN") for value in (2., 8.)]
    return "amp-master-output", chain, conditions


SWEEPS = {"cab": cab, "amp": amp}


def run(host, name, chain, conditions, take, dry, out, reference="default"):
    """One sweep, measured and written. False when it was stopped or its reference was not measured."""
    print(f"{name}: {len(conditions)} conditions on {take}", flush=True)

    def said(event):
        if "skipped" in event:
            print(f"{event['skipped']}: the unit did not take the value; not measured", flush=True)
        else:
            row = event["row"]
            print(f"{row['label']}: {row['rms_db']} dB, centroid {row['centroid_hz']} Hz, {row['seconds']} s",
                  flush=True)

    found = ask(host, "lab.tools.sweeps.sweep", chain, conditions, dry, each=said)
    rows = found["rows"]
    if found["stopped"] or not any(row["label"] == reference for row in rows):
        print(f"{name}: stopped; nothing was written" if found["stopped"] else
              f"{name}: the unit did not return the reference condition; nothing was written", flush=True)
        return False
    result = {"take": take, "seconds": len(dry) / RATE, "firmware": found["firmware"], "bands_hz": list(CENTRES),
              "chain": [block.name for _, _, block in chain.blocks],
              "summary": summary(rows, reference), "conditions": rows, "skipped": found["skipped"]}
    write_whole(out / f"{name}.json", json.dumps(result, indent=1) + "\n")
    for row in result["summary"]:
        print(f"{row['label']:26} level {row['level_db']:+6.2f}  centroid {row['centroid_hz']:5}  crest {row['crest_db']:5.2f}  "
              f"<200 {row['below_200_db']:+6.2f}  mid {row['200_to_800_db']:+6.2f}  hi-mid {row['800_to_3k_db']:+6.2f}  "
              f"3-6k {row['3k_to_6k_db']:+6.2f}  >6k {row['above_6k_db']:+6.2f}")
    return True


def main(argv=None):
    parser = options("What the cab's microphone controls and an amp's Master and Output do, measured silently.")
    parser.add_argument("sweeps", nargs="*", metavar="cab|amp", help="which to measure: both when none is named")
    args = checked(parser.parse_intermixed_args(argv))      # words and options in any order
    unknown = [name for name in args.sweeps if name not in SWEEPS]
    if unknown:
        raise ValueError(f"There is no sweep {unknown[0]!r}. Name cab, amp, or neither for both.")
    out = args.out or OUT
    take, dry = phrase(SECONDS, cli.data_folder(args.simulated), wav=args.phrase, instrument=args.instrument)
    with unit(args.simulated) as (host, _):
        for name in args.sweeps or list(SWEEPS):
            if not run(host, *SWEEPS[name](), take, dry, out):
                return 1
    print("finished", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(told(main))
