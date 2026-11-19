"""Everything the app does with the unit during a session, in one place, in order.

The engine holds the unit for the session. It is driven by a single caller,
one command at a time (the desk talks to it through `host.py`, which runs it
in a process of its own so that a driver failure cannot take the desk down).

Two sounds, A and B, are compared on one preset when each fits in two rows:
A on rows 1 and 2, B on rows 3 and 4. Each pair of rows has its own two
processors, so two sounds that each fit alone always fit together, and
switching between them is moving the input from one row to the other, about
20 ms, where recalling another preset takes seconds. The pair is built once
and kept in the cache, like any chain. A sound that needs more than two rows,
such as a large saved preset, is compared by recalling its own slot instead.

Every change follows the unit's rules (see `unit.py`): the output level and
mute of a row are set before its input is connected, nothing is written to a
preset the player has selected on the unit, and the player's preset is given
back when the session ends, or after a crash when the app next starts.
"""

from dataclasses import replace

from qc_mcp import leveling

from . import catalog
from .chain import COLUMNS, Chain, Row, routed
from .listen import calibrate, calibration, listen
from .rig import Rig
from .unit import same_slot

MULTI_OUT, USB_IN, USB_OUT = 19, 12, 14
PREVIEW_DB = -12.     # the level of a sound nobody has measured yet
MUTE, VOLUME = catalog.LANE_MUTE, catalog.LANE_VOLUME


def rows_used(chain):
    return [r for r, row in enumerate(chain.rows) if row.blocks or row.inlet or row.split]


def fits_two_rows(chain):
    used = rows_used(chain)
    return not used or max(used) <= 1


def place(chain, at):
    """The chain moved down to start at row `at` (0 or 2). Only a chain on rows 0 and 1 can move."""
    if at == 0:
        return chain
    rows = (Row(), Row()) + tuple(chain.rows[:2])
    return replace(chain, rows=rows)


def pair(a, b):
    """A on rows 0 and 1, B on rows 2 and 3, as one preset. Scenes are A's."""
    rows = tuple(a.rows[:2]) + tuple(place(b, 2).rows[2:])
    return Chain(rows, a.labels)


