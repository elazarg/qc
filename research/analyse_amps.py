"""Turn the amp sweep into what each control does, per amp, and a report of what stands out.

Run from the repository root:
    .venv\\Scripts\\python.exe research\\analyse_amps.py [--measurements FOLDER] [--out FOLDER]
It reads whatever research/measurements/amps/*.json exist, so it can be run
while the sweep (measure_amps.py) is still going and again when it is done. It
writes lab/tables/controls.json, which the app loads through
`lab.knowledge.table("controls")`, and research/amp-controls-report.md.
`--measurements` names another folder holding `amps` and `amps-low` (a trial
run's), and `--out` another folder for the table and the report.

A knob's effect is given per tenth of its travel, as the knob rises, so the
side below the default and the side above it can be compared. Band changes
have the change of the whole spectrum removed (the mean change of the bands
that sound), and the level change is given beside them.

The firmware the table is marked with is the one the sweep files say they were
measured on. A file from before they said so was measured on the firmware of
the table the app ships, or, without one, on the firmware the USB round trip
in the data folder was measured on. Files of more than one firmware give a
mark that names them all, which `lab.knowledge.stale` then reports whatever
the unit runs.
"""

import argparse
import json
import os
from pathlib import Path
from statistics import median
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import catalog_rows as models  # noqa: E402

from lab import knowledge  # noqa: E402
from lab.sound.knobs import AMP, AMP_INPUT_GAIN_NAMES, AMP_VOLUME_AS_DRIVE, ROLE_BANDS, ROLES  # noqa: E402

MEASUREMENTS = Path(__file__).resolve().parent / "measurements"
TABLE = knowledge.SHARED / "controls.json"
REPORT = Path(__file__).resolve().parent / "amp-controls-report.md"
LOW_ROLES = ("bass", "thump")
# The five bands a control's effect is given in: the spectrum cut at EDGES. `ROLE_BANDS` names them by position.
BANDS = ("below 200 Hz", "200–800 Hz", "800 Hz–3 kHz", "3–6 kHz", "above 6 kHz")
EDGES = (200., 800., 3000., 6000.)
SILENT = -80.  # dB. A condition this quiet says nothing about tone.
SOUNDING = 45.  # dB below the default's loudest band. Quieter bands carry no sound through this cab.
WEAK = .1  # dB per point. Below this on both sides, a control does almost nothing to its band.
STILL = .05  # dB. An amp none of whose controls moves the level this far is not processing the signal.
MASTERS = ("MASTER", "LEAD MASTER", "OUTPUT MASTER", "MASTER NORMAL", "MASTER OD")


def groups(centres):
    centres = np.asarray(centres)
    bounds = (0., *EDGES, float("inf"))
    return [(centres >= low) & (centres < high) for low, high in zip(bounds, bounds[1:])]


def change(base, row, centres):
    """What a condition did against the default: level, the five bands, brightness and crest."""
    before, after = np.array(base["bands_db"]), np.array(row["bands_db"])
    heard = before >= before.max() - SOUNDING
    shift = after - before
    shift = shift - shift[heard].mean()
    bands = [round(float(shift[group & heard].mean()), 3) if (group & heard).any() else None
             for group in groups(centres)]
    return {"level": round(row["rms_db"] - base["rms_db"], 3), "bands": bands,
            "centroid": round(float(np.log2(max(row["centroid_hz"], 1) / max(base["centroid_hz"], 1))), 4),
            "crest": round(row["crest_db"] - base["crest_db"], 3)}


def per_point(found, points):
    return {"moved": round(points, 2), "level": round(found["level"] / points, 3),
            "bands": [None if value is None else round(value / points, 3) for value in found["bands"]],
            "centroid": round(found["centroid"] / points, 4), "crest": round(found["crest"] / points, 3)}


def role_of(name):
    for (category, role), controls in ROLES.items():
        if category == AMP and name in controls:
            return role, controls[name]
    return None


