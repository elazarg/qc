"""The Guitar Learner's seat: the data folder, the audio hold, work followed on the desk's clock, faults written.

`seat_for(test)` is what the other learner tests sit on: a real `LearnerSeat` on a made-up studio, with every
file inside a temporary folder.
"""

from concurrent.futures import Future
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lab.learner import folders, seat as seat_module
from lab.learner.folders import LOG
from lab.learner.seat import LearnerSeat


def answered(value=None, error=None):
    future = Future()
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(value)
    return future


class FakeHost:
    """The engine's host: `call("inputs", n)` says where the dry guitar is; anything else is refused."""

    def __init__(self):
        self.calls = []
        self.inputs = {"channels": (2, 3, 4), "preset": "Lead", "scene": 1, "position": {"setlist": 0, "slot": 5},
                       "firmware": "4.1.1"}
        self.error = None

    def call(self, name, *args, **options):
        self.calls.append((name, args))
        if self.error is not None or name != "inputs":
            return answered(error=self.error or RuntimeError(f"no {name} here"))
        return answered(self.inputs)


class FakeStudio:
    """What the seat, the windows and gestures use of `lab.desk.studio.Studio`."""

    def __init__(self, folder, instrument="Charvel"):
        self.folder = Path(folder)
        self.state = SimpleNamespace(instrument=instrument, mode=None, side="b")
        self.store = SimpleNamespace(instrument=lambda name: {"name": name, "input": 2})
        self.host = FakeHost()
        self.statuses, self.held, self.turned = [], [], []
        self.audio_held_by = None
        self.let_go = None                  # the future `hold_audio` returns; a finished one unless a test sets it
        self.shown = SimpleNamespace(knobs={})
        self.offered = ["drive", "space"]

    def set(self, **changes):
        if "status" in changes:
            self.statuses.append(changes["status"])

    def hold_audio(self, who):
        if self.audio_held_by not in (None, who):          # as the studio does: one holder at a time
            raise RuntimeError(f"{self.audio_held_by} is using the input.")
        self.audio_held_by = who
        self.held.append(("hold", who))
        return self.let_go if self.let_go is not None else answered()

    def release_audio(self, who):
        self.held.append(("release", who))
        if self.audio_held_by == who:
            self.audio_held_by = None

    def sound(self, side):
        return self.shown

    def available(self):
        return self.offered

    def turn(self, values):
        self.turned.append(values)
        self.shown.knobs = values


def seat_for(test, root=None, instrument="Charvel"):
    """A real seat on a made-up studio whose folder is temporary. Nothing reaches the player's folders."""
    directory = TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    test.addCleanup(folders.log_to, None)
    test.addCleanup(folders.use, None)
    studio = FakeStudio(directory.name, instrument)
    with patch.object(folders, "bring_old", side_effect=AssertionError("a test must never copy the player's data")):
        seat = LearnerSeat(SimpleNamespace(root=root, studio=studio))
    test.addCleanup(seat.worker.shutdown, wait=True)
    return seat


def settle(seat, done, *, root=None, seconds=20):
    """Tick the seat, as the desk's clock would, until `done()` is true."""
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        seat.tick()
        if root is not None:
            root.update()
        time.sleep(.005)
    seat.tick()
    return done()


