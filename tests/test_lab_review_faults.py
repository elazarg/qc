"""The faults the codebase review of 20 November 2026 found in the studio (docs/CODEBASE_REVIEW.md), each
reproduced as it was found, on the simulated unit.

The faults it found in the engine, the host, the slots, listening, the probe, the assistant and the listening
history are with the tests of those modules. These belong with `test_lab_studio.py`.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
from concurrent.futures import Future  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402
from tempfile import TemporaryDirectory  # noqa: E402
import unittest  # noqa: E402

from lab.desk.studio import Studio  # noqa: E402
from lab.qc.host import Host  # noqa: E402
from lab.sound.assistant import Assistant  # noqa: E402

from support import StudioCase  # noqa: E402


class StudioFaultsTest(StudioCase):
    def test_a_job_finishing_between_two_looks_still_has_its_result_handled(self):
        handled = []

        class Flicker(Future):
            looks = 0

            def done(self):
                Flicker.looks += 1
                if Flicker.looks == 1:
                    return False
                if not super().done():
                    self.set_result("late")
                return True

        self.studio.later(Flicker(), handled.append)
        for _ in range(3):
            self.studio.poll()
        self.assertEqual(handled, ["late"])

    def test_undo_after_stopping_does_not_start_playing_again_and_puts_the_knob_back(self):
        self.put_two()
        self.studio.play("guitar")
        self.settle()
        self.studio.turn({"drive": .4})
        self.studio.stop()
        self.settle()
        self.studio.undo()
        self.settle()
        self.assertIsNone(self.studio.state.mode)
        self.assertEqual(self.studio.sound("b").knobs, {})

    def test_the_name_given_is_saved_with_the_sound_it_was_given_for(self):
        self.put_two()
        b = self.studio.sound("b")
        self.studio.select("a")
        self.studio.save("Kept B", sound_id=b.id, knob_values={})
        self.settle(30)
        self.assertEqual(self.studio.store.sound(b.id).saved["name"], "Kept B")
        self.assertIsNone(self.studio.sound("a").saved)

    def test_blind_play_keeps_the_names_hidden_and_a_choice_shows_them(self):
        self.put_two()
        self.studio.blind()
        self.settle()
        self.studio.play("guitar", "a")
        self.settle()
        names = {"Plexi", "Hiwatt"}
        self.assertEqual([name for name in names if name in self.studio.state.status], [])
        self.studio.mark("keeper", "a")
        self.assertFalse(self.studio.state.blind)
        self.assertIn("A was", self.studio.state.status)

    def test_a_measurement_belongs_to_the_part_of_the_phrase_it_was_made_on(self):
        self.put_two()
        b = self.studio.sound("b")
        b.heard = {"phrase": self.studio.phrase_key(), "digest": b.chain.digest(), "level_db": -20.,
                   "loudness_lufs": -20.}
        self.studio.store.save(b)
        self.assertIsNotNone(self.studio.evidence(self.studio.store.sound(b.id)))
        self.studio.phrase_whole = (self.studio.phrase[1], 0, 12000)
        self.assertIsNone(self.studio.evidence(self.studio.store.sound(b.id)))

    def test_recording_keeps_the_dry_and_wet_takes_and_says_when_to_play(self):
        self.put_two()
        said = []
        self.studio.listeners.append(lambda state: said.append(state.working))
        self.studio.record("Neck", 1.)
        self.settle()
        self.assertIn("play now", " | ".join(said))
        phrase_id = self.studio.phrase[0]
        folder = self.folder / "phrases" / phrase_id
        self.assertTrue((folder / "dry.wav").is_file(), "the dry take")
        self.assertTrue((folder / "wet.wav").is_file(), "the wet take")
        info = json.loads((folder / "phrase.json").read_text(encoding="utf-8"))
        self.assertEqual(info["wet"]["name"], "Hiwatt")
        self.assertEqual(len(self.studio.phrase[1]), 48000)


class CancelTest(unittest.TestCase):
    def test_cancel_drops_every_request_in_flight(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        host = Host(Path(folder.name) / "unit", simulated=True, local=True)
        studio = Studio(host, Path(folder.name),
                        assistant=Assistant(folder.name, automatic=False, run=lambda *a, **k: {}))
        self.addCleanup(studio.close)
        first, second = studio._request(), studio._request()
        self.assertTrue(studio.cancel())
        self.assertTrue(first.is_set(), "the first request's signal")
        self.assertTrue(second.is_set(), "the second request's signal")


if __name__ == "__main__":
    unittest.main()
