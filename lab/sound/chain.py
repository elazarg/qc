"""A chain as the Quad Cortex holds it, and the writes that turn one chain into another.

A `Chain` is four rows of eight cells, each row with its input and output,
an optional split, the mixer that joins the split, and the row's output
control. A cell is empty or holds a `Block`. A block's parameters are the
values the unit stores (0 to 1 for numbers, text for microphones, impulse
responses and captures). A value that differs by scene is a tuple of eight.

The same type describes what was read from the unit, what a design asks
for, and what a saved preset contains. The `plan_...` functions work out the
writes from one to another, and `lab.qc.unit.Unit.apply` carries them out in
the order the unit needs:

1. Inputs of rows that are about to change are disconnected first, so that
   nothing half-built is heard.
2. Blocks that are not wanted are removed, splits are set, wanted blocks are
   added. Adding or removing a block mutes all audio for about 0.2 s, so this
   is the only phase that interrupts sound.
3. Values are written. A value that differs by scene is first assigned to
   scenes, then written with each scene selected in turn. A value the unit
   already holds by scene, wanted the same in every scene, is written in each
   scene too: nothing takes a value back off the scenes.
4. The rows' output controls (volume, mute) are set before any input is
   connected.
5. Inputs and outputs are routed last.

A parameter missing from a wanted block means "leave it as it is". A block
the app adds starts with the unit's own values, so a design states only what
it means to change, and a build starts every block afresh.
"""

from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
import struct

from lab.sound import catalog

ROWS, COLUMNS, SCENES = 4, 8, 8
TOLERANCE = .006        # stored values within this of each other are the same (the unit rounds to about 1/256)
MULTI_OUT = 19          # the port a row leaves the unit by: the main outputs


def _value(value):
    """A stored value in canonical form: a float, a string, or a tuple of eight of either."""
    if isinstance(value, (list, tuple)):
        values = tuple(_value(item) for item in value)
        if len(values) != SCENES:
            raise ValueError("A value that differs by scene has one entry per scene.")
        return values
    if isinstance(value, str):
        return value
    return float(value)


def same(a, b):
    """Whether two stored values are the same, allowing for the unit's rounding."""
    if isinstance(a, tuple) or isinstance(b, tuple):
        a = a if isinstance(a, tuple) else (a,) * SCENES
        b = b if isinstance(b, tuple) else (b,) * SCENES
        return all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return abs(float(a) - float(b)) <= TOLERANCE


# Text the unit binds by a key at its start, and text that only names what is bound (see `assets.py`).
# A capture block's file_name is the capture's 64-character key followed by a name the unit fills in; an IR
# loader's IR NAME parameters only label the IR its IR PATH holds.
KEYED = {(model, 5): 64 for model in catalog.CAPTURE_MODELS}
LABELS = {(model, index) for model in (catalog.IR_LOADER, catalog.IR_LOADER_STEREO) for index in (22, 23)}


def agrees(model, index, have, want):
    """Whether a stored value of a block is the wanted one: `same`, except that only the key of a capture's
    file_name decides."""
    width = KEYED.get((model, index))
    if width and isinstance(have, str) and isinstance(want, str) and len(want) >= width:
        return have[:width].casefold() == want[:width].casefold()
    return same(have, want)


def in_scene(value, scene):
    return value[scene] if isinstance(value, tuple) else value


@dataclass(frozen=True)
class Block:
    model: int
    params: tuple = ()         # ((index, value), ...) sorted by index
    bypass: object = False     # a bool, or eight when it differs by scene

    def __post_init__(self):
        object.__setattr__(self, "params", tuple(sorted((int(i), _value(v)) for i, v in dict(self.params).items())))
        if isinstance(self.bypass, (list, tuple)):
            flags = tuple(bool(flag) for flag in self.bypass)
            object.__setattr__(self, "bypass", flags[0] if len(set(flags)) == 1 else flags)
        else:
            object.__setattr__(self, "bypass", bool(self.bypass))

    @property
    def values(self):
        return dict(self.params)

    def get(self, index, scene=0):
        value = self.values.get(index)
        return None if value is None else in_scene(value, scene)

    def set(self, index, value):
        return replace(self, params={**self.values, index: value})

    def display(self, index, scene=0):
        value = self.get(index, scene)
        return value if value is None or isinstance(value, str) else catalog.to_display(self.model, index, value)

    def set_display(self, index, display):
        return self.set(index, catalog.to_norm(self.model, index, display))

    @property
    def info(self):
        return catalog.model(self.model)

    @property
    def name(self):
        info = self.info
        return info.name if info else f"model {self.model}"


