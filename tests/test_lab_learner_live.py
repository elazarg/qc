"""Live listening for the Guitar Learner: the rolling dry window, saving only on request, and the worker boundary."""

from pathlib import Path
from queue import Queue
import sys
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from lab import audio
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

    def test_verified_dry_channel_rolls_and_only_saves_on_request(self):
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
            if event["event"] == "learner-state":
                stop.set()

        with (TemporaryDirectory() as folder, patch.object(folders, "SESSIONS", Path(folder)),
              patch.object(guitar_live, "continuous", analyzer),
              patch.object(inputs, "asio_device", return_value=(sd, 8)),
              patch.object(inputs, "recording_channels", return_value=(4, 1, 2)) as channels):
            result = guitar_live.run({"physical_input": 2, "guitar": "Charvel"}, stop, commands, report)
            saved = next(event for event in events if event["event"] == "learner-snapshot")
            saved_path = Path(saved["path"])
            self.assertEqual(saved["request_id"], "one")
            self.assertEqual(saved["seconds"], .75)
            np.testing.assert_array_equal(audio.read_wave(saved_path), dry[-round(.75 * audio.RATE):])
            self.assertEqual([source.path for source in guitar_dataset.take_sources("Charvel")], [saved_path])
            self.assertFalse((Path(folder) / "recordings").exists())
            self.assertFalse(guitar_dataset.records("Charvel", "dk24_hh_toggle"))
        channels.assert_called_once_with(2)
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
        with TemporaryDirectory() as folder, patch.object(folders, "SESSIONS", Path(folder)):
            guitar_live._save_request({"request_id": "mark", "change_id": marked[0], "part": "after"},
                                      window, marked, "Charvel", 1, 1, events.append)
            saved = events[-1]
            self.assertEqual(saved["request_id"], "mark")
            self.assertAlmostEqual(saved["seconds"], .7)
            self.assertIsNone(saved["boundary_seconds"])
            np.testing.assert_array_equal(audio.read_wave(saved["path"])[:, 0], dry[audio.RATE:round(1.7 * audio.RATE)])

    def test_stream_is_closed_when_native_start_fails(self):
        stream = Mock()
        stream.start.side_effect = RuntimeError("driver failed")
        sd = SimpleNamespace(AsioSettings=Mock(), InputStream=Mock(return_value=stream))
        with (patch.object(guitar_live, "continuous", SimpleNamespace()),
              patch.object(inputs, "asio_device", return_value=(sd, 8)),
              patch.object(inputs, "recording_channels", return_value=(1, 3, 4))):
            with self.assertRaisesRegex(RuntimeError, "driver failed"):
                guitar_live.run({"physical_input": 1}, Event(), Queue(), Mock())
        stream.close.assert_called_once()


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

    def test_a_worker_error_is_raised_to_the_window(self):
        failing = 'import json; print(json.dumps({"event": "error", "text": "no dry input"}), flush=True)'
        with self.assertRaisesRegex(RuntimeError, "no dry input"):
            worker.supervise([sys.executable, "-c", failing], Event(), Queue(), Mock())


if __name__ == "__main__":
    unittest.main()
