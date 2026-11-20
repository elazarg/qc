import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from tone_lab import audio, capture, listening, setup_ui


class SetupLevelTest(unittest.TestCase):
    def test_trim_planning_uses_the_desk_sides_even_for_separate_folders(self):
        time = np.arange(audio.RATE) / audio.RATE
        wave = .15 * np.sin(2 * np.pi * 220 * time)
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            a, b = folder / "a", folder / "b"
            a.mkdir()
            b.mkdir()
            first = listening.Sound("Original tone", a / "wet.wav", "clip-one", (0, 1))
            second = listening.Sound("Tuned tone", b / "render.wav", "clip-one", (0, 1))
            audio.write_wave(first.path, wave)
            audio.write_wave(second.path, wave * 2)
            result = setup_ui.measure_pair((first, second), "A")
        rows = result["renders"]
        self.assertEqual([(item["label"], item["name"]) for item in rows],
                         [("A", "Original tone"), ("B", "Tuned tone")])
        self.assertAlmostEqual(rows[0]["post_chain_trim_db"], 0)
        self.assertEqual(rows[1]["post_chain_trim_db"], -6.1)

    def test_reject_unaligned_pair(self):
        first = listening.Sound("A", Path("a.wav"), "clip-one", (0, 1))
        second = listening.Sound("B", Path("b.wav"), "clip-two", (0, 1))
        with self.assertRaisesRegex(ValueError, "same phrase"):
            setup_ui.measure_pair((first, second), None)

    def test_training_capture_leaves_the_active_phrase_in_place(self):
        root = tk.Tcl()
        selected = object()
        desk = SimpleNamespace(root=root, job=None, leaving=None, ready=Event(), input_channel=1,
                               take=selected, submit=Mock(), set_status=Mock())
        recorder = setup_ui.ExampleRecorder(desk)
        recorder.press(guitar="Charvel")
        args = desk.submit.call_args.args
        self.assertEqual(args[0], "record")
        self.assertEqual(args[2:5], (capture.record_take, 1, "Charvel"))
        self.assertIs(args[5], desk.stop_signal)
        recorder.press()
        self.assertTrue(desk.stop_signal.is_set())
        with TemporaryDirectory() as temporary, patch.object(listening, "takes", return_value=["training"]):
            desk.submit.call_args.kwargs["done"]((Path(temporary), "Clip saved"))
        self.assertIs(desk.take, selected)
        self.assertIsNone(recorder.stop)
        self.assertEqual(desk.takes, ["training"])


if __name__ == "__main__":
    unittest.main()
