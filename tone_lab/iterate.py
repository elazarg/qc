"""Render style changes on a temporary USB copy of the recorded tone."""

import json
from pathlib import Path
import time

import numpy as np
from qc_mcp import preset as wire

from . import audio, capture, device, models as catalog, presets, session, styles

SCRATCH_NAME = "Tone Lab Style"
SCRATCH = {"folder_key": "/media/p4/Presets/My Presets", "position": 254, "is_factory": False}
LIVE_B_NAME = "Tone Lab Live B"
LIVE_B = {"folder_key": "/media/p4/Presets/Tone Lab", "position": 255, "is_factory": False}
USB_ROUTES = [(12, 14), (0, 0), (0, 0), (0, 0)]


class FixtureUnavailable(RuntimeError):
    """The Lab could not confirm an unused or app-owned working slot."""


def routes(preset):
    return [(chain.in_portid, chain.out_portid) for chain in preset.chains]


def reamp_routes(source, physical_input, *, monitor=False):
    original = routes(source)
    if (len(original) != 4 or physical_input not in (1, 2)
            or any(in_port not in (0, physical_input) or out_port not in (0, 16, 17, 18, 19)
                   for in_port, out_port in original)
            or not any(in_port == physical_input for in_port, _ in original)
            or not any(out_port == 19 for _, out_port in original)):
        raise ValueError("This preset uses other inputs or outputs. Use a single guitar input and Multi Out for direct reamping.")
    return [(12 if in_port == physical_input else in_port,
             14 if out_port == 19 and not monitor else out_port) for in_port, out_port in original]


def sound_state(preset):
    grid = wire.describe(preset)
    return ([[chain[k] for k in ("models", "split_points", "splitter", "mixer", "combined_splitter")]
             for chain in grid["chains"]], grid["bypass"])


def recall(qc, position, name):
    qc.recall(**position)
    for _ in range(4):
        time.sleep(.25)
        if qc.get_setlist_position() == position:
            current = device.read_preset(qc)
            if name is None or current.name == name:
                return current
    raise RuntimeError(f"Could not confirm preset {name} at the expected slot.")


def fixture_identity(position):
    if position == SCRATCH:
        return SCRATCH_NAME, "My Presets 32G"
    if position == LIVE_B:
        return LIVE_B_NAME, "Tone Lab 32H"
    raise ValueError("Choose one of Tone Lab's verified working slots.")


def verify_fixture(qc, source, expected_routes=USB_ROUTES, *, position=SCRATCH):
    name, _ = fixture_identity(position)
    readback = device.read_preset(qc)
    if qc.get_setlist_position() != position or readback.name != name or routes(readback) != expected_routes:
        raise RuntimeError("Tone Lab could not verify the routing on its test preset.")
    for a, b in zip(readback.chains, source.chains):
        if ([m.hash for m in a.models] != [m.hash for m in b.models]
                or [(p.split, p.mix) for p in a.split_control_points] != [(p.split, p.mix) for p in b.split_control_points]):
            raise RuntimeError("The copied signal path did not match the source.")
    return readback


def set_routes(qc, expected):
    current = routes(device.read_preset(qc))
    for row, (before, after) in enumerate(zip(current, expected)):
        if before != after:
            qc.set_routing(row, *after)
            time.sleep(.10)
    time.sleep(.35)


def _confirm_empty_live_b(qc):
    # Recalling a genuinely vacant slot and a preset named "Unsaved" with no
    # blocks can return the same grid. Only a fresh directory can distinguish them.
    matches = [row for row in qc.list_directory().get("presets", [])
               if row.get("key") == LIVE_B["folder_key"] and not row.get("is_factory")]
    files = matches[0].get("files", []) if len(matches) == 1 else []
    if tuple(row.get("index") for row in files) != tuple(range(256)):
        raise FixtureUnavailable("The Quad Cortex did not confirm the Tone Lab setlist's working slot.")
    entry = files[LIVE_B["position"]]
    if entry.get("name") or entry.get("key") or entry.get("is_readonly"):
        raise FixtureUnavailable("Tone Lab 32H contains another preset. Tone Lab left it untouched.")


def _fixture_sound(preset):
    return sound_state(preset), preset.tempo, preset.volume, preset.pan


