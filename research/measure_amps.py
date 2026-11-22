"""Measure what every control of every guitar amp does, on the unit, with no sound in the room.

Run from the repository root:
    .venv\\Scripts\\python.exe research\\measure_amps.py [low] [inert] [model ids] [--limit N]
                                                       [--phrase WAV] [--instrument NAME] [--out FOLDER]
                                                       [--simulated]

WITHOUT --simulated THIS DRIVES THE REAL QUAD CORTEX. Run it with the desk closed (the desk holds the unit, and
the run is refused while it is open) and with the preset on the unit saved. Nothing is heard in the room: the
phrase is replayed through the app's build slot over USB. The preset that was selected is given back at the
end. Ctrl+C stops at the next safe point, and so does choosing another preset on the unit.

One amp at a time takes the first cell of a fixed chain (amp, 412 V30 cab,
level trim). Each knob is measured three points below and three above its
default, and each switch in its other positions. Results go to
research/measurements/amps/<id>.json (or under --out), one file per amp, so a
stopped run resumes where it left off: an amp whose file was measured on the
unit's firmware is not measured again, and --limit ends a run after so many
amps. A file from before they said their firmware counts as measured on the
firmware of the table the app ships.

The phrase is the loudest 3.5 s of the newest phrase recorded on the desk that
is long enough (see measure_controls.py). The loudest stretch of a take is
mostly upper strings, which says little about bass controls. `low` repeats the
bass and low-end controls on the stretch with the most energy below 200 Hz,
into research/measurements/amps-low/.

Amps named by id are measured again even when a result exists. `inert` names
the amps that were found to pass the signal through untouched, which is what
the unit does with a plugin it holds no licence for: run it after buying one,
then run analyse_amps.py.

With --simulated the simulated unit is driven instead, to try the mechanics.
Its numbers mean nothing, so --out must name a scratch folder for them.
"""

import json
from pathlib import Path
import sys

from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import catalog_rows as models  # noqa: E402
from measure_controls import (OUT, ask, checked, loudest, mono, no_phrase, options, phrase, takes, told,  # noqa: E402
                              trimmed, unit, write_whole)

from lab import cli, knowledge  # noqa: E402
from lab.hearing.audio import RATE  # noqa: E402
from lab.hearing.measure import CENTRES  # noqa: E402
from lab.sound.chain import Block  # noqa: E402
from lab.sound.design import EVERYDAY  # noqa: E402
from lab.sound.knobs import AMP, ROLES  # noqa: E402

STAND_IN, CAB = 1018, 12001     # the amp the chain is built with, and the cab every amp is heard through
SECONDS = 3.5
SKIP = {"OUTPUT", "MUTE TRIG"}
STEP = .3  # of the control's range, either side of its default
LOW_END = {*ROLES[AMP, "bass"], *ROLES[AMP, "thump"]}


def low_phrase(folder, seconds=SECONDS, *, wav=None, instrument=None):
    """The stretch with the most energy below 200 Hz among the takes, as (its name, mono dry audio)."""
    length = round(seconds * RATE)
    lows = signal.butter(4, 200, "low", fs=RATE, output="sos")

    def cut(dry):
        start, energy = loudest(signal.sosfilt(lows, dry[:, 0].astype("float64")), length)
        return energy, dry[start:start + length].copy()

    if wav is not None:
        dry = mono(wav)
        return Path(wav).name, cut(dry)[1] if len(dry) > length else dry
    best = None
    for info, path in takes(folder, instrument):
        try:
            dry = mono(path)
        except (OSError, ValueError):
            continue
        if len(dry) <= length:
            continue
        energy, part = cut(dry)
        if best is None or energy > best[0]:
            best = (energy, info["id"], part)
    if best is None:
        raise no_phrase(seconds, folder, instrument)
    return best[1], best[2]


def conditions(model_id, only=None):
    found = [("default", [])]
    for index, param in enumerate(models.lookup(model_id)["params"]):
        name, kind = param["name"], param["type"]
        if name in SKIP or only is not None and name not in only:
            continue
        try:
            low, high, default = float(param["min"]), float(param["max"]), float(param["default"])
        except (TypeError, ValueError):
            continue
        if kind in ("float", "fader"):
            span = high - low
            # A control that sits near one end is measured halfway to that end, since the end itself is often silent.
            below = default - STEP * span if default - STEP * span >= low + .1 * span else (low + default) / 2
            above = default + STEP * span if default + STEP * span <= high - .1 * span else (high + default) / 2
            for side, value in (("low", below), ("high", above)):
                if abs(value - default) > .05 * span:
                    found.append((f"{name}|{side}|{value:g}|{default:g}", [(index, value)]))
        elif kind in ("switch", "rotarySwitch", "comboBox") and high - low <= 3:
            found += [(f"{name}|switch|{value:g}|{default:g}", [(index, float(value))])
                      for value in range(int(low), int(high) + 1) if value != default]
    return found


