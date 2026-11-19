"""A simulated Quad Cortex, so that the whole app runs and is tested without hardware.

It answers the calls the app makes the way the unit was seen to answer them
on CorOS 4.1.1:

- The working preset is a BinaryPreset with four rows of eight cells. A block
  is added with its catalog defaults plus the one extra value every block
  holds. A write to an empty cell is ignored. A block the unit cannot fit is
  refused silently: the grid simply comes back without it.
- A value assigned to scenes holds eight values, and a plain write lands on
  the selected scene only. Before that, a write lands on all of them.
- Presets live in slots, addressed by folder key and position. Recalling a
  slot loads its preset and discards unsaved edits; saving writes the working
  preset into a slot under a name. The directory lists every slot.
- Selecting another preset, as a player would on the unit's footswitches, is
  `player_selects`. Cortex Control can be "open".

It also renders audio, crudely: enough to tell clean from driven, quiet from
loud, muted from not, and dry from echoing. See `SimAudio`.
"""

from copy import deepcopy
from threading import Lock
from types import SimpleNamespace

import numpy as np
from qc_mcp import preset as wire

from . import catalog
from .chain import COLUMNS, ROWS, SCENES

MY_PRESETS = "/media/p4/Presets/My Presets"
TONE_LAB = "/media/p4/Presets/Tone Lab"
FACTORY = "/opt/neuraldsp/Factory Library"


def blank(name=""):
    """An empty preset in the shape the unit stores one."""
    grid = type(wire.PresetBuilder("x").build())()
    grid.name = name
    for row in range(ROWS):
        lane = grid.chains.add()
        lane.row = row
        for _ in range(COLUMNS):
            lane.models.add()
        point = lane.split_control_points.add()
        point.split = point.mix = -1
        mixer = lane.mixer.add()
        mixer.hash = catalog.MIXER
        for index, param in enumerate(catalog.model(catalog.MIXER).params):
            mixer.params.add().param_values.add().float_value = catalog.to_norm(catalog.MIXER, index, param.default or 0)
        control = lane.output_control.add()
        for value in (.769, .5, 0., 0., 0.):  # volume at 0 dB, pan centred, not muted
            control.params.add().param_values.add().float_value = value
        bypass = grid.bypass.add()
        bypass.row = row
        for column in range(COLUMNS):
            cell = bypass.colBypass.add()
            cell.column = column
            for _ in range(SCENES):
                cell.sceneBypass.add()
    grid.scene_labels.extend([""] * SCENES)
    return grid


