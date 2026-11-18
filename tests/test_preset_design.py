from contextlib import nullcontext
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from qc_mcp import catalog, preset

from tone_lab import advisor, draft_render, iterate, preset_design, qc_library, session, styles
from tone_lab.preset_library import SavedPreset


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

    def test_owned_ir_creates_a_versioned_draft_and_verifies_the_exact_attachment(self):
        ir = qc_library.Asset("ir", "CIR_D1CE", "Own closed back", "user")
        original = {**raw_draft(), "id": "previous", "guitar": "Les Paul"}
        with TemporaryDirectory() as folder, patch.object(session, "SESSIONS", Path(folder)):
            draft = preset_design.variant_with_asset(original, ir)
            self.assertEqual([block["model_id"] for block in draft["chain"]], [1120, ir.model_id])
            self.assertEqual(draft["parent"], "previous")
            self.assertEqual(preset_design.history()[0]["chain"], draft["chain"])
            self.assertIn(ir.name, preset_design.chain_text(draft))

        chain = draft_render.compile_chain(draft, -12)
        builder = preset.PresetBuilder("Verified IR")
        for row, (inlet, outlet) in enumerate(iterate.USB_ROUTES):
            builder.set_routing(row, in_portid=inlet, out_portid=outlet)
        for column, (model_id, settings) in enumerate(chain):
            builder.add_block(0, column, model_id, params={index: catalog.to_norm(model_id, index, display)
                                                          for index, display in settings})
        grid = builder.build()
        binding = grid.chains[0].models[1].params.add()
        binding.param_values.add().string_value = ir.key
        with patch.object(draft_render.device, "read_preset", return_value=grid):
            draft_render.assert_grid(Mock(), chain, iterate.USB_ROUTES, draft)
            binding.param_values[0].string_value = "CIR_F0"
            with self.assertRaisesRegex(RuntimeError, "asset binding"):
                draft_render.assert_grid(Mock(), chain, iterate.USB_ROUTES, draft)

    def test_capture_requires_an_explicit_version_and_cabinet_status(self):
        capture = qc_library.Asset("capture", "1" * 64, "Owned combo", "user")
        ir = qc_library.Asset("ir", "CIR_D1CE", "Own closed back", "user")
        original = {**raw_draft(), "id": "previous", "guitar": "Les Paul"}
        with TemporaryDirectory() as folder, patch.object(session, "SESSIONS", Path(folder)):
            with self.assertRaises(ValueError):
                preset_design.variant_with_asset(original, capture)
            with self.assertRaises(ValueError):
                preset_design.variant_with_asset(original, capture, capture_has_cab=True)
            combo = preset_design.variant_with_asset(original, capture, capture_has_cab=True, capture_version=2)
            self.assertEqual([block["model_id"] for block in combo["chain"]], [14001])
            with self.assertRaisesRegex(ValueError, "already|includes"):
                preset_design.variant_with_asset(combo, ir)
            amp = preset_design.variant_with_asset(original, capture, capture_has_cab=False, capture_version=1)
            self.assertEqual([block["model_id"] for block in amp["chain"]], [14000, 12026])

    def test_advisor_asset_token_can_only_resolve_an_eligible_owned_asset(self):
        ir = qc_library.Asset("ir", "CIR_D1CE", "Own closed back", "user")
        capture = qc_library.Asset("capture", "1" * 64, "Owned combo", "user", capture_version=2)
        with TemporaryDirectory() as folder, patch.object(session, "SESSIONS", Path(folder)):
            with patch.object(advisor, "request", return_value={**raw_draft(), "owned_assets": ["asset1"]}):
                draft = preset_design.design("Use Own closed back", "Les Paul", {}, assets=[capture, ir])
                self.assertEqual(draft["chain"][-1]["asset"]["key"], ir.key)
            with patch.object(advisor, "request", return_value={**raw_draft(), "owned_assets": ["asset2"]}):
                with self.assertRaisesRegex(ValueError, "unusable draft"):
                    preset_design.design("Use Own closed back", "Les Paul", {}, assets=[capture, ir])

    def test_invalid_tone_direction_cannot_be_saved(self):
        self.assertEqual(draft_render.clean_values({"drive": 0, "warmth": .3}), {"warmth": .3})
        for values in ({"warmth": 1.1}, {"warmth": float("nan")}, {"warmth": True}, {"unknown": .3}):
            with self.assertRaises(ValueError):
                draft_render.clean_values(values)

    def test_save_verifies_the_shaped_readback_and_keeps_the_native_address(self):
        choice = SavedPreset("Goldtop Bite", "Tone Lab", "2_q", 3)
        rig = {**raw_draft(), "id": "draft123"}
        qc = Mock(firmware="4.1.1")
        grid = preset.PresetBuilder("verified").build()
        edits = {(0, 0, 0): {"to": 6}}
        with (
            TemporaryDirectory() as folder,
            patch.object(session, "SESSIONS", Path(folder)),
            patch.object(draft_render.device, "connect", return_value=nullcontext(qc)),
            patch.object(draft_render, "working_grid", return_value=nullcontext(None)),
            patch.object(draft_render, "build_grid"),
            patch.object(draft_render, "route_to_guitar", return_value=grid),
            patch.object(draft_render, "_shape", return_value=(edits, 1)) as shape,
            patch.object(draft_render, "assert_grid", return_value=grid) as verify,
            patch.object(draft_render.device, "active_scene", return_value=1),
            patch.object(draft_render.device, "live_position", return_value=draft_render.WORK_SLOT),
            patch.object(draft_render.iterate, "recall"),
            patch.object(draft_render.presets, "archive"),
            patch.object(draft_render.time, "sleep"),
            patch.object(qc_library, "destination", return_value=choice) as destination,
        ):
            label = draft_render.save(rig, values={"warmth": .3})
            self.assertEqual(label, "Goldtop Bite · Tone Lab 1D")
            receipt = json.loads((Path(folder) / "drafts" / "draft123.kept.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["preset"]["position"], choice.position)
            self.assertEqual(receipt["knob_values"], {"warmth": .3})
            self.assertEqual(receipt["scene"], "B")
        shape.assert_called_once_with(qc, grid, {"warmth": .3}, None)
        self.assertEqual(verify.call_count, 2)
        self.assertTrue(all(call.kwargs == {"edits": edits, "scene": 1} for call in verify.call_args_list))
        destination.assert_called_with(qc, "Goldtop Bite", allow_my_presets=False)
        qc.write_preset_file.assert_called_once_with("2_q", 3, "Goldtop Bite")


if __name__ == "__main__":
    unittest.main()
