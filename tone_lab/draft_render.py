"""Audition and keep validated drafts through the connected Quad Cortex."""

from contextlib import contextmanager
from pathlib import Path
from queue import Empty
import time

import numpy as np
from qc_mcp import catalog, protocol

from . import audio, device, iterate, preset_design, presets, session


WORK_SLOT = {**iterate.SCRATCH, "position": 255}
WORK_NAME = "Tone Lab Vai"
OUTPUT_GAIN = 16005
PREVIEW_GAIN = -12.


def my_presets(qc):
    folders = qc.list_directory().get("presets", [])
    folder = next((row for row in folders if row["key"] == WORK_SLOT["folder_key"]), None)
    if folder is None or folder["is_factory"] or len(folder["files"]) != 256:
        raise RuntimeError("The Quad Cortex did not return the full My Presets setlist.")
    return folder["files"]


@contextmanager
def working_grid(qc, visited=()):
    position = device.live_position(qc)
    dirty = qc.read_state("PresetDirty", timeout_ms=2000)
    if (not position or not position["folder_key"] or position == WORK_SLOT or dirty is None
            or dirty.action != protocol.ACTION["UPDATE"] or dirty.is_dirty):
        raise RuntimeError("Save your current Quad Cortex changes and select a regular preset before auditioning a draft.")
    source = device.read_preset(qc)
    scene = device.active_scene(qc)
    presets.archive(source, position, qc.firmware, reason="Before auditioning a draft")
    try:
        scratch = iterate.recall(qc, WORK_SLOT, None)
        blank = (scratch.name.strip().casefold() in ("", "unsaved")
                 and not any(model.hash for row in scratch.chains for model in row.models))
        if scratch.name != WORK_NAME and not blank:
            raise RuntimeError("My Presets 32H is in use. The draft needs the Tone Lab working slot.")
        yield scratch
    finally:
        if device.live_position(qc) not in (position, WORK_SLOT, *visited):
            raise RuntimeError("The selected preset changed on the Quad Cortex. Tone Lab released live control.")
        restored = iterate.recall(qc, position, source.name)
        if device.active_scene(qc) != scene:
            device.select_scene(qc, scene)
        if iterate.sound_state(restored) != iterate.sound_state(source) or iterate.routes(restored) != iterate.routes(source):
            raise RuntimeError("The Quad Cortex recalled its saved preset. The opening state is in the local preset history.")


def compile_chain(draft, gain):
    recipe = preset_design.validate(draft)
    models = preset_design.choices()
    chain = []
    for block in recipe["chain"]:
        model_id = block["model_id"]
        controls = models[model_id]["controls"]
        settings = [(controls[setting["name"]][0], setting["value"]) for setting in block["settings"]]
        chain.append((model_id, settings))
    chain.append((OUTPUT_GAIN, [(0, gain)]))
    return chain


def assert_grid(qc, chain, routing):
    grid = device.read_preset(qc)
    expected = [[model_id for model_id, _ in chain], [], [], []]
    actual = [[model.hash for model in row.models if model.hash] for row in grid.chains]
    if actual != expected or iterate.routes(grid) != routing:
        raise RuntimeError("The Quad Cortex did not confirm the draft blocks and routing.")
    for column, (model_id, settings) in enumerate(chain):
        params = grid.chains[0].models[column].params
        for index, display in settings:
            raw = catalog.to_norm(model_id, index, display)
            values = params[index].param_values
            if not values or abs(values[0].float_value - raw) > .006:
                raise RuntimeError(f"The Quad Cortex did not confirm a setting on {catalog.lookup(model_id)['name']}.")
    return grid


def build_grid(qc, scratch, chain):
    if device.live_position(qc) != WORK_SLOT:
        raise RuntimeError("The Quad Cortex changed before the draft could be built.")
    iterate.set_routes(qc, iterate.USB_ROUTES)
    for row, lane in enumerate(scratch.chains):
        for column, model in enumerate(lane.models):
            if model.hash:
                qc.delete_block(row, column)
                time.sleep(.07)
        if any((item.split, item.mix) != (-1, -1) for item in lane.split_control_points):
            qc.set_split_points(row, -1, -1)
            time.sleep(.10)
    time.sleep(.25)
    cleared = device.read_preset(qc)
    if any(model.hash for row in cleared.chains for model in row.models):
        raise RuntimeError("The temporary Quad Cortex grid did not clear.")
    for column, (model_id, settings) in enumerate(chain):
        if device.live_position(qc) != WORK_SLOT:
            raise RuntimeError("The selected preset changed while the draft was being built.")
        qc.add_block(model_id, row=0, column=column)
        time.sleep(.10)
        for index, value in settings:
            qc.set_param(0, column, index, catalog.to_norm(model_id, index, value))
            time.sleep(.07)
    time.sleep(.40)
    assert_grid(qc, chain, iterate.USB_ROUTES)


def guitar_routes(physical_input):
    if physical_input not in (1, 2):
        raise ValueError("Use guitar input 1 or 2 on the Quad Cortex.")
    return [(physical_input, 19), (0, 0), (0, 0), (0, 0)]


def route_to_guitar(qc, chain, physical_input):
    if device.live_position(qc) != WORK_SLOT:
        raise RuntimeError("The working draft is no longer selected.")
    routing = guitar_routes(physical_input)
    # Disconnect the input before switching the destination to the analog outputs.
    qc.set_routing(0, 0, 19)
    time.sleep(.15)
    qc.set_routing(0, *routing[0])
    time.sleep(.25)
    return assert_grid(qc, chain, routing)


