"""What the app knows about gear: the shipped tables against the catalog, what a chain costs, which models the
unit will not run, polarity, and what an amp's crest factor measured. The unit's own table is made up here, in a
data folder of the test's own."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from lab import knowledge
from lab.sound import catalog

GATE, GOVERNOR, RECTIFIER, PLEXI, V30, GREENBACK, EQ, DELAY, ROOM = 16003, 11, 1018, 1081, 12001, 12006, 4005, 6011, 8000
HIWATT, TOP_BOOST, RAT = 1063, 1011, 4
CACHES = (knowledge.table, knowledge.locked, knowledge._gear_index)


def fresh():
    for cached in CACHES:
        cached.cache_clear()


class Home(unittest.TestCase):
    """A data folder of the test's own, with no earlier app's files to copy from."""

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name) / "home"
        self.home.mkdir()
        for patch in (mock.patch.dict(os.environ, {"TONE_LAB_HOME": str(self.home)}),
                      mock.patch.object(knowledge, "REPO", Path(folder.name) / "nowhere")):
            patch.start()
            self.addCleanup(patch.stop)
        fresh()
        self.addCleanup(fresh)

    def harvest(self, **extra):
        (self.home / knowledge.DEVICE).write_text(json.dumps({"firmware": "4.1.1", **extra, "models": {
            "27": {"name": "Green 808", "category": "Guitar Overdrive", "available": True, "presets": [
                {"id": "x", "name": "Default", "values": [["OVERDRIVE", 5.], ["TONE", 5.], ["LEVEL", 5.]]},
                {"id": "1", "name": "Boost", "values": [["OVERDRIVE", 0.], ["TONE", 5.], ["LEVEL", 10.], ["MODE", "x"]]}]},
            "1172": {"name": "Nolly Rhythm", "category": "Guitar Amplifier", "available": False, "presets": []},
        }}), encoding="utf-8")
        fresh()

    def tables(self, **replaced):
        """Stand some tables in for the real ones."""
        if getattr(self, "standing", None):
            self.standing.stop()
        real = knowledge.table
        self.standing = mock.patch.object(knowledge, "table",
                                          lambda name: replaced[name] if name in replaced else real(name))
        self.standing.start()
        self.addCleanup(self.standing.stop)
        knowledge.locked.cache_clear()


class TablesTest(Home):
    def test_the_gear_table_matches_the_catalog(self):
        data = knowledge.table("gear")
        amps = {model.id for model in catalog.models() if model.category == "Guitar Amplifier"}
        described = [model_id for family in data["amps"].values() for model_id in family["models"]]
        self.assertEqual(sorted(described), sorted(amps))           # every amp, each in one family
        for family in data["amps"].values():
            names = set().union(*({param.name for param in catalog.model(model_id).params}
                                  for model_id in family["models"]))
            self.assertLessEqual(set(family["controls"]), names)
            for start in family["starts"]:
                self.assertLessEqual(set(start["settings"]), names)
            for item in (*family["boosts"], *family["cabs"]):
                self.assertIsNotNone(catalog.model(item["model"]))
        for model_id, drive in data["drives"].items():
            names = {param.name for param in catalog.model(int(model_id)).params}
            self.assertLessEqual(set(drive["controls"]), names)
            self.assertTrue(all(set(role["settings"]) <= names for role in drive["roles"]))
        for section in ("cabs", "effects"):
            self.assertTrue(all(catalog.model(int(model_id)) for model_id in data[section]), section)

    def test_the_measured_tables_name_only_models_and_controls_the_catalog_has(self):
        controls = knowledge.table("controls")
        for model_id, amp in controls["amps"].items():
            info = catalog.model(int(model_id))
            self.assertIsNotNone(info, model_id)
            for name in (*amp["knobs"], *amp["switches"]):
                self.assertTrue(info.named(name), (info.name, name))
            self.assertIn("crest", amp["default"])
        self.assertTrue(all(catalog.model(model_id) for model_id in controls["inert"]))
        self.assertLessEqual({knob.get("role") for amp in controls["amps"].values() for knob in amp["knobs"].values()}
                             - {None}, set(controls["typical"]))
        polarity = knowledge.table("polarity")
        for kind in ("amps", "cabs"):
            measured, inverted = set(polarity[kind]["measured"]), set(polarity[kind]["inverted"])
            self.assertLessEqual(inverted, measured)
            self.assertTrue(all(catalog.model(model_id) for model_id in measured))
        costs = knowledge.table("costs")
        self.assertTrue(all(catalog.model(int(model_id)) for model_id in costs["models"]))
        self.assertTrue(all(isinstance(entry.get("cpu"), (int, float)) for entry in costs["models"].values()))

    def test_a_model_is_known_by_what_players_say_of_it(self):
        vox = knowledge.gear(TOP_BOOST)
        self.assertEqual(vox["kind"], "amp")
        self.assertIn("Vox AC30", vox["real"])
        self.assertIn("WORKS BACKWARDS", knowledge.gear(RAT)["controls"]["FILTER"]["does"])
        self.assertEqual(knowledge.gear(V30)["kind"], "cab")
        self.assertIsNone(knowledge.gear(16005))
        self.assertIn("Full Shred", knowledge.guitar_notes("Charvel")["pickups"])
        self.assertIsNone(knowledge.guitar_notes("Ukulele"))

    def test_a_shipped_table_that_is_missing_is_an_error_and_the_units_own_is_not(self):
        with mock.patch.object(knowledge, "SHARED", self.home / "none"):
            fresh()
            with self.assertRaises(knowledge.MissingTable):
                knowledge.table("costs")
            self.assertEqual(knowledge.missing(), ["gear.json", "costs.json", "controls.json", "polarity.json"])
            self.assertEqual(knowledge.table("device"), {})
        fresh()
        self.assertEqual(knowledge.missing(), [])

    def test_tables_made_on_another_firmware_are_named_with_how_to_remake_them(self):
        self.harvest()
        self.assertEqual(knowledge.stale("4.1.1"), [])
        found = {name: how for name, _, how in knowledge.stale("4.2.0")}
        self.assertEqual(set(found), {"costs", "controls", "polarity", "device"})
        self.assertEqual((found["costs"], found["device"]), ("python -m lab budget", "python -m lab named-settings"))


class BudgetTest(Home):
    def test_the_model_agrees_with_every_case_the_unit_accepted_or_refused(self):
        jm, comp, gain, loader, capture = 1182, 5005, 16005, 29001, 14001
        seen = [
            ([RECTIFIER] * 2, True), ([RECTIFIER] * 3, False),  # the third has no processor with room for its code
            ([jm] * 2, True), ([jm] * 3, False),
            ([ROOM] * 3, True), ([ROOM] * 4, False),            # delay memory is one pool
            ([loader] * 4, True), ([loader] * 5, False),
            ([DELAY] * 13, True), ([DELAY] * 14, False),
            ([capture] * 3, True), ([capture] * 4, False),
            ([GOVERNOR] * 9 + [comp] * 4 + [gain], True), ([GOVERNOR] * 10, False),
            ([RECTIFIER] * 2 + [GOVERNOR] * 4 + [comp] * 5 + [gain] * 2, True),
            ([RECTIFIER] * 2 + [GOVERNOR] * 5, False),
            ([jm] * 2 + [ROOM] * 3, True),
        ]
        for blocks, fits in seen:
            self.assertEqual(knowledge.weigh(blocks).fits, fits, blocks)

    def test_a_stock_cab_costs_what_the_default_cab_block_costs(self):
        self.assertEqual(knowledge.cost(GREENBACK), knowledge.cost(12000))
        self.assertGreater(knowledge.cost(RECTIFIER)["cpu"], 40)
        self.assertIsNone(knowledge.weigh([RECTIFIER, V30]).refused)
        self.assertIn("memory", knowledge.cost(ROOM))

    def test_what_does_not_fit_is_named_and_a_model_of_unknown_cost_is_said_not_counted(self):
        self.assertEqual(knowledge.weigh([RECTIFIER] * 3 + [V30]).refused, RECTIFIER)
        self.assertEqual(knowledge.weigh([ROOM] * 4).refused, ROOM)
        load = knowledge.weigh([RECTIFIER, 0, 987654])
        self.assertEqual((load.fits, load.unknown), (True, (987654,)))
        self.assertIsNone(knowledge.cost(987654))

    def test_the_limits_come_from_the_costs_table_when_it_gives_them(self):
        self.assertEqual(knowledge.limits(), (knowledge.CPU, 85.))     # as found by filling the unit
        real = knowledge.table("costs")
        self.tables(costs={**real, "core": {**real["core"], "processors": [40., 40.], "memory": 20.}})
        self.assertEqual(knowledge.limits(), ((40., 40.), 20.))
        self.assertEqual(knowledge.weigh([RECTIFIER, V30]).refused, RECTIFIER)
        self.assertEqual(knowledge.weigh([ROOM]).refused, ROOM)
        self.tables(costs={**real, "core": {**real["core"], "processors": "many", "memory": None}})
        self.assertEqual(knowledge.limits(), (knowledge.CPU, knowledge.MEMORY))


class LockedTest(Home):
    def test_a_plugin_the_unit_refused_one_model_of_is_locked_throughout(self):
        self.harvest()
        self.tables(controls={"inert": []})
        locked = knowledge.locked()
        self.assertTrue({1171, 1172, 1173, 1174, 34, 35} <= locked)
        self.assertFalse({1161, RECTIFIER, 5, 27} & locked)

    def test_with_the_plugins_the_unit_owns_listed_every_other_plugin_is_locked(self):
        self.harvest(plugins=[3])
        self.tables(controls={"inert": []})
        locked = knowledge.locked()
        self.assertNotIn(1161, locked)          # Plini Lead, plugin 3
        self.assertIn(1163, locked)             # Gojira RST belongs to another plugin
        self.assertNotIn(RECTIFIER, locked)     # a native model needs no licence

    def test_nothing_from_a_plugin_whose_amp_does_nothing_is_usable(self):
        self.tables(controls={"inert": [1180]})         # Petrucci Lead loaded and passed the guitar through
        locked = knowledge.locked()
        self.assertTrue({1177, 1178, 1179, 1180} <= locked)
        self.assertFalse({RECTIFIER, TOP_BOOST} & locked)

    def test_without_a_harvest_only_what_was_measured_inert_is_ruled_out(self):
        self.assertEqual(knowledge.table("device"), {})
        inert = set(knowledge.table("controls")["inert"])
        plugins = {catalog.model(model_id).plugin for model_id in inert}
        self.assertEqual(knowledge.locked(), {model.id for model in catalog.models() if model.plugin in plugins} | inert)
        self.tables(controls={"inert": []})
        self.assertEqual(knowledge.locked(), frozenset())
        self.assertEqual(knowledge.named_settings(27), {})

    def test_named_settings_are_read_without_the_default_and_without_text(self):
        self.harvest()
        self.assertEqual(knowledge.named_settings(27), {"Boost": {"OVERDRIVE": 0., "TONE": 5., "LEVEL": 10.}})
        self.assertEqual(knowledge.named_settings(1172), {})        # the unit would not load it
        self.assertEqual(knowledge.named_settings(RECTIFIER), {})


class MeasuredTest(Home):
    def test_paths_oppose_when_an_odd_number_of_inverting_models_separates_them(self):
        self.tables(polarity={"amps": {"measured": [1, 2, 3, 4], "inverted": [1, 2]},
                              "cabs": {"measured": [20, 21], "inverted": [21]}})
        self.assertTrue(knowledge.opposed([1, 20], [3, 20]))
        self.assertFalse(knowledge.opposed([1, 20], [2, 20]))
        self.assertFalse(knowledge.opposed([3], [4]))
        self.assertFalse(knowledge.opposed([1, 21], [3, 20]))       # the amp and the cab each turn the path over
        # An amp beside a path without one was not measured, and neither was a capture.
        self.assertFalse(knowledge.opposed([1], []))
        self.assertFalse(knowledge.opposed([1, 99], [1, 20]))
        self.assertTrue(knowledge.opposed([1, 99], [3, 98]))

    def test_the_real_table_turns_a_plexi_over_against_a_rectifier(self):
        self.assertTrue(knowledge.opposed([PLEXI, V30], [RECTIFIER, GREENBACK]))
        self.assertFalse(knowledge.opposed([RECTIFIER, V30], [RECTIFIER, GREENBACK]))

    def test_an_amps_crest_factor_is_given_at_three_settings_of_its_gain_with_the_word_it_suggests(self):
        side = lambda moved, crest, **more: {"moved": moved, "level": 1., "bands": [0.] * 5, "crest": crest, **more}
        amp = {"default": {"crest": 13.}, "switches": {}, "knobs": {
            "PREAMP": {"low": side(-3., 0.)},                               # not a control this model has
            "GAIN": {"low": side(-3., -2.), "high": side(3., -1.)}, "BASS": {"low": side(-3., 0.)}}}
        quiet = {"default": {"crest": 13.}, "switches": {}, "knobs": {"GAIN": {"low": side(-3., 0., silent=True)}}}
        self.tables(controls={"amps": {str(RECTIFIER): amp, str(PLEXI): quiet, str(HIWATT): {"default": {"crest": 20.},
                                                                                             "knobs": {"BASS": {}}}}})
        # Three points down the peaks stand 6 dB higher, three points up 3 dB lower.
        self.assertEqual(knowledge.crest_by_gain(RECTIFIER),
                         "GAIN 2: 19 dB (suggests clean), 5: 13 dB (suggests crunch), 8: 10 dB (suggests saturated)")
        self.assertEqual(knowledge.crest_by_gain(PLEXI), "GAIN 2: silent, 5: 13 dB (suggests crunch)")
        self.assertIsNone(knowledge.crest_by_gain(HIWATT))          # no gain control was measured
        self.assertIsNone(knowledge.crest_by_gain(TOP_BOOST))       # not measured at all
        self.assertEqual([knowledge.crest_word(crest) for crest in (19.7, 15., 11.3, 9.5, -1.)],
                         ["clean", "edge of breakup", "overdriven", "saturated", "saturated"])

    def test_measured_on_the_unit_a_hiwatt_keeps_its_crest_and_a_rectifier_does_not(self):
        self.assertEqual(knowledge.crest_by_gain(HIWATT),
                         "GAIN 2: 20 dB (suggests clean), 5: 20 dB (suggests clean), 8: 20 dB (suggests clean)")
        self.assertIn("5: 9 dB (suggests saturated)", knowledge.crest_by_gain(RECTIFIER))
        self.assertFalse(hasattr(knowledge, "drive_curve"))         # named for the inference, not the measurement


if __name__ == "__main__":
    unittest.main()
