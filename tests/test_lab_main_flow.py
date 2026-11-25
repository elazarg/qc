"""The main flow from the first request to a preset on the unit, in one piece.

Ask for a sound; a round of three designs comes back, each built on the unit and listened to; the first is on B.
Copy it to A and step to the second; play A and then B with the guitar; a remark moves a Shape knob; when the
playing stops the turn is checked by listening; save. Then the unit is read back: the preset in the Tone Lab
setlist is the design on B with its knob, under the name given.

Everything is the app's own: the studio, the engine on its worker (`Host`), the simulated unit. Only the
assistant is scripted, and no window is opened.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import unittest  # noqa: E402

from lab.qc.engine import MULTI_OUT  # noqa: E402
from lab.qc.sim import TONE_LAB  # noqa: E402
from lab.sound import catalog, knobs  # noqa: E402
from lab.sound.chain import differences  # noqa: E402

from support import GREENBACK, PLEXI, StudioCase  # noqa: E402
from test_lab_design import answer, block, reply  # noqa: E402

GAIN = catalog.model(PLEXI).index("GAIN")


class MainFlowTest(StudioCase):
    idle_seconds = 0.        # the app listens to a round as soon as the unit is free

    def held(self):
        """The chain on the unit now, read where the unit is held."""
        from lab.sound.chain import Chain

        return Chain.from_record(self.host.call("lab.tools.probe.held").result(10))

    def test_from_a_request_to_a_preset_on_the_unit(self):
        studio = self.studio
        self.script.answers.append(reply(*[answer(name, [block(PLEXI, 0, gain=gain), block(GREENBACK, 1)],
                                                  differs=f"gain at {gain}")
                                           for name, gain in (("One", 3), ("Two", 6), ("Three", 9))]))

        # Ask: a round of three, the first on B, each built on the unit and listened to.
        studio.ask("make a classic rock crunch")
        self.settle(30)
        found = studio.round()
        designs = [studio.store.sound(candidate["sound"]) for candidate in found["candidates"]]
        self.assertEqual([design.name for design in designs], ["One", "Two", "Three"])
        self.assertEqual((studio.sound("b").name, studio.state.shown), ("One", "pair"))
        for design in designs:
            self.assertNotIn("failed", design.heard, design.name)
            self.assertEqual(design.heard["digest"], design.chain.digest())
        harmonics = [design.heard["probe"]["distortion_db"] for design in designs]
        self.assertEqual(harmonics, sorted(harmonics))          # more gain was heard as more distortion
        self.assertGreater(harmonics[2] - harmonics[0], 6)
        self.assertEqual(len(self.script.asked), 1)

        # Copy B to A and step to the second design: One on A, Two on B.
        studio.copy_to_a()
        self.settle()
        studio.step(1)
        self.settle()
        self.assertEqual((studio.sound("a").name, studio.sound("b").name), ("One", "Two"))
        self.assertEqual(studio.state.shown, "pair")

        # Play A, then B, with the guitar: both sit on one preset, and only the input moves.
        self.host.call("lab.qc.sim.strum", True).result(10)        # the player plays
        heard_on = {}
        for side, row in (("a", 0), ("b", 2)):
            studio.play("guitar", side)
            self.settle()
            self.assertEqual((studio.state.mode, studio.state.side), ("guitar", side))
            on_unit = self.held()
            heard_on[side] = [lane.inlet for lane in on_unit.rows]
            self.assertEqual(on_unit.rows[row].route, (1, MULTI_OUT))
        self.assertEqual(heard_on, {"a": [1, 0, 0, 0], "b": [0, 0, 1, 0]})
        both = self.held()
        self.assertEqual([lane.cells[0].model if lane.cells and lane.cells[0] else None for lane in both.rows],
                         [PLEXI, None, PLEXI, None])
        self.assertEqual((round(both.block(0, 0).display(GAIN)), round(both.block(2, 0).display(GAIN))), (3, 6))

        # A remark in the player's words moves a Shape knob on the sound heard, at once and without the assistant.
        studio.settle_seconds = 0.
        studio.ask("more gain")
        self.settle()
        self.assertEqual(studio.sound("b").knobs, {"drive": .3})
        self.assertEqual(studio.sound("a").knobs, {})
        self.assertEqual(len(self.script.asked), 1)
        self.assertGreater(self.held().block(2, 0).display(GAIN), 6.2)      # turned on the unit while it plays
        self.assertNotIn("shaped", studio.sound("b").heard)                 # and not listened to while playing

        # When the strings are quiet, the turn is checked by listening: it did what it was meant to.
        self.host.call("lab.qc.sim.strum", False).result(10)
        self.until(lambda: "shaped" in (studio.sound("b").heard or {}), 30)
        self.settle(30)
        b = studio.sound("b")
        shaped = b.heard["shaped"]
        self.assertEqual((shaped["knobs"], shaped["check"]["drive"]["verdict"], shaped["corrected"]),
                         ({"drive": .3}, "as intended", None))
        self.assertGreater(shaped["probe"]["distortion_db"], b.heard["probe"]["distortion_db"])
        self.assertEqual(b.knobs, {"drive": .3})

        # Save B under a name.
        studio.save("Crunch Two")
        self.settle(30)
        b = studio.sound("b")
        slot = b.saved["slot"]
        self.assertEqual((b.saved["name"], b.saved["knobs"], slot["folder_key"]), ("Crunch Two", {"drive": .3}, TONE_LAB))
        self.assertIn(f"Saved · Tone Lab {slot['position'] + 1} · Crunch Two", studio.state.status)
        self.assertEqual(studio.state.shown, "pair")                         # and A and B are ready again

        # Read the unit back: the preset there is the design on B with its knob, routed from the guitar.
        on_unit, name = self.host.call("read", slot).result(10)
        self.assertEqual(name, "Crunch Two")
        self.assertEqual([(row, column, found.model) for row, column, found in on_unit.blocks],
                         [(0, 0, PLEXI), (0, 1, GREENBACK)])
        self.assertEqual(on_unit.rows[0].route, (1, MULTI_OUT))
        # The same design saved without the knob, and the knob applied to that by the app's own rule, is what
        # the unit holds; and the knob made a difference.
        plain_slot, _ = self.host.call("save", b.chain, "Crunch Two plain").result(10)
        plain, _ = self.host.call("read", plain_slot).result(10)
        self.assertEqual(differences(on_unit, knobs.apply(plain, {"drive": .3})), [])
        self.assertNotEqual(differences(on_unit, plain), [])
        self.assertAlmostEqual(plain.block(0, 0).display(GAIN), 6., places=1)
        self.assertGreater(on_unit.block(0, 0).display(GAIN), 6.2)


if __name__ == "__main__":
    unittest.main()