@dataclass(frozen=True)
class Row:
    cells: tuple = (None,) * COLUMNS
    inlet: int = 0
    outlet: int = 0
    split: tuple | None = None      # (split column, mix column)
    mixer: tuple = ()               # ((index, value), ...) of the mixer joining the split
    output: tuple = ()              # ((index, value), ...) of the row's output control

    def __post_init__(self):
        cells = tuple(self.cells) + (None,) * (COLUMNS - len(self.cells))
        object.__setattr__(self, "cells", cells[:COLUMNS])
        object.__setattr__(self, "split", tuple(self.split) if self.split else None)
        object.__setattr__(self, "mixer", tuple(sorted((int(i), _value(v)) for i, v in dict(self.mixer).items())))
        object.__setattr__(self, "output", tuple(sorted((int(i), _value(v)) for i, v in dict(self.output).items())))

    @property
    def blocks(self):
        return [(column, block) for column, block in enumerate(self.cells) if block is not None]

    @property
    def route(self):
        return self.inlet, self.outlet


@dataclass(frozen=True)
class Chain:
    rows: tuple = field(default_factory=lambda: (Row(),) * ROWS)
    labels: tuple = ("",) * SCENES

    def __post_init__(self):
        rows = tuple(self.rows) + (Row(),) * (ROWS - len(self.rows))
        object.__setattr__(self, "rows", rows[:ROWS])
        labels = tuple(self.labels) + ("",) * (SCENES - len(self.labels))
        object.__setattr__(self, "labels", labels[:SCENES])

    def block(self, row, column):
        return self.rows[row].cells[column]

    def with_block(self, row, column, block):
        cells = list(self.rows[row].cells)
        cells[column] = block
        return self.with_row(row, replace(self.rows[row], cells=tuple(cells)))

    def with_row(self, row, value):
        rows = list(self.rows)
        rows[row] = value
        return replace(self, rows=tuple(rows))

    def with_routes(self, routes):
        return replace(self, rows=tuple(replace(row, inlet=inlet, outlet=outlet)
                                        for row, (inlet, outlet) in zip(self.rows, routes)))

    @property
    def routes(self):
        return [row.route for row in self.rows]

    @property
    def blocks(self):
        return [(r, column, block) for r, row in enumerate(self.rows) for column, block in row.blocks]

    @property
    def scenes(self):
        """How many scenes the chain names, or 0 when it is one sound."""
        named = [index for index, label in enumerate(self.labels) if label]
        return max(named) + 1 if named else 0

    def scenes_in_use(self):
        """How many scenes make a difference: the named ones, or up to the last scene where a value or a
        block's switching differs from scene A. 0 for one sound."""
        last = 0
        for _, _, block in self.blocks:
            states = [value for _, value in block.params if isinstance(value, tuple)]
            if isinstance(block.bypass, tuple):
                states.append(tuple(float(flag) for flag in block.bypass))
            for state in states:
                for scene in range(len(state) - 1, 0, -1):
                    if isinstance(state[scene], float) and abs(state[scene] - state[0]) > TOLERANCE:
                        last = max(last, scene)
                        break
        return max(self.scenes, last + 1 if last else 0)

    def sound(self):
        """What makes the chain sound as it does, without its routing, levels or names.

        Only the values the chain states are taken. A design leaves most values
        to the unit, which sets its own when it adds a block, and those are not
        always the catalog's defaults (a cab microphone's level is 0 in the
        catalog and half way on the unit). So two designs that state the same
        values share a digest, and a design is checked against what was built
        with `differences`, which ignores what it leaves unset.
        """
        return {"rows": [{"cells": [None if found is None else
                                    {"model": found.model, "params": [[i, v] for i, v in found.params],
                                     "bypass": found.bypass}
                                    for found in row.cells],
                          "split": row.split, "mixer": [[i, v] for i, v in row.mixer] if row.split else []}
                         for row in self.rows]}

    def digest(self):
        """A short fingerprint of how the chain sounds. Equal chains, as the unit rounds them, share it."""
        def rounded(value):
            if isinstance(value, float):
                # The unit stores 32-bit floats. Compare at that precision, then on a 1/256 grid.
                return round(struct.unpack("f", struct.pack("f", value))[0] * 256) / 256
            if isinstance(value, list):
                return [rounded(item) for item in value]
            if isinstance(value, dict):
                return {key: rounded(item) for key, item in value.items()}
            if isinstance(value, tuple):
                return [rounded(item) for item in value]
            return value

        text = json.dumps(rounded(self.sound()), sort_keys=True)
        return sha256(text.encode("utf-8")).hexdigest()[:16]

    def to_record(self):
        return {"labels": list(self.labels), "rows": [
            {"inlet": row.inlet, "outlet": row.outlet, "split": list(row.split) if row.split else None,
             "mixer": [[i, v] for i, v in row.mixer], "output": [[i, v] for i, v in row.output],
             "cells": [None if block is None else
                       {"model": block.model, "params": [[i, list(v) if isinstance(v, tuple) else v]
                                                         for i, v in block.params],
                        "bypass": list(block.bypass) if isinstance(block.bypass, tuple) else block.bypass}
                       for block in row.cells]}
            for row in self.rows]}

    @classmethod
    def from_record(cls, record):
        rows = []
        for row in record["rows"]:
            cells = tuple(None if cell is None else Block(cell["model"], tuple((i, v) for i, v in cell["params"]),
                                                          cell.get("bypass", False))
                          for cell in row["cells"])
            rows.append(Row(cells, row.get("inlet", 0), row.get("outlet", 0), row.get("split"),
                            tuple(map(tuple, row.get("mixer", []))), tuple(map(tuple, row.get("output", [])))))
        return cls(tuple(rows), tuple(record.get("labels", ())))


