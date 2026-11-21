"""Putting a chain on the unit so that it can be heard, without ever building it twice.

`Rig.ready(chain)` returns a slot holding the chain. The cache is keyed by the
chain's digest, and a recalled slot is checked against the chain itself. If a
chain with the same digest was built before, it is recalled from the cache: 1 to 3 s. Otherwise
it is assembled in the build slot and stored in the cache: 11 to 17 s, once.

A cached chain is stored with its inputs disconnected and its outputs as
wanted, so recalling it is silent. `Rig.connect` then sets each row's output
level and mute, and only after that connects the inputs. Nothing half-built
or at the wrong level is ever heard.
"""

from dataclasses import replace

from qc_mcp import leveling

from lab.sound import catalog
from lab.sound.chain import differences
from lab.qc.slots import BUILD, BUILD_NAME, cache_name
from lab.qc.unit import Mismatch

USB = (12, 14)   # the phrase from the computer in, the processed sound back to the computer


UNITY = (leveling.norm_of(0.), 0.)   # a row's output control at 0 dB, not muted


def held_silent(chain):
    """The chain with every input disconnected, every output kept, and each row's level stated.

    A row's output control that the chain leaves unset is set to 0 dB and not
    muted. Left unset it would keep whatever the build slot last held, which
    can be a mute or a trim left by something else.
    """
    rows = tuple(replace(row, inlet=0, output=tuple({catalog.LANE_VOLUME: UNITY[0], catalog.LANE_MUTE: UNITY[1],
                                                     **dict(row.output)}.items()))
                 for row in chain.rows)
    return replace(chain, rows=rows)


class Rig:
    def __init__(self, unit, slots):
        self.unit, self.slots = unit, slots

    def ready(self, chain, *, stop=None):
        """A slot holding `chain`, recalled from the cache or built once. Returns (slot, what it holds)."""
        self.slots.may_leave()
        digest = chain.digest()
        slot = self.slots.cached(digest)
        if slot is not None:
            self.unit.slot = None
            held = self.unit.recall(slot)
            if not differences(held, held_silent(chain)):
                self.unit.slot = slot
                self.slots.touch(digest)
                return slot, held
            self.slots.forget(digest)  # Someone changed that slot. Build it again.
        return self.build(chain, stop=stop)

    def build(self, chain, *, stop=None):
        self.slots.may_leave()
        digest = chain.digest()
        self.slots.refresh()                # the player may have saved a preset into the build slot since
        self.slots.claim(BUILD, BUILD_NAME)
        self.unit.slot = None
        self.unit.recall(BUILD)
        self.unit.slot = BUILD
        held = self.unit.apply(held_silent(chain), stop=stop, fresh=True)
        # The directory read at the start may be old: the player may have saved something into the cache's slots.
        self.slots.refresh()
        target = self.slots.place_for(digest)
        self.slots.claim(target, cache_name(digest))
        held = self.unit.store(target, cache_name(digest))
        wrong = differences(held, held_silent(chain))
        if wrong:
            raise Mismatch("The stored chain did not match what was asked for: " + "; ".join(wrong[:3]))
        self.slots.remember(digest, target)
        return target, held

    def connect(self, chain, routes, *, output=None):
        """Route the chain on the unit as `routes` says, with each row's output control set first.

        `output` is {row: ((index, value), ...)} for the output controls; rows
        not named keep theirs.
        """
        wanted = chain.with_routes(routes)
        if output:
            wanted = replace(wanted, rows=tuple(replace(row, output=tuple({**dict(row.output),
                                                                            **dict(output.get(r, ()))}.items()))
                                                for r, row in enumerate(wanted.rows)))
        return self.unit.apply(wanted)
