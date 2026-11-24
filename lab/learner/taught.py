"""What the app was taught about one control of a guitar at a time, and checking a take against it.

One control (the pickup selector, the volume knob or the tone knob) is taught
from dry excerpts recorded at two or more of its settings, with the other
controls held fixed. Nothing here is a window: `lab.learner.teach` is.

    taught.add_example("Charvel", "pickup", "Neck", wav)      # a copy of the excerpt is kept, with its record
    profile = taught.fit("Charvel", "pickup")                 # what `state.estimate` reads; saved
    examples, profile, mapping = taught.load("Charvel", "pickup")
    samples, overall, track, gestures_heard = taught.check(profile, mapping, examples, fresh_wav)

Examples are kept the way the Guitar Learner keeps its own
(`dataset.keep_excerpt`): `<guitar>/<control>-examples/<id>/dry.wav` beside a
`record.json`, as copies, so a take that is later moved or deleted takes
nothing with it. The earlier app kept a list of paths
(`<control>-examples.json`); `load` brings such a list into the store once,
copying what it can still find.

On disk this lives in `guitar_input` (`folders.taught`), the earlier app's
name for it.
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from uuid import uuid4

import numpy as np

from lab.hearing import audio
from lab.hearing.measure import measure
from lab.learner import dataset, folders, gestures, profiles, state
from lab.learner.folders import LOG, write_json
from lab.sound.sounds import INSTRUMENTS

SCHEMA = 1
GUITARS = tuple(instrument["name"] for instrument in INSTRUMENTS if instrument["kind"] == "electric")
CONTROLS = {"Pickup selector": "pickup", "Volume knob": "volume", "Tone knob": "tone"}
SETTINGS = {
    "pickup": ("Neck", "Bridge", "Middle"),           # for a guitar the app does not know; see `settings`
    "tone": ("Tone 10", "Tone 0", "Tone 5"),
    "volume": ("Volume 10", "Volume 5", "Volume 2"),
}
HINTS = {
    "pickup": "Move the selector. Keep both knobs and your picking position fixed.",
    "volume": "Turn the volume knob. Keep pickup, tone, Quad Cortex input gain and picking strength fixed: "
              "volume is read from the level of the dry signal, which all of these change.",
    "tone": "Turn the tone knob. Keep pickup, volume and your picking position fixed.",
}
CLOSE, NEAR = .8, .55           # closeness from which a match is called close, and near


def settings(instrument, control):
    """Names to offer for a control's settings. A pickup selector's are the instrument's own, as the desk and the
    Guitar Learner name them: the two ends of the selector first, since they are the easiest to tell apart."""
    if control == "pickup":
        known = dataset.pickup_settings(instrument)
        if len(known) >= 2:
            return (known[-1], known[0], *known[1:-1])
    return SETTINGS[control]


def controls(instrument):
    """The controls of an instrument that can be taught, by title, as `CONTROLS`. A pickup selector only when the
    instrument has one: an instrument the app knows with fewer than two pickup settings (a bass, an acoustic or a
    classical guitar with one pickup) has none. One the app does not know is offered all three."""
    key = (instrument or "").strip().casefold()
    known = next((item for item in INSTRUMENTS if item["name"].casefold() == key), None)
    if known is not None and len(known.get("settings") or ()) < 2:
        return {title: value for title, value in CONTROLS.items() if value != "pickup"}
    return dict(CONTROLS)


def instruments():
    """The electric guitars the app knows, and any other that something was taught for."""
    names = list(GUITARS)
    for path in folders.taught_instruments().glob("*/instrument.json"):
        name = folders.read_json(path).get("name")
        if isinstance(name, str) and name.strip() and all(name.casefold() != known.casefold() for known in names):
            names.append(name)
    return names


def match_words(setting, closeness, control=None):
    """How an estimate is said: the setting and how close it is to what was taught. Not a chance of being right."""
    if not setting:
        return "Uncertain"
    words = "close match" if closeness >= CLOSE else "near match" if closeness >= NEAR else "far match"
    return f"{setting} · {words}" + (" · by dry level" if control == "volume" else "")


def example_counts(examples):
    counts = {}
    for example in examples:
        label = example["label"].strip()
        name, count = counts.get(label.casefold(), (label, 0))
        counts[label.casefold()] = (name, count + 1)
    return counts


# ---- the examples ----

def _store(instrument, control):
    return folders.taught(instrument) / f"{control}-examples"


def _control(control):
    if control not in state.CONTROLS:
        raise ValueError("Choose the pickup selector, the volume knob or the tone knob.")
    return control


def _rows(instrument, control):
    rows = []
    for path in _store(instrument, _control(control)).glob("*/record.json"):
        row = folders.read_json(path)
        wave = path.with_name("dry.wav")
        if (row.get("schema") != SCHEMA or row.get("id") != path.parent.name or row.get("control") != control
                or not isinstance(row.get("label"), str) or not row["label"].strip() or not wave.is_file()):
            continue
        rows.append({**row, "wav": str(wave.resolve())})
    return sorted(rows, key=lambda row: str(row.get("created_at")))


def examples(instrument, control):
    """The examples in use for one control, oldest first; each has its "label" and its saved "wav"."""
    return [row for row in _rows(instrument, control) if row.get("active", True)]


def add_example(instrument, control, label, wav, *, start=None, end=None, source_label=None):
    """Keep a copy of a dry excerpt as an example of one setting. Returns its record."""
    instrument, label = instrument.strip(), label.strip()
    if not instrument:
        raise ValueError("Enter a name for this guitar.")
    if not label:
        raise ValueError(f"Name the setting you played, such as {settings(instrument, _control(control))[0]}.")
    wav = Path(wav)
    if not wav.is_file():
        raise ValueError("That dry recording is unavailable. Choose another take.")
    source = dataset.DrySource(wav.resolve(), source_label or f"{wav.parent.name}/{wav.name}")
    excerpt = dataset.inspect(source, start=start, end=end)
    kept = examples(instrument, control)
    for row in kept:
        same = row["source_id"] == excerpt.source_id and max(excerpt.source_start, row["source_start"]) < min(
            excerpt.source_end, row["source_end"])
        if same or row["excerpt_id"] == excerpt.excerpt_id:
            raise ValueError(f"This part of the dry take is already an example of {row['label']}. "
                             "Use another take or a separate clip range.")
    label = example_counts(kept).get(label.casefold(), (label, 0))[0]
    folder = folders.taught(instrument)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / "instrument.json", {"name": instrument})
    row = {"schema": SCHEMA, "id": uuid4().hex, "instrument": instrument, "control": control, "label": label,
           "source_id": excerpt.source_id, "excerpt_id": excerpt.excerpt_id, "source": folders.stored(wav),
           "source_label": source.label, "source_start": excerpt.source_start, "source_end": excerpt.source_end,
           "seconds": len(excerpt.samples) / audio.RATE, "peak_dbfs": excerpt.peak_dbfs,
           "created_at": datetime.now(timezone.utc).isoformat(), "active": True}
    wave = dataset.keep_excerpt(_store(instrument, control), row, excerpt.samples)
    return {**row, "wav": str(wave)}


def remove_example(instrument, control, identity):
    """Take an example out of use. Its audio and record are kept."""
    row = next((row for row in examples(instrument, control) if row["id"] == identity), None)
    if row is None:
        raise ValueError("That example is no longer there.")
    record = {key: value for key, value in row.items() if key != "wav"}
    write_json(Path(row["wav"]).with_name("record.json"),
               {**record, "active": False, "removed_at": datetime.now(timezone.utc).isoformat()})


def _bring_list(instrument, control):
    """Bring the earlier app's list of example paths into the store, once: each excerpt still found is copied."""
    listed = folders.taught(instrument) / f"{control}-examples.json"
    store = _store(instrument, control)
    if store.exists() or not listed.is_file():
        return
    try:
        records = json.loads(listed.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        records = None
    if not isinstance(records, list):
        raise ValueError("Could not read the saved examples for this control.")
    store.mkdir(parents=True, exist_ok=True)
    for item in records:
        try:
            add_example(instrument, control, item["label"], folders.found(item["wav"]),
                        start=item.get("start"), end=item.get("end"))
        except (OSError, KeyError, TypeError, ValueError) as error:
            LOG.warning("An earlier example of %s could not be brought in: %s", control, error)


# ---- the profile and the mapping ----

def load(instrument, control):
    """(examples, profile, gesture mapping) for one control; the last two may be None."""
    _bring_list(instrument, _control(control))
    profile, mapping = gestures.saved(instrument, control)
    return examples(instrument, control), profile, mapping


def fit(instrument, control):
    """Fit the control's profile to its examples and save it."""
    rows = examples(instrument, _control(control))
    profile = state.fit(instrument, control, [{"label": row["label"], "wav": row["wav"]} for row in rows])
    profiles.save(folders.taught(instrument) / f"{control}.json", profile)
    return profile


def save_mapping(instrument, control, home, excursion, knob, step_percent):
    try:
        step = float(step_percent) / 100
    except (TypeError, ValueError) as error:
        raise ValueError("Enter the step as a percentage, such as +10.") from error
    mapping = gestures.GestureMapping(home, excursion, knob, step)
    folder = folders.taught(instrument)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / f"{_control(control)}-gesture.json", mapping.to_dict())
    return mapping


