"""Shape one Quad Cortex working sound with either guitar or a saved DI."""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from queue import Empty
import time

from . import audio, device, diagnostics, draft_render, guitar_runtime, iterate, live, preset_design, presets, qc_library, session, styles


@contextmanager
def grid(qc, config, events, visited=()):
    device.require_clean(qc)
    address = device.live_position(qc)
    original = device.read_preset(qc)
    original_scene = device.active_scene(qc)
    if not address or address in (iterate.SCRATCH, draft_render.WORK_SLOT):
        raise RuntimeError("Select a saved sound on the Quad Cortex before starting Shape.")

    fixture = draft_render.WORK_SLOT if config.get("draft") else iterate.SCRATCH
    events({"event": "restore", "position": address, "name": original.name, "scene": original_scene, "fixture": fixture})
    if draft := config.get("draft"):
        physical_input = config["physical_input"]
        gain = config.get("gain")
        chain = draft_render.compile_chain(draft, min(draft_render.PREVIEW_GAIN if gain is None else gain, -6))
        with draft_render.working_grid(qc) as scratch:
            draft_render.build_grid(qc, scratch, chain, draft=draft)
            scene = device.active_scene(qc)
            yield device.read_preset(qc), scene, fixture, draft_render.guitar_routes(physical_input), physical_input
        return

    pick = config.get("preset")
    source_address = pick["position"] if pick else address
    try:
        source = original if source_address == address else iterate.recall(qc, source_address, pick["name"])
        if pick and source.name != pick["name"]:
            raise RuntimeError("The preset in this Quad Cortex slot changed. Refresh Sounds and choose it again.")
        scene = config.get("scene")
        if scene is None:
            scene = original_scene if source_address == address else source.default_scene
        original_routes = iterate.routes(source)
        inputs = {port for port, _ in original_routes if port}
        physical_input = next(iter(inputs)) if len(inputs) == 1 else 0
        base = iterate.prepare_fixture(qc, source_address, source, physical_input)
        if device.active_scene(qc) != scene:
            device.select_scene(qc, scene)
        yield base, scene, fixture, original_routes, physical_input
    finally:
        position = device.live_position(qc)
        if position in (fixture, source_address, address, *visited):
            restored = iterate.recall(qc, address, original.name)
            if device.active_scene(qc) != original_scene:
                device.select_scene(qc, original_scene)
            if iterate.sound_state(restored) != iterate.sound_state(original):
                raise RuntimeError("The Quad Cortex restored the saved sound. Check its last edits on the device.")


def drain(commands):
    messages = {}
    try:
        first = commands.get(timeout=.075)
        messages[first["event"]] = first
        while True:
            message = commands.get_nowait()
            messages[message["event"]] = message
    except Empty:
        return messages


def input_routes(physical_routes, physical_input, source):
    if source == "guitar":
        return physical_routes
    if source != "phrase":
        raise ValueError("Choose Guitar or Phrase as the input.")
    return [(12 if inlet == physical_input else inlet, outlet) for inlet, outlet in physical_routes]


def switch_source(qc, position, physical_routes, physical_input, message, stream_stack, events):
    if device.live_position(qc) != position:
        raise RuntimeError("The selected preset changed on the Quad Cortex.")
    source = message["mode"]
    stream_stack.close()
    routes = input_routes(physical_routes, physical_input, source)
    try:
        if source == "phrase":
            if not message.get("take"):
                raise ValueError("Choose or record a dry phrase first.")
            _, _, dry, _ = iterate.take_audio(Path(message["take"]))
            iterate.set_routes(qc, routes)
            stream, meters = stream_stack.enter_context(audio.loop_feed(dry))
        else:
            iterate.set_routes(qc, routes)
            stream, meters = None, {}
        if device.live_position(qc) != position or iterate.routes(device.read_preset(qc)) != routes:
            raise RuntimeError("The Quad Cortex could not verify the input switch.")
    except Exception as error:
        if source != "phrase" or device.live_position(qc) != position:
            raise
        stream_stack.close()
        iterate.set_routes(qc, physical_routes)
        if iterate.routes(device.read_preset(qc)) != physical_routes:
            raise RuntimeError("The Quad Cortex could not restore the guitar input.") from error
        events({"event": "source", "mode": "guitar", "reason": str(error)})
        return None, {}
    events({"event": "source", "mode": source})
    return stream, meters


def attach_observer(qc, physical_input, config, streams, events, armed_until):
    """Start dry analysis only after the reamp stream has released ASIO."""
    allow = config.get("arm") is True and (armed_until is None or time.monotonic() < armed_until)
    try:
        channel = device.recording_channels_from(qc, physical_input)[0]
        observer = streams.enter_context(guitar_runtime.listen({**config, "arm": allow}, channel))
    except Exception as error:
        diagnostics.LOG.info("Shape dry input observation could not start: %s", error)
        events({"event": "guitar-status", "text": f"Guitar observation is unavailable. {error}"})
        return None, armed_until
    if allow and observer.detector is not None:
        if armed_until is None:
            armed_until = time.monotonic() + observer.detector.arm_seconds
        status = "Observing dry guitar input. A mapped return gesture is briefly armed."
    else:
        status = "Observing dry guitar input."
    events({"event": "guitar-status", "text": status})
    return observer, armed_until


def observe_guitar(observer, now, available, settings, armed_until, events):
    """Return a bounded proposal only from an explicitly armed, supported control."""
    observation, gesture = observer.poll(now)
    if observation is not None:
        events(observation)
    if gesture is None:
        return settings, None
    proposed = settings
    if (observer.allow_changes and armed_until is not None and now < armed_until
            and observer.detector.is_armed(now) and gesture.control in available):
        proposed = gesture.apply(settings)
    outcome = {**gesture.to_dict(), "event": "guitar-gesture", "applied": proposed != settings,
               "value": proposed.get(gesture.control, 0.)}
    return proposed, outcome


