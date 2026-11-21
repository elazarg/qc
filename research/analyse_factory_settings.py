"""What do Neural DSP's own preset names say each parameter does?

Pooled contrasts over the device presets harvested by `python -m lab named-settings`.
Run from the repository root: python research/analyse_factory_settings.py
"""
import collections, json, re, sys
from pathlib import Path
import numpy as np
from scipy import stats
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import catalog_rows as models  # noqa: E402

from lab import knowledge  # noqa: E402

data = json.loads(knowledge.device_path().read_text(encoding="utf-8"))["models"]
PAIRS = {
    "bright_vs_dark": (r"\b(bright|brite|brighten|brighter|sparkl\w*|glass\w*|chim\w*|crisp\w*|treble)\b", r"\b(dark|darker|warm|mellow|dull|muffl\w*|wool\w*)\b"),
    "tight_vs_loose": (r"\b(tight|chug\w*|percussive|focus\w*|lean)\b", r"\b(flub\w*|fat|thick\w*|weighty|massive|boom\w*|bassy|big|loose|huge)\b"),
    "mid_vs_scoop": (r"\b(mid|mids|middy|honk\w*|nasal)\b", r"\b(scoop\w*)\b"),
}
LADDER = [(r"\b(clean|cleanish|headroom)\b", 0), (r"\b(edge|breakup|barely|verge|pushed|hair|grit|gritty)\b", 1), (r"\b(crunch\w*)\b", 2),
          (r"\b(drive|driven|overdrive|od|rhythm|dist\w*)\b", 3), (r"\b(lead|high gain|heavy|full on|max\w*|saturat\w*|hot|extreme|fuzz\w*|all in|mayhem)\b", 4)]
only = sys.argv[1] if len(sys.argv) > 1 else None

def norm(model_id, name_index, value, param):
    try:
        lo, hi = float(param["min"]), float(param["max"])
    except (TypeError, ValueError):
        return None
    return (value - lo) / (hi - lo) if hi > lo else None

pooled = {k: collections.defaultdict(list) for k in PAIRS}
gain = collections.defaultdict(list)
per_model_gain = {}
for mid, m in data.items():
    if not m["available"] or (only and m["category"] != only): continue
    info = models.lookup(int(mid))
    presets = [p for p in m["presets"] if not p["name"].lower().startswith("bass ")]
    cols = {}
    for i, param in enumerate(info["params"]):
        if param["type"] not in ("float", "fader"): continue
        vals = []
        for p in presets:
            v = p["values"][i][1] if i < len(p["values"]) else None
            vals.append(norm(mid, i, v, param) if isinstance(v, (int, float)) else None)
        if all(v is not None for v in vals) and len(set(vals)) > 1:
            cols[(i, param["name"])] = np.array(vals)
    names = [p["name"].lower() for p in presets]
    for key, (a, b) in PAIRS.items():
        A = np.array([bool(re.search(a, n)) and not re.search(b, n) for n in names])
        B = np.array([bool(re.search(b, n)) and not re.search(a, n) for n in names])
        if A.any() and B.any():
            for (i, pname), v in cols.items():
                pooled[key][(m["category"], pname)].append(v[A].mean() - v[B].mean())
    ranks = np.array([next((r for pat, r in reversed(LADDER) if re.search(pat, n)), np.nan) for n in names])
    ok = ~np.isnan(ranks)
    if ok.sum() >= 4 and len(set(ranks[ok])) >= 3:
        best = []
        for (i, pname), v in cols.items():
            rho = stats.spearmanr(ranks[ok], v[ok]).statistic
            if not np.isnan(rho):
                gain[(m["category"], pname)].append(rho); best.append((rho, pname))
        if best:
            per_model_gain[m["name"]] = sorted(best, reverse=True)[:2]

for key, table in pooled.items():
    print(f"\n=== {key}: mean normalised difference (first word class minus second), models, share with the same sign")
    rows = [(cat, p, np.mean(d), len(d), np.mean(np.sign(d) == np.sign(np.mean(d)))) for (cat, p), d in table.items() if len(d) >= 2]
    for cat, p, mean, n, share in sorted(rows, key=lambda r: (r[0], -abs(r[2]))):
        if abs(mean) >= .06:
            print(f"  {cat:18} {p:16} {mean:+.2f}  n={n:3}  same-sign {share:.0%}")
print("\n=== gain ladder: mean Spearman rho between the clean→lead rank of the preset name and the parameter")
rows = [(cat, p, np.mean(r), len(r)) for (cat, p), r in gain.items() if len(r) >= 2]
for cat, p, mean, n in sorted(rows, key=lambda r: (r[0], -r[2])):
    if abs(mean) >= .25:
        print(f"  {cat:18} {p:16} {mean:+.2f}  n={n}")
print("\n=== per model: parameter that best follows the gain ladder")
odd = {name: best for name, best in per_model_gain.items() if best[0][1] not in ("GAIN", "DRIVE", "DISTORTION", "OVERDRIVE")}
for name, best in odd.items():
    print(f"  {name:34} {best}")
print(len(per_model_gain), "models ranked;", len(odd), "whose best parameter is not GAIN/DRIVE/DISTORTION/OVERDRIVE")
