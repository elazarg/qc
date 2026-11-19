"""What the amp sweep says each control does. These hold for a partial sweep and for the whole one."""

from pathlib import Path
from statistics import median
import sys
import unittest
from unittest.mock import patch

from tone_lab import measured, models, probe, styles

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
import analyse_amps  # noqa: E402

RECTIFIER, TOP_BOOST = 1018, 1011


def amps():
    return measured.table()["amps"]


def condition(label, bands, *, rms=-40., crest=10., centroid=1000):
    return {"label": label, "rms_db": rms, "crest_db": crest, "centroid_hz": centroid, "bands_db": list(bands)}


class AnalysisTest(unittest.TestCase):
    def test_a_knob_is_given_per_point_with_the_change_of_the_whole_spectrum_removed(self):
        centres = list(probe.CENTRES)
        flat = [-60.] * 22 + [-120.] * 3  # Nothing sounds in the top three bands.
        raised = [level + 3. if centre < 200 else level for level, centre in zip(flat, centres)]
        raised[-3:] = [-90.] * 3  # A large change where nothing sounds must not count.
        record = analyse_amps.summarise({"id": RECTIFIER, "name": "Test", "bands_hz": centres, "conditions": [
            condition("default", flat),
            condition("BASS|high|8|5", raised, rms=-39., crest=10.6, centroid=2000),
            condition("BASS|low|2|5", flat, rms=-120.),
            condition("BRIGHT|switch|1|0", raised, rms=-38.)]})
        bass = record["knobs"]["BASS"]
        self.assertEqual((bass["role"], bass["sign"]), ("bass", 1))
        self.assertEqual(bass["low"], {"moved": -3., "silent": True})
        high = bass["high"]
        # Seven of the 22 sounding bands rose 3 dB, so the spectrum as a whole rose 21/22 dB.
        self.assertEqual(high["bands"], [.682, -.318, -.318, -.318, -.318])
        self.assertEqual((high["moved"], high["level"], high["centroid"], high["crest"]), (3., .333, .3333, .2))
        self.assertEqual(measured.side_strength(bass, "high"), .682)
        self.assertIsNone(measured.side_strength(bass, "low"))
        switch = record["switches"]["BRIGHT"]["1"]
        self.assertEqual((switch["from"], switch["level"], switch["bands"][0]), ("0", 2., 2.045))

    def test_an_amp_that_is_silent_at_its_defaults_is_left_out(self):
        quiet = {"id": RECTIFIER, "name": "Test", "bands_hz": list(probe.CENTRES),
                 "conditions": [condition("default", [-150.] * 25, rms=-130.)]}
        self.assertIsNone(analyse_amps.summarise(quiet))

    def test_a_cut_control_carries_the_sign_of_its_role(self):
        self.assertEqual(analyse_amps.role_of("TONE CUT"), ("presence", -1))
        self.assertIsNone(analyse_amps.role_of("GAIN"))


class TableTest(unittest.TestCase):
    def setUp(self):
        if not amps():
            self.skipTest("No amp has been measured yet.")

    def test_every_entry_names_a_real_control_of_a_real_amp(self):
        table = measured.table()
        self.assertEqual(table["bands"], list(measured.BANDS))
        roles = {name: (role, sign) for (category, role), controls in styles.ROLES.items()
                 if category == styles.AMP for name, sign in controls.items()}
        for model_id, amp in table["amps"].items():
            info = models.lookup(int(model_id))
            self.assertEqual((info["category"], info["name"]), ("Guitar Amplifier", amp["name"]))
            kinds = {param["name"]: param["type"] for param in info["params"]}
            for name, knob in amp["knobs"].items():
                self.assertIn(kinds[name], ("float", "fader"))
                self.assertEqual((knob.get("role"), knob.get("sign")), roles.get(name, (None, None)), name)
                self.assertLessEqual(set(knob) - {"role", "sign", "phrase"}, {"low", "high"})
                for side, found in ((side, knob[side]) for side in ("low", "high") if side in knob):
                    self.assertEqual(found["moved"] > 0, side == "high")
                    if not found.get("silent"):
                        self.assertEqual(len(found["bands"]), len(measured.BANDS))
            for name, positions in amp["switches"].items():
                self.assertIn(name, kinds)
                self.assertTrue(all(found["from"] != position for position, found in positions.items()))

    def test_tone_controls_mostly_move_their_own_band_the_way_their_role_says(self):
        for name in ("BASS", "TREBLE"):
            found = [value for model_id in amps() for rising in (False, True)
                     if (value := measured.strength(model_id, name, rising)) is not None]
            self.assertGreater(median(found), 0, name)

    def test_turning_a_gain_down_from_its_default_lowers_the_level(self):
        slopes = [knob["low"]["level"] for amp in amps().values() for name, knob in amp["knobs"].items()
                  if name in styles.AMP_INPUT_GAIN_NAMES and "level" in knob.get("low", {})]
        if slopes:
            self.assertGreater(median(slopes), 0)

    def test_a_tone_cut_darkens_as_it_rises(self):
        if str(TOP_BOOST) not in amps():
            self.skipTest("The Top Boost has not been measured yet.")
        self.assertGreater(measured.strength(TOP_BOOST, "TONE CUT"), 0)  # Positive follows the role, which is a cut.
        self.assertLess(measured.effect(TOP_BOOST, "TONE CUT")["high"]["bands"][3], 0)

    def test_a_scale_stays_within_its_limits(self):
        for model_id, amp in amps().items():
            for name in amp["knobs"]:
                for rising in (False, True):
                    self.assertTrue(measured.SCALE[0] <= measured.scale(model_id, name, rising) <= measured.SCALE[1])


