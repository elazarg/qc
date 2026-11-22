"""Labelled dry excerpts: the one store of examples the learner is taught from and checked against.

An excerpt is kept as a copy, `<folder>/<id>/dry.wav`, beside its `record.json` (`keep_excerpt`); the take it
came from is never changed. The Guitar Learner keeps a configuration with each (`add`, per guitar and control
layout); teaching one control keeps a setting name (`lab.learner.taught`), in the same form.

Names of pickup settings are the instrument's own, from `lab.sound.sounds.INSTRUMENTS`: a layout's
"pickups" are that instrument's settings in blade order, and `setting_name` writes a configuration as the
desk writes a phrase's setting ("Neck · Series").

Two things keep a check honest. A recording used to teach can never be a check (`source_id`: the whole
recording, or the whole run of live listening a kept clip came from). And a check counts as from a separate
recording session only when it was recorded on another day than every teaching example (`session`: the day
in the take's name).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import uuid4

import numpy as np

from lab.hearing import audio
from lab.learner import folders
from lab.learner.folders import write_json
from lab.sound.sounds import INSTRUMENTS

SCHEMA = 1
ROLES = ("example", "test")
def _instrument_settings(name):
    item = next(item for item in INSTRUMENTS if item["name"] == name)
    return tuple(item["settings"]), tuple(item["wiring"])


def _layout_of(name, instrument, knobs, *, wiring=False):
    pickups, switch = _instrument_settings(instrument)
    layout = {"name": name, "instrument": instrument, "pickups": pickups, "knobs": knobs}
    if wiring:
        layout["wiring"] = switch
    return layout


LAYOUTS = {
    "dk24_hh_toggle": _layout_of("DK24 · 5-way + mini-toggle", "Charvel", ("volume", "tone"), wiring=True),
    "dk24_blade": _layout_of("DK24 · 5-way", "Charvel", ("volume", "tone")),
    "les_paul": _layout_of("Les Paul · 3-way", "Les Paul", ("bridge_volume", "bridge_tone", "neck_volume", "neck_tone")),
}
_WRITE = Lock()


@dataclass(frozen=True)
class DrySource:
    path: Path
    label: str
    passage: tuple[float, float] | None = None
    guitar: str | None = None
    kind: str = "wav"
    mapping: str | None = None
    duration: float | None = None
    # The recording session (the day it was recorded), when known.
    session: str | None = None
    # For a clip kept from live listening: that run of listening, and where the clip starts in it (seconds).
    run: tuple[str, float] | None = None
    # Original recording hash, start in that recording, and saved excerpt hash.
    origin: tuple[str, float, str] | None = None


@dataclass(frozen=True)
class DryExcerpt:
    samples: np.ndarray
    source_id: str
    excerpt_id: str
    source_start: float
    source_end: float
    duration: float
    peak_dbfs: float
    quiet: bool
    clipped: bool


def layouts_for(instrument):
    """The control layouts of one instrument, by key; none for an instrument the learner has no layout for."""
    key = instrument.strip().casefold()
    return [name for name, layout in LAYOUTS.items() if layout["instrument"].casefold() == key]


def pickup_settings(instrument):
    """The names of an instrument's pickup settings, as the desk names them; () when it has none."""
    key = instrument.strip().casefold()
    return next((tuple(item["settings"]) for item in INSTRUMENTS if item["name"].casefold() == key), ())


def setting_name(layout, values):
    """A configuration's pickup setting in the desk's words: "Neck", or "Neck · Series" with a toggle."""
    place = _layout(layout)["pickups"][values["pickup"] - 1]
    return f"{place} · {values['wiring']}" if values.get("wiring") else place


def suggested_setup(record, instrument):
    """Compact physical settings for a sound, shown only for the instrument they describe."""
    if not isinstance(record, dict) or record.get("instrument") != instrument:
        return ""
    layout = record.get("layout")
    if layout not in layouts_for(instrument):
        return ""
    try:
        values = configuration(layout, record.get("values"))
    except ValueError:
        return ""
    pickup = setting_name(layout, values)
    pieces = [pickup]
    for key in LAYOUTS[layout]["knobs"]:
        if (pickup == "Neck" and key.startswith("bridge_")) or (pickup == "Bridge" and key.startswith("neck_")):
            continue
        label = key.replace("volume", "vol").replace("_", " ").capitalize()
        pieces.append(f"{label} {values[key]:g}")
    return " · ".join(pieces)


