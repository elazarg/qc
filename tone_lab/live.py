"""Hold the test preset while physical controls update the sound."""

from contextlib import nullcontext
import json
from pathlib import Path
from queue import Empty
import time

from qc_mcp import catalog

from . import audio, device, iterate, styles


def update(qc, base, scene, values, previous):
    edits = styles.knobs(base, scene, values)
    next_values = {(x["row"] - 1, x["column"] - 1, x["index"]): x for x in edits}
    changed = {key: value["from"] for key, value in previous.items() if key not in next_values}
    for key, value in next_values.items():
        if key not in previous or abs(previous[key]["to"] - value["to"]) > .001:
            changed[key] = value["to"]
    if changed:
        if device.live_position(qc) != iterate.SCRATCH:
            raise RuntimeError("The preset was switched while Tone Lab had live control.")
        for (row, column, index), value in changed.items():
            model = base.chains[row].models[column].hash
            qc.set_param(row, column, index, catalog.to_norm(model, index, value))
            time.sleep(.012)
    return next_values


def run(take, mode, stop, commands, report, *, audible=True, capture_return=False, events=lambda event: None):
    if mode not in ("reamp", "guitar"):
        raise ValueError("Choose Reamp loop or Live guitar.")
    take = Path(take)
    info = json.loads((take / "clip.json").read_text(encoding="utf-8"))
    dry = None
    if mode == "reamp":
        start, end = info.get("selected_passage_seconds", [0, info["duration_seconds"]])
        dry = audio.read_wave(take / "dry.wav")[round(start * audio.RATE):round(end * audio.RATE)]
        if len(dry) < audio.RATE // 2:
            raise ValueError("Use a take with at least half a second of dry guitar.")
    physical_routes = [(info.get("physical_input", 1), 19), (0, 0), (0, 0), (0, 0)]
    if stop.is_set():
        return {}
    with device.connect(write=True) as qc:
        original_address = qc.get_setlist_position()
        original = device.read_preset(qc)
        scene = device.active_scene(qc)
        if (not original_address or original_address == iterate.SCRATCH
                or original.name != info.get("wet_preset") or device.SCENES[scene] != info.get("wet_scene")
                or iterate.routes(original) != physical_routes
                or (info.get("wet_position") and info["wet_position"] != original_address)):
            raise ValueError("Load the recorded Vai preset and scene before starting live control.")
        events({"event": "restore", "position": original_address, "name": original.name, "scene": scene})
        try:
            if stop.is_set():
                return {}
            iterate.recall(qc, iterate.SCRATCH, iterate.SCRATCH_NAME)
            base = iterate.verify_fixture(qc, original)
            if device.active_scene(qc) != scene:
                device.select_scene(qc, scene)
            if mode == "guitar":
                qc.set_routing(0, *physical_routes[0])
                time.sleep(.35)
                iterate.verify_fixture(qc, original, physical_routes)
            elif audible:
                playback_routes = [(12, 19), (0, 0), (0, 0), (0, 0)]
                qc.set_routing(0, *playback_routes[0])
                time.sleep(.35)
                iterate.verify_fixture(qc, original, playback_routes)
            if stop.is_set():
                return {}
            audition = audio.loop_feed(dry, monitor_return=capture_return) if mode == "reamp" else nullcontext((None, {}))
            with audition as (stream, meters):
                report("Reamp loop running" if mode == "reamp" else "Live guitar · turn the knobs while you play")
                previous = {}
                last_check = time.monotonic()
                last_frames = 0
                while not stop.is_set():
                    try:
                        values = commands.get(timeout=.09)
                    except Empty:
                        values = None
                    while values is not None:
                        try:
                            values = commands.get_nowait()
                        except Empty:
                            break
                    if values is not None and not stop.is_set():
                        previous = update(qc, base, scene, values, previous)
                    now = time.monotonic()
                    if now - last_check >= .8:
                        if device.live_position(qc) != iterate.SCRATCH:
                            raise RuntimeError("Stopped because the preset changed on the Quad Cortex.")
                        if stream is not None:
                            if not stream.active or last_frames == meters["frames"]:
                                raise RuntimeError("The Quad Cortex audio stream stopped responding.")
                            last_frames = meters["frames"]
                        events({"event": "heartbeat", "audio": dict(meters)})
                        last_check = now
                return dict(meters)
        finally:
            if device.live_position(qc) == iterate.SCRATCH:
                iterate.recall(qc, original_address, original.name)
                if device.active_scene(qc) != scene:
                    device.select_scene(qc, scene)
