"""Search recorded tones and match them to a local reference without changing the Quad Cortex."""

from dataclasses import dataclass
from collections.abc import Mapping
from functools import lru_cache
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from uuid import uuid4

import numpy as np
from scipy import signal, stats

from . import audio, session


MATCH_PRIORITIES = {
    "Balanced": {"tone": .6, "dynamics": .25, "space": .15},
    "Tone": {"tone": 1., "dynamics": 0., "space": 0.},
    "Dynamics": {"tone": .25, "dynamics": .65, "space": .1},
    "Space": {"tone": .25, "dynamics": .1, "space": .65},
}
_HOP = audio.RATE // 50
_WORDS = re.compile(r"[\w]+", re.UNICODE)
_STOP = set("a an and be but can find for get give i is it look me moar more most my now of please search some sounding sounds that the to tone with you".split())
_AXES = {
    "warm": ("brightness", False), "dark": ("brightness", False), "darker": ("brightness", False),
    "bright": ("brightness", True), "brighter": ("brightness", True), "sparkly": ("brightness", True),
    "even": ("dynamics", False), "smooth": ("dynamics", False), "compressed": ("dynamics", False),
    "punchy": ("dynamics", True), "responsive": ("dynamics", True), "dynamic": ("dynamics", True),
    "wide": ("width", True), "stereo": ("width", True),
    "narrow": ("width", False), "focused": ("width", False), "mono": ("width", False),
    "ambient": ("tails", True), "roomy": ("tails", True), "sustain": ("tails", True),
    "dry": ("tails", False), "tight": ("tails", False),
    "fizz": ("fizz", True), "fizzy": ("fizz", True),
}
_SYNONYMS = {
    "jazz": {"jazz", "jazzy"}, "clean": {"clean", "cleans"},
    "drive": {"drive", "overdrive", "distortion", "crunch", "saturated", "gain"},
    "delay": {"delay", "echo", "repeats"}, "reverb": {"reverb", "room", "hall"},
}


@dataclass(frozen=True)
class Tone:
    spectrum: tuple[float, ...]
    brightness: float
    fizz: float
    dynamics: float
    crest: float
    width: float
    tails: float
    envelope: tuple[float, ...]


