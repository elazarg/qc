"""Audition and keep validated drafts through the connected Quad Cortex."""

from contextlib import contextmanager
import math
from pathlib import Path
from queue import Empty
import time

import numpy as np
from qc_mcp import protocol

from . import audio, device, diagnostics, iterate, measured, models as catalog, preset_design, presets, qc_library, session, styles


WORK_SLOT = {**iterate.SCRATCH, "position": 255}
WORK_NAME = "Tone Lab Preview"
LEGACY_WORK_NAME = "Tone Lab Vai"
OUTPUT_GAIN = 16005
PREVIEW_GAIN = -12.
ASSET_LOADERS = {29001, 14000, 14001}
MIXER = 11000
MIXER_PAN_A, MIXER_LEVEL_B, MIXER_PAN_B, MIXER_PHASE = 1, 2, 3, 4


class Chain(list):
    """The first row of the grid by column, as (model id, settings). An empty cell has model id 0.

    A draft with a second path or with scenes carries them here as well, so code
    that knows a single row of blocks keeps working on it.
    """

    side = ()  # The second row by column, in the same form.
    split = None  # (column of the split, column of the mix)
    mixer = ()  # [(parameter index, display value)]
    scenes = ()  # Scene names. Empty for a draft that is one sound.
    varied = {}  # (row, column, parameter index) -> {scene: display value}
    off = {}  # (row, column) -> the scenes in which the block is bypassed
    assets = None  # column -> asset on the first row


def rows_of(chain):
    return (chain, getattr(chain, "side", ()))


def my_presets(qc):
    folders = qc.list_directory().get("presets", [])
    folder = next((row for row in folders if row["key"] == WORK_SLOT["folder_key"]), None)
    if folder is None or folder["is_factory"] or len(folder["files"]) != 256:
        raise RuntimeError("The Quad Cortex did not return the full My Presets setlist.")
    return folder["files"]


def name_preview_slot(qc, scratch):
    """Replace the early app-owned fixture name once, with its grid intact."""
    if scratch.name != LEGACY_WORK_NAME:
        return scratch
    if device.live_position(qc) != WORK_SLOT:
        raise RuntimeError("The Quad Cortex changed before naming the preview slot.")
    directory_slot = my_presets(qc)[WORK_SLOT["position"]]
    if directory_slot.get("name") != LEGACY_WORK_NAME or directory_slot.get("is_readonly"):
        raise RuntimeError("Couldn't verify the old Tone Lab preview slot in My Presets 32H.")
    presets.archive(scratch, WORK_SLOT, qc.firmware, reason="Before naming the Tone Lab preview slot")
    qc.write_preset_file(WORK_SLOT["folder_key"], WORK_SLOT["position"], WORK_NAME)
    time.sleep(.55)
    renamed = iterate.recall(qc, WORK_SLOT, WORK_NAME)
    if iterate.sound_state(scratch) != iterate.sound_state(renamed) or iterate.routes(scratch) != iterate.routes(renamed):
        raise RuntimeError("The Quad Cortex could not verify the renamed preview slot.")
    return renamed


def label_preview(qc, scene, draft):
    """Put the logical draft name on the active temporary scene. A draft with scenes has its own labels."""
    if draft.get("scenes"):
        return
    if device.live_position(qc) == WORK_SLOT:
        try:
            qc.set_scene_label(scene, preset_design.preset_name(draft["name"]))
        except (OSError, RuntimeError):
            diagnostics.LOG.warning("The Quad Cortex could not label the preview scene", exc_info=True)


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
        if scratch.name not in (WORK_NAME, LEGACY_WORK_NAME) and not blank:
            raise RuntimeError("My Presets 32H is in use. The draft needs the Tone Lab working slot.")
        yield name_preview_slot(qc, scratch)
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
    (upper, lower), split = preset_design.layout(recipe)
    chain = Chain()
    chain.varied, chain.off, chain.assets = {}, {}, {}

    def cells(row, blocks):
        placed = []
        for column, block in enumerate(blocks):
            if block is None:
                placed.append((0, []))
                continue
            model_id = block["model_id"]
            controls = models[model_id]["controls"] if model_id in models else {}
            twins = models[model_id]["twins"] if model_id in models else {}
            settings = [(index, setting["value"]) for setting in block["settings"]
                        for index in (controls[setting["name"]][0], *twins.get(setting["name"], ()))]
            for item in block.get("scene_settings", ()):
                for index in (controls[item["name"]][0], *twins.get(item["name"], ())):
                    chain.varied.setdefault((row, column, index), {})[item["scene"]] = item["value"]
            if block.get("off_in_scenes"):
                chain.off[row, column] = frozenset(block["off_in_scenes"])
            if asset := preset_design.asset_attachment(block):
                chain.assets[column] = asset
            placed.append((model_id, settings))
        return placed

    chain.extend(cells(0, upper))
    chain.append((OUTPUT_GAIN, [(0, gain)]))
    chain.side, chain.split = cells(1, lower), split
    if split:
        blend = recipe["blend"]
        # Two amps of opposite polarity cancel each other when blended, so the mixer turns one path over.
        paths = [[block["model_id"] for block in row[split[0]:split[1]] if block] for row in (upper, lower)]
        chain.mixer = [(MIXER_PAN_A, 5 - blend["spread"] / 20), (MIXER_LEVEL_B, blend["level"]),
                       (MIXER_PAN_B, 5 + blend["spread"] / 20), (MIXER_PHASE, float(measured.opposed(*paths)))]
    chain.scenes = tuple(recipe.get("scenes", ()))
    return chain


