"""The Guitar Learner on a seat: the guitar it works on, recording and listening through the seat, work that
waits its turn, and choosing a take or a WAV in place. Everything is inside a temporary folder; no window is
seen, no driver opened. That it is shown in the real desk's window and fits it is in test_lab_one_window.py."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import json
from pathlib import Path
from threading import Event
import tkinter as tk
import unittest
from unittest.mock import patch

import numpy as np

import support  # noqa: E402
from lab.hearing import audio
from lab.learner import dataset as data, folders, gestures, inputs, worker
from lab.learner.window import GuitarLearner, offered_for

from test_lab_learner_gestures import note
from test_lab_learner_seat import seat_for, settle
from test_lab_learner_takes import four_inputs
from support import hosting

PACKAGE = Path(__file__).resolve().parents[1] / "lab" / "learner"


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

    def test_everything_is_followed_on_the_desks_one_clock(self):
        for name in ("window.py", "teach.py", "gestures.py"):
            source = (PACKAGE / name).read_text(encoding="utf-8")
            self.assertNotIn(".after(", source, name)

    def test_the_learner_is_offered_only_for_a_guitar_it_has_a_layout_and_a_photograph_for(self):
        self.assertEqual(offered_for("Charvel"), ["dk24_hh_toggle", "dk24_blade"])
        for instrument in ("Les Paul", "Bass", "Acoustic", "", None):
            self.assertEqual(offered_for(instrument), [], instrument)


class WindowTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"No display: {error}")
        self.root.withdraw()
        self.seat = seat_for(self, self.root)
        hosting(self.seat.desk, self.root)
        self.learner = None

    def tearDown(self):
        gestures.stop()
        if self.learner is not None:
            self.learner.hide()
            settle(self.seat, lambda: self.seat.job is None and self.learner.job is None, root=self.root, seconds=5)
        self.root.destroy()
        # Free the window's Tk variables here, on the main thread: if a later test's worker thread happens to run
        # the collector, Tk aborts the whole interpreter.
        self.learner = self.root = None
        support.free_now()

    def opened(self):
        self.learner = GuitarLearner(self.seat)
        self.settle()
        return self.learner

    def settle(self, done=None):
        done = done or (lambda: True)
        self.assertTrue(settle(self.seat, lambda: self.learner.job is None and not self.learner.queued and done(),
                               root=self.root))

    def wave(self, name, frequency=220):
        signal = (.05 * np.sin(np.arange(audio.RATE * 2) * (frequency * 2 * np.pi / audio.RATE)))[:, None]
        path = folders.root() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        audio.write_wave(path, signal.astype(np.float32))
        return path

    def test_opens_on_a_seat_reads_the_dataset_and_remembers_the_layout(self):
        data.add("Charvel", "dk24_blade", self.wave("original.wav"), {"pickup": 5, "volume": 10., "tone": 10.})
        learner = self.opened()
        self.assertEqual(learner.TITLE, "Guitar Learner")
        self.assertEqual(learner.frame.winfo_toplevel(), self.root)        # in the desk's window, not one of its own
        self.assertEqual((learner.guitar, learner.layout), ("Charvel", "dk24_hh_toggle"))
        self.assertEqual(learner.count.get(), "0 examples  ·  0 setups")
        self.assertEqual(learner.title.get(), "Check a recording")

        learner.change_layout("dk24_blade")
        self.settle()
        self.assertEqual(folders.layout_choice().read_text(encoding="utf-8").count("dk24_blade"), 1)
        self.assertEqual(learner.count.get(), "1 example  ·  1 setup")
        self.assertIn("Taught blade positions 5", learner.coverage.get())

        learner.toggle_dataset()
        self.assertEqual(learner.table.size(), 1)
        self.assertIn("original", learner.table.get(0))
        self.assertIn("1 examples", learner.dataset_summary.get())
        learner.table.selection_set(0)
        learner.open_dataset_row()                          # the button under the list, not a double click
        self.settle(lambda: learner.source is not None)
        self.assertEqual(learner.source.kind, "saved-excerpt")
        self.assertFalse(learner.dataset_open)              # the recording is on show in its place

    def test_a_take_or_a_wav_is_chosen_in_place(self):
        # In place of the menu of takes and the file dialog: the takes listed, and the WAVs found or a path typed.
        take = folders.recordings() / "clip-20261118-010000-abcdef"
        take.mkdir(parents=True)
        audio.write_wave(take / "dry.wav", (np.concatenate([note(110, "neck", seconds=1.)] * 3))[:, None])
        folders.write_json(take / "clip.json", {"guitar": "Charvel", "duration_seconds": 3.})
        (self.seat.studio.folder / "imports").mkdir(parents=True)      # where the app's own imports are kept
        found = self.wave("found.wav").replace(self.seat.studio.folder / "imports" / "found.wav")
        learner = self.opened()
        self.assertEqual(learner.chooser.winfo_manager(), "")         # shown on request
        learner.toggle_takes()
        self.assertEqual(learner.chooser.winfo_manager(), "pack")
        self.assertEqual(learner.takes.size(), 1)
        learner.takes.selection_set(0)
        learner.open_take()
        self.settle(lambda: learner.source is not None)
        self.assertEqual(learner.source.path, (take / "dry.wav").resolve())
        self.assertEqual(learner.chooser.winfo_manager(), "")         # put away once one is chosen

        learner.toggle_takes()
        self.assertIn("found.wav", learner.wav_choice.list.get(0))
        learner.wav_choice.path.insert(0, str(found.with_name("missing.wav")))
        learner.wav_choice.typed()
        self.assertIn("There is no file at", learner.wav_choice.said.cget("text"))
        learner.wav_choice.list.selection_set(0)
        learner.wav_choice.use()
        self.settle(lambda: learner.source is not None and learner.source.path.name == "found.wav")
        self.assertEqual(learner.source.path.name, "found.wav")

    def test_it_works_on_the_desks_guitar_and_stays_closed_for_one_it_has_no_layout_for(self):
        # Before: every take was stamped "Charvel" and taught into its dataset, whatever the desk had chosen.
        self.seat.studio.state.instrument = "Les Paul"
        learner = self.opened()
        learner.show()
        self.assertFalse(learner.showing())
        self.assertIsNone(self.seat.desk.hosted)
        self.assertIn("no control layout for the Les Paul", self.seat.studio.statuses[-1])
        self.assertIsNone(learner.guitar)
        self.seat.studio.state.instrument = "Charvel"
        learner.show()
        self.settle()
        self.assertTrue(learner.showing())
        self.assertEqual(learner.frame.winfo_manager(), "pack")
        self.assertEqual((learner.guitar, learner.layout), ("Charvel", "dk24_hh_toggle"))

    def test_going_back_to_the_desk_ends_a_recording_and_a_live_listening(self):
        learner = self.opened()
        learner.show()
        learner.stop, learner.live_stop = Event(), Event()
        stop, live = learner.stop, learner.live_stop
        self.seat.desk.back()
        self.assertTrue(stop.is_set() and live.is_set())
        self.assertFalse(learner.showing())
        learner.stop = learner.live_stop = None

    def test_the_knobs_and_the_switches_are_set_with_the_mouse_alone(self):
        # Before: a double click put a knob back, and the arrow keys turned it.
        learner = self.opened()
        for dial in learner.board.dials.values():
            self.assertEqual([key for key in dial.bind() if "Key" in key or "Double" in key], [])
        board = learner.board
        board.set_knob("tone", 4.)
        board.set_knob("volume", 7.5)
        self.assertEqual((board.values["tone"], board.values["volume"]), (4., 7.5))
        board.full_button.invoke()                      # what the double click did, as a button that says so
        self.assertEqual((board.values["tone"], board.values["volume"]), (10., 10.))
        self.assertEqual(board.full_button.cget("text"), "Knobs to 10")

    def test_a_take_is_recorded_through_the_seat_stamped_with_the_desks_guitar_and_opened(self):
        learner = self.opened()
        played = four_inputs(2, seconds=1.5)

        def deliver(seconds, channels, *, stop, ready):
            ready.set()
            stop.wait(10)
            return played, []

        with patch.object(inputs, "record_inputs", deliver):
            learner.record()
            self.assertTrue(settle(self.seat, lambda: learner.ready is not None and learner.ready.is_set(), root=self.root))
            self.seat.tick()
            self.assertEqual(learner.status.get(), "Recording… play the notes, then press Stop.")
            self.assertEqual(self.seat.studio.audio_held_by, "learner")
            learner.record()
            self.settle(lambda: learner.stop is None and learner.source is not None)
        self.assertEqual(self.seat.studio.host.calls, [("inputs", (2,))])
        self.assertIsNone(self.seat.studio.audio_held_by)
        take, = folders.recordings().glob("clip-*")
        self.assertEqual(json.loads((take / "clip.json").read_text(encoding="utf-8"))["guitar"], "Charvel")
        self.assertEqual(learner.source.path, (take / "dry.wav").resolve())
        self.assertIn("Clip saved", self.seat.studio.statuses[-1])
        self.assertEqual(self.seat.watchers, [])

    def test_a_recording_that_fails_is_said_and_leaves_the_window_free(self):
        learner = self.opened()
        with patch.object(inputs, "record_inputs", side_effect=RuntimeError("Expected one Quad Cortex ASIO device.")):
            learner.record()
            self.settle(lambda: learner.stop is None)
        self.assertEqual(learner.status.get(), "Expected one Quad Cortex ASIO device.")
        self.assertIsNone(self.seat.studio.audio_held_by)
        self.assertEqual(str(learner.record_button.cget("state")), "normal")
        self.assertIn("Expected one Quad Cortex ASIO device.", (self.seat.studio.folder / "errors.log").read_text(encoding="utf-8"))

    def test_listening_live_gets_its_channel_from_the_engine_and_gives_the_audio_back(self):
        learner = self.opened()
        runs, release = [], Event()

        def run(config, stop, commands, report):
            runs.append(config)
            report({"event": "learner-ready", "dry_channel": config["dry_channel"], "sample_rate": audio.RATE})
            stop.wait(10)
            return {"dropouts": 0}

        with patch.object(worker, "run", run):
            learner.toggle_live()
            self.assertTrue(settle(self.seat, lambda: runs and "USB dry input 2" in learner.live_status.get(), root=self.root))
            self.assertEqual((runs[0]["dry_channel"], runs[0]["guitar"], runs[0]["physical_input"]), (2, "Charvel", 2))
            self.assertEqual(self.seat.studio.audio_held_by, "learner")
            learner.toggle_live()
            self.settle(lambda: learner.live_stop is None)
        self.assertEqual(learner.status.get(), "Live listening stopped.")
        self.assertIsNone(self.seat.studio.audio_held_by)
        self.assertEqual(self.seat.watchers, [])
        release.set()

    def test_work_asked_for_while_other_work_runs_waits_its_turn(self):
        # Before: it was dropped without a word, so a clip kept while the examples were being read was never opened.
        learner = self.opened()
        gate, order = Event(), []
        learner.work("first…", lambda: gate.wait(10) and "first", order.append)
        learner.work("second…", lambda: "second", order.append)
        learner.work("third…", lambda: "third", order.append)
        self.assertEqual(len(learner.queued), 2)
        gate.set()
        self.settle(lambda: len(order) == 3)
        self.assertEqual(order, ["first", "second", "third"])

    def test_two_clips_of_one_performance_cannot_be_example_and_check(self):
        learner = self.opened()
        for key, begins in (("010000", 0.), ("010001", 1.)):
            folder = folders.live_recordings() / f"clip-20261118-{key}-abcdef"
            folder.mkdir(parents=True)
            playing = np.concatenate([note(110, "neck", seconds=1., level=level) for level in (.08, .07, .06, .05)])
            first_sample = round(begins * audio.RATE)
            audio.write_wave(folder / "dry.wav", playing[first_sample:first_sample + 3 * audio.RATE, None])
            folders.write_json(folder / "clip.json", {"guitar": "Charvel", "duration_seconds": 3., "live_run": "run-a",
                                                      "run_start_seconds": begins})
        first, second = (data.source_from_take("Charvel", folder) for folder in sorted(folders.live_recordings().glob("clip-*")))
        learner.select_source(first)
        self.settle(lambda: learner.excerpt is not None)
        learner.add("example")
        self.settle(lambda: len(learner.examples) == 1)
        learner.select_source(second)
        self.settle(lambda: learner.source is not None and learner.source.path == second.path)
        self.assertEqual(learner.status.get(), "The learner has heard this audio in training.")
        learner.add("test")
        self.settle()
        self.assertIn("other role", learner.status.get())
        self.assertEqual(learner.tests, [])


def tearDownModule():
    # Tk objects left as garbage here must be freed on the main thread: if a later test's worker thread runs
    # the collector, Tcl aborts the whole interpreter ("Tcl_AsyncDelete").
    support.free_now()


if __name__ == "__main__":
    unittest.main()