class DataFolderTest(unittest.TestCase):
    def test_the_data_is_kept_in_the_desks_folder_and_faults_go_to_its_errors_log(self):
        seat = seat_for(self)
        self.assertEqual(seat.folder, seat.studio.folder / "learner")
        self.assertEqual(folders.root(), seat.folder)
        self.assertEqual(folders.recordings(), seat.folder / "recordings")
        self.assertEqual(Path(folders.log_path()), seat.studio.folder / "errors.log")
        try:
            raise RuntimeError("the driver said no")
        except RuntimeError:
            LOG.exception("Recording failed")
        written = (seat.studio.folder / "errors.log").read_text(encoding="utf-8")
        self.assertIn("Guitar Learner: Recording failed", written)
        self.assertIn("RuntimeError: the driver said no", written)        # with its trace

    def test_only_the_players_own_data_folder_is_given_the_earlier_apps_data(self):
        with TemporaryDirectory() as directory:
            profile = Path(directory)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(profile)}):
                os.environ.pop("TONE_LAB_HOME", None)
                self.assertTrue(seat_module.is_the_players_home(profile / "Tone Lab"))
                self.assertFalse(seat_module.is_the_players_home(profile / "Tone Lab" / "simulated"))
                self.assertFalse(seat_module.is_the_players_home(profile / "elsewhere"))
                with patch.dict(os.environ, {"TONE_LAB_HOME": str(profile / "Tone Lab")}):
                    self.assertFalse(seat_module.is_the_players_home(profile / "Tone Lab"))

    def test_the_guitar_and_its_input_are_the_desks(self):
        seat = seat_for(self, instrument="Les Paul")
        self.assertEqual((seat.guitar, seat.input_channel), ("Les Paul", 2))
        seat.studio.state.instrument = "Charvel"
        self.assertEqual(seat.guitar, "Charvel")


class AudioHoldTest(unittest.TestCase):
    def test_work_waits_for_the_engine_to_let_go_and_the_hold_is_given_back(self):
        seat = seat_for(self)
        seat.studio.let_go = Future()               # the engine has not closed its stream yet
        began, results = Event(), []
        self.assertTrue(seat.submit("Opening…", lambda: began.set() or "take", done=results.append, stop=Event()))
        self.assertEqual(seat.studio.audio_held_by, "learner")
        self.assertFalse(began.wait(.2), "the driver was opened before the engine let go of it")
        seat.studio.let_go.set_result(None)
        self.assertTrue(settle(seat, lambda: results))
        self.assertEqual(results, [("take", None)])
        self.assertIsNone(seat.studio.audio_held_by)
        self.assertIsNone(seat.job)
        self.assertIsNone(seat.stop_signal)
        self.assertEqual(seat.studio.statuses[0], "Opening…")

    def test_a_failure_is_handed_over_written_down_and_the_hold_given_back(self):
        seat = seat_for(self)
        results = []

        def work():
            raise RuntimeError("no audio device")

        seat.submit("Opening…", work, done=results.append)
        self.assertTrue(settle(seat, lambda: results))
        self.assertEqual(results, [(None, "no audio device")])
        self.assertIsNone(seat.studio.audio_held_by)
        self.assertIn("no audio device", (seat.studio.folder / "errors.log").read_text(encoding="utf-8"))

    def test_an_engine_that_is_gone_does_not_stop_the_work_but_one_that_never_answers_does(self):
        seat = seat_for(self)
        results = []
        seat.studio.let_go = answered(error=RuntimeError("the engine stopped"))
        seat.submit("Opening…", lambda: "take", done=results.append)
        self.assertTrue(settle(seat, lambda: results))
        self.assertEqual(results.pop(), ("take", None))
        seat.studio.let_go = Future()
        with self.assertRaisesRegex(RuntimeError, "did not let go"):
            seat_module.wait_for(seat.studio.let_go, .05)

    def test_nothing_starts_while_something_else_holds_the_audio_or_another_job_runs(self):
        seat = seat_for(self)
        seat.studio.audio_held_by = "gestures"
        self.assertFalse(seat.submit("Opening…", lambda: None, done=lambda outcome: None))
        self.assertIn("gestures", seat.studio.statuses[-1])
        self.assertEqual(seat.studio.held, [])
        seat.studio.audio_held_by = None
        gate = Event()
        self.assertTrue(seat.submit("One", gate.wait, done=lambda outcome: None))
        self.assertFalse(seat.submit("Two", lambda: None, done=lambda outcome: None))
        gate.set()
        self.assertTrue(settle(seat, lambda: seat.job is None))

    def test_closing_the_desk_stops_the_work_and_gives_the_hold_back(self):
        seat = seat_for(self)
        stop = Event()
        seat.submit("Recording…", stop.wait, done=lambda outcome: None, stop=stop)
        seat.leaving = True
        self.assertTrue(stop.is_set())
        self.assertIsNone(seat.studio.audio_held_by)
        self.assertFalse(seat.submit("Again", lambda: None, done=lambda outcome: None))
        self.assertTrue(settle(seat, lambda: seat.job is None))


