import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tone_lab import guitar_ui, session


class GuitarCalibrationFlowTest(unittest.TestCase):
    def setUp(self):
        # Tcl variables exercise the workflow without creating a window or opening audio.
        interpreter = tk.Tcl()
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        sessions = patch.object(session, "SESSIONS", self.folder)
        sessions.start()
        self.addCleanup(sessions.stop)
        self.info = {}
        self.recorder = SimpleNamespace(stop=None, ready=Event(), press=Mock(side_effect=self.press))
        self.panel = panel = guitar_ui.GuitarInputPanel.__new__(guitar_ui.GuitarInputPanel)
        panel.review = SimpleNamespace(recorder=self.recorder, latest=lambda **_: self.info,
                                       busy=lambda: self.recorder.stop is not None)
        panel.future, panel.live_running, panel.profile = None, False, None
        panel.examples, panel.counts, panel.examples_changed = [], {}, False
        panel.latest_dry, panel.example_source = None, None
        panel.recording_before = guitar_ui.NO_RECORDING
        panel.recording_kind = panel.recording_setting = None
        panel.instrument = tk.StringVar(interpreter, "Charvel")
        panel.control = tk.StringVar(interpreter, "Pickup selector")
        panel.loaded_selection = panel.selection()[2], "pickup"
        for name in ("status", "latest_text", "source_text", "progress_text", "next_step", "example_label", "control_hint",
                     "start_seconds", "end_seconds", "estimate_text"):
            setattr(panel, name, tk.StringVar(interpreter))
        for name in ("set_buttons", "label_box", "instrument_box", "example_list"):
            setattr(panel, name, Mock())

    def press(self, *, guitar=None):
        if self.recorder.stop is None:
            self.recorder.stop = Event()
        else:
            self.recorder.stop.set()
        self.panel.capture_changed()

    def take(self, name):
        path = self.folder / f"{name}.wav"
        path.touch()
        self.info = {"dry": str(path), "song": name, "duration_seconds": 3}
        return path

    def finish_recording(self):
        self.recorder.stop = None
        self.panel.capture_changed()

    def test_record_example_uses_shared_transport_and_saves_chosen_setting(self):
        self.take("Older take")
        self.panel.example_label.set("Neck")
        self.panel.record_example()
        self.assertIsNone(self.panel.example_source)
        self.assertIn("Wait for Recording", self.panel.source_text.get())
        self.recorder.ready.set()
        self.panel.capture_changed()
        self.assertIn("Play your notes", self.panel.source_text.get())
        self.panel.record_example()
        self.assertTrue(self.recorder.stop.is_set())
        self.assertEqual(self.recorder.press.call_count, 2)
        new_take = self.take("Neck take")
        self.finish_recording()
        self.assertIsNone(self.panel.example_source)
        data = json.loads((self.panel.loaded_selection[0] / "pickup-examples.json").read_text(encoding="utf-8"))
        self.assertEqual(data, [{"label": "Neck", "wav": str(new_take)}])
        self.assertEqual(self.panel.progress_text.get(), "Neck ×1")
        self.assertIn("Bridge", self.panel.next_step.get())
        self.assertEqual(self.panel.example_label.get(), "Bridge")

    def test_failed_recording_does_not_attach_the_previous_take(self):
        self.take("Older take")
        self.panel.example_label.set("Neck")
        self.panel.record_example()
        self.finish_recording()
        self.assertIsNone(self.panel.example_source)
        self.assertIn("No new dry recording", self.panel.source_text.get())

    def test_guided_capture_uses_the_returned_clip_even_if_another_take_is_newer(self):
        self.take("Older take")
        self.panel.example_label.set("Neck")
        self.panel.record_example()
        owned_capture = self.take("Guided take")
        self.panel.review.completed_example = Mock(return_value=self.info)
        self.take("Concurrent take")
        self.finish_recording()
        self.assertEqual(self.panel.examples[0]["wav"], str(owned_capture))

    def test_fresh_check_inspects_exact_clip_without_adding_a_training_example(self):
        self.take("Older take")
        self.panel.profile = {"labels": ["Neck", "Bridge"]}
        self.panel.inspect_path = Mock()
        self.panel.record_check()
        self.panel.record_example()
        self.assertFalse(self.recorder.stop.is_set())
        self.panel.record_check()
        self.assertTrue(self.recorder.stop.is_set())
        fresh = self.take("Fresh check")
        self.finish_recording()
        self.panel.inspect_path.assert_called_once_with(fresh)
        self.assertEqual(self.panel.examples, [])

    def test_latest_take_from_another_guitar_is_explained(self):
        self.take("Unrelated take")
        self.info["guitar"] = "Les Paul"
        self.panel.select_latest()
        self.assertIsNone(self.panel.example_source)
        self.assertIn("Les Paul", self.panel.status.get())

    def test_labeled_source_stays_fixed_when_a_newer_take_appears(self):
        captured = self.take("Chosen take")
        self.panel.select_latest()
        self.take("Newer take")
        self.panel.capture_changed()
        self.panel.example_label.set("Bridge")
        self.panel.add_example()
        self.assertEqual(self.panel.examples[0]["wav"], str(captured))

    def test_ordered_ranges_and_casefolded_settings(self):
        self.take("Two ranges")
        self.panel.select_latest()
        self.panel.example_label.set("Neck")
        self.panel.end_seconds.set("1")
        self.panel.add_example()

        self.panel.select_latest()
        self.panel.example_label.set("Bridge")
        self.panel.start_seconds.set("0.5")
        self.panel.add_example()
        self.assertIn("already labeled Neck", self.panel.status.get())
        self.assertEqual(len(self.panel.examples), 1)

        self.panel.example_label.set("neck")
        self.panel.start_seconds.set("1")
        self.panel.end_seconds.set("2")
        self.panel.add_example()
        self.assertEqual(self.panel.examples[1]["label"], "Neck")
        self.assertEqual(self.panel.progress_text.get(), "Neck ×2")
