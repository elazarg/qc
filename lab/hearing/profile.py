"""What the player's own instrument puts out, from phrases recorded with each pickup setting.

Presets are made by other people, on other guitars, and assume an input they
were built for. A preset that feels thin, weak or harsh on this guitar may be
hearing less level, or another balance, than its maker's.

What is measured, per pickup setting, is the takes recorded on it, and every
sentence says so ("in these takes"): a take is the pickup and the playing
together.

- Level: the peak of firmly held notes (the 90th percentile of the take's
  50 ms peaks, the level the test tones are played at), the loudness (ITU-R
  BS.1770) and the largest single peak, which one hard-picked note sets and
  which is shown but not reasoned from. The held-note peak is set against what
  a typical passive humbucker gives.
- The noise floor: the level of the quietest tenth of the take's 100 ms
  stretches, when those are at least 20 dB under the held notes (otherwise the
  take has no silence to measure it in).
- Against the bridge pickup in series, which is what most presets assume:
  loudness, the spectral centre, and the level in each band with the overall
  level taken off (`measure.band_gaps`). What is played decides all three as
  much as the pickup does (the same phrase an octave up reads most of an octave
  brighter), so two settings are compared only on takes of the same gesture: a
  take may carry a gesture tag ("open strings strummed once"), and settings
  that share none are said not to be comparable instead of being compared.

The reference figures are published measurements (gathered in the gear research for the rewrite):
with the unit's input at full scale near 14 to 15 dBu, a medium-hot passive
humbucker played firmly peaks around -18 dBFS.
"""

import numpy as np

from lab.hearing.audio import RATE, db
from lab.hearing.measure import BANDS, band_gaps, band_levels_db, level, measure
from lab.hearing.probe import held_note_peak

TYPICAL_PEAK_DB = -18.     # a medium-hot passive humbucker, firm playing, at the unit's input level 0
# Gestures worth offering when a phrase is recorded for the profile: anything played the same way on every setting.
GESTURES = ("open strings strummed once and left to ring", "each open string picked once", "the same riff")
WORDS = tuple(word for _, _, _, word in BANDS)       # lows, low mids, mids, presence, air
LOWS = BANDS[0][0]
SILENCE_UNDER_DB = 20.     # a stretch this far under the held notes counts as silence


def reference_of(summary):
    """The setting most presets assume, the bridge pickup in series, when a phrase was recorded on it."""
    return next((key for key in summary if "bridge" in key.lower() and "series" in key.lower()), None)


def _shared_gesture(item, base):
    """How two settings can be compared: (True, the gesture both were recorded with), (True, None) for summaries
    that carry no gestures at all (made before takes had them), or (False, None)."""
    if "by_gesture" not in item or "by_gesture" not in base:
        return True, None
    shared = sorted(set(item["by_gesture"]) & set(base["by_gesture"]))
    return (True, shared[0]) if shared else (False, None)


