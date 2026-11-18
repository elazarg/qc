"""Offline digital level checks and post-chain trim suggestions."""

import json
import math
from pathlib import Path

import numpy as np
import pyloudnorm
from scipy import signal

from . import audio, session

DIGITAL_NOTE = ("These are levels in the recorded USB files. Check the Quad Cortex input meter with your hardest "
                "playing and check the analog output, interface or mixer separately. A quiet DI can be intentional.")
TRIM_NOTE = ("Apply trim after the tone-producing blocks using a preset or scene output control. "
             "Keep input gain, overdrive level and amp gain fixed for this comparison.")


def _loudness(samples):
    value = float(pyloudnorm.Meter(audio.RATE).integrated_loudness(samples))
    return value if math.isfinite(value) else None


def measure(samples, *, passage=None):
    """Measure peaks over the whole file and gated loudness over the chosen passage."""
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim == 1:
        samples = samples[:, None]
    if (samples.ndim != 2 or samples.shape[1] not in (1, 2) or len(samples) < audio.RATE // 2
            or not np.isfinite(samples).all()):
        raise ValueError("Use at least half a second of finite mono or stereo audio.")
    active = samples
    if passage is not None:
        start, end = (round(second * audio.RATE) for second in passage)
        if not 0 <= start < end <= len(samples) or end - start < audio.RATE // 2:
            raise ValueError("The selected passage does not fit the WAV or is shorter than half a second.")
        active = samples[start:end]
    peak = float(np.max(np.abs(samples)))
    # Polyphase interpolation at 4x estimates intersample peaks in 48 kHz playback.
    true_peak = max(peak, float(np.max(np.abs(signal.resample_poly(samples, 4, 1, axis=0)))))
    result = {"sample_peak_dbfs": audio.db(peak), "true_peak_dbtp_estimate": audio.db(true_peak),
              "lufs": _loudness(active), "near_rail_samples": int(np.count_nonzero(np.abs(samples) >= .999)),
              "duration_seconds": len(samples) / audio.RATE, "measured_seconds": len(active) / audio.RATE,
              "channels": samples.shape[1]}
    if active.shape[1] == 2:
        stereo_rms = float(np.sqrt(np.mean(np.square(active, dtype=np.float64))))
        mono_rms = float(np.sqrt(np.mean(np.square(active.mean(axis=1), dtype=np.float64))))
        result["mono_fold_down_db"] = audio.db(mono_rms / stereo_rms) if stereo_rms > 1e-10 else None
    return result


def take_report(take=None):
    """Check the latest paired take, or a directory holding dry.wav and wet.wav."""
    if take is None:
        from .capture import latest_clip

        latest = latest_clip(pair=True)
        if not latest.get("dry") or not latest.get("wet"):
            raise ValueError("The latest take needs verified dry and wet audio before checking levels.")
        take = Path(latest["dry"]).parent
    take = Path(take)
    info = json.loads((take / "clip.json").read_text(encoding="utf-8"))
    passage = info.get("selected_passage_seconds")
    dry, wet = (audio.read_wave(take / f"{kind}.wav") for kind in ("dry", "wet"))
    if len(dry) != len(wet):
        raise ValueError("Choose a take with synchronized dry and wet audio of the same length.")
    readings = {"dry": measure(dry, passage=passage), "wet": measure(wet, passage=passage)}
    checks = [f"The {kind} file reached or approached full scale. Check this gain stage and record again."
              for kind, metrics in readings.items() if metrics["near_rail_samples"]]
    checks.extend(f"The selected {kind} audio is too quiet for a loudness reading. Check the routing and passage."
                  for kind, metrics in readings.items() if metrics["lufs"] is None)
    report = {"take": take.name, "selection_seconds": passage, **readings, "checks": checks,
              "measurement_note": DIGITAL_NOTE}
    if info.get("wet_preset"):
        report["preset"] = info["wet_preset"]
    return report


def latest_comparison():
    for item in sorted(session.SESSIONS.glob("*/session.json"), reverse=True):
        try:
            data = session.load_session(item.parent)
        except (OSError, ValueError):
            continue
        if data.get("state") == "ready" and data.get("kind") != "demo" and len(data.get("candidates", [])) >= 2:
            return item.parent
    raise ValueError("Save a comparison with at least two renders first.")