def side_strength(knob, side):
    """How far a knob moves its role's band on one side of its default, signed so that positive follows the role."""
    found = knob.get(side) or {}
    bands = [found["bands"][index] for index in ROLE_BANDS.get(knob.get("role"), ()) if "bands" in found]
    if not bands or None in bands:
        return None
    return round(knob["sign"] * sum(bands) / len(bands), 3)


def summarise(result):
    """One amp's file as the record kept for it, or None when its default made no sound."""
    rows = result.get("conditions") or []
    base = next((row for row in rows if row["label"] == "default"), None)
    if base is None or base["rms_db"] < SILENT:
        return None
    ranges = {}
    for param in models.lookup(result["id"])["params"]:
        try:
            ranges.setdefault(param["name"], (float(param["max"]) - float(param["min"])) / 10)
        except (TypeError, ValueError):
            continue
    knobs, switches = {}, {}
    for row in rows:
        if row["label"] == "default":
            continue
        name, side, value, default = row["label"].split("|")
        if side == "switch":
            if row["rms_db"] >= SILENT:
                switches.setdefault(name, {})[f"{float(value):g}"] = {
                    "from": f"{float(default):g}", **change(base, row, result["bands_hz"])}
            continue
        points = (float(value) - float(default)) / ranges[name]
        entry = knobs.setdefault(name, {})
        role = role_of(name)
        if role:
            entry["role"], entry["sign"] = role
        entry[side] = ({"moved": round(points, 2), "silent": True} if row["rms_db"] < SILENT
                       else per_point(change(base, row, result["bands_hz"]), points))
    return {"name": result["name"], "knobs": knobs, "switches": switches,
            "default": {"level": base["rms_db"], "crest": base["crest_db"], "centroid": base["centroid_hz"]}}


def strengths(amps, role):
    """[(dB per point averaged over the measured sides, amp name, control)] for every control with the role."""
    found = []
    for model_id, amp in amps.items():
        for name, knob in amp["knobs"].items():
            if knob.get("role") == role:
                sides = [value for value in (side_strength(knob, side) for side in ("low", "high"))
                         if value is not None]
                if sides:
                    found.append((sum(sides) / len(sides), amp["name"], name, model_id))
    return sorted(found)


def inert(result):
    """Whether no knob or switch of an amp changed what came out.

    The unit loads the amps of some plugins and passes the signal through them
    untouched. The player owns none of those plugins, so this is what the unit
    does with a plugin it holds no licence for.
    """
    rows = result["conditions"]
    return len(rows) > 4 and all(abs(row["rms_db"] - rows[0]["rms_db"]) < STILL and max(
        abs(a - b) for a, b in zip(row["bands_db"], rows[0]["bands_db"])) < STILL for row in rows[1:])


def earlier_firmware():
    """The firmware of sweep files that do not say theirs: the shipped table's, made from such files, or without
    one the firmware the USB round trip in the data folder was measured on."""
    for path in (TABLE, knowledge.home() / "calibration.json"):
        try:
            found = json.loads(path.read_text(encoding="utf-8")).get("firmware")
        except (OSError, ValueError, AttributeError):
            continue
        if found:
            return found
    return None


