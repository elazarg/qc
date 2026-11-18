import unittest

from qc_mcp import catalog, preset

from tone_lab import styles


class ToneDirectionsTest(unittest.TestCase):
    def test_stacked_clean_controls_leave_the_amp_input_open(self):
        model = 1045  # Solo 100 Lead, the amp in the reported failure.
        original_gain = catalog.to_norm(model, 0, 5.4)
        source = preset.PresetBuilder("Lead").add_block(0, 0, model, params={0: original_gain}).build()
        directions = {"drive": -1, "jazz": 1, "sustain": -1, "bloom": 1}
        gain = next(edit for edit in styles.knobs(source, 0, directions) if edit["name"] == "GAIN")
        self.assertAlmostEqual(gain["from"], 5.4, places=4)
        self.assertAlmostEqual(gain["to"], 1.5)
        self.assertAlmostEqual(source.chains[0].models[0].params[0].param_values[0].float_value, original_gain)

    def test_clean_controls_keep_an_already_quiet_amp_and_full_capture_gain_range(self):
        model = 1045
        capture = 14000
        builder = preset.PresetBuilder("Quiet amp and capture")
        builder.add_block(0, 0, model, params={0: catalog.to_norm(model, 0, .7)})
        builder.add_block(0, 1, capture, params={0: catalog.to_norm(capture, 0, 0)})
        edits = styles.knobs(builder.build(), 0, {"drive": -1, "jazz": 1})
        self.assertEqual([(edit["column"], edit["name"], edit["to"]) for edit in edits], [(2, "GAIN", -24.)])

    def test_macros_can_shape_only_the_active_scene(self):
        model = 1084
        settings = {0: [catalog.to_norm(model, 0, 3.), catalog.to_norm(model, 0, 7.)]}
        source = preset.PresetBuilder("Two scenes").add_block(0, 0, model, params=settings).build()
        edits = styles.knobs(source, 1, {"drive": -.5})
        self.assertEqual(len(edits), 1)
        self.assertAlmostEqual(edits[0]["from"], 7., places=4)
        self.assertAlmostEqual(source.chains[0].models[0].params[0].param_values[0].float_value,
                               settings[0][0], places=5)
        self.assertAlmostEqual(source.chains[0].models[0].params[0].param_values[1].float_value,
                               settings[0][1], places=5)

    def test_owned_ir_filters_participate_in_musical_directions(self):
        model = 29001
        metadata = catalog.lookup(model)["params"]
        index = next(i for i, param in enumerate(metadata) if param["name"] == "LOW PASS")
        # A real QC working grid carries every intervening parameter.
        settings = {i: .25 for i in range(index + 1)}
        settings[index] = catalog.to_norm(model, index, 8000.)
        source = preset.PresetBuilder("Cab filter").add_block(0, 0, model, params=settings).build()
        edits = styles.knobs(source, 0, {"soft_top": .5})
        self.assertTrue(any(edit["index"] == index and edit["to"] < edit["from"] for edit in edits))


if __name__ == "__main__":
    unittest.main()
