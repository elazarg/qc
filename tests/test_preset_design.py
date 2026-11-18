from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from qc_mcp import preset

from tone_lab import advisor, draft_render, preset_design, session, styles


def raw_draft():
    return {"name": "Goldtop Bite", "target": "Vintage rock lead", "explanation": "An amp pushed hard.",
            "chain": [{"model_id": 12026, "settings": []},
                      {"model_id": 1120, "settings": [{"name": "VOLUME", "value": 7.5}]}],
            "sources": []}


class PresetDraftTest(unittest.TestCase):
    def test_intent_distinguishes_new_preset_from_adjustments(self):
        self.assertTrue(preset_design.is_request("Make me a Lenny Kravitz preset."))
        self.assertTrue(preset_design.is_request("Preset for old-school soul"))
        self.assertFalse(preset_design.is_request("Make the current preset warmer"))
        self.assertFalse(preset_design.is_request("More jazz, keep the attack"))

    def test_design_uses_the_validated_chain_and_preserves_revision_history(self):
        with TemporaryDirectory() as folder, patch.object(session, "SESSIONS", Path(folder)):
            with patch.object(advisor, "request", side_effect=[raw_draft(), raw_draft()]) as provider:
                first = preset_design.design("Vintage lead", "Les Paul", {})
                second = preset_design.design("Tighter bass", "Les Paul", {}, first, "too woolly")
            self.assertTrue(provider.call_args_list[0].kwargs["web"])
            self.assertFalse(provider.call_args_list[1].kwargs["web"])
            self.assertEqual([row["model_id"] for row in first["chain"]], [1120, 12026])
            self.assertEqual(second["parent"], first["id"])
            self.assertEqual([row["id"] for row in preset_design.history()], [second["id"], first["id"]])
            self.assertEqual(draft_render.compile_chain(first, -12)[-1], (draft_render.OUTPUT_GAIN, [(0, -12)]))

    def test_draft_disallows_levels_routing_and_out_of_range_controls(self):
        for setting in ({"name": "OUTPUT", "value": 12}, {"name": "VOLUME", "value": 100}):
            recipe = raw_draft()
            recipe["chain"][1]["settings"] = [setting]
            with self.assertRaises(ValueError):
                preset_design.validate(recipe)
        recipe = raw_draft()
        recipe["chain"][0]["model_id"] = draft_render.OUTPUT_GAIN
        with self.assertRaises(ValueError):
            preset_design.validate(recipe)

    def test_new_slot_preserves_named_and_keyed_presets_and_scratch_slots(self):
        slots = [{"index": index, "name": "", "key": ""} for index in range(256)]
        slots[4]["name"] = "Goldtop Bite"
        slots[11]["key"] = "existing"
        self.assertEqual(draft_render.new_slot(slots, "Goldtop Bite"), (12, "Goldtop Bite 2"))
        for row in slots[:-2]:
            row["key"] = "existing"
        with self.assertRaises(RuntimeError):
            draft_render.new_slot(slots, "Goldtop Bite")

    def test_drive_turns_down_a_classic_amp_volume_without_zeroing_it(self):
        grid = preset.PresetBuilder("Test").add_block(0, 0, 1120, params={0: .75})
        original = grid.build()
        edits = styles.knobs(original, 0, {"drive": -1.0})
        volume = next(row for row in edits if row["name"] == "VOLUME")
        self.assertAlmostEqual(volume["to"], 1.5)


if __name__ == "__main__":
    unittest.main()
