"""Shape one Quad Cortex working sound with either guitar or a saved DI."""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from queue import Empty
import time

from . import (audio, device, diagnostics, draft_render, guitar_runtime, iterate, live, live_levels,
               preset_design, presets, qc_library, session, styles)


def draft_scene(qc, config, default=None):
    """Select the scene of a draft that the desk asked for. Without one, keep `default` or the active scene."""
    scene = config.get("scene")
    if scene is None:
        scene = device.active_scene(qc) if default is None else default
    if device.active_scene(qc) != scene:
        device.select_scene(qc, scene)
    return scene


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
            scene = draft_scene(qc, config)
            draft_render.label_preview(qc, scene, draft)
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
        message = commands.get(timeout=.075)
        while True:
            if message["event"] == "tone":
                messages.pop(("knobs", message["config"].get("tone_id")), None)
            key = ("knobs", message.get("tone_id")) if message["event"] == "knobs" else message["event"]
            messages[key] = message
            message = commands.get_nowait()
    except Empty:
        return messages


def input_routes(physical_routes, physical_input, source):
    if source == "guitar":
        return physical_routes
    if source != "phrase":
        raise ValueError("Choose Guitar or Phrase as the input.")
    return [(12 if inlet == physical_input else inlet, outlet) for inlet, outlet in physical_routes]


def set_input_routes(qc, position, routes):
    if device.live_position(qc) != position:
        raise RuntimeError("The selected preset changed on the Quad Cortex.")
    iterate.set_routes(qc, routes)
    if device.live_position(qc) != position or iterate.routes(device.read_preset(qc)) != routes:
        raise RuntimeError("The Quad Cortex could not verify the input switch.")


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
            set_input_routes(qc, position, routes)
            transport = message.get("transport")
            audition = None
            if message.get("measure_return"):
                try:
                    channels = device.recording_channels_from(qc, physical_input)[1:]
                    audition = stream_stack.enter_context(audio.loop_feed(
                        dry, monitor_return=True, return_channels=channels, transport=transport))
                except Exception as error:
                    diagnostics.LOG.info("The Phrase return could not be monitored for loudness: %s", error)
            stream, meters = (audition if audition is not None else
                              stream_stack.enter_context(audio.loop_feed(dry, transport=transport)))
        else:
            set_input_routes(qc, position, routes)
            stream, meters = None, {}
    except Exception as error:
        if source != "phrase" or device.live_position(qc) != position:
            raise
        stream_stack.close()
        if prepare_guitar := message.get("prepare_guitar"):
            prepare_guitar()
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


def same_working_grid(before, after):
    if before.get("draft") or after.get("draft"):
        keys = ("draft", "gain", "physical_input", "scene")
    else:
        keys = ("preset", "scene")
    return all(before.get(key) == after.get(key) for key in keys)


