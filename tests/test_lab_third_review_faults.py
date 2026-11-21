"""The faults the re-review of 68cbd78 found in the studio (docs/CODEBASE_REVIEW.md, "Re-review of 68cbd78"),
each reproduced as it was found, on the simulated unit.

The faults it found in the engine and in the taste model are with the tests of those modules. These belong
with `test_lab_studio.py`.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import threading  # noqa: E402
import unittest  # noqa: E402

from lab.qc.engine import MULTI_OUT  # noqa: E402
from lab.sound import catalog  # noqa: E402
from lab.sound.chain import Block, Chain, Row  # noqa: E402
from lab.sound.sounds import Sound  # noqa: E402

from support import GREENBACK, HIWATT, PLEXI, V30, StudioCase, one_row, settle_until  # noqa: E402
from test_lab_design import answer, block, reply  # noqa: E402


def switched():
    """A sound whose second scene has more gain than the others."""
    gain = catalog.model(PLEXI).index("GAIN")
    return Chain((Row((Block(PLEXI).set(gain, (.3, .9) + (.3,) * 6), Block(V30)), inlet=1, outlet=MULTI_OUT),))


class ShapeSceneTest(StudioCase):
    def test_the_shape_check_measures_the_scene_heard(self):
        self.studio.put("b", Sound("Switched", switched(), {"kind": "designed"}))
        self.settle()
        self.studio.scene(1)
        self.settle()
        self.studio.settle_seconds = 0.
        asked, call = [], self.host.call

        def note(name, *args, **options):
            if name in ("hear", "hear_shaped"):
                asked.append((name, args[-1]))
            return call(name, *args, **options)

        self.host.call = note
        self.studio.turn({"brightness": .3}, "b")
        self.settle(30)
        self.assertEqual(asked, [("hear", 1), ("hear_shaped", 1)])
        self.assertEqual(self.studio.sound("b").heard["scene"], 1)
        self.assertEqual(self.studio.sound("b").heard["shaped"]["scene"], 1)

    def test_a_check_is_current_only_for_its_own_sound(self):
        self.studio.put("a", Sound("Same", one_row(PLEXI, V30), {"kind": "designed"}))
        self.settle()
        elsewhere = Sound("Same elsewhere", one_row(PLEXI, V30), {"kind": "designed"})
        self.studio.store.save(elsewhere)
        inputs_ = (self.studio.phrase_key(), self.studio.phrase[1], self.studio.state.scene,
                   elsewhere.chain.digest(), elsewhere.id)
        self.assertFalse(self.studio._current(inputs_))


class DifferenceTest(StudioCase):
    def test_difference_reads_the_sounds_as_they_are_set_now(self):
        key = self.studio.phrase_key()

        def heard(brightness, digest, shaped=None):
            record = {"phrase": key, "digest": digest, "scene": 0, "level_db": -20., "compression": .3,
                      "brightness_oct": brightness, "low_end_db": 0., "tail_db": -40., "distortion": "clean",
                      "probe": {"distortion_db": -40.}}
            if shaped is not None:
                record["shaped"] = {**record, "phrase": key, "knobs": {"brightness": -.5},
                                    "brightness_oct": shaped}
            return record

        a, b = one_row(PLEXI, V30), one_row(HIWATT, GREENBACK)
        self.studio.put("a", Sound("A", a, {"kind": "designed"}, heard=heard(.0, a.digest())))
        self.studio.put("b", Sound("B", b, {"kind": "designed"}, knobs={"brightness": -.5},
                                   heard=heard(.1, b.digest(), shaped=-.4)))
        self.settle()
        lines = " ".join(self.studio.difference())
        self.assertIn("0.4 octave darker", lines)


class UndoSceneTest(StudioCase):
    def test_undo_keeps_the_scene_the_unit_is_on(self):
        self.studio.put("b", Sound("Switched", switched(), {"kind": "designed"}))
        self.settle()
        self.studio.turn({"drive": .4}, "b")
        self.studio.scene(1)
        self.settle()
        self.studio.undo()
        self.settle()
        self.assertEqual(self.studio.state.scene, 1)
        self.assertEqual(self.host.call("scene", 1).result(5), 1)


class OlderRequestTest(StudioCase):
    gate = threading.Event()        # holds the answer to the first request back until the test lets it go

    @staticmethod
    def script(prompt, schema, **options):
        first = "first request" in prompt
        if first:
            OlderRequestTest.gate.wait(10)
            if "fails" in prompt:
                raise RuntimeError("the assistant failed")
        found = reply(*[answer(f"{'Late' if first else 'Soon'} {n}", [block(PLEXI, 0, gain=n * 3),
                                                                     block(GREENBACK, 1)], differs="d")
                        for n in (1, 2, 3)])
        if first:
            found["song"] = {"artist": "Old Band", "title": "Old Song", "part": "solo"}
        return found

    def setUp(self):
        OlderRequestTest.gate.clear()
        self.addCleanup(OlderRequestTest.gate.set)
        super().setUp()

    def test_an_older_answer_does_not_change_the_song(self):
        self.studio.ask("make the first request")
        self.studio.ask("make the second request")
        # The later request is answered while the answer to the earlier one is still held back.
        settle_until(self.studio, lambda: self.studio.sound("b") is not None, 30, "the later answer")
        self.assertTrue(self.studio.sound("b").name.startswith("Soon"), self.studio.sound("b").name)
        song = self.studio.state.song
        self.assertNotIn("Old Song", song or "")
        OlderRequestTest.gate.set()
        self.settle(30)
        self.assertEqual(self.studio.state.song, song)
        self.assertTrue(self.studio.sound("b").name.startswith("Soon"), self.studio.sound("b").name)

    def test_an_older_failure_keeps_cancel_while_the_latest_is_on_its_way(self):
        latest = threading.Event()
        self.addCleanup(latest.set)
        script = self.studio.assistant.run

        def second_waits(prompt, schema, **options):
            if "second request" in prompt:
                latest.wait(10)
            return script(prompt, schema, **options)

        self.studio.assistant.run = second_waits
        self.studio.ask("make the first request, which fails")
        self.studio.ask("make the second request")
        OlderRequestTest.gate.set()
        settle_until(self.studio, lambda: "earlier request failed" in (self.studio.state.status or ""), 10,
                     "word of the earlier failure")
        self.assertNotEqual(self.studio.state.working, "")     # the latest request is still said to be on its way
        self.assertTrue(self.studio.cancellable())
        latest.set()
        self.settle(30)


if __name__ == "__main__":
    unittest.main()
