"""Compress the research files in this folder into the knowledge the app loads.

Run from the repository root: python research/build_knowledge.py
Every model id, parameter name and setting is checked against the Quad Cortex
catalog, and anything that does not match is dropped and reported.
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import catalog_rows as models  # noqa: E402

HERE = Path(__file__).resolve().parent
TARGET = HERE.parent / "lab" / "tables" / "gear.json"
dropped = []


def load(name):
    path = HERE / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def short(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


def params(model_id):
    return {param["name"]: param for param in models.lookup(model_id)["params"]}


def settings(model_ids, values, where):
    """Keep numeric settings that every listed model can take, inside its range."""
    kept = {}
    for name, value in (values or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        ok = False
        for model_id in model_ids:
            param = params(model_id).get(name)
            try:
                ok = param is not None and float(param["min"]) <= value <= float(param["max"])
            except (TypeError, ValueError):
                ok = False
            if ok:
                break
        if ok:
            kept[name] = value
        else:
            dropped.append(f"{where}: {name}={value}")
    return kept


def named(category):
    return {model["name"]: model["id"] for model in models.all_models() if model["category"] == category}


def build():
    drives_by_name, cabs_by_name = named("Guitar Overdrive"), named("Cabsim Guitar (M)")
    amps = {}
    for source in ("amps-classic.json", "amps-highgain.json"):
        for family in load(source).get("families", []):
            ids = [model_id for model_id in family["models"]
                   if (models.lookup(model_id) or {}).get("category") == "Guitar Amplifier"]
            if len(ids) != len(family["models"]):
                dropped.append(f"{family['key']}: unknown amp ids")
            names = set().union(*(params(model_id) for model_id in ids)) if ids else set()
            controls = {}
            for name, note in family.get("controls", {}).items():
                if name not in names:
                    dropped.append(f"{family['key']}: control {name}")
                    continue
                controls[name] = {"does": short(note.get("does"), 170),
                                  **{key: note[key] for key in ("brighter", "tighter")
                                     if note.get(key) in ("up", "down")}}
            amps[family["key"]] = {
                "real": short(family["real"], 150), "models": ids, "plugin": family.get("plugin", ""),
                "gain_range": family.get("gain_range", ""), "drive_control": family.get("drive_control", ""),
                "voice": short(family.get("voice"), 170), "controls": controls,
                "model_notes": {key: short(value, 170) for key, value in family.get("model_notes", {}).items()
                                if int(key) in ids},
                "starts": [{"name": short(start["name"], 60), "basis": start.get("basis", "typical"),
                            "settings": kept,
                            **({"models": [model_id for model_id in start["models"] if model_id in ids]}
                               if start.get("models") else {})}
                           for start in family.get("starts", [])
                           if (kept := settings(ids, start.get("settings"), f"{family['key']} start"))],
                "boosts": [{"model": drives_by_name[boost["model"]], "why": short(boost.get("why"), 150),
                            "settings": settings([drives_by_name[boost["model"]]], boost.get("settings"),
                                                 f"{family['key']} boost")}
                           for boost in family.get("boosts", []) if boost.get("model") in drives_by_name],
                "cabs": [{"model": cabs_by_name[cab["model"]], "why": short(cab.get("why"), 150)}
                         for cab in family.get("cabs", []) if cab.get("model") in cabs_by_name],
                "known_for": [short(item, 90) for item in family.get("known_for", [])][:4],
                "poor_at": short(family.get("poor_at"), 170), "watch_out": short(family.get("watch_out"), 220),
                "tags": family.get("tags", []),
            }
    research = load("drives-effects.json")
    drives = {}
    for drive in research.get("drives", []):
        if (models.lookup(drive["id"]) or {}).get("category") != "Guitar Overdrive":
            dropped.append(f"drive {drive['id']}")
            continue
        names = params(drive["id"])
        drives[str(drive["id"])] = {
            "real": short(drive.get("real"), 90), "plugin": drive.get("plugin", ""), "type": drive.get("type", ""),
            "signature": short(drive.get("signature"), 170),
            "controls": {name: {"does": short(note.get("does"), 170),
                                **({"brighter": note["brighter"]} if note.get("brighter") in ("up", "down") else {})}
                         for name, note in drive.get("controls", {}).items() if name in names},
            "roles": [{"role": role["role"], "basis": role.get("basis", "typical"), "settings": kept}
                      for role in drive.get("roles", [])
                      if (kept := settings([drive["id"]], role.get("settings"), f"drive {drive['id']} role"))],
            "pairs_with": drive.get("pairs_with", []), "watch_out": short(drive.get("watch_out"), 200),
            "tags": drive.get("tags", []),
        }
    effects = {str(effect["id"]): {"real": short(effect.get("real"), 80), "character": short(effect.get("character"), 170),
                                   "good_for": effect.get("good_for", [])[:4]}
               for effect in research.get("effects", []) if models.lookup(effect["id"])}
    cab_research = load("cabs-mics.json")
    cabs = {}
    for cab in cab_research.get("cabs", []):
        if (models.lookup(cab["id"]) or {}).get("category") != "Cabsim Guitar (M)":
            dropped.append(f"cab {cab['id']}")
            continue
        cabs[str(cab["id"])] = {"real": short(cab.get("real"), 90), "config": cab.get("config", ""),
                                "back": cab.get("back", ""), "speaker": short(cab.get("speaker"), 60),
                                "character": short(cab.get("character"), 170),
                                "pairs_with": cab.get("pairs_with", []), "tags": cab.get("tags", [])}
    york = cab_research.get("york_mes_212_v30", {})
    words = load("vocabulary.json")
    recipes = [{"target": recipe["target"], "aliases": recipe.get("aliases", []),
                **({"sources": recipe["verified_sources"]} if "verified_sources" in recipe else {}),
                "documented_gear": short(recipe.get("documented_gear"), 420),
                "chain": [{key: short(block.get(key), 150) for key in ("block", "choice", "settings", "why")
                           if block.get(key)} for block in recipe.get("chain", [])],
                "guitar": short(" ".join(filter(None, (recipe.get("guitar_setting", {}).get(key)
                                                        for key in ("choice", "settings")))), 200),
                "playing": short(recipe.get("playing_notes"), 220)}
               for recipe in words.get("recipes", [])]
    charvel = words.get("guitars", {}).get("charvel_dk24_hh", {})
    paul = words.get("guitars", {}).get("les_paul", {})
    guitars = {}
    if charvel:
        guitars["charvel"] = {
            "pickups": short(charvel.get("pickups"), 330),
            "positions": [{"position": row["position"], "selects": short(row["selects"], 70),
                           "sound": short(row["sound"], 200), "suits": short(row["suits"], 220)}
                          for row in charvel.get("positions", [])],
            "toggle": short(charvel.get("toggle"), 260), "tone_control": short(charvel.get("tone_control"), 220)}
    if paul:
        guitars["les paul"] = {
            "differences": short(paul.get("differences"), 420),
            "compensations": [{"action": short(row["action"], 140), "why": short(row.get("why"), 150)}
                              for row in paul.get("compensations", [])]}
    knowledge = {
        "amps": amps, "drives": drives, "cabs": cabs, "effects": effects,
        "mics": [{"name": mic["qc_name"], "real": mic.get("real", ""), "character": short(mic.get("character"), 200),
                  "blend_with": mic.get("blend_with", [])[:3]} for mic in cab_research.get("mics", [])],
        "speakers": [{"name": speaker["name"], "character": short(speaker.get("character"), 180)}
                     for speaker in cab_research.get("speakers", [])],
        "irs": {"YA MES 212 V30": {
            "about": short(york.get("description"), 260),
            "mics": york.get("mic_abbreviations", {}),
            "mixes": {mix["file"]: short(mix["description"].split(" (")[0], 40) for mix in york.get("mixes", [])},
        }} if york else {},
        "recipes": recipes, "guitars": guitars,
    }
    TARGET.parent.mkdir(exist_ok=True)
    TARGET.write_text(json.dumps(knowledge, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    covered = {model_id for family in amps.values() for model_id in family["models"]}
    every = {model["id"] for model in models.all_models() if model["category"] == "Guitar Amplifier"}
    print(f"{len(amps)} amp families covering {len(covered)} of {len(every)} amp models, {len(drives)} drives, "
          f"{len(cabs)} cabs, {len(effects)} effects, {TARGET.stat().st_size // 1024} KB")
    if every - covered:
        print("amps without knowledge:", sorted(every - covered))
    print(f"{len(dropped)} items dropped:", *dropped[:60], sep="\n  ")


if __name__ == "__main__":
    build()
