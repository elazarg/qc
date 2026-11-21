"""The faults the independent re-review of 20 November 2026 found in the studio (docs/CODEBASE_REVIEW.md,
"Independent re-review"), each reproduced as it was found, on the simulated unit.

The faults it found in the engine and in the taste model are with the tests of those modules. These belong
with `test_lab_studio.py`.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import threading  # noqa: E402
import unittest  # noqa: E402

from lab.player import taste  # noqa: E402
from lab.qc.engine import MULTI_OUT  # noqa: E402
from lab.sound import catalog  # noqa: E402
from lab.sound.chain import Block, Chain, Row  # noqa: E402
from lab.sound.sounds import Sound  # noqa: E402

from support import GREENBACK, HIWATT, PLEXI, V30, StudioCase, one_row, settle_until  # noqa: E402
from test_lab_design import answer, block, reply  # noqa: E402


class UndoTest(StudioCase):
    def test_undo_keeps_the_side_that_is_heard(self):
        self.put_two()
        self.studio.turn({"drive": .4}, "b")
        self.studio.play("guitar", "b")
        self.settle()
        self.studio.select("a")
        self.settle()
        self.studio.undo()
        self.settle()
        self.assertEqual(self.studio.state.side, "a")
        self.assertEqual(self.studio.state.mode, "guitar")

    def test_undoing_the_only_sound_stops_the_sound(self):
        self.studio.put("b", Sound("Hiwatt", one_row(HIWATT, GREENBACK), {"kind": "designed"}))
        self.settle()
        self.studio.play("guitar", "b")
        self.settle()
        self.studio.undo()
        self.settle()
        self.assertIsNone(self.studio.state.mode)
        self.assertIsNone(self.studio.state.b)


class ShapeCheckTest(StudioCase):
    def test_a_phrase_changed_during_the_check_changes_no_knob(self):
        self.put_two()
        self.studio.settle_seconds = 0.
        call = self.host.call

        def change_take_meanwhile(name, *args, **options):
            if name == "hear_shaped":
                self.studio.phrase_whole = (self.studio.phrase[1], 0, 12000)
            return call(name, *args, **options)

        self.host.call = change_take_meanwhile
        self.studio.turn({"brightness": .3}, "b")
        self.settle(30)
        sound = self.studio.sound("b")
        self.assertEqual(sound.knobs, {"brightness": .3})
        self.assertNotIn("shaped", sound.heard or {})


class SceneEvidenceTest(StudioCase):
    def test_a_measurement_of_the_first_scene_does_not_hold_for_another(self):
        gain = catalog.model(PLEXI).index("GAIN")
        switched = Chain((Row((Block(PLEXI).set(gain, (.3, .9) + (.3,) * 6), Block(V30)), inlet=1,
                              outlet=MULTI_OUT),))
        sound = Sound("Switched", switched, {"kind": "designed"})
        sound.heard = {"phrase": self.studio.phrase_key(), "digest": switched.digest(), "scene": 0, "level_db": -20.}
        self.assertIsNotNone(self.studio.evidence(sound))
        self.studio.set(scene=1)
        self.assertIsNone(self.studio.evidence(sound))


class BlindTest(StudioCase):
    def test_keep_on_a_sound_already_kept_is_still_a_blind_choice(self):
        self.put_two()
        self.studio.mark("keeper", "a")
        self.studio.blind()
        self.settle()
        kept = self.studio.state.a
        self.studio.mark("keeper", "a")
        self.assertFalse(self.studio.state.blind)
        self.assertIn("keeper", self.studio.store.sound(kept).marks)
        self.assertEqual(taste.choices(self.folder)[-1]["how"], "blind")


class RequestsTest(StudioCase):
    gate = threading.Event()        # holds the answer to the first request back until the test lets it go

    @staticmethod
    def script(prompt, schema, **options):
        name = "Late" if "first request" in prompt else "Soon"
        if name == "Late":
            RequestsTest.gate.wait(10)
        return reply(*[answer(f"{name} {n}", [block(PLEXI, 0, gain=n * 3), block(GREENBACK, 1)], differs="d")
                       for n in (1, 2, 3)])

    def test_cancel_shows_at_once_and_an_older_answer_does_not_take_b(self):
        RequestsTest.gate.clear()
        self.addCleanup(RequestsTest.gate.set)
        cancel_shown, said = [], []
        self.studio.listeners.append(lambda state: cancel_shown.append(bool(state.working)
                                                                       and self.studio.cancellable()))
        self.studio.listeners.append(lambda state: said.append(state.status))
        self.studio.ask("make the first request")
        self.assertEqual(cancel_shown[-1:], [True])
        self.studio.ask("make the second request")
        # The later request is answered while the answer to the earlier one is still held back.
        settle_until(self.studio, lambda: self.studio.sound("b") is not None, 30, "the later answer")
        self.assertTrue(self.studio.sound("b").name.startswith("Soon"), self.studio.sound("b").name)
        RequestsTest.gate.set()
        self.settle(30)
        self.assertTrue(self.studio.sound("b").name.startswith("Soon"), self.studio.sound("b").name)
        self.assertIn("earlier request", " | ".join(said))


if __name__ == "__main__":
    unittest.main()