def _instrument(value):
    name = value.strip()
    if not name:
        raise ValueError("Name the guitar used for these recordings.")
    return name, name.casefold()


def _layout(layout):
    try:
        return LAYOUTS[layout]
    except KeyError as error:
        raise ValueError("Choose a supported guitar control layout.") from error


def configuration(layout, values):
    selected = _layout(layout)
    expected = {"pickup", *selected["knobs"]}
    if "wiring" in selected:
        expected.add("wiring")
    if not isinstance(values, dict) or set(values) != expected:
        raise ValueError("Set the pickup selector and all controls for this layout.")
    result = {}
    pickup = values["pickup"]
    if type(pickup) not in (int, float) or pickup not in range(1, len(selected["pickups"]) + 1):
        raise ValueError("Choose one of the guitar's pickup selector positions.")
    result["pickup"] = int(pickup)
    if "wiring" in selected:
        wiring = values["wiring"]
        wiring = next((choice for choice in selected["wiring"]
                       if isinstance(wiring, str) and wiring.strip().casefold() == choice.casefold()), None)
        if wiring is None:
            raise ValueError("Set the mini-toggle to Series or Parallel.")
        result["wiring"] = wiring
    for knob in selected["knobs"]:
        value = values[knob]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10:
            raise ValueError("Set each guitar knob between 0 and 10.")
        result[knob] = float(value)
    return result


def _role(role):
    if role not in ROLES:
        raise ValueError("Choose Example or Test for this recording.")
    return role


def dataset_folder(instrument, layout, *, root=None):
    _, key = _instrument(instrument)
    _layout(layout)
    return (root if root is not None else folders.learner()) / folders.slug(key) / layout


def _check_instrument(instrument, source):
    name, key = _instrument(instrument)
    if source.guitar and source.guitar.strip().casefold() != key:
        raise ValueError(f"This take used {source.guitar}. Choose a recording of {name}.")


def source_from_take(instrument, folder):
    """Resolve a particular saved dry recording without consulting the newest take."""
    source = _take_source(folder)
    _check_instrument(instrument, source)
    return source


