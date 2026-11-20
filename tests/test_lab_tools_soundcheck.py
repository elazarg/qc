"""Setup and sound check: the stage EQ check, the take check, and asking the simulated unit.

Ported from test_stage_check."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from lab.hearing.audio import RATE, read_wave
from lab.hearing.measure import measure
from lab.tools import soundcheck

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lab_tools_support import PLEXI, V30, chain, host, phrase  # noqa: E402


class StageCheckTest(unittest.TestCase):
    def wet(self):
        t = np.arange(RATE, dtype=float) / RATE
        a = .09 * np.sin(2 * np.pi * 45 * t) + .08 * np.sin(2 * np.pi * 580 * t)
        b = .09 * np.sin(2 * np.pi * 6100 * t)
        return np.column_stack((a + b, a - b)).astype(np.float32)

    def test_two_distinct_eq_moves_are_level_matched_and_the_source_is_kept(self):
        wet = self.wet()
        original = wet.copy()
        variants = soundcheck.stage_variants(wet, "mix")
        np.testing.assert_array_equal(wet, original)
        (_, plain), (low, low_audio), (high, high_audio) = variants
        self.assertEqual(low["post_eq"][0]["type"], "high_pass")
        self.assertEqual(high["post_eq"][0]["type"], "high_shelf")
        self.assertLess(measure(low_audio).rms_db, measure(high_audio).rms_db)
        auditions = soundcheck.blind(soundcheck.level_matched(variants), seed=1)
        self.assertEqual(set(auditions), set("ABC"))
        loudness = [measure(audio).lufs for _, audio in auditions.values()]
        self.assertLess(max(loudness) - min(loudness), .05)
        with TemporaryDirectory() as folder:
            target = soundcheck.keep_preview(Path(folder), "Test", "mix", auditions)
            record = json.loads((target / "preview.json").read_text(encoding="utf-8"))
            self.assertEqual(set(record["auditions"]), set("ABC"))
            self.assertEqual(read_wave(target / "audition-A.wav").shape[1], 2)

    def test_a_clipped_or_silent_sound_is_refused(self):
        for value in (0., 1.):
            with self.assertRaisesRegex(ValueError, "unclipped"):
                soundcheck.stage_variants(np.full((RATE, 1), value, dtype=np.float32))
        with self.assertRaises(ValueError):
            soundcheck.stage_variants(self.wet(), "arena")

    def test_the_shelf_cuts_the_top_and_leaves_the_bottom(self):
        from scipy import signal

        b, a = soundcheck.high_shelf(4000, -6.)
        _, response = signal.freqz(b, a, worN=[100, 15000], fs=RATE)
        self.assertAlmostEqual(20 * np.log10(abs(response[0])), 0., delta=.1)
        self.assertAlmostEqual(20 * np.log10(abs(response[1])), -6., delta=.5)

    def test_the_take_check_warns_of_a_hot_input(self):
        report = soundcheck.take_report(phrase(level=.8))
        self.assertTrue(any("headroom" not in check and "full scale" in check for check in report["checks"]))
        quiet = soundcheck.take_report(phrase(level=.05))
        self.assertEqual(quiet["checks"], [])
        self.assertGreater(quiet["headroom_db"], 20)


class OnTheUnitTest(unittest.TestCase):
    def test_the_dry_input_is_recorded_and_played_through_the_sound(self):
        with TemporaryDirectory() as folder:
            unit = host(folder)
            try:
                report = unit.call("lab.tools.soundcheck.check", chain(PLEXI, V30), None, 1.).result(60)
                self.assertIn("wet", report)
                self.assertLess(report["dry"]["sample_peak_dbfs"], 0)
                self.assertIsNotNone(report["wet"]["lufs"])
                found = unit.call("lab.tools.soundcheck.unit_check").result(30)
                self.assertEqual(found["firmware"], "4.1.1")
                self.assertTrue(found["tone_lab"])
                self.assertTrue(found["calibrated"])      # the check measured the round trip first
                wet = unit.call("lab.tools.soundcheck.stage_render", chain(PLEXI, V30), phrase()).result(60)
                self.assertEqual(len(soundcheck.stage_variants(wet, "frfr")), 3)
            finally:
                unit.close()


if __name__ == "__main__":
    unittest.main()
