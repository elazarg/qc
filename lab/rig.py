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

from . import catalog
from .chain import differences
from .slots import BUILD, BUILD_NAME, cache_name
from .unit import Mismatch

USB = (12, 14)   # the phrase from the computer in, the processed sound back to the computer


def held_silent(chain):
    """The chain with every input disconnected and every output kept."""
    return chain.with_routes([(0, row.outlet) for row in chain.rows])


class Rig:
    def __init__(self, unit, slots):
        self.unit, self.slots = unit, slots

    def ready(self, chain, *, stop=None):
        """A slot holding `chain`, recalled from the cache or built once. Returns (slot, what it holds)."""
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
        digest = chain.digest()
        self.slots.claim(BUILD, BUILD_NAME)
        self.unit.slot = None
        self.unit.recall(BUILD)
        self.unit.slot = BUILD
        held = self.unit.apply(held_silent(chain), stop=stop)
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
