"""Drafts that are more than one chain: several scenes, a second path, and what the unit can carry."""

from threading import Lock
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from qc_mcp import preset

from tone_lab import budget, draft_render, iterate, models, monitor, preset_design, styles

GATE, GOVERNOR, RECTIFIER, PLEXI, V30, GREENBACK, EQ, DELAY, ROOM = 16003, 11, 1018, 1081, 12001, 12006, 4005, 6011, 8000


def block(model_id, **settings):
    return {"model_id": model_id, "settings": [{"name": name.upper().replace("_", " "), "value": value}
                                                for name, value in settings.items()]}


def recipe(chain, **extra):
    return {"name": "Two voices", "target": "Test", "explanation": "Test", "chain": chain, "sources": [], **extra}


def dual():
    rhythm = block(RECTIFIER, gain=4.)
    rhythm["scene_settings"] = [{"scene": 1, "name": "GAIN", "value": 6.5}]
    delay = block(DELAY, mix=18.)
    delay["off_in_scenes"] = [0]
    return recipe([block(GOVERNOR), rhythm, block(V30), block(EQ), delay],
                  parallel=[block(PLEXI, gain=6.), block(GREENBACK, position=6.)],
                  blend={"level": -3., "spread": 60.}, scenes=["Rhythm", "Lead"])


class FakeUnit:
    """A grid that answers the way the Quad Cortex was seen to: sparse writes, one value per scene."""

    firmware = "4.1.1"

    def __init__(self, refuse=()):
        self.grid = type(preset.PresetBuilder("x").build())()
        self.grid.name = draft_render.WORK_NAME
        for _ in range(4):
            chain = self.grid.chains.add()
            for _ in range(8):
                chain.models.add()
            point = chain.split_control_points.add()
            point.split = point.mix = -1
            bypass = self.grid.bypass.add()
            for _ in range(8):
                cell = bypass.colBypass.add()
                for _ in range(8):
                    cell.sceneBypass.add()
        mixer = self.grid.chains[0].mixer.add()
        mixer.hash = draft_render.MIXER
        for _ in models.lookup(draft_render.MIXER)["params"]:
            mixer.params.add().param_values.add().float_value = .5
        self.grid.scene_labels.extend([""] * 8)
        self.scene, self.refuse = 0, set(refuse)
        self._pending, self._io_lock = [], Lock()

    def get_current_preset(self, **_):
        copy = type(self.grid)()
        copy.CopyFrom(self.grid)
        return copy

    def get_setlist_position(self):
        return draft_render.WORK_SLOT

    def list_directory(self):
        return {}

    def read_state(self, name, **_):
        return SimpleNamespace(selected_scene=self.scene) if name == "Scene" else None

    def set_scene(self, scene):
        self.scene = scene

    def set_scene_label(self, scene, name):
        self.grid.scene_labels[scene] = name

    def set_routing(self, row, in_portid=None, out_portid=None):
        self.grid.chains[row].in_portid, self.grid.chains[row].out_portid = in_portid, out_portid

    def delete_block(self, row, column):
        self.grid.chains[row].models[column].Clear()

    def add_block(self, model_id, row=0, column=0):
        if model_id in self.refuse:
            return
        cell = self.grid.chains[row].models[column]
        cell.Clear()
        cell.hash = model_id
        for index, param in enumerate([*models.lookup(model_id)["params"], {}]):  # The unit stores one more.
            try:
                start = models.to_norm(model_id, index, float(param["default"]))
            except (KeyError, TypeError, ValueError):
                start = 0.
            values = cell.params.add().param_values
            for _ in range(8):
                values.add().float_value = start

    def set_param(self, row, column, index, value):
        if not self.grid.chains[row].models[column].hash:
            return  # A write to an empty cell is ignored, as on the unit.
        parameter = self.grid.chains[row].models[column].params[index]
        for entry in [parameter.param_values[self.scene]] if parameter.scene_mode else parameter.param_values:
            entry.float_value = value

    def assign_param_to_scenes(self, row, column, index):
        cell = self.grid.chains[row].models[column]
        if index == len(cell.params):
            self.grid.bypass[row].colBypass[column].sceneMode = True
        else:
            cell.params[index].scene_mode = True

    def set_block_bypass(self, row, column, bypassed=True, scenes=None):
        cell = self.grid.bypass[row].colBypass[column]
        for entry in [cell.sceneBypass[self.scene]] if cell.sceneMode else cell.sceneBypass:
            entry.bypass = bypassed

    def set_split_points(self, row, split=-1, mix=-1):
        point = self.grid.chains[row].split_control_points[0]
        point.split, point.mix = split, mix

    def set_lane_param(self, row, field, index, value, column=0):
        getattr(self.grid.chains[row], field)[0].params[index].param_values[0].float_value = value


