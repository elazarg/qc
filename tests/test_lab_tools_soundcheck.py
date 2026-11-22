"""Setup and sound check: the stage EQ check, the take check, and asking the simulated unit.

Ported from test_stage_check."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from lab.hearing.audio import RATE, read_wave
from lab.hearing.measure import measure
from lab.tools import soundcheck

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import PLEXI, V30, chain, host, phrase  # noqa: E402


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
        with TemporaryDirectory() as folder:
            # The stage versions are a blind comparison like any other: the workbench's, kept with the others.
            from lab.tools import workbench

            target, record = soundcheck.stage_comparison(Path(folder), wet, "mix", seed=1)
            self.assertEqual(target.parent, Path(folder) / "comparisons")
            self.assertEqual(json.loads((target / "comparison.json").read_text(encoding="utf-8")), record)
            self.assertEqual((record["kind"], record["about"]["context"]), ("Stage EQ", "mix"))
            self.assertEqual(set(record["auditions"]), set("ABC"))
            self.assertEqual({row["name"] for row in record["auditions"].values()},
                             {"As it is", "Low-end cleanup", "Gentler top"})
            cut = next(row for row in record["auditions"].values() if row["name"] == "Low-end cleanup")
            self.assertEqual(cut["post_eq"][0]["type"], "high_pass")
            auditions = workbench.auditions(target, record)
            loudness = [measure(audio).lufs for audio in auditions.values()]
            self.assertLess(max(loudness) - min(loudness), .05)
            self.assertEqual(read_wave(target / "audition-A.wav").shape[1], 2)
            self.assertEqual([path for path, _ in workbench.earlier(folder)], [target])
            for name in ("level_matched", "blind", "keep_preview"):     # the sound check's own copy is gone
                self.assertFalse(hasattr(soundcheck, name), name)

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
        self.assertTrue(any("reducing the Quad Cortex input gain" in check and "headroom" in check
                            for check in report["checks"]))
        quiet = soundcheck.take_report(phrase(level=.05))
        self.assertEqual(quiet["checks"], [])
        self.assertGreater(quiet["headroom_db"], 20)

    def test_the_take_is_measured_the_one_way_the_app_measures(self):
        dry = phrase(level=.2)
        wide = np.column_stack((dry[:, 0], -dry[:, 0]))             # out of phase: nothing left in mono
        report = soundcheck.take_report(dry, wide)
        self.assertEqual(report["dry"], measure(dry).to_record())
        self.assertLess(report["wet"]["mono_fold_down_db"], -60)
        self.assertIsNone(soundcheck.mono_fold_down_db(dry))
        self.assertAlmostEqual(soundcheck.mono_fold_down_db(np.column_stack((dry[:, 0], dry[:, 0]))), 0., places=3)


class IdleCheckTest(unittest.TestCase):
    def test_the_gate_is_kept_only_when_it_lowers_idle_noise_and_leaves_soft_playing(self):
        idle = phrase(seconds=2.5, level=.002)
        dry = phrase(seconds=2., level=.2)
        tone = chain(PLEXI, V30)
        input_end = len(idle) + 2 * RATE

        def rendered(engine, chain, probe, **options):
            wet = np.pad(probe * 4., ((0, RATE), (0, 0)))
            if chain.block(0, 0).model == soundcheck.UTILITY_GATE:
                wet[:input_end] *= .1
                if cut_notes:
                    wet[input_end:] *= .1
            return wet

        with patch.object(soundcheck, "render", rendered):
            cut_notes = False
            found = soundcheck.idle_check(None, tone, {}, idle, dry, 0)
            self.assertEqual(found["reduction_db"], 20.)
            self.assertEqual(found["chain"].block(0, 1), tone.block(0, 0))
            cut_notes = True
            self.assertIsNone(soundcheck.idle_check(None, tone, {}, idle, dry, 0)["chain"])

    def test_a_guitar_gate_is_not_offered_for_noise_present_with_zero_input(self):
        idle = phrase(seconds=3., level=.001)
        with patch.object(soundcheck, "render", side_effect=lambda engine, chain, dry, **options: np.full_like(dry, .01)):
            found = soundcheck.idle_check(None, chain(PLEXI, V30), {}, idle, None, 0)
        self.assertIsNone(found["chain"])
        self.assertIn("silent input", found["note"])


class OnTheUnitTest(unittest.TestCase):
    def test_the_dry_input_is_recorded_and_played_through_the_sound(self):
        with TemporaryDirectory() as folder:
            unit = host(folder)
            try:
                report = unit.call("lab.tools.soundcheck.check", chain(PLEXI, V30), None, 1.).result(60)
                self.assertIn("wet", report)
                self.assertLess(report["dry"]["peak_db"], 0)
                self.assertIsNotNone(report["wet"]["lufs"])
                found = unit.call("lab.tools.probe.unit_state").result(30)
                self.assertEqual(found["firmware"], "4.1.1")
                self.assertTrue(found["tone_lab"])
                self.assertTrue(found["calibrated"])      # the check measured the round trip first
                wet = unit.call("lab.tools.soundcheck.stage_render", chain(PLEXI, V30), phrase()).result(60)
                self.assertEqual(len(soundcheck.stage_variants(wet, "frfr")), 3)
            finally:
                unit.close()


if __name__ == "__main__":
    unittest.main()
