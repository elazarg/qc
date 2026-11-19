"""The performance, evidence and playback model for the listening desk."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from threading import Lock
from uuid import uuid4

import numpy as np

from . import audio, capture, diagnostics, feedback, level_plan, session


def same_passage(first, second):
    """Compare the sample boundaries used by the recording and reamp."""
    if first is None or second is None:
        return first is second
    try:
        start, end = (round(seconds * audio.RATE) for seconds in first)
        compare_start, compare_end = (round(seconds * audio.RATE) for seconds in second)
    except (TypeError, ValueError, OverflowError):
        return False
    return start == compare_start and end == compare_end


@dataclass(frozen=True)
class Sound:
    name: str
    path: Path
    take_id: str | None = None
    passage: tuple | None = None
    read_range: tuple | None = None
    folder: Path | None = None
    draft_id: str | None = None

    def paired_with(self, other):
        return bool(self.take_id and self.take_id == other.take_id and same_passage(self.passage, other.passage))

    def to_record(self):
        return {"name": self.name, "path": str(self.path), "take": self.take_id, "passage": self.passage,
                "range": self.read_range, "folder": str(self.folder) if self.folder else None, "draft": self.draft_id}

    @classmethod
    def from_record(cls, value):
        if not isinstance(value, dict):
            return None
        try:
            path = Path(value["path"])
            if not path.is_file():
                return None
            return cls(value["name"], path, value.get("take"),
                       tuple(value["passage"]) if value.get("passage") else None,
                       tuple(value["range"]) if value.get("range") else None,
                       Path(value["folder"]) if value.get("folder") else None, value.get("draft"))
        except (OSError, ValueError, TypeError, KeyError):
            return None


@dataclass(frozen=True)
class Performance:
    folder: Path
    info: dict

    @property
    def name(self):
        return self.info.get("song") or self.info.get("passage") or "Recorded phrase"

    @property
    def section(self):
        value = self.info.get("selected_passage_seconds")
        return tuple(value) if value else (0, self.info["duration_seconds"])

    @property
    def original(self):
        name = self.info.get("wet_preset", "Recorded tone")
        scene = self.info.get("wet_scene")
        if self.info.get("wet_preset_unverified"):
            name = "Recorded tone · preset unverified"
        return Sound(name + (f" · {scene}" if scene else ""), Path(self.info["wet"]), self.folder.name,
                     self.section, self.section)


def performance(path):
    path = Path(path)
    source = path / "dry.wav" if path.is_dir() else path
    info = capture.clip_info(source)
    if (source.name != "dry.wav" or not source.is_file() or not info.get("wet")
            or not Path(info["wet"]).is_file() or not info.get("duration_seconds")):
        raise ValueError("That recording has no verified pair of dry and processed audio.")
    return Performance(source.parent, info)


def takes():
    result = []
    for path in capture.clip_paths():
        try:
            result.append(performance(path))
        except (OSError, ValueError, TypeError):
            continue
    return result


def comparison(folder, take, *, required=False):
    """Read a fixed-performance pair, explaining rejected fresh renders on request."""
    def unavailable(message):
        if required:
            raise ValueError(message)
        return None

    folder = Path(folder)
    saved = session.load_session(folder)
    if saved.get("source_clip") != take.folder.name:
        return unavailable("This render used a different recording.")
    if not same_passage(saved.get("selection_seconds", ()), take.section):
        return unavailable("This render used a different part of the recording.")

    rows = [row for row in saved.get("candidates", []) if row.get("file")]
    included = {item["render"] for item in saved.get("auditions", {}).values()}
    original = next((row for row in rows if row.get("audition_role") == "original"), None)
    if original is None:
        original = next((row for row in rows if "baseline" in (row.get("name") or "").casefold()), None)
    if original is None:
        original = next((row for row in rows if row["file"] in included and not row.get("excluded")), None)
    if original is None:
        return unavailable("The render returned no comparison audio.")
    if reason := original.get("excluded"):
        return unavailable(f"The original recording was excluded because it was {reason.lower()}.")
    if not (folder / original["file"]).is_file():
        return unavailable("The original comparison audio is missing.")

    alternatives = [row for row in rows if row["file"] != original["file"]]
    change = next((row for row in alternatives if row["file"] in included and not row.get("excluded")
                   and (folder / row["file"]).is_file()), None)
    if not change:
        reasons = {row.get("excluded") for row in alternatives}
        if "Silent or too quiet" in reasons:
            return unavailable("The new tone came back silent or too quiet to play.")
        if "Clipped" in reasons:
            return unavailable("The new tone clipped. Lower its output and try again.")
        return unavailable("The render did not return a playable alternative.")
    original_name = take.original.name if original.get("audition_role") == "original" else original.get("name")
    return (Sound(original_name or take.original.name, folder / original["file"], take.folder.name, take.section,
                  folder=folder),
            Sound(change.get("name") or change.get("source") or "Processed version", folder / change["file"],
                  take.folder.name, take.section, folder=folder, draft_id=saved.get("draft_id")))


def latest_comparison(take):
    for folder, record in feedback.comparisons(take.folder.name):
        if record.get("kind") in ("draft", "knobs", "style") and (pair := comparison(folder, take)):
            return pair
    return take.original, None


def draft_comparison(take, draft_id):
    for folder, record in feedback.comparisons(take.folder.name):
        if record.get("draft_id") == draft_id and (pair := comparison(folder, take)):
            return pair
    return None


def captured_sound(reference, take):
    return Sound(f"{reference.name} · {reference.scene}", Path(reference.path), take.folder.name, take.section,
                 folder=Path(reference.path).parent)


def reference_history():
    for proof in sorted(session.SESSIONS.glob("*-preset-reference-*/session.json"), reverse=True):
        try:
            record = session.load_session(proof.parent)
            source = record["qc_reference"]
            wav = proof.with_name("reference.wav")
            if record["state"] == "ready" and wav.is_file():
                sound = Sound(f"{source['name']} · {source['scene']}", wav, record["source_clip"],
                              tuple(record["selection_seconds"]), folder=proof.parent)
                yield sound, source
        except (OSError, ValueError, KeyError, TypeError):
            continue


def closest(reference, take):
    from . import tone_search

    matches = tone_search.search(reference=reference.path, take_id=take.folder.name)
    for row in matches:
        if row.get("take_id") != take.folder.name:
            continue
        path = Path(row["file"])
        folder = row.get("session")
        selection = row.get("selection")
        if folder:
            info = session.load_session(folder)
            if not same_passage(info.get("selection_seconds", ()), take.section):
                continue
        elif selection and not same_passage(selection, take.section):
            continue
        return Sound(row["title"], path, take.folder.name, take.section,
                     tuple(selection) if selection else None, folder=folder)
    raise RuntimeError("There is no saved alternative for this phrase. Render a draft or choose a phrase with comparisons.")


def keep(sound, take, target, baseline, *, note=""):
    """Store the decision with enough identity to reconstruct the listening context."""
    root = session.SESSIONS / "listening-decisions"
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    path = root / f"{now:%Y%m%d-%H%M%S}-{uuid4().hex[:6]}.json"
    session.write_json(path, {"created_utc": now.isoformat(), "kept": str(sound.path), "name": sound.name,
                             "draft_id": sound.draft_id, "against": str(baseline.path) if baseline else None,
                             "take": take.folder.name if take else None, "take_guitar": take.info.get("guitar") if take else None,
                             "passage": list(sound.passage) if sound.passage else None, "target": target, "note": note})
    return path


def keep_tone(tone, shape, take, *, source, reference="", note=""):
    """Store a preference for the tone heard live, identified by its recipe and shape."""
    root = session.SESSIONS / "listening-decisions"
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    path = root / f"{now:%Y%m%d-%H%M%S}-{uuid4().hex[:6]}.json"
    session.write_json(path, {"created_utc": now.isoformat(), "kind": "live", "tone": tone, "name": tone.get("name"),
                             "draft_id": tone.get("draft_id"), "shape": dict(shape), "heard_with": source,
                             "take": take.folder.name if take else None,
                             "take_guitar": take.info.get("guitar") if take else None,
                             "target": reference, "note": note})
    return path


def route(text, *, has_draft):
    """Resolve explicit session commands and pass open-ended musical language to the planner."""
    request = text.strip().rstrip(".?!")
    lowered = request.casefold()
    volume = {
        "louder": ("louder", "too quiet", "turn it up", "turn up the volume", "make it louder"),
        "quieter": ("quieter", "too loud", "turn it down", "turn down the volume", "make it quieter"),
        "slightly louder": ("a little louder", "slightly louder"),
        "slightly quieter": ("a little quieter", "slightly quieter"),
        "normal": ("normal volume", "reset volume"),
        "mute": ("mute", "mute playback"),
        "unmute": ("unmute", "unmute playback"),
    }
    for command, phrases in volume.items():
        if lowered in phrases:
            return "volume", command
    if lowered in {"stop", "stop playback", "go back to playing", "stop preview"}:
        return "stop", ""
    if lowered in {"undo", "undo that", "undo this", "go back", "revert", "revert that", "put it back",
                   "undo the last change", "change it back"}:
        return "undo", ""
    side = re.fullmatch(r"(?:(?:switch|go|change|flip)(?: back)? to|play|hear|select|use)?\s*(?:tone )?([ab])", lowered)
    if side:
        return "side", side[1]
    if lowered in {"save", "save this", "save it", "save this sound", "save this tone", "save to qc",
                   "save to the quad cortex", "save this to the quad cortex"}:
        return "save", ""
    if lowered in {"guitar", "live guitar", "play guitar", "let me play", "guitar input"}:
        return "source", "guitar"
    if lowered in {"phrase", "play the phrase", "play phrase", "loop", "loop the phrase", "loop phrase"}:
        return "source", "phrase"
    if lowered in {"listen", "compare", "compare sounds"}:
        return "compare", ""
    if lowered in {"hear the draft", "hear draft", "compare the draft"}:
        return "draft", ""
    if lowered in {"keep this", "keep this sound", "keep this version"}:
        return "keep", ""
    note = re.match(r"(?i)^keep this(?: sound| version)?[,:]\s*(\S.*)$", request)
    if note:
        return "keep", note[1]
    if lowered in {"match the reference", "match reference", "find a closer sound"}:
        return "match", ""
    match = re.fullmatch(r"(?:use|compare (?:with|to))\s+(?:my\s+)?(.+?)(?:\s+preset)?(?:\s+as\s+(?:the |a )?reference)?",
                         request, flags=re.IGNORECASE)
    if match and (lowered.startswith("compare ") or lowered.endswith(" reference")):
        return "reference", match[1].strip(" \"'")
    if re.search(r"(?i)\b(?:current|live) (?:qc |quad cortex )?(?:sound|tone|preset)\b", request):
        return "adjust-live", text
    from . import preset_design

    if preset_design.is_request(request):
        return "create", text
    if preset_design.is_revision(request):
        return ("refine" if has_draft else "adjust-live"), text
    if re.match(r"(?i)^(?:why|what|how|which|can i|does|is)\b", request):
        return "adjust-live", text
    # A new sound needs a sign that one was asked for. An observation about the
    # current sound must never replace it, whether or not a draft is loaded.
    if (re.match(r"(?i)^(?:make|give me|build|create|design|i want|i need)\b", request)
            or re.search(r"(?i)\b(?:style|inspired|in the vein of|sounds? like|tone like)\b", request)
            or names_something(request)):
        return "create", text
    return "adjust-live", text


PRODUCT_NAMES = re.compile(r"\b(?:Quad Cortex|Cortex Control|Tone Lab|Neural DSP|Guitar Learner)\b")


def names_something(text):
    """Notice an artist, song or rig written as a proper name, such as “Andy Timmons”."""
    return bool(re.search(r"\b[A-Z][\w'’]+(?:\s+(?:of|the|and|&|for|a))?\s+[A-Z][\w'’]+", PRODUCT_NAMES.sub("", text)))


def samples(sound):
    wave = audio.read_wave(sound.path)
    if sound.read_range:
        start, end = (round(value * audio.RATE) for value in sound.read_range)
        wave = wave[start:end]
    if len(wave) < audio.RATE // 2:
        raise ValueError("Choose a phrase of at least half a second.")
    return wave


def playback_material(sounds):
    if len(sounds) == 2 and not sounds[0].paired_with(sounds[1]):
        raise ValueError("These recordings are from different performances. Play them individually.")
    waves = [samples(sound) for sound in sounds]
    if len({len(wave) for wave in waves}) != 1:
        raise RuntimeError("The two recordings have different lengths. Capture them again from the same phrase.")
    if any(np.any(np.abs(wave) >= .999) for wave in waves):
        raise ValueError("The sound is silent or clipped. Choose another phrase or reference.")
    tracks = [np.array(np.broadcast_to(wave, (len(wave), 2)), dtype=np.float32) for wave in waves]
    edge = round(.006 * audio.RATE)
    ramp = np.linspace(0, 1, edge, dtype=np.float32)[:, None]
    for track in tracks:
        track[:edge] *= ramp
        track[-edge:] *= ramp[::-1]
    measured = tuple(level_plan.measure(track) for track in tracks)
    if any(item["lufs"] is None for item in measured):
        raise ValueError("The sound is silent or clipped. Choose another phrase or reference.")
    return tuple(tracks), measured


def prepare(sounds):
    tracks, metrics = playback_material(sounds)
    return tuple(track * gain for track, gain in zip(tracks, audio.audition_gains(metrics)))


class Loop:
    """Render all versions from the same sample cursor on the audio thread."""

    def __init__(self, tracks, selected=0, crossfade=480, *, gains=None):
        self.tracks = tracks
        self.selected = selected
        self.current = selected
        self.cursor = 0
        self.crossfade = crossfade
        self.weight = float(selected)
        self.progress = np.arange(1, crossfade + 1, dtype=np.float32)
        self.gains = gains or (1.,) * len(tracks)

    def process(self, output, *, match=True):
        cursor = self.cursor
        total = len(self.tracks[0])
        while len(output):
            frames = min(len(output), total - cursor)
            chunk = output[:frames]
            a = self.tracks[0][cursor:cursor + frames]
            selected = self.selected
            gain_a = self.gains[0] if match else 1.
            if len(self.tracks) == 1:
                np.multiply(a, gain_a, out=chunk)
            else:
                b = self.tracks[1][cursor:cursor + frames]
                gain_b = self.gains[1] if match else 1.
                if self.weight == selected:
                    np.multiply(b if selected else a, gain_b if selected else gain_a, out=chunk)
                    self.current = selected
                else:
                    start = self.weight
                    delta = (1 if selected else -1) / self.crossfade
                    split = min(frames, len(self.progress))
                    levels = np.clip(start + self.progress[:split] * delta, 0, 1)[:, None]
                    start_track = a[:split] * gain_a
                    chunk[:split] = start_track + (b[:split] * gain_b - start_track) * levels
                    np.multiply(b[split:] if selected else a[split:], gain_b if selected else gain_a,
                                out=chunk[split:])
                    self.weight = float(levels[-1, 0])
                    if self.weight == selected:
                        self.current = selected
            cursor = (cursor + frames) % total
            output = output[frames:]
        self.cursor = cursor


class Player:
    """Use the normal Windows output and keep ASIO access on the same owner thread."""

    def __init__(self):
        self.stream = self.loop = None
        self.failures = 0
        self.device = None
        self.metrics = ()
        self.level_feedback = {}
        self.settings = {"match": True, "muted": False, "loudness_db": 0, "master_db": 0}
        self.mix = (True, 1.)
        self.level_lock = Lock()

    def set_volume(self, settings):
        with self.level_lock:
            self.settings = dict(settings)
            self.plan_volume()

    def plan_volume(self):
        settings, metrics = self.settings, self.metrics
        match = settings["match"]
        applied = settings["loudness_db"] if match else settings["master_db"]
        feedback = {}
        if metrics and match:
            plan = audio.loudness_plan(metrics, offset_db=applied, max_boost_db=6.)
            applied = plan["target_lufs"] - plan["reference_lufs"]
            feedback = {"status": "matched" if len(metrics) > 1 else "measured",
                        "limited": plan["target_lufs"] < plan["requested_lufs"] - .05}
        elif metrics:
            safe = -3. - max(row["true_peak_dbtp_estimate"] for row in metrics)
            applied = min(applied, safe)
            feedback = {"status": "manual", "limited": applied < settings["master_db"] - .05}
        self.mix = (match, 0. if settings["muted"] else 10 ** (applied / 20))
        self.level_feedback = feedback

    def open(self, tracks, selected, *, metrics):
        self.close()
        os.environ["SD_ENABLE_ASIO"] = "1"
        import sounddevice as sd

        host = next((host for host in sd.query_hostapis() if host["name"] == "Windows WASAPI"), None)
        if host is None or host["default_output_device"] < 0:
            raise RuntimeError("Choose a Windows playback device, then try the comparison again.")
        index = host["default_output_device"]
        self.device = sd.query_devices(index)["name"]
        loop = Loop(tracks, selected, gains=audio.audition_gains(metrics))
        with self.level_lock:
            self.metrics = metrics
            self.plan_volume()
        previous_match, amplitude = self.mix
        ramp = GainRamp(amplitude)
        self.failures = 0

        def callback(out, _, timing, status):
            nonlocal previous_match
            if status.output_underflow:
                self.failures += 1
            match, gain = self.mix
            loop.process(out, match=match)
            if match != previous_match:
                # The old multiplier belongs to a different set of base gains.
                ramp.reset(gain)
                previous_match = match
            ramp.apply(out, gain)

        stream = sd.OutputStream(device=index, channels=2, samplerate=audio.RATE, dtype="float32", latency="low",
                                 callback=callback, extra_settings=sd.WasapiSettings(auto_convert=True))
        try:
            stream.start()
        except BaseException:
            stream.close()
            raise
        self.stream, self.loop = stream, loop
        return self.device

    def close(self):
        stream, self.stream, self.loop = self.stream, None, None
        if stream is not None:
            try:
                stream.abort()
            finally:
                stream.close()
        with self.level_lock:
            self.metrics = ()
            self.plan_volume()


class GainRamp:
    """Smooth listening-level changes over 20 ms on the audio callback."""

    def __init__(self, gain):
        self.progress = np.arange(1, audio.RATE // 50 + 1, dtype=np.float32)
        self.reset(gain)

    def reset(self, gain):
        self.current = self.target = gain
        self.remaining = 0

    def apply(self, out, gain):
        if gain != self.target:
            self.target = gain
            self.step = (gain - self.current) / len(self.progress)
            self.remaining = len(self.progress)
        count = min(len(out), self.remaining)
        if count:
            out[:count] *= (self.current + self.progress[:count] * self.step)[:, None]
            self.current += count * self.step
            self.remaining -= count
        out[count:] *= gain