def build(draft, unit):
    chain = draft_render.compile_chain(draft, -12.)
    with patch("time.sleep"):
        return chain, draft_render.build_grid(unit, unit.get_current_preset(), chain, draft)


class BudgetTest(unittest.TestCase):
    def test_the_model_agrees_with_every_case_the_unit_accepted_or_refused(self):
        jm, comp, gain, loader, capture = 1182, 5005, 16005, 29001, 14001
        seen = [
            ([RECTIFIER] * 2, True), ([RECTIFIER] * 3, False),  # The third has no processor with room for its code.
            ([jm] * 2, True), ([jm] * 3, False),
            ([ROOM] * 3, True), ([ROOM] * 4, False),  # Delay memory is one pool.
            ([loader] * 4, True), ([loader] * 5, False),
            ([DELAY] * 13, True), ([DELAY] * 14, False),
            ([capture] * 3, True), ([capture] * 4, False),
            ([GOVERNOR] * 9 + [comp] * 4 + [gain], True), ([GOVERNOR] * 10, False),
            ([RECTIFIER] * 2 + [GOVERNOR] * 4 + [comp] * 5 + [gain] * 2, True),
            ([RECTIFIER] * 2 + [GOVERNOR] * 5, False),
            ([jm] * 2 + [ROOM] * 3, True),
        ]
        for blocks, fits in seen:
            self.assertEqual(budget.weigh(blocks).fits, fits, blocks)

    def test_a_stock_cab_costs_what_the_default_cab_block_costs(self):
        self.assertEqual(budget.cost(GREENBACK), budget.cost(12000))
        self.assertGreater(budget.cost(RECTIFIER)["cpu"], 40)
        self.assertEqual(budget.weigh([RECTIFIER, V30]).refused, None)

    def test_a_draft_the_unit_cannot_carry_is_rejected_by_name(self):
        with patch.object(budget, "CPU", (40., 40.)):
            with self.assertRaisesRegex(ValueError, "processing power.*CA Duo Ch3 Modern"):
                preset_design.validate(recipe([block(RECTIFIER), block(V30)]))

    def test_a_neutral_stage_is_left_out_when_there_is_no_room_for_it(self):
        plain = recipe([block(RECTIFIER), block(V30)])
        roomy = {preset_design.choices()[item["model_id"]]["role"] for item in preset_design._with_shaping(
            preset_design.validate(plain))["chain"]}
        with patch.object(budget, "CPU", (43., 43.)):
            tight = {preset_design.choices()[item["model_id"]]["role"] for item in preset_design._with_shaping(
                preset_design.validate(plain))["chain"]}
        self.assertIn("Modulation", roomy)
        self.assertEqual(roomy - tight, {"Modulation"})

    def test_a_card_tells_the_planner_what_a_block_costs(self):
        self.assertEqual(preset_design._card(RECTIFIER)["cpu"], 43)
        self.assertEqual(preset_design._card(ROOM)["memory"], 24)


