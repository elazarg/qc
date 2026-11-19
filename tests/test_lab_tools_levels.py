"""Levels: the listening level, measuring, one common loudness, and matching sounds on the simulated unit.

Ported from test_level_plan and test_live_levels (the planning and the row trims; the old engine's
phrase-by-phrase observing has no counterpart on the new core)."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from qc_mcp import leveling

from lab import catalog
from lab.chain import Chain, Row
from lab.tools import levels
from lab.unit import CEILING_DB

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lab_tools_support import PLEXI, V30, chain, host, phrase  # noqa: E402


def sine(hz=440, level=.2, seconds=1, phase=0):
    frames = np.arange(round(48000 * seconds))
    return (level * np.sin(2 * np.pi * hz * frames / 48000 + phase))[:, None]


class ListeningLevelTest(unittest.TestCase):
    def test_settings_stay_within_the_floor_and_the_ceiling(self):
        settings = levels.saved_settings({"loudness_db": 40, "master_db": 3})
        self.assertEqual(settings["loudness_db"], CEILING_DB)
        self.assertEqual(settings["master_db"], 0)
        self.assertEqual(levels.saved_settings({"loudness_db": float("nan")})["loudness_db"], 0)
        louder = levels.listening_command(levels.saved_settings({}), "louder")
        self.assertEqual(louder["loudness_db"], 3)
        self.assertEqual(levels.listening_command(louder, "normal")["loudness_db"], 0)
        self.assertTrue(levels.listening_command(louder, "mute")["muted"])
        self.assertEqual(levels.reading(louder), "+3 dB from matched")

    def test_a_side_never_plays_above_the_ceiling(self):
        settings = levels.saved_settings({"loudness_db": 6})
        self.assertEqual(levels.side_level(3., settings), CEILING_DB)
        self.assertEqual(levels.side_level(-20., settings), -14.)
        self.assertEqual(levels.side_level(0., {**settings, "muted": True}), levels.FLOOR_DB)


class MeasureTest(unittest.TestCase):
    def test_true_peak_detects_between_sample_maxima_and_checks_the_whole_of_it(self):
        samples = sine(12000, .8, phase=np.pi / 4)
        samples[10, 0] = 1
        found = levels.measure(samples, passage=(.45, .95))
        self.assertEqual(found["near_rail_samples"], 1)
        self.assertEqual(found["sample_peak_dbfs"], 0)
        self.assertEqual(found["measured_seconds"], .5)
        clean = levels.measure(sine(12000, .8, phase=np.pi / 4))
        self.assertGreater(clean["true_peak_dbtp_estimate"] - clean["sample_peak_dbfs"], 2.5)

    def test_offsets_respect_the_common_headroom_and_a_clipped_sound_is_left_out(self):
        readings = {name: levels.measure(sine(level=amplitude))
                    for name, amplitude in (("A", .50), ("B", .55), ("C", 1.1))}
        found = levels.plan(readings, reference="A", offsets={"B": 3}, ceiling_dbtp=-7)
        rhythm, boost, rejected = found["rows"]
        self.assertTrue(found["lowered_for_headroom"])
        self.assertEqual(found["reference"], "A")
        self.assertAlmostEqual(rhythm["sample_peak_dbfs"], -6.02, places=1)
        self.assertAlmostEqual(boost["expected_lufs"] - rhythm["expected_lufs"], 3, delta=.11)
        self.assertLessEqual(boost["expected_true_peak_dbtp"], -7)
        self.assertIsNone(rejected["trim_db"])
        self.assertIn("full scale", rejected["issue"])

    def test_without_a_reference_the_plan_only_cuts(self):
        quiet, loud = sine(level=.025, seconds=2), np.repeat(sine(level=.25, seconds=2), 2, axis=1)
        found = levels.plan({"dry": levels.measure(quiet), "wet": levels.measure(loud)})
        self.assertTrue(all(row["trim_db"] <= 0 for row in found["rows"]))
        with self.assertRaisesRegex(ValueError, "two measurable"):
            levels.plan({"dry": levels.measure(quiet)})


class PairTest(unittest.TestCase):
    def test_a_lift_is_limited_by_true_peak_and_by_each_sound_s_headroom(self):
        a = {"lufs": -18., "peak_dbfs": -12., "true_peak_dbtp_estimate": -1.}
        b = {"lufs": -22., "peak_dbfs": -10., "max_gain_db": 1.}
        self.assertEqual(levels.loudness_plan([a, b]), {"reference_lufs": -23., "requested_lufs": -23.,
                                                        "target_lufs": -23., "trims_db": [-5., -1.]})
        self.assertEqual(levels.loudness_plan([a, b], offset_db=6., max_boost_db=6.),
                         {"reference_lufs": -23., "requested_lufs": -17., "target_lufs": -21., "trims_db": [-3., 1.]})
        self.assertEqual(levels.loudness_plan([a, b], offset_db=6.)["trims_db"], [-4., 0.])
        self.assertEqual(levels.loudness_plan([a], offset_db=6., max_boost_db=6.)["target_lufs"], -20.)

    def test_matching_needs_two_measurable_sounds_and_keeps_headroom(self):
        a = {"clipped_samples": 0, "lufs": -17., "peak_dbfs": -2.}
        b = {"clipped_samples": 0, "lufs": -24., "peak_dbfs": -8.}
        self.assertEqual(levels.matched_pair({"a": a}), {})
        trims = levels.matched_pair({"a": a, "b": b})
        self.assertAlmostEqual(trims["a"], -7.)
        self.assertAlmostEqual(trims["b"], 0.)
        b["clipped_samples"] = 1
        self.assertEqual(levels.matched_pair({"a": a, "b": b}), {})

    def test_row_trims_keep_parallel_outputs_blended_and_existing_mutes(self):
        norm = leveling.norm_of
        rows = (Row(outlet=16, output={catalog.LANE_VOLUME: norm(0.)}),
                Row(outlet=19, output={catalog.LANE_VOLUME: (norm(0.), norm(2.)) + (norm(0.),) * 6}),
                Row(outlet=19, output={catalog.LANE_VOLUME: norm(-4.)}),
                Row(outlet=19, output={catalog.LANE_VOLUME: norm(-2.), catalog.LANE_MUTE: 1.}))
        lanes = levels.lanes(Chain(rows), scene=1)
        self.assertEqual(set(lanes), {1, 2, 3})
        applied, targets = levels.lane_targets(lanes, -6., False)
        self.assertEqual(applied, -6.)
        self.assertEqual({row: round(level, 2) for row, (level, _) in targets.items()}, {1: -4., 2: -10., 3: -8.})
        self.assertEqual([targets[row][1] for row in (1, 2, 3)], [0., 0., 1.])
        _, muted = levels.lane_targets(lanes, -6., True)
        self.assertTrue(all(flag == 1. for _, flag in muted.values()))
        lifted, _ = levels.lane_targets(lanes, 12., False, max_boost_db=12.)
        self.assertEqual(lifted, CEILING_DB - 2.)   # the loudest row reaches the ceiling and stops there


class TrimTest(unittest.TestCase):
    def test_a_trim_goes_on_a_gain_block_and_stops_at_the_ceiling(self):
        trimmed, applied = levels.with_trim(chain(PLEXI, V30), -4.)
        self.assertEqual(applied, -4.)
        block = trimmed.block(0, 2)
        self.assertEqual(block.model, catalog.GAIN_BLOCK)
        self.assertAlmostEqual(block.display(0), -4., places=3)
        again, applied = levels.with_trim(trimmed, 12.)
        self.assertEqual(len(again.rows[0].blocks), 3)      # the same block took it
        self.assertAlmostEqual(again.block(0, 2).display(0), CEILING_DB, places=3)
        self.assertAlmostEqual(applied, 10., places=2)


class OnTheUnitTest(unittest.TestCase):
    def test_sounds_measured_on_the_unit_come_out_matched_once_trimmed(self):
        with TemporaryDirectory() as folder:
            unit = host(folder)
            try:
                sounds = {"Rhythm": chain(gain_db=0.), "Lead": chain(gain_db=-9.), "Crunch": chain(PLEXI, V30)}
                dry = phrase()
                readings = unit.call("lab.tools.levels.measure_sounds", sounds, dry).result(60)
                self.assertAlmostEqual(readings["Rhythm"]["lufs"] - readings["Lead"]["lufs"], 9., delta=.3)
                planned = levels.plan(readings)
                trims = {row["name"]: row["trim_db"] for row in planned["rows"]}
                self.assertTrue(all(trim <= 0 for trim in trims.values()))
                leveled = {name: levels.with_trim(sound, trims[name])[0] for name, sound in sounds.items()}
                after = unit.call("lab.tools.levels.measure_sounds", leveled, dry).result(60)
                loudness = [found["lufs"] for found in after.values()]
                self.assertLess(max(loudness) - min(loudness), .3)
            finally:
                unit.close()

    def test_the_listening_level_is_added_to_what_the_desk_asks_and_capped(self):
        with TemporaryDirectory() as folder:
            unit = host(folder)
            try:
                unit.call("show", chain(PLEXI, V30), chain(gain_db=0.)).result(30)
                played = unit.call("lab.tools.levels.install", {"loudness_db": -6}).result(10)
                self.assertEqual(played, {"a": -18., "b": -18.})
                unit.call("level", "a", 3.).result(10)          # the desk asks for a lift
                played = unit.call("lab.tools.levels.install", {"loudness_db": 6}).result(10)
                self.assertEqual(played["a"], CEILING_DB)
                self.assertEqual(played["b"], -6.)
            finally:
                unit.close()


if __name__ == "__main__":
    unittest.main()
