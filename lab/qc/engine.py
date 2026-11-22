"""Everything the app does with the unit during a session, in one place, in order.

The engine holds the unit for the session. It is driven by a single caller,
one command at a time (the desk talks to it through `host.py`, which runs it
in a process of its own so that a driver failure cannot take the desk down).

Two sounds, A and B, are compared on one preset when each fits in two rows:
A on rows 1 and 2, B on rows 3 and 4. Each pair of rows has its own two
processors, so two sounds that each fit alone always fit together, and
switching between them is moving the input from one row to the other, about
20 ms, where recalling another preset takes seconds. The pair is built once
and kept in the cache, like any chain. A sound that needs more than two rows
or has an expression or stomp assignment is compared by recalling its own slot.

Every change follows the unit's rules (see `unit.py`): the output level and
mute of a row are set before its input is connected, nothing is written to a
preset the player has selected on the unit, and the player's preset is given
back when the session ends, or after a crash when the app next starts.
"""

from dataclasses import replace

from qc_mcp import leveling

from lab.sound import catalog
from lab.sound.chain import INTERNAL, MULTI_OUT, Chain, Row, routed
from lab.hearing.listen import RoundTripChanged, calibrate, calibration, listen
from lab.qc.rig import Rig
from lab.qc.unit import CEILING_DB, same_slot