class SceneTest(unittest.TestCase):
    def test_a_single_sound_keeps_the_shape_it_always_had(self):
        checked = preset_design.validate(recipe([block(RECTIFIER), block(V30)]))
        self.assertEqual(set(checked), {"name", "target", "explanation", "chain", "sources", "excluded_stages"})
        self.assertEqual(set(checked["chain"][0]), {"model_id", "settings"})

    def test_scene_changes_are_checked_like_any_setting(self):
        amp = block(RECTIFIER, gain=4.)
        amp["scene_settings"] = [{"scene": 1, "name": "GAIN", "value": 7.}]
        checked = preset_design.validate(recipe([amp, block(V30)], scenes=["Rhythm", "Lead guitar solo"]))
        self.assertEqual(checked["scenes"], ["Rhythm", "Lead guitar"])
        self.assertEqual(checked["chain"][0]["scene_settings"], amp["scene_settings"])
        for scenes, change, off in (
                (["Rhythm", "Lead"], {"scene": 2, "name": "GAIN", "value": 7.}, []),  # No such scene.
                (["Rhythm", "Lead"], {"scene": 1, "name": "GAIN", "value": 70.}, []),  # Out of range.
                (["Rhythm", "Lead"], {"scene": 1, "name": "OUTPUT", "value": 3.}, []),  # Not offered.
                ([], {"scene": 0, "name": "GAIN", "value": 7.}, []),  # A change without scenes.
                (["Rhythm", "Lead"], None, [0, 1]),  # Off everywhere is not a block.
                (["Only"], None, [])):
            amp = block(RECTIFIER)
            amp["scene_settings"], amp["off_in_scenes"] = [change] if change else [], off
            with self.assertRaises(ValueError, msg=(scenes, change, off)):
                preset_design.validate(recipe([amp, block(V30)], scenes=scenes))

    def test_a_block_that_changes_between_scenes_is_not_a_neutral_stage(self):
        reverb = block(ROOM, mix=0)
        self.assertTrue(preset_design.validate(recipe([block(RECTIFIER), block(V30), reverb]))["chain"][-1]["reserved"])
        reverb["scene_settings"] = [{"scene": 1, "name": "MIX", "value": 30}]
        checked = preset_design.validate(recipe([block(RECTIFIER), block(V30), reverb], scenes=["Dry", "Wet"]))
        self.assertNotIn("reserved", checked["chain"][-1])

    def test_the_desk_reloads_a_draft_when_its_scene_changes(self):
        draft = {"id": "one"}
        self.assertTrue(monitor.same_working_grid({"draft": draft, "scene": 0}, {"draft": draft, "scene": 0}))
        self.assertFalse(monitor.same_working_grid({"draft": draft, "scene": 0}, {"draft": draft, "scene": 1}))