def describe(samples):
    """Extract conservative loudness-independent descriptors from a played excerpt."""
    values = np.asarray(samples, dtype=np.float64)
    if values.ndim == 1:
        values = values[:, None]
    if (values.ndim != 2 or values.shape[1] not in (1, 2) or len(values) < _HOP * 3
            or not np.isfinite(values).all()):
        raise ValueError("Choose a valid mono or stereo WAV excerpt.")
    peak = np.max(np.abs(values))
    if peak == 0:
        raise ValueError("The WAV contains silence. Choose a passage with played notes.")
    # Normalizing before any gate makes playback gain irrelevant, even for quiet DI-derived renders.
    values = values / peak
    windows = values[:len(values) // _HOP * _HOP].reshape(-1, _HOP, values.shape[1])
    power = np.mean(windows ** 2, axis=(1, 2))
    strong = float(np.max(power))
    active = power >= strong * 10 ** (-3.6)
    first, last = np.flatnonzero(active)[[0, -1]]
    played = values[first * _HOP:(last + 1) * _HOP]
    level = 10 * np.log10(np.maximum(power / strong, 1e-7))
    loud = level[active]
    q10, q90 = np.percentile(loud, (10, 90))

    bands = audio.BANDS
    hz, psd = signal.welch(played, audio.RATE, nperseg=min(len(played), 4096), axis=0)
    energy = psd.mean(axis=1)
    totals = np.array([energy[(hz >= lo) & (hz < hi)].sum() for lo, hi in zip(bands, bands[1:])])
    if not totals.sum() > 1e-18:
        raise ValueError("The WAV has no usable guitar-range signal.")
    fractions = totals / totals.sum()
    spectrum = tuple(10 * np.log10(np.maximum(fractions, 1e-5)))
    brightness = float(np.log2(np.dot(fractions, np.sqrt(np.asarray(bands[:-1]) * bands[1:]))))
    fizz = float(fractions[-2:].sum())
    side = np.mean(((played[:, 0] - played[:, 1]) / 2) ** 2) if played.shape[1] == 2 else 0.
    width = float(np.clip(side / np.mean(played ** 2), 0, 1))
    # Quiet frames following an attack are a rough continuity/decay cue, including sustain and effects.
    trailing_peak = np.maximum.reduce([np.roll(level, delay) for delay in range(2, 13)])
    trailing_peak[:12] = -70
    after_notes = trailing_peak > -16
    tails = float(np.mean(np.clip((level[after_notes] - trailing_peak[after_notes] + 35) / 35, 0, 1))) if after_notes.any() else 0.
    crest = float(20 * np.log10(np.max(np.abs(played)) / np.sqrt(np.mean(played ** 2))))
    return Tone(spectrum, brightness, fizz, float(q90 - q10), crest, width, tails,
                tuple(np.maximum(level[first:last + 1], -45)))


def _samples(path, selection):
    samples = audio.read_wave(path)
    if selection:
        start, stop = (round(value * audio.RATE) for value in selection)
        samples = samples[start:stop]
    return samples


@lru_cache(maxsize=128)
def _describe_file(path, modified, size, selection):
    return describe(_samples(path, selection))


def _features(path, selection=None):
    stamp = Path(path).stat()
    return _describe_file(Path(path).resolve(), stamp.st_mtime_ns, stamp.st_size, selection)


def _selection(info):
    selected = info.get("selected_passage_seconds")
    if (isinstance(selected, (tuple, list)) and len(selected) == 2
            and all(type(value) in (float, int) and math.isfinite(value) for value in selected)
            and 0 <= selected[0] < selected[1] <= audio.MAX_SECONDS + 3):
        return tuple(selected)
    return None


def _read_json(path):
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _library():
    known_wet = set()
    for path in sorted((session.SESSIONS / "recordings").glob("*/wet.wav"), reverse=True):
        info = _read_json(path.with_name("clip.json"))
        if not info:
            continue
        known_wet.add(path.parent.name)
        title = info.get("wet_preset") or "Recorded wet take"
        tags = [tag for key in ("artist", "passage", "pickup_note") if (tag := info.get(key))]
        yield {"title": title, "file": path, "take_id": path.parent.name, "session": None,
               "selection": _selection(info), "guitar": info.get("guitar", ""),
               "target": info.get("song") or info.get("passage", ""), "tags": tags}
    for path in sorted(session.SESSIONS.glob("*/session.json"), reverse=True):
        info = _read_json(path)
        if info.get("state") != "ready" or info.get("kind") in ("demo", "match-audition"):
            continue
        take = info.get("source_clip") if isinstance(info.get("source_clip"), str) else None
        candidates = info.get("candidates")
        if not isinstance(candidates, list):
            continue
        for row in candidates:
            if not isinstance(row, dict) or row.get("excluded") or row.get("simulation"):
                continue
            filename = row.get("file")
            if not isinstance(filename, str) or Path(filename).name != filename:
                continue
            render = path.parent / filename
            if render.suffix.lower() != ".wav" or not render.is_file():
                continue
            fallback = f"Scene {row['scene']}" if "scene" in row else render.stem
            title = str(row.get("name") or row.get("source") or fallback)
            if take in known_wet and (row.get("source_clip") == take or title.casefold() in {
                "recorded wet baseline", "vai preset from your take", "recorded original wet",
            }):
                continue
            if type(row.get("amount")) in (int, float):
                title = f"{title.capitalize()} · {row['amount']:.0%}"
            tags = [str(info[key]) for key in ("style", "original_preset", "pickup") if info.get(key)]
            if isinstance(row.get("blocks"), list):
                tags.extend(str(block) for block in row["blocks"])
            yield {"title": str(title), "file": render, "take_id": take, "session": path.parent,
                   "selection": None, "guitar": info.get("guitar", ""),
                   "target": info.get("target", ""), "tags": tags}


def _weights(priorities):
    if isinstance(priorities, str):
        try:
            priorities = MATCH_PRIORITIES[priorities.title()]
        except KeyError as error:
            raise ValueError("Choose Balanced, Tone, Dynamics or Space for matching.") from error
    if priorities is None:
        return MATCH_PRIORITIES["Balanced"]
    if (not isinstance(priorities, Mapping) or not priorities or set(priorities) - {"tone", "dynamics", "space"}
            or any(type(value) not in (float, int) or not 0 <= value <= 1 for value in priorities.values())
            or not sum(priorities.values())):
        raise ValueError("Matching weights must be between zero and one with at least one positive weight.")
    total = sum(priorities.values())
    return {name: priorities.get(name, 0) / total for name in MATCH_PRIORITIES["Balanced"]}


def _paired_error(a, b):
    a, b = np.asarray(a), np.asarray(b)
    if min(len(a), len(b)) < 25:
        return None
    errors = []
    for lag in range(-20, 21):
        x, y = a[max(0, lag):], b[max(0, -lag):]
        size = min(len(x), len(y))
        if size >= .85 * min(len(a), len(b)):
            errors.append(float(np.mean(np.abs(x[:size] - y[:size]))))
    return min(errors) if errors else None


def _match(candidate, reference, same_take):
    spectrum = np.abs(np.asarray(candidate.spectrum) - reference.spectrum)
    shape = float(np.mean(spectrum)) / 12
    dynamics = (abs(candidate.dynamics - reference.dynamics) + .5 * abs(candidate.crest - reference.crest)) / 18
    if same_take:
        envelope = _paired_error(candidate.envelope, reference.envelope)
        if envelope is not None:
            dynamics = .55 * dynamics + .45 * envelope / 16
    space = .65 * abs(candidate.width - reference.width) / .5 + .35 * abs(candidate.tails - reference.tails) / .4
    return {"tone": shape, "dynamics": dynamics, "space": space}


def _ranks(tones):
    fields = ("brightness", "dynamics", "width", "tails", "fizz")
    if not tones:
        return []
    ranks = {key: (stats.rankdata([getattr(tone, key) for tone in tones]) - .5) / len(tones) for key in fields}
    return [{key: float(values[index]) for key, values in ranks.items()} for index in range(len(tones))]


def _query_score(query, entry, ranks):
    words = [word for word in _WORDS.findall(query.casefold()) if word not in _STOP]
    indexed = {word for value in (entry["title"], entry["guitar"], entry["target"], *entry["tags"])
               for word in _WORDS.findall(str(value).casefold())}
    evidence = []
    for index, word in enumerate(words):
        if word in {"less", "without", "no"}:
            continue
        negate = index > 0 and words[index - 1] in {"less", "without", "no"}
        if word in _AXES or (negate and word in {"delay", "reverb", "echo"}):
            axis, high = _AXES.get(word, ("tails", True))
            measured = ranks[axis] if high != negate else 1 - ranks[axis]
            if word in indexed and not negate:
                measured = max(measured, .85)
            evidence.append(measured)
        else:
            alternatives = next((others for others in _SYNONYMS.values() if word in others), {word})
            evidence.append(float(bool(alternatives & indexed) != negate))
    return sum(evidence) / len(evidence) if evidence else None


def search(query="", *, reference=None, priorities=None, take_id=None, limit=8, excluding=None, same_take_only=False):
    """Rank local rendered or captured tones by searchable intent and/or WAV descriptors.

    ``take_id`` can favor a known recording. Only linked takes get a paired envelope
    comparison. Scores indicate descriptor similarity, separate from subjective fidelity.
    """
    if limit < 1:
        return []
    weights = _weights(priorities)
    all_entries = list(_library())
    reference_path = Path(reference).resolve() if reference else None
    reference_row = next((item for item in all_entries if reference_path and item["file"].resolve() == reference_path), None)
    reference_take = reference_row.get("take_id") if reference_row else None
    scope = reference_take or take_id
    reference_features = _features(reference_path, reference_row["selection"] if reference_row else None) if reference_path else None
    excluded = Path(excluding).resolve() if excluding else None
    valid = []
    for item in all_entries:
        if item["file"].resolve() in (reference_path, excluded):
            continue
        item["same_take"] = bool(scope and item["take_id"] == scope)
        if same_take_only and not item["same_take"]:
            continue
        try:
            tone = _features(item["file"], item["selection"])
        except (OSError, ValueError, EOFError):
            continue
        valid.append((item, tone))

    results = []
    for (item, tone), ranks in zip(valid, _ranks([tone for _, tone in valid])):
        text = _query_score(query, item, ranks)
        if text == 0 and reference_features is None:
            continue
        terms = _match(tone, reference_features, bool(reference_take and item["same_take"])) if reference_features else None
        distance = sum(weights[axis] * value for axis, value in terms.items()) if terms else None
        similarity = math.exp(-distance) if distance is not None else None
        score = similarity if reference_features else text
        if similarity is not None and text is not None:
            score = .8 * similarity + .2 * text
        item.update(score=round(100 * score, 1) if score is not None else None,
                    similarity=round(100 * similarity, 1) if similarity is not None else None,
                    distance=round(distance, 4) if distance is not None else None, dimensions=terms,
                    traits=[high if ranks[axis] >= .66 else low for axis, low, high in (
                        ("brightness", "Warm", "Bright"), ("dynamics", "Even", "Responsive"),
                        ("width", "Focused", "Wide"), ("tails", "Shorter tails", "Sustained"),
                    ) if ranks[axis] <= .34 or ranks[axis] >= .66])
        results.append(item)
    results.sort(key=lambda row: (row["score"] or 0, row["same_take"]), reverse=True)
    return results[:limit]


def audition(result):
    """Return a normalized PCM16 preview for Windows playback."""
    path = Path(result["file"] if isinstance(result, dict) else result)
    selection = result.get("selection") if isinstance(result, dict) else None
    stamp = path.stat()
    key = sha256(repr((path.resolve(), stamp.st_mtime_ns, stamp.st_size, selection)).encode()).hexdigest()[:20]
    folder = session.SESSIONS / "search-previews"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{key}.wav"
    if target.is_file():
        return target
    samples = _samples(path, selection)
    metrics = audio.describe(samples)
    if metrics["lufs"] is None:
        raise ValueError("The selected tone is too quiet to audition.")
    gain = min(12, -23 - metrics["lufs"], -3 - metrics["peak_dbfs"])
    pending = folder / f"{uuid4().hex}.wav"
    audio.write_wave(pending, samples * 10 ** (gain / 20), pcm16=True)
    pending.replace(target)
    return target


def compare_to_reference(result, reference, reference_title, take_id, *, reference_take=None, selection=None):
    """Build an audition using a common playback loudness for a reference and a result."""
    reference = Path(reference)
    original = _samples(reference, selection)
    candidate = _samples(result["file"], result.get("selection"))
    # The player sees whether these are the same performance in the comparison title.
    folder, manifest = session.new_session("match-audition", result.get("guitar", ""), "",
                                           result.get("target", ""))
    is_same = reference_take and reference_take == result.get("take_id")
    manifest.update(source_clip=take_id, style="Reference comparison", same_candidate_take=bool(is_same),
                    match_source=str(Path(result["file"]).resolve()))
    return session.make_shortlist(folder, manifest, [
        ({"name": f"Reference · {reference_title}", "audition_role": "reference"}, original),
        ({"name": result["title"], "audition_role": "candidate"}, candidate),
    ], original)