# ---- Reading what the unit holds -------------------------------------------------------------------------

def _stored(param_value):
    kind = param_value.WhichOneof("value")
    if kind == "string_value":
        return param_value.string_value
    if kind == "int_value":
        return float(param_value.int_value)
    return float(param_value.float_value)


def _params(message):
    found = {}
    # Parameters are stored in order: a parameter's index is its position.
    for index, param in enumerate(message.params):
        values = [_stored(value) for value in param.param_values]
        if not values:
            continue
        if param.scene_mode and len(values) >= SCENES:
            found[index] = tuple(values[:SCENES])
        else:
            found[index] = values[0]
    return found


def from_preset(preset):
    """The chain in a preset as the unit returns it (a BinaryPreset message)."""
    rows = []
    for r, lane in enumerate(list(preset.chains)[:ROWS]):
        cells = []
        bypass = preset.bypass[r] if r < len(preset.bypass) else None
        for column, held in enumerate(list(lane.models)[:COLUMNS]):
            if not held.hash:
                cells.append(None)
                continue
            flags = False
            if bypass is not None and column < len(bypass.colBypass):
                entry = bypass.colBypass[column]
                scenes = [flag.bypass for flag in entry.sceneBypass]
                if entry.sceneMode and len(scenes) >= SCENES:
                    flags = tuple(scenes[:SCENES])
                elif scenes:
                    flags = scenes[0]
            cells.append(Block(int(held.hash), tuple(_params(held).items()), flags))
        points = [(point.split, point.mix) for point in lane.split_control_points if (point.split, point.mix) != (-1, -1)]
        mixer = tuple(_params(lane.mixer[0]).items()) if points and len(lane.mixer) else ()
        output = tuple(_params(lane.output_control[0]).items()) if len(lane.output_control) else ()
        rows.append(Row(tuple(cells), int(lane.in_portid), int(lane.out_portid), points[0] if points else None,
                        mixer, output))
    return Chain(tuple(rows), tuple(preset.scene_labels))


