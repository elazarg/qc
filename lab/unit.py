"""The Quad Cortex, real or simulated, behind one guarded interface.

Every rule about writing to the unit lives here, once:

- Nothing is written unless the selected preset is the one being worked on.
  It is checked before each block, before each scene, and before each group
  of values. Asking the unit costs about 0.1 s, so it is not checked before
  every single value.
- A chain is put on the unit by `apply`, which plans the writes (see
  `chain.py`), carries them out in order, then reads back until the unit
  holds what was asked for. Where the old code paused for a fixed time after
  each write, this reads back until the change is there, with a limit.
- A row's output volume never goes above `CEILING_DB`.
- A block the unit leaves out was refused for want of processing, and
  `Refused` names it.
- `stop` is honoured between any two writes.

The device library re-queues retained packets each time a reply matches, so a
polled request grows its queue without bound. `position` clears it under the
library's lock, as the old app learned to.
"""

from dataclasses import replace
import time

from qc_mcp import leveling

from . import catalog
from .chain import Chain, Op, differences, from_preset, plan_levels, plan_routes, plan_structure, plan_values

CEILING_DB = 6.        # a row's output volume, in dB, never goes above this
SETTLE = 3.            # seconds to wait for the unit to show a change before giving up
POLL = .05


class Moved(RuntimeError):
    """The selected preset is not the one being worked on. Nothing more was written."""


class Refused(RuntimeError):
    """The unit did not accept a block, which it does silently when its processing is full."""


class Stopped(RuntimeError):
    """The work was stopped between two writes."""


class Mismatch(RuntimeError):
    """The unit did not end up holding what was asked for."""


def bounded(chain):
    """The chain with no row's output volume above the ceiling."""
    top = leveling.norm_of(CEILING_DB)
    rows = []
    for row in chain.rows:
        output = dict(row.output)
        if catalog.LANE_VOLUME in output and not isinstance(output[catalog.LANE_VOLUME], tuple):
            output[catalog.LANE_VOLUME] = min(output[catalog.LANE_VOLUME], top)
        rows.append(replace(row, output=tuple(output.items())))
    return replace(chain, rows=tuple(rows))


def same_slot(a, b):
    return (bool(a) and bool(b) and a.get("folder_key") == b.get("folder_key")
            and int(a.get("position", -1)) == int(b.get("position", -2)))


