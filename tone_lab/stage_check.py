"""Create an offline, level-matched check of two independent output EQ moves."""

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
from scipy import signal

from . import audio, guitar_compensation, session


CONTEXTS = {
    "frfr": {"name": "Full-range speaker or PA", "high_pass_hz": 65, "shelf_hz": 4200, "shelf_db": -2.0},
    "cab": {"name": "Physical guitar cabinet", "high_pass_hz": 50, "shelf_hz": 4000, "shelf_db": -1.0},
    "mix": {"name": "Band mix", "high_pass_hz": 80, "shelf_hz": 4800, "shelf_db": -1.5},
}
SOURCES = [
    {"title": "Quad Cortex manual", "url": "https://neuraldsp.com/manual/quad-cortex"},
    {"title": "Yamaha PA beginner's guide",
     "url": "https://usa.yamaha.com/audio/portable-pa/explore/pa-beginners-guide/setting/band-pa.html"},
]
NOTES = [
    "Play the comparison at rehearsal level alongside the rest of the band. These small EQ moves are listening tests.",
    "Compare the low cut and top trim separately. Keep enough bass for your tuning and enough presence to hear the guitar in the mix.",
    "Use post-chain EQ to adjust a tone that already plays well. Use pre-drive EQ when the problem is low notes driving the amp too hard, then recheck the tone and output level.",
    "Save output or room correction separately from the preset. Quad Cortex Global EQ can be assigned to specific output pairs and may be bypassed by the unit on resource-heavy presets.",
    "Use the wet recording from the actual destination chain. If you use a physical guitar cabinet, the full-range version with a cab or IR block is a different signal path.",
]


def _take_input(wet_path):
    samples = audio.read_wave(wet_path)
    metadata = {}
    if wet_path.name.casefold() == "wet.wav" and (wet_path.parent / "clip.json").exists():
        metadata = json.loads((wet_path.parent / "clip.json").read_text(encoding="utf-8"))
        if "selected_passage_seconds" in metadata:
            start, end = metadata["selected_passage_seconds"]
            if not 0 <= start < end <= len(samples) / audio.RATE + .01:
                raise ValueError("The saved take selection falls outside the wet recording.")
            samples = samples[round(start * audio.RATE):round(end * audio.RATE)]
    if len(samples) < audio.RATE // 2:
        raise ValueError("Use at least half a second of wet guitar audio.")
    try:
        relative = wet_path.relative_to(session.SESSIONS.resolve() / "recordings")
        take_id = relative.parts[0] if len(relative.parts) == 2 and metadata else None
    except ValueError:
        take_id = None
    return samples, metadata, take_id


def _trim_peak(samples):
    peak = float(np.max(np.abs(samples)))
    gain_db = min(0., -1 - audio.db(peak))
    return (samples * 10 ** (gain_db / 20)).astype(np.float32), round(gain_db, 3)


def create_preview(wet_path, output_dir=None, *, context="frfr", guitar="", target=""):
    """Write an A/B/C audition without changing QC settings or the original WAV."""
    if context not in CONTEXTS:
        raise ValueError("Choose frfr, cab, or mix.")
    wet_path = Path(wet_path).resolve()
    wet, metadata, take_id = _take_input(wet_path)
    source = audio.describe(wet)
    if source["lufs"] is None or source["clipped_samples"]:
        raise ValueError("Start with an audible, unclipped wet guitar recording.")

    spec = dict(CONTEXTS[context])
    hp = signal.butter(2, spec["high_pass_hz"], fs=audio.RATE, btype="highpass", output="sos")
    low, low_trim = _trim_peak(signal.sosfilt(hp, wet, axis=0))
    b, a = guitar_compensation.high_shelf(spec["shelf_hz"], spec["shelf_db"])
    high, high_trim = _trim_peak(signal.lfilter(b, a, wet, axis=0))
    renders = [
        ({"name": "Original wet tone", "post_eq": [], "post_eq_trim_db": 0.0}, wet),
        ({"name": "Low-end cleanup", "post_eq": [{"type": "high_pass", "frequency_hz": spec["high_pass_hz"],
                                                   "slope_db_per_octave": 12}], "post_eq_trim_db": low_trim}, low),
        ({"name": "Gentler top", "post_eq": [{"type": "high_shelf", "frequency_hz": spec["shelf_hz"],
                                              "gain_db": spec["shelf_db"]}], "post_eq_trim_db": high_trim}, high),
    ]
    if any(audio.describe(track)["lufs"] is None for _, track in renders):
        raise ValueError("The filtered variants are too quiet. Use a clearer wet take with guitar across the passage.")

    guitar = guitar or metadata.get("guitar", "")
    target = target or metadata.get("song", wet_path.stem)
    if output_dir is None:
        folder, manifest = session.new_session("stage-eq", guitar, "", target)
    else:
        folder = Path(output_dir).resolve()
        if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
            raise ValueError("Choose a new or empty folder for the audition.")
        folder.mkdir(parents=True, exist_ok=True)
        manifest = {"kind": "stage-eq", "guitar": guitar, "pickup": "", "target": target, "candidates": [],
                    "created_utc": datetime.now(timezone.utc).isoformat()}
    manifest.update(output_context=context, context_spec=spec, source_wet=str(wet_path), sources=SOURCES, notes=NOTES)
    if take_id is not None:
        manifest["source_clip"] = take_id
    if "selected_passage_seconds" in metadata:
        manifest["selection_seconds"] = metadata["selected_passage_seconds"]
    session.make_shortlist(folder, manifest, renders, None)
    audible = manifest["continuous_audition"]
    by_file = {item["file"]: item for item in manifest["candidates"]}
    audition = {label: {"name": by_file[item["render"]]["name"], "file": str(folder / item["file"]),
                       "post_eq": by_file[item["render"]]["post_eq"]}
                for label, item in manifest["auditions"].items()}
    return {"folder": str(folder), "file": str(folder / audible["file"]),
            "starts_seconds": audible["starts_seconds"], "auditions": audition, "context": spec,
            "manifest": str(folder / "session.json"), "notes": NOTES, "sources": SOURCES}
