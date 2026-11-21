"""Recording a take for the Guitar Learner, with the driver stood in for and the engine asked through a host."""
import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch

import numpy as np

from lab.hearing import audio
from lab.learner import dataset as data, folders, inputs, takes

from test_lab_learner_seat import FakeHost


def four_inputs(dry_channel, *, seconds=1.):
    t = np.arange(round(seconds * audio.RATE)) / audio.RATE
    samples = np.zeros((len(t), 4), dtype=np.float32)
    samples[:, dry_channel - 1] = .1 * np.sin(2 * np.pi * 110 * t)
    wet = (3, 4) if dry_channel in (1, 2) else (1, 2)
    for channel in wet:
        samples[:, channel - 1] = .2 * np.sin(2 * np.pi * 110 * t + .3)
    return samples


class TakeTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        folders.use(self.home)
        self.addCleanup(folders.use, None)
        self.host = FakeHost()

    def record(self, played, physical=2, dropouts=()):
        with patch.object(inputs, "record_inputs", return_value=(played, list(dropouts))):
            return takes.record_take(physical, "Charvel", Event(), Event(),
                                     routing=lambda: inputs.routing(self.host, physical))

    def test_the_engine_says_which_inputs_are_dry_and_the_learner_can_use_the_take(self):
        folder, message = self.record(four_inputs(2))
        self.assertEqual(self.host.calls, [("inputs", (2,))])           # asked of the engine: the unit is never opened
        self.assertTrue(message.startswith("Clip saved · 1.0s"), message)
        self.assertEqual(folder.parent, self.home / "recordings")
        info = json.loads((folder / "clip.json").read_text(encoding="utf-8"))
        self.assertEqual((info["dry_channel"], info["wet_channels"]), (2, [3, 4]))
        self.assertEqual((info["wet_preset"], info["wet_scene"], info["coros"]), ("Lead", "B", "4.1.1"))
        self.assertEqual(audio.read_wave(folder / "dry.wav").shape[1], 1)
        self.assertEqual(audio.read_wave(folder / "wet.wav").shape[1], 2)
        self.assertEqual(takes.clip_paths(), [folder / "dry.wav"])
        self.assertEqual(takes.latest_clip()["dry"], str(folder / "dry.wav"))
        source = data.source_from_result("Charvel", (folder, message))
        self.assertEqual(source.path, folder / "dry.wav")

    def test_the_learner_has_no_way_to_open_the_unit_itself(self):
        for name in ("connect", "channels_from", "editor_running", "EditorActive", "recording_channels"):
            self.assertFalse(hasattr(inputs, name), name)
        source = Path(inputs.__file__).read_text(encoding="utf-8")
        self.assertNotIn("qc_mcp", source)
        self.assertNotIn("QuadCortex(", source)

    def test_without_an_answer_from_the_engine_the_pair_is_found_from_the_signal_and_said(self):
        self.host.error = RuntimeError("The part of the app that holds the Quad Cortex stopped.")
        folder, message = self.record(four_inputs(1), physical=1)
        self.assertIn("found from the signal", message)
        self.assertTrue((folder / "inputs.wav").is_file())
        info = json.loads((folder / "clip.json").read_text(encoding="utf-8"))
        self.assertEqual((info["usb_mapping_source"], info["dry_channel"]), ("inferred-from-signal", 1))
        self.assertIn("stopped", info["routing_unread"])
        self.assertEqual(data.source_from_take("Charvel", folder).mapping, "inferred-from-signal")

    def test_when_the_signal_does_not_show_it_all_four_inputs_are_kept_and_not_offered_as_dry(self):
        self.host.error = RuntimeError("no unit")
        noisy = four_inputs(1)
        noisy[:, 1] = noisy[:, 0]
        folder, message = self.record(noisy, physical=1)
        self.assertIn("routing needs checking", message)
        self.assertTrue((folder / "inputs.wav").is_file())
        self.assertFalse((folder / "dry.wav").exists())
        self.assertEqual(data.take_sources("Charvel"), [])

    def test_a_dropout_does_not_cost_the_take(self):
        folder, message = self.record(four_inputs(2, seconds=3.), dropouts=(1.25,))
        self.assertIn("1 dropout in the audio", message)
        info = json.loads((folder / "clip.json").read_text(encoding="utf-8"))
        self.assertEqual(info["dropouts_seconds"], [1.25])
        self.assertEqual(len(audio.read_wave(folder / "dry.wav")), 3 * audio.RATE)

    def test_too_short_a_take_is_refused(self):
        with self.assertRaisesRegex(ValueError, "half a second"):
            self.record(four_inputs(1, seconds=.2), physical=1)

    def test_a_pair_is_found_from_the_signal_only_beside_a_quiet_neighbour(self):
        self.assertEqual(takes.infer_pair(four_inputs(1), 1), (1, 3, 4))
        noisy = four_inputs(1)
        noisy[:, 1] = noisy[:, 0]
        self.assertIsNone(takes.infer_pair(noisy, 1))


class RecordingTest(unittest.TestCase):
    """`record_inputs` with a stand-in for the driver: an overflow is noted, and the audio after it is kept."""

    def test_the_recording_goes_on_past_a_dropped_block(self):
        blocks = [np.full((4800, 4), value, dtype=np.float32) for value in (.1, .2, .3, .4, .5)]
        state = {}

        class Stream:
            def __init__(self, **options):
                state["callback"], state["finished"] = options["callback"], options["finished_callback"]

            def __enter__(self):
                for index, block in enumerate(blocks):
                    state["callback"](block, len(block), None, SimpleNamespace(input_overflow=index == 2))
                stop.set()
                return self

            def __exit__(self, *reason):
                return False

        class Stop(Exception):
            pass

        sd = SimpleNamespace(InputStream=Stream, AsioSettings=Mock(), CallbackStop=Stop)
        stop, ready = Event(), Event()
        with patch.object(inputs, "asio_device", return_value=(sd, 3)):
            samples, dropouts = inputs.record_inputs(audio.MAX_SECONDS, (1, 2, 3, 4), stop=stop, ready=ready)
        self.assertTrue(ready.is_set())
        self.assertEqual(len(samples), 5 * 4800)                 # nothing after the dropout was thrown away
        self.assertEqual(dropouts, [.2])
        self.assertAlmostEqual(float(samples[-1, 0]), .5)


class RoutingTest(unittest.TestCase):
    def test_the_engines_answer_is_checked_and_only_what_can_be_written_down_is_kept(self):
        host = FakeHost()
        host.inputs = {"channels": [3, 1, 2], "preset": "Clean", "scene": 9, "position": object(), "firmware": None}
        self.assertEqual(inputs.routing(host, 1), ((3, 1, 2), {"wet_preset": "Clean"}))
        host.inputs = {"channels": [1, 1, 2]}
        with self.assertRaisesRegex(RuntimeError, "which USB channels"):
            inputs.routing(host, 1)
        with self.assertRaisesRegex(ValueError, "input 1 or 2"):
            inputs.routing(host, 3)


if __name__ == "__main__":
    unittest.main()
