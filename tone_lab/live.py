"""Hold the test preset while physical controls update the sound."""

from contextlib import ExitStack, nullcontext
import json
from pathlib import Path
from queue import Empty
import time

from qc_mcp import catalog

from . import audio, device, iterate, styles


def update(qc, base, scene, values, previous):
    edits = styles.knobs(base, scene, values)
    next_values = {(x["row"] - 1, x["column"] - 1, x.get("index")): x for x in edits}
    changed = {key: value["from"] for key, value in previous.items() if key not in next_values}
    for key, value in next_values.items():
        if key not in previous or abs(previous[key]["to"] - value["to"]) > .001:
            changed[key] = value["to"]
    if changed:
        if device.live_position(qc) != iterate.SCRATCH:
            raise RuntimeError("The preset was switched while Tone Lab had live control.")
        if any(index is None for _, _, index in changed) and device.active_scene(qc) != scene:
            raise RuntimeError("The scene was switched while Tone Lab had live control.")
        for (row, column, index), value in changed.items():
            if index is not None:
                model = base.chains[row].models[column].hash
                qc.set_param(row, column, index, catalog.to_norm(model, index, value))
                time.sleep(.012)
        for (row, column, index), value in changed.items():
            if index is None:
                qc.set_block_bypass(row, column, value)
                time.sleep(.012)
    return next_values


def run(take, mode, stop, commands, report, *, audible=True, capture_return=False, guitar_config=None,
        events=lambda event: None):
    if mode not in ("reamp", "guitar"):
        raise ValueError("Choose Reamp loop or Live guitar.")
    if take is None and mode == "reamp":
        raise ValueError("Record a dry take before starting the loop.")
    take = Path(take) if take is not None else None
    info = json.loads((take / "clip.json").read_text(encoding="utf-8")) if take is not None else {}
    dry = None
    if mode == "reamp":
        start, end = info.get("selected_passage_seconds", [0, info["duration_seconds"]])
        dry = audio.read_wave(take / "dry.wav")[round(start * audio.RATE):round(end * audio.RATE)]
        if len(dry) < audio.RATE // 2:
            raise ValueError("Use a take with at least half a second of dry guitar.")
    physical_input = info.get("physical_input", 1)
    if stop.is_set():
        return {}
    with device.connect(write=True) as qc:
        original_address = qc.get_setlist_position()
        original = device.read_preset(qc)
        scene = device.active_scene(qc)
        if not original_address or original_address == iterate.SCRATCH:
            raise ValueError("Select the saved preset you want to use as a starting point.")
        physical_routes = iterate.routes(original)
        usb_routes = iterate.reamp_routes(original, physical_input)
        events({"event": "restore", "position": original_address, "name": original.name, "scene": scene})
        try:
            if stop.is_set():
                return {}
            base = iterate.prepare_fixture(qc, original_address, original, physical_input)
            if device.active_scene(qc) != scene:
                device.select_scene(qc, scene)
            available = styles.available_knobs(base, scene)
            events({"event": "controls", "available": available})
            if mode == "guitar":
                iterate.set_routes(qc, physical_routes)
                iterate.verify_fixture(qc, original, physical_routes)
            elif audible:
                playback_routes = iterate.reamp_routes(original, physical_input, monitor=True)
                iterate.set_routes(qc, playback_routes)
                iterate.verify_fixture(qc, original, playback_routes)
            else:
                iterate.verify_fixture(qc, original, usb_routes)
            if stop.is_set():
                return {}
            audition = audio.loop_feed(dry, monitor_return=capture_return) if mode == "reamp" else nullcontext((None, {}))
            with ExitStack() as stack:
                stream, meters = stack.enter_context(audition)
                observer = None
                armed = False
                if mode == "guitar" and guitar_config and not stop.is_set():
                    from . import guitar_runtime

                    try:
                        channel = device.recording_channels_from(qc, physical_input)[0]
                        observer = stack.enter_context(guitar_runtime.listen(guitar_config, channel))
                    except Exception as error:
                        events({"event": "guitar-status", "text": f"Guitar input experiment unavailable: {error}"})
                    else:
                        armed = observer.allow_changes and observer.detector is not None
                        events({"event": "guitar-status", "text": "Guitar input is briefly armed" if armed else "Observing dry guitar only"})
                report("Reamp loop running" if mode == "reamp" else "Live guitar · turn the knobs while you play")
                previous = {}
                settings = {}
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
                        settings = dict(values)
                        previous = update(qc, base, scene, values, previous)
                    now = time.monotonic()
                    if observer is not None and not stop.is_set():
                        if armed and not observer.detector.is_armed(now):
                            armed = False
                            events({"event": "guitar-status", "text": "Arm expired · observing dry guitar only"})
                        observation, gesture = observer.poll(now)
                        if observation is not None:
                            events(observation)
                        if gesture is not None:
                            applied = (observer.allow_changes and observer.detector.is_armed(now)
                                       and gesture.control in available)
                            if applied:
                                next_settings = gesture.apply(settings)
                                applied = next_settings != settings
                                if applied:
                                    previous = update(qc, base, scene, next_settings, previous)
                                    settings = next_settings
                            events({**gesture.to_dict(), "event": "guitar-gesture", "applied": bool(applied),
                                    "value": settings.get(gesture.control, 0.0)})
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
                restored = iterate.recall(qc, original_address, original.name)
                if device.active_scene(qc) != scene:
                    device.select_scene(qc, scene)
                if iterate.sound_state(restored) != iterate.sound_state(original):
                    raise RuntimeError("The saved preset was recalled. Its starting working state is in the local preset history.")