def collect(measurements=MEASUREMENTS):
    source, low_source = Path(measurements) / "amps", Path(measurements) / "amps-low"
    amps, refused, silent, meta, untouched = {}, [], [], {}, {}
    lows, tops = [], []  # How far under the loudest band the ends of the spectrum sit, per amp.
    firmwares, earlier = set(), earlier_firmware()
    for path in sorted(source.glob("*.json"), key=lambda item: int(item.stem)):
        result = json.loads(path.read_text(encoding="utf-8"))
        firmwares.add(result.get("firmware") or earlier)
        if result.get("refused") or not result.get("conditions"):
            refused.append(result["name"])
            continue
        if inert(result):
            untouched[result["id"]] = result["name"]
            continue
        record = summarise(result)
        if record is None:
            silent.append(result["name"])
            continue
        low = low_source / path.name
        if low.exists():
            # Bass controls are judged on the low-string phrase, where there is bass to move.
            again = summarise(json.loads(low.read_text(encoding="utf-8")))
            for name, knob in (again["knobs"] if again else {}).items():
                if knob.get("role") in LOW_ROLES:
                    record["knobs"][name] = {**knob, "phrase": "low strings"}
        amps[str(result["id"])] = record
        levels, centres = np.array(result["conditions"][0]["bands_db"]), np.array(result["bands_hz"])
        lows.append(float(levels.max() - levels[centres < EDGES[0]].max()))
        tops.append(float(levels.max() - levels[centres >= 7900].max()))
        meta = {"cab": result["cab"], "take": result["take"], "seconds": result["seconds"]}
    firmwares = sorted(found for found in firmwares if found)
    firmware = " and ".join(firmwares) if firmwares else earlier
    typical = {}
    for role in ROLE_BANDS:
        values = [value for value, *_ in strengths(amps, role) if value > 0]
        if values:
            typical[role] = round(median(values), 3)
    meta["quiet_ends"] = {"below_200": round(median(lows), 1), "from_8k": round(median(tops), 1)} if lows else {}
    table = {"firmware": firmware, **meta, "bands": list(BANDS),
             "unit": "dB per tenth of a control's travel, as it rises. Switches are dB against the default position.",
             "typical": typical, "refused": refused, "silent": silent, "inert": sorted(untouched),
             "inert_names": [untouched[model_id] for model_id in sorted(untouched)], "amps": amps}
    return table


# ---- The report -------------------------------------------------------------------------------------


def biggest(found, count=2, scale=1.):
    """The band changes that stand out, in words."""
    ranked = sorted(((abs(value), index) for index, value in enumerate(found["bands"]) if value is not None),
                    reverse=True)[:count]
    return ", ".join(f"{BANDS[index]} {found['bands'][index] * scale:+.1f}" for _, index in sorted(
        ranked, key=lambda item: item[1]))


def sides(knob):
    return [(side, knob[side]) for side in ("low", "high") if side in knob and not knob[side].get("silent")]


