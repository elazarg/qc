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

from lab.sound import catalog
from lab.sound.chain import COLUMNS, ROWS, SCENES

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

    def record(self, seconds, channels, *, cancel=None):
        """A plucked note, as if the player had played one."""
        time = np.arange(round(48000 * seconds)) / 48000
        note = .2 * np.sin(2 * np.pi * 196 * time) * np.exp(-2 * time)
        return np.repeat(note[:, None], len(channels), axis=1).astype(np.float32)


def _open(self, handshake=True):
    """Opening the simulated unit, so that it can be connected like the real one (`device.connect`)."""
    return self


SimUnit.open = _open


# ---- The unit's own tables: processing costs and named device presets ----
#
# The real unit answers a ModuleStats read with the cost of every model it can load, and a ModelPreset read
# with the index of Neural DSP's named settings ("device presets"); a named setting's values come back only by
# loading it onto a block (`load_model_preset`), and `select_model_slot` says which one a block holds. The
# simulated unit does the same, for a few models, with made-up numbers.

DEFAULT_PRESET = "SpecialFactoryModelPresetID"
SIM_MODEL_PRESETS = {      # model -> [(id, name, {parameter index: display value}, is_default)]
    1001: [("f-1001-default", "Default", {}, True), ("f-1001-tight", "Tight Modern", {0: 3., 1: 6.}, False),
           ("f-1001-hot", "Hot Lead", {0: 8.5, 4: 6.}, False)],
    6011: [("f-6011-default", "Default", {}, True), ("f-6011-slap", "Slapback", {0: 30., 1: 5.}, False)],
    4002: [("f-4002-default", "Default", {}, True), ("f-4002-tamer", "Humbucker Tamer", {}, False)],
}


def _module_stats(self):
    """What the unit says each model costs, made up but steady: (cpu, memory) by model id."""
    stats = []
    for found in catalog.models():
        if found.category in ("Internal Routing", "Lane output control", "Tempo control", "InputGateControl",
                              "IOSettings Internal", "Mixer", "Splitter"):
            continue
        echoes = found.category in ("Delay", "Reverb")
        stats.append(SimpleNamespace(id=found.id, cpu_percent=round(1 + found.id % 97 / 10, 3),
                                     dm_heap_bytes=1000 + found.id % 500, pm_heap_bytes=2000 + found.id % 300,
                                     dmda_bytes=0, pmda_bytes=0, edma_percent=5 + found.id % 20 if echoes else 0,
                                     latency=0))
    cores = [SimpleNamespace(id=0, cpu_percent=92., dm_heap_bytes=200000, pm_heap_bytes=300000, dmda_bytes=50000,
                             pmda_bytes=60000, edma_percent=85., latency=0),
             SimpleNamespace(id=1, cpu_percent=92., dm_heap_bytes=180000, pm_heap_bytes=300000, dmda_bytes=50000,
                             pmda_bytes=60000, edma_percent=85., latency=0)]
    return SimpleNamespace(stats=stats, core_stats=cores)


_read_message = SimUnit.read_message


def _read_message_with_stats(self, name, **options):
    if name == "ModuleStats":
        return _module_stats(self)
    return _read_message(self, name, **options)


def _model_presets(self):
    return getattr(self, "model_presets", None) or SIM_MODEL_PRESETS


def _list_model_presets(self, model_hash=None, **_):
    rows = []
    for model_id, presets in _model_presets(self).items():
        if model_hash is not None and model_id != int(model_hash):
            continue
        for preset_id, name, _values, is_default in presets:
            rows.append({"id": preset_id, "name": name, "model_hash": model_id, "is_factory": True,
                         "is_default": is_default})
    return rows


def _load_model_preset(self, row, column, model_hash, preset_id, is_factory=True, settle_s=0., **_):
    self._write("model preset", row, column, model_hash, preset_id)
    cell = self.working.chains[row].models[column]
    found = {item[0]: item for item in _model_presets(self).get(int(model_hash), ())}
    if cell.hash != int(model_hash) or preset_id not in found:
        return
    self._fill(cell, cell.hash)
    for index, display in found[preset_id][2].items():
        for entry in cell.params[index].param_values:
            entry.float_value = catalog.to_norm(cell.hash, index, display)
    loaded = self.__dict__.setdefault("loaded_presets", {})
    loaded[(row, column)] = preset_id


def _select_model_slot(self, row, column, **_):
    cell = self.working.chains[row].models[column]
    if not cell.hash:
        return None
    loaded = self.__dict__.get("loaded_presets", {})
    return {"value": loaded.get((row, column), DEFAULT_PRESET), "is_factory": True, "hash": cell.hash}


_add_block = SimUnit.add_block


def _add_block_fresh(self, model_id, row=0, column=0, **options):
    self.__dict__.setdefault("loaded_presets", {}).pop((row, column), None)
    return _add_block(self, model_id, row=row, column=column, **options)


SimUnit.read_message = _read_message_with_stats
SimUnit.list_model_presets = _list_model_presets
SimUnit.load_model_preset = _load_model_preset
SimUnit.select_model_slot = _select_model_slot
SimUnit.add_block = _add_block_fresh


