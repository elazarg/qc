"""The seat the Guitar Learner takes at the desk, on the real studio and the simulated unit: its one job at a time
holds the audio input (`Studio.hold_audio`), runs on the seat's worker, reports back on the desk's clock, and
gives the input back however it ends."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from lab.desk.studio import Studio
from lab.desk.window import LearnerSeat
from lab.qc.host import Host
from lab.sound.assistant import Assistant


class SeatTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        home = Path(folder.name)
        host = Host(home / "unit", simulated=True, local=True)
        host.ready.result(10)
        self.studio = Studio(host, home, assistant=Assistant(home, automatic=False, run=lambda *a, **k: {}))
        self.addCleanup(self.studio.close)
        self.seat = LearnerSeat(SimpleNamespace(root=None, studio=self.studio))

    def finish(self):
        self.seat.job.result(30)
        self.seat.tick()

    def test_a_learner_job_holds_the_input_runs_once_and_gives_it_back(self):
        results = []
        self.assertTrue(self.seat.submit("Opening the dry input…", lambda x: x * 2, 21, done=results.append))
        self.assertEqual((self.studio.audio_held_by, self.studio.state.status), ("learner", "Opening the dry input…"))
        self.studio.play("guitar")                        # the desk does not start playing while it works
        self.assertIsNone(self.studio.state.mode)
        self.assertIn("is using the input", self.studio.state.status)
        self.assertFalse(self.seat.submit("again", lambda: 0, done=results.append))     # one at a time
        self.finish()
        self.assertEqual(results, [(42, None)])
        self.assertIsNone(self.seat.job)
        self.assertIsNone(self.studio.audio_held_by)

    def test_a_failing_job_reports_why_and_gives_the_input_back(self):
        results = []

        def broken():
            raise ValueError("no input")

        self.seat.submit("…", broken, done=results.append)
        self.finish()
        self.assertEqual(results, [(None, "no input")])
        self.assertIsNone(self.studio.audio_held_by)

    def test_the_seat_does_not_start_while_someone_else_holds_the_input(self):
        self.studio.hold_audio("gestures")
        self.assertFalse(self.seat.submit("…", lambda: 0, done=lambda result: None))
        self.assertIsNone(self.seat.job)
        self.assertEqual(self.studio.audio_held_by, "gestures")


if __name__ == "__main__":
    unittest.main()
