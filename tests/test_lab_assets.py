"""The player's IRs and captures: read from the unit, remembered, and put into a sound in place of its cab or amp."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lab.qc import assets
from lab.sound import catalog
from lab.qc.assets import Asset
from lab.sound.chain import Block, Chain, Row, agrees, differences
from lab.qc.host import Host
from lab.qc.sim import SIM_CAPTURES, SIM_IRS, SimUnit
from lab.qc.unit import Unit

PLEXI, RECTIFIER, V30, GREENBACK, STEREO_CAB, DELAY = 1081, 1018, 12001, 12006, 32000, 6011
IR = Asset("ir", SIM_IRS[0][0], SIM_IRS[0][1])
V1 = Asset("capture", SIM_CAPTURES[0][0], SIM_CAPTURES[0][1], "factory", 1)
MINE = Asset("capture", SIM_CAPTURES[2][0], SIM_CAPTURES[2][1], "user", None)


def engine_on(device):
    return SimpleNamespace(unit=SimpleNamespace(device=device))


def one_path(*models, inlet=1, outlet=19):
    return Chain((Row(tuple(Block(model) for model in models), inlet=inlet, outlet=outlet),))


class ListingTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(TemporaryDirectory()))

    def test_the_unit_lists_its_irs_and_captures_with_their_keys_versions_and_sources(self):
        found = assets.from_listing(SimUnit().list_directory())
        self.assertEqual([asset for asset in found if asset.kind == "ir"],
                         sorted([Asset("ir", key, name) for key, name in SIM_IRS], key=lambda a: a.name.casefold()))
        captures = {asset.name: asset for asset in found if asset.kind == "capture"}
        self.assertEqual(captures["Brit 2555 Lead 16"], V1)
        self.assertEqual(captures["Bogna Fish 8"].capture_version, 2)
        self.assertEqual(captures["Dark Jazz"], MINE)               # the unit does not say a player's version
        self.assertEqual(found[0].source, "user")                  # the player's own come first
        self.assertNotIn("Factory 412 Greenback 57", [asset.name for asset in found])  # no key, cannot be bound

    def test_links_without_names_and_downloads_not_on_the_unit_are_left_out(self):
        listing = {"irs": [{"key": "local_ir_root", "files": [{"name": "A", "key": "CIR_1"},
                                                                {"name": "", "key": "CIR_2"},
                                                                {"name": "Bad", "key": "not a key"}]}],
                   "captures": [{"key": "cloud-2-1", "is_downloads": True,
                                 "files": [{"name": "Cloud", "key": "d" * 64}]}]}
        self.assertEqual(assets.from_listing(listing), [Asset("ir", "CIR_1", "A")])

    def test_the_simulated_unit_is_read_and_remembered(self):
        sim = SimUnit()
        key = sim.add_ir("Fredman Mix")
        records = assets.listing(engine_on(sim), folder=self.folder)
        self.assertEqual(json.loads(json.dumps(records)), records)
        self.assertIn({"kind": "ir", "key": key, "name": "Fredman Mix", "source": "user", "capture_version": None},
                      records)
        self.assertEqual(assets.cached(self.folder), [Asset.from_record(record) for record in records])
        self.assertEqual(json.loads((self.folder / assets.FILE).read_text(encoding="utf-8"))["firmware"], "4.1.1")

    def test_a_listing_without_the_libraries_keeps_what_was_read_before(self):
        before = assets.listing(engine_on(SimUnit()), folder=self.folder)
        broken = SimpleNamespace(list_directory=lambda: {"presets": [], "irs": [], "captures": []}, firmware="4.1.1")
        with self.assertRaisesRegex(RuntimeError, "did not list"):
            assets.listing(engine_on(broken), folder=self.folder)
        self.assertEqual([item.to_record() for item in assets.cached(self.folder)], before)

    def test_a_stopped_read_does_not_ask_the_unit(self):
        stop, asked = Event(), []
        stop.set()
        with self.assertRaises(RuntimeError):
            assets.listing(engine_on(SimpleNamespace(list_directory=lambda: asked.append(1))), stop=stop,
                           folder=self.folder)
        self.assertEqual(asked, [])

    def test_nothing_read_or_a_damaged_file_reads_as_not_read(self):
        self.assertIsNone(assets.cached(self.folder))
        (self.folder / assets.FILE).write_text('{"assets": [{"kind": 1}]}', encoding="utf-8")
        self.assertIsNone(assets.cached(self.folder))

    def test_the_host_runs_it_where_the_unit_is_held(self):
        with patch.dict(os.environ, {"TONE_LAB_HOME": str(self.folder / "home")}):
            host = Host(self.folder / "engine", simulated=True, local=True)
            try:
                self.assertEqual(host.ready.result(10), "ready")
                records = host.call("lab.qc.assets.listing").result(10)
                self.assertEqual(len(records), len(SIM_IRS) + len(SIM_CAPTURES))
                self.assertEqual(len(assets.cached()), len(records))
            finally:
                host.close()


class BlockTest(unittest.TestCase):
    def test_an_ir_is_bound_by_its_path_and_name(self):
        block = assets.block_for(IR)
        self.assertEqual((block.model, block.get(2), block.get(22)), (catalog.IR_LOADER, IR.key, IR.name))
        self.assertEqual(assets.block_for(IR, stereo=True).model, assets.IR_STEREO)
        self.assertEqual(assets.bound(block), IR)

    def test_a_capture_is_bound_by_its_key_and_name_on_the_block_of_its_version(self):
        block = assets.block_for(V1)
        self.assertEqual((block.model, block.get(5)), (14000, V1.key + V1.name))
        self.assertEqual(assets.bound(block, [V1]), V1)
        self.assertEqual(assets.block_for(MINE, version=2).model, 14001)
        with self.assertRaisesRegex(ValueError, "V1 or a V2"):
            assets.block_for(MINE)

    def test_an_asset_without_a_key_cannot_be_bound(self):
        with self.assertRaisesRegex(ValueError, "no key"):
            assets.block_for(Asset("ir", "", "Factory 412"))


class SubstituteTest(unittest.TestCase):
    def test_an_ir_takes_the_cabs_place_and_switching(self):
        chain = Chain((Row((Block(PLEXI), Block(V30, bypass=(False, True) + (False,) * 6), Block(DELAY)),
                           inlet=1, outlet=19),))
        changed, said = assets.with_ir(chain, IR)
        self.assertEqual(assets.bound(changed.block(0, 1)), IR)
        self.assertEqual(changed.block(0, 1).bypass, chain.block(0, 1).bypass)
        self.assertEqual((changed.block(0, 0), changed.block(0, 2)), (chain.block(0, 0), chain.block(0, 2)))
        self.assertEqual(changed.routes, chain.routes)
        self.assertIn(IR.name, said)
        self.assertIn(Block(V30).name, said)

    def test_both_paths_of_a_split_get_the_ir_and_a_stereo_cab_a_stereo_loader(self):
        chain = Chain((Row((Block(PLEXI), Block(V30)), inlet=1, outlet=19, split=(0, 3)),
                       Row((Block(RECTIFIER), Block(STEREO_CAB)))))
        changed, said = assets.with_ir(chain, IR)
        self.assertEqual([(r, c, b.model) for r, c, b in changed.blocks if assets.bound(b)],
                         [(0, 1, catalog.IR_LOADER), (1, 1, assets.IR_STEREO)])
        self.assertEqual(changed.rows[0].split, (0, 3))
        self.assertIn("both cabs", said)

    def test_an_ir_needs_a_cab_to_replace(self):
        with self.assertRaisesRegex(ValueError, "no cab"):
            assets.with_ir(one_path(PLEXI), IR)
        with self.assertRaisesRegex(ValueError, "IR"):
            assets.with_ir(one_path(PLEXI, V30), V1)

    def test_an_amp_only_capture_takes_the_amps_place_and_keeps_the_cab(self):
        changed, said = assets.with_capture(one_path(PLEXI, V30), V1, has_cab=False)
        self.assertEqual(assets.bound(changed.block(0, 0), [V1]), V1)
        self.assertEqual(changed.block(0, 1).model, V30)
        self.assertEqual(said, f"Replaced the amp ({Block(PLEXI).name}) with the capture {V1.name}.")

    def test_a_capture_with_its_cab_takes_the_cabs_place_too(self):
        changed, said = assets.with_capture(one_path(PLEXI, V30, DELAY), V1, has_cab=True)
        self.assertEqual([b.model for _, _, b in changed.blocks], [14000, DELAY])
        self.assertEqual(changed.block(0, 2).model, DELAY)                 # every other block keeps its place
        self.assertIn("and the cab", said)

    def test_what_a_capture_cannot_replace_is_said(self):
        with self.assertRaisesRegex(ValueError, "needs a cab"):
            assets.with_capture(one_path(PLEXI), V1, has_cab=False)
        with self.assertRaisesRegex(ValueError, "two amps"):
            assets.with_capture(Chain((Row((Block(PLEXI), Block(V30)), split=(0, 3)),
                                       Row((Block(RECTIFIER), Block(V30))))), V1, has_cab=False)
        with self.assertRaisesRegex(ValueError, "no amp"):
            assets.with_capture(one_path(V30), V1, has_cab=False)
        with self.assertRaisesRegex(ValueError, "includes its cab"):
            assets.with_capture(one_path(PLEXI, V30), V1, has_cab=None)
        with self.assertRaisesRegex(ValueError, "V1 or a V2"):
            assets.with_capture(one_path(PLEXI, V30), MINE, has_cab=False)
        changed, _ = assets.with_capture(one_path(PLEXI, V30), MINE, has_cab=False, version=2)
        self.assertEqual(changed.block(0, 0).model, 14001)


class OnTheUnitTest(unittest.TestCase):
    def setUp(self):
        self.sim = SimUnit()
        self.unit = Unit(self.sim, settle=.05, sleep=lambda seconds: None)

    def test_an_ir_is_built_and_confirmed(self):
        wanted, _ = assets.with_ir(one_path(PLEXI, V30), IR)
        held = self.unit.apply(wanted)
        self.assertEqual(assets.bound(held.block(0, 1)), IR)
        writes = [args for call, args in self.sim.log if call == "param" and args[:2] == (0, 1)]
        self.assertEqual([args[2] for args in writes], [2, 22])             # the path, then its name
        self.assertEqual(differences(self.unit.read(), wanted), [])

    def test_a_capture_is_confirmed_by_its_key_when_the_unit_names_it_its_own_way(self):
        wanted = Chain((Row((Block(14000, ((5, V1.key + "Something else"),)), Block(V30)), inlet=1, outlet=19),))
        held = self.unit.apply(wanted)
        self.assertEqual(held.block(0, 0).get(5), V1.key + V1.name)         # the unit's own name for it
        self.assertEqual(differences(held, wanted), [])
        self.assertEqual(self.unit.apply(wanted), held)                    # and nothing is written again
        self.assertEqual(differences(held, wanted.with_block(0, 0, assets.block_for(MINE, version=1)))[:1],
                         [f"row 1 column 1 {Block(14000).name} parameter 5 is {V1.key + V1.name}, "
                          f"wanted {MINE.key + MINE.name}"])

    def test_the_library_calls_bind_the_same_way(self):
        self.sim.set_ir(0, 2, IR.key, IR.name)
        self.sim.set_capture(0, 3, MINE.key, MINE.name, version="v2")
        held = self.unit.read()
        self.assertEqual(assets.bound(held.block(0, 2)), IR)
        self.assertEqual(assets.bound(held.block(0, 3)), Asset("capture", MINE.key, MINE.name, "user", 2))

    def test_an_ir_name_the_unit_shows_differently_is_not_a_difference(self):
        built = one_path(PLEXI).with_block(0, 1, Block(catalog.IR_LOADER, ((2, IR.key), (22, "Its own name"))))
        self.assertEqual(differences(built, one_path(PLEXI).with_block(0, 1, assets.block_for(IR))), [])
        other = Block(catalog.IR_LOADER, ((2, SIM_IRS[1][0]), (22, IR.name)))
        self.assertEqual(len(differences(one_path(PLEXI).with_block(0, 1, other),
                                         one_path(PLEXI).with_block(0, 1, assets.block_for(IR)))), 1)

    def test_only_a_captures_key_decides(self):
        self.assertTrue(agrees(14000, 5, V1.key + "x", V1.key + "y"))
        self.assertFalse(agrees(14000, 5, MINE.key + "x", V1.key + "x"))
        self.assertFalse(agrees(catalog.IR_LOADER, 2, "CIR_1", "CIR_2"))
        self.assertTrue(agrees(PLEXI, 0, .5, .502))


if __name__ == "__main__":
    unittest.main()