class SecondPathTest(unittest.TestCase):
    def test_two_amps_run_side_by_side_between_shared_blocks(self):
        checked = preset_design.validate(dual())
        (upper, lower), split = preset_design.layout(checked)
        ids = lambda cells: [cell and cell["model_id"] for cell in cells]
        self.assertEqual(ids(upper), [GOVERNOR, RECTIFIER, V30, EQ, DELAY])
        self.assertEqual(ids(lower), [None, PLEXI, GREENBACK])
        self.assertEqual(split, (1, 3))
        self.assertEqual(preset_design.chain_text(checked),
                         "Brit Governor  →  [ CA Duo Ch3 Modern  →  412 CA Stand OS S V30 90s (M)  ∥  "
                         "Brit Plexi 50 Normal  →  412 Brit 60B GB 71 (M) ]  →  Graphic-9 (neutral)  →  Digital Delay (ST)")

    def test_an_amp_alone_joins_before_the_main_cab(self):
        checked = preset_design.validate(recipe([block(RECTIFIER), block(V30)], parallel=[block(PLEXI)]))
        (upper, lower), split = preset_design.layout(checked)
        self.assertEqual((len(upper), len(lower), split), (2, 1, (0, 1)))
        self.assertEqual(checked["blend"], {"level": 0., "spread": 0.})

    def test_effects_on_their_own_path_leave_the_main_one_dry(self):
        checked = preset_design.validate(recipe([block(RECTIFIER), block(V30)],
                                                parallel=[block(DELAY, mix=100.), block(ROOM, mix=100.)]))
        (upper, lower), split = preset_design.layout(checked)
        self.assertEqual((upper[2:], lower[:2], split), ([None, None], [None, None], (2, 4)))
        self.assertIn("[ dry  ∥  Digital Delay (ST)  →  Room ]", preset_design.chain_text(checked))

    def test_what_cannot_be_built_is_refused(self):
        for parallel, blend, why in (
                ([block(PLEXI), block(EQ)], {}, "own cab"),  # The second amp would reach the mix with no cab.
                ([block(PLEXI), block(PLEXI)], {}, "one block of each type"),
                ([block(PLEXI)], {"level": 20.}, "blend"),
                ([block(draft_render.OUTPUT_GAIN)], {}, "outside the available")):
            with self.assertRaisesRegex(ValueError, why):
                preset_design.validate(recipe([block(RECTIFIER), block(V30)], parallel=parallel, blend=blend))
        wide = recipe([block(GATE), block(5005), block(GOVERNOR), block(RECTIFIER), block(V30), block(EQ)],
                      parallel=[block(DELAY), block(ROOM)])
        with self.assertRaisesRegex(ValueError, "seven device blocks"):
            preset_design.validate(wide)

    def test_neutral_stages_stay_outside_the_stretch_the_second_path_covers(self):
        shaped = preset_design._with_shaping(preset_design.validate(
            recipe([block(RECTIFIER), block(V30)], parallel=[block(PLEXI), block(GREENBACK), block(EQ)])))
        roles = [preset_design.choices()[item["model_id"]]["role"] for item in shaped["chain"]]
        self.assertNotIn("Equalizer", roles)
        self.assertIn("Compressor", roles)

    def test_knobs_reach_both_paths(self):
        controls = styles.draft_controls(preset_design.validate(dual()))
        self.assertEqual({model for model, _ in controls["drive"]},
                         {"Brit Governor", "CA Duo Ch3 Modern", "Brit Plexi 50 Normal"})
        self.assertEqual({model for model, _ in controls["mic"]},
                         {"412 CA Stand OS S V30 90s (M)", "412 Brit 60B GB 71 (M)"})
        # Each path has its own cab, and each cab's filter answers for its path.
        self.assertEqual(len(controls["soft_top"]), 2)


