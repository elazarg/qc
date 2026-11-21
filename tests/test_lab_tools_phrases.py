"""Keeping a take as a phrase: the dry guitar alone, checked, with the processed return beside it."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import numpy as np

from lab.hearing.audio import read_wave
from lab.tools import phrases


def take(dry_level=.2, wet_level=.5, seconds=1.):
    """A take as the engine returns it: dry guitar, then the processed return left and right."""
    time = np.arange(round(48000 * seconds)) / 48000
    note = np.sin(2 * np.pi * 196 * time)
    return np.column_stack((dry_level * note, wet_level * note, wet_level * note)).astype(np.float32)


class KeepTest(unittest.TestCase):
    def test_the_phrase_is_the_dry_guitar_alone_and_its_peak_is_the_dry_guitar_s(self):
        # Regression: `python -m lab record` kept all three channels as "dry", and gave the return's peak.
        with TemporaryDirectory() as folder:
            info, dry, level = phrases.keep(folder, take(), instrument="Charvel", setting="Bridge")
            target = Path(folder) / "phrases" / info["id"]
            self.assertEqual(read_wave(target / "dry.wav").shape, (48000, 1))
            self.assertEqual(dry.shape, (48000, 1))
            self.assertAlmostEqual(level, 20 * np.log10(.2), delta=.05)
            self.assertEqual(json.loads((target / "phrase.json").read_text(encoding="utf-8")), info)
            self.assertEqual((info["instrument"], info["setting"], info["seconds"]), ("Charvel", "Bridge", 1.0))
            self.assertEqual(read_wave(target / "wet.wav").shape, (48000, 2))
            self.assertEqual(info["wet"], {"sound": None, "name": "the preset on the unit"})

    def test_the_return_is_named_for_the_sound_it_was_heard_through_and_left_out_when_silent(self):
        with TemporaryDirectory() as folder:
            heard = SimpleNamespace(id="abc", name="Plexi")
            info, _, _ = phrases.keep(folder, take(), instrument="Charvel", setting="Neck", heard_through=heard)
            self.assertEqual(info["wet"], {"sound": "abc", "name": "Plexi"})
            quiet, _, _ = phrases.keep(folder, take(wet_level=0.), instrument="Charvel", setting="Neck")
            self.assertNotIn("wet", quiet)
            self.assertFalse((Path(folder) / "phrases" / quiet["id"] / "wet.wav").exists())

    def test_a_take_with_nothing_in_it_or_clipped_is_not_kept(self):
        with TemporaryDirectory() as folder:
            with self.assertRaisesRegex(phrases.NotKept, "Nothing came in"):
                phrases.keep(folder, take(dry_level=.0005), instrument="Charvel", setting="Bridge")
            with self.assertRaisesRegex(phrases.NotKept, "clipped"):
                phrases.keep(folder, take(dry_level=1.), instrument="Charvel", setting="Bridge")
            self.assertFalse((Path(folder) / "phrases").exists())
        self.assertTrue(issubclass(phrases.NotKept, ValueError))    # said in one line by the command line

    def test_a_mono_take_is_its_own_dry_guitar(self):
        dry, level = phrases.dry_of(take()[:, 0])
        self.assertEqual(dry.shape, (48000, 1))
        self.assertAlmostEqual(level, 20 * np.log10(.2), delta=.05)


if __name__ == "__main__":
    unittest.main()
