"""An idle clip belongs to the exact guitar setup and can be replayed through any sound."""

import isolated

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from lab.hearing.audio import RATE, write_wave
from lab.learner import idle


class IdleReferenceTest(unittest.TestCase):
    def test_recording_is_reused_and_separate_for_knobs_pickup_and_wiring(self):
        with TemporaryDirectory() as directory:
            home = Path(directory)
            settings = {"pickup": 1, "wiring": "Series", "volume": 10, "tone": 7}
            key = idle.setup("Charvel", "dk24_hh_toggle", settings)
            audio = np.full((RATE * 3, 3), .0002, dtype=np.float32)
            idle.keep(home, key, audio)
            self.assertEqual(idle.selected(home, "Charvel"), key)
            self.assertEqual(idle.recorded(home, "Charvel"), [key])
            self.assertIsNone(idle.selected(home, "Les Paul"))
            dry, source = idle.reference(home, key)
            self.assertEqual(source, "recorded reference")
            np.testing.assert_array_equal(dry, audio[RATE // 2:, :1])
            for change in ({"pickup": 5}, {"wiring": "Parallel"}, {"tone": 8}):
                other = idle.setup("Charvel", "dk24_hh_toggle", {**settings, **change})
                self.assertIsNone(idle.reference(home, other))
                idle.choose(home, other)
                self.assertEqual(idle.selected(home, "Charvel"), other)
                self.assertIsNotNone(idle.reference(home, key))

    def test_a_labeled_take_with_a_stable_nonzero_gap_supplies_dry_idle_audio(self):
        with TemporaryDirectory() as directory:
            home = Path(directory)
            key = idle.setup("Charvel", "dk24_hh_toggle", {"pickup": 1, "wiring": "Series", "volume": 10, "tone": 7})
            t = np.arange(RATE * 4) / RATE
            wave = np.sin(2 * np.pi * 60 * t).astype(np.float32)[:, None]
            wave[:2 * RATE] *= .0003
            wave[2 * RATE:] *= .1
            path = home / "labeled.wav"
            write_wave(path, wave)
            row = {"layout": key["layout"], "configuration": key["values"], "source_kind": "take", "wav": path}
            with patch.object(idle, "available", return_value=[row]):
                dry, source = idle.reference(home, key)
                self.assertEqual(source, "quiet gap in a labeled take")
                self.assertLess(np.max(np.abs(dry)), .001)
                wave[:2 * RATE] = 0.
                write_wave(path, wave)
                self.assertIsNone(idle.reference(home, key))
