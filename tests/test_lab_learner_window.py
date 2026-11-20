"""The Guitar Learner's window and its reports, on the new package alone."""

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import numpy as np

from lab.hearing import audio
from lab.learner import dataset as data, folders
from lab.learner.window import GuitarLearner, report_score, training_coverage

PACKAGE = Path(__file__).resolve().parents[1] / "lab" / "learner"


def recorded(pickup, wiring="Parallel", tone=10, volume=10):
    return {"configuration": {"pickup": pickup, "wiring": wiring, "tone": tone, "volume": volume}}


class GuitarLearnerReportTest(unittest.TestCase):
    def test_untaught_switch_combinations_are_separate_from_blind_accuracy(self):
        examples = [recorded(4), recorded(5), recorded(5, "Series")]
        checks = [(recorded(5), {"pickup": 5, "wiring": None}),
                  (recorded(2), {"pickup": None, "wiring": None}),
                  (recorded(3), {"pickup": None, "wiring": None})]
        result = report_score(checks, "dk24_hh_toggle", examples)
        self.assertIn("Pickup 1/1 correct", result)
        self.assertIn("Wiring uncertain", result)
        self.assertIn("2 outside taught setups", result)

    def test_coverage_exposes_fixed_knobs_and_taught_positions(self):
        result = training_coverage([recorded(5), recorded(4, tone=5)])
        self.assertIn("Taught blade positions 4, 5", result)
        self.assertIn("Tone 5–10", result)
        self.assertIn("Volume 10", result)


class StandaloneTest(unittest.TestCase):
    def test_nothing_in_the_learner_mentions_the_old_package(self):
        files = [path for path in PACKAGE.rglob("*") if path.is_file() and path.suffix in (".py", ".md", ".json", ".txt")]
        self.assertTrue(any(path.name == "window.py" for path in files))
        mentions = [path.name for path in files if "tone_lab" in path.read_text(encoding="utf-8")]
        self.assertEqual(mentions, [])

    def test_the_guitar_photographs_travel_with_the_package(self):
        from lab.learner.board import PHOTO

        for filename, _ in PHOTO.values():
            self.assertTrue((PACKAGE / "assets" / filename).is_file(), filename)


class Seat:
    """The desk's side of the learner, without a unit: nothing is ever submitted to hardware here."""

    def __init__(self, root):
        self.root = root
        self.job = self.leaving = self.ready = self.stop_signal = None
        self.live_is_ready = False
        self.takes, self.statuses, self.submitted = [], [], []
        self.input_channel = 1

    @staticmethod
    def read_state(path):
        return {}

    def set_status(self, value, **_):
        self.statuses.append(value)

    def task_if_active(self, name):
        return False

    def stop(self):
        pass

    def submit(self, task, message, work, *args, done, **options):
        self.submitted.append(task)


class WindowTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"No display: {error}")
        self.root.withdraw()
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        guard = patch.object(folders, "SESSIONS", self.home)
        guard.start()
        self.addCleanup(guard.stop)
        self.learner = None

    def tearDown(self):
        if self.learner is not None:
            self.learner.shutdown()
        self.root.destroy()
        # Free the window's Tk variables here, on the main thread: if a later test's worker thread happens to run
        # the collector, Tk aborts the whole interpreter.
        import gc

        self.learner = self.root = None
        gc.collect()

    def settle(self):
        deadline = time.monotonic() + 20
        while self.learner.job is not None and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.root.update()
        self.assertIsNone(self.learner.job)

    def test_opens_on_a_seat_reads_the_dataset_and_remembers_the_layout(self):
        signal = (.05 * np.sin(np.arange(audio.RATE * 2) * (220 * 2 * np.pi / audio.RATE)))[:, None]
        wave = self.home / "original.wav"
        audio.write_wave(wave, signal.astype(np.float32))
        data.add("Charvel", "dk24_blade", wave, {"pickup": 5, "volume": 10., "tone": 10.})

        self.learner = GuitarLearner(Seat(self.root))
        self.settle()
        self.assertEqual(self.learner.window.title(), "Guitar Learner · Tone Lab")
        self.assertEqual(self.learner.layout, "dk24_hh_toggle")
        self.assertEqual(self.learner.count.get(), "0 examples  ·  0 setups")
        self.assertEqual(self.learner.title.get(), "Check a recording")

        self.learner.change_layout("dk24_blade")
        self.settle()
        self.assertEqual(folders.layout_choice().read_text(encoding="utf-8").count("dk24_blade"), 1)
        self.assertEqual(self.learner.count.get(), "1 example  ·  1 setup")
        self.assertIn("Taught blade positions 5", self.learner.coverage.get())

        self.learner.toggle_dataset()
        self.assertEqual(len(self.learner.table.get_children()), 1)
        self.assertIn("1 examples", self.learner.dataset_summary.get())
        self.learner.toggle_dataset()
        self.assertFalse(self.learner.dataset_open)


def tearDownModule():
    # Tk objects left as garbage here must be freed on the main thread: if a later test's worker thread runs
    # the collector, Tcl aborts the whole interpreter ("Tcl_AsyncDelete").
    import gc

    gc.collect()


if __name__ == "__main__":
    unittest.main()
