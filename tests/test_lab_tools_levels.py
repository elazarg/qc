"""Levels: the listening level, measuring, one common loudness, and matching sounds on the simulated unit.

Ported from test_level_plan (the planning). A and B are matched by the desk itself; the tool measures with
`hearing.measure`, the one measurement."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from lab.hearing.measure import measure
from lab.sound import catalog
from lab.tools import levels
from lab.qc.unit import CEILING_DB

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import PLEXI, V30, chain, host, phrase  # noqa: E402


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


class PlanTest(unittest.TestCase):
    def readings(self, **amplitudes):
        return {name: measure(sine(level=amplitude)).to_record() for name, amplitude in amplitudes.items()}

    def test_the_plan_reads_the_one_measurement_of_the_app(self):
        # Regression: the tool had a measurement of its own, with other names for the same quantities.
        self.assertFalse(hasattr(levels, "measure") and levels.measure is not measure)
        found = levels.plan(self.readings(A=.5, B=.25))
        rows = {row["name"]: row for row in found["rows"]}
        self.assertAlmostEqual(rows["A"]["trim_db"], -6., delta=.15)
        self.assertEqual(rows["B"]["trim_db"], 0.)
        self.assertIn("true_peak_db", rows["A"])

    def test_offsets_respect_the_common_headroom_and_a_clipped_sound_is_left_out(self):
        found = levels.plan(self.readings(A=.50, B=.55, C=1.1), reference="A", offsets={"B": 3}, ceiling_dbtp=-7)
        rhythm, boost, rejected = found["rows"]
        self.assertTrue(found["lowered_for_headroom"])
        self.assertEqual(found["reference"], "A")
        self.assertAlmostEqual(rhythm["peak_db"], -6.02, places=1)
        self.assertAlmostEqual(boost["expected_lufs"] - rhythm["expected_lufs"], 3, delta=.11)
        self.assertLessEqual(boost["expected_true_peak_db"], -7)
        self.assertIsNone(rejected["trim_db"])
        self.assertIn("full scale", rejected["issue"])

    def test_without_a_reference_the_plan_only_cuts(self):
        quiet, loud = sine(level=.025, seconds=2), np.repeat(sine(level=.25, seconds=2), 2, axis=1)
        found = levels.plan({"dry": measure(quiet).to_record(), "wet": measure(loud).to_record()})
        self.assertTrue(all(row["trim_db"] <= 0 for row in found["rows"]))
        with self.assertRaisesRegex(ValueError, "two measurable"):
            levels.plan({"dry": measure(quiet).to_record()})

    def test_a_sound_too_quiet_to_read_is_left_out_with_the_reason(self):
        found = levels.plan({**self.readings(A=.5, B=.25), "silent": measure(np.zeros((48000, 1))).to_record()})
        silent = next(row for row in found["rows"] if row["name"] == "silent")
        self.assertIsNone(silent["trim_db"])
        self.assertIn("quiet", silent["issue"])

    def test_the_code_carried_over_for_the_old_live_engine_is_gone(self):
        for name in ("loudness_plan", "matched_pair", "valid", "lane_targets", "lanes"):
            self.assertFalse(hasattr(levels, name), name)


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
                # Put back: each side at what the desk asked for it, no offset.
                self.assertEqual(unit.call("lab.tools.levels.clear").result(10), {"a": 3., "b": -12.})
            finally:
                unit.close()


if __name__ == "__main__":
    unittest.main()