def scene_value(chain, row, column, index, scene):
    """The display value a scene-varying control has in one scene."""
    per_scene = chain.varied[row, column, index]
    if scene in per_scene:
        return per_scene[scene]
    model_id, settings = rows_of(chain)[row][column]
    return dict(settings).get(index, float(catalog.lookup(model_id)["params"][index]["default"]))


def _attachments(chain, draft):
    if getattr(chain, "assets", None) is not None:
        return chain.assets
    needed = {column for column, (model, _) in enumerate(chain) if model in ASSET_LOADERS}
    if not needed:
        return {}
    recipe = preset_design.validate(draft)
    assets = {column: asset for column, block in enumerate(recipe["chain"])
              if (asset := preset_design.asset_attachment(block))}
    if needed != assets.keys() or any(chain[column][0] != asset.model_id for column, asset in assets.items()):
        raise ValueError("The draft's asset bindings no longer match its working chain.")
    return assets


def _confirm_assets(qc, assets):
    if not assets:
        return
    library = qc_library.from_catalog(qc.list_directory())
    available = {(item.kind, item.key): item for item in (*library.irs, *library.captures)}
    for asset in assets.values():
        found = available.get((asset.kind, asset.key))
        if found is None:
            raise RuntimeError(f"{asset.name} is missing from this Quad Cortex. Restore the asset and try again.")
        if found.capture_version is not None and found.capture_version != asset.capture_version:
            raise RuntimeError(f"The Quad Cortex reports a different capture version for {asset.name}.")


def assert_grid(qc, chain, routing, draft=None, *, edits=None, scene=0):
    assets = _attachments(chain, draft)
    grid = device.read_preset(qc)
    rows = rows_of(chain)
    expected = [[model_id for model_id, _ in cells if model_id] for cells in rows] + [[], []]
    actual = [[model.hash for model in row.models if model.hash] for row in grid.chains]
    if actual != expected or iterate.routes(grid) != routing:
        raise RuntimeError("The Quad Cortex did not confirm the draft blocks and routing.")
    split = getattr(chain, "split", None)
    if split and [(point.split, point.mix) for point in grid.chains[0].split_control_points] != [tuple(split)]:
        raise RuntimeError("The Quad Cortex did not confirm where the draft's two paths part and join.")
    edits = edits or {}
    varied, off = getattr(chain, "varied", {}), getattr(chain, "off", {})
    for row, cells in enumerate(rows):
        for column, (model_id, settings) in enumerate(cells):
            if not model_id:
                continue
            block = grid.chains[row].models[column]
            if row == 0 and column in assets and not qc_library.asset_matches(block, assets[column]):
                raise RuntimeError(f"The Quad Cortex did not confirm the asset binding for {assets[column].name}.")
            expected_values = dict(settings)
            expected_values.update({index: scene_value(chain, row, column, index, scene)
                                    for (at_row, at_column, index) in varied if (at_row, at_column) == (row, column)})
            expected_values.update({index: edit["to"] for (at_row, at_column, index), edit in edits.items()
                                    if (at_row, at_column) == (row, column) and index is not None})
            for index, display in expected_values.items():
                raw = catalog.to_norm(model_id, index, display)
                parameter = block.params[index]
                selected = scene if parameter.scene_mode else 0
                values = parameter.param_values
                if (selected >= len(values) or abs(values[selected].float_value - raw) > .006
                        or (row, column, index) in varied and not parameter.scene_mode):
                    raise RuntimeError(
                        f"The Quad Cortex did not confirm a setting on {catalog.lookup(model_id)['name']}.")
            wanted = scene in off[row, column] if (row, column) in off else None
            if (change := edits.get((row, column, None))) is not None:
                wanted = change["to"]
            if wanted is not None:
                bypass = grid.bypass[row].colBypass[column]
                selected = scene if bypass.sceneMode else 0
                if selected >= len(bypass.sceneBypass) or bypass.sceneBypass[selected].bypass != wanted:
                    raise RuntimeError("The Quad Cortex did not confirm which blocks are on in this scene.")
    return grid


