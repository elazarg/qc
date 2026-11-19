"""The old app's history, brought into the new Store, so that History starts with what the player already made.

What the old app (`tone_lab`) left under `tone_sessions/`, and what becomes of it:

- `drafts/<id>.json`: a design the assistant wrote, as a list of blocks with
  their settings by name in display units, sorted in the usual order (gate,
  compressor, drive, amp, cab, equalizer, modulation, delay, reverb), with an
  optional second path, blend and scenes. Each becomes a sound of origin
  "designed", its chain laid out on the grid as the old app built it. A
  `<id>.kept.json` beside it says where it was saved on the unit.
- `rounds/<id>.json`: an aim and the drafts that answered it, with what the
  old app heard, the knob positions, the player's marks and remarks. Each
  becomes a round in the new form, and its marks, remarks and knob positions
  go onto the sounds. What the old app heard is kept in the sound's origin,
  not as `heard`: it was measured another way, on another phrase, and the new
  app measures again.
- `presets/<sha>/preset.{pb,json}` and `device_snapshots/<name>.{pb,json}`:
  presets read from the unit before the old app borrowed their slot. Each
  becomes a sound of origin "preset". Copies of the old app's own working
  slots are not sounds and are left out.
- `recordings/clip-*/`: recorded takes. Each becomes a phrase, with the
  pickup setting when its own JSON, or the Guitar Learner's label of it, says
  which; otherwise the setting is "unknown".
- The dated session folders hold recordings and measurements of comparisons,
  not chains. They are counted and left where they are.

Every new record's id is made from the old one, so running this twice adds
nothing. Anything that cannot be read is skipped and counted, with the reason.
Nothing under `tone_sessions/` is changed.
"""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil

from . import catalog, knowledge
from .chain import Block, Chain, Row, SCENES
from .sounds import Sound, Store

OLD = Path(__file__).resolve().parents[1] / "tone_sessions"
MULTI_OUT = 19
ROLES = ("Gate", "Compressor", "Drive", "Amp", "Cab", "Equalizer", "Modulation", "Delay", "Reverb")
SETLISTS = "/media/p4/Presets/"
WORKING = {("/media/p4/Presets/My Presets", 254), ("/media/p4/Presets/My Presets", 255),
           ("/media/p4/Presets/Tone Lab", 255)}       # the old app's own working slots
MARKS = {"this is it": "keeper", "not it": "not this", "closer": "closer"}
DK24 = {"1": "Bridge", "2": "Outer coils", "3": "Both humbuckers", "4": "Inner coils", "5": "Neck",
        "bridge": "Bridge", "neck": "Neck", "middle": "Both humbuckers"}
TOGGLE = {"up": "Parallel", "down": "Series", "parallel": "Parallel", "series": "Series"}


def new_id(kind, old_id):
    """The id a migrated record gets: the same every time for the same old record."""
    return sha256(f"tone_lab {kind} {old_id}".encode("utf-8")).hexdigest()[:12]


class Unreadable(ValueError):
    """An old record that cannot be turned into a new one."""


# ---- Drafts ----

def role(model_id):
    info = catalog.model(model_id)
    category = info.category if info else ""
    for word, name in (("Gate", "Gate"), ("Compressor", "Compressor"), ("Overdrive", "Drive"),
                       ("Amplifier", "Amp"), ("Cabsim", "Cab"), ("Equalizer", "Equalizer"),
                       ("Modulation", "Modulation"), ("Delay", "Delay"), ("Reverb", "Reverb")):
        if word in category:
            return name
    return None


def _layout(main, side):
    """Where the old app put each block: two rows of cells and the columns of the split and the join.

    A second path runs beside the main one from the earliest role it holds to the latest; main blocks in
    that stretch sit on their own path, the rest are shared (as `tone_lab.preset_design.layout`)."""
    if not side:
        return [list(main), []], None
    order = {name: place for place, name in enumerate(ROLES)}
    places = [order.get(role(block["model_id"]), len(ROLES)) for block in side]
    first, last = min(places), max(places)
    where = lambda block: order.get(role(block["model_id"]), len(ROLES))
    before = [block for block in main if where(block) < first]
    beside = [block for block in main if first <= where(block) <= last]
    after = [block for block in main if where(block) > last]
    width = max(len(beside), len(side))
    upper = before + beside + [None] * (width - len(beside)) + after
    lower = [None] * len(before) + list(side) + [None] * (width - len(side))
    return [upper, lower], (len(before), len(before) + width)


