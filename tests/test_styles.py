import unittest

from qc_mcp import catalog, preset

from tone_lab import styles


class ToneDirectionsTest(unittest.TestCase):
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
