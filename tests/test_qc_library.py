import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tone_lab import preset_library, qc_library, session


V1 = "a" * 64
V2 = "b" * 64
USER = "c" * 64
CLOUD = "d" * 64


def directory_folder(name, key, files, **flags):
    return {"name": name, "key": key, "files": files, "is_factory": False, "is_downloads": False, **flags}


def item(name, key, index=0):
    return {"name": name, "key": key, "index": index}


def slots(*contents):
    rows = [item("", "", index) for index in range(256)]
    for index, name, key in contents:
        rows[index] = item(name, key, index)
    return rows


class QuadCortexLibraryTest(unittest.TestCase):
    def test_directory_uses_root_asset_keys_and_resolves_duplicate_folder_links(self):
        library = qc_library.from_catalog({"irs": [
            directory_folder("My IRs", "2_q", [item("", "CIR_ff")]),
            directory_folder("IRs Library", "local_ir_root", [item("User IR", "CIR_ff")]),
        ], "captures": [
            directory_folder("Factory Captures V1", "1_f", [item("", V1)], is_factory=True),
            directory_folder("Factory Captures V2", "2_f", [item("", V2)], is_factory=True),
            directory_folder("Library", "local_nc_root", [item("First", V1), item("Second", V2), item("Mine", USER)]),
            directory_folder("My Captures", "1_q", [item("", USER)]),
            directory_folder("Downloads", "cloud-2-1", [item("Cloud listing", CLOUD)], is_downloads=True),
        ]})
        (ir,) = library.irs
        self.assertEqual(ir.folders, ("IRs Library", "My IRs"))
        self.assertEqual(ir.model_id, 29001)
        by_name = {asset.name: asset for asset in library.captures}
        self.assertEqual(by_name["First"].model_id, 14000)
        self.assertEqual(by_name["Second"].model_id, 14001)
        self.assertEqual(by_name["Mine"].source, "user")
        self.assertIsNone(by_name["Mine"].capture_version)
        self.assertEqual(library.download_captures[0].name, "Cloud listing")
        self.assertEqual(library.find_assets("cloud"), [])

    def test_save_plans_free_slot_and_avoids_existing_name(self):
        rows = [directory_folder("Tone Lab", "/media/p4/Presets/Tone Lab",
                                 slots((0, "First", "slot1"), (3, "", "key-only")))]
        plan = qc_library.destination(SimpleNamespace(list_directory=lambda: {"presets": rows}), "First")
        self.assertEqual(plan.name, "First 2")
        self.assertEqual(plan.slot, "1E")
        self.assertEqual(plan.position, {"folder_key": rows[0]["key"], "position": 4, "is_factory": False})

    def test_save_requires_complete_user_slots_and_preserves_my_presets_fixtures(self):
        incomplete = [directory_folder("Tone Lab", "/media/p4/Presets/Tone Lab", slots()[:-1])]
        qc = SimpleNamespace(list_directory=lambda: {"presets": incomplete})
        with self.assertRaises(qc_library.SetlistUnavailable):
            qc_library.destination(qc, "Name")
        used = slots(*((i, f"P{i}", f"slot{i}") for i in range(254)))
        qc.list_directory = lambda: {"presets": [directory_folder("My Presets", qc_library.MY_PRESETS_KEY, used)]}
        with self.assertRaisesRegex(qc_library.SetlistUnavailable, "has no empty"):
            qc_library.destination(qc, "Name", allow_my_presets=True)

    def test_receipt_round_trips_position_and_preserves_scene_and_macros(self):
        with TemporaryDirectory() as target, patch.object(session, "SESSIONS", Path(target)):
            preset = preset_library.SavedPreset("Silk", "Tone Lab", "/media/p4/Presets/Tone Lab", 3)
            label = qc_library.store_receipt("id", preset, "fingerprint", scene="B", values={"warmth": .35})
            self.assertEqual(label, "Silk · Tone Lab 1D")
            self.assertEqual(qc_library.lookup_receipt("id"), preset)
            record = json.loads((Path(target) / "drafts" / "id.kept.json").read_text(encoding="utf-8"))
            self.assertEqual(record["preset"]["fingerprint"], "fingerprint")
            self.assertEqual(record["scene"], "B")
            self.assertEqual(record["knob_values"], {"warmth": .35})

    def test_captures_verify_key_and_model_version_from_the_grid(self):
        cap = qc_library.Asset("capture", V2, "Named", "factory", capture_version=2)
        token = SimpleNamespace(string_value=f"{V2}Named")
        block = SimpleNamespace(hash=14001, params=[SimpleNamespace(param_values=[token])])
        self.assertTrue(qc_library.asset_matches(block, cap))
        block.hash = 14000
        self.assertFalse(qc_library.asset_matches(block, cap))


if __name__ == "__main__":
    unittest.main()
