"""The chain's planning where a value the unit holds by scene is wanted the same in every scene, on the
simulated unit, and the digest as a key."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
from dataclasses import replace
import unittest

from lab.qc.sim import SimUnit
from lab.qc.slots import BUILD
from lab.qc.unit import Unit
from lab.sound import catalog, chain as chains
from lab.sound.chain import Block, Chain, Op, Row, differences, plan_values

RECTIFIER, V30, DELAY = 1018, 12001, 6011
GAIN = catalog.model(RECTIFIER).index("GAIN")


def stored(display):
    return catalog.to_norm(RECTIFIER, GAIN, display)


def by_scene():
    """A Rectifier whose gain differs in the lead scene, and a delay on only there."""
    amp = Block(RECTIFIER, ((GAIN, (stored(4.), stored(6.5)) + (stored(4.),) * 6),))
    return Chain((Row((amp, Block(V30), Block(DELAY, bypass=(True, False) + (True,) * 6)), inlet=12, outlet=14),),
                 ("Rhythm", "Lead"))


class SceneValuesTest(unittest.TestCase):
    def test_a_value_held_by_scene_can_be_made_the_same_in_every_scene(self):
        unit = Unit(SimUnit(), settle=.05, sleep=lambda seconds: None)
        unit.device.recall(**BUILD)
        held = unit.apply(by_scene())
        self.assertEqual(differences(held, by_scene()), [])
        flat = by_scene().with_block(0, 0, Block(RECTIFIER, ((GAIN, stored(5.)),)))
        flat = flat.with_block(0, 2, Block(DELAY, bypass=False))
        after = unit.apply(flat)                    # raised Mismatch: the plain write reached one scene only
        self.assertEqual(differences(after, flat), [])
        self.assertEqual([round(after.block(0, 0).display(GAIN, scene), 2) for scene in range(8)], [5.] * 8)
        self.assertEqual(after.block(0, 2).bypass, (False,) * 8)

    def test_the_plan_writes_each_scene_that_differs_and_only_those(self):
        current = by_scene()
        wanted = current.with_block(0, 0, Block(RECTIFIER, ((GAIN, stored(4.)),)))
        wanted = wanted.with_block(0, 2, replace(current.block(0, 2), bypass=True))
        ops = plan_values(current, wanted)
        self.assertEqual(ops, [Op("scene", (1,)), Op("param", (0, 0, GAIN, stored(4.))), Op("bypass", (0, 2, True)),
                               Op("scene", (0,))])                     # nothing assigned anew, nothing written twice
        self.assertEqual(plan_values(current, current), [])
        fresh = plan_values(Chain((Row((Block(RECTIFIER), Block(V30), Block(DELAY))),)), current)
        self.assertEqual([op for op in fresh if op.kind == "assign"],
                         [Op("assign", (0, 0, GAIN)), Op("assign", (0, 2, catalog.model(DELAY).bypass_index))])


class NamesTest(unittest.TestCase):
    def test_the_ports_and_tolerance_are_named_once(self):
        self.assertEqual(chains.MULTI_OUT, 19)
        for gone in ("PORT_USB_IN", "PORT_USB_OUT", "PORT_MULTI_OUT", "SILENT"):
            self.assertFalse(hasattr(chains, gone), gone)
        self.assertFalse(hasattr(Op("add"), "interrupts"))
        self.assertEqual(catalog.IR_LOADER_STEREO, 29002)
        self.assertIn((catalog.IR_LOADER_STEREO, 22), chains.LABELS)
        within = Block(RECTIFIER, ((GAIN, (.5, .5 + chains.TOLERANCE / 2) + (.5,) * 6),))
        self.assertEqual(Chain((Row((within,)),)).scenes_in_use(), 0)       # within the unit's rounding: one sound

    def test_the_digest_is_a_key_that_follows_the_sound(self):
        one = by_scene()
        self.assertEqual(one.digest(), Chain.from_record(one.to_record()).digest())      # the same after a save
        self.assertEqual(one.digest(), replace(one, labels=("A", "B")).digest())          # names are not the sound
        self.assertNotEqual(one.digest(), one.with_block(0, 1, None).digest())


if __name__ == "__main__":
    unittest.main()