class BuildTest(unittest.TestCase):
    def setUp(self):
        # No model is taken to invert the signal unless a test says so.
        upright = patch.object(draft_render.measured, "polarity", return_value=())
        upright.start()
        self.addCleanup(upright.stop)

    def test_paths_of_opposite_polarity_are_put_right_at_the_mixer(self):
        phase = lambda draft: dict(draft_render.compile_chain(draft, -12.).mixer)[draft_render.MIXER_PHASE]
        self.assertEqual(phase(dual()), 0.)
        amps = frozenset({RECTIFIER, PLEXI})
        with patch.object(draft_render.measured, "polarity", return_value=((amps, frozenset({RECTIFIER})),)):
            self.assertEqual(phase(dual()), 1.)
            _, grid = build(dual(), FakeUnit())
            self.assertEqual(grid.chains[0].mixer[0].params[draft_render.MIXER_PHASE].param_values[0].float_value, 1.)
        # Two amps that each turn their path over leave the paths agreeing, and so does a block before the split.
        with patch.object(draft_render.measured, "polarity", return_value=((amps, amps),)):
            self.assertEqual(phase(dual()), 0.)
        drives = frozenset({GOVERNOR})
        with patch.object(draft_render.measured, "polarity", return_value=((drives, drives),)):
            self.assertEqual(phase(dual()), 0.)

    def test_the_compiled_chain_places_both_rows_and_what_varies(self):
        chain = draft_render.compile_chain(dual(), -12.)
        self.assertEqual([model for model, _ in chain], [GOVERNOR, RECTIFIER, V30, EQ, DELAY, draft_render.OUTPUT_GAIN])
        self.assertEqual([model for model, _ in chain.side], [0, PLEXI, GREENBACK])
        self.assertEqual((chain.split, chain.scenes), ((1, 3), ("Rhythm", "Lead")))
        self.assertEqual(chain.mixer, [(1, 2.), (2, -3.), (3, 8.), (4, 0.)])
        self.assertEqual(chain.varied, {(0, 1, 0): {1: 6.5}})
        self.assertEqual(chain.off, {(0, 4): frozenset({0})})
        self.assertEqual(draft_render.scene_value(chain, 0, 1, 0, 0), 4.)
        # Both microphones of the second path's cab move together.
        self.assertEqual(chain.side[2][1], [(5, 6.), (13, 6.)])
        plain = draft_render.compile_chain(recipe([block(RECTIFIER), block(V30)]), -12.)
        self.assertEqual((plain.split, plain.scenes, list(plain.side)), (None, (), []))

    def test_the_unit_ends_up_with_both_paths_and_every_scene(self):
        unit = FakeUnit()
        chain, grid = build(dual(), unit)
        rows = [[cell.hash for cell in row.models if cell.hash] for row in grid.chains]
        self.assertEqual(rows, [[GOVERNOR, RECTIFIER, V30, EQ, DELAY, draft_render.OUTPUT_GAIN], [PLEXI, GREENBACK], [], []])
        self.assertEqual([(point.split, point.mix) for point in grid.chains[0].split_control_points], [(1, 3)])
        self.assertEqual(iterate.routes(grid), iterate.USB_ROUTES)
        gain = grid.chains[0].models[1].params[0]
        self.assertTrue(gain.scene_mode)
        self.assertEqual([round(models.to_display(RECTIFIER, 0, value.float_value), 2) for value in gain.param_values[:3]],
                         [4., 6.5, 4.])
        self.assertFalse(grid.chains[1].models[1].params[0].scene_mode)  # The second amp is the same in each scene.
        delay = grid.bypass[0].colBypass[4]
        self.assertEqual((delay.sceneMode, [entry.bypass for entry in delay.sceneBypass][:2]), (True, [True, False]))
        self.assertEqual(list(grid.scene_labels)[:3], ["Rhythm", "Lead", ""])
        mixer = [round(models.to_display(draft_render.MIXER, index, grid.chains[0].mixer[0].params[index]
                                         .param_values[0].float_value), 2) for index in (1, 2, 3)]
        self.assertEqual(mixer, [2., -3., 8.])
        self.assertEqual(unit.scene, 0)
        # The same grid is confirmed for the second scene, and a wrong value there is caught.
        with patch("time.sleep"):
            draft_render.assert_grid(unit, chain, iterate.USB_ROUTES, dual(), scene=1)
            unit.grid.chains[0].models[1].params[0].param_values[1].float_value = .1
            with self.assertRaisesRegex(RuntimeError, "CA Duo Ch3 Modern"):
                draft_render.assert_grid(unit, chain, iterate.USB_ROUTES, dual(), scene=1)

    def test_a_block_the_unit_refuses_is_named(self):
        with self.assertRaisesRegex(RuntimeError, "no processing power left for Brit Plexi 50 Normal"):
            build(dual(), FakeUnit(refuse={PLEXI}))

    def test_a_plain_draft_builds_as_before(self):
        unit = FakeUnit()
        _, grid = build(recipe([block(RECTIFIER, gain=3.), block(V30)]), unit)
        self.assertEqual([cell.hash for cell in grid.chains[0].models if cell.hash],
                         [RECTIFIER, V30, draft_render.OUTPUT_GAIN])
        self.assertEqual([(point.split, point.mix) for point in grid.chains[0].split_control_points], [(-1, -1)])
        self.assertEqual(list(grid.scene_labels)[:1], [""])


if __name__ == "__main__":
    unittest.main()