def _indices(info, name):
    """The parameters a setting by name moves. A cab has each control once per microphone; one setting moved
    both in the old app."""
    found = [param.index for param in info.named(name)]
    return found if info.is_stock_cab else found[:1]


def _block(spec, scenes):
    model_id = spec.get("model_id")
    if spec.get("asset"):
        raise Unreadable("it loads an impulse response or capture, which the new app cannot yet hold in a chain")
    info = catalog.model(model_id) if isinstance(model_id, int) else None
    if info is None:
        raise Unreadable(f"model {model_id!r} is not in the catalog")
    values, dropped = {}, []
    for setting in spec.get("settings") or []:
        name, value = setting.get("name"), setting.get("value")
        indices = _indices(info, name)
        if not indices or not isinstance(value, (int, float)):
            dropped.append(f"{info.name} {name}")
            continue
        for index in indices:
            values[index] = catalog.to_norm(model_id, index, value)
    for setting in spec.get("scene_settings") or []:
        scene, name, value = setting.get("scene"), setting.get("name"), setting.get("value")
        indices = _indices(info, name)
        if not indices or not isinstance(value, (int, float)) or not isinstance(scene, int) or not 0 <= scene < SCENES:
            dropped.append(f"{info.name} {name} in scene {scene}")
            continue
        for index in indices:
            base = values.get(index)
            if base is None:
                base = catalog.defaults(model_id).get(index, 0.)
            per_scene = list(base) if isinstance(base, tuple) else [base] * SCENES
            per_scene[scene] = catalog.to_norm(model_id, index, value)
            values[index] = tuple(per_scene)
    off = [scene for scene in spec.get("off_in_scenes") or [] if isinstance(scene, int) and 0 <= scene < SCENES]
    bypass = tuple(scene in off for scene in range(SCENES)) if off and scenes else False
    return Block(model_id, tuple(values.items()), bypass), dropped


def draft_chain(draft, physical_input=1):
    """The chain an old draft describes, and the settings that could not be carried over."""
    main = draft.get("chain")
    if not isinstance(main, list) or not main:
        raise Unreadable("it has no chain")
    side = draft.get("parallel") or []
    scenes = [str(name).strip()[:12] for name in draft.get("scenes") or [] if str(name).strip()]
    (upper, lower), split = _layout(main, side)
    if len(upper) > 8 or len(lower) > 8:
        raise Unreadable("it has more blocks than a row holds")
    dropped, rows = [], []
    for cells in (upper, lower):
        placed = []
        for spec in cells:
            if spec is None:
                placed.append(None)
                continue
            block, lost = _block(spec, len(scenes))
            placed.append(block)
            dropped += lost
        rows.append(placed)
    first = Row(tuple(rows[0]), physical_input, MULTI_OUT, split)
    second = Row(tuple(rows[1]))
    if split:
        blend = draft.get("blend") or {}
        level = min(max(float(blend.get("level", 0) or 0), -20.), 6.)
        spread = min(max(float(blend.get("spread", 0) or 0), 0.), 100.)
        paths = [[block.model for block in row[split[0]:split[1]] if block] for row in rows]
        mixer = {1: catalog.to_norm(catalog.MIXER, 1, 5 - spread / 20),
                 2: catalog.to_norm(catalog.MIXER, 2, level),
                 3: catalog.to_norm(catalog.MIXER, 3, 5 + spread / 20),
                 4: float(knowledge.opposed(*paths))}
        first = Row(first.cells, first.inlet, first.outlet, split, tuple(mixer.items()))
    labels = tuple(scenes) + ("",) * (SCENES - len(scenes)) if len(scenes) >= 2 else ("",) * SCENES
    return Chain((first, second, Row(), Row()), labels), dropped


def _slot_from_label(label):
    """'Goldtop Bite · My Presets 9B' → the slot My Presets position 65, or None."""
    found = re.search(r"·\s*(.+?)\s+(\d+)([A-H])\s*$", label or "")
    if not found:
        return None
    setlist, bank, letter = found.groups()
    return {"folder_key": SETLISTS + setlist, "position": (int(bank) - 1) * 8 + "ABCDEFGH".index(letter),
            "is_factory": False}


def _kept(path):
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    preset = kept.get("preset") or {}
    name = preset.get("name") or (kept.get("label") or "").split("·")[0].strip()
    slot = preset.get("position") or _slot_from_label(kept.get("label"))
    return {"slot": slot, "name": name} if name else None