class SimUnit:
    """Stands in for `qc_mcp.QuadCortex`."""

    firmware = "4.1.1"
    protocol_version_verified = True

    def __init__(self, *, refuse=None, setlists=(MY_PRESETS, TONE_LAB)):
        self._pending, self._io_lock = [], Lock()
        self.slots = {}                      # (folder key, position) -> saved preset
        self.folders = {key: key.rsplit("/", 1)[-1] for key in setlists}
        self.folders[FACTORY] = "Factory Library"
        self.slots[(FACTORY, 0)] = self._preset("Brit 2203", [(1001, {}), (12001, {})])
        self.position = {"folder_key": FACTORY, "position": 0, "is_factory": True}
        self.working = deepcopy(self.slots[(FACTORY, 0)])
        self.dirty = False
        self.scene = 0
        self.refuse = refuse or (lambda grid, model_id: False)
        self.editor_open = False
        self.log = []                        # every write, in order: (call, arguments)
        self.writes_while = []               # writes made while the selected slot was not the one recorded

    # ---- presets and slots ----

    def _preset(self, name, blocks, *, routes=((1, 19),)):
        grid = blank(name)
        for column, (model_id, values) in enumerate(blocks):
            self._fill(grid.chains[0].models[column], model_id)
            for index, display in values.items():
                grid.chains[0].models[column].params[index].param_values[0].float_value = \
                    catalog.to_norm(model_id, index, display)
        for row, (inlet, outlet) in enumerate(routes):
            grid.chains[row].in_portid, grid.chains[row].out_portid = inlet, outlet
        return grid

    def save_slot(self, folder_key, position, name, blocks, *, routes=((1, 19),)):
        """Put a preset in a slot, as if the player had saved it there."""
        self.slots[(folder_key, position)] = self._preset(name, blocks, routes=routes)

    def get_current_preset(self, **_):
        return deepcopy(self.working)

    def get_setlist_position(self):
        return dict(self.position)

    def recall(self, folder_key="", position=0, is_factory=False, **_):
        self._write("recall", folder_key, position)
        grid = self.slots.get((folder_key, position))
        self.working = deepcopy(grid) if grid is not None else blank("Unsaved")
        self.position = {"folder_key": folder_key, "position": position, "is_factory": bool(is_factory)}
        self.dirty = False
        self.scene = 0

    def write_preset_file(self, folder_key, position, name, binary_preset=None):
        self._write("save", folder_key, position, name)
        grid = deepcopy(self.working)
        grid.name = name
        self.slots[(folder_key, position)] = grid
        self.working.name = name
        self.position = {"folder_key": folder_key, "position": position, "is_factory": False}
        self.dirty = False

    def player_selects(self, folder_key, position):
        """The player picks another preset on the unit itself."""
        self.position = {"folder_key": folder_key, "position": position, "is_factory": folder_key == FACTORY}
        grid = self.slots.get((folder_key, position))
        self.working = deepcopy(grid) if grid is not None else blank("Unsaved")
        self.dirty = False

    def list_directory(self, **_):
        folders = []
        for key, name in self.folders.items():
            files = []
            for index in range(256):
                grid = self.slots.get((key, index))
                files.append({"index": index, **({"name": grid.name, "key": f"{grid.name}.pb"} if grid is not None else {})})
            folders.append({"key": key, "name": name, "is_factory": key == FACTORY, "files": files})
        return {"presets": folders, "irs": [], "captures": []}

    def read_state(self, name, **_):
        if name == "Scene":
            return SimpleNamespace(selected_scene=self.scene)
        if name == "PresetDirty":
            return SimpleNamespace(dirty=self.dirty, is_dirty=self.dirty)
        if name == "SetlistPosition":
            return SimpleNamespace(**self.position)
        return None

    def read_message(self, name, **_):
        if name == "IOSettings":
            usb = SimpleNamespace(dry_wet=0)
            return SimpleNamespace(HasField=lambda field: True, settings=SimpleNamespace(
                HasField=lambda field: True, usb_port=usb))
        return None

    # ---- grid edits ----

    def _write(self, call, *args):
        self.log.append((call, args))
        if call not in ("recall",):
            self.dirty = call != "save"

    def _fill(self, cell, model_id):
        cell.Clear()
        cell.hash = model_id
        found = catalog.model(model_id)
        defaults = catalog.defaults(model_id)
        for index in range(len(found.params) + 1):  # the unit stores one value more than the catalog lists
            values = cell.params.add().param_values
            for _ in range(SCENES):
                values.add().float_value = defaults.get(index, 0.)

    def add_block(self, model_id, row=0, column=0, **_):
        self._write("add", row, column, model_id)
        if self.refuse(self.working, model_id):
            return None
        self._fill(self.working.chains[row].models[column], model_id)
        return True

    def delete_block(self, row, column, **_):
        self._write("delete", row, column)
        self.working.chains[row].models[column].Clear()
        cell = self.working.bypass[row].colBypass[column]
        cell.sceneMode = False
        for flag in cell.sceneBypass:
            flag.bypass = False

    def _targets(self, parameter):
        return [parameter.param_values[self.scene]] if parameter.scene_mode else list(parameter.param_values)

    def set_param(self, row, column, index, value):
        self._write("param", row, column, index, value)
        cell = self.working.chains[row].models[column]
        if not cell.hash or index >= len(cell.params):
            return
        for entry in self._targets(cell.params[index]):
            entry.float_value = float(value)

    def set_param_typed(self, row, column, index, valspecs, scene_mode=False):
        self._write("param", row, column, index, valspecs)
        cell = self.working.chains[row].models[column]
        if not cell.hash or index >= len(cell.params):
            return
        number, whole, text = valspecs[0]
        for entry in self._targets(cell.params[index]):
            if text is not None:
                entry.string_value = text
            elif whole is not None:
                entry.int_value = whole
            else:
                entry.float_value = number

    def assign_param_to_scenes(self, row, column, index):
        self._write("assign", row, column, index)
        cell = self.working.chains[row].models[column]
        if index >= len(cell.params):  # one past the values it holds: the block's bypass
            self.working.bypass[row].colBypass[column].sceneMode = True
        else:
            cell.params[index].scene_mode = True

    def set_block_bypass(self, row, column, bypassed=True, scenes=None, **_):
        self._write("bypass", row, column, bypassed)
        cell = self.working.bypass[row].colBypass[column]
        for flag in [cell.sceneBypass[self.scene]] if cell.sceneMode else cell.sceneBypass:
            flag.bypass = bool(bypassed)

    def set_split_points(self, row, split=-1, mix=-1):
        self._write("split", row, split, mix)
        point = self.working.chains[row].split_control_points[0]
        point.split, point.mix = split, mix

    def set_lane_param(self, row, field, index, value, column=0):
        self._write("lane", row, field, index, value)
        getattr(self.working.chains[row], field)[0].params[index].param_values[0].float_value = float(value)

    def set_routing(self, row, in_portid=None, out_portid=None):
        self._write("route", row, in_portid, out_portid)
        lane = self.working.chains[row]
        if in_portid is not None:
            lane.in_portid = in_portid
        if out_portid is not None:
            lane.out_portid = out_portid

    def set_scene(self, scene):
        self.scene = int(scene)

    def set_scene_label(self, index, label):
        self._write("label", index, label)
        self.working.scene_labels[index] = label

    def close(self):
        pass


