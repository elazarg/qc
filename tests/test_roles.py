import unittest
from unittest import mock

from qc_mcp import catalog, preset

from tone_lab import styles


def chain(*blocks):
    """Build one row from (model, {parameter: display value}) pairs, every other control at its default."""
    builder = preset.PresetBuilder("Roles")
    for column, (model, chosen) in enumerate(blocks):
        values = {}
        for index, param in enumerate(catalog.lookup(model)["params"]):
            try:
                values[index] = catalog.to_norm(model, index, chosen.get(param["name"], float(param["default"])))
            except (TypeError, ValueError):
                values[index] = 0.
        builder.add_block(0, column, model, params=values)
    return builder.build()


def moves(source, knobs):
    return {(edit["model"], edit["name"]): round(edit["to"] - edit["from"], 2) for edit in styles.knobs(source, 0, knobs)}


class ControlRolesTest(unittest.TestCase):
    def setUp(self):
        # The directions are checked with every amp's controls taken as equally strong.
        even = mock.patch.object(styles.measured, "scale", return_value=1.)
        even.start()
        self.addCleanup(even.stop)

    def test_warmth_turns_backward_controls_the_other_way(self):
        # A RAT Filter and a Vox Tone Cut both darken as they rise.
        edits = moves(chain((4, {}), (1011, {})), {"warmth": .5})
        self.assertEqual(edits, {("Rodent Drive", "FILTER"): 1., ("UK C30 TopBoost", "TREBLE"): -1.,
                                 ("UK C30 TopBoost", "TONE CUT"): 1.})
        self.assertEqual(moves(chain((1011, {})), {"presence": .4}), {("UK C30 TopBoost", "TONE CUT"): -1.})

    def test_tightness_reaches_the_low_end_resonance_and_a_tight_control(self):
        edits = moves(chain((8, {}), (1017, {})), {"tightness": .4})
        self.assertEqual(edits, {("Freeman BOD", "BASS"): -.72, ("Freeman BOD", "TIGHT"): .72,
                                 ("EV101IIIS Red EL34 100W", "BASS"): -.8,
                                 ("EV101IIIS Red EL34 100W", "RESONANCE"): -1.})

    def test_drive_moves_whatever_a_model_calls_its_gain(self):
        edits = moves(chain((5, {}), (1176, {})), {"drive": .5})
        self.assertEqual(set(edits), {("Green 808", "OVERDRIVE"), ("Dumbbell ODS", "VOLUME")})
        self.assertTrue(all(change > 0 for change in edits.values()))
        self.assertEqual(set(moves(chain((1121, {})), {"drive": .5})), {("Brit UBL Lead", "INPUT GAIN")})

    def test_volume_is_the_drive_only_on_amps_without_a_gain_control(self):
        self.assertTrue({1008, 1011, 1095, 1136, 1176} <= styles.AMP_VOLUME_AS_DRIVE)
        # A Herbert lead channel has both, and its Volume is a level.
        self.assertFalse({1137, 1018, 1093} & styles.AMP_VOLUME_AS_DRIVE)

    def test_mids_follow_either_spelling(self):
        self.assertEqual(moves(chain((1180, {})), {"mids": .5}), {("Petrucci Lead", "MIDDLE"): 1.})
        self.assertEqual(moves(chain((1093, {})), {"mids": .5}), {("Brit Plexi 100 Normal", "MID"): 1.})

    def test_a_move_scales_to_the_range_of_the_control(self):
        # The 1981 DRV's Cut runs from 0 to 100 and darkens as it rises.
        self.assertEqual(moves(chain((42, {"CUT": 20.})), {"warmth": .5}), {("81 Creations Drive", "CUT"): 10.})


class MeasuredStrengthTest(unittest.TestCase):
    def test_a_weak_tone_control_is_turned_further(self):
        source = chain((1011, {}))
        with mock.patch.object(styles.measured, "scale", return_value=1.):
            plain = moves(source, {"warmth": .5})["UK C30 TopBoost", "TREBLE"]
        asked = []

        def scale(model_id, name, rising=True):
            asked.append((model_id, name, rising))
            return 2. if name == "TREBLE" else 1.

        with mock.patch.object(styles.measured, "scale", scale):
            scaled = moves(source, {"warmth": .5})
        self.assertEqual(scaled["UK C30 TopBoost", "TREBLE"], 2 * plain)
        self.assertIn((1011, "TREBLE", False), asked)


if __name__ == "__main__":
    unittest.main()
