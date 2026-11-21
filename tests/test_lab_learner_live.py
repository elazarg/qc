"""Live listening for the Guitar Learner: the rolling dry window, saving only on request, and the worker boundary."""

from pathlib import Path
from queue import Queue
import sys
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import json

import numpy as np

from lab.hearing import audio
from lab.learner import dataset as guitar_dataset, folders, inputs, live as guitar_live, worker
from lab.learner.live import DryWindow


class LiveDryTest(unittest.TestCase):
    def test_recent_window_keeps_order_and_only_the_current_contiguous_run(self):
        window = DryWindow(5)
        window.append(np.array([1., 2., 3.]), 1.)
        self.assertIsNone(window.recent(4, minimum=4))
        window.append(np.array([4., 5., 6.]), 2.)
        generation, at, dry, gaps, filled = window.recent(3)
        np.testing.assert_array_equal(dry, [4., 5., 6.])
        self.assertEqual((at, gaps, filled), (2., 0, 5))
        self.assertIsNone(window.recent(generation=generation))
        window.append(np.array([7., 8.]), 4., gap=True)
        _, _, dry, gaps, filled = window.recent()
        np.testing.assert_array_equal(dry, [7., 8.])
        self.assertEqual((gaps, filled), (1, 2))

    def test_window_preserves_sample_order_and_drops_discontinuous_history(self):
        window = DryWindow(5)
        window.append(np.array([1., 2., 3.]), 1.)
        self.assertIsNone(window.latest(0))
        window.append(np.array([4., 5., 6.]), 2.)
        generation, timestamp, sample = window.latest(0)
        np.testing.assert_equal(sample, [2, 3, 4, 5, 6])
        self.assertEqual(timestamp, 2.)
        self.assertIsNone(window.latest(generation))
        window.append(np.array([7.]), 6., gap=True)
        self.assertIsNone(window.latest(generation))
        window.append(np.array([8., 9., 10., 11.]), 7.)
        np.testing.assert_equal(window.latest(generation)[2], [7, 8, 9, 10, 11])

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        folders.use(self.folder)
        self.addCleanup(folders.use, None)

    def test_the_dry_channel_the_desk_named_rolls_and_only_saves_on_request(self):
        stop, commands, events = Event(), Queue(), []
        dry = np.linspace(-.06, .1, audio.RATE, dtype=np.float32)[:, None]
        stream = Mock(active=True)
        tracker = Mock()
        tracker.update.return_value = {"mode": "listening", "estimate": None, "change": None,
                                       "reason": "Listening for a dry note."}
        analyzer = SimpleNamespace(Tracker=Mock(return_value=tracker))
        sd = SimpleNamespace(AsioSettings=Mock(), InputStream=Mock(return_value=stream))

        def start():
            callback = sd.InputStream.call_args.kwargs["callback"]
            callback(dry, len(dry), None, SimpleNamespace(input_overflow=False))

        stream.start.side_effect = start

        def report(event):
            events.append(event)
            if event["event"] == "learner-ready":
                self.assertEqual(event["dry_channel"], 4)
                self.assertFalse(guitar_dataset.live_recordings().exists())
                commands.put({"event": "learner-snapshot", "request_id": "one", "seconds": .75})
                commands.put({"event": "learner-snapshot", "request_id": "two", "seconds": .5})
            kinds = [seen["event"] for seen in events]
            if kinds.count("learner-snapshot") == 2 and "learner-state" in kinds:
                stop.set()

        folder = self.folder
        with (patch.object(guitar_live, "continuous", analyzer),
              patch.object(inputs, "asio_device", return_value=(sd, 8))):
            result = guitar_live.run({"physical_input": 2, "guitar": "Charvel", "dry_channel": 4}, stop, commands,
                                     report)
            saved, again = (event for event in events if event["event"] == "learner-snapshot")
            saved_path = Path(saved["path"])
            self.assertEqual(saved["request_id"], "one")
            self.assertEqual(saved["seconds"], .75)
            np.testing.assert_array_equal(audio.read_wave(saved_path), dry[-round(.75 * audio.RATE):])
            self.assertEqual({source.path for source in guitar_dataset.take_sources("Charvel")},
                             {saved_path, Path(again["path"])})
            # Both clips are of the same run of listening, each with where it starts in it: one performance.
            notes = [json.loads(Path(event["folder"], "clip.json").read_text(encoding="utf-8")) for event in (saved, again)]
            self.assertEqual(notes[0]["live_run"], notes[1]["live_run"])
            self.assertAlmostEqual(notes[1]["run_start_seconds"] - notes[0]["run_start_seconds"], .25, places=2)
            first, second = (guitar_dataset.inspect(guitar_dataset.source_from_take("Charvel", event["folder"]))
                             for event in (saved, again))
            self.assertEqual(first.source_id, second.source_id)
            self.assertFalse((Path(folder) / "recordings").exists())
            self.assertFalse(guitar_dataset.records("Charvel", "dk24_hh_toggle"))
        sd.AsioSettings.assert_called_once_with([3])
        self.assertEqual(sd.InputStream.call_args.kwargs["channels"], 1)
        stream.close.assert_called_once()
        self.assertEqual(result["dropouts"], 0)
        self.assertEqual(len(next(event for event in events if event["event"] == "learner-state")["peaks"]), 64)

    def test_marked_change_keeps_the_requested_side_without_teaching_it(self):
        dry = np.concatenate((np.full(audio.RATE, .04), np.full(audio.RATE, .08))).astype(np.float32)
        snapshot = (1, 10., dry, 0, len(dry))
        marked = guitar_live._marked_change(snapshot, {"start": 8.1, "end": 9.7, "at": 9.})
        window = DryWindow(len(dry))
        events = []
        guitar_live._save_request({"request_id": "mark", "change_id": marked[0], "part": "after"},
                                  window, marked, "Charvel", 1, 1, events.append, ("run-a", 2.))
        saved = events[-1]
        self.assertEqual(saved["request_id"], "mark")
        self.assertAlmostEqual(saved["seconds"], .7)
        self.assertIsNone(saved["boundary_seconds"])
        np.testing.assert_array_equal(audio.read_wave(saved["path"])[:, 0], dry[audio.RATE:round(1.7 * audio.RATE)])
        note = json.loads(Path(saved["folder"], "clip.json").read_text(encoding="utf-8"))
        # The window ends at 10 s and is 2 s long; the change is at 9 s; listening began at 2 s.
        self.assertEqual((note["live_run"], note["run_start_seconds"]), ("run-a", 7.))

    def test_stream_is_closed_when_native_start_fails(self):
        stream = Mock()
        stream.start.side_effect = RuntimeError("driver failed")
        sd = SimpleNamespace(AsioSettings=Mock(), InputStream=Mock(return_value=stream))
        with (patch.object(guitar_live, "continuous", SimpleNamespace()),
              patch.object(inputs, "asio_device", return_value=(sd, 8))):
            with self.assertRaisesRegex(RuntimeError, "driver failed"):
                guitar_live.run({"physical_input": 1, "dry_channel": 1}, Event(), Queue(), Mock())
        stream.close.assert_called_once()

    def test_listening_never_opens_the_unit_and_needs_the_channel_from_the_desk(self):
        self.assertFalse(hasattr(inputs, "connect") or hasattr(inputs, "recording_channels"))
        with patch.object(inputs, "asio_device", side_effect=AssertionError("the driver must not be opened")):
            with self.assertRaisesRegex(ValueError, "which USB channel"):
                guitar_live.run({"physical_input": 1, "guitar": "Charvel"}, Event(), Queue(), Mock())


