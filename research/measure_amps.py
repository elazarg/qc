"""Measure what every control of every guitar amp does, on the unit, with no sound in the room.

Run from the repository root: python research/measure_amps.py [low] [inert] [model ids]
One amp at a time takes the first cell of a fixed chain (amp, 412 V30 cab,
level trim). Each knob is measured three points below and three above its
default, and each switch in its other positions. Results go to
research/measurements/amps/<id>.json, one file per amp, so a stopped run
resumes where it left off. Selecting another preset on the unit stops it.

The loudest stretch of a take is mostly upper strings, which says little about
bass controls. `low` repeats the bass and low-end controls on the stretch with
the most energy below 200 Hz, into research/measurements/amps-low/.

Amps named by id are measured again even when a result exists. `inert` names
the amps that were found to pass the signal through untouched, which is what
the unit does with a plugin it holds no licence for: run it after buying one,
then run analyse_amps.py.
"""

import json
from pathlib import Path
import sys

import numpy as np
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_lab import audio, device_presets, measured, models, preset_design, probe, session, styles  # noqa: E402
from measure_controls import phrase  # noqa: E402

OUT = Path(__file__).resolve().parent / "measurements" / "amps"
CAB, GAIN_BLOCK = 12001, 16005
SKIP = {"OUTPUT", "MUTE TRIG"}
STEP = .3  # of the control's range, either side of its default
LOW_END = {*styles.ROLES[styles.AMP, "bass"], *styles.ROLES[styles.AMP, "thump"]}


def low_phrase(seconds=3.5):
    """The Charvel stretch with the most energy below 200 Hz, as mono dry audio."""
    length = round(seconds * audio.RATE)
    lows = signal.butter(4, 200, "low", fs=audio.RATE, output="sos")
    best = None
    for take in sorted((session.SESSIONS / "recordings").glob("clip-*")):
        try:
            if json.loads((take / "clip.json").read_text(encoding="utf-8")).get("guitar") != "Charvel":
                continue
            dry = audio.read_wave(take / "dry.wav")
        except (OSError, ValueError, KeyError):
            continue
        if len(dry) <= length:
            continue
        energy = np.concatenate(([0.], np.cumsum(np.square(signal.sosfilt(lows, dry[:, 0].astype(np.float64))))))
        window = energy[length:] - energy[:-length]
        start = int(np.argmax(window))
        if best is None or window[start] > best[0]:
            best = (window[start], take.name, dry[start:start + length].copy())
    if best is None:
        raise RuntimeError("No Charvel take of that length was found.")
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
    locked = device_presets.unavailable()
    amps = [model["id"] for model in models.all_models()
            if model["category"] == "Guitar Amplifier" and model["id"] not in locked]
    everyday = [model_id for model_id in preset_design.MODELS["Amp"] if model_id in amps]
    return everyday + [model_id for model_id in amps if model_id not in everyday]


def main(chosen, low=False):
    out = OUT.with_name("amps-low") if low else OUT
    out.mkdir(parents=True, exist_ok=True)
    take, dry = low_phrase() if low else phrase(3.5)
    only = LOW_END if low else None
    # The full pass keeps measuring amps that did nothing, in case the unit gains a licence. The low pass skips them.
    wanted = [model_id for model_id in (chosen or order()) if (chosen or not (out / f"{model_id}.json").exists())
              and len(conditions(model_id, only)) > 1 and not (low and model_id in measured.inert())]
    print(f"{len(wanted)} amps to measure on {take}", flush=True)

    def done(model_id, rows):
        info = models.lookup(model_id)
        result = {"id": model_id, "name": info["name"], "take": take, "seconds": len(dry) / audio.RATE,
                  "bands_hz": list(probe.CENTRES), "cab": models.lookup(CAB)["name"],
                  "conditions": rows, "refused": rows is None}
        (out / f"{model_id}.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        if rows is None:
            print(f"{info['name']}: the unit would not load it", flush=True)

    chain = [(1018, []), (CAB, []), (GAIN_BLOCK, [(0, -12.)])]
    probe.sweep_models(0, [(model_id, conditions(model_id, only)) for model_id in wanted], chain, dry, done,
                       report=lambda line: print(line, flush=True))
    print("finished", flush=True)


if __name__ == "__main__":
    words = sys.argv[1:]
    main([int(value) for value in words if value.isdigit()] + (sorted(measured.inert()) if "inert" in words else []),
         low="low" in words)
