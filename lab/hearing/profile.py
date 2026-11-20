"""What the player's own instrument puts out, from phrases recorded with each pickup setting.

Presets are made by other people, on other guitars, and assume an input they
were built for. A preset that feels thin, weak or harsh on this guitar may be
hearing less level, or another balance, than its maker's. This says, per
pickup setting, how loud the dry guitar is (peak and loudness, against what a
typical passive humbucker gives), how bright, and how much low end, and
compares each setting with the bridge pickup in series, which is what most
presets assume.

The reference figures are published measurements (gathered in the gear research for the rewrite):
with the unit's input at full scale near 14 to 15 dBu, a medium-hot passive
humbucker played firmly peaks around -18 dBFS.
"""

import numpy as np

from lab.hearing.measure import CENTRES, measure

TYPICAL_PEAK_DB = -18.     # a medium-hot passive humbucker, firm playing, at the unit's input level 0
BANDS = ((60, 200, "lows"), (200, 800, "low mids"), (800, 3000, "mids"), (3000, 6000, "presence"),
         (6000, 12000, "air"))


def reference_of(summary):
    """The setting most presets assume, the bridge pickup in series, when a phrase was recorded on it."""
    return next((key for key in summary if "bridge" in key.lower() and "series" in key.lower()), None)


def _setting(summary, setting, reference):
    item = summary[setting]
    gap = round(item["peak_db"] - TYPICAL_PEAK_DB, 1)
    found = {"peak_db": item["peak_db"], "peak_against_typical_humbucker_db": gap}
    if abs(gap) <= 3:
        implies = ["about as hot as the humbucker presets assume: amps distort about as their settings suggest"]
    elif gap < 0:
        implies = [f"about {abs(gap):.0f} dB weaker than presets assume: amps and drives will distort less, and "
                   "sound cleaner and quieter, than their settings suggest"]
    else:
        implies = [f"about {gap:.0f} dB hotter than presets assume: amps and drives will distort more than their "
                   "settings suggest, and clean sounds may break up"]
    base = summary.get(reference) if reference else None
    if base and setting != reference:
        against = {}
        if item["lufs"] is not None and base["lufs"] is not None:
            against["loudness_db"] = round(item["lufs"] - base["lufs"], 1)
        if item["centroid_hz"] and base["centroid_hz"]:
            against["brightness_octaves"] = round(float(np.log2(item["centroid_hz"] / base["centroid_hz"])), 2)
        lows = item.get("bands", {}).get("lows")
        if lows is not None and base.get("bands", {}).get("lows") is not None:
            against["lows_db"] = round(lows - base["bands"]["lows"], 1)
        found["against_reference"] = against
        bright = against.get("brightness_octaves", 0)
        if bright > .2:
            implies.append(f"brighter than {reference}: sounds made for it come out brighter and harsher")
        elif bright < -.2:
            implies.append(f"darker than {reference}: sounds made for it come out darker and duller")
        if against.get("lows_db", 0) < -2:
            implies.append(f"thinner in the lows than {reference}")
        elif against.get("lows_db", 0) > 2:
            implies.append(f"fuller in the lows than {reference}")
    found["implies"] = "; ".join(implies) + "."
    return found


def facts(summary, setting=None):
    """What the guitar puts out, compactly, for the designer: the setting the phrase was recorded on (or every
    setting), its peak against a typical passive humbucker in dB, its loudness, brightness (octaves of spectral
    centroid) and lows against the bridge pickup in series, and what that means in plain words. None when no
    setting has been profiled."""
    if not summary:
        return None
    reference = reference_of(summary)
    known = {key.casefold(): key for key in summary}
    chosen = known.get((setting or "").casefold())
    found = {"typical_humbucker_peak_db": TYPICAL_PEAK_DB, "reference": reference}
    if setting:
        found["recorded_on"] = chosen or setting
    if chosen:
        found["settings"] = {chosen: _setting(summary, chosen, reference)}
        others = {key: _setting(summary, key, reference)["implies"] for key in summary if key != chosen}
        if others:
            found["other_settings"] = others
    else:
        if setting:
            found["recorded_on_profiled"] = False
        found["settings"] = {key: _setting(summary, key, reference) for key in summary}
    if reference is None:
        found["note"] = ("No phrase was recorded on the bridge pickup in series, so settings are compared only "
                         "with a typical humbucker.")
    return found


def describe_take(dry):
    found = measure(dry)
    centres = np.array(CENTRES)
    power = 10 ** (np.array(found.bands_db) / 10)
    total = power[(centres >= 60) & (centres < 12000)].sum()
    shares = {name: round(10 * np.log10(power[(centres >= low) & (centres < high)].sum() / total + 1e-12), 1)
              for low, high, name in BANDS}
    return {"peak_db": round(found.peak_db, 1), "lufs": None if found.lufs is None else round(found.lufs, 1),
            "centroid_hz": round(found.centroid_hz), "bands": shares}


def profile(takes):
    """takes: [(setting, dry audio)]. Returns {setting: summary} and plain sentences about it."""
    groups = {}
    for setting, dry in takes:
        groups.setdefault(setting, []).append(describe_take(dry))
    summary = {}
    for setting, found in groups.items():
        loud = [item["lufs"] for item in found if item["lufs"] is not None]
        summary[setting] = {"takes": len(found), "peak_db": max(item["peak_db"] for item in found),
                            "lufs": round(float(np.median(loud)), 1) if loud else None,
                            "centroid_hz": round(float(np.median([item["centroid_hz"] for item in found]))),
                            "bands": {name: round(float(np.median([item["bands"][name] for item in found])), 1)
                                      for _, _, name in BANDS}}
    lines = []
    reference = reference_of(summary)
    for setting, item in summary.items():
        line = f"{setting}: peaks at {item['peak_db']:g} dBFS"
        gap = item["peak_db"] - TYPICAL_PEAK_DB
        line += (", about as hot as the humbucker most presets assume" if abs(gap) <= 3 else
                 f", {abs(gap):.0f} dB {'hotter' if gap > 0 else 'weaker'} than the humbucker most presets assume")
        if reference and setting != reference and summary[reference]["lufs"] and item["lufs"]:
            diff = item["lufs"] - summary[reference]["lufs"]
            line += f"; {abs(diff):.0f} dB {'louder' if diff > 0 else 'quieter'} than {reference}"
            bright = np.log2(item["centroid_hz"] / summary[reference]["centroid_hz"])
            if abs(bright) > .2:
                line += f", {'brighter' if bright > 0 else 'darker'}"
        lines.append(line + ".")
    if reference is None:
        lines.append("Record the same phrase on the bridge pickup in series too, to compare every setting with "
                     "what most presets were built on.")
    return summary, lines