def draft_sound(draft, kept=None):
    chain, dropped = draft_chain(draft)
    old_id = str(draft["id"])
    origin = {"kind": "designed", "request": draft.get("request") or "", "target": draft.get("target") or "",
              "instrument": draft.get("guitar"), "migrated": {"from": "draft", "id": old_id}}
    if dropped:
        origin["migrated"]["dropped"] = dropped
    sound = Sound(name=str(draft.get("name") or "Untitled")[:24], chain=chain, origin=origin,
                  id=new_id("draft", old_id), created=_when(draft.get("created_utc")),
                  parent=new_id("draft", draft["parent"]) if draft.get("parent") else None,
                  explanation=str(draft.get("explanation") or ""),
                  sources=[{"title": str(item.get("title", ""))[:120], "url": item.get("url")}
                           for item in draft.get("sources") or [] if isinstance(item, dict)])
    if kept:
        sound.saved = kept
    return sound


# ---- Rounds ----

def apply_round(found, sounds):
    """The round in the new form, with what the player and the old app said of each design put on its sound.
    `sounds` maps an old draft id to the new sound, for the drafts being migrated now."""
    aim = found.get("aim") or {}
    record = {"id": new_id("round", found["id"]), "request": found.get("request") or "", "aim": aim,
              "created": _when(found.get("created_utc")),
              "previous": new_id("round", found["parent"]) if found.get("parent") else None, "by": "player",
              "candidates": [], "migrated": {"from": "round", "id": str(found["id"])}}
    for candidate in found.get("candidates") or []:
        draft = candidate.get("draft")
        record["candidates"].append({"sound": new_id("draft", draft), "differs": candidate.get("differs") or ""})
        sound = sounds.get(draft)
        if sound is None:
            continue
        if aim:
            sound.tags = [f"aim: {aim.get('drive')}, {aim.get('tone')}, {aim.get('space')}"]
        heard = candidate.get("heard")
        if isinstance(heard, dict):
            sound.origin.setdefault("migrated", {})["heard"] = heard
            if all(key in heard for key in ("drive", "tone", "space")):
                sound.tags.append(f"heard by the old app: {heard['drive']}, {heard['tone']}, {heard['space']}")
        if isinstance(candidate.get("shape"), dict):
            sound.knobs = {key: value for key, value in candidate["shape"].items() if isinstance(value, (int, float))}
        marks = [MARKS.get(str(tag).casefold(), str(tag).casefold()) for tag in candidate.get("user_tags") or []]
        if marks and hasattr(sound, "marks"):
            sound.marks = list(dict.fromkeys(marks))
        if candidate.get("review"):
            sound.notes = [{"at": record["created"], "text": str(candidate["review"])}]
        sound.played = bool(candidate.get("played"))
    return record


# ---- Presets read from the unit ----

def _preset_snapshots(old):
    """(old id, metadata, path of the preset message) for every preset the old app archived."""
    found = []
    for folder in sorted((old / "presets").glob("*")):
        if (folder / "preset.pb").is_file():
            found.append((folder.name, folder / "preset.json", folder / "preset.pb"))
    for pb in sorted((old / "device_snapshots").glob("*.pb")):
        found.append((f"device_snapshots/{pb.stem}", pb.with_suffix(".json"), pb))
    return found


def preset_sound(old_id, info, chain):
    position = info.get("position") or {}
    name = info.get("name") or info.get("preset") or "Preset"
    slot = {key: position.get(key) for key in ("folder_key", "position", "is_factory")} if position else None
    return Sound(name=str(name)[:32], chain=chain,
                 origin={"kind": "preset", "slot": slot, "firmware": info.get("firmware"),
                         "migrated": {"from": "preset snapshot", "id": old_id, "reason": info.get("reason")}},
                 id=new_id("preset", old_id), created=_when(info.get("captured_utc")))


def _read_preset(blob):
    from qc_mcp import preset as wire
    from .chain import from_preset

    message = wire.BinaryPreset()
    message.ParseFromString(blob)
    return from_preset(message)


# ---- Recordings ----

def clip_setting(info, instrument, learned=None, clips=None):
    """The pickup setting a take was played on, in the desk's words ("Neck · Parallel"), or "unknown"."""
    if isinstance(info.get("pickup"), str) and info["pickup"].strip():
        return info["pickup"].strip()
    positions = info.get("pickup_positions")
    if isinstance(positions, list) and positions:
        place = DK24.get(str(positions[0]).casefold()) if instrument == "Charvel" else str(positions[0]).title()
        wiring = TOGGLE.get(str(positions[1]).casefold()) if len(positions) > 1 else None
        if place:
            return f"{place} · {wiring}" if wiring else place
    context = info.get("pickup_context_clip")
    if context and clips and context in clips and clips[context] is not info:
        return clip_setting(clips[context], instrument, None, None)
    if learned:
        return learned
    return "unknown"


