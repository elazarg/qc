"""Quad Cortex reads and reversible tests in a dedicated preset."""

from contextlib import contextmanager
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from qc_mcp import catalog, protocol
from qc_mcp.autolevel import IN_PORT_USB_5_6, OUT_PORT_USB_5_6
from qc_mcp.transport import QuadCortex

SCENES = "ABCDEFGH"
LAB_PREFIX = "Tone Lab"


def editor_running():
    if sys.platform != "win32":
        return False
    tasklist = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tasklist.exe"
    try:
        result = subprocess.run([str(tasklist), "/FI", "IMAGENAME eq Cortex Control.exe", "/FO", "CSV", "/NH"],
                                capture_output=True, timeout=2.0, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return b'"cortex control.exe"' in result.stdout.lower()


class EditorActive(RuntimeError):
    """Cortex Control owns the hardware. Tone Lab can still use local audio."""


@contextmanager
def connect(*, write=False):
    """Give the stock editor priority on Windows, including while it starts."""
    handoff = "Cortex Control is using the Quad Cortex. Close it to use this device action. Your listening session stays open."
    if editor_running():
        raise EditorActive(handoff)
    qc = QuadCortex(share=not write)
    try:
        try:
            if editor_running():
                raise EditorActive(handoff)
            qc.open()
        except (OSError, RuntimeError) as error:
            if ("exclusively" in str(error).casefold() or "sharing violation" in str(error).casefold()) and editor_running():
                raise EditorActive(handoff) from error
            raise
        if not qc.protocol_version_verified:
            raise RuntimeError("The Quad Cortex firmware could not be verified.")
        yield qc
    finally:
        qc.close()


def read_preset(qc):
    preset = qc.get_current_preset()
    if preset is None:
        raise RuntimeError("The Quad Cortex returned no current preset.")
    return preset


def active_scene(qc):
    scene = qc.read_state("Scene", timeout_ms=1800)
    if scene is None or not 0 <= scene.selected_scene < len(SCENES):
        raise RuntimeError("The Quad Cortex did not report the active scene.")
    return scene.selected_scene


def require_clean(qc):
    dirty = qc.read_state("PresetDirty", timeout_ms=2200)
    if dirty is None or dirty.action != protocol.ACTION["UPDATE"]:
        raise RuntimeError("Couldn't verify the current preset's edits. Check the Quad Cortex and try again.")
    if dirty.is_dirty:
        raise RuntimeError("The current Quad Cortex sound has unsaved edits. Save them in Cortex Control or on the unit first.")


def live_position(qc):
    """Read the slot without retaining old unsolicited USB messages.

    The pinned qc-mcp request code can duplicate retained packets on a match.
    The exclusive live session has no other consumer of unsolicited packets.
    """
    with qc._io_lock:
        qc._pending.clear()
        try:
            return qc.get_setlist_position()
        finally:
            qc._pending.clear()


def recording_channels_from(qc, physical_input):
    if physical_input not in (1, 2):
        raise ValueError("Choose physical guitar input 1 or 2.")
    io = qc.read_message("IOSettings")
    if io is None:
        io = qc.read_message("IOSettings")
    if (io is None or not io.HasField("settings") or not io.settings.HasField("usb_port")
            or io.settings.usb_port.dry_wet not in (0, 1)):
        raise RuntimeError("Could not read the Quad Cortex USB Dry/Wet setting. Check the connection and retry.")
    return (physical_input + 2, 1, 2) if io.settings.usb_port.dry_wet else (physical_input, 3, 4)


def recording_channels(physical_input, *, include_context=False):
    with connect() as qc:
        channels = recording_channels_from(qc, physical_input)
        context = {}
        if include_context:
            try:
                context = {"wet_preset": read_preset(qc).name, "wet_scene": SCENES[active_scene(qc)],
                           "wet_position": qc.get_setlist_position(), "coros": qc.firmware}
            except Exception:
                logging.getLogger("tone_lab").warning("Recording preset context unavailable", exc_info=True)
    return (channels, context) if include_context else channels


def inspect(qc):
    preset = read_preset(qc)
    try:
        selected = SCENES[active_scene(qc)]
    except RuntimeError:
        selected = None
    lanes = []
    for row, chain in enumerate(preset.chains, 1):
        blocks = []
        for column, model in enumerate(chain.models, 1):
            if not model.hash:
                continue
            info = catalog.lookup(model.hash) or {}
            params = []
            for index, setting in enumerate(model.params):
                if index >= len(info.get("params", ())) or not setting.param_values:
                    continue
                meta = info["params"][index]
                value = setting.param_values[0].float_value
                if math.isfinite(value):
                    params.append({"index": index, "name": meta["name"],
                                   "value": catalog.to_display(model.hash, index, value),
                                   "per_scene": setting.scene_mode})
            blocks.append({"column": column, "name": info.get("name", str(model.hash)),
                           "model_hash": model.hash, "parameters": params})
        lanes.append({"row": row, "input": chain.in_portid, "output": chain.out_portid,
                      "blocks": blocks})
    return {"firmware": qc.firmware, "preset": preset.name, "scene": selected,
            "scene_labels": list(preset.scene_labels), "position": qc.get_setlist_position(),
            "lanes": lanes}


def require_lab_preset(qc, expected_position=None):
    preset = read_preset(qc)
    position = qc.get_setlist_position()
    if (not position or not position.get("folder_key", "").startswith("/media/p4/Presets/")
            or position.get("is_factory") or not preset.name.startswith(f"{LAB_PREFIX} ")):
        raise RuntimeError(f"Load a saved user test preset named '{LAB_PREFIX} …' first.")
    if expected_position is not None and expected_position != position:
        raise RuntimeError("The selected QC preset changed during the test.")
    chains = list(preset.chains)
    active = [row for row, chain in enumerate(chains) if chain.in_portid or chain.out_portid
              or any(model.hash for model in chain.models)]
    if (len(active) != 1 or chains[active[0]].in_portid != IN_PORT_USB_5_6
            or chains[active[0]].out_portid != OUT_PORT_USB_5_6
            or any(point.split >= 0 or point.mix >= 0
                   for point in chains[active[0]].split_control_points)):
        raise RuntimeError("Use one unsplit lane routed from USB 5/6 to USB 5/6, with other lanes unused.")
    return preset, position, active[0]


def select_scene(qc, scene):
    qc.set_scene(scene)
    for _ in range(3):
        time.sleep(0.25)
        try:
            selected = active_scene(qc)
        except RuntimeError:
            continue
        if selected == scene:
            return
    raise RuntimeError(f"Scene {SCENES[scene]} could not be verified.")


def parameter(preset, row, column, index):
    try:
        model = preset.chains[row].models[column]
        setting = model.params[index]
    except IndexError as error:
        raise ValueError("That block column or parameter index is missing from the test preset.") from error
    info = catalog.lookup(model.hash)
    if (not info or index >= len(info["params"]) or not setting.param_values
            or setting.scene_mode or setting.expression):
        raise ValueError("Choose a catalogued parameter shared by all scenes with no expression assignment.")
    meta = info["params"][index]
    if (not setting.param_values[0].HasField("float_value")
            or len(setting.param_values) != 1 or meta.get("type") not in ("Float", "float")):
        raise ValueError("Choose a continuous numeric parameter.")
    return model.hash, meta, setting.param_values[0].float_value


def bounded_value(model_hash, index, requested, starting_value):
    value = catalog.to_norm(model_hash, index, requested)
    if (not math.isfinite(value) or abs(value - starting_value) > 0.2
            or not math.isclose(catalog.to_display(model_hash, index, value), requested, abs_tol=0.01)):
        raise ValueError("Choose a legal value within 20% of the knob’s normalized travel from its starting point.")
    return value


def write_parameter(qc, position, row, column, index, model_hash, value):
    preset, _, active = require_lab_preset(qc, position)
    if active != row or parameter(preset, row, column, index)[0] != model_hash:
        raise RuntimeError("The target block changed during the sweep.")
    qc.set_param(row, column, index, value)
    time.sleep(0.3)
    preset, _, _ = require_lab_preset(qc, position)
    actual_hash, _, actual = parameter(preset, row, column, index)
    if actual_hash != model_hash or not math.isclose(value, actual, abs_tol=0.002):
        raise RuntimeError("The changed parameter could not be verified on the Quad Cortex.")