class Unit:
    def __init__(self, device, *, pace=.0, settle=SETTLE, clock=time.monotonic, sleep=time.sleep):
        self.device = device
        self.pace = pace          # seconds between two writes; the real unit has not needed more than this
        self.settle = settle
        self.clock, self.sleep = clock, sleep
        self.slot = None          # the slot being worked on; writes are refused anywhere else
        self.firmware = getattr(device, "firmware", None)

    # ---- reading ----

    def position(self):
        lock = getattr(self.device, "_io_lock", None)
        pending = getattr(self.device, "_pending", None)
        if lock is None or pending is None:
            return self.device.get_setlist_position()
        with lock:
            pending.clear()
            try:
                return self.device.get_setlist_position()
            finally:
                pending.clear()

    def read(self):
        preset = self.device.get_current_preset()
        if preset is None:
            raise RuntimeError("The Quad Cortex did not return its preset.")
        return from_preset(preset)

    def name(self):
        return self.device.get_current_preset().name

    def scene(self):
        state = self.device.read_state("Scene")
        if state is None:
            raise RuntimeError("The Quad Cortex did not say which scene is selected.")
        return int(state.selected_scene)

    def dirty(self):
        state = self.device.read_state("PresetDirty")
        return bool(getattr(state, "dirty", getattr(state, "is_dirty", False))) if state is not None else False

    def until(self, test, what):
        """Read until `test()` is true. Raise when the unit has not shown it within `settle` seconds."""
        deadline = self.clock() + self.settle
        while True:
            result = test()
            if result:
                return result
            if self.clock() > deadline:
                raise Mismatch(f"The Quad Cortex did not show {what}.")
            self.sleep(POLL)

    # ---- moving between presets ----

    def guard(self):
        if self.slot is not None and not same_slot(self.position(), self.slot):
            raise Moved("The selected preset changed on the Quad Cortex. Nothing more was written to it.")

    def recall(self, slot):
        self.device.recall(folder_key=slot["folder_key"], position=slot["position"],
                           is_factory=slot.get("is_factory", False))
        self.until(lambda: same_slot(self.position(), slot), "the recalled preset")
        return self.read()

    def select_scene(self, scene):
        self.device.set_scene(scene)
        self.until(lambda: self.scene() == scene, f"scene {'ABCDEFGH'[scene]}")

    def store(self, slot, name):
        """Save the working preset into a slot under a name, and confirm it by recalling it."""
        if not name.strip() or len(name) > 24 or any(c in name for c in '\\/:*?"<>|\r\n'):
            raise ValueError("A preset name is up to 24 characters without file path punctuation.")
        self.guard()
        built = self.read()
        self.device.write_preset_file(slot["folder_key"], slot["position"], name)
        self.until(lambda: same_slot(self.position(), slot), "the saved preset selected")
        stored = self.recall(slot)
        wrong = differences(stored, built)
        if wrong:
            raise Mismatch("The saved preset did not match what was built: " + "; ".join(wrong[:3]))
        self.slot = slot
        return stored

    # ---- writing ----

    def execute(self, op):
        kind, args = op.kind, op.args
        device = self.device
        if kind == "route":
            device.set_routing(args[0], in_portid=args[1], out_portid=args[2])
        elif kind == "delete":
            device.delete_block(*args)
        elif kind == "add":
            row, column, model_id = args
            device.add_block(model_id, row=row, column=column)
        elif kind == "split":
            device.set_split_points(*args)
        elif kind == "param":
            row, column, index, value = args
            if isinstance(value, str):
                device.set_param_typed(row, column, index, [[None, None, value]])
            else:
                device.set_param(row, column, index, value)
        elif kind == "assign":
            device.assign_param_to_scenes(*args)
        elif kind == "bypass":
            row, column, flag = args
            device.set_block_bypass(row, column, bool(flag))
        elif kind == "lane":
            row, field, index, value = args
            if field == "output_control" and index == catalog.LANE_VOLUME:
                value = min(value, leveling.norm_of(CEILING_DB))
            device.set_lane_param(row, field, index, value)
        elif kind == "label":
            device.set_scene_label(*args)
        elif kind == "scene":
            self.select_scene(args[0])
        else:
            raise ValueError(f"Unknown write {kind}.")
        if self.pace:
            self.sleep(self.pace)

    def run(self, ops, stop=None):
        """Carry out writes, checking the selected preset before each block and each scene."""
        for position, op in enumerate(ops):
            if stop is not None and stop.is_set():
                raise Stopped("Stopped.")
            if position == 0 or op.kind in ("add", "delete", "scene", "split") or ops[position - 1].kind == "scene":
                self.guard()
            self.execute(op)

    def apply(self, wanted, *, stop=None, sounding=(), fresh=False):
        """Make the unit hold `wanted`, and return what it holds afterwards.

        `sounding` are rows that may stay connected while their blocks change.
        `fresh` adds every block anew, so that values the chain leaves unset are
        the unit's own and not what the slot held before.
        """
        wanted = bounded(wanted)
        current = self.read()
        structure = plan_structure(current, wanted, sounding=sounding, fresh=fresh)
        self.run(structure, stop)
        if fresh:
            current = self.read()
        if structure:
            def placed():
                held = self.read()
                return held if all((held.block(r, c).model if held.block(r, c) else 0) == (b.model if b else 0)
                                   for r, row in enumerate(wanted.rows) for c, b in enumerate(row.cells)) else None
            try:
                current = self.until(placed, "every block in place")
            except Mismatch:
                self.guard()  # A block missing from another preset was not refused: the player moved.
                held = self.read()
                missing = [b.name for r, row in enumerate(wanted.rows) for c, b in enumerate(row.cells)
                           if b is not None and (held.block(r, c) is None or held.block(r, c).model != b.model)]
                if missing:
                    raise Refused(f"The Quad Cortex has no processing left for {missing[0]}. "
                                  "Remove a block or choose a lighter one.") from None
                raise
        self.guard()
        for attempt in range(2):
            ops = plan_values(current, wanted)
            if not ops:
                break
            self.run(ops, stop)
            current = self.read()
        self.run(plan_levels(current, wanted), stop)
        self.run(plan_routes(current, wanted), stop)
        self.guard()
        held = self.read()
        wrong = differences(held, wanted)
        if wrong:
            raise Mismatch("The Quad Cortex did not confirm the chain: " + "; ".join(wrong[:3]))
        return held
