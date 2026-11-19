"""Recording a take for the Guitar Learner, with the unit's inputs stood in for."""

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import json
import unittest
from unittest.mock import patch

import numpy as np

from lab import audio
from lab.learner import dataset as data, folders, inputs, takes


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
        guard = patch.object(folders, "SESSIONS", self.home)
        guard.start()
        self.addCleanup(guard.stop)

    def test_a_verified_take_keeps_dry_and_wet_and_the_learner_can_use_it(self):
        context = {"wet_preset": "Lead", "wet_scene": "A"}
        with (patch.object(inputs, "recording_channels", return_value=((2, 3, 4), context)),
              patch.object(inputs, "record_inputs", return_value=four_inputs(2))):
            folder, message = takes.record_take(2, "Charvel", Event(), Event())
        self.assertTrue(message.startswith("Clip saved · 1.0s"), message)
        self.assertEqual(folder.parent, self.home / "recordings")
        info = json.loads((folder / "clip.json").read_text(encoding="utf-8"))
        self.assertEqual((info["dry_channel"], info["wet_channels"], info["wet_preset"]), (2, [3, 4], "Lead"))
        self.assertEqual(audio.read_wave(folder / "dry.wav").shape[1], 1)
        self.assertEqual(audio.read_wave(folder / "wet.wav").shape[1], 2)
        self.assertEqual(takes.clip_paths(), [folder / "dry.wav"])
        source = data.source_from_result("Charvel", (folder, message))
        self.assertEqual(source.path, folder / "dry.wav")

    def test_with_the_editor_open_the_pair_is_inferred_from_the_signal(self):
        with (patch.object(inputs, "recording_channels", side_effect=inputs.EditorActive("editor")),
              patch.object(inputs, "record_inputs", return_value=four_inputs(1))):
            folder, message = takes.record_take(1, "Charvel", Event(), Event())
        self.assertIn("preset unverified", message)
        self.assertTrue((folder / "inputs.wav").is_file())

    def test_unresolved_routing_keeps_all_four_inputs_and_is_not_offered_as_dry(self):
        with (patch.object(inputs, "recording_channels", side_effect=RuntimeError("no USB")),
              patch.object(inputs, "record_inputs", return_value=four_inputs(1))):
            folder, message = takes.record_take(1, "Charvel", Event(), Event())
        self.assertIn("routing needs checking", message)
        self.assertEqual(data.take_sources("Charvel"), [])

    def test_too_short_a_take_is_refused(self):
        with (patch.object(inputs, "recording_channels", return_value=((1, 3, 4), {})),
              patch.object(inputs, "record_inputs", return_value=four_inputs(1, seconds=.2))):
            with self.assertRaisesRegex(ValueError, "half a second"):
                takes.record_take(1, "Charvel", Event(), Event())

    def test_editor_pair_needs_a_quiet_neighbour(self):
        self.assertEqual(takes.infer_editor_pair(four_inputs(1), 1), (1, 3, 4))
        noisy = four_inputs(1)
        noisy[:, 1] = noisy[:, 0]
        self.assertIsNone(takes.infer_editor_pair(noisy, 1))


if __name__ == "__main__":
    unittest.main()