def order():
    locked = models.unavailable()
    amps = [model["id"] for model in models.all_models()
            if model["category"] == AMP and model["id"] not in locked]
    everyday = [model_id for model_id in EVERYDAY if model_id in amps]
    return everyday + [model_id for model_id in amps if model_id not in everyday]


def shipped(key, default=None):
    """A field of the table of amp controls the app ships, or `default` without that table."""
    try:
        return knowledge.table("controls").get(key, default)
    except knowledge.MissingTable:
        return default


def measured_on(path, earlier):
    """The firmware an amp's result file was measured on (`earlier` for a file from before they said), None
    when there is no such file. A file that cannot be read is left as it is: ValueError says so."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("firmware") or earlier
    except (OSError, ValueError, AttributeError):
        raise ValueError(f"{path} cannot be read. Move it away to measure that amp again.") from None


def main(argv=None):
    parser = options("What every control of every guitar amp does, measured silently, one file per amp.")
    parser.add_argument("words", nargs="*", metavar="low|inert|ID",
                        help="low: the bass controls on a low-string stretch; inert: the amps found to do nothing, "
                             "again; ids: these amps, again")
    parser.add_argument("--limit", type=int, metavar="N", help="measure at most N amps in this run")
    args = checked(parser.parse_intermixed_args(argv))      # words and options in any order
    unknown = [word for word in args.words if word not in ("low", "inert") and not word.isdigit()]
    if unknown:
        raise ValueError(f"{unknown[0]!r} is not low, inert or a model id.")
    low, inert = "low" in args.words, set(shipped("inert", ()))
    chosen = [int(word) for word in args.words if word.isdigit()] + (sorted(inert) if "inert" in args.words else [])
    strangers = [model_id for model_id in chosen if not models.lookup(model_id)]
    if strangers:
        raise ValueError(f"The catalog has no model {strangers[0]}.")
    out = (args.out or OUT) / ("amps-low" if low else "amps")
    folder = cli.data_folder(args.simulated)
    take, dry = (low_phrase(folder, wav=args.phrase, instrument=args.instrument) if low
                 else phrase(SECONDS, folder, wav=args.phrase, instrument=args.instrument))
    only = LOW_END if low else None
    with unit(args.simulated) as (host, firmware):
        # The full pass keeps measuring amps that did nothing, in case the unit gains a licence. The low pass
        # skips them.
        wanted = [model_id for model_id in (chosen or order())
                  if (chosen or measured_on(out / f"{model_id}.json", shipped("firmware")) != firmware)
                  and len(conditions(model_id, only)) > 1 and not (low and model_id in inert)]
        todo = wanted if args.limit is None else wanted[:max(args.limit, 0)]
        print(f"Amps to measure on {take}: {len(todo)}" + (f", and {len(wanted) - len(todo)} more after this run"
                                                           if len(todo) < len(wanted) else ""), flush=True)

        def done(event):
            rows = event["rows"]
            result = {"id": event["model"], "name": event["name"], "take": take, "seconds": len(dry) / RATE,
                      "firmware": event["firmware"], "bands_hz": list(CENTRES), "cab": models.lookup(CAB)["name"],
                      "conditions": rows, "refused": rows is None, "skipped": event["skipped"]}
            write_whole(out / f"{event['model']}.json", json.dumps(result, indent=1) + "\n")
            if rows is None:
                print(f"{event['name']}: the unit would not load it", flush=True)
            else:
                print(f"{event['name']}: {len(rows)} conditions, {sum(row['seconds'] for row in rows):.0f} s"
                      + (f"; the unit did not take {', '.join(event['skipped'])}" if event["skipped"] else ""),
                      flush=True)

        found = {"left": 0, "stopped": False}
        if todo:
            found = ask(host, "lab.tools.sweeps.sweep_models", trimmed(Block(STAND_IN), Block(CAB)), (0, 0),
                        [(model_id, conditions(model_id, only)) for model_id in todo], dry, each=done)
    left = found["left"] + len(wanted) - len(todo)
    print(f"stopped; amps left: {left}. Run it again to go on" if found["stopped"] else
          f"finished this run; amps left: {left}. Run it again to go on" if left else "finished", flush=True)
    return 1 if found["stopped"] else 0


if __name__ == "__main__":
    sys.exit(told(main))