USB_IN = 12
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
        self.wanted = {"a": None, "b": None}    # each side's knob positions as last asked: (values, scene)
        self.on_unit = None                     # comparing by recall: the side whose preset the unit holds
        self.bases = {"a": None, "b": None}     # each side as the unit holds it, every value stated, from row 0
        # What each side is played at, in dB against the sound's own output gain (a preset's authored levels
        # are kept; matching and listening move them together).
        self.levels = {"a": PREVIEW_DB, "b": PREVIEW_DB}
        self.shown = None                       # what is on the unit now: ("pair", chain) or ("single", side, chain)
        self.active, self.mode, self.loop = "a", None, None
        self.loop_phrase = None                 # the audio the loop is playing
        self.connected = False                  # an input was connected by something other than play
        self.visited = None                     # a preset of the player's the app itself moved the unit to
        self.chosen_scene = None                # the scene heard, kept across making ready and recalling
        self.borrowed = None
        self.offset = None
        self.asked, self.peak_limits, self.listening = {}, {}, None

    # ---- the session ----

    def open(self):
        """Give back anything a crash left borrowed, then note the player's preset before anything else is loaded."""
        recovered = self.slots.recover()
        self.slots.refresh()
        self.slots.may_leave()                  # unsaved edits on the player's preset: wait, touch nothing
        self.borrowed = self.slots.note_borrowed()
        self.offset = calibration(self.slots.folder, self.unit.firmware)
        return {"recovered": recovered, "borrowed": self.borrowed, "calibrated": self.offset is not None,
                "firmware": self.unit.firmware}

    def close(self):
        self.quiet()
        if self.borrowed is not None:
            self.slots.give_back(self.borrowed, also=self.visited)
            self.borrowed = None

    # ---- what is shown ----

    def show(self, a, b, *, stop=None):
        """Make A and B ready to play. Returns how they are compared: "pair" or "recall"."""
        self.quiet()
        for side, chain in (("a", a), ("b", b)):
            if chain != self.sides[side]:
                self.wanted[side] = None        # another sound: its knobs are asked for again
        self.sides = {"a": a, "b": b}
        self.knobs = {"a": {}, "b": {}}
        self.on_unit = None
        both = [chain for chain in (a, b) if chain is not None]
        if all(fits_two_rows(chain) and not chain.performance_controls for chain in both):
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
                self.on_unit = side
        self.shown = ("recall", None, None)
        return "recall"

    def _own_db(self, side, row):
        """A row's own output gain in the sound as the unit holds it, in dB (0 when the sound leaves it unset)."""
        base = self.bases.get(side)
        if base is None or row >= len(base.rows):
            return 0.
        value = dict(base.rows[row].output).get(VOLUME)
        return leveling.db_of(value) if isinstance(value, (int, float)) else 0.

    def _own_mute(self, side, row):
        """A row's own mute in the sound as the unit holds it: a lane its author muted stays muted when heard."""
        base = self.bases.get(side)
        if base is None or row >= len(base.rows):
            return 0.
        value = dict(base.rows[row].output).get(MUTE)
        return 1. if isinstance(value, (int, float)) and value >= .5 else 0.

    def _on_shown(self):
        """Whether the unit is on the slot A and B were made ready on."""
        return (self.shown is not None and self.shown[1] is not None
                and same_slot(self.unit.position(), self.shown[1]))

    def _first_row(self, side):
        return 0 if side == "a" or self.shown[0] != "pair" else 2

    def _rows(self, side):
        first = self._first_row(side)
        return (first, first + 1)

    # ---- playing ----

    def play(self, side, mode, phrase=None):
        """Hear one side: from the guitar (`mode` "guitar") or from the recorded phrase ("phrase")."""
        if self.shown is not None and self.shown[0] == "pair" and not self._on_shown():
            self.shown = None           # the unit is elsewhere: the prepared A/B rows are not there to route
        if self.shown is None:
            if not any(self.sides.values()):
                raise RuntimeError("Nothing is ready to play.")
            self.show(self.sides["a"], self.sides["b"])   # Listening or saving used the unit meanwhile.
        if self.shown[0] == "recall" and (side != self.on_unit or self.mode is None):
            self._recall_side(side)
        try:
            return self._play(side, mode, phrase)
        except Exception:
            self.quiet()          # a scene or audio that would not start: nothing stays connected
            raise

    def _play(self, side, mode, phrase):
        port = USB_IN if mode == "phrase" else self.physical_input
        held = self.unit.read()
        if self.shown[0] == "recall":
            # One sound on its own preset, played with its own routing: a preset spread over several rows whole.
            wanted = held.with_routes(routed(self.sides[side], port, MULTI_OUT).routes)
            for r, row in enumerate(wanted.rows):
                if row.outlet not in (0, 16, 17, 18):
                    level = leveling.norm_of(self._own_db(side, r) + self.levels[side])
                    wanted = wanted.with_row(r, replace(row, output=tuple({**dict(row.output), VOLUME: level,
                                                                           MUTE: self._own_mute(side, r)}.items())))
            self.connected = True             # from the first route set, until `quiet`
            self.unit.apply(wanted, current=held)
            self._keep_scene()
            self._loop(mode, phrase)
            self.active, self.mode = side, mode
            return {"side": side, "mode": mode}
        wanted = held
        for name in ("a", "b"):
            if self.sides[name] is None:
                continue
            # Each side with its own routing over its two rows (a row feeding the next stays joined); the side
            # not heard has no input and its outputs muted.
            first = self._first_row(name)
            on = name == side
            routes = routed(self.sides[name], port if on else 0, MULTI_OUT).routes
            for offset in (0, 1):
                inlet, outlet = routes[offset] if offset < len(routes) else (0, 0)
                if not on and inlet not in INTERNAL:
                    inlet = 0
                row = wanted.rows[first + offset]
                output = dict(row.output)
                if outlet not in INTERNAL:
                    level = leveling.norm_of(self._own_db(name, offset) + self.levels[name])
                    output.update({VOLUME: level, MUTE: self._own_mute(name, offset) if on else 1.})
                wanted = wanted.with_row(first + offset, replace(row, inlet=inlet, outlet=outlet,
                                                                 output=tuple(output.items())))
        self.connected = True
        self.unit.apply(wanted, current=held)
        self._keep_scene()
        self._loop(mode, phrase)
        self.active, self.mode = side, mode
        return {"side": side, "mode": mode}

    def _loop(self, mode, phrase):
        """Keep the phrase looping while switching sides; start it again when the phrase itself changed."""
        import numpy as np

        if mode == "phrase" and phrase is not None:
            same = (self.loop is not None and self.loop_phrase is not None and self.loop_phrase.shape == phrase.shape
                    and np.array_equal(self.loop_phrase, phrase))
            if not same:
                from lab.hearing.audio import Loop

                if self.loop is not None:
                    self.loop.stop()
                self.loop = self.loop_phrase = None       # nothing cached if the new one fails to start
                self.loop = Loop(self.audio, phrase).start()
                self.loop_phrase = phrase
        if mode != "phrase" and self.loop is not None:
            self.loop.stop()
            self.loop = self.loop_phrase = None

    def _on_ours(self):
        """Whether the unit is still on the slot the app made ready (the player may have chosen another)."""
        return self.unit.slot is not None and same_slot(self.unit.position(), self.unit.slot)

    def watch(self):
        """While something plays: if the player chose another preset on the unit, stop sending audio at once and
        leave that preset alone. Returns {"moved": True} when that happened."""
        if self.mode is None:
            return {"moved": False}
        if self._on_ours():
            return {"moved": False}
        if self.loop is not None:
            self.loop.stop()
            self.loop = self.loop_phrase = None
        self.mode, self.shown = None, None
        return {"moved": True}

    def _recall_side(self, side):
        chain = self.sides[side]
        self.on_unit = None
        slot, held = self.rig.ready(chain)
        self.knobs[side] = {}
        self.shown = ("recall", slot, held)
        self.on_unit = side
        if self.wanted[side] is not None and self.wanted[side][0]:
            self.turn(side, *self.wanted[side])   # turned while the other side was on the unit

    def scene(self, scene):
        """Choose the scene heard (0 to 7). It is kept when A and B are made ready again or recalled."""
        self.chosen_scene = scene
        if self.shown is not None and self._on_ours():
            self.unit.select_scene(scene)
        return scene

    def _keep_scene(self):
        chosen = self.chosen_scene
        if chosen is not None and self.unit.scene() != chosen:
            self.unit.select_scene(chosen)

    def quiet(self):
        """Disconnect every input, keeping the place in the phrase."""
        if self.loop is not None:
            self.loop.stop()
            self.loop = self.loop_phrase = None
        if (self.mode is not None or self.connected) and self.slots.ours(self.unit.position()):
            held = self.unit.read()
            if any(row.inlet not in INTERNAL for row in held.rows):
                self.unit.apply(held.with_routes([(0 if row.inlet not in INTERNAL else row.inlet, row.outlet)
                                                  for row in held.rows]))
        self.mode, self.connected = None, False

    # ---- shaping ----

    def turn(self, side, values, scene=None):
        """Set one side's Shape knobs. Returns the edits made, in display units, for the desk to show."""
        from lab.sound import knobs

        # The knobs work on the side as the unit holds it, where every value is stated.
        chain = self.bases[side]
        if chain is None:
            return []
        self.wanted[side] = (dict(values), scene)
        if self.shown is None or (self.shown[0] == "recall" and (self.on_unit != side or not self._on_ours())):
            # That side's preset is not the one on the unit: the knobs go on when it is recalled.
            return knobs.moves(chain, values)
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
        # A scene-assigned knob writes several scenes. Keep the one actually playing on the unit.
        self.unit.apply(changed, current=held, preserve_scene=True)
        self.knobs[side] = {key: value for key, value in wanted.items() if key in {
            (row + first, column, index) for (row, column, index) in targets}}
        return knobs.moves(chain, values)

    def level(self, side, db, *, peak_limit=None):
        """Set the level a side is played at. `listening`, when set, maps what is asked to what is played (the
        Levels window's listening level); the result stays within the floor and the ceiling."""
        self.asked[side] = db
        self.peak_limits[side] = peak_limit
        played = self.listening(db) if self.listening else db
        base = self.bases.get(side)
        loudest = max((self._own_db(side, r) for r, row in enumerate(base.rows) if row.outlet not in INTERNAL),
                      default=0.) if base else 0.
        ceiling = CEILING_DB - round(loudest, 4)
        if peak_limit is not None:
            ceiling = min(ceiling, peak_limit)
        before, self.levels[side] = self.levels[side], max(-40., min(ceiling, played))
        # A level that is already the one played is not written again: the desk matches levels at every play.
        if self.mode is not None and side == self.active and abs(self.levels[side] - before) > .05:
            self.play(side, self.mode)

    def listen_at(self, listening):
        """Play every side through `listening` (asked dB -> played dB) from now on, or as asked when None."""
        self.listening = listening
        for side in ("a", "b"):
            self.level(side, self.asked.get(side, PREVIEW_DB), peak_limit=self.peak_limits.get(side))
        return dict(self.levels)

    # ---- listening, reading, saving ----

    def measure_round_trip(self):
        self.quiet()
        self.shown = None
        self.offset = calibrate(self.rig, self.audio, self.slots.folder)
        return self.offset

    def hear(self, chain, dry, scene=0, *, stop=None):
        """What `chain` does to the phrase in `scene`, measured silently. Whatever was playing stops."""
        self.quiet()
        self.shown = None               # the unit is borrowed: A and B must be made ready again, also after a failure
        if self.offset is None:
            self.measure_round_trip()
        return self._listen(chain, dry, stop, scene)

    def _listen(self, chain, dry, stop, scene):
        """Listen once; when the test click says the USB round trip is no longer what was measured (another buffer
        size, another driver), measure it again and listen once more."""
        try:
            return listen(self.rig, self.audio, chain, dry, self.offset, stop=stop, scene=scene)
        except RoundTripChanged:
            self.measure_round_trip()
            return listen(self.rig, self.audio, chain, dry, self.offset, stop=stop, scene=scene)

    def hear_shaped(self, chain, knobs, dry, scene=0, *, stop=None):
        """What `chain` with its Shape knobs at `knobs` does to the phrase, measured silently, as `hear` does.

        As `save` does, the knobs are applied to the chain as the unit holds it once built, where every value is
        stated; the shaped chain is then listened to like any other (built once and cached)."""
        from lab.sound.knobs import apply as with_knobs

        self.quiet()
        self.shown = None
        if self.offset is None:
            self.measure_round_trip()
        slot, held = self.rig.ready(chain, stop=stop)
        shaped = with_knobs(held, knobs) if knobs else held      # as the desk turns them: every scene moved
        return self._listen(shaped, dry, stop, scene)

    def read(self, slot):
        """The chain and name of a preset saved on the unit."""
        self.quiet()
        self.slots.may_leave()
        self.shown = None
        self.unit.slot = None
        held = self.unit.recall(slot)
        name = self.unit.name()
        self.visited = slot                 # the app moved the unit here, so it may move it back on close
        return held, name

    def save(self, chain, name, knobs=None):
        """Write a sound to the next free slot of the Tone Lab setlist, ready to play with the guitar.

        `knobs` are the sound's Shape knob positions. They are applied to the chain as the unit holds it once
        built, where every value is stated: a design leaves most values to the unit, and a knob can only move
        a value that is there."""
        self.quiet()
        self.shown = None
        target = self.slots.free_for_saving()
        # Its own routing, from the guitar input to the unit's outputs: a preset spread over several rows whole.
        # Every row's own level and mute are kept as the sound has them.
        playable = chain.with_routes(routed(chain, self.physical_input, MULTI_OUT).routes)
        self.slots.claim(target, name)
        self.unit.slot = None
        self.rig.build(playable)            # assembled and cached silent
        if knobs:
            from lab.sound.knobs import apply as with_knobs

            held = self.unit.read()
            playable = with_knobs(held.with_routes(playable.routes), knobs)
        # Building took a while: the slot must still be empty (or the app's own) before anything is connected.
        self.slots.refresh()
        self.slots.claim(target, name)
        self.connected = True
        try:
            self.unit.apply(playable)          # connected as it will be played, and stored so
            stored = self.unit.store(target, name)
        except Exception as error:
            # The write may have gone through even if reading it back failed: the input is taken off wherever the
            # unit really is, the new slot included, and the player told what may be there.
            written = False
            try:
                written = same_slot(self.unit.position(), target)
                if written:
                    self.unit.slot = target
            except Exception:  # noqa: BLE001
                pass
            self.quiet()
            if written:
                raise RuntimeError(f"Tone Lab {target['position'] + 1} may hold {name!r} now, but saving it could "
                                   f"not be confirmed: {error}") from error
            raise
        # Saved as it will be played, from the guitar input. Left as saved: disconnecting it now would be an edit to
        # the preset just written. The desk makes A and B ready again at once.
        self.connected = False
        return target, stored

    def inputs(self, physical_input=None):
        """Which USB channels carry the dry guitar and the processed sound now, and what the unit is on: for the
        Guitar Learner, which records the inputs itself and cannot open the unit while the engine holds it.
        Returns {"channels": (dry, wet left, wet right), "preset", "scene", "position", "firmware", "ours"};
        "ours" says whether the unit is on a slot the app works in or put up itself, and not on a preset the
        player chose by hand."""
        from lab.qc.device import recording_channels

        channels = recording_channels(self.unit.device, physical_input or self.physical_input)
        position = self.unit.position()
        ours = self.slots.scratch(position) or same_slot(position, self.unit.slot)
        return {"channels": tuple(channels), "preset": self.unit.name(), "scene": self.unit.scene(),
                "position": position, "firmware": self.unit.firmware, "ours": bool(ours)}

    def record(self, seconds, side=None, knobs=None, *, stop=None, progress=None):
        """Record the guitar: its dry input, as it reaches the unit before any processing, and the processed stereo
        return. With `side`, the player hears (and the wet take holds) that side's sound while playing. Returns
        the audio as (dry, wet left, wet right) columns. `progress("open")` says when the input is open."""
        from lab.qc.device import recording_channels

        self.quiet()
        # Which USB channels carry the dry input depends on the unit's USB Dry/Wet setting: read before anything
        # is connected.
        channels = list(recording_channels(self.unit.device, self.physical_input))
        try:
            if side is not None and self.sides.get(side) is not None:
                self.play(side, "guitar")
                if knobs:
                    self.turn(side, knobs)       # the wet take is the sound as shaped, as it is named
            return self.audio.record(seconds, channels, cancel=stop,
                                     started=(lambda: progress("open")) if progress else None)
        finally:
            self.quiet()