def _passage(manifest, candidate):
    seconds = manifest.get("continuous_audition", {}).get("excerpt_seconds")
    bypass = manifest.get("bypass", {}).get("measurements", [])
    if seconds and bypass:
        offset = 0 if candidate.get("source_clip") else bypass[0]["offset_samples_in_saved_file"] / audio.RATE
        return offset, offset + seconds
    return None


def comparison_plan(folder=None, *, reference=None, target_lufs=None, ceiling_dbtp=-3.0, offsets=None):
    """Plan a shared reference with optional explicit relative offsets in dB.

    References and offsets use audition letters or raw render filenames. Without
    a chosen reference or target, the plan only cuts gain. Louder roles can still
    be relatively louder by cutting the other roles further.
    """
    folder = latest_comparison() if folder is None else Path(folder)
    if not math.isfinite(ceiling_dbtp) or not -24 <= ceiling_dbtp <= 0:
        raise ValueError("Choose a true-peak ceiling between -24 and 0 dBTP.")
    if target_lufs is not None and (not math.isfinite(target_lufs) or not -60 <= target_lufs <= 0):
        raise ValueError("Choose a common loudness target between -60 and 0 LUFS.")
    manifest = session.load_session(folder)
    files = {x["file"]: x for x in manifest.get("candidates", []) if "file" in x}
    labels = {key: item["render"] for key, item in manifest.get("auditions", {}).items() if "render" in item}
    inverse = {filename: key for key, filename in labels.items()}

    def key_for(key):
        path = labels.get(key.upper(), key)
        if path not in files:
            raise ValueError(f"Unknown render or audition label: {key}.")
        return path

    ref = key_for(reference) if reference else None
    relatives = {}
    for key, value in (offsets or {}).items():
        if not math.isfinite(value) or not -12 <= value <= 12:
            raise ValueError("Choose relative loudness offsets within ±12 dB.")
        relatives[key_for(key)] = float(value)
    rows = {}
    for name, candidate in files.items():
        row = {"label": inverse.get(name), "file": name, "name": candidate.get("name") or candidate.get("source") or name,
               "requested_offset_db": relatives.get(name, 0.0), "post_chain_trim_db": None}
        try:
            if Path(name).name != name:
                raise ValueError("Render file must be in the comparison folder.")
            row.update(measure(audio.read_wave(folder / name), passage=_passage(manifest, candidate)))
        except (OSError, ValueError) as error:
            row["issue"] = str(error)
        else:
            if row["near_rail_samples"]:
                row["issue"] = "Near full scale in the source. Re-render with headroom before matching."
            elif row["lufs"] is None:
                row["issue"] = "Too quiet for a loudness reading."
        rows[name] = row
    valid = {key: row for key, row in rows.items() if "issue" not in row}
    if ref and ref not in valid:
        raise ValueError("Choose a reference with measurable levels and digital headroom.")
    if len(valid) < 2:
        raise ValueError("Use at least two measurable raw renders without near-full-scale samples.")

    original_bases = {key: row["lufs"] - row["requested_offset_db"] for key, row in valid.items()}
    requested = target_lufs if target_lufs is not None else original_bases[ref] if ref else min(original_bases.values())
    allowed = min(original_bases[key] + ceiling_dbtp - row["true_peak_dbtp_estimate"] for key, row in valid.items())
    base = min(requested, allowed)
    for key, row in valid.items():
        # Round downward so the displayed trim respects the ceiling if copied to a tenth-dB control.
        trim = math.floor((base - original_bases[key] + 1e-9) * 10) / 10
        row.update(post_chain_trim_db=trim, expected_lufs=round(row["lufs"] + trim, 2),
                   expected_true_peak_dbtp=round(row["true_peak_dbtp_estimate"] + trim, 2))

    return {"comparison": folder.name, "reference": ref, "requested_base_lufs": round(requested, 2),
            "planned_base_lufs": round(base, 2), "true_peak_ceiling_dbtp": ceiling_dbtp,
            "lowered_for_headroom": base < requested - .005, "renders": list(rows.values()),
            "source_take": manifest.get("source_clip"), "measurement_note": TRIM_NOTE,
            "listening_note": "Verify with the same guitar part, monitoring level and backing mix. Intentional level differences use explicit offsets."}