def run(config, stop, commands, events):
    guitar_config = config.get("guitar_config")
    try:
        if stop.is_set():
            return {}
        with device.connect(write=True) as qc, grid(qc, config, events) as selected, ExitStack() as stream_stack:
            base, scene, position, physical_routes, physical_input = selected
            available = styles.available_knobs(base, scene)
            events({"event": "controls", "available": available})
            if stop.is_set():
                return {}
            settings = dict(config.get("values", {}))
            previous = live.update(qc, base, scene, settings, {}, position=position)
            observer = armed_until = None

            def change_input(message):
                nonlocal observer, armed_until
                old_observer, observer = observer, None
                stream, meters = switch_source(qc, position, physical_routes, physical_input, message, stream_stack, events)
                diagnostics.LOG.info("Shape using %s on scene %s of %s",
                                     "Phrase" if stream is not None else "Guitar", device.SCENES[scene], base.name)
                if guitar_config and not stop.is_set():
                    if stream is None:
                        observer, armed_until = attach_observer(qc, physical_input, guitar_config,
                                                               stream_stack, events, armed_until)
                    else:
                        if old_observer is not None:
                            events({"event": "guitar-state", "control": old_observer.control,
                                    "label": None, "confidence": 0.})
                        events({"event": "guitar-status", "text": "Dry guitar observation pauses in Phrase mode."})
                return stream, meters

            stream, meters = change_input(config)
            checked, frames = time.monotonic(), -1
            while not stop.is_set():
                pending = drain(commands)
                if stop.is_set():
                    break
                if source := pending.get("source"):
                    stream, meters = change_input(source)
                    frames = -1
                if knobs := pending.get("knobs"):
                    settings = dict(knobs["values"])
                    previous = live.update(qc, base, scene, settings, previous, position=position)
                now = time.monotonic()
                if observer is not None:
                    if observer.allow_changes and armed_until is not None and now >= armed_until:
                        observer.allow_changes = False
                        events({"event": "guitar-status", "text": "Gesture arm expired. Observing dry guitar input."})
                    proposed, outcome = observe_guitar(observer, now, available, settings, armed_until, events)
                    if proposed != settings:
                        previous = live.update(qc, base, scene, proposed, previous, position=position)
                        settings = proposed
                    if outcome is not None:
                        events(outcome)
                if now - checked >= .8:
                    if device.live_position(qc) != position or device.active_scene(qc) != scene:
                        raise RuntimeError("The Quad Cortex preset or scene changed during Shape.")
                    if stream is not None and (not stream.active or frames == meters["frames"]):
                        raise RuntimeError("The Quad Cortex loop stopped responding.")
                    frames = meters.get("frames", -1)
                    events({"event": "heartbeat", "audio": dict(meters)})
                    checked = now
            return dict(meters)
    finally:
        if guitar_config:
            events({"event": "guitar-stopped"})


def render_variant(take, config):
    """Reamp a saved preset with musical offsets, keeping its native assets."""
    take = Path(take)
    info, passage, dry, wet = iterate.take_audio(take)
    with device.connect(write=True) as qc:
        offset = iterate.calibration_offset(qc)
        with grid(qc, config, lambda _: None) as selected:
            base, scene, position, _, _ = selected
            shape = live.update(qc, base, scene, config["values"], {}, position=position)
            processed = audio.reamp(dry)[offset:offset + len(dry)].copy()
            if processed.shape != wet.shape:
                raise RuntimeError("The Quad Cortex returned incomplete audio.")
    name = config.get("name", "Current Quad Cortex") + " · shaped"
    folder, manifest = session.new_session("knobs", info.get("guitar", ""), info.get("pickup_note", ""), info.get("song", ""))
    manifest.update(source_clip=take.name, selection_seconds=list(passage), knob_values=config["values"],
                    compared_base=config.get("preset"), change_parameters=list(shape.values()))
    return session.make_shortlist(folder, manifest, [
        ({"name": "Original recording", "audition_role": "original"}, wet),
        ({"name": name, "audition_role": "shape"}, processed),
    ], wet)


def save_variant(config):
    """Write the existing working chain, after applying offsets, to a free Lab slot."""
    visited = []
    with device.connect(write=True) as qc:
        with grid(qc, config, lambda _: None, visited) as selected:
            base, scene, position, physical, _ = selected
            live.update(qc, base, scene, config["values"], {}, position=position)
            picked = qc_library.destination(qc, preset_design.preset_name(config.get("name", "Shaped tone") + " Shape"))
            if device.live_position(qc) != position:
                raise RuntimeError("The Quad Cortex changed before the save.")
            iterate.set_routes(qc, physical)
            current = device.read_preset(qc)
            if iterate.routes(current) != physical or device.live_position(qc) != position:
                raise RuntimeError("The Quad Cortex did not verify the working sound.")
            qc.write_preset_file(picked.folder_key, picked.index, picked.name)
            time.sleep(.7)
            visited.append(picked.position)
            check = iterate.recall(qc, picked.position, picked.name)
            if iterate.sound_state(current) != iterate.sound_state(check) or iterate.routes(check) != physical:
                raise RuntimeError("The new preset slot did not match the shaped sound.")
            presets.archive(check, picked.position, qc.firmware, reason="Preserved shaped Quad Cortex preset")
    return picked, scene, qc_library.preset_fingerprint(check)