def still_working(qc, doing="the draft was being built"):
    """Stop before a write when the player has selected another preset, which the write would land on.

    Asking the unit takes about a tenth of a second, so one check guards each
    block, the split, the mixer and each scene, and not every single write.
    """
    if device.live_position(qc) != WORK_SLOT:
        raise RuntimeError(f"The selected preset changed while {doing}.")


def _build_scenes(qc, chain):
    """Give each scene its own values, bypasses and name. Ends on the first scene."""
    names = getattr(chain, "scenes", ())
    if not names:
        return
    doing = "the draft's scenes were being built"
    grid = device.read_preset(qc)
    still_working(qc, doing)
    for row, column, index in chain.varied:
        qc.assign_param_to_scenes(row, column, index)
        time.sleep(.05)
    for row, column in chain.off:
        # A block's bypass is the control one past its last readable one.
        qc.assign_param_to_scenes(row, column, len(grid.chains[row].models[column].params))
        time.sleep(.05)
    for scene, name in enumerate(names):
        # A plain write lands on whichever scene is active, so each switch is confirmed first.
        still_working(qc, doing)
        device.select_scene(qc, scene)
        still_working(qc, doing)
        for (row, column, index), per_scene in chain.varied.items():
            if scene in per_scene:
                model_id = rows_of(chain)[row][column][0]
                qc.set_param(row, column, index, catalog.to_norm(model_id, index, per_scene[scene]))
                time.sleep(.07)
        for (row, column), scenes in chain.off.items():
            if scene in scenes:
                qc.set_block_bypass(row, column, True)
                time.sleep(.07)
        qc.set_scene_label(scene, name)
        time.sleep(.07)
    still_working(qc, doing)
    device.select_scene(qc, 0)
    time.sleep(.3)
    grid = device.read_preset(qc)
    if list(grid.scene_labels)[:len(names)] != list(names):
        raise RuntimeError("The Quad Cortex did not confirm the draft's scene names.")
    for (row, column, index) in chain.varied:
        parameter = grid.chains[row].models[column].params[index]
        model_id = rows_of(chain)[row][column][0]
        for scene in range(len(names)):
            raw = catalog.to_norm(model_id, index, scene_value(chain, row, column, index, scene))
            if (not parameter.scene_mode or scene >= len(parameter.param_values)
                    or abs(parameter.param_values[scene].float_value - raw) > .006):
                raise RuntimeError(f"The Quad Cortex did not confirm scene {device.SCENES[scene]} on "
                                   f"{catalog.lookup(model_id)['name']}.")
    for (row, column), scenes in chain.off.items():
        bypass = grid.bypass[row].colBypass[column]
        flags = [entry.bypass for entry in bypass.sceneBypass][:len(names)]
        if not bypass.sceneMode or flags != [scene in scenes for scene in range(len(names))]:
            raise RuntimeError("The Quad Cortex did not confirm which blocks are on in each scene.")


