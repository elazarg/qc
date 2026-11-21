"""Tone search: find a sound among yours by words, or by how close it comes to a reference recording.

Carried over from the old search and match (`tone_search`, the search side of `library_ui`):

- **Words.** Every sound kept on this computer (designed, read from the unit, revised, saved) is searched
  by its name, request, explanation, notes, blocks and tags, and by measured qualities: "bright", "dark",
  "punchy", "even", "ambient", "dry"… rank sounds against each other by what was measured of them. "No
  delay" and "less reverb" turn a quality round.
- **Match a reference.** A reference recording (a WAV chosen in place from where recordings are kept, or by
  its path; a preset captured from the unit in the Sounds panel; or the sound on A) is described without regard to its level: spectral balance in octave bands, brightness,
  top-end fizz, how much the level moves, crest, stereo width and what rings on after notes. Each candidate
  sound is played silently through the unit with the desk's phrase and described the same way; the
  closest come first, by the weighting chosen (Balanced, Tone, Dynamics, Space). When the reference was
  captured with the same phrase, the two envelopes are compared note for note as well.

On the old desk results were recordings in session folders. On the new core they are sounds, so a result
goes on A or B to be heard through the unit, and the descriptions of rendered sounds are kept in
`search-cache.json` by the digest of the chain as heard (its Shape knobs applied) and the phrase, so a sound
is rendered for matching once per phrase and again when its knobs move. Each description is written as soon
as its sound is rendered, where the unit is held: stopping, leaving the tool or one sound that cannot be
built costs nothing already rendered. A sound the unit will not build is noted and left out of the ranking.

The reference is played on the computer at the level of the app's other previews, never louder.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
import tkinter as tk

import numpy as np
from scipy import signal, stats

from lab.hearing.audio import RATE, read_wave
from lab.hearing.measure import match as match_levels, measure
from lab.qc.unit import Mismatch, Refused, Stopped
from lab.sound.sounds import describe as describe_chain
from lab.desk.theme import FONT, INK, SOFT, SUBTLE, TILE
from lab.tools.common import (WIDTH, Chips, FileChoice, Tool, button, disclosure, gained, heard_chain, label, listbox,
                              phrase, play, render, section, usual_folders)

BANDS = (65, 125, 250, 500, 1_000, 2_000, 4_000, 8_000, 12_000)
MATCH_PRIORITIES = {
    "Balanced": {"tone": .6, "dynamics": .25, "space": .15},
    "Tone": {"tone": 1., "dynamics": 0., "space": 0.},
    "Dynamics": {"tone": .25, "dynamics": .65, "space": .1},
    "Space": {"tone": .25, "dynamics": .1, "space": .65},
}
CACHE = "search-cache.json"
_HOP = RATE // 50
_WORDS = re.compile(r"[\w]+", re.UNICODE)
_STOP = set("a an and be but can find for get give i is it look me moar more most my now of please search some "
            "sounding sounds that the to tone with you".split())
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
    "drive": {"drive", "overdrive", "distortion", "crunch", "saturated", "gain", "overdriven"},
    "delay": {"delay", "echo", "repeats"}, "reverb": {"reverb", "room", "hall"},
}
FIELDS = ("brightness", "dynamics", "width", "tails", "fizz")


@dataclass(frozen=True)
class Tone:
    spectrum: tuple
    brightness: float
    fizz: float
    dynamics: float
    crest: float
    width: float
    tails: float
    envelope: tuple

    def to_record(self):
        return {key: list(value) if isinstance(value, tuple) else value for key, value in asdict(self).items()}

    @classmethod
    def from_record(cls, record):
        return cls(**{key: tuple(value) if isinstance(value, list) else value for key, value in record.items()})


def describe(samples):
    """Descriptors of a played excerpt that do not depend on its level."""
    values = np.asarray(samples, dtype=np.float64)
    if values.ndim == 1:
        values = values[:, None]
    if (values.ndim != 2 or values.shape[1] not in (1, 2) or len(values) < _HOP * 3
            or not np.isfinite(values).all()):
        raise ValueError("Choose a valid mono or stereo excerpt.")
    peak = np.max(np.abs(values))
    if peak == 0:
        raise ValueError("The audio is silent. Choose a passage with played notes.")
    # Normalized before any gate, so that the playback level makes no difference.
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
    hz, psd = signal.welch(played, RATE, nperseg=min(len(played), 4096), axis=0)
    energy = psd.mean(axis=1)
    totals = np.array([energy[(hz >= low) & (hz < high)].sum() for low, high in zip(BANDS, BANDS[1:])])
    if not totals.sum() > 1e-18:
        raise ValueError("The audio has no usable guitar-range signal.")
    fractions = totals / totals.sum()
    spectrum = tuple(float(value) for value in 10 * np.log10(np.maximum(fractions, 1e-5)))
    brightness = float(np.log2(np.dot(fractions, np.sqrt(np.asarray(BANDS[:-1]) * BANDS[1:]))))
    fizz = float(fractions[-2:].sum())
    side = np.mean(((played[:, 0] - played[:, 1]) / 2) ** 2) if played.shape[1] == 2 else 0.
    width = float(np.clip(side / np.mean(played ** 2), 0, 1))
    # Quiet frames after an attack: a rough cue for decay, sustain and effects.
    trailing = np.maximum.reduce([np.roll(level, delay) for delay in range(2, 13)])
    trailing[:12] = -70
    after = trailing > -16
    tails = float(np.mean(np.clip((level[after] - trailing[after] + 35) / 35, 0, 1))) if after.any() else 0.
    crest = float(20 * np.log10(np.max(np.abs(played)) / np.sqrt(np.mean(played ** 2))))
    return Tone(spectrum, brightness, fizz, float(q90 - q10), crest, width, tails,
                tuple(float(value) for value in np.maximum(level[first:last + 1], -45)))


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
        raise ValueError("Matching weights are between zero and one, with at least one above zero.")
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


def distances(candidate, reference, same_take):
    spectrum = np.abs(np.asarray(candidate.spectrum) - reference.spectrum)
    shape = float(np.mean(spectrum)) / 12
    dynamics = (abs(candidate.dynamics - reference.dynamics) + .5 * abs(candidate.crest - reference.crest)) / 18
    if same_take:
        envelope = _paired_error(candidate.envelope, reference.envelope)
        if envelope is not None:
            dynamics = .55 * dynamics + .45 * envelope / 16
    space = .65 * abs(candidate.width - reference.width) / .5 + .35 * abs(candidate.tails - reference.tails) / .4
    return {"tone": shape, "dynamics": dynamics, "space": space}


def _ranks(features):
    if not features:
        return []
    ranks = {key: (stats.rankdata([found[key] for found in features]) - .5) / len(features) for key in FIELDS}
    return [{key: float(values[index]) for key, values in ranks.items()} for index in range(len(features))]


def query_score(query, entry, ranks):
    """How well an entry answers the words of a query, from 0 to 1, or None for an empty query."""
    words = [word for word in _WORDS.findall(query.casefold()) if word not in _STOP]
    indexed = {word for value in (entry["title"], entry.get("guitar", ""), entry.get("target", ""), *entry["tags"])
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


# ---- the sounds as entries ----

def features_of(tone=None, heard=None):
    """The qualities words rank by, from a description of a render or from what the desk heard."""
    if tone is not None:
        return {key: getattr(tone, key) for key in FIELDS}
    if heard:
        return {"brightness": heard.get("brightness_oct", 0.), "dynamics": heard.get("crest_db", 0.), "width": 0.,
                "tails": heard.get("tail_db", heard.get("ambience_db", -60.)), "fizz": heard.get("brightness_oct", 0.)}
    return None


def entry(sound):
    origin = sound.origin or {}
    tags = [origin.get("kind", ""), origin.get("request", ""), origin.get("song", ""), sound.explanation, *sound.tags,
            *(note.get("text", "") for note in sound.notes), *describe_chain(sound.chain)]
    if sound.heard:
        tags += [sound.heard.get(key) or "" for key in ("distortion", "compressed", "brightness", "tail")]
    if sound.saved:
        tags.append(f"saved {sound.saved.get('name', '')}")
    # The digest of the chain as it is heard and rendered: with its Shape knobs applied.
    return {"title": sound.name, "sound": sound.id, "digest": heard_chain(sound).digest(),
            "target": origin.get("song", ""),
            "tags": [str(tag) for tag in tags if tag], "heard": sound.heard, "saved": bool(sound.saved)}


def search(entries, query="", *, tones=None, reference=None, same_take=False, priorities=None, limit=8,
           excluding=()):
    """Entries ranked by the words of `query` and, with a reference Tone, by closeness to it.

    `tones` is {sound id: Tone} for sounds rendered with the phrase; a same-take reference compares envelopes.
    Scores are similarities of descriptions, not of how the sounds feel to play.
    """
    if limit < 1:
        return []
    weights = _weights(priorities)
    tones = tones or {}
    pool = []
    for item in entries:
        if item["sound"] in excluding:
            continue
        if reference is not None and item["sound"] not in tones:
            continue
        pool.append(item)
    measured = [(item, features_of(tones.get(item["sound"]), item.get("heard"))) for item in pool]
    known = [found for _, found in measured if found is not None]
    ranked = iter(_ranks(known))
    results = []
    for item, found in measured:
        ranks = next(ranked) if found is not None else {key: .5 for key in FIELDS}
        text = query_score(query, item, ranks)
        if text == 0 and reference is None:
            continue
        terms = distances(tones[item["sound"]], reference, same_take) if reference is not None else None
        distance = sum(weights[axis] * value for axis, value in terms.items()) if terms else None
        similarity = math.exp(-distance) if distance is not None else None
        score = similarity if reference is not None else text
        if similarity is not None and text is not None:
            score = .8 * similarity + .2 * text
        results.append({**item, "score": round(100 * score, 1) if score is not None else None,
                        "similarity": round(100 * similarity, 1) if similarity is not None else None,
                        "distance": round(distance, 4) if distance is not None else None, "dimensions": terms,
                        "same_take": bool(same_take and reference is not None),
                        "traits": [high if ranks[axis] >= .66 else low for axis, low, high in (
                            ("brightness", "Warm", "Bright"), ("dynamics", "Even", "Responsive"),
                            ("width", "Focused", "Wide"), ("tails", "Shorter tails", "Sustained"),
                        ) if found is not None and (ranks[axis] <= .34 or ranks[axis] >= .66)]})
    results.sort(key=lambda row: (row["score"] or 0, row["saved"]), reverse=True)
    return results[:limit]


# ---- the cache of descriptions, and references ----

def load_cache(folder):
    try:
        found = json.loads((Path(folder) / CACHE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def save_cache(folder, cache):
    path = Path(folder) / CACHE
    part = path.with_suffix(".part")
    # Only the last 400 descriptions are kept; they cost a render each to make again.
    part.write_text(json.dumps(dict(list(cache.items())[-400:])), encoding="utf-8")
    part.replace(path)


def cache_key(digest, phrase_name):
    return f"{digest}:{phrase_name}"


def references(folder):
    """References captured from the unit by the library tool: [{"name", "file", "phrase", ...}], newest first."""
    found = []
    for info in sorted((Path(folder) / "references").glob("*/reference.json"), reverse=True):
        try:
            record = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (info.parent / "wet.wav").is_file():
            found.append({**record, "file": str(info.parent / "wet.wav")})
    return found


def at_preview_level(samples):
    """The audio at the level of the app's previews (−23 LUFS, true peak under −3 dB) when it is louder than
    that; never turned up."""
    found = measure(samples)
    if found.lufs is None:
        return np.asarray(samples, dtype=np.float32)
    return gained(samples, min(0., match_levels([found])[0]))


# ---- operations, run where the unit is held ----

def describe_sounds(engine, chains, dry, *, stop=None):
    """Describe {cache key: chain}, each the phrase played silently through it, into the cache in the data folder.

    Each description is written as soon as it is made. A sound the unit will not build, or that returns
    silence, is noted in the cache with the reason and not tried again; one that failed for a passing reason
    is only reported. Returns {"described": how many, "issues": {key: why}}. Stopping, or the player choosing
    another preset on the unit, ends it with what was described so far kept.
    """
    folder = engine.slots.folder
    described, issues = 0, {}
    for key, chain in chains.items():
        if stop is not None and stop.is_set():
            raise Stopped("Stopped.")
        try:
            record = describe(render(engine, chain, dry, tail=1., stop=stop)).to_record()
        except (ValueError, Refused) as error:
            record = {"issue": str(error)}          # this chain, whenever it is tried: remembered
            issues[key] = str(error)
        except Mismatch as error:
            issues[key] = str(error)                # the unit did not show it this time: tried again next time
            continue
        else:
            described += 1
        cache = load_cache(folder)
        cache.pop(key, None)                        # the newest last: the oldest are the ones dropped
        cache[key] = record
        save_cache(folder, cache)
    return {"described": described, "issues": issues}


# ---- the tool ----

# The narrowest first: matching renders every sound in the scope that has not been rendered with this phrase,
# and a sound not yet in the cache takes the unit a quarter of a minute to build.
SCOPES = ("The last 20", "This round", "Saved on the unit", "Every sound")


class SearchTool(Tool):
    TITLE = "Tone search"

    def build(self):
        self.results, self.reference = [], None
        find = section(self.body, "Find a sound", "Words such as: bright clean, punchy lead no delay, "
                                                  "something from Rhythm.")
        row = tk.Frame(find, bg=find["bg"])
        row.pack(fill="x")
        self.query = tk.Entry(row, font=(FONT, 10), bg=TILE, fg=INK, relief="flat", insertbackground=INK)
        self.query.pack(side="left", fill="x", expand=True, ipady=4)
        self.query.bind("<Return>", lambda _: self.find())
        button(row, "Find", self.find).pack(side="left", padx=(6, 0))

        # Matching against a reference is the rarer use: shown on request.
        match = disclosure(self.body, "Match a reference")
        label(match, "Your sounds are played silently through the unit with the desk's phrase and compared with "
                     "the reference.", size=9, color=SUBTLE, wrap=WIDTH).pack(fill="x", pady=(0, 4))
        row = tk.Frame(match, bg=match["bg"])
        row.pack(fill="x")
        button(row, "A WAV", lambda: self.offer("wav"), quiet=True).pack(side="left")
        button(row, "A captured preset", lambda: self.offer("captured"), quiet=True).pack(side="left", padx=4)
        button(row, "The sound on A", self.reference_from_a, quiet=True).pack(side="left")
        # Choosing the WAV or the captured preset, in place: one of the two at a time, under the buttons.
        self.choice = tk.Frame(match, bg=match["bg"])
        self.choice.pack(fill="x")
        self.offered = None
        self.wave_choice = FileChoice(self.choice, folders=lambda: usual_folders("wav", self.folder),
                                      patterns=("*.wav",), choose=self.use_wave, action="Use as the reference")
        self.captured = tk.Frame(self.choice, bg=match["bg"])
        self.captured_list = listbox(self.captured, height=4)
        self.captured_list.pack(fill="x")
        button(self.captured, "Use as the reference", self.use_captured).pack(anchor="w", pady=(4, 0))
        self.captured_found = []
        self.reference_text = label(match, size=9, color=SOFT, wrap=WIDTH)
        self.reference_text.pack(fill="x", pady=(6, 0))
        label(match, "Among", size=9, color=SUBTLE).pack(fill="x", pady=(6, 0))
        self.scope = tk.StringVar(master=self.frame, value=SCOPES[0])
        Chips(match, self.scope, SCOPES).pack(fill="x")
        label(match, "Weighing", size=9, color=SUBTLE).pack(fill="x", pady=(4, 0))
        self.priority = tk.StringVar(master=self.frame, value="Balanced")
        Chips(match, self.priority, tuple(MATCH_PRIORITIES)).pack(fill="x")
        row = tk.Frame(match, bg=match["bg"])
        row.pack(fill="x", pady=(6, 0))
        button(row, "Find the closest", self.match).pack(side="left")
        button(row, "Stop", self.stop, quiet=True).pack(side="left", padx=4)
        button(row, "Hear the reference", self.hear_reference, quiet=True).pack(side="left")

        found = section(self.body, "Results")
        self.box = listbox(found, height=8)
        self.box.pack(fill="x")
        row = tk.Frame(found, bg=found["bg"])
        row.pack(fill="x", pady=(6, 0))
        for side in ("a", "b"):
            button(row, f"Put on {side.upper()}", lambda side=side: self.put(side)).pack(side="left", padx=(0, 6))
        self.detail = label(found, size=9, color=SUBTLE, wrap=WIDTH)
        self.detail.pack(fill="x", pady=(6, 0))
        self.box.bind("<<ListboxSelect>>", lambda _: self.describe_selected())

    # finding

    def entries(self):
        sounds = self.studio.store.sounds()
        scope = self.scope.get()
        if scope == "Saved on the unit":
            sounds = [sound for sound in sounds if sound.saved or (sound.origin or {}).get("kind") == "preset"]
        elif scope == "This round":
            found = self.studio.round()
            ids = {candidate["sound"] for candidate in found["candidates"]} if found else set()
            sounds = [sound for sound in sounds if sound.id in ids]
        elif scope == "The last 20":
            sounds = sounds[-20:]
        return [entry(sound) for sound in sounds]

    def tones(self):
        taken = phrase(self.studio)
        if taken is None:
            return {}
        cache = load_cache(self.folder)
        found = {}
        for item in self.entries():
            record = cache.get(cache_key(item["digest"], taken[0]))
            if record and "issue" not in record:
                found[item["sound"]] = Tone.from_record(record)
        return found

    def find(self):
        text = self.query.get().strip()
        if not text:
            return
        self.results = search(self.entries(), text, tones=self.tones(), limit=20)
        self.show("No sound answers those words." if not self.results else "")

    def show(self, empty=""):
        self.box.delete(0, "end")
        for row in self.results:
            score = f"{row['score']:.0f}" if row["score"] is not None else "–"
            traits = ", ".join(row["traits"])
            self.box.insert("end", f"{score:>4}  {row['title']}" + (f"  ·  {traits}" if traits else "")
                                   + ("  ★" if row["saved"] else ""))
        self.say(empty)

    def describe_selected(self):
        chosen = self.box.curselection()
        if not chosen:
            return
        row = self.results[chosen[0]]
        terms = row.get("dimensions")
        how = ("Distance: " + ", ".join(f"{name} {value:.2f}" for name, value in terms.items())) if terms else ""
        self.detail.configure(text=(how + ("  ·  same phrase" if row.get("same_take") else "")).strip(" ·"))

    def put(self, side):
        chosen = self.box.curselection()
        if chosen:
            self.studio.put(side, self.studio.store.sound(self.results[chosen[0]]["sound"]))

    # the reference

    def set_reference(self, samples, name, phrase_name=None):
        try:
            self.reference = {"tone": describe(samples), "name": name, "phrase": phrase_name, "samples": samples}
        except ValueError as error:
            self.say(str(error))
            return
        self.reference_text.configure(text=f"Reference: {name}" + (" · recorded with the desk's phrase"
                                                                   if phrase_name else ""))

    def offer(self, kind):
        """Show the choice of a WAV or of a captured preset under the buttons; pressed again, put it away."""
        for part in (self.wave_choice, self.captured):
            part.pack_forget()
        self.offered = None if kind == self.offered else kind
        if self.offered == "wav":
            self.wave_choice.look()
            self.wave_choice.pack(fill="x", pady=(4, 0))
        elif self.offered == "captured":
            self.captured_found = references(self.folder)[:15]
            self.captured_list.delete(0, "end")
            for item in self.captured_found:
                self.captured_list.insert("end", f"{item['name']} · {item.get('setlist', '')}"[:60])
            self.captured.pack(fill="x", pady=(4, 0))
            if not self.captured_found:
                self.say("No preset has been captured yet: Sounds, More with this preset, Use as reference.")

    def use_wave(self, path):
        try:
            self.set_reference(read_wave(path), Path(path).name)
        except (OSError, ValueError) as error:
            self.say(str(error))
            return
        self.offer(None)

    def use_captured(self):
        picked = self.captured_list.curselection()
        if not picked:
            self.say("Choose a captured preset in the list first.")
            return
        item = self.captured_found[picked[0]]
        try:
            self.set_reference(read_wave(item["file"]), item["name"], item.get("phrase"))
        except (OSError, ValueError) as error:
            self.say(str(error))
            return
        self.offer(None)

    def reference_from_a(self):
        sound, taken = self.studio.sound("a"), phrase(self.studio)
        if sound is None or taken is None:
            self.say("Put a sound on A and choose a phrase first.")
            return
        self.call("lab.tools.common.render", heard_chain(sound), taken[1],
                  done=lambda wet: self.set_reference(wet, f"{sound.name} (on A)", taken[0]),
                  working=f"Playing the phrase silently through {sound.name}…", renders=True)

    def hear_reference(self):
        if self.reference:
            play(at_preview_level(self.reference["samples"]), self.folder / "previews")

    def match(self):
        taken = phrase(self.studio)
        if self.reference is None:
            self.say("Choose a reference first.")
            return
        if taken is None:
            self.say("Record or choose a phrase on the desk first.")
            return
        cache = load_cache(self.folder)
        missing = {}
        for item in self.entries():
            if cache_key(item["digest"], taken[0]) not in cache:
                missing[cache_key(item["digest"], taken[0])] = heard_chain(self.studio.store.sound(item["sound"]))
        if missing:
            def described(found):
                self.rank()
                if found["issues"]:
                    self.say(f"{len(found['issues'])} of these sounds could not be played through the unit and "
                             "are left out: " + next(iter(found["issues"].values())))

            def ended(error):
                self.rank()             # what was described before it ended is kept, and ranked
                self.say(f"{error} The sounds described so far are ranked.")

            self.call("lab.tools.search.describe_sounds", missing, taken[1], done=described, failed=ended,
                      working=f"Playing the phrase silently through {len(missing)} sounds…", renders=True)
        else:
            self.rank()

    def rank(self):
        if not self.alive():
            return
        taken = phrase(self.studio)
        same = bool(self.reference.get("phrase") and taken and self.reference["phrase"] == taken[0])
        self.results = search(self.entries(), self.query.get().strip(), tones=self.tones(),
                              reference=self.reference["tone"], same_take=same, priorities=self.priority.get(),
                              limit=20)
        self.show("None of these sounds could be described." if not self.results else "")


def open(desk):  # noqa: A001
    return SearchTool.open(desk)
