from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from tone_lab import audio, level_plan, session


def sine(hz=440, level=.2, seconds=1, phase=0):
    frames = np.arange(round(audio.RATE * seconds))
    return (level * np.sin(2 * np.pi * hz * frames / audio.RATE + phase))[:, None]


class LevelPlanTest(unittest.TestCase):
    def test_true_peak_detects_between_sample_maxima_and_checks_whole_file(self):
        samples = sine(12000, .8, phase=np.pi / 4)
        samples[10, 0] = 1
        read = level_plan.measure(samples, passage=(.45, .95))
        self.assertEqual(read["near_rail_samples"], 1)
        self.assertEqual(read["sample_peak_dbfs"], 0)
        self.assertEqual(read["measured_seconds"], .5)
        clean = level_plan.measure(sine(12000, .8, phase=np.pi / 4))
        self.assertGreater(clean["true_peak_dbtp_estimate"] - clean["sample_peak_dbfs"], 2.5)

    def test_role_offsets_use_raw_wavs_and_respect_common_headroom(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            baseline, lead, clipped = (sine(level=amplitude) for amplitude in (.50, .55, 1.1))
            for name, data in (("render-1.wav", baseline), ("render-2.wav", lead), ("render-3.wav", clipped),
                               ("audition-A.wav", baseline * .03), ("audition-B.wav", lead * .03)):
                audio.write_wave(path / name, data)
            session.write_json(path / "session.json", {"source_clip": "same-take", "candidates": [
                {"file": f"render-{i}.wav"} for i in range(1, 4)], "auditions": {
                    "A": {"render": "render-1.wav", "file": "audition-A.wav"},
                    "B": {"render": "render-2.wav", "file": "audition-B.wav"},
                }})
            info = level_plan.comparison_plan(path, reference="A", offsets={"B": 3}, ceiling_dbtp=-7)
            rhythm, boost, rejected = info["renders"]
            self.assertTrue(info["lowered_for_headroom"])
            self.assertEqual(info["source_take"], "same-take")
            self.assertEqual(info["reference"], "render-1.wav")
            self.assertAlmostEqual(rhythm["sample_peak_dbfs"], -6.02, places=1)
            self.assertAlmostEqual(boost["expected_lufs"] - rhythm["expected_lufs"], 3, delta=.11)
            self.assertLessEqual(boost["expected_true_peak_dbtp"], -7)
            self.assertIsNone(rejected["post_chain_trim_db"])
            self.assertIn("headroom", rejected["issue"])

    def test_default_trim_does_not_boost_and_take_preserves_alignment(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            dry = sine(level=.025, seconds=2)
            audio.write_wave(path / "dry.wav", dry)
            audio.write_wave(path / "wet.wav", np.repeat(dry * 10, 2, axis=1))
            session.write_json(path / "clip.json", {"selected_passage_seconds": [.25, .85]})
            takes = level_plan.take_report(path)
            self.assertEqual(takes["dry"]["channels"], 1)
            self.assertEqual(takes["wet"]["channels"], 2)
            self.assertAlmostEqual(takes["wet"]["measured_seconds"], .6)
            self.assertAlmostEqual(takes["wet"]["sample_peak_dbfs"] - takes["dry"]["sample_peak_dbfs"], 20, delta=.01)
            session.write_json(path / "session.json", {"candidates": [{"file": "wet.wav"}, {"file": "dry.wav"}]})
            report = level_plan.comparison_plan(path)
            self.assertTrue(all(row["post_chain_trim_db"] <= 0 for row in report["renders"]))


if __name__ == "__main__":
    unittest.main()
