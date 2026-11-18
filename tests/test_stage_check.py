from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from tone_lab import audio, session, stage_check


class StageCheckTest(unittest.TestCase):
    def test_level_matched_distinct_eq_preview_preserves_source(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wet.wav"
            t = np.arange(audio.RATE, dtype=float) / audio.RATE
            a = .09 * np.sin(2 * np.pi * 45 * t) + .08 * np.sin(2 * np.pi * 580 * t)
            b = .09 * np.sin(2 * np.pi * 6100 * t)
            audio.write_wave(source, np.column_stack((a + b, a - b)))
            original = source.read_bytes()
            preview = stage_check.create_preview(source, root / "audition", context="mix")
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(set(preview["auditions"]), set("ABC"))
            self.assertTrue(Path(preview["file"]).is_file())
            manifest = session.load_session(Path(preview["folder"]))
            low, high = manifest["candidates"][1:]
            self.assertEqual(low["post_eq"][0]["type"], "high_pass")
            self.assertEqual(high["post_eq"][0]["type"], "high_shelf")
            self.assertLess(low["metrics"]["rms_dbfs"], high["metrics"]["rms_dbfs"])
            levels = [audio.describe(audio.read_wave(Path(item["file"])))["lufs"]
                      for item in preview["auditions"].values()]
            self.assertLess(max(levels) - min(levels), .03)
            self.assertTrue(all(x <= -22.95 for x in levels))
            with self.assertRaisesRegex(ValueError, "empty"):
                stage_check.create_preview(source, root / "audition")

    def test_rejects_clipped_or_silent_source(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bad.wav"
            for value in (0, 1):
                audio.write_wave(source, np.full((audio.RATE, 1), value))
                with self.assertRaisesRegex(ValueError, "unclipped"):
                    stage_check.create_preview(source, root / "audition")
                self.assertFalse((root / "audition").exists())