class ClockTest(unittest.TestCase):
    def test_later_hands_a_result_or_a_failure_to_the_desks_thread(self):
        seat = seat_for(self)
        got, failed = [], []
        seat.later(answered(3), got.append)
        seat.later(answered(error=ValueError("bad clip")), got.append, failed.append)
        seat.later(answered(error=ValueError("worse clip")), got.append)
        pending = Future()
        seat.later(pending, got.append)
        seat.tick()
        self.assertEqual(got, [3])
        self.assertEqual(str(failed[0]), "bad clip")
        self.assertIn("worse clip", seat.studio.statuses[-1])
        pending.set_result(4)
        seat.tick()
        self.assertEqual(got, [3, 4])
        self.assertEqual(seat.waiting, [])

    def test_a_step_that_fails_once_is_called_again_and_one_that_is_done_is_dropped(self):
        seat = seat_for(self)
        calls = []

        def step():
            calls.append(len(calls))
            if len(calls) == 1:
                raise RuntimeError("one bad event")
            return len(calls) < 3

        seat.watch(step)
        seat.watch(step)                    # asked twice, followed once
        for _ in range(5):
            seat.tick()
        self.assertEqual(calls, [0, 1, 2])
        self.assertEqual(seat.watchers, [])
        self.assertIn("one bad event", seat.studio.statuses[-1])


class RealStudioTest(unittest.TestCase):
    """The seat on the desk's real studio, with a simulated unit: the hold, and the engine's answer about inputs."""

    def setUp(self):
        from lab.desk.studio import Studio
        from lab.qc.host import Host

        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(folders.log_to, None)
        self.addCleanup(folders.use, None)
        home = Path(directory.name)
        host = Host(home / "unit", simulated=True, local=True)
        host.ready.result(10)
        self.studio = Studio(host, home)
        self.addCleanup(self.studio.close)
        self.seat = LearnerSeat(SimpleNamespace(root=None, studio=self.studio))
        self.addCleanup(self.seat.worker.shutdown, wait=True)

    def test_the_engine_says_where_the_dry_guitar_is_without_the_learner_opening_the_unit(self):
        from lab.learner import inputs

        channels, context = inputs.routing(self.seat.host, self.seat.input_channel)
        self.assertEqual(len(channels), 3)
        self.assertIn(channels[0], (1, 2, 3, 4))
        self.assertIn("wet_preset", context)

    def test_work_runs_with_the_studios_hold_and_the_studio_gets_it_back(self):
        self.assertEqual(self.seat.folder, Path(self.studio.folder) / "learner")
        held, results = [], []
        self.assertTrue(self.seat.submit("Working…", lambda: held.append(self.studio.audio_held_by) or "done",
                                         done=results.append))
        deadline = time.monotonic() + 15
        while not results and time.monotonic() < deadline:
            self.studio.poll()
            self.seat.tick()
            time.sleep(.01)
        self.assertEqual((held, results), (["learner"], [("done", None)]))
        self.assertIsNone(self.studio.audio_held_by)
        # While gestures hold the input, the learner does not start, and says who has it.
        self.studio.hold_audio("gestures")
        self.assertFalse(self.seat.submit("Working…", lambda: None, done=results.append))
        self.studio.release_audio("gestures")


if __name__ == "__main__":
    unittest.main()
