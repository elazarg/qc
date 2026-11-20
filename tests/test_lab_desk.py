"""The seat the Guitar Learner takes on the new desk: its jobs run on a worker and report back on the desk's clock."""

from types import SimpleNamespace
import unittest

from lab.desk.window import LearnerSeat


class Studio:
    def __init__(self):
        self.state = SimpleNamespace(instrument="Charvel", status="")
        self.store = SimpleNamespace(instrument=lambda name: {"name": name, "input": 2})
        self.stopped = 0

    def stop(self):
        self.stopped += 1

    def set(self, **changes):
        self.state.status = changes.get("status", self.state.status)


class SeatTest(unittest.TestCase):
    def test_a_learner_job_stops_playback_runs_once_and_reports_back(self):
        studio = Studio()
        desk = SimpleNamespace(root=None, studio=studio)
        seat = LearnerSeat(desk)
        self.assertEqual(seat.input_channel, 2)
        results = []
        seat.submit("record", "Opening the dry input…", lambda x: x * 2, 21, done=results.append)
        self.assertEqual((studio.stopped, studio.state.status), (1, "Opening the dry input…"))
        self.assertTrue(seat.task_if_active("record"))
        seat.submit("record", "again", lambda: 0, done=results.append)   # One at a time.
        seat.job.result(5)
        seat.tick()
        self.assertEqual(results, [42])
        self.assertIsNone(seat.job)

    def test_a_failing_job_is_said_on_the_status_line(self):
        studio = Studio()
        seat = LearnerSeat(SimpleNamespace(root=None, studio=studio))

        def broken(_):
            raise ValueError("no input")

        seat.submit("record", "…", lambda: None, done=broken)
        seat.job.result(5)
        seat.tick()
        self.assertEqual(studio.state.status, "Guitar Learner: no input")


if __name__ == "__main__":
    unittest.main()
