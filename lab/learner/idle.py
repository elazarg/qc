"""Reusable dry idle audio for an exact guitar setup, kept by Guitar Learner."""

from hashlib import sha256
import json

import numpy as np

from lab.hearing.audio import RATE, read_wave, write_wave
from lab.learner import dataset, folders
from lab.sound.sounds import set_aside


def setup(instrument, layout, values):
    if layout not in dataset.layouts_for(instrument):
        raise ValueError("Choose a layout for this guitar.")
    return {"instrument": instrument, "layout": layout, "values": dataset.configuration(layout, values)}


def _directory(home, instrument):
    return home / "learner" / "idle" / folders.slug(instrument)


def _sample(home, item):
    key = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    return _directory(home, item["instrument"]) / (sha256(key).hexdigest() + ".wav")


def selected(home, instrument):
    held = folders.read_json(_directory(home, instrument) / "selected.json")
    try:
        return setup(instrument, held.get("layout"), held.get("values"))
    except ValueError:
        return None


def recorded(home, instrument):
    """Previously captured setups, newest first, with a readable configuration label."""
    found = []
    for path in _directory(home, instrument).glob("*.wav"):
        held = folders.read_json(path.with_suffix(".json"))
        try:
            item = setup(instrument, held.get("layout"), held.get("values"))
            if path == _sample(home, item):
                found.append((path.stat().st_mtime_ns, item))
        except (OSError, ValueError):
            continue
    return [item for _, item in sorted(found, key=lambda pair: pair[0], reverse=True)]


def choose(home, item):
    path = _directory(home, item["instrument"]) / "selected.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and folders.read_json(path) == {} and set_aside(path) is None:
        raise OSError("The saved guitar setup could not be read or moved aside.")
    folders.write_json(path, item)


def available(home, instrument):
    """Valid active configuration labels, without consulting any data folder other than this desk's."""
    root = home / "learner" / "guitar_learner"
    rows = [row for layout in dataset.layouts_for(instrument) for row in dataset.records(instrument, layout, root=root)]
    return sorted(rows, key=lambda row: row["created_at"], reverse=True)


def for_sound(home, instrument, sound=None):
    """Use a confirmed setup first, then an exact setup from the sound or the learner."""
    held = selected(home, instrument)
    if held:
        return held
    suggested = sound.origin.get("guitar_setup") if sound else None
    if isinstance(suggested, dict) and suggested.get("instrument") == instrument:
        try:
            return setup(instrument, suggested.get("layout"), suggested.get("values"))
        except ValueError:
            pass
    labels = available(home, instrument)
    return setup(instrument, labels[0]["layout"], labels[0]["configuration"]) if labels else None


def _gap(samples):
    """A sustained, nonzero quiet plateau, separated from audible playing in the same tagged excerpt."""
    step = RATE // 10
    frames = len(samples) // step
    if frames < 18 or not np.isfinite(samples).all():
        return None
    chunk = samples[:frames * step, 0].reshape(-1, step).astype(np.float64)
    rms = np.sqrt(np.mean(chunk * chunk, axis=1))
    peak = np.quantile(rms, .9)
    quiet_limit = min(peak / 10, 10 ** (-40 / 20))
    audible_floor = 10 ** (-105 / 20)
    gaps = []
    for start in range(2, frames - 15):
        span = rms[start:start + 13]
        low, high = span.min(), span.max()
        if low > audible_floor and high <= min(quiet_limit, 2 * low):
            gaps.append((float(np.median(span)), start))
    if not gaps:
        return None
    # A representative floor from plausible gaps, rather than the very quietest instant.
    _, start = sorted(gaps)[len(gaps) // 2]
    return samples[start * step:(start + 13) * step]


def reference(home, item):
    """(dry mono, provenance), or None. Older pickup-only labels lack knob positions."""
    path = _sample(home, item)
    try:
        samples = read_wave(path)[:, :1]
        if len(samples) >= RATE and np.isfinite(samples).all():
            return samples, "recorded reference"
    except (OSError, ValueError):
        pass
    for row in available(home, item["instrument"]):
        if row["layout"] != item["layout"] or row["configuration"] != item["values"]:
            continue
        if row.get("source_kind") not in ("take", "live"):
            continue
        try:
            quiet = _gap(read_wave(row["wav"])[:, :1])
        except (OSError, ValueError):
            continue
        if quiet is not None:
            return quiet, "quiet gap in a labeled take"
    return None


def keep(home, item, samples):
    dry = np.asarray(samples[RATE // 2:, :1], dtype=np.float32)
    if len(dry) < RATE or not np.isfinite(dry).all() or np.max(np.abs(dry)) >= .999:
        raise ValueError("The idle recording was incomplete or clipped. Try again with the strings muted.")
    path = _sample(home, item)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            read_wave(path)
        except (OSError, ValueError):
            if set_aside(path) is None:
                raise OSError("The old idle recording could not be read or moved aside.") from None
    write_wave(path, dry)
    label = path.with_suffix(".json")
    if label.exists() and folders.read_json(label) == {} and set_aside(label) is None:
        raise OSError("The old idle label could not be read or moved aside.")
    folders.write_json(label, item)
    choose(home, item)