def _take_source(folder):
    folder = Path(folder).resolve()
    if folder.is_file():
        if folder.name.casefold() != "dry.wav":
            raise ValueError("Choose the take's dry.wav file.")
        folder = folder.parent
    path = folder / "dry.wav"
    try:
        info = json.loads((folder / "clip.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as error:
        raise ValueError("The selected take has no readable recording metadata.") from error
    if not isinstance(info, dict) or info.get("routing_unresolved") or not path.is_file():
        raise ValueError("This take has no verified dry recording. Check its USB input mapping.")
    passage = info.get("selected_passage_seconds")
    if (not isinstance(passage, (list, tuple)) or len(passage) != 2
            or not all(type(value) in (int, float) and math.isfinite(value) for value in passage)
            or not 0 <= passage[0] < passage[1]):
        passage = None
    label = info.get("song") or info.get("passage") or folder.name
    duration = info.get("duration_seconds")
    duration = float(duration) if type(duration) in (int, float) and math.isfinite(duration) and duration > 0 else None
    recorded = re.fullmatch(r"clip-\d{8}-(\d{2})(\d{2})(\d{2})-[0-9a-f]+", folder.name)
    if recorded and label != folder.name:
        label = f"{label} · {':'.join(recorded.groups())}"
    if duration is not None:
        label = f"{label} · {duration:.1f}s"
    guitar = info.get("guitar")
    run, begins = info.get("live_run"), info.get("run_start_seconds")
    run = ((run, float(begins)) if isinstance(run, str) and run and type(begins) in (int, float)
           and math.isfinite(begins) else None)
    return DrySource(path, str(label), tuple(passage) if passage else None,
                     guitar if isinstance(guitar, str) and guitar.strip() else None, "take",
                     info.get("usb_mapping_source"), duration, session=_day(folder.name), run=run)


def _day(name):
    """The day a take was recorded, from its folder's name: the recording session it belongs to."""
    named = re.match(r"clip-(\d{8})-", str(name))
    return named.group(1) if named else None


def session_of(row):
    """The recording session of a label: as written, or for an earlier label the day in its take's name."""
    session = row.get("session")
    return session if isinstance(session, str) and session else _day(Path(str(row.get("source", ""))).parent.name)


def source_from_result(instrument, result):
    """Get the exact take returned by takes.record_take."""
    if not isinstance(result, (tuple, list)) or len(result) != 2 or not result[0]:
        raise ValueError("No new dry recording was saved.")
    return source_from_take(instrument, result[0])


def source_from_wav(path):
    path = Path(path).resolve()
    if path.suffix.casefold() != ".wav" or not path.is_file():
        raise ValueError("Choose an available mono dry WAV.")
    managed = (folders.recordings(), live_recordings())
    if any(path.parent.parent == root.resolve() for root in managed) and (path.parent / "clip.json").is_file():
        if path.name.casefold() != "dry.wav":
            raise ValueError("Choose the take's verified dry WAV rather than the processed or raw USB recording.")
        return _take_source(path.parent)
    return DrySource(path, path.name)


def take_sources(instrument, *, include_unknown=False):
    recordings = [*folders.recordings().glob("clip-*"), *live_recordings().glob("clip-*")]
    sources = []
    for folder in sorted(recordings, key=lambda path: path.name, reverse=True):
        if not folder.is_dir():
            continue
        try:
            source = source_from_take(instrument, folder)
        except (OSError, ValueError):
            continue
        if source.guitar or include_unknown:
            sources.append(source)
    return sources


def live_recordings():
    return folders.live_recordings()


def label_history(instrument, layout, *, root=None):
    """Read active and retired labels, including their configuration edits."""
    folder = dataset_folder(instrument, layout, root=root)
    result = []
    for path in folder.glob("*/record.json"):
        if not re.fullmatch("[0-9a-f]{32}", path.parent.name):
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            wave = path.with_name("dry.wav")
            if (row["schema"] != SCHEMA or row["id"] != path.parent.name or row["layout"] != layout
                    or _instrument(row["instrument"])[1] != _instrument(instrument)[1]
                    or not isinstance(row["created_at"], str) or not isinstance(row.get("history", []), list)
                    or not isinstance(row.get("active", True), bool) or not isinstance(row.get("source"), str)):
                continue
            _role(row["role"])
            configuration(layout, row["configuration"])
            if (not all(re.fullmatch("[0-9a-f]{64}", row[key]) for key in ("source_id", "excerpt_id"))
                    or not all(math.isfinite(row[key]) for key in ("source_start", "source_end"))
                    or not math.isfinite(row.get("source_offset", 0.))):
                continue
        except (AttributeError, OSError, KeyError, TypeError, ValueError):
            continue
        result.append({**row, "active": path.parent.parent == folder and row.get("active", True) and wave.is_file(),
                       "wav": str(wave.resolve())})
    return sorted(result, key=lambda row: row["created_at"], reverse=True)


def _exposure(rows):
    examples = [row for row in rows if row["role"] == "example"
                or any(item.get("role") == "example" for item in row.get("history", []) if isinstance(item, dict))]
    return {row["source_id"] for row in examples}, {row["excerpt_id"] for row in examples}


def records(instrument, layout, *, role=None, root=None):
    """Return active labels. A test query returns only score-eligible checks."""
    if role is not None:
        _role(role)
    return _active_records(label_history(instrument, layout, root=root), role)


def _active_records(rows, role):
    sources, excerpts = _exposure(rows)
    taught = {session_of(row) for row in rows if row["active"] and row["role"] == "example"}
    result = []
    for row in rows:
        if not row["active"] or (role is not None and row["role"] != role):
            continue
        row = {key: value for key, value in row.items() if key != "active"}
        exposed = row["source_id"] in sources or row["excerpt_id"] in excerpts
        if row["role"] == "test" and exposed:
            if role == "test":
                continue
            row["score_eligible"] = False
        if row["role"] == "test":
            # Known to be from another recording session than everything taught: only then is it called independent.
            session = session_of(row)
            row["separate_session"] = session is not None and None not in taught and session not in taught
        result.append(row)
    return result


def dataset_sources(instrument, layout, *, include_unknown=False):
    """List (dry source, active labels) for takes and previously labeled imports."""
    groups = {source.path: (source, []) for source in take_sources(instrument, include_unknown=include_unknown)}
    history = label_history(instrument, layout)
    labeled = {row["id"]: row for row in _active_records(history, None)}
    saved = {Path(row["wav"]): row for row in history}
    for row in history:
        original = folders.found(row["source"]).resolve()
        if original in groups:
            key = original
        elif original in saved and original.is_file():
            key = original
            groups[key] = (_saved_source(instrument, saved[key]), [])
        else:
            try:
                source = source_from_wav(original)
                _check_instrument(instrument, source)
                key = original
            except (OSError, ValueError):
                key = Path(row["wav"])
                if not key.is_file():
                    continue
                source = _saved_source(instrument, row)
            groups.setdefault(key, (source, []))
        if row["id"] in labeled:
            groups[key][1].append(labeled[row["id"]])
    return [(source, tuple(labels)) for source, labels in groups.values()]


def _saved_source(instrument, row):
    origin = row["source_id"], row["source_start"] + row.get("source_offset", 0.), row["excerpt_id"]
    return DrySource(Path(row["wav"]), row["source_label"], guitar=instrument, kind="saved-excerpt",
                     mapping=row.get("mapping"), duration=row["seconds"], origin=origin, session=session_of(row))


def find_label(instrument, layout, excerpt):
    """Return the active label for an exact selection, including an identical export."""
    return next((row for row in records(instrument, layout) if row["excerpt_id"] == excerpt.excerpt_id), None)


def _bounds(source, frames, start, end):
    fallback = source.passage or (0., frames / audio.RATE)
    try:
        first = fallback[0] if start is None else float(start)
        last = fallback[1] if end is None else float(end)
        if not math.isfinite(first) or not math.isfinite(last):
            raise ValueError
        a, b = round(first * audio.RATE), round(last * audio.RATE)
    except (TypeError, ValueError) as error:
        raise ValueError("Enter a start and end in seconds.") from error
    if not 0 <= a < b <= frames or b - a < audio.RATE // 2:
        raise ValueError("Choose at least half a second within the dry recording.")
    return a, b


def _digest(samples):
    return sha256(np.asarray(samples, dtype="<f4", order="C").tobytes()).hexdigest()


def _check_reuse(existing, source_id, excerpt_id, start, end, role):
    for row in existing:
        if row["source_id"] != source_id and row["excerpt_id"] != excerpt_id:
            continue
        if row["role"] != role:
            raise ValueError("This recording is already used for the other role. Use a fresh take for a check.")
        offset = row.get("source_offset", 0.)
        if row["excerpt_id"] == excerpt_id or max(start, row["source_start"] + offset) < min(end, row["source_end"] + offset):
            raise ValueError("This part of the dry recording already has a configuration label.")


def inspect(source, *, start=None, end=None):
    """Read the chosen dry span and its IDs without creating a dataset label."""
    if not isinstance(source, DrySource):
        source = source_from_wav(source)
    raw = audio.read_wave(source.path)
    if raw.shape[1] != 1:
        raise ValueError("The learner needs a mono dry WAV from the guitar input.")
    first, last = _bounds(source, len(raw), start, end)
    excerpt = raw[first:last]
    peak = float(np.max(np.abs(excerpt)))
    source_id = _digest(raw)
    if source.origin:
        origin_id, _, saved_excerpt_id = source.origin
        if source_id != saved_excerpt_id:
            raise ValueError("The saved dry excerpt no longer matches its recording label.")
        source_id = origin_id
    elif source.run:
        # Every clip kept from one run of live listening is the same performance: one source.
        source_id = sha256(f"live-run:{source.run[0]}".encode()).hexdigest()
    return DryExcerpt(excerpt, source_id, _digest(excerpt), first / audio.RATE, last / audio.RATE,
                      len(raw) / audio.RATE, audio.db(peak), peak < .001, peak >= .999)


def never_taught(instrument, layout, excerpt):
    """Whether this performance has never been used to teach this dataset (the least a check must be)."""
    sources, excerpts = _exposure(label_history(instrument, layout))
    return excerpt.source_id not in sources and excerpt.excerpt_id not in excerpts


def check_test(instrument, layout, excerpt):
    """Refuse a check on audio previously used to teach."""
    if not never_taught(instrument, layout, excerpt):
        raise ValueError("This recording helped train the learner. Choose a new performance for a check.")


def keep_excerpt(folder, row, samples):
    """Keep one labelled excerpt as `<folder>/<id>/dry.wav` beside its record. Written in a staging folder and
    renamed into place, so a half-written label is never read. Returns the saved WAV."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".label-", dir=folder) as directory:
        stage = Path(directory)
        audio.write_wave(stage / "dry.wav", samples)
        write_json(stage / "record.json", row)
        stage.rename(folder / row["id"])
    return (folder / row["id"] / "dry.wav").resolve()


def add(instrument, layout, source, values, *, role="example", start=None, end=None):
    selected = configuration(layout, values)
    name, _ = _instrument(instrument)
    _role(role)
    if not isinstance(source, DrySource):
        source = source_from_wav(source)
    _check_instrument(name, source)
    excerpt = inspect(source, start=start, end=end)
    if excerpt.clipped:
        raise ValueError("This excerpt clips. Record it again with more input headroom.")
    if excerpt.quiet and role == "example":
        raise ValueError("The excerpt is too quiet to teach a configuration. Play an audible dry example.")
    folder = dataset_folder(name, layout)
    identity = uuid4().hex
    row = {"schema": SCHEMA, "id": identity, "instrument": name, "layout": layout, "role": role,
           "configuration": selected, "source_id": excerpt.source_id, "excerpt_id": excerpt.excerpt_id,
           "source": folders.stored(source.path), "source_label": source.label, "source_kind": source.kind, "mapping": source.mapping,
           "source_start": excerpt.source_start, "source_end": excerpt.source_end, "seconds": len(excerpt.samples) / audio.RATE,
           "peak_dbfs": excerpt.peak_dbfs, "quiet": excerpt.quiet, "created_at": datetime.now(timezone.utc).isoformat(),
           "session": source.session}
    offset = source.origin[1] if source.origin else source.run[1] if source.run else 0.
    if source.origin or source.run:
        row["source_offset"] = offset
    with _WRITE:
        _check_reuse(records(name, layout), excerpt.source_id, excerpt.excerpt_id,
                     offset + excerpt.source_start, offset + excerpt.source_end, role)
        if role == "test":
            check_test(name, layout, excerpt)
        wave = keep_excerpt(folder, row, excerpt.samples)
    return {**row, "wav": str(wave)}


def _active_label(instrument, layout, identity):
    if not isinstance(identity, str) or not re.fullmatch("[0-9a-f]{32}", identity):
        raise ValueError("Choose an existing labeled recording.")
    row = next((row for row in label_history(instrument, layout) if row["id"] == identity and row["active"]), None)
    if row is None:
        raise ValueError("That labeled recording is unavailable.")
    return row


def _update_label(row, action, **updates):
    before = {"action": action, "at": datetime.now(timezone.utc).isoformat(),
              "role": row["role"], "configuration": row["configuration"]}
    updated = {**row, **updates, "history": [*row.get("history", []), before]}
    manifest = Path(row["wav"]).with_name("record.json")
    write_json(manifest, {key: value for key, value in updated.items() if key != "wav"})
    return updated


def retag(instrument, layout, identity, values, *, role=None, promote=False):
    """Correct a configuration, or explicitly promote a saved check to an example."""
    selected = configuration(layout, values)
    with _WRITE:
        row = _active_label(instrument, layout, identity)
        target = row["role"] if role is None else _role(role)
        if target != row["role"]:
            if target == "test":
                raise ValueError("This recording helped train the learner. Choose a new performance for a check.")
            if not promote:
                raise ValueError("Use this saved check to teach explicitly. It will no longer count as a check.")
            if row.get("quiet"):
                raise ValueError("The excerpt is too quiet to teach a configuration. Play an audible dry example.")
            offset = row.get("source_offset", 0.)
            _check_reuse(records(instrument, layout, role="example"), row["source_id"], row["excerpt_id"],
                         offset + row["source_start"], offset + row["source_end"], "example")
        if selected != row["configuration"] or target != row["role"]:
            action = "promote" if target != row["role"] else "retag"
            row = _update_label(row, action, role=target, configuration=selected)
        row.pop("active")
        return row


def unlabel(instrument, layout, identity):
    """Retire a label while retaining the source, excerpt and earlier settings."""
    with _WRITE:
        _update_label(_active_label(instrument, layout, identity), "unlabel", active=False)