@contextmanager
def resident_grids(qc, events):
    """Keep independent Quad Cortex working copies of the saved A and B chains."""
    address = device.live_position(qc)
    reserved = (iterate.SCRATCH, iterate.LIVE_B)
    if not address or address in (*reserved, draft_render.WORK_SLOT):
        raise RuntimeError("Select a saved sound on the Quad Cortex before starting Shape.")
    device.require_clean(qc)
    original = device.read_preset(qc)
    original_scene = device.active_scene(qc)
    # Recovery after a crash returns to the playing preset from any slot Tone Lab works in. A draft is
    # built in the Preview slot before it is stored in a side's own slot.
    events({"event": "restore", "position": address, "scene": original_scene, "name": original.name,
            "fixtures": [*reserved, draft_render.WORK_SLOT]})
    staging = ()
    prepared = {}

    def select(config, mode):
        nonlocal staging
        side = config["tone_id"]
        position, name = ((iterate.SCRATCH, iterate.SCRATCH_NAME) if side == "a"
                          else (iterate.LIVE_B, iterate.LIVE_B_NAME))
        if draft := config.get("draft"):
            return select_draft(config, draft, mode, side, position, name)
        pick = config.get("preset")
        source_address = pick["position"] if pick else address
        source_name = pick["name"] if pick else original.name
        if source_address in (*reserved, draft_render.WORK_SLOT):
            raise ValueError("Choose a saved tone outside Tone Lab's working slots.")
        staging = (position,)
        identity = (source_address, source_name)
        if side in prepared and prepared[side][0] == identity:
            _, stored, physical, inlet, default_scene = prepared[side]
            base = iterate.recall(qc, position, name)
            if (iterate.sound_state(base) != iterate.sound_state(stored) or iterate.routes(base) != iterate.routes(stored)
                    or (base.tempo, base.volume, base.pan) != (stored.tempo, stored.volume, stored.pan)):
                raise RuntimeError("A Tone Lab working preset changed on the Quad Cortex.")
        else:
            staging = (position, source_address)
            if source_address == address:
                source = original if device.live_position(qc) == address else iterate.recall(qc, address, source_name)
            else:
                source = iterate.recall(qc, source_address, source_name)
            if source.name != source_name:
                raise RuntimeError("The preset in this Quad Cortex slot changed. Refresh Sounds and choose it again.")
            physical = iterate.routes(source)
            inputs = {port for port, _ in physical if port}
            inlet = next(iter(inputs)) if len(inputs) == 1 else 0
            default_scene = original_scene if source_address == address else source.default_scene
            base = iterate.prepare_fixture(qc, source_address, source, inlet, position=position, monitor_mode=mode)
            prepared[side] = (identity, base, physical, inlet, default_scene)
        scene = config.get("scene")
        scene = default_scene if scene is None else scene
        if device.active_scene(qc) != scene:
            device.select_scene(qc, scene)
        staging = ()
        return base, scene, position, physical, inlet

    def select_draft(config, draft, mode, side, position, name):
        """Build a draft once, keep it in this side's working slot, and recall it from then on.

        Rebuilding a chain block by block takes the unit ten seconds or more.
        A recall takes about one, so every flip after the first is quick.

        The stored copy stays on USB, so building or recalling it sends nothing
        to the outputs. The caller connects the guitar or the phrase once the
        output trim and mute are in place.
        """
        nonlocal staging
        inlet = config["physical_input"]
        gain = config.get("gain")
        gain = min(draft_render.PREVIEW_GAIN if gain is None else gain, -6)
        physical = draft_render.guitar_routes(inlet)
        identity = ("draft", draft["id"], gain, inlet)
        staging = (position,)
        if side in prepared and prepared[side][0] == identity:
            _, stored, _, _, scene = prepared[side]
            base = iterate.recall(qc, position, name)
            if iterate.sound_state(base) != iterate.sound_state(stored) or iterate.routes(base) != iterate.routes(stored):
                raise RuntimeError("A Tone Lab working preset changed on the Quad Cortex.")
            scene = draft_scene(qc, config, scene)
        else:
            staging = (position, draft_render.WORK_SLOT)
            # Confirm the slot is ours before spending time on the build.
            try:
                holder = iterate.recall(qc, position, None)
            except RuntimeError as error:
                raise iterate.FixtureUnavailable("The Quad Cortex could not recall a Tone Lab working slot.") from error
            blank = (holder.name.strip().casefold() in ("", "unsaved")
                     and not any(model.hash for row in holder.chains for model in row.models))
            if not blank and holder.name != name:
                raise iterate.FixtureUnavailable(f"{iterate.fixture_identity(position)[1]} contains another preset. "
                                                 "Tone Lab left it untouched.")
            if blank and position == iterate.LIVE_B:
                iterate._confirm_empty_live_b(qc)
            if not blank:
                presets.archive(holder, position, qc.firmware, reason="Previous Tone Lab working copy")
            scratch = iterate.recall(qc, draft_render.WORK_SLOT, None)
            empty = (scratch.name.strip().casefold() in ("", "unsaved")
                     and not any(model.hash for row in scratch.chains for model in row.models))
            if scratch.name not in (draft_render.WORK_NAME, draft_render.LEGACY_WORK_NAME) and not empty:
                raise RuntimeError("My Presets 32H is in use. The draft needs the Tone Lab working slot.")
            scratch = draft_render.name_preview_slot(qc, scratch)
            chain = draft_render.compile_chain(draft, gain)
            draft_render.build_grid(qc, scratch, chain, draft=draft)
            draft_render.label_preview(qc, device.active_scene(qc), draft)
            built = device.read_preset(qc)
            draft_render.still_working(qc, "the draft was being stored")
            qc.write_preset_file(position["folder_key"], position["position"], name)
            time.sleep(.55)
            base = iterate.recall(qc, position, name)
            if iterate.sound_state(base) != iterate.sound_state(built) or iterate.routes(base) != iterate.routes(built):
                raise RuntimeError("The Quad Cortex did not confirm the stored copy of the draft.")
            scene = draft_scene(qc, config)
            prepared[side] = (identity, base, physical, inlet, scene)
        if device.active_scene(qc) != scene:
            device.select_scene(qc, scene)
        staging = ()
        return base, scene, position, physical, inlet

    try:
        yield select
    finally:
        owned = ((iterate.SCRATCH,) if "a" in prepared else ()) + ((iterate.LIVE_B,) if "b" in prepared else ())
        if device.live_position(qc) in (address, *owned, *staging):
            restored = iterate.recall(qc, address, original.name)
            if device.active_scene(qc) != original_scene:
                device.select_scene(qc, original_scene)
            if iterate.sound_state(restored) != iterate.sound_state(original) or iterate.routes(restored) != iterate.routes(original):
                raise RuntimeError("The Quad Cortex restored the saved sound. Check its last edits on the device.")


