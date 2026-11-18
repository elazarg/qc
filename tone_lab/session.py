"""Reproducible tone comparisons for a single guitar and passage."""

from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import random
from uuid import uuid4

import numpy as np
from scipy import signal

from . import audio, device

GUITARS = ("Charvel", "Les Paul")
SESSIONS = Path(__file__).resolve().parents[1] / "tone_sessions"


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def new_session(kind, guitar, pickup, target):
    now = datetime.now(timezone.utc)
    folder = SESSIONS / f"{now:%Y%m%d-%H%M%S}-{kind}-{uuid4().hex[:6]}"
    folder.mkdir(parents=True)
    return folder, {"kind": kind, "guitar": guitar, "pickup": pickup, "target": target,
                    "created_utc": now.isoformat(), "candidates": []}


def make_shortlist(folder, manifest, renders, reference):
    reference_metrics = audio.describe(reference) if reference is not None else None
    if reference_metrics and reference_metrics["lufs"] is None:
        raise ValueError("The reference WAV is silent or too quiet to analyze.")
    eligible = []
    for index, (settings, samples) in enumerate(renders, 1):
        filename = f"render-{index}.wav"
        audio.write_wave(folder / filename, samples)
        metrics = audio.describe(samples)
        candidate = {**settings, "file": filename, "metrics": metrics}
        manifest["candidates"].append(candidate)
        if metrics["lufs"] is None or metrics["clipped_samples"]:
            candidate["excluded"] = "Silent or too quiet" if metrics["lufs"] is None else "Clipped"
            continue
        if reference_metrics:
            candidate["spectral_distance"] = audio.distance(metrics, reference_metrics)
        eligible.append((candidate, samples))

    if reference_metrics:
        audio.write_wave(folder / "reference.wav", reference)
        manifest["reference"] = {"file": "reference.wav", "metrics": reference_metrics}
        eligible.sort(key=lambda item: item[0]["spectral_distance"])
    shortlist = eligible[:3]
    random.SystemRandom().shuffle(shortlist)
    manifest["auditions"] = {}
    if shortlist:
        gains = audio.audition_gains([candidate["metrics"] for candidate, _ in shortlist])
        pieces = []
        starts = {}
        cursor = 0
        gap = np.zeros((round(0.75 * audio.RATE), 2), dtype=np.float32)
        for label, (candidate, samples), gain in zip("ABC", shortlist, gains):
            file = f"audition-{label}.wav"
            clip = samples * gain
            audio.write_wave(folder / file, clip, pcm16=True)
            manifest["auditions"][label] = {"file": file, "render": candidate["file"], "gain": gain}
            if pieces:
                pieces.append(gap)
                cursor += len(gap)
            starts[label] = round(cursor / audio.RATE, 3)
            pieces.append(np.broadcast_to(clip, (len(clip), 2)))
            cursor += len(clip)
        manifest["continuous_audition"] = {"file": "listen.wav", "starts_seconds": starts}
        audio.write_wave(folder / "listen.wav", np.concatenate(pieces), pcm16=True)
    manifest["state"] = "ready" if shortlist else "no valid renders"
    write_json(folder / "session.json", manifest)
    if not shortlist:
        raise RuntimeError(f"Every render was silent or clipped. Check the routing and gain. Results are in {folder}.")
    return folder


def imported(guitar, pickup, target, paths, reference=None):
    if not 2 <= len(paths) <= 8:
        raise ValueError("Choose two to eight WAV renders of the same guitar passage.")
    renders = [({"source": path.name}, audio.read_wave(path)) for path in map(Path, paths)]
    reference = audio.read_wave(reference) if reference else None
    folder, manifest = new_session("import", guitar, pickup, target)
    return make_shortlist(folder, manifest, renders, reference)