def build_grid(qc, scratch, chain, draft=None):
    assets = _attachments(chain, draft)
    _confirm_assets(qc, assets)
    if device.live_position(qc) != WORK_SLOT:
        raise RuntimeError("The Quad Cortex changed before the draft could be built.")
    iterate.set_routes(qc, iterate.USB_ROUTES)
    for row, lane in enumerate(scratch.chains):
        parted = any((item.split, item.mix) != (-1, -1) for item in lane.split_control_points)
        if parted or any(model.hash for model in lane.models):
            still_working(qc)
        for column, model in enumerate(lane.models):
            if model.hash:
                qc.delete_block(row, column)
                time.sleep(.07)
        if parted:
            qc.set_split_points(row, -1, -1)
            time.sleep(.10)
    time.sleep(.25)
    cleared = device.read_preset(qc)
    if any(model.hash for row in cleared.chains for model in row.models):
        raise RuntimeError("The temporary Quad Cortex grid did not clear.")
    split = getattr(chain, "split", None)
    for row, cells in enumerate(rows_of(chain)):
        if row == 1 and split:
            # The second row carries sound only between the split and the mix, so they are set before it is filled.
            still_working(qc)
            qc.set_split_points(0, *split)
            time.sleep(.25)
        for column, (model_id, settings) in enumerate(cells):
            if not model_id:
                continue
            still_working(qc)
            if row == 0 and column in assets:
                qc_library.add_asset(qc, assets[column], 0, column)
            else:
                qc.add_block(model_id, row=row, column=column)
            time.sleep(.10)
            for index, value in settings:
                qc.set_param(row, column, index, catalog.to_norm(model_id, index, value))
                time.sleep(.07)
    if getattr(chain, "mixer", ()):
        still_working(qc)
    for index, value in getattr(chain, "mixer", ()):
        qc.set_lane_param(0, "mixer", index, catalog.to_norm(MIXER, index, value))
        time.sleep(.07)
    time.sleep(.40)
    placed = device.read_preset(qc)
    for row, cells in enumerate(rows_of(chain)):
        for column, (model_id, _) in enumerate(cells):
            if model_id and placed.chains[row].models[column].hash != model_id:
                # The unit refuses a block silently when its processors or delay memory are full.
                raise RuntimeError(f"The Quad Cortex has no processing power left for "
                                   f"{catalog.lookup(model_id)['name']}. Remove a block or choose a lighter one.")
    _build_scenes(qc, chain)
    grid = assert_grid(qc, chain, iterate.USB_ROUTES, draft)
    mixer = grid.chains[0].mixer[0].params if split and grid.chains[0].mixer else ()
    for index, value in getattr(chain, "mixer", ()):
        if index >= len(mixer) or abs(mixer[index].param_values[0].float_value - catalog.to_norm(MIXER, index, value)) > .006:
            raise RuntimeError("The Quad Cortex did not confirm the blend of the draft's two paths.")
    return grid


def guitar_routes(physical_input):
    if physical_input not in (1, 2):
        raise ValueError("Use guitar input 1 or 2 on the Quad Cortex.")
    return [(physical_input, 19), (0, 0), (0, 0), (0, 0)]


def route_to_guitar(qc, chain, physical_input, draft=None):
    if device.live_position(qc) != WORK_SLOT:
        raise RuntimeError("The working draft is no longer selected.")
    routing = guitar_routes(physical_input)
    # Disconnect the input before switching the destination to the analog outputs.
    qc.set_routing(0, 0, 19)
    time.sleep(.15)
    qc.set_routing(0, *routing[0])
    time.sleep(.25)
    return assert_grid(qc, chain, routing, draft)


def try_guitar(draft, physical_input, gain, stop, commands, report):
    """Monitor the draft on the hardware until Stop live is pressed."""
    from . import live

    if stop.is_set():
        return
    chain = compile_chain(draft, min(PREVIEW_GAIN if gain is None else gain, -6.0))
    with device.connect(write=True) as qc:
        with working_grid(qc) as scratch:
            build_grid(qc, scratch, chain, draft)
            if stop.is_set():
                return
            base = route_to_guitar(qc, chain, physical_input, draft)
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


def clean_values(values):
    clean = {}
    for name, value in (values or {}).items():
        if (name not in styles.KNOBS or type(value) not in (int, float) or not math.isfinite(value)
                or not styles.KNOBS[name][1] <= value <= 1):
            raise ValueError(f"Invalid tone direction setting for {name}.")
        if value:
            clean[name] = float(value)
    return clean


def _shape(qc, base, values, scene=None):
    from . import live

    current = device.active_scene(qc)
    scene = current if scene is None else scene
    if scene != current:
        device.select_scene(qc, scene)
    return live.update(qc, base, scene, values, {}, position=WORK_SLOT) if values else {}, scene