# A stand-in for the worker process: it says it is ready, answers a kept clip, and stops when told.
FAKE_WORKER = r"""
import json, sys
print(json.dumps({"event": "learner-ready", "dry_channel": 1}), flush=True)
for line in sys.stdin:
    event = json.loads(line)
    if event["event"] == "learner-snapshot":
        print(json.dumps({"event": "learner-snapshot", "request_id": event["request_id"], "path": None}), flush=True)
    if event["event"] == "stop":
        print(json.dumps({"event": "done", "audio": {"dropouts": 0}}), flush=True)
        break
"""


class WorkerBoundaryTest(unittest.TestCase):
    def test_the_worker_starts_from_this_package(self):
        self.assertEqual(worker.worker_command()[-2:], ["-m", "lab.learner.worker"])

    def test_commands_go_over_and_events_come_back_until_stop(self):
        stop, commands, events = Event(), Queue(), []

        def report(event):
            events.append(event)
            if event["event"] == "learner-ready":
                commands.put({"event": "learner-snapshot", "request_id": "r1", "seconds": 8})
            elif event["event"] == "learner-snapshot":
                stop.set()

        result = worker.supervise([sys.executable, "-c", FAKE_WORKER], stop, commands, report)
        self.assertEqual(result, {"dropouts": 0})
        self.assertEqual([event["event"] for event in events], ["learner-ready", "learner-snapshot"])
        self.assertEqual(events[1]["request_id"], "r1")

    def test_gesture_listening_events_come_back_too(self):
        script = ('import json, sys\n'
                  'print(json.dumps({"event": "guitar-state", "label": "Neck"}), flush=True)\n'
                  'sys.stdin.readline()\n'
                  'print(json.dumps({"event": "done", "audio": {}}), flush=True)\n')
        stop, events = Event(), []

        def report(event):
            events.append(event)
            stop.set()

        self.assertEqual(worker.supervise([sys.executable, "-c", script], stop, Queue(), report), {})
        self.assertEqual(events, [{"event": "guitar-state", "label": "Neck"}])

    def test_the_worker_process_keeps_its_data_and_writes_its_faults_where_the_desk_said(self):
        # Before: the worker's faults went to a log with no handler, and its stderr was thrown away.
        said = []
        with TemporaryDirectory() as directory:
            home = Path(directory)
            settings = home / "session.json"
            settings.write_text(json.dumps({"mode": "gestures", "physical_input": 1, "dry_channel": 1,
                                            "profile": str(home / "missing.json"), "folder": str(home / "learner"),
                                            "log": str(home / "errors.log")}), encoding="utf-8")
            self.addCleanup(folders.use, None)
            self.addCleanup(folders.log_to, None)
            with patch.object(worker, "emit", said.append), patch.object(worker, "read_commands"):
                self.assertEqual(worker.main([str(settings)]), 1)
            self.assertEqual(folders.root(), home / "learner")
            self.assertTrue((home / "learner" / "live-driver.log").is_file())
            written = (home / "errors.log").read_text(encoding="utf-8")
            self.assertIn("Learner live worker failed", written)
            self.assertIn("missing.json", written)
            self.assertEqual(said[-1]["event"], "error")

    def test_the_settings_sent_to_the_worker_say_where_data_and_faults_go(self):
        sent = {}

        def supervise(argv, stop, commands, report):
            sent.update(json.loads(Path(argv[-1]).read_text(encoding="utf-8")))
            return {}

        with TemporaryDirectory() as directory:
            folders.use(Path(directory) / "learner")
            folders.log_to(Path(directory) / "errors.log")
            self.addCleanup(folders.use, None)
            self.addCleanup(folders.log_to, None)
            with patch.object(worker, "supervise", supervise):
                worker.run({"guitar": "Charvel", "dry_channel": 2}, Event(), Queue(), Mock())
            self.assertEqual((sent["folder"], sent["log"], sent["dry_channel"]),
                             (str(Path(directory) / "learner"), str(Path(directory) / "errors.log"), 2))

    def test_a_worker_error_is_raised_to_the_window(self):
        failing = 'import json; print(json.dumps({"event": "error", "text": "no dry input"}), flush=True)'
        with self.assertRaisesRegex(RuntimeError, "no dry input"):
            worker.supervise([sys.executable, "-c", failing], Event(), Queue(), Mock())


if __name__ == "__main__":
    unittest.main()