def compare_qc(guitar, pickup, target, dry_path, scenes, reference=None, sweep=None,
               *, arm=False, progress=lambda message: None):
    if not arm:
        raise ValueError("Arm the Quad Cortex reamp explicitly before running it.")
    dry = audio.read_wave(dry_path)
    if (dry.shape[1] != 1 or len(dry) > audio.RATE * audio.MAX_SECONDS
            or not 0.001 <= np.max(np.abs(dry)) < 0.999):
        raise ValueError("Use an unclipped, audible mono DI up to 45 seconds long.")
    reference = audio.read_wave(reference) if reference else None
    scenes = scenes.strip().upper()
    if reference is not None and audio.describe(reference)["lufs"] is None:
        raise ValueError("The reference WAV is silent or too quiet to analyze.")
    if sweep is None and (not scenes or len(set(scenes)) != len(scenes)
                          or any(scene not in device.SCENES for scene in scenes)):
        raise ValueError("Enter distinct scene letters A–H, such as AB or ACD.")
    audio.check_asio()
    with device.connect(write=True) as qc:
        preset, position, lane = device.require_lab_preset(qc)
        original = device.active_scene(qc) if sweep is None else None
        if sweep is not None:
            column, index, values = sweep
            column -= 1
            if column < 0 or index < 0 or not 2 <= len(values) <= 7:
                raise ValueError("Choose a 1-based column, 0-based parameter index, and 2–7 values.")
            model_hash, meta, original_value = device.parameter(preset, lane, column, index)
            normalized = [device.bounded_value(model_hash, index, value, original_value) for value in values]
        folder, manifest = new_session("reamp", guitar, pickup, target)
        manifest["qc"] = {"preset": preset.name, "firmware": qc.firmware, "position": position,
                          "usb_send": 5, "usb_return": [5, 6]}
        manifest["state"] = "recording"
        audio.write_wave(folder / "dry.wav", dry)
        write_json(folder / "session.json", manifest)
        renders = []
        try:
            with ExitStack() as restore:
                if sweep is None:
                    def restore_scene():
                        device.require_lab_preset(qc, position)
                        device.select_scene(qc, original)

                    restore.callback(restore_scene)
                    for scene in scenes:
                        progress(f"Recording scene {scene}…")
                        device.require_lab_preset(qc, position)
                        device.select_scene(qc, device.SCENES.index(scene))
                        renders.append(({"scene": scene}, audio.reamp(dry)))
                else:
                    def apply(value):
                        device.write_parameter(qc, position, lane, column, index, model_hash, value)

                    restore.callback(apply, original_value)
                    for value, requested in zip(normalized, values):
                        progress(f"Recording {meta['name']} at {requested:g}…")
                        apply(value)
                        renders.append(({"row": lane + 1, "column": column + 1, "parameter": index,
                                         "name": meta["name"], "value": requested,
                                         "model_hash": model_hash}, audio.reamp(dry)))
        except (Exception, KeyboardInterrupt) as error:
            manifest.update(state="interrupted", error=str(error))
            for index, (settings, samples) in enumerate(renders, 1):
                filename = f"render-{index}.wav"
                audio.write_wave(folder / filename, samples)
                manifest["candidates"].append({**settings, "file": filename})
            write_json(folder / "session.json", manifest)
            raise
    return make_shortlist(folder, manifest, renders, reference)


def demo(guitar):
    folder, manifest = new_session("demo", guitar, "Simulated", "Synthetic test passage")
    t = np.arange(audio.RATE * 3) / audio.RATE
    notes = (82.4, 123.5, 110, 164.8, 146.8)
    phase = t % 0.6
    base = np.asarray(notes)[np.minimum((t / 0.6).astype(int), len(notes) - 1)]
    dry = np.exp(-6 * phase) * (1 - np.exp(-350 * phase)) * sum(
        np.sin(2 * np.pi * t * base * partial) / partial for partial in range(1, 9)) * 0.1
    dry = dry[:, None]
    audio.write_wave(folder / "dry.wav", dry)
    renders = []
    for name, drive, cutoff in (("Warm", 2.0, 1300), ("Balanced", 3.4, 2600), ("Bright", 4.5, 4500)):
        lowpass = signal.butter(2, cutoff, fs=audio.RATE, output="sos")
        wet = signal.sosfilt(lowpass, np.tanh(dry * drive), axis=0) * 0.45
        renders.append(({"simulation": name}, wet))
    return make_shortlist(folder, manifest, renders, renders[1][1] * 0.8)


def load_session(folder):
    return json.loads((Path(folder) / "session.json").read_text(encoding="utf-8"))


def choose(folder, label, notes):
    folder = Path(folder)
    manifest = load_session(folder)
    audition = manifest.get("auditions", {}).get(label)
    if audition is None:
        raise ValueError(f"Audition {label} does not exist in that session.")
    candidate = next(row for row in manifest["candidates"] if row["file"] == audition["render"])
    preference = {"guitar": manifest["guitar"], "pickup": manifest["pickup"], "target": manifest["target"],
                  "label": label, "notes": notes, "qc": manifest.get("qc"), "candidate": candidate}
    write_json(folder / "winner.json", preference)
    return folder / "winner.json"


def apply_winner(folder, *, arm=False):
    if not arm:
        raise ValueError("Arm the Quad Cortex explicitly before applying a winner.")
    winner = json.loads((Path(folder) / "winner.json").read_text(encoding="utf-8"))
    expected = winner.get("qc")
    if not expected:
        raise ValueError("This preference came from a demo or imported audio and has no QC settings to apply.")
    candidate = winner["candidate"]
    with device.connect(write=True) as qc:
        preset, position, row = device.require_lab_preset(qc, expected["position"])
        if preset.name != expected["preset"] or qc.firmware != expected["firmware"]:
            raise RuntimeError("Load the original Tone Lab test preset on the original firmware to apply this winner.")
        if "scene" in candidate:
            device.select_scene(qc, device.SCENES.index(candidate["scene"]))
        else:
            column, index = candidate["column"] - 1, candidate["parameter"]
            model_hash, _, current = device.parameter(preset, row, column, index)
            if model_hash != candidate["model_hash"] or row != candidate["row"] - 1:
                raise RuntimeError("The winning block has moved or changed model.")
            value = device.bounded_value(model_hash, index, candidate["value"], current)
            device.write_parameter(qc, position, row, column, index, model_hash, value)
    return {"preset": expected["preset"], "applied": winner["label"], "saved_to_device": False}