def run(config, stop, commands, events):
    guitar_config = config.get("guitar_config")
    try:
        if stop.is_set():
            return {}
        with device.connect(write=True) as qc, ExitStack() as tone_stack, ExitStack() as stream_stack:
            current = config
            anchor = observer = armed_until = resident = None
            warm_available = True
            active_source = active_take = phrase_take = None
            level_config = config.get("level")
            level_take = (level_config or {}).get("take", current.get("take"))
            cursor = audio.PhraseCursor()
            levels = live_levels.Levels(level_config, firmware=getattr(qc, "firmware", None))

            def report(event):
                nonlocal anchor
                if event["event"] == "restore":
                    before = (event["position"], event["scene"])
                    if anchor is not None and before != anchor:
                        raise RuntimeError("The Quad Cortex changed while restoring the previous working preset.")
                    anchor = before
                events(event)

            def announce(reason=None, requested_revision=None):
                tone_id = current.get("tone_id")
                revision = current.get("tone_revision")
                events({"event": "controls", "available": available, "tone_id": tone_id, "tone_revision": revision})
                events({"event": "tone", "tone_id": tone_id, "name": current.get("name"), "reason": reason,
                        "tone_revision": revision, "requested_revision": requested_revision})

            def close_tone():
                nonlocal resident
                resident = None
                tone_stack.close()
                if anchor is not None and (device.live_position(qc), device.active_scene(qc)) != anchor:
                    raise RuntimeError("The Quad Cortex changed while restoring the previous working preset.")

            def open_tone(pick, mode):
                nonlocal resident, warm_available
                events({"event": "loading-tone", "tone_id": pick.get("tone_id"), "name": pick.get("name")})
                use_resident = warm_available and pick.get("tone_id") in ("a", "b")
                if resident is None or not use_resident:
                    close_tone()
                if use_resident:
                    if resident is None:
                        resident = tone_stack.enter_context(resident_grids(qc, report))
                    try:
                        new_grid = resident(pick, mode)
                    except iterate.FixtureUnavailable as error:
                        diagnostics.LOG.info("A second named Quad Cortex working slot is unavailable: %s", error)
                        close_tone()
                        warm_available = False
                if resident is None:
                    new_grid = tone_stack.enter_context(grid(qc, pick, report))
                new_base, new_scene, _, _, _ = new_grid
                return new_grid, styles.available_knobs(new_base, new_scene)

            (base, scene, position, physical_routes, physical_input), available = open_tone(current, config["mode"])
            if stop.is_set():
                return {}
            settings = dict(current.get("values", {}))

            def check_active():
                if device.live_position(qc) != position or device.active_scene(qc) != scene:
                    raise RuntimeError("The Quad Cortex preset or scene changed during Shape.")

            def adjust_level(*, bind=False, source=None):
                cursor.level_probe = None
                if bind:
                    levels.bind(base, scene, physical_routes)
                levels.select(current, settings, level_take, source=source or active_source or config["mode"])
                levels.apply(qc, check_active, events)

            previous = live.update(qc, base, scene, settings, {}, position=position)
            adjust_level(bind=True)
            announce()

            def change_input(message):
                nonlocal observer, armed_until, active_source, active_take, phrase_take, cursor, level_take
                new_take = message.get("take") if message["mode"] == "phrase" else None
                if message["mode"] == "phrase" and (new_take != phrase_take or message.get("reload")):
                    levels.clear_boost(qc, check_active)
                    cursor = audio.PhraseCursor()
                    phrase_take = new_take
                cursor.level_probe = None
                level_take = message.get("take", level_take)
                old_observer, observer = observer, None

                def prepare_guitar():
                    adjust_level(source="guitar")

                if message["mode"] == "guitar":
                    prepare_guitar()
                stream, meters = switch_source(qc, position, physical_routes, physical_input,
                                               {**message, "transport": cursor, "measure_return": levels.enabled,
                                                "prepare_guitar": prepare_guitar},
                                               stream_stack, events)
                active_source, active_take = ("phrase", new_take) if stream is not None else ("guitar", None)
                levels.return_unavailable = active_source == "phrase" and not cursor.returns
                adjust_level()
                diagnostics.LOG.info("Shape using %s on scene %s of %s",
                                     active_source.title(), device.SCENES[scene], current.get("name") or base.name)
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

            def retune(pick):
                nonlocal current, base, scene, position, physical_routes, physical_input, available
                nonlocal settings, previous, observer, armed_until
                check_active()
                target_values = dict(pick.get("values", {}))
                if same_working_grid(current, pick):
                    if target_values != settings:
                        levels.clear_boost(qc, check_active)
                    previous = live.update(qc, base, scene, target_values, previous, position=position)
                    settings, current = target_values, pick
                    adjust_level()
                    announce()
                    return

                old_input = physical_input
                # Keep ASIO running and advance through the same performance while the
                # unit recalls another chain. Silence the transient recall/routing gap.
                cursor.level_probe = None
                cursor.muted = True
                reason = None
                requested_revision = pick.get("tone_revision")

                def load(candidate, values):
                    loaded, controls = open_tone(candidate, active_source)
                    selected, selected_scene, selected_position, physical, inlet = loaded
                    if stop.is_set():
                        return loaded, controls, {}
                    changed = live.update(qc, selected, selected_scene, values, {}, position=selected_position)

                    def still_selected():
                        if (device.live_position(qc) != selected_position
                                or device.active_scene(qc) != selected_scene):
                            raise RuntimeError("The Quad Cortex preset or scene changed during Shape.")

                    # The output trim and the mute come first. A draft arrives on USB, and connecting it
                    # before they are set would play it at its own level for a moment, muted or not.
                    levels.bind(selected, selected_scene, physical)
                    levels.select(candidate, values, level_take, source=active_source)
                    levels.apply(qc, still_selected, events)
                    routes = input_routes(physical, inlet, active_source)
                    if resident is None or iterate.routes(selected) != routes:
                        set_input_routes(qc, selected_position, routes)
                    return loaded, controls, changed

                try:
                    selected, controls, changed = load(pick, target_values)
                except (RuntimeError, ValueError, OSError) as error:
                    close_tone()
                    if anchor is None:
                        raise
                    reason = f"Could not load {pick.get('name') or 'the selected tone'}. {error}"
                    pick, target_values = current, settings
                    selected, controls, changed = load(pick, target_values)
                if stop.is_set():
                    return
                base, scene, position, physical_routes, physical_input = selected
                available, previous = controls, changed
                if active_source == "guitar" and guitar_config and old_input != physical_input:
                    stream_stack.close()
                    observer, armed_until = attach_observer(qc, physical_input, guitar_config,
                                                           stream_stack, events, armed_until)
                settings, current = target_values, pick
                adjust_level()  # Bound to this tone in load, before it was connected.
                cursor.muted = False
                announce(reason, requested_revision if reason else None)

            stream, meters = change_input(config)
            checked, frames = time.monotonic(), -1
            while not stop.is_set():
                pending = drain(commands)
                if stop.is_set():
                    break
                if volume := pending.get("level"):
                    levels.configure(volume)
                    if active_source == "guitar":
                        level_take = volume.get("take", level_take)
                if pending_tone := pending.get("tone"):
                    retune(pending_tone["config"])
                    if stop.is_set():
                        break
                if source := pending.get("source"):
                    take = source.get("take") if source["mode"] == "phrase" else None
                    if (source["mode"], take) != (active_source, active_take) or source.get("reload"):
                        stream, meters = change_input(source)
                        frames = -1
                    else:
                        events({"event": "source", "mode": active_source})
                tone_id = current.get("tone_id")
                if knobs := pending.get(("knobs", tone_id)) or pending.get(("knobs", None)):
                    next_settings = dict(knobs["values"])
                    if next_settings != settings:
                        levels.clear_boost(qc, check_active)
                    previous = live.update(qc, base, scene, next_settings, previous, position=position)
                    if next_settings != settings:
                        events({"event": "shape", "tone_id": tone_id, "has_edits": bool(previous),
                                "reason": "This tone has no writable controls for those settings."
                                if any(next_settings.values()) and not previous else None})
                    settings = next_settings
                    adjust_level()
                elif volume:
                    adjust_level()
                now = time.monotonic()
                if observer is not None:
                    if observer.allow_changes and armed_until is not None and now >= armed_until:
                        observer.allow_changes = False
                        events({"event": "guitar-status", "text": "Gesture arm expired. Observing dry guitar input."})
                    proposed, outcome = observe_guitar(observer, now, available, settings, armed_until, events)
                    if proposed != settings:
                        previous = live.update(qc, base, scene, proposed, previous, position=position)
                        settings = proposed
                        adjust_level()
                    if outcome is not None:
                        outcome["tone_id"] = tone_id
                        events(outcome)
                if now - checked >= .8:
                    check_active()
                    if stream is not None and (not stream.active or frames == meters["frames"]):
                        raise RuntimeError("The Quad Cortex loop stopped responding.")
                    frames = meters.get("frames", -1)
                    if active_source == "phrase" and levels.observe(cursor, meters):
                        levels.apply(qc, check_active, events)
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