def _setting(summary, setting, reference):
    item = summary[setting]
    held = item.get("held_peak_db")
    level_db = item["peak_db"] if held is None else held
    difference = round(level_db - TYPICAL_PEAK_DB, 1)
    found = {"peak_db": item["peak_db"], "peak_against_typical_humbucker_db": difference,
             "level_from": "the largest single peak" if held is None
                           else "firmly held notes (the 90th percentile of 50 ms peaks)"}
    if held is not None:
        found["held_peak_db"] = held
    if item.get("noise_db") is not None:
        found["noise_db"] = item["noise_db"]
    if abs(difference) <= 3:
        implies = ["in these takes about as hot as the humbucker presets assume: amps should distort about as "
                   "their settings suggest"]
    elif difference < 0:
        implies = [f"in these takes about {abs(difference):.0f} dB weaker than presets assume: amps and drives may "
                   "distort less, and sound cleaner and quieter, than their settings suggest"]
    else:
        implies = [f"in these takes about {difference:.0f} dB hotter than presets assume: amps and drives may "
                   "distort more than their settings suggest, and clean sounds may break up"]
    base = summary.get(reference) if reference else None
    if base and setting != reference:
        comparable, gesture = _shared_gesture(item, base)
        if not comparable:
            found["not_compared"] = (f"not compared: whether it is louder or brighter than {reference} cannot be told "
                                     "from takes that are not marked as the same gesture, since what is played "
                                     "changes these figures as much as the pickup does")
            implies.append(found["not_compared"])
        else:
            mine, theirs = (item["by_gesture"][gesture], base["by_gesture"][gesture]) if gesture else (item, base)
            against = {"gesture": gesture} if gesture else {}
            if mine.get("lufs") is not None and theirs.get("lufs") is not None:
                against["loudness_db"] = round(mine["lufs"] - theirs["lufs"], 1)
            if mine.get("centroid_hz") and theirs.get("centroid_hz"):
                against["brightness_octaves"] = round(float(np.log2(mine["centroid_hz"] / theirs["centroid_hz"])), 2)
            if mine.get("spectrum_db") and theirs.get("spectrum_db"):
                # Each band against the reference setting's, with the difference in overall level taken off.
                against["bands_db"] = band_gaps(theirs["spectrum_db"], mine["spectrum_db"])
                against["lows_db"] = against["bands_db"][LOWS]
            else:
                lows = mine.get("bands", {}).get("lows")
                if lows is not None and theirs.get("bands", {}).get("lows") is not None:
                    against["lows_db"] = round(lows - theirs["bands"]["lows"], 1)
            found["against_reference"] = against
            where = f"on the same gesture ({gesture})" if gesture else "in these takes"
            bright = against.get("brightness_octaves", 0)
            if bright > .2:
                implies.append(f"brighter than {reference} {where} (a higher spectral centre): sounds made for it "
                               "may come out brighter")
            elif bright < -.2:
                implies.append(f"darker than {reference} {where} (a lower spectral centre): sounds made for it may "
                               "come out darker")
            if against.get("lows_db", 0) < -2:
                implies.append(f"thinner in the lows than {reference}")
            elif against.get("lows_db", 0) > 2:
                implies.append(f"fuller in the lows than {reference}")
    found["implies"] = "; ".join(implies) + "."
    return found


def facts(summary, setting=None):
    """What the guitar put out in the takes recorded, compactly, for the designer: the setting the phrase was
    recorded on (or every setting), its held-note peak against a typical passive humbucker in dB, and, where the
    takes are of the same gesture, its loudness, spectral centre (octaves) and level by band against the bridge
    pickup in series, with what that may mean in plain words. None when no setting has been profiled."""
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


