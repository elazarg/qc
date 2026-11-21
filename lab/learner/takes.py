"""Recording a take: the guitar's dry input and the unit's processed return, saved together.

    folder, said = record_take(1, "Charvel", stop, ready, routing=lambda: inputs.routing(host, 1))

All four USB inputs are recorded while `routing` asks the engine which of them
is the dry guitar. When the engine cannot say (no unit, or it is busy past
the wait), the dry input is found from the signal itself where that is
unambiguous, and otherwise all four inputs are kept as they came, for the
routing to be settled later; the take's note says which happened.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import numpy as np

from lab.hearing import audio
from lab.hearing.measure import measure
from lab.learner import folders, inputs
from lab.learner.folders import LOG, write_json


def clip_paths():
    """Every recorded take, newest first."""
    folder = folders.recordings()
    clips = [*folder.glob("clip-*.wav"), *folder.glob("clip-*/dry.wav"),
             *(raw for raw in folder.glob("clip-*/inputs.wav") if not raw.with_name("dry.wav").is_file())]
    return sorted(clips, key=lambda clip: clip.stat().st_mtime_ns, reverse=True)


def clip_info(path):
    """What is known of a take: its clip.json, with the paths of its dry and wet recordings."""
    path = Path(path)
    if path.name == "inputs.wav":
        info = json.loads((path.parent / "clip.json").read_text(encoding="utf-8"))
        return {**info, "raw": str(path), "dry": None, "wet": None}
    if path.name != "dry.wav":
        return {"dry": str(path), "wet": None}
    info = json.loads((path.parent / "clip.json").read_text(encoding="utf-8"))
    return {**info, "dry": str(path), "wet": str(path.with_name("wet.wav"))}


def latest_clip():
    paths = clip_paths()
    if not paths:
        raise ValueError("There is no take yet. Record one first.")
    return clip_info(paths[0])


def infer_pair(samples, input_channel):
    """(dry, wet left, wet right) from the signal alone: only when the chosen dry input has a quiet neighbour, in
    exactly one of the two ways the unit can route its inputs to USB."""
    if input_channel not in (1, 2) or len(samples) < audio.RATE // 2:
        return None
    rms = np.sqrt(np.mean(np.square(samples, dtype=np.float64), axis=0))
    possible = []
    for dry_base, wet_base in ((0, 2), (2, 0)):
        dry = dry_base + input_channel - 1
        idle = dry_base + 2 - input_channel
        useful = min(rms[dry], rms[wet_base], rms[wet_base + 1])
        if useful > .0002 and rms[idle] < useful * .063:
            possible.append((dry + 1, wet_base + 1, wet_base + 2))
    return possible[0] if len(possible) == 1 else None


def record_take(input_channel, guitar, stop, ready, *, routing):
    """Save one performance and return its folder and what to say of it."""
    take_id = uuid4().hex[:6]
    LOG.info("Capture %s requested on physical input %d", take_id, input_channel)
    # Capture both USB pairs at once. The engine says what they are while the take runs.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip-routing") as asked:
        answer = asked.submit(routing)
        samples, dropouts = inputs.record_inputs(audio.MAX_SECONDS, (1, 2, 3, 4), stop=stop, ready=ready)
        try:
            channels, context = answer.result()
        except Exception as error:  # noqa: BLE001 - whatever stopped the engine answering, the take is kept
            LOG.warning("Capture %s: the engine did not say which inputs are dry (%s)", take_id, error, exc_info=True)
            channels = infer_pair(samples, input_channel)
            context = {"usb_mapping_source": "inferred-from-signal", "wet_preset_unverified": True,
                       "routing_unread": str(error)}
    if len(samples) < audio.RATE // 2:
        raise ValueError("Wait for Recording, then play for at least half a second.")
    folder = folders.recordings()
    folder.mkdir(parents=True, exist_ok=True)
    duration = len(samples) / audio.RATE
    destination = folder / f"clip-{datetime.now():%Y%m%d-%H%M%S}-{take_id}"
    info = {"duration_seconds": duration, "sample_rate": audio.RATE, "guitar": guitar, "physical_input": input_channel}
    if dropouts:
        info["dropouts_seconds"] = dropouts
        LOG.warning("Capture %s: the driver dropped input %d time(s); the take is kept", take_id, len(dropouts))
    gaps = f" · {len(dropouts)} dropout{'s' if len(dropouts) != 1 else ''} in the audio" if dropouts else ""
    if channels is None:
        with TemporaryDirectory(dir=folder, prefix=".capture-") as directory:
            staging = Path(directory)
            audio.write_wave(staging / "inputs.wav", samples)
            write_json(staging / "clip.json", {**info, "routing_unresolved": True,
                                               "routing_unread": context.get("routing_unread")})
            staging.rename(destination)
        return destination, (f"Clip saved · {duration:.1f}s · the unit did not say which input is dry and the signal "
                             f"does not show it: all four inputs kept, routing needs checking{gaps}")
    dry = samples[:, [channels[0] - 1]]
    wet = samples[:, [channel - 1 for channel in channels[1:]]]
    dry_found, wet_found = measure(dry), measure(wet)
    if dry_found.lufs is None and wet_found.lufs is None:
        raise ValueError("No guitar signal detected. Check your Quad Cortex input.")
    inferred = context.get("usb_mapping_source") == "inferred-from-signal"
    with TemporaryDirectory(dir=folder, prefix=".capture-") as directory:
        staging = Path(directory)
        if inferred:
            audio.write_wave(staging / "inputs.wav", samples)
        audio.write_wave(staging / "dry.wav", dry)
        audio.write_wave(staging / "wet.wav", wet)
        write_json(staging / "clip.json", {**context, **info, "dry_channel": channels[0], "wet_channels": channels[1:],
                                           "dry_peak_dbfs": dry_found.peak_db, "wet_peak_dbfs": wet_found.peak_db})
        staging.rename(destination)
    if dry_found.clipped or wet_found.clipped:
        note = " · clipping detected"
    elif dry_found.lufs is None:
        note = " · dry input quiet"
    elif wet_found.lufs is None:
        note = " · wet output quiet"
    elif inferred:
        note = " · the unit did not say which input is dry: found from the signal, preset unverified"
    else:
        note = " · dry + wet"
    LOG.info("Capture %s saved %s (%.2f seconds%s)", take_id, destination.name, duration, note)
    return destination, f"Clip saved · {duration:.1f}s{note}{gaps}"