class ScaleTest(unittest.TestCase):
    def test_a_move_is_scaled_towards_what_the_typical_amp_does(self):
        def knob(low, high, sign=1):
            side = lambda value, moved: {"moved": moved, "level": 0., "bands": [value, 0., 0., 0., 0.]}
            return {"role": "bass", "sign": sign, "low": side(low, -3.), "high": side(high, 3.)}

        table = {"typical": {"bass": .4}, "amps": {"1": {"knobs": {
            "BASS": knob(.8, .2), "LOWS": knob(.01, -.3), "GAIN": {"high": {"moved": 3., "level": 1., "bands": [1.] * 5}}},
            "switches": {}}}}
        with patch.object(measured, "table", return_value=table):
            self.assertEqual(measured.scale(1, "BASS", rising=False), .5)  # Twice as strong below its default.
            self.assertEqual(measured.scale(1, "BASS", rising=True), 2.)  # Half as strong above it.
            self.assertEqual(measured.scale(1, "LOWS", rising=False), 1.)  # Does nothing: left alone.
            self.assertEqual(measured.scale(1, "LOWS", rising=True), 1.)  # Runs backwards: left alone.
            self.assertEqual(measured.scale(1, "GAIN"), 1.)  # No role.
            self.assertEqual(measured.scale(2, "BASS"), 1.)  # Not measured.
            self.assertIsNone(measured.strength(1, "GAIN"))
            self.assertEqual(measured.effect(1, "BASS")["role"], "bass")

    def test_mids_are_not_evened_out(self):
        side = {"moved": 3., "level": .5, "bands": [0., .05, .07, 0., 0.]}
        table = {"typical": {"mid": .14}, "amps": {"1": {"knobs": {"MID": {"role": "mid", "sign": 1, "high": side}},
                                                         "switches": {}}}}
        with patch.object(measured, "table", return_value=table):
            self.assertEqual(measured.scale(1, "MID"), 1.)


class InertTest(unittest.TestCase):
    def test_an_amp_none_of_whose_controls_changes_anything_is_inert(self):
        flat = [-60.] * 25
        rows = [condition("default", flat)] + [condition(f"GAIN|high|{value}|5", flat) for value in (6, 7, 8, 9)]
        self.assertTrue(analyse_amps.inert({"conditions": rows}))
        rows[2] = condition("GAIN|high|7|5", flat, rms=-38.)
        self.assertFalse(analyse_amps.inert({"conditions": rows}))
        self.assertFalse(analyse_amps.inert({"conditions": rows[:2]}))  # Too few conditions to say.

    def test_nothing_from_a_plugin_whose_amp_does_nothing_is_offered(self):
        from tone_lab import preset_design

        with patch.object(measured, "inert", return_value=frozenset({1180})):  # Petrucci Lead
            locked = preset_design.locked_models()
        self.assertTrue({1177, 1178, 1179, 1180} <= locked)
        self.assertFalse({RECTIFIER, TOP_BOOST} & locked)


class SwitchAndPolarityTest(unittest.TestCase):
    def test_a_switch_that_changed_nothing_in_any_position_is_idle(self):
        still = {"from": "0", "level": 0., "bands": [0., 0., None, 0., 0.]}
        moved = {"from": "0", "level": 0., "bands": [0., 0., 0., 1.2, 0.]}
        table = {"typical": {}, "amps": {"1": {"knobs": {}, "switches": {
            "VOICE": {"1": still, "2": still}, "BRIGHT": {"1": moved}, "MODE": {"1": still, "2": moved}}}}}
        with patch.object(measured, "table", return_value=table):
            self.assertTrue(measured.idle_switch(1, "VOICE"))
            self.assertFalse(measured.idle_switch(1, "BRIGHT"))
            self.assertFalse(measured.idle_switch(1, "MODE"))
            self.assertFalse(measured.idle_switch(1, "FAT"))  # Not measured.
            self.assertFalse(measured.idle_switch(2, "VOICE"))

    def test_paths_oppose_when_an_odd_number_of_inverting_models_separates_them(self):
        amps, cabs = (frozenset({1, 2, 3, 4}), frozenset({1, 2})), (frozenset({20, 21}), frozenset({21}))
        with patch.object(measured, "polarity", return_value=(amps, cabs)):
            self.assertTrue(measured.opposed([1, 20], [3, 20]))
            self.assertFalse(measured.opposed([1, 20], [2, 20]))
            self.assertFalse(measured.opposed([3], [4]))
            self.assertFalse(measured.opposed([1, 21], [3, 20]))  # The amp and the cab each turn the path over.
            # An amp beside a path without one was not measured, and neither was a capture.
            self.assertFalse(measured.opposed([1], []))
            self.assertFalse(measured.opposed([1, 99], [1, 20]))
            self.assertTrue(measured.opposed([1, 99], [3, 98]))

    def test_the_table_from_the_unit_sets_a_rectifier_against_a_plexi(self):
        self.assertTrue(measured.opposed([1018, 12001], [1081, 12006]))
        self.assertFalse(measured.opposed([1081, 12001], [1011, 12006]))


if __name__ == "__main__":
    unittest.main()
