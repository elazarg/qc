"""Browse saved Quad Cortex setlists and capture comparable reference audio."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

import numpy as np
from qc_mcp import protocol, preset as wire

from . import audio, device, iterate, session


@dataclass(frozen=True)
class SavedPreset:
    name: str
    setlist: str
    folder_key: str
    index: int

    @property
    def slot(self):
        bank, switch = divmod(self.index, len(device.SCENES))
        return f"{bank + 1}{device.SCENES[switch]}"

    @property
    def position(self):
        return {"folder_key": self.folder_key, "position": self.index, "is_factory": False}


@dataclass(frozen=True)
class CapturedReference:
    path: Path
    name: str
    scene: str


def saved_presets(catalog):
    """Use the directory's array indices, which include empty slots."""
    rows = []
    for folder in catalog.get("presets", []):
        key = folder["key"]
        user_setlist = key.startswith("/media/p4/Presets/") or re.fullmatch(r"\d+_q", key)
        if folder["is_factory"] or folder["is_downloads"] or not user_setlist:
            continue
        for entry in folder["files"]:
            name, index = entry["name"], entry["index"]
            if not name.strip() or entry.get("is_readonly") or not 0 <= index < 256:
                continue
            if key == iterate.SCRATCH["folder_key"] and index >= 254 and name.startswith("Tone Lab "):
                continue
            rows.append(SavedPreset(name, folder["name"] or key, key, index))
    return sorted(rows, key=lambda row: (row.name.casefold(), row.setlist.casefold(), row.index))


def inventory():
    with device.connect() as qc:
        return saved_presets(qc.list_directory())


def _current_saved(qc, *, allow_scratch=False):
    dirty = qc.read_state("PresetDirty")
    if dirty is None or dirty.action != protocol.ACTION["UPDATE"]:
        raise RuntimeError("Could not verify whether the current Quad Cortex preset has unsaved changes.")
    if dirty.is_dirty:
        raise RuntimeError("Save or discard your edits on the Quad Cortex, then try again.")
    position = qc.get_setlist_position()
    if not position or not position["folder_key"] or (position == iterate.SCRATCH and not allow_scratch):
        raise RuntimeError("Load a saved setlist preset on the Quad Cortex first.")
    return position, device.read_preset(qc), device.active_scene(qc)


def _restore(qc, current, visited):
    position, original, scene = current
    if qc.get_setlist_position() not in (position, *visited):
        raise RuntimeError("The Quad Cortex was switched during the operation. Check the selected preset.")
    restored = iterate.recall(qc, position, original.name)
    if device.active_scene(qc) != scene:
        device.select_scene(qc, scene)
    if wire.describe(restored) != wire.describe(original):
        raise RuntimeError("The previous preset was recalled, but its settings changed. Check it on the Quad Cortex.")


def load_preset(choice):
    """Leave an explicitly chosen preset active with its saved settings."""
    with device.connect(write=True) as qc:
        current = _current_saved(qc, allow_scratch=True)
        if current[0] == choice.position and current[1].name == choice.name:
            return choice
        try:
            iterate.recall(qc, choice.position, choice.name)
        except Exception:
            _restore(qc, current, (choice.position,))
            raise
    return choice


def render_reference(choice, take, *, scene=None):
    """Reamp the selected passage over USB and restore the previous preset and scene."""
    if scene is not None and scene not in range(len(device.SCENES)):
        raise ValueError("Choose a Quad Cortex scene from A to H.")
    if take is None:
        raise ValueError("Record a dry and wet clip before capturing a preset reference.")
    take = Path(take)
    info, selection, dry, wet = iterate.take_audio(take)
    with device.connect(write=True) as qc:
        try:
            offset = iterate.calibration_offset(qc)
        except (OSError, ValueError, KeyError) as error:
            raise RuntimeError("Run the Quad Cortex USB bypass calibration to align reference audio.") from error
        firmware = qc.firmware
        current = _current_saved(qc)
        try:
            source = iterate.recall(qc, choice.position, choice.name)
            fingerprint = sha256(source.SerializeToString(deterministic=True)).hexdigest()
            reference_scene = source.default_scene if scene is None else scene
            if not 0 <= reference_scene < len(device.SCENES):
                raise RuntimeError("The preset did not report a valid default scene. Choose A to H explicitly.")
            inputs = {input_port for input_port, _ in iterate.routes(source) if input_port}
            physical_input = next(iter(inputs)) if len(inputs) == 1 else 0
            routes = iterate.reamp_routes(source, physical_input)
            iterate.prepare_fixture(qc, choice.position, source, physical_input)
            if device.active_scene(qc) != reference_scene:
                device.select_scene(qc, reference_scene)
            iterate.verify_fixture(qc, source, routes)
            samples = audio.reamp(dry)[offset:offset + len(dry)].copy()
            if samples.shape != wet.shape or not np.isfinite(samples).all():
                raise RuntimeError("The Quad Cortex returned incomplete reference audio.")
        finally:
            _restore(qc, current, (choice.position, iterate.SCRATCH))
    metrics = audio.describe(samples)
    if metrics["clipped_samples"] or metrics["lufs"] is None:
        raise RuntimeError("The reference return was silent or clipped. Check the preset's routing and level.")

    scene_name = device.SCENES[reference_scene]
    folder, manifest = session.new_session("preset-reference", info.get("guitar", ""),
                                           info.get("pickup_note", ""), info.get("song", ""))
    path = folder / "reference.wav"
    audio.write_wave(path, samples)
    manifest.update(state="ready", source_clip=take.name, selection_seconds=list(selection), firmware=firmware,
                    qc_reference={"name": choice.name, "scene": scene_name, "setlist": choice.setlist,
                                  "slot": choice.slot, "position": choice.position, "fingerprint": fingerprint},
                    candidates=[{"name": f"{choice.name} · Scene {scene_name}", "file": path.name,
                                 "scene": scene_name, "metrics": metrics}])
    session.write_json(folder / "session.json", manifest)
    return CapturedReference(path, choice.name, scene_name)