# ---- Impulse responses and Neural Captures on the unit ----
#
# The directory lists them as the unit does (see `assets.py`): every IR a file of `local_ir_root` with its name
# and `CIR_` key, links to them by key with blank names in "My IRs", and the factory IRs in a folder of their own
# with no keys; every capture a file of `local_nc_root`, and the factory ones also in "Factory Captures V1" or
# "V2". A block binds one by its strings (IR PATH 2 and IR NAME 22; a capture's file_name 5, key then name), and a
# capture block reads back with the name the unit has for that key.

SIM_IRS = [("CIR_5a11ed0000000000000000000000001", "YA MES 212 V30 Mix 04"),
           ("CIR_5a11ed0000000000000000000000002", "Celestion G12M SM57 Cap")]
SIM_FACTORY_IRS = ["Factory 412 Greenback 57"]
SIM_CAPTURES = [("a" * 64, "Brit 2555 Lead 16", 1, True), ("b" * 64, "Bogna Fish 8", 2, True),
                ("c" * 64, "Dark Jazz", None, False)]       # (key, name, factory version or None, factory)


def _assets(self):
    if "irs" not in self.__dict__:
        self.irs = [{"key": key, "name": name} for key, name in SIM_IRS]
        self.captures = [{"key": key, "name": name, "version": version, "factory": factory}
                         for key, name, version, factory in SIM_CAPTURES]
    return self.irs, self.captures


def _add_ir(self, name, key=None):
    """Put an IR on the simulated unit, as the player would with Cortex Control. Returns its key."""
    irs, _ = _assets(self)
    key = key or f"CIR_{len(irs) + 1:031x}"
    irs.append({"key": key, "name": name})
    return key


def _add_capture(self, name, key=None, *, version=None, factory=False):
    """Put a capture on the simulated unit. `version` 1 or 2 lists it in that factory folder. Returns its key."""
    _, captures = _assets(self)
    key = key or f"{len(captures) + 1:064x}"
    captures.append({"key": key, "name": name, "version": version, "factory": factory})
    return key


def _asset_folders(self):
    irs, captures = _assets(self)
    entry = lambda index, name, key: {"index": index, "name": name, "key": key}
    ir_folders = [
        {"key": "local_ir_root", "name": "IRs Library", "is_factory": False, "is_downloads": False,
         "files": [entry(index, item["name"], item["key"]) for index, item in enumerate(irs)]},
        {"key": "2_q", "name": "My IRs", "is_factory": False, "is_downloads": False,
         "files": [entry(index, "", item["key"]) for index, item in enumerate(irs)]},
        {"key": "/opt/neuraldsp/impulse_responses", "name": "", "is_factory": True, "is_downloads": False,
         "files": [entry(index, name, "") for index, name in enumerate(SIM_FACTORY_IRS)]}]
    capture_folders = [
        {"key": "local_nc_root", "name": "Captures Library", "is_factory": False, "is_downloads": False,
         "files": [entry(index, item["name"], item["key"]) for index, item in enumerate(captures)]},
        {"key": "1_q", "name": "My Captures", "is_factory": False, "is_downloads": False,
         "files": [entry(index, "", item["key"]) for index, item in enumerate(
             [item for item in captures if not item["factory"]])]}]
    for number in (1, 2):
        capture_folders.append(
            {"key": f"{number}_f", "name": f"Factory Captures V{number}", "is_factory": True, "is_downloads": False,
             "files": [entry(index, "", item["key"]) for index, item in enumerate(
                 [item for item in captures if item["factory"] and item["version"] == number])]})
    return ir_folders, capture_folders


_list_directory = SimUnit.list_directory


def _list_directory_with_assets(self, **options):
    listing = _list_directory(self, **options)
    listing["irs"], listing["captures"] = _asset_folders(self)
    return listing


_set_param_typed = SimUnit.set_param_typed


def _set_param_typed_naming(self, row, column, index, valspecs, scene_mode=False):
    _set_param_typed(self, row, column, index, valspecs, scene_mode)
    cell = self.working.chains[row].models[column]
    text = valspecs[0][2] if valspecs else None
    if cell.hash in catalog.CAPTURE_MODELS and index == 5 and isinstance(text, str) and index < len(cell.params):
        _, captures = _assets(self)
        known = next((item for item in captures if item["key"] == text[:64]), None)
        if known is not None:
            for entry in cell.params[index].param_values:
                entry.string_value = known["key"] + known["name"]


def _set_ir(self, row, column, ir_key, ir_name="", stereo=False):
    echo = self.add_block(29002 if stereo else catalog.IR_LOADER, row=row, column=column)
    self.set_param_typed(row, column, 2, [[None, None, str(ir_key)]])
    self.set_param_typed(row, column, 22, [[None, None, str(ir_name)]])
    return echo


def _set_capture(self, row, column, capture_key, capture_name="", version="v1"):
    model = catalog.CAPTURE_MODELS[1] if str(version).lower() in ("v2", "2") else catalog.CAPTURE_MODELS[0]
    echo = self.add_block(model, row=row, column=column)
    self.set_param_typed(row, column, 5, [[None, None, f"{capture_key}{capture_name}"]])
    return echo


SimUnit.add_ir = _add_ir
SimUnit.add_capture = _add_capture
SimUnit.list_directory = _list_directory_with_assets
SimUnit.set_param_typed = _set_param_typed_naming
SimUnit.set_ir = _set_ir
SimUnit.set_capture = _set_capture