def hear(draft, take, *, values=None, scene=None):
    take = Path(take)
    values = clean_values(values)
    info, selection, dry, wet = iterate.take_audio(take)
    target_level = audio.describe(wet)["lufs"]
    if target_level is None:
        raise ValueError("The recorded preset is silent. Record a take with dry and wet sound.")
    gain = PREVIEW_GAIN
    chain = compile_chain(draft, gain)
    with device.connect(write=True) as qc:
        offset = iterate.calibration_offset(qc)
        with working_grid(qc) as scratch:
            base = build_grid(qc, scratch, chain, draft)
            edits, scene = _shape(qc, base, values, scene)
            if edits or scene:
                assert_grid(qc, chain, iterate.USB_ROUTES, draft, edits=edits, scene=scene)
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
                assert_grid(qc, compile_chain(draft, gain), iterate.USB_ROUTES, draft, edits=edits, scene=scene)
                scaled = audio.reamp(dry)[offset:offset + len(dry)].copy()
            metrics = audio.describe(scaled)
            if scaled.shape != wet.shape or metrics["lufs"] is None or metrics["clipped_samples"]:
                raise RuntimeError("The draft output clipped, went silent, or lost audio. The current preset was restored.")
    folder, manifest = session.new_session("draft", info.get("guitar", ""),
                                           info.get("pickup_note", ""), draft["target"])
    manifest.update(draft_id=draft["id"], source_clip=take.name, selection_seconds=list(selection),
                    save_input=info.get("physical_input", 1), output_gain_db=gain,
                    original_preset=info.get("wet_preset"),
                    original_scene=info.get("wet_scene"), draft_scene=device.SCENES[scene], knob_values=values)
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


def assert_scenes(qc, chain, routing, draft=None, *, edits=None, scene=0):
    """Confirm every scene of a draft, with the shape applied on `scene`.

    A shape edit to a control that differs by scene belongs to that scene
    alone. An edit to any other control is the same in all of them.
    """
    grid = assert_grid(qc, chain, routing, draft, edits=edits, scene=scene)
    varied, off = getattr(chain, "varied", {}), getattr(chain, "off", {})
    shared = {key: edit for key, edit in (edits or {}).items()
              if key not in varied and not (key[2] is None and key[:2] in off)}
    for other in range(len(getattr(chain, "scenes", ()))):
        if other != scene:
            assert_grid(qc, chain, routing, draft, edits=shared, scene=other)
    return grid


def save(draft, proof=None, *, physical_input=1, allow_my_presets=False, values=None, scene=None):
    """Write a draft to a free slot. `scene` is the one the shape belongs to and the preset opens on.

    Without it, the scene of a matching audition is used, and failing that the first.
    """
    if scene is not None and (type(scene) is not int or not 0 <= scene < max(len(draft.get("scenes") or ()), 1)):
        raise ValueError("The draft has no such scene.")
    gain = PREVIEW_GAIN
    proof_values = {}
    proof_scene = None
    if proof is not None:
        manifest = session.load_session(proof)
        if manifest.get("draft_id") != draft["id"] or manifest.get("state") != "ready":
            raise ValueError("This audition belongs to a different preset draft.")
        proof_values = clean_values(manifest.get("knob_values"))
        if values is None or clean_values(values) == proof_values:
            gain, physical_input = manifest["output_gain_db"], manifest["save_input"]
            recorded_scene = manifest.get("draft_scene")
            if isinstance(recorded_scene, str) and len(recorded_scene) == 1 and recorded_scene in device.SCENES:
                proof_scene = device.SCENES.index(recorded_scene)
    values = proof_values if values is None else clean_values(values)
    if (type(gain) not in (int, float) or not -30 <= gain <= 0 or physical_input not in (1, 2)):
        raise ValueError("The draft audition is missing verified routing and gain.")
    chain = compile_chain(draft, gain)
    with device.connect(write=True) as qc:
        visited = []
        name = preset_design.preset_name(draft["name"])
        qc_library.destination(qc, name, allow_my_presets=allow_my_presets)
        with working_grid(qc, visited) as scratch:
            build_grid(qc, scratch, chain, draft)
            base = route_to_guitar(qc, chain, physical_input, draft)
            edits, scene = _shape(qc, base, values, proof_scene if scene is None else scene)
            if edits or scene:
                assert_scenes(qc, chain, guitar_routes(physical_input), draft, edits=edits, scene=scene)
            saved = qc_library.destination(qc, name, allow_my_presets=allow_my_presets)
            address = saved.position
            if device.live_position(qc) != WORK_SLOT:
                raise RuntimeError("The working draft is no longer selected. No preset was saved.")
            qc.write_preset_file(address["folder_key"], address["position"], saved.name)
            time.sleep(.65)
            visited.append(address)
            iterate.recall(qc, address, saved.name)
            if device.active_scene(qc) != scene:
                device.select_scene(qc, scene)
            verified = assert_scenes(qc, chain, guitar_routes(physical_input), draft, edits=edits, scene=scene)
            presets.archive(verified, address, qc.firmware, reason="Kept AI draft in a new Quad Cortex slot")
    return qc_library.store_receipt(draft["id"], saved, qc_library.preset_fingerprint(verified),
                                    scene=device.SCENES[scene], values=values)
