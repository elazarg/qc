import unittest

from qc_mcp import catalog, preset

from tone_lab import draft_render, models, preset_design, probe, styles

CAB = 12006
STOCK = {"name": "Cab moves", "target": "Test", "explanation": "Test", "chain": [
    {"model_id": 1081, "settings": []},
    {"model_id": CAB, "settings": [{"name": "POSITION", "value": 6.5}, {"name": "HPF", "value": 90}]},
]}


def cab_block(builder, column, *, low_pass=20000., high_pass=20.):
    layout = models.lookup(CAB)["params"]
    values = {}
    for index, param in enumerate(layout):
        if param["name"] == "LPF":
            values[index] = models.to_norm(CAB, index, low_pass)
        elif param["name"] == "HPF":
            values[index] = models.to_norm(CAB, index, high_pass)
        else:
            values[index] = .4
    values[len(layout)] = 0.  # The device stores one value more than the layout lists.
    return builder.add_block(0, column, CAB, params=values)


class CabLayoutTest(unittest.TestCase):
    def test_a_stock_cab_is_read_in_the_layout_the_device_stores(self):
        names = [param["name"] for param in models.lookup(CAB)["params"]]
        self.assertEqual([param["name"] for param in catalog.lookup(CAB)["params"]], ["ir selector", "ir selector"])
        self.assertEqual(names.count("POSITION"), 2)
        self.assertEqual((names.index("HPF"), names.index("LPF")), (16, 17))
        self.assertEqual(models.lookup(CAB)["name"], catalog.lookup(CAB)["name"])
        self.assertAlmostEqual(models.to_display(CAB, 5, .4), 4.)
        self.assertAlmostEqual(models.to_display(CAB, 16, models.to_norm(CAB, 16, 90.)), 90.)
        self.assertEqual(models.lookup(1081), catalog.lookup(1081))

    def test_a_draft_setting_moves_both_microphones(self):
        compiled = dict(draft_render.compile_chain(STOCK, -12.))
        self.assertEqual(compiled[CAB], [(5, 6.5), (13, 6.5), (16, 90)])
        self.assertEqual(preset_design.choices()[CAB]["twins"], {"DISTANCE": [12], "POSITION": [13]})

    def test_the_cab_takes_the_filter_move_and_a_later_equalizer_stays(self):
        equalizer = 4005
        builder = cab_block(preset.PresetBuilder("Cab then EQ"), 0)
        settings = {index: catalog.to_norm(equalizer, index, float(param["default"]))
                    for index, param in enumerate(catalog.lookup(equalizer)["params"])}
        source = builder.add_block(0, 1, equalizer, params=settings).build()
        edits = styles.knobs(source, 0, {"soft_top": .5, "low_cut": 1.})
        self.assertEqual({(edit["column"], edit["name"]) for edit in edits}, {(1, "HPF"), (1, "LPF")})
        moved = {edit["name"]: edit["to"] for edit in edits}
        # From filters parked wide open, half the knob reaches the range engineers use on guitar.
        self.assertAlmostEqual(moved["LPF"], 14000 * 2 ** -.75, places=0)
        self.assertAlmostEqual(moved["HPF"], 45 * 2 ** 1.6, places=1)

    def test_the_stage_whose_filter_is_doing_the_work_takes_the_move(self):
        equalizer = 4005
        builder = cab_block(preset.PresetBuilder("Cab then set EQ"), 0)
        names = [param["name"] for param in catalog.lookup(equalizer)["params"]]
        settings = {index: catalog.to_norm(equalizer, index, float(param["default"]))
                    for index, param in enumerate(catalog.lookup(equalizer)["params"])}
        settings[names.index("LPF")] = catalog.to_norm(equalizer, names.index("LPF"), 6800.)
        source = builder.add_block(0, 1, equalizer, params=settings).build()
        edits = styles.knobs(source, 0, {"soft_top": .5, "low_cut": .5})
        # The equalizer already cuts at 6.8 kHz, so lowering the cab's parked filter would do nothing.
        self.assertEqual({(edit["column"], edit["name"]) for edit in edits}, {(2, "LPF"), (1, "HPF")})
        draft = {"chain": [{"model_id": CAB, "settings": []},
                           {"model_id": equalizer, "settings": [{"name": "LPF", "value": 6800}]}]}
        controls = styles.draft_controls(draft)
        self.assertEqual((controls["soft_top"], controls["low_cut"]),
                         ((("Graphic-9", "Lpf"),), (("412 Brit 60B GB 71 (M)", "Hpf"),)))

    def test_a_filter_already_in_use_moves_from_where_it_is(self):
        source = cab_block(preset.PresetBuilder("Set cab"), 0, low_pass=8000., high_pass=100.).build()
        moved = {edit["name"]: edit["to"] for edit in styles.knobs(source, 0, {"soft_top": -.5, "low_cut": -.5})}
        self.assertAlmostEqual(moved["LPF"], 8000 * 2 ** .75, places=0)
        self.assertAlmostEqual(moved["HPF"], 100 * 2 ** -.8, places=1)

    def test_the_mic_knob_moves_both_microphones_together(self):
        source = cab_block(preset.PresetBuilder("Mics"), 0).build()
        edits = [(edit["index"], edit["name"], round(edit["to"], 2)) for edit in styles.knobs(source, 0, {"mic": .5})]
        self.assertEqual(edits, [(5, "POSITION", 6.5), (13, "POSITION", 6.5)])
        self.assertEqual(styles.draft_controls({"chain": [{"model_id": CAB, "settings": []}]})["mic"],
                         (("412 Brit 60B GB 71 (M)", "Position"),))

    def test_a_cab_of_another_shape_is_left_alone(self):
        source = preset.PresetBuilder("Short cab").add_block(0, 0, CAB, params={0: .5, 1: .5}).build()
        self.assertEqual(styles.knobs(source, 0, {"soft_top": 1.}), [])


class ProbeTest(unittest.TestCase):
    def test_a_gap_ignores_overall_level(self):
        first = {"bands_db": [0., 0., 0., 0.]}
        second = {"bands_db": [6., 6., 6., 10.]}
        self.assertEqual(probe.gap(first, second), [-1., -1., -1., 3.])


if __name__ == "__main__":
    unittest.main()
