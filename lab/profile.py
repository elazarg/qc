"""What the player's own instrument puts out, from phrases recorded with each pickup setting.

Presets are made by other people, on other guitars, and assume an input they
were built for. A preset that feels thin, weak or harsh on this guitar may be
hearing less level, or another balance, than its maker's. This says, per
pickup setting, how loud the dry guitar is (peak and loudness, against what a
typical passive humbucker gives), how bright, and how much low end, and
compares each setting with the bridge pickup in series, which is what most
presets assume.

The reference figures are published measurements (review/reports/gear-research.md):
with the unit's input at full scale near 14 to 15 dBu, a medium-hot passive
humbucker played firmly peaks around -18 dBFS.
"""

import numpy as np

from .measure import CENTRES, measure

TYPICAL_PEAK_DB = -18.     # a medium-hot passive humbucker, firm playing, at the unit's input level 0
BANDS = ((60, 200, "lows"), (200, 800, "low mids"), (800, 3000, "mids"), (3000, 6000, "presence"),
         (6000, 12000, "air"))


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
    reference = next((key for key in summary if "bridge" in key.lower() and "series" in key.lower()), None)
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
