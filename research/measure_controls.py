"""Measure on the unit what the cab's microphone controls and an amp's Master and Output do.

Run from the repository root: python research/measure_controls.py [cab|amp]
A recorded dry phrase is replayed through the working slot over USB, so nothing
is heard in the room. Results are written to research/measurements/.
"""

import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tone_lab import audio, iterate, models, probe, session  # noqa: E402

OUT = Path(__file__).resolve().parent / "measurements"
PLEXI, GREENBACK, RECTIFIER, V30, GAIN_BLOCK = 1081, 12006, 1018, 12001, 16005
MICS = ("Dynamic 57", "Dynamic 421", "Ribbon 121", "Ribbon 160", "Condenser 414", "Condenser 184")


def phrase(seconds=7.):
    """The loudest stretch of the newest Charvel take, as mono dry audio."""
    takes = sorted((session.SESSIONS / "recordings").glob("clip-*"), reverse=True)
    for take in takes:
        try:
            info = json.loads((take / "clip.json").read_text(encoding="utf-8"))
            if info.get("guitar") != "Charvel" or info["duration_seconds"] < seconds:
                continue
            dry = audio.read_wave(take / "dry.wav")
        except (OSError, ValueError, KeyError):
            continue
        length = round(seconds * audio.RATE)
        energy = np.convolve(np.square(dry[:, 0], dtype=np.float64), np.ones(length), "valid")
        start = int(np.argmax(energy))
        return take.name, dry[start:start + length].copy()
    raise RuntimeError("No Charvel take of that length was found.")


def index(model_id, name, nth=0):
    return [i for i, param in enumerate(models.lookup(model_id)["params"]) if param["name"] == name][nth]


def both(name, value):
    return [(1, index(GREENBACK, name, 0), value), (1, index(GREENBACK, name, 1), value)]


def summary(rows, reference):
    centres = np.array(probe.CENTRES)
    base = next(row for row in rows if row["label"] == reference)
    table = []
    for row in rows:
        change = np.array(probe.gap(base, row))
        table.append({"label": row["label"], "level_db": round(row["rms_db"] - base["rms_db"], 2),
                      "centroid_hz": row["centroid_hz"], "crest_db": row["crest_db"],
                      "below_200_db": round(float(change[centres < 200].mean()), 2),
                      "200_to_800_db": round(float(change[(centres >= 200) & (centres < 800)].mean()), 2),
                      "800_to_3k_db": round(float(change[(centres >= 800) & (centres < 3000)].mean()), 2),
                      "3k_to_6k_db": round(float(change[(centres >= 3000) & (centres < 6000)].mean()), 2),
                      "above_6k_db": round(float(change[centres >= 6000].mean()), 2)})
    return table


def run(name, chain, conditions, reference):
    take, dry = phrase()
    print(f"{name}: {len(conditions)} conditions on {take}")
    rows = probe.sweep(chain, conditions, dry)
    OUT.mkdir(exist_ok=True)
    result = {"take": take, "seconds": len(dry) / audio.RATE, "bands_hz": list(probe.CENTRES),
              "chain": [models.lookup(model_id)["name"] for model_id, _ in chain],
              "summary": summary(rows, reference), "conditions": rows}
    (OUT / f"{name}.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    for row in result["summary"]:
        print(f"{row['label']:26} level {row['level_db']:+6.2f}  centroid {row['centroid_hz']:5}  crest {row['crest_db']:5.2f}  "
              f"<200 {row['below_200_db']:+6.2f}  mid {row['200_to_800_db']:+6.2f}  hi-mid {row['800_to_3k_db']:+6.2f}  "
              f"3-6k {row['3k_to_6k_db']:+6.2f}  >6k {row['above_6k_db']:+6.2f}")


def cab():
    chain = [(PLEXI, [(index(PLEXI, "GAIN"), 6.)]), (GREENBACK, []), (GAIN_BLOCK, [(0, -12.)])]
    prefix = "ML_412 Brit 60B GB 71_"
    solo = [(1, index(GREENBACK, "bypass", 1), 1.)]
    conditions = [("default", []), ("default again", [])]
    conditions += [(f"position {value:g}", both("POSITION", value)) for value in (0., 2., 6., 8., 10.)]
    conditions += [(f"distance {value:g}", both("DISTANCE", value)) for value in (3., 6., 10.)]
    conditions += [("high-pass 100 Hz", [(1, index(GREENBACK, "HPF"), 100.)]),
                   ("low-pass 6 kHz", [(1, index(GREENBACK, "LPF"), 6000.)])]
    conditions += [(f"mic 1 alone: {mic}", [*solo, (1, index(GREENBACK, "ir selector", 0), prefix + mic)])
                   for mic in MICS]
    run("cab-microphones", chain, conditions, "default")


def amp():
    chain = [(RECTIFIER, [(index(RECTIFIER, "GAIN"), 5.)]), (V30, []), (GAIN_BLOCK, [(0, -12.)])]
    conditions = [("default", []), ("default again", [])]
    conditions += [(f"master {value:g}", [(0, index(RECTIFIER, "MASTER"), value)]) for value in (1., 5., 7., 9.)]
    conditions += [(f"output {value:+g} dB", [(0, index(RECTIFIER, "OUTPUT"), value)]) for value in (-6., 6.)]
    conditions += [(f"{name.lower()} {value:g}", [(0, index(RECTIFIER, name), value)])
                   for name in ("PRESENCE", "BASS", "GAIN") for value in (2., 8.)]
    run("amp-master-output", chain, conditions, "default")


if __name__ == "__main__":
    chosen = sys.argv[1:] or ["cab", "amp"]
    for name in chosen:
        {"cab": cab, "amp": amp}[name]()
