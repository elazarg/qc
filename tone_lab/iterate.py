"""Render style changes on a temporary USB copy of the recorded tone."""

import json
from pathlib import Path
import time

import numpy as np
from qc_mcp import catalog, preset as wire

from . import audio, capture, device, session, styles

SCRATCH_NAME = "Tone Lab Style"
SCRATCH = {"folder_key": "/media/p4/Presets/My Presets", "position": 254, "is_factory": False}
USB_ROUTES = [(12, 14), (0, 0), (0, 0), (0, 0)]


def routes(preset):
    return [(chain.in_portid, chain.out_portid) for chain in preset.chains]


def recall(qc, position, name):
    qc.recall(**position)
    for _ in range(4):
        time.sleep(.25)
        if qc.get_setlist_position() == position:
            current = device.read_preset(qc)
            if current.name == name:
                return current
    raise RuntimeError(f"Could not confirm preset {name} at the expected slot.")


def verify_fixture(qc, source, expected_routes=USB_ROUTES):
    readback = device.read_preset(qc)
    if qc.get_setlist_position() != SCRATCH or readback.name != SCRATCH_NAME or routes(readback) != expected_routes:
        raise RuntimeError("Tone Lab could not verify the routing on its test preset.")
    for a, b in zip(readback.chains, source.chains):
        if ([m.hash for m in a.models] != [m.hash for m in b.models]
                or [(p.split, p.mix) for p in a.split_control_points] != [(p.split, p.mix) for p in b.split_control_points]):
            raise RuntimeError("The copied signal path did not match the source.")
    return readback


def apply_changes(qc, source, changes):
    initial = verify_fixture(qc, source)
    expected = []
    for edit in changes:
        r, c, i = edit["row"] - 1, edit["column"] - 1, edit["index"]
        model = initial.chains[r].models[c]
        raw = catalog.to_norm(model.hash, i, edit["to"])
        qc.set_param(r, c, i, raw)
        expected.append((r, c, i, raw, edit["name"]))
        time.sleep(.08)
    time.sleep(.35)
    actual = verify_fixture(qc, source)
    for r, c, i, raw, name in expected:
        setting = actual.chains[r].models[c].params[i]
        if not setting.param_values or abs(setting.param_values[0].float_value - raw) > .005:
            raise RuntimeError(f"The adjusted {name} did not read back correctly.")


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
    info = json.loads((take / "clip.json").read_text(encoding="utf-8"))
    start, end = info.get("selected_passage_seconds", [0, info["duration_seconds"]])
    selection = slice(round(start * audio.RATE), round(end * audio.RATE))
    dry, wet = [audio.read_wave(take / filename)[selection].copy() for filename in ("dry.wav", "wet.wav")]
    if len(dry) != len(wet) or len(dry) < audio.RATE // 2 or dry.shape[1] != 1 or wet.shape[1] != 2:
        raise ValueError("Use a take with synchronized mono dry and stereo wet audio.")
    calibration = json.loads((session.SESSIONS / "calibration.json").read_text(encoding="utf-8"))
    offset = calibration["reamp_offset_samples"]
    if calibration["rate"] != audio.RATE or not 0 <= offset < audio.RATE:
        raise RuntimeError("Run the USB bypass calibration first.")
    with device.connect(write=True) as qc:
        if qc.firmware != calibration["firmware"]:
            raise RuntimeError("The Quad Cortex firmware changed. Run the USB bypass calibration again.")
        position = qc.get_setlist_position()
        source = device.read_preset(qc)
        scene = device.active_scene(qc)
        if (not position or position == SCRATCH or source.name != info.get("wet_preset")
                or device.SCENES[scene] != info.get("wet_scene")
                or (info.get("wet_position") and position != info["wet_position"])):
            raise ValueError("Load the same preset and scene that produced this take’s wet track.")
        if routes(source) != [(info.get("physical_input", 1), 19), (0, 0), (0, 0), (0, 0)]:
            raise ValueError("This tone uses routing the style renderer cannot yet copy safely.")
        renders = [({"name": "Recorded wet baseline"}, wet)]
        try:
            recall(qc, SCRATCH, SCRATCH_NAME)
            progress("Checking the preset copy against the wet take…")
            copy = verify_fixture(qc, source)
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
                apply_changes(qc, source, changes)
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