# ---- checking a take ----

def check(profile, mapping, taught_examples, source):
    """What is heard in a fresh dry take: (samples, the estimate for the whole of it, the estimates window by
    window at the cadence of live listening, the gestures that would have been recognised)."""
    samples = audio.read_wave(source)
    if samples.shape[1] != 1:
        raise ValueError("Choose a mono dry WAV from the guitar input.")
    whole = dataset._digest(samples)
    if any(row["source_id"] == whole for row in taught_examples):
        raise ValueError("This dry take was used to teach. Record a fresh check, or choose another dry take.")
    overall = state.estimate(profile, samples)
    track = state.track(profile, samples)
    return samples, overall, track, gestures.replay(mapping, track) if mapping else []


def timeline(control, track, heard):
    """The check as lines to read: (seconds, text, seconds of audio before it worth hearing)."""
    points = []
    for event in heard:
        from lab.sound.knobs import KNOBS

        knob = KNOBS.get(event.control, (event.control,))[0]
        points.append((event.at, f"Return {event.home} → {event.excursion} → {event.home}  ·  {knob} {event.step:+.0%}", .95))
    last = object()
    for reading in track:
        if reading["label"] != last:
            last = reading["label"]
            points.append((reading["time"], match_words(last, reading.get("closeness", 0), control), .35))
    return sorted(points)


def preview(samples, interval):
    """A stretch of the dry audio as 16-bit samples at a comfortable level, for the computer's own output."""
    part = samples[slice(*interval)]
    if len(part) < audio.RATE // 2:
        raise ValueError("There is too little audio in that selection.")
    found = measure(part)
    if found.lufs is None:
        raise ValueError("There is no audible dry signal in that selection.")
    gain = 10 ** (min(-24 - found.lufs, -6 - found.peak_db) / 20)
    if not math.isfinite(gain):
        raise ValueError("There is no audible dry signal in that selection.")
    return np.rint(np.clip(part * gain, -1, 1) * 32767).astype(np.int16)
