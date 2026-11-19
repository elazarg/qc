import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tone_lab import device_presets, lore, models


class GearKnowledgeTest(unittest.TestCase):
    def test_the_knowledge_file_matches_the_catalog(self):
        data = json.loads(lore.KNOWLEDGE.read_text(encoding="utf-8"))
        amps = {model["id"] for model in models.all_models() if model["category"] == "Guitar Amplifier"}
        described = [model_id for family in data["amps"].values() for model_id in family["models"]]
        self.assertEqual(sorted(described), sorted(amps))  # Every amp, each in one family.
        for family in data["amps"].values():
            names = set().union(*({param["name"] for param in models.lookup(model_id)["params"]}
                                  for model_id in family["models"]))
            self.assertLessEqual(set(family["controls"]), names)
            for start in family["starts"]:
                self.assertLessEqual(set(start["settings"]), names)
            for item in (*family["boosts"], *family["cabs"]):
                self.assertIsNotNone(models.lookup(item["model"]))
        for model_id, drive in data["drives"].items():
            names = {param["name"] for param in models.lookup(int(model_id))["params"]}
            self.assertLessEqual(set(drive["controls"]), names)
            self.assertTrue(all(set(role["settings"]) <= names for role in drive["roles"]))
        self.assertTrue(all(models.is_stock_cab(int(model_id)) or models.lookup(int(model_id))
                            for model_id in data["cabs"]))

    def test_a_model_is_described_in_plain_sentences(self):
        vox = lore.about(1011)
        self.assertIn("Vox AC30", vox)
        self.assertIn("TONE CUT works backwards", vox)
        self.assertIn("up = darker", lore.about(4))  # The RAT's Filter.
        self.assertIn("Vintage 30", lore.about(12001))
        self.assertEqual(lore.about(16005), "")
        self.assertIn("57 + 121", lore.about_asset("YA MES 212 V30 Mix 01"))
        self.assertIn("SM57", lore.about_asset("YA MES 212 V30 57-CE 1"))
        self.assertEqual(lore.about_asset("My own cab"), "")

    def test_a_request_points_at_gear_by_name_artist_or_genre(self):
        def named(text):
            return {models.lookup(model_id)["name"] for model_id in lore.candidates(text)}

        self.assertIn("UK C30 TopBoost", named("something like The Edge"))
        self.assertTrue({"CA Duo Ch3 Modern", "Green 808", "412 CA Stand OS S V30 90s (M)"}
                        <= named("rectifier wall of sound"))
        self.assertTrue({"Plini Clean", "Plini Lead"} <= named("plini"))
        self.assertIn("US DLX Vibrato", named("blues with a deluxe reverb"))
        self.assertEqual(lore.candidates("a bit tighter"), [])
        self.assertEqual(lore.candidates("too bright on the high strings"), [])

    def test_a_request_finds_the_usual_chain_for_the_sound_it_names(self):
        def found(text):
            return [recipe["target"] for recipe in lore.recipes(text)]

        self.assertEqual(found("steve vai for the love of god"), ["Steve Vai lead"])
        self.assertEqual(found("santana europa"), ["Santana lead"])
        self.assertEqual(found("make a tight metal tone"), ["modern metal rhythm"])
        self.assertEqual(found("a bit tighter"), [])
        self.assertIn("Full Shred", lore.guitar("Charvel")["pickups"])
        self.assertIsNone(lore.guitar("Ukulele"))

    def test_the_assistant_is_briefed_on_the_chain_and_the_words(self):
        brief = lore.brief([4, 1011, 16005], "it gets muddy and harsh")
        self.assertEqual([block["model"] for block in brief["loaded_gear"]], ["Rodent Drive", "UK C30 TopBoost"])
        self.assertIn("INVERTED", brief["loaded_gear"][1]["controls"]["TONE CUT"])
        self.assertEqual([remark["word"] for remark in brief["remarks"]], ["harsh", "muddy"])
        self.assertTrue(brief["remarks"][0]["players_try_in_order"][0].startswith("less presence"))
        self.assertEqual(lore.brief([16005], "hello"), {})


class DevicePresetsTest(unittest.TestCase):
    def record(self, folder, **extra):
        path = Path(folder) / "device-presets.json"
        path.write_text(json.dumps({"firmware": "4.1.1", **extra, "models": {
            "27": {"name": "Green 808", "category": "Guitar Overdrive", "available": True, "presets": [
                {"id": "x", "name": "Default", "values": [["OVERDRIVE", 5.], ["TONE", 5.], ["LEVEL", 5.]]},
                {"id": "1", "name": "Boost", "values": [["OVERDRIVE", 0.], ["TONE", 5.], ["LEVEL", 10.]]}]},
            "5": {"name": "Green 808", "category": "Guitar Overdrive", "available": True, "presets": [
                {"id": "x", "name": "Default", "values": [["OVERDRIVE", 5.], ["TONE", 5.], ["LEVEL", 5.]]}]},
            "1172": {"name": "Nolly Rhythm", "category": "Guitar Amplifier", "available": False, "presets": []},
        }}), encoding="utf-8")
        return path

    def test_named_settings_are_read_and_shared_with_a_model_of_the_same_name(self):
        with TemporaryDirectory() as folder, patch.object(device_presets, "PATH", self.record(folder)):
            device_presets.load.cache_clear()
            try:
                boost = {"name": "Boost", "values": {"OVERDRIVE": 0., "TONE": 5., "LEVEL": 10.}}
                self.assertIn(boost, device_presets.settings(27))
                self.assertIn(boost, device_presets.settings(5))  # The older id of the same pedal.
                self.assertEqual(device_presets.settings(1172), [])
                # A plugin the unit refused one model of is treated as unlicensed throughout.
                locked = device_presets.unavailable()
                self.assertTrue({1171, 1172, 1173, 1174, 34, 35} <= locked)
                self.assertFalse({1161, 1018, 5, 27} & locked)
            finally:
                device_presets.load.cache_clear()

    def test_models_of_an_unlicensed_plugin_are_unavailable(self):
        with TemporaryDirectory() as folder, patch.object(device_presets, "PATH", self.record(folder, plugins=[3])):
            locked = device_presets.unavailable()
        self.assertNotIn(1161, locked)  # Plini Lead, plugin 3.
        self.assertIn(1163, locked)  # Gojira RST belongs to another plugin.
        self.assertNotIn(1018, locked)  # A native model needs no licence.

    def test_without_a_harvest_nothing_is_ruled_out(self):
        with TemporaryDirectory() as folder, patch.object(device_presets, "PATH", Path(folder) / "none.json"):
            device_presets.load.cache_clear()
            try:
                self.assertEqual(device_presets.unavailable(), set())
                self.assertEqual(device_presets.settings(27), [])
            finally:
                device_presets.load.cache_clear()


if __name__ == "__main__":
    unittest.main()