class Engine:
    def __init__(self, unit, slots, audio, *, physical_input=1):
        self.unit, self.slots, self.audio = unit, slots, audio
        self.rig = Rig(unit, slots)
        self.physical_input = physical_input
        self.sides = {"a": None, "b": None}     # the chains being compared, without knob moves
        self.knobs = {"a": {}, "b": {}}         # stored values each side's knobs have changed: {(row, col, index): value}
        self.bases = {"a": None, "b": None}     # each side as the unit holds it, every value stated, from row 0
        self.levels = {"a": PREVIEW_DB, "b": PREVIEW_DB}
        self.shown = None                       # what is on the unit now: ("pair", chain) or ("single", side, chain)
        self.active, self.mode, self.loop = "a", None, None
        self.borrowed = None
        self.offset = None

    # ---- the session ----

    def open(self):
        """Give back anything a crash left borrowed, then note the player's preset before anything else is loaded."""
        recovered = self.slots.recover()
        self.slots.refresh()
        self.borrowed = self.slots.note_borrowed()
        self.offset = calibration(self.slots.folder, self.unit.firmware)
        return {"recovered": recovered, "borrowed": self.borrowed, "calibrated": self.offset is not None}

    def close(self):
        self.quiet()
        if self.borrowed is not None:
            self.slots.give_back(self.borrowed)
            self.borrowed = None

    # ---- what is shown ----

    def show(self, a, b, *, stop=None):
        """Make A and B ready to play. Returns how they are compared: "pair" or "recall"."""
        self.quiet()
        self.sides = {"a": a, "b": b}
        self.knobs = {"a": {}, "b": {}}
        both = [chain for chain in (a, b) if chain is not None]
        if all(fits_two_rows(chain) for chain in both):
            combined = pair(a or Chain(), b or Chain())
            slot, held = self.rig.ready(combined, stop=stop)
            self.shown = ("pair", slot, held)
            for side, first in (("a", 0), ("b", 2)):
                self.bases[side] = Chain(held.rows[first:first + 2], held.labels) if self.sides[side] else None
            return "pair"
        for side in ("a", "b"):
            if self.sides[side] is not None:
                slot, held = self.rig.ready(self.sides[side], stop=stop)
                self.bases[side] = held
        self.shown = ("recall", None, None)
        return "recall"

    def _first_row(self, side):
        return 0 if side == "a" or self.shown[0] != "pair" else 2

    def _rows(self, side):
        first = self._first_row(side)
        return (first, first + 1)

    # ---- playing ----

    def play(self, side, mode, phrase=None):
        """Hear one side: from the guitar (`mode` "guitar") or from the recorded phrase ("phrase")."""
        if self.shown is None:
            if not any(self.sides.values()):
                raise RuntimeError("Nothing is ready to play.")
            self.show(self.sides["a"], self.sides["b"])   # Listening or saving used the unit meanwhile.
        if self.shown[0] == "recall" and side != self.active or self.shown[0] == "recall" and self.mode is None:
            self._recall_side(side)
        port = USB_IN if mode == "phrase" else self.physical_input
        held = self.unit.read()
        if self.shown[0] == "recall":
            # One sound on its own preset, played with its own routing: a preset spread over several rows whole.
            wanted = held.with_routes(routed(self.sides[side], port, MULTI_OUT).routes)
            level = leveling.norm_of(self.levels[side])
            for r, row in enumerate(wanted.rows):
                if row.outlet not in (0, 16, 17, 18):
                    wanted = wanted.with_row(r, replace(row, output=tuple({**dict(row.output), VOLUME: level,
                                                                           MUTE: 0.}.items())))
            self.unit.apply(wanted)
            self._loop(mode, phrase)
            self.active, self.mode = side, mode
            return {"side": side, "mode": mode}
        wanted = held
        for name in ("a", "b"):
            if self.shown[0] != "pair" and name != side:
                continue
            first = self._first_row(name)
            on = name == side
            level = leveling.norm_of(self.levels[name])
            row = replace(wanted.rows[first], inlet=port if on else 0, outlet=MULTI_OUT,
                          output=tuple({**dict(wanted.rows[first].output), VOLUME: level, MUTE: 0. if on else 1.}.items()))
            wanted = wanted.with_row(first, row)
        self.unit.apply(wanted)
        self._loop(mode, phrase)
        self.active, self.mode = side, mode
        return {"side": side, "mode": mode}

    def _loop(self, mode, phrase):
        if mode == "phrase" and self.loop is None and phrase is not None:
            from .audio import Loop

            self.loop = Loop(self.audio, phrase).start()
        if mode != "phrase" and self.loop is not None:
            self.loop.stop()
            self.loop = None

    def _recall_side(self, side):
        chain = self.sides[side]
        slot, held = self.rig.ready(chain)
        self.knobs[side] = {}
        self.shown = ("recall", slot, held)

    def quiet(self):
        """Disconnect every input, keeping the place in the phrase."""
        if self.loop is not None:
            self.loop.stop()
            self.loop = None
        if self.shown is not None and self.mode is not None and self.slots.ours(self.unit.position()):
            held = self.unit.read()
            self.unit.apply(held.with_routes([(0, row.outlet) for row in held.rows]))
        self.mode = None

    # ---- shaping ----

    def turn(self, side, values, scene=None):
        """Set one side's Shape knobs. Returns the edits made, in display units, for the desk to show."""
        from . import knobs

        # The knobs work on the side as the unit holds it, where every value is stated.
        chain = self.bases[side]
        if chain is None:
            return []
        targets = knobs.targets(chain, values, scene=scene)
        first = self._first_row(side) if self.shown and self.shown[0] == "pair" else 0
        wanted = {}
        for (row, column, index), value in targets.items():
            wanted[(row + first, column, index)] = value
        # Values a knob moved earlier and no longer moves go back to the sound's own.
        for key in self.knobs[side]:
            if key not in wanted:
                row, column, index = key
                block = chain.block(row - first, column)
                if index is None:
                    wanted[key] = block.bypass
                else:
                    wanted[key] = block.values.get(index)
        held = self.unit.read()
        changed = held
        for (row, column, index), value in wanted.items():
            block = changed.block(row, column)
            if block is None:
                continue
            block = replace(block, bypass=value) if index is None else block.set(index, value)
            changed = changed.with_block(row, column, block)
        self.unit.apply(changed)
        self.knobs[side] = {key: value for key, value in wanted.items() if key in {
            (row + first, column, index) for (row, column, index) in targets}}
        return knobs.moves(chain, values)

    def level(self, side, db):
        self.levels[side] = max(-40., min(6., db))
        if self.mode is not None and side == self.active:
            self.play(side, self.mode)

    # ---- listening, reading, saving ----

    def measure_round_trip(self):
        self.quiet()
        self.offset = calibrate(self.rig, self.audio, self.slots.folder)
        self.shown = None
        return self.offset

    def hear(self, chain, dry, *, stop=None):
        """What `chain` does to the phrase, measured silently. Whatever was playing stops."""
        self.quiet()
        if self.offset is None:
            self.measure_round_trip()
        found = listen(self.rig, self.audio, chain, dry, self.offset, stop=stop)
        self.shown = None
        return found

    def read(self, slot):
        """The chain and name of a preset saved on the unit."""
        self.quiet()
        self.unit.slot = None
        held = self.unit.recall(slot)
        name = self.unit.name()
        self.shown = None
        return held, name

    def save(self, chain, name):
        """Write a sound to the next free slot of the Tone Lab setlist, ready to play with the guitar."""
        self.quiet()
        target = self.slots.free_for_saving()
        playable = chain.with_routes([(self.physical_input, MULTI_OUT) if r == 0 else row.route
                                      for r, row in enumerate(chain.rows)])
        playable = replace(playable, rows=(replace(playable.rows[0], output=tuple(
            {**dict(playable.rows[0].output), VOLUME: leveling.norm_of(0.), MUTE: 0.}.items())),) + playable.rows[1:])
        self.slots.claim(target, name)
        self.unit.slot = None
        self.rig.build(playable)            # assembled and cached silent
        self.unit.apply(playable)          # then connected as it will be played
        stored = self.unit.store(target, name)
        self.shown = None
        return target, stored

    def record(self, seconds, *, stop=None):
        """Record the dry guitar from its input, as it reaches the unit before any processing."""
        self.quiet()
        dry_channel = self.physical_input
        if hasattr(self.unit.device, "read_message"):
            from .device import recording_channels

            # Which USB channels carry the dry input depends on the unit's USB Dry/Wet setting.
            dry_channel = recording_channels(self.unit.device, self.physical_input)[0]
        return self.audio.record(seconds, [dry_channel], cancel=stop)

