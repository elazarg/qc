"""The Quad Cortex, real or simulated, behind one guarded interface.

Every rule about writing to the unit lives here, once:

- Nothing is written unless the selected preset is the one being worked on.
  It is checked before every write: a player who selects another preset on
  the unit between two writes must not have the next one land on theirs.
  Asking the unit costs about 0.1 s a write; that is the price.
- A chain is put on the unit by `apply`, which plans the writes (see
  `chain.py`), carries them out in order, then reads back until the unit
  holds what was asked for. Where the old code paused for a fixed time after
  each write, this reads back until the change is there, with a limit.
- A row's output volume never goes above `CEILING_DB`.
- A block the unit leaves out was refused for want of processing, and
  `Refused` names it.
- `stop` is honoured between any two writes.
- The unit may show a change a moment after it was written, and may leave a
  read unanswered. Every check after a write reads until the change shows,
  within `SETTLE` seconds; a read that goes unanswered inside such a wait is
  "not shown yet", and outside one it is asked once more.
- When a change fails after an input was being connected, the input is taken
  off by writing so, without first asking the unit what it holds: what it
  shows may be older than what it does.

The device library re-queues retained packets each time a reply matches, so a
polled request grows its queue without bound. `device.Device` clears it under
the library's lock around every read, `position` included.
"""

from dataclasses import replace
import time

from qc_mcp import leveling
from qc_mcp.transport import QCError

from lab.sound import catalog
from lab.sound.chain import differences, from_preset, plan_levels, plan_routes, plan_structure, plan_values, same

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
        return self.device.get_setlist_position()

    def _held(self):
        """What the unit holds now, or None when it left this read unanswered."""
        try:
            preset = self.device.get_current_preset()
        except QCError:
            return None
        return None if preset is None else from_preset(preset)

    def read(self):
        """What the unit holds. A read it leaves unanswered is asked once more."""
        for _ in range(2):
            held = self._held()
            if held is not None:
                return held
        raise RuntimeError("The Quad Cortex did not return its preset.")

    def shows(self, test, what):
        """Read the preset until `test(held)` is true and return it. A read left unanswered is not shown yet."""
        def shown():
            held = self._held()
            return held if held is not None and test(held) else None
        return self.until(shown, what)

    def name(self):
        return self.device.get_current_preset().name

    def scene(self):
        state = self.device.read_state("Scene")
        if state is None:
            raise RuntimeError("The Quad Cortex did not say which scene is selected.")
        return int(state.selected_scene)

    def dirty(self):
        """Whether the selected preset has unsaved edits: True, False, or None when the unit did not say (asked
        twice). Callers treat None as edits that must not be lost."""
        for _ in range(2):
            try:
                state = self.device.read_state("PresetDirty")
            except QCError:
                state = None
            if state is not None:
                return bool(getattr(state, "is_dirty", getattr(state, "dirty", False)))
        return None

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
        elif kind == "expression":
            device.assign_expression(*args)
        elif kind == "stomp":
            device.assign_stomp(*args, settle_s=0)
        elif kind == "unstomp":
            device.unassign_stomp(*args, settle_s=0)
        elif kind == "momentary":
            device.set_stomp_momentary(*args)
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
        """Carry out writes, checking the selected preset before each one."""
        for op in ops:
            if stop is not None and stop.is_set():
                raise Stopped("Stopped.")
            self.guard()
            self.execute(op)

    def apply(self, wanted, *, stop=None, sounding=(), fresh=False, current=None):
        """Make the unit hold `wanted`, and return what it holds afterwards.

        `sounding` are rows that may stay connected while their blocks change.
        `fresh` adds every block anew, so that values the chain leaves unset are
        the unit's own and not what the slot held before. `current` is what the
        unit holds when the caller has just read it, to save reading it again.
        """
        wanted = bounded(wanted)
        current = self.read() if current is None else current
        structure = plan_structure(current, wanted, sounding=sounding, fresh=fresh)
        self.run(structure, stop)
        if fresh:
            current = self.read()
        if structure:
            def placed(held):
                return all((held.block(r, c).model if held.block(r, c) else 0) == (b.model if b else 0)
                           for r, row in enumerate(wanted.rows) for c, b in enumerate(row.cells))
            try:
                current = self.shows(placed, "every block in place")
            except Mismatch:
                self.guard()  # A block missing from another preset was not refused: the player moved.
                held = self.read()
                missing = [b.name for r, row in enumerate(wanted.rows) for c, b in enumerate(row.cells)
                           if b is not None and (held.block(r, c) is None or held.block(r, c).model != b.model)]
                if missing:
                    raise Refused(f"The Quad Cortex has no processing left for {missing[0]}. "
                                  "Remove a block or choose a lighter one.") from None
                raise
        for attempt in range(2):
            ops = plan_values(current, wanted)
            if not ops:
                break
            self.run(ops, stop)
            try:
                # The values are waited for as the blocks are. One the unit has not shown by then is written
                # once more, and the check at the end decides.
                current = self.shows(lambda held: not plan_values(held, wanted), "the values")
            except Mismatch:
                current = self.read()
        levels = plan_levels(current, wanted)
        self.run(levels, stop)
        routes = plan_routes(current, wanted)
        # Every row's level must be on the unit before any input is connected: a level the unit did not take
        # would play the sound at whatever the preset holds. A mute needs no such check: one the unit did not
        # take leaves a row silent, or its input is taken off by the routes that follow and checked below.
        if routes and any(op.args[2] == catalog.LANE_VOLUME for op in levels):
            def unlevelled(held):
                return [r for r, want in enumerate(wanted.rows) for index, value in want.output
                        if index not in dict(held.rows[r].output) or not same(dict(held.rows[r].output)[index], value)]
            try:
                self.shows(lambda held: not unlevelled(held), "the levels")
            except Mismatch:
                row = (unlevelled(self.read()) or [0])[0]
                raise Mismatch(f"The Quad Cortex did not take the level of row {row + 1}, so nothing was "
                               "connected.") from None
        try:
            self.run(routes, stop)
            self.guard()
            try:
                held = self.shows(lambda held: not differences(held, wanted), "the chain")
            except Mismatch:
                wrong = differences(self.read(), wanted)
                raise Mismatch("The Quad Cortex did not confirm the chain: " + "; ".join(wrong[:3])) from None
        except Exception:
            self._disconnect(routes)
            raise
        return held

    def _disconnect(self, routes):
        """After a failure: take the input off every row this change was connecting, as far as the unit lets.
        Written without asking what the unit holds first: a unit that has not yet shown the connection would
        be taken to have none, and left sounding."""
        try:
            for op in routes:
                row, inlet, outlet = op.args
                self.guard()
                self.device.set_routing(row, in_portid=inlet if inlet in (16, 17, 18) else 0, out_portid=outlet)
        except Exception:  # noqa: BLE001
            pass