def try_guitar(draft, physical_input, gain, stop, commands, report):
    """Monitor the draft on the hardware until Stop live is pressed."""
    from . import live, styles

    if stop.is_set():
        return
    chain = compile_chain(draft, min(PREVIEW_GAIN if gain is None else gain, -6.0))
    with device.connect(write=True) as qc:
        with working_grid(qc) as scratch:
            build_grid(qc, scratch, chain)
            if stop.is_set():
                return
            base = route_to_guitar(qc, chain, physical_input)
            scene = device.active_scene(qc)
            report({"event": "controls", "available": styles.available_knobs(base, scene)})
            report(f"Playing draft · {draft['name']}")
            previous = {}
            checked = time.monotonic()
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
                    previous = live.update(qc, base, scene, values, previous, position=WORK_SLOT)
                now = time.monotonic()
                if now - checked >= .8:
                    if device.live_position(qc) != WORK_SLOT:
                        raise RuntimeError("The selected preset changed on the Quad Cortex.")
                    checked = now


def hear(draft, take):
    take = Path(take)
    info, selection, dry, wet = iterate.take_audio(take)
    target_level = audio.describe(wet)["lufs"]
    if target_level is None:
        raise ValueError("The recorded preset is silent. Record a take with dry and wet sound.")
    gain = PREVIEW_GAIN
    chain = compile_chain(draft, gain)
    with device.connect(write=True) as qc:
        offset = iterate.calibration_offset(qc)
        with working_grid(qc) as scratch:
            build_grid(qc, scratch, chain)
            samples = audio.reamp(dry)[offset:offset + len(dry)].copy()
            if samples.shape != wet.shape:
                raise RuntimeError("The Quad Cortex return did not match the performance length.")
            measured = audio.describe(samples)
            if measured["lufs"] is None:
                raise RuntimeError("The draft returned silence. Try a new take or another draft.")
            # This adjusts final output only. It keeps the amp and effects drive unchanged.
            trim = min(float(np.clip(target_level, -36, -18)) - measured["lufs"],
                       -3 - measured["peak_dbfs"])
            gain = round(float(np.clip(PREVIEW_GAIN + trim, -30, 0)), 2)
            scaled = samples * 10 ** ((gain - PREVIEW_GAIN) / 20)
            if measured["clipped_samples"] or abs(gain - PREVIEW_GAIN) >= .1:
                control = catalog.to_norm(OUTPUT_GAIN, 0, gain)
                qc.set_param(0, len(chain) - 1, 0, control)
                time.sleep(.25)
                assert_grid(qc, compile_chain(draft, gain), iterate.USB_ROUTES)
                scaled = audio.reamp(dry)[offset:offset + len(dry)].copy()
            metrics = audio.describe(scaled)
            if scaled.shape != wet.shape or metrics["lufs"] is None or metrics["clipped_samples"]:
                raise RuntimeError("The draft output clipped, went silent, or lost audio. The current preset was restored.")
    folder, manifest = session.new_session("draft", info.get("guitar", ""),
                                           info.get("pickup_note", ""), draft["target"])
    manifest.update(draft_id=draft["id"], source_clip=take.name, selection_seconds=list(selection),
                    save_input=info.get("physical_input", 1), output_gain_db=gain,
                    original_preset=info.get("wet_preset"),
                    original_scene=info.get("wet_scene"))
    fade = np.linspace(0, 1, 480)[:, None]
    for track in (wet, scaled):
        track[:480] *= fade
        track[-480:] *= fade[::-1]
    return session.make_shortlist(folder, manifest, [
        ({"name": "Recorded wet baseline", "audition_role": "original"}, wet),
        ({"name": draft["name"], "audition_role": "draft"}, scaled),
    ], wet)


def new_slot(files, name):
    occupied = {row["name"].casefold() for row in files if row["name"]}
    used = [row["index"] for row in files[:-2] if row["name"] or row["key"]]
    free = [row["index"] for row in files[:-2] if not row["name"] and not row["key"]]
    if not free:
        raise RuntimeError("My Presets has no empty slots. Free a slot before keeping this draft.")
    slot = next((i for i in free if i > max(used, default=-1)), free[0])
    candidate = name
    for copy in range(2, 257):
        if candidate.casefold() not in occupied:
            return slot, candidate
        suffix = f" {copy}"
        candidate = name[:24 - len(suffix)].rstrip() + suffix
    raise RuntimeError("Choose a different name for this preset.")


def save(draft, proof=None, *, physical_input=1):
    gain = PREVIEW_GAIN
    if proof is not None:
        manifest = session.load_session(proof)
        if manifest.get("draft_id") != draft["id"] or manifest.get("state") != "ready":
            raise ValueError("This audition belongs to a different preset draft.")
        gain, physical_input = manifest["output_gain_db"], manifest["save_input"]
    if (type(gain) not in (int, float) or not -30 <= gain <= 0 or physical_input not in (1, 2)):
        raise ValueError("The draft audition is missing verified routing and gain.")
    chain = compile_chain(draft, gain)
    with device.connect(write=True) as qc:
        visited = []
        with working_grid(qc, visited) as scratch:
            build_grid(qc, scratch, chain)
            index, name = new_slot(my_presets(qc), preset_design.preset_name(draft["name"]))
            route_to_guitar(qc, chain, physical_input)
            address = {**WORK_SLOT, "position": index}
            qc.write_preset_file(address["folder_key"], index, name)
            time.sleep(.65)
            visited.append(address)
            iterate.recall(qc, address, name)
            verified = assert_grid(qc, chain, guitar_routes(physical_input))
            presets.archive(verified, address, qc.firmware, reason="Kept AI draft in a new Quad Cortex slot")
    label = f"{name} · My Presets {index // 8 + 1}{'ABCDEFGH'[index % 8]}"
    session.write_json(session.SESSIONS / "drafts" / f"{draft['id']}.kept.json", {"label": label})
    return label
