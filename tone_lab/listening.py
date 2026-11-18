"""The performance, evidence and playback model for the listening desk."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from uuid import uuid4

import numpy as np

from . import audio, capture, diagnostics, feedback, session


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
        return bool(self.take_id and self.take_id == other.take_id and self.passage == other.passage)

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


def comparison(folder, take):
    saved = session.load_session(folder)
    if saved.get("source_clip") != take.folder.name or not saved.get("auditions"):
        return None
    passage = tuple(saved.get("selection_seconds", []))
    eligible = [row for row in saved.get("candidates", []) if row.get("file") and not row.get("excluded")]
    included = {item["render"] for item in saved["auditions"].values()}
    eligible = [row for row in eligible if row["file"] in included and (folder / row["file"]).is_file()]
    if len(eligible) < 2 or passage != take.section:
        return None
    original = next((row for row in eligible if row.get("audition_role") == "original"
                     or "baseline" in row.get("name", "").casefold()), eligible[0])
    change = next((row for row in eligible if row is not original), None)
    if not change:
        return None
    original_name = take.original.name if original.get("audition_role") == "original" else original.get("name")
    return (Sound(original_name or take.original.name, folder / original["file"], take.folder.name, passage,
                  folder=folder),
            Sound(change.get("name") or change.get("source") or "Processed version", folder / change["file"],
                  take.folder.name, passage, folder=folder, draft_id=saved.get("draft_id")))


def latest_comparison(take):
    for folder, record in feedback.comparisons(take.folder.name):
        if record.get("kind") in ("draft", "knobs", "style") and (pair := comparison(folder, take)):
            return pair
    return take.original, None


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
            if tuple(info.get("selection_seconds", [])) != take.section:
                continue
        elif selection and tuple(selection) != take.section:
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


def route(text, *, has_draft):
    """Resolve explicit session commands and pass open-ended musical language to the planner."""
    request = text.strip().rstrip(".?!")
    lowered = request.casefold()
    if lowered in {"stop", "stop playback", "go back to playing", "stop preview"}:
        return "stop", ""
    if lowered in {"listen", "compare", "compare sounds", "hear the draft"}:
        return "compare", ""
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
    if has_draft and not re.match(r"(?i)^(?:why|what|how|which|can i)\b", request):
        return "refine", text
    if re.match(r"(?i)^(?:make|give me|build|create|design|i want|i need)\b", request):
        return "create", text
    return "adjust-live", text


def samples(sound):
    wave = audio.read_wave(sound.path)
    if sound.read_range:
        start, end = (round(value * audio.RATE) for value in sound.read_range)
        wave = wave[start:end]
    if len(wave) < audio.RATE // 2:
        raise ValueError("Choose a phrase of at least half a second.")
    return wave


def prepare(sounds):
    if len(sounds) == 2 and not sounds[0].paired_with(sounds[1]):
        raise ValueError("These recordings are from different performances. Play them individually.")
    waves = [samples(sound) for sound in sounds]
    if len({len(wave) for wave in waves}) != 1:
        raise RuntimeError("The two recordings have different lengths. Capture them again from the same phrase.")
    measured = [audio.describe(wave) for wave in waves]
    if any(item["lufs"] is None or item["clipped_samples"] for item in measured):
        raise ValueError("The sound is silent or clipped. Choose another phrase or reference.")
    tracks = [np.array(np.broadcast_to(wave, (len(wave), 2)) * gain, dtype=np.float32)
              for wave, gain in zip(waves, audio.audition_gains(measured))]
    edge = round(.006 * audio.RATE)
    ramp = np.linspace(0, 1, edge, dtype=np.float32)[:, None]
    for track in tracks:
        track[:edge] *= ramp
        track[-edge:] *= ramp[::-1]
    return tuple(tracks)


class Loop:
    """Render all versions from the same sample cursor on the audio thread."""

    def __init__(self, tracks, selected=0, crossfade=480):
        self.tracks = tracks
        self.selected = selected
        self.current = selected
        self.cursor = 0
        self.crossfade = crossfade
        self.weight = float(selected)
        self.progress = np.arange(1, crossfade + 1, dtype=np.float32)

    def process(self, output):
        cursor = self.cursor
        total = len(self.tracks[0])
        while len(output):
            frames = min(len(output), total - cursor)
            chunk = output[:frames]
            a = self.tracks[0][cursor:cursor + frames]
            selected = self.selected
            if len(self.tracks) == 1:
                chunk[:] = a
            else:
                b = self.tracks[1][cursor:cursor + frames]
                if self.weight == selected:
                    chunk[:] = b if selected else a
                    self.current = selected
                else:
                    start = self.weight
                    delta = (1 if selected else -1) / self.crossfade
                    split = min(frames, len(self.progress))
                    levels = np.clip(start + self.progress[:split] * delta, 0, 1)[:, None]
                    chunk[:split] = a[:split] + (b[:split] - a[:split]) * levels
                    chunk[split:] = b[split:] if selected else a[split:]
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

    def open(self, tracks, selected):
        self.close()
        os.environ["SD_ENABLE_ASIO"] = "1"
        import sounddevice as sd

        host = next((host for host in sd.query_hostapis() if host["name"] == "Windows WASAPI"), None)
        if host is None or host["default_output_device"] < 0:
            raise RuntimeError("Choose a Windows playback device, then try the comparison again.")
        index = host["default_output_device"]
        self.device = sd.query_devices(index)["name"]
        loop = Loop(tracks, selected)
        self.failures = 0

        def callback(out, _, timing, status):
            if status.output_underflow:
                self.failures += 1
            loop.process(out)

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