# ---- Planning the writes ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Op:
    """One write to the unit. `kind` names the call; `args` are its arguments."""
    kind: str
    args: tuple = ()


def plan_structure(current, wanted, *, sounding=(), fresh=False):
    """Disconnect what is about to change, then remove, split and add blocks.

    `sounding` are rows that may keep their input while they change, because
    the caller has made sure nothing is audible (for example a row on USB).
    `fresh` removes and adds every block, even one already in its place, so
    that each starts from the unit's own values and nothing is left over from
    what the slot held before.
    """
    def differ(a, b):
        return fresh or (a.model if a else 0) != (b.model if b else 0)

    ops = []
    for r, (now, want) in enumerate(zip(current.rows, wanted.rows)):
        changing = any(differ(a, b) and (a or b) for a, b in zip(now.cells, want.cells)) or now.split != want.split
        if changing and now.inlet and r not in sounding:
            ops.append(Op("route", (r, 0, now.outlet)))
    for r, (now, want) in enumerate(zip(current.rows, wanted.rows)):
        for column, (a, b) in enumerate(zip(now.cells, want.cells)):
            if a is not None and (b is None or differ(a, b)):
                ops.append(Op("delete", (r, column)))
    for r, (now, want) in enumerate(zip(current.rows, wanted.rows)):
        if now.split != want.split:
            ops.append(Op("split", (r, *(want.split or (-1, -1)))))
    for r, (now, want) in enumerate(zip(current.rows, wanted.rows)):
        for column, (a, b) in enumerate(zip(now.cells, want.cells)):
            if b is not None and (a is None or differ(a, b)):
                ops.append(Op("add", (r, column, b.model)))
    return ops


def plan_values(current, wanted):
    """The writes of values, assuming every wanted block is already in place.

    Values that differ by scene come last, grouped by scene, each group
    preceded by a scene switch, and the first scene is selected again at the
    end. A value held by scene on either side is written scene by scene: a
    plain write to a value the unit holds by scene lands on the selected
    scene only.
    """
    shared, assign, per_scene = [], [], {scene: [] for scene in range(SCENES)}
    for r, (now, want) in enumerate(zip(current.rows, wanted.rows)):
        for column, (a, b) in enumerate(zip(now.cells, want.cells)):
            if b is None:
                continue
            have = a.values if a is not None and a.model == b.model else {}
            for index, value in b.params:
                before = have.get(index)
                if before is not None and agrees(b.model, index, before, value):
                    continue
                if isinstance(value, tuple) or isinstance(before, tuple):
                    if isinstance(value, tuple) and not isinstance(before, tuple):
                        assign.append(Op("assign", (r, column, index)))
                    for scene in range(SCENES):
                        if before is None or not same(in_scene(before, scene), in_scene(value, scene)):
                            per_scene[scene].append(Op("param", (r, column, index, in_scene(value, scene))))
                else:
                    shared.append(Op("param", (r, column, index, value)))
            before = a.bypass if a is not None and a.model == b.model else False
            if isinstance(b.bypass, tuple) or isinstance(before, tuple):
                if isinstance(b.bypass, tuple) and not isinstance(before, tuple):
                    assign.append(Op("assign", (r, column, catalog.model(b.model).bypass_index)))
                for scene in range(SCENES):
                    if in_scene(before, scene) != in_scene(b.bypass, scene):
                        per_scene[scene].append(Op("bypass", (r, column, in_scene(b.bypass, scene))))
            elif before != b.bypass:
                shared.append(Op("bypass", (r, column, b.bypass)))
        if want.split:
            have = dict(now.mixer)
            for index, value in want.mixer:
                if index not in have or not same(have[index], value):
                    shared.append(Op("lane", (r, "mixer", index, value)))
    ops = shared + assign
    labels = [Op("label", (scene, label)) for scene, (old, label) in enumerate(zip(current.labels, wanted.labels))
              if old != label]
    for scene in range(SCENES):
        group = per_scene[scene] + [op for op in labels if op.args[0] == scene]
        if group:
            ops.append(Op("scene", (scene,)))
            ops.extend(group)
    if any(op.kind == "scene" for op in ops):
        ops.append(Op("scene", (0,)))
    return ops