def learner_labels(old):
    """The Guitar Learner's label for each take it was taught with, by take name, where its labels agree and
    cover nearly the whole take."""
    found = {}
    for path in (old / "guitar_learner").glob("*/*/*/record.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        source, setup = Path(str(record.get("source") or "")), record.get("configuration") or {}
        take = source.parent.name
        if not take.startswith("clip-") or not isinstance(setup, dict):
            continue
        place = DK24.get(str(setup.get("pickup")))
        wiring = TOGGLE.get(str(setup.get("wiring") or "").casefold())
        if place:
            found.setdefault(take, []).append((f"{place} · {wiring}" if wiring else place,
                                               float(record.get("seconds") or 0)))
    return found


def _learner_setting(entries, seconds):
    settings = {setting for setting, _ in entries}
    covered = max((length for _, length in entries), default=0)
    if len(settings) == 1 and seconds and covered >= .8 * seconds:
        return settings.pop()
    return None


# ---- The whole of it ----

def migrate(folder, old=OLD, *, dry_run=False):
    """Bring the old history in `old` into the Store in `folder`. Returns what was done, or with `dry_run`
    what would be, as counts and the reasons anything was skipped. Nothing in `old` is changed."""
    old, folder = Path(old), Path(folder)
    report = {"sounds": {"designed": 0, "preset": 0}, "rounds": 0, "phrases": {"known": 0, "unknown": 0},
              "already": {"sounds": 0, "rounds": 0, "phrases": 0}, "skipped": [], "left": {}}
    store = None if dry_run else Store(folder)
    have = _existing(folder)

    def skip(what, why):
        report["skipped"].append({"what": what, "why": why})

    # Drafts first, so that rounds can put what was said onto them before they are written.
    drafts = {}
    for path in sorted((old / "drafts").glob("*.json")):
        if path.name.endswith(".kept.json"):
            continue
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(draft, dict) or not draft.get("id"):
                raise Unreadable("it is not a draft")
            sound = draft_sound(draft, _kept(path.with_name(f"{path.stem}.kept.json")))
        except Unreadable as error:
            skip(f"draft {path.stem}", str(error))
            continue
        except (OSError, ValueError, KeyError, TypeError) as error:
            skip(f"draft {path.stem}", f"it could not be read ({type(error).__name__}: {error})")
            continue
        drafts[str(draft["id"])] = sound
    # A draft whose parent did not come across has no parent.
    for sound in drafts.values():
        if sound.parent and sound.parent not in {item.id for item in drafts.values()} | have["sounds"]:
            sound.parent = None

    rounds = []
    for path in sorted((old / "rounds").glob("*.json")):
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(found, dict) or not found.get("id"):
                raise Unreadable("it is not a round")
            fresh = {draft: sound for draft, sound in drafts.items() if sound.id not in have["sounds"]}
            record = apply_round(found, fresh)
        except Unreadable as error:
            skip(f"round {path.stem}", str(error))
            continue
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            skip(f"round {path.stem}", f"it could not be read ({type(error).__name__}: {error})")
            continue
        known = {sound.id for sound in drafts.values()} | have["sounds"]
        record["candidates"] = [item for item in record["candidates"] if item["sound"] in known]
        if not record["candidates"]:
            skip(f"round {path.stem}", "none of its designs could be brought across")
            continue
        rounds.append(record)
    ids = {record["id"] for record in rounds} | have["rounds"]
    for record in rounds:
        if record["previous"] not in ids:
            record["previous"] = None
        for earlier in rounds:
            if earlier["id"] == record["previous"]:
                earlier["next"] = record["id"]

    for sound in drafts.values():
        if sound.id in have["sounds"]:
            report["already"]["sounds"] += 1
            continue
        report["sounds"]["designed"] += 1
        if store:
            store.save(sound)
    for record in rounds:
        if record["id"] in have["rounds"]:
            report["already"]["rounds"] += 1
            continue
        report["rounds"] += 1
        if not dry_run:
            (folder / "rounds").mkdir(parents=True, exist_ok=True)
            _write_json(folder / "rounds" / f"{record['id']}.json", record)

    # Presets read from the unit. The same preset in the same slot, unchanged, is one sound.
    seen, left = set(), 0
    for old_id, meta, pb in _preset_snapshots(old):
        try:
            info = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
            position = info.get("position") or {}
            if (position.get("folder_key"), position.get("position")) in WORKING:
                left += 1
                continue
            chain = _read_preset(pb.read_bytes())
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            skip(f"preset {old_id}", f"it could not be read ({type(error).__name__}: {str(error)[:100]})")
            continue
        except Exception as error:  # noqa: BLE001  the message parser raises its own kinds
            skip(f"preset {old_id}", f"it could not be read ({type(error).__name__})")
            continue
        if not chain.blocks:
            skip(f"preset {old_id}", "it holds no blocks")
            continue
        key = (info.get("name") or info.get("preset"), position.get("folder_key"), position.get("position"),
               chain.digest())
        if key in seen:
            left += 1
            continue
        seen.add(key)
        sound = preset_sound(old_id, info, chain)
        for draft in drafts.values():
            # A kept draft and the preset it became: the preset is the draft's child.
            if draft.saved and draft.saved.get("slot") == sound.origin["slot"] and draft.saved["name"] == sound.name:
                sound.parent = draft.id
        if sound.id in have["sounds"]:
            report["already"]["sounds"] += 1
            continue
        report["sounds"]["preset"] += 1
        if store:
            store.save(sound)
    report["left"]["copies of the old app's working slots, or repeats"] = left

    # Recorded takes.
    clips, labels = {}, learner_labels(old)
    for take in sorted((old / "recordings").glob("clip-*")):
        try:
            clips[take.name] = json.loads((take / "clip.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            clips[take.name] = {}
    for take in sorted((old / "recordings").glob("clip-*")):
        dry = take / "dry.wav"
        if not dry.is_file():
            skip(f"recording {take.name}", "it has no dry take")
            continue
        info = clips[take.name]
        instrument = info.get("guitar") or "unknown"
        learned = _learner_setting(labels.get(take.name, []), info.get("duration_seconds"))
        setting = clip_setting(info, instrument, learned, clips)
        phrase_id = new_id("recording", take.name)
        if phrase_id in have["phrases"]:
            report["already"]["phrases"] += 1
            continue
        report["phrases"]["unknown" if setting == "unknown" else "known"] += 1
        if dry_run:
            continue
        record = {"id": phrase_id, "instrument": instrument, "setting": setting,
                  "recorded": datetime.fromtimestamp(dry.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
                  "seconds": round(float(info.get("duration_seconds") or 0), 2),
                  "migrated": {"from": "recording", "id": take.name},
                  **{key: info[key] for key in ("song", "artist", "passage", "note", "pickup_note") if info.get(key)}}
        target = folder / "phrases" / phrase_id
        staging = folder / "phrases" / f".{phrase_id}.part"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        shutil.copy2(dry, staging / "dry.wav")
        _write_json(staging / "phrase.json", record)
        staging.replace(target)

    report["left"]["comparison sessions (recordings and measurements, no chain)"] = sum(
        1 for path in old.glob("2*-*") if (path / "session.json").is_file())
    return report


def _existing(folder):
    """What an earlier migration (or the app) already holds: sound, round and phrase ids."""
    found = {"sounds": set(), "rounds": set(), "phrases": set()}
    for path in (folder / "sounds").glob("*.json"):
        found["sounds"].add(path.stem)
    for path in (folder / "rounds").glob("*.json"):
        found["rounds"].add(path.stem)
    for path in (folder / "phrases").glob("*/phrase.json"):
        found["phrases"].add(path.parent.name)
    return found


def _write_json(path, data):
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    part.replace(path)


def _when(text):
    if not text:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        return datetime.fromisoformat(str(text)).astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return str(text)


def summary(report):
    """The report in a few plain lines."""
    lines = [f"{report['sounds']['designed']} designs and {report['sounds']['preset']} presets as sounds, "
             f"{report['rounds']} rounds, {report['phrases']['known']} recordings with their pickup setting and "
             f"{report['phrases']['unknown']} without."]
    already = report["already"]
    if any(already.values()):
        lines.append(f"Already there: {already['sounds']} sounds, {already['rounds']} rounds, "
                     f"{already['phrases']} recordings.")
    for what, count in report["left"].items():
        if count:
            lines.append(f"Left where they are: {count} {what}.")
    if report["skipped"]:
        lines.append(f"Skipped {len(report['skipped'])}: " + "; ".join(f"{item['what']} ({item['why']})"
                                                                       for item in report["skipped"][:8]))
    return lines


if __name__ == "__main__":
    import sys

    dry = "--dry-run" in sys.argv
    for line in summary(migrate(knowledge.home(), dry_run=dry)):
        print(line)