def prepare_fixture(qc, source_position, source, physical_input, *, position=SCRATCH, monitor_mode=None):
    """Stage a protected copy for offline reamp, guitar, or an audible USB phrase."""
    name, slot = fixture_identity(position)
    if monitor_mode not in (None, "guitar", "phrase"):
        raise ValueError("Choose Guitar or Phrase as the input.")
    expected = reamp_routes(source, physical_input, monitor=monitor_mode == "phrase")
    if monitor_mode == "guitar":
        expected = routes(source)
    if source_position in (SCRATCH, LIVE_B) or qc.get_setlist_position() != source_position:
        raise RuntimeError("The original preset is no longer selected.")
    presets.archive(source, source_position, qc.firmware, reason="Before Tone Lab reamp")
    try:
        fixture = recall(qc, position, None)
    except RuntimeError as error:
        if position == LIVE_B:
            raise FixtureUnavailable("The Quad Cortex could not recall the Tone Lab 32H working slot.") from error
        raise
    blank = (fixture.name.strip().lower() in ("", "unsaved")
             and not any(model.hash for chain in fixture.chains for model in chain.models))
    if not blank and fixture.name != name:
        raise FixtureUnavailable(f"{slot} contains another preset. Tone Lab left it untouched.")
    if blank and position == LIVE_B:
        _confirm_empty_live_b(qc)
    if not blank and _fixture_sound(fixture) == _fixture_sound(source) and routes(fixture) == expected:
        return fixture
    if not blank:
        presets.archive(fixture, position, qc.firmware, reason="Previous Tone Lab working copy")
    saved = recall(qc, source_position, source.name)
    if _fixture_sound(saved) != _fixture_sound(source) or qc.get_setlist_position() != source_position:
        raise RuntimeError("The recalled preset differs from the working version. Tone Lab kept a local snapshot and skipped the copy.")
    qc.write_preset_file(position["folder_key"], position["position"], name)
    time.sleep(.55)
    copy = recall(qc, position, name)
    if _fixture_sound(copy) != _fixture_sound(source):
        raise RuntimeError("The new test copy did not match the saved preset.")
    if routes(copy) == expected:
        return copy
    set_routes(qc, expected)
    verify_fixture(qc, source, expected, position=position)
    qc.write_preset_file(position["folder_key"], position["position"], name)
    time.sleep(.55)
    recall(qc, position, name)
    return verify_fixture(qc, source, expected, position=position)


def apply_changes(qc, source, changes, expected_routes=USB_ROUTES):
    initial = verify_fixture(qc, source, expected_routes)
    expected = []
    for edit in changes:
        r, c, i = edit["row"] - 1, edit["column"] - 1, edit["index"]
        model = initial.chains[r].models[c]
        raw = catalog.to_norm(model.hash, i, edit["to"])
        qc.set_param(r, c, i, raw)
        expected.append((r, c, i, raw, edit["name"]))
        time.sleep(.08)
    time.sleep(.35)
    actual = verify_fixture(qc, source, expected_routes)
    for r, c, i, raw, name in expected:
        setting = actual.chains[r].models[c].params[i]
        if not setting.param_values or abs(setting.param_values[0].float_value - raw) > .005:
            raise RuntimeError(f"The adjusted {name} did not read back correctly.")


def take_audio(take):
    info = json.loads((take / "clip.json").read_text(encoding="utf-8"))
    start, end = info.get("selected_passage_seconds", [0, info["duration_seconds"]])
    selection = slice(round(start * audio.RATE), round(end * audio.RATE))
    dry, wet = [audio.read_wave(take / filename)[selection].copy() for filename in ("dry.wav", "wet.wav")]
    if len(dry) != len(wet) or len(dry) < audio.RATE // 2 or dry.shape[1] != 1 or wet.shape[1] != 2:
        raise ValueError("Use a take with synchronized mono dry and stereo wet audio.")
    return info, (start, end), dry, wet


def calibration_offset(qc):
    calibration = json.loads((session.SESSIONS / "calibration.json").read_text(encoding="utf-8"))
    offset = calibration["reamp_offset_samples"]
    if calibration["rate"] != audio.RATE or not 0 <= offset < audio.RATE or qc.firmware != calibration["firmware"]:
        raise RuntimeError("Run the USB bypass calibration again before making aligned comparisons.")
    return offset