def report(table):
    amps = table["amps"]
    low_count = sum(any(knob.get("phrase") for knob in amp["knobs"].values()) for amp in amps.values())
    out = ["# What each amp control does, measured", "",
           f"Generated by `research/analyse_amps.py` from {len(amps)} amps measured on the Quad Cortex "
           f"(CorOS {table['firmware']}). Run it again after the sweep to refresh this file.", "",
           f"**One phrase, one cab, one take.** Every number comes from a {table.get('seconds')} s stretch of "
           f"`{table.get('take')}` replayed through each amp into `{table.get('cab')}` at its default microphones. "
           f"On the typical amp the loudest band below 200 Hz is {table['quiet_ends'].get('below_200')} dB under "
           f"the loudest band overall, and from 8 kHz up it is {table['quiet_ends'].get('from_8k')} dB under. "
           + (f"The bass and low-end controls of {low_count} amps were therefore measured again on the stretch of a "
              "take with the most energy below 200 Hz, and their figures come from that pass. " if low_count else
              "The bass figures therefore rest on little signal, and a phrase on the low strings should be measured "
              "before they are trusted. ")
           + f"Bands more than {SOUNDING:g} dB under an amp's loudest band are left out. Another "
           "guitar, phrase or cab would give other numbers. Nothing here has been listened to.", "",
           "Knob effects are dB per point as the knob rises, where a point is a tenth of the knob's travel. "
           "\"Below\" is measured from the default down, \"above\" from the default up. Band changes are "
           "relative to the rest of the spectrum. Level is given separately.", ""]
    if table["refused"] or table["silent"]:
        out += [f"Not measured: {', '.join(table['refused']) or 'none'} (the unit would not load them); "
                f"{', '.join(table['silent']) or 'none'} (silent at their defaults).", ""]

    if table["inert_names"]:
        out += ["## Amps that pass the signal through untouched", "",
                f"The unit loaded these {len(table['inert_names'])} amps, and no knob or switch of them changed "
                "what came out: every one returned the same sound, the guitar through the cab alone. A plugin "
                "the player does not own behaves this way. Tone Lab does not offer them, "
                "or anything else from the same plugins.", "", ", ".join(table["inert_names"]) + ".", ""]

    out += ["## How strong each kind of control is", "",
            "| Role | Band judged | Controls | Typical | Weakest | Strongest |", "| --- | --- | --- | --- | --- | --- |"]
    for role, bands in ROLE_BANDS.items():
        found = strengths(amps, role)
        if not found:
            continue
        span = " and ".join(BANDS[index] for index in bands)
        weakest, strongest = found[0], found[-1]
        out.append(f"| {role} | {span} | {len(found)} | {median(value for value, *_ in found):+.2f} | "
                   f"{weakest[0]:+.2f} ({weakest[1]} {weakest[2].title()}) | "
                   f"{strongest[0]:+.2f} ({strongest[1]} {strongest[2].title()}) |")
    out += ["", "Positive means the control moves its band the way its role says. "
                "A Shape knob moves these controls two to two and a half points."]

    wrong, idle, elsewhere = [], [], []
    for amp in amps.values():
        for name, knob in amp["knobs"].items():
            if "role" not in knob:
                continue
            measured_sides = [(side, side_strength(knob, side), found) for side, found in sides(knob)]
            for side, value, found in measured_sides:
                if value is not None and value <= -WEAK:
                    wrong.append(f"- **{amp['name']} {name.title()}** ({knob['role']}), "
                                 f"{'below' if side == 'low' else 'above'} its default: {value:+.2f} dB per point "
                                 f"in its own band. Raising it gives level {found['level']:+.2f}, "
                                 f"{biggest(found)}.")
            for side, value, found in measured_sides:
                own = ROLE_BANDS[knob["role"]]
                other = max(((abs(shift), index) for index, shift in enumerate(found["bands"])
                             if shift is not None and index not in own), default=(0., 0))
                if value is not None and other[0] >= 2 * WEAK and other[0] >= 2 * abs(value):
                    elsewhere.append(f"- **{amp['name']} {name.title()}** ({knob['role']}), "
                                     f"{'below' if side == 'low' else 'above'} its default: {value:+.2f} in its own "
                                     f"band, {found['bands'][other[1]]:+.2f} {BANDS[other[1]]}, "
                                     f"level {found['level']:+.2f}.")
            if measured_sides and all(value is not None and abs(value) < WEAK and abs(found["level"]) < WEAK
                                      for _, value, found in measured_sides):
                idle.append(f"- {amp['name']} {name.title()} ({knob['role']}): "
                            + ", ".join(f"{value:+.2f} {'below' if side == 'low' else 'above'}"
                                        for side, value, _ in measured_sides) + " dB per point.")
    out += ["", "## Controls that go against their role", "",
            "`lab.sound.knobs.ROLES` says which way each control should move its band. These moved it the other way by "
            f"at least {WEAK} dB per point.", "", *(wrong or ["None."])]
    out += ["", "## Controls that move another band more than their own", "",
            f"Another band moved at least twice as far as the role's own, and at least {2 * WEAK} dB per point. "
            "Figures are dB per point as the control rises.", "", *(elsewhere or ["None."])]
    out += ["", "## Controls that do almost nothing", "",
            f"Under {WEAK} dB per point in their band and in level, on every side measured.", "",
            *(idle or ["None."])]

    out += ["", "## Switches", "", "Change against the default position, in dB.", ""]
    for name in sorted({name for amp in amps.values() for name in amp["switches"]}):
        out.append(f"**{name.title()}**")
        out.append("")
        for amp in amps.values():
            for position, found in amp["switches"].get(name, {}).items():
                out.append(f"- {amp['name']}, position {position} from {found['from']}: level {found['level']:+.1f}, "
                           f"{biggest(found, 3)}, crest {found['crest']:+.1f}.")
        out.append("")
    if out[-2] == "Change against the default position, in dB.":
        out += ["None measured yet.", ""]

    gains = AMP_INPUT_GAIN_NAMES | {"DRIVE", "PREAMP", "PREAMP GAIN", "VOLUME NORMAL", "VOLUME BRIGHT"}
    out += ["## Gain controls", "",
            "Level and brightness per point as the control rises. Brightness is the move of the spectral centre in "
            "octaves. A crest that falls as gain rises is compression.", "",
            "| Amp | Control | Level below | Level above | Brightness below | Brightness above | Crest below | Crest above |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    below, above = [], []
    for model_id, amp in amps.items():
        for name, knob in amp["knobs"].items():
            if name in gains or name == "VOLUME" and int(model_id) in AMP_VOLUME_AS_DRIVE:
                low, high = knob.get("low", {}), knob.get("high", {})
                cell = lambda side, key, form: "silent" if side.get("silent") else (
                    format(side[key], form) if key in side else "")
                out.append(f"| {amp['name']} | {name.title()} | {cell(low, 'level', '+.1f')} | {cell(high, 'level', '+.1f')} | "
                           f"{cell(low, 'centroid', '+.3f')} | {cell(high, 'centroid', '+.3f')} | "
                           f"{cell(low, 'crest', '+.2f')} | {cell(high, 'crest', '+.2f')} |")
                below += [low["level"]] if "level" in low else []
                above += [high["level"]] if "level" in high else []
    if below and above:
        out += ["", f"Across these controls the typical level change is {median(below):+.1f} dB per point below the "
                    f"default and {median(above):+.1f} above it: most amps are already saturated at their default "
                    "gain, so turning up adds little level and turning down removes a lot."]

    masters = []
    for amp in amps.values():
        for name, knob in amp["knobs"].items():
            if name in MASTERS:
                for side, found in sides(knob):
                    tone = max((abs(value) for value in found["bands"] if value is not None), default=0.)
                    masters.append((max(tone, abs(found["crest"])), amp["name"], name, side, found))
    out += ["", "## Master", "",
            "Where the Master changes more than level, per point, strongest first. On the rest it is close to a "
            "plain volume over the range measured.", ""]
    for score, amp, name, side, found in sorted(masters, key=lambda item: -item[0])[:20]:
        if score < WEAK:
            break
        out.append(f"- {amp} {name.title()}, {'below' if side == 'low' else 'above'} its default: "
                   f"level {found['level']:+.1f}, crest {found['crest']:+.2f}, {biggest(found)}.")
    plain = sorted({amp for score, amp, *_ in masters} - {amp for score, amp, *_ in masters if score >= WEAK})
    if plain:
        out += ["", f"Close to a plain volume: {', '.join(plain)}."]
    return "\n".join(out) + "\n"


def write_whole(path, text):
    """Write a file beside itself and then put it in place, so that a failure leaves the one that was there."""
    path.parent.mkdir(parents=True, exist_ok=True)
    spare = path.with_name(path.name + ".part")
    spare.write_text(text, encoding="utf-8")
    os.replace(spare, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="What each amp control does, from the sweep files.")
    parser.add_argument("--measurements", type=Path, default=MEASUREMENTS,
                        help="the folder holding amps/ and amps-low/ (research/measurements)")
    parser.add_argument("--out", type=Path, help="a folder for controls.json and the report, in place of the app's")
    args = parser.parse_args(argv)
    target = args.out / TABLE.name if args.out else TABLE
    report_path = args.out / REPORT.name if args.out else REPORT
    table = collect(args.measurements)
    write_whole(target, json.dumps(table, separators=(",", ":"), ensure_ascii=False) + "\n")
    knowledge.table.cache_clear()
    knowledge.locked.cache_clear()
    write_whole(report_path, report(table))
    print(f"{len(table['amps'])} amps, {len(table['refused'])} refused, {len(table['silent'])} silent. "
          f"Typical strength per point: {table['typical']}")
    print(f"Wrote {target} and {report_path}")


if __name__ == "__main__":
    main()