class SimAudio:
    """What comes back over USB when a dry phrase is played into the simulated unit.

    The row whose input is USB and whose output is USB is played. Each amp or
    drive pushes the signal into a soft clipper by its first parameter (its
    gain); the app's level block applies its trim; a delay or reverb adds a
    decaying copy; a muted row returns silence. Nothing here sounds like a
    guitar amp. It only moves the measures the app relies on the right way.
    """

    LATENCY = 96   # samples

    def __init__(self, unit):
        self.unit = unit

    def reamp(self, dry, *, tail=2., cancel=None):
        rate = 48000
        signal = np.concatenate([dry[:, 0].astype(np.float64), np.zeros(round(tail * rate))])
        out = np.zeros_like(signal)
        for lane in self.unit.working.chains:
            if (lane.in_portid, lane.out_portid) != (12, 14):
                continue
            control = lane.output_control[0].params if len(lane.output_control) else []
            if len(control) > catalog.LANE_MUTE and control[catalog.LANE_MUTE].param_values[0].float_value >= .5:
                continue
            sound = signal.copy()
            for column, cell in enumerate(lane.models):
                found = catalog.model(cell.hash) if cell.hash else None
                if not found or self.unit.working.bypass[lane.row].colBypass[column].sceneBypass[self.unit.scene].bypass:
                    continue
                first = cell.params[0].param_values[0]
                value = first.float_value if first.WhichOneof("value") == "float_value" else 0.
                if found.category in ("Guitar Amplifier", "Guitar Overdrive", "Bass Amplifier", "Bass Overdrive"):
                    push = 10 ** (40 * value / 20)
                    sound = np.tanh(sound * push) * .3
                elif cell.hash == catalog.GAIN_BLOCK:
                    sound = sound * 10 ** (catalog.to_display(cell.hash, 0, value) / 20)
                elif found.category in ("Delay", "Reverb"):
                    echo = np.zeros_like(sound)
                    step = rate // 4
                    for repeat in range(1, 6):
                        echo[repeat * step:] += sound[:-repeat * step] * (.35 ** repeat)
                    sound = sound + echo
            out += sound
        out = np.concatenate([np.zeros(self.LATENCY), out])[:len(signal)]
        return np.stack([out, out], axis=1).astype(np.float32)