def render_knobs(take, values):
    """Save a level-matched comparison using the current preset and knob settings."""
    from . import live

    take = Path(take)
    info, selection, dry, wet = take_audio(take)
    with device.connect(write=True) as qc:
        device.require_clean(qc)
        offset = calibration_offset(qc)
        position = qc.get_setlist_position()
        source = device.read_preset(qc)
        scene = device.active_scene(qc)
        if not position or position == SCRATCH:
            raise ValueError("Select the saved preset you want to use as a starting point.")
        physical_input = info.get("physical_input", 1)
        try:
            base = prepare_fixture(qc, position, source, physical_input)
            if device.active_scene(qc) != scene:
                device.select_scene(qc, scene)
            edits = live.update(qc, base, scene, values, {})
            verify_fixture(qc, source, reamp_routes(source, physical_input))
            samples = audio.reamp(dry)[offset:offset + len(dry)].copy()
            if samples.shape != wet.shape:
                raise RuntimeError("The reamp return did not match this take's length.")
            measured = audio.describe(samples)
            if measured["clipped_samples"] or measured["lufs"] is None:
                raise RuntimeError("The saved tone clipped or returned silence.")
        finally:
            restored = recall(qc, position, source.name)
            if device.active_scene(qc) != scene:
                device.select_scene(qc, scene)
            if sound_state(restored) != sound_state(source):
                raise RuntimeError("The saved preset was recalled. Its starting working state is in the local preset history.")
    folder, data = session.new_session("knobs", info.get("guitar", ""), info.get("pickup_note", ""), info.get("song", ""))
    data.update(source_clip=take.name, selection_seconds=list(selection), original_preset=source.name,
                original_scene=device.SCENES[scene], knob_values=values)
    fade = np.linspace(0, 1, 480)[:, None]
    for track in (wet, samples):
        track[:480] *= fade
        track[-480:] *= fade[::-1]
    return session.make_shortlist(folder, data, [({"name": "Recorded wet baseline"}, wet),
                                                ({"name": source.name + " · saved controls", "changes": list(edits.values()),
                                                  "knobs": values}, samples)], wet)


def render(note, *, take=None, arm=False, progress=lambda message: None):
    style = styles.intent(note)
    if style is None:
        raise ValueError("Try a supported style request such as ‘more jazz’, ‘less delay’, or ‘darker’.")
    if not arm:
        raise ValueError("Arm USB rendering before applying a style.")
    if take is None:
        latest = capture.latest_clip(pair=True)
        if not latest.get("dry"):
            raise ValueError("The latest take does not have a verified dry track.")
        take = Path(latest["dry"]).parent
    take = Path(take)
    info, (start, end), dry, wet = take_audio(take)
    with device.connect(write=True) as qc:
        device.require_clean(qc)
        offset = calibration_offset(qc)
        position = qc.get_setlist_position()
        source = device.read_preset(qc)
        scene = device.active_scene(qc)
        if (not position or position == SCRATCH or source.name != info.get("wet_preset")
                or device.SCENES[scene] != info.get("wet_scene")
                or (info.get("wet_position") and position != info["wet_position"])):
            raise ValueError("Load the same preset and scene that produced this take’s wet track.")
        physical_input = info.get("physical_input", 1)
        expected_routes = reamp_routes(source, physical_input)
        renders = [({"name": "Recorded wet baseline"}, wet)]
        try:
            progress("Checking the preset copy against the wet take…")
            copy = prepare_fixture(qc, position, source, physical_input)
            if device.active_scene(qc) != scene:
                device.select_scene(qc, scene)
            baseline = audio.reamp(dry)[offset:offset + len(dry)]
            baseline_metrics = audio.describe(baseline)
            if baseline_metrics["clipped_samples"] or baseline_metrics["lufs"] is None:
                raise RuntimeError("The copied preset returned silence or clipped.")
            baseline_distance = audio.distance(baseline_metrics, audio.describe(wet))
            if baseline_distance > 4:
                raise RuntimeError(f"The copied preset is too far from the recorded baseline ({baseline_distance:.2f} dB band distance).")
            for amount in (.4, .85):
                _, changes = styles.apply(copy, scene, style, amount)
                progress(f"Rendering {style} at {amount:.0%}…")
                recall(qc, SCRATCH, SCRATCH_NAME)
                if device.active_scene(qc) != scene:
                    device.select_scene(qc, scene)
                apply_changes(qc, source, changes, expected_routes)
                samples = audio.reamp(dry)[offset:offset + len(dry)].copy()
                measures = audio.describe(samples)
                if measures["clipped_samples"] or measures["lufs"] is None:
                    raise RuntimeError("A style variation returned silence or clipped.")
                renders.append(({"name": style, "amount": amount, "changes": changes}, samples))
        finally:
            restored = recall(qc, position, source.name)
            if device.active_scene(qc) != scene:
                device.select_scene(qc, scene)
            if wire.describe(restored)["chains"] != wire.describe(source)["chains"]:
                raise RuntimeError("The original preset was recalled. Its working-grid readback differs from the snapshot.")
    folder, data = session.new_session("style", info.get("guitar", ""), info.get("pickup_note", ""),
                                      info.get("song", ""))
    data.update(source_clip=take.name, selection_seconds=[start, end], style=style,
                copied_baseline_distance=baseline_distance, original_preset=source.name,
                original_scene=device.SCENES[scene])
    audio.write_wave(folder / "copied-baseline.wav", baseline)
    fade = np.linspace(0, 1, 480)[:, None]
    for _, samples in renders:
        samples[:480] *= fade
        samples[-480:] *= fade[::-1]
    return session.make_shortlist(folder, data, renders, wet)