def noise_floor_db(dry, held_db):
    """The level, in dBFS, of the quietest tenth of the take's 100 ms stretches: the noise and hum in its silences.
    None when even those are within SILENCE_UNDER_DB of the held notes (the take has no silence), or the take is
    shorter than a second."""
    frame = RATE // 10
    samples = np.asarray(dry, dtype=np.float64).reshape(len(dry), -1)
    levels = sorted(level(samples[i:i + frame]) for i in range(0, len(samples) - frame + 1, frame))
    if len(levels) < 10:
        return None
    quiet = float(np.median(levels[:max(1, len(levels) // 10)]))
    return round(quiet, 1) if quiet <= held_db - SILENCE_UNDER_DB else None


def describe_take(dry):
    """One take: its levels, its loudness, its spectral centre, each band's share of its energy, its third-octave
    spectrum and the noise floor in its silences."""
    found = measure(dry)
    levels = band_levels_db(dry)
    total = 10 * np.log10(sum(10 ** (levels[name] / 10) for name, _, _, _ in BANDS) + 1e-30)
    shares = {word: round(float(levels[name] - total), 1) for name, _, _, word in BANDS}
    held = round(db(held_note_peak(dry)), 1)
    return {"peak_db": round(found.peak_db, 1), "held_peak_db": held,
            "lufs": None if found.lufs is None else round(found.lufs, 1), "centroid_hz": round(found.centroid_hz),
            "bands": shares, "spectrum_db": list(found.bands_db), "noise_db": noise_floor_db(dry, held)}


def _together(found):
    """Several takes as one: medians, and the largest of the single peaks."""
    def median(key):
        values = [item[key] for item in found if item.get(key) is not None]
        return round(float(np.median(values)), 1) if values else None

    return {"takes": len(found), "peak_db": max(item["peak_db"] for item in found), "held_peak_db": median("held_peak_db"),
            "lufs": median("lufs"), "centroid_hz": round(float(np.median([item["centroid_hz"] for item in found]))),
            "bands": {word: round(float(np.median([item["bands"][word] for item in found])), 1) for word in WORDS},
            "spectrum_db": [round(float(value), 2) for value in np.median([item["spectrum_db"] for item in found], axis=0)],
            "noise_db": median("noise_db")}


def profile(takes):
    """takes: [(setting, dry audio)] or [(setting, dry audio, gesture)], the gesture a tag shared by takes played
    the same way (None when not known). Returns {setting: summary} and plain sentences about it. Each summary
    carries "by_gesture": the same figures for the takes of each tagged gesture, which is what settings are
    compared on."""
    groups = {}
    for take in takes:
        setting, dry, gesture = (*take, None)[:3]
        groups.setdefault(setting, []).append((gesture or None, describe_take(dry)))
    summary = {}
    for setting, found in groups.items():
        summary[setting] = _together([item for _, item in found])
        gestures = sorted({gesture for gesture, _ in found if gesture})
        summary[setting]["by_gesture"] = {gesture: _together([item for tag, item in found if tag == gesture])
                                          for gesture in gestures}
    lines = []
    reference = reference_of(summary)
    uncompared = False
    for setting, item in summary.items():
        said = _setting(summary, setting, reference)
        takes_said = "this take" if item["takes"] == 1 else f"these {item['takes']} takes"
        line = (f"{setting}: in {takes_said} firmly held notes peak around {item['held_peak_db']:g} dBFS (the largest "
                f"single peak is {item['peak_db']:g})")
        difference = said["peak_against_typical_humbucker_db"]
        line += (", about as hot as the humbucker most presets assume" if abs(difference) <= 3 else
                 f", {abs(difference):.0f} dB {'hotter' if difference > 0 else 'weaker'} than the humbucker most "
                 "presets assume")
        if item["noise_db"] is not None:
            line += f"; noise in the silences at {item['noise_db']:g} dBFS"
        against = said.get("against_reference")
        if "not_compared" in said:
            uncompared = True
            line += f"; {said['not_compared']}"
        elif against:
            parts = []
            if "loudness_db" in against and abs(against["loudness_db"]) >= .5:
                parts.append(f"{abs(against['loudness_db']):.0f} dB {'louder' if against['loudness_db'] > 0 else 'quieter'}")
            elif "loudness_db" in against:
                parts.append("as loud")
            if abs(against.get("brightness_octaves", 0)) > .2:
                parts.append("a higher spectral centre" if against["brightness_octaves"] > 0 else "a lower spectral centre")
            moved = {name: value for name, value in (against.get("bands_db") or {}).items() if abs(value) >= 2}
            parts += [f"{name} {value:+.0f} dB" for name, value in moved.items()]
            if parts:
                line += f"; against {reference} on the same gesture ({against['gesture']}): " + ", ".join(parts)
        lines.append(line + ".")
    if reference is None:
        lines.append("Record the same gesture on the bridge pickup in series too, to compare every setting with "
                     "what most presets were built on.")
    if uncompared:
        lines.append("To compare settings, record the same gesture on each (for example " + GESTURES[0] +
                     ") and mark the takes with it: what is played changes these figures as much as the pickup does.")
    return summary, lines