def plan_levels(current, wanted):
    """Row output controls, set before anything is connected."""
    ops = []
    for r, (now, want) in enumerate(zip(current.rows, wanted.rows)):
        have = dict(now.output)
        for index, value in want.output:
            if index not in have or not same(have[index], value):
                ops.append(Op("lane", (r, "output_control", index, value)))
    return ops


def plan_routes(current, wanted):
    """Routing last. An input is connected only after the row's output and level are right."""
    ops = []
    for r, (now, want) in enumerate(zip(current.rows, wanted.rows)):
        if now.route != want.route:
            ops.append(Op("route", (r, want.inlet, want.outlet)))
    return ops


def differences(current, wanted):
    """What still differs, in words, ignoring what the wanted chain leaves unset. Empty when they agree."""
    found = []
    for r, (now, want) in enumerate(zip(current.rows, wanted.rows)):
        if now.route != want.route:
            found.append(f"row {r + 1} is routed {now.route}, wanted {want.route}")
        if now.split != want.split:
            found.append(f"row {r + 1} splits at {now.split}, wanted {want.split}")
        for column, (a, b) in enumerate(zip(now.cells, want.cells)):
            where = f"row {r + 1} column {column + 1}"
            if (a.model if a else 0) != (b.model if b else 0):
                found.append(f"{where} holds {a.name if a else 'nothing'}, wanted {b.name if b else 'nothing'}")
                continue
            if b is None:
                continue
            have = a.values
            for index, value in b.params:
                if (b.model, index) in LABELS and isinstance(have.get(index), str):
                    continue        # the unit may name the IR its own way; the IR PATH says which it is
                if index not in have or not agrees(b.model, index, have[index], value):
                    found.append(f"{where} {b.name} parameter {index} is {have.get(index)}, wanted {value}")
            if a.bypass != b.bypass:
                found.append(f"{where} {b.name} bypass is {a.bypass}, wanted {b.bypass}")
        if want.split:
            have = dict(now.mixer)
            for index, value in want.mixer:
                if index not in have or not same(have[index], value):
                    found.append(f"row {r + 1} mixer parameter {index} is {have.get(index)}, wanted {value}")
        have = dict(now.output)
        for index, value in want.output:
            if index not in have or not same(have[index], value):
                found.append(f"row {r + 1} output parameter {index} is {have.get(index)}, wanted {value}")
    for scene, (old, label) in enumerate(zip(current.labels, wanted.labels)):
        if label and old != label:
            found.append(f"scene {scene + 1} is called {old!r}, wanted {label!r}")
    return found


INTERNAL = {0, 16, 17, 18}        # ports that join rows inside the preset, or nothing


def routed(chain, inlet, outlet):
    """The chain's own routing with its instrument input replaced by `inlet` and its outputs by `outlet`.

    A row fed from an instrument input (or USB) takes `inlet`; a row that leaves
    the preset takes `outlet`; rows joined inside the preset keep their ports.
    A chain with no input anywhere gets `inlet` on its first row.
    """
    routes, fed = [], False
    for row in chain.rows:
        new_in = inlet if row.inlet not in INTERNAL else row.inlet
        new_out = outlet if row.outlet not in INTERNAL else row.outlet
        fed = fed or new_in == inlet
        routes.append((new_in, new_out))
    if not fed:
        first = next((r for r, row in enumerate(chain.rows) if row.blocks), 0)
        routes[first] = (inlet, routes[first][1] if routes[first][1] not in (0,) else outlet)
    return chain.with_routes(routes)
