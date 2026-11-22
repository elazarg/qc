"""The rewrite's core: chains, the simulated unit, the guarded unit, slots and the cache."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
from pathlib import Path  # noqa: E402
from tempfile import TemporaryDirectory  # noqa: E402
import unittest  # noqa: E402

from qc_mcp import leveling  # noqa: E402

from lab.sound import catalog  # noqa: E402
from lab.sound.chain import Block, Chain, Row, differences, plan_structure  # noqa: E402
from lab.qc.rig import Rig  # noqa: E402
from lab.qc.sim import FACTORY, MY_PRESETS, TONE_LAB, SimUnit  # noqa: E402
from lab.qc.slots import BUILD, CACHE_SLOTS, NotOurs, Slots  # noqa: E402
from lab.qc.unit import Moved, Refused  # noqa: E402

from support import engine_on, fast, one_row  # noqa: E402

RECTIFIER, PLEXI, V30, GREENBACK, EQ, DELAY, GOVERNOR = 1018, 1081, 12001, 12006, 4005, 6011, 11


def norm(model_id, name, display):
    found = catalog.model(model_id)
    return found.index(name), catalog.to_norm(model_id, found.index(name), display)


def lead_and_rhythm():
    """Two paths and two scenes: a Rectifier beside a Plexi, the delay on only in the lead scene."""
    gain_index, rhythm = norm(RECTIFIER, "GAIN", 4.)
    _, lead = norm(RECTIFIER, "GAIN", 6.5)
    rectifier = Block(RECTIFIER, ((gain_index, (rhythm, lead) + (rhythm,) * 6),))
    delay = Block(DELAY, bypass=(True, False) + (True,) * 6)
    upper = Row((Block(GOVERNOR), rectifier, Block(V30), None, Block(EQ), delay),
                inlet=12, outlet=14, split=(1, 3), mixer=((2, catalog.to_norm(catalog.MIXER, 2, -3.)),))
    lower = Row((None, Block(PLEXI), Block(GREENBACK)))
    return Chain((upper, lower), ("Rhythm", "Lead"))


class Player(SimUnit):
    """A unit whose player selects another preset while write number `leave_after` is under way: that write
    is already sent and lands on the preset they chose. `stray` are the writes that came after it."""

    def __init__(self, leave_after=None, **options):
        super().__init__(**options)
        self.leave_after, self.stray = leave_after, []

    def _write(self, call, *args):
        if self.leave_after is not None and len(self.log) == self.leave_after:
            self.player_selects(FACTORY, 0)
        elif self.leave_after is not None and len(self.log) > self.leave_after and call not in ("recall",):
            self.stray.append(call)
        super()._write(call, *args)


class ChainTest(unittest.TestCase):
    def test_scene_assignments_survive_even_when_every_scene_has_the_same_value(self):
        unit = fast(SimUnit())
        unit.device.recall(**BUILD)
        block = Block(PLEXI, ((0, (.5,) * 8),), (False,) * 8)
        wanted = Chain((Row((block,), inlet=1, outlet=19),))
        held = unit.apply(wanted)
        self.assertEqual(held.block(0, 0).get(0, 7), .5)
        self.assertEqual(held.block(0, 0).values[0], (.5,) * 8)
        self.assertEqual(held.block(0, 0).bypass, (False,) * 8)

    def test_a_chain_read_from_the_unit_is_the_chain_that_was_put_there(self):
        unit = fast(SimUnit())
        unit.device.recall(**BUILD)
        held = unit.apply(lead_and_rhythm())
        self.assertEqual([b.model for _, _, b in held.blocks], [GOVERNOR, RECTIFIER, V30, EQ, DELAY, PLEXI, GREENBACK])
        self.assertEqual(held.rows[0].split, (1, 3))
        self.assertEqual(held.labels[:2], ("Rhythm", "Lead"))
        gain = held.block(0, 1).values[catalog.model(RECTIFIER).index("GAIN")]
        self.assertEqual(len(gain), 8)
        self.assertAlmostEqual(catalog.to_display(RECTIFIER, 0, gain[1]), 6.5, places=1)
        self.assertEqual(held.block(0, 5).bypass[:2], (True, False))
        self.assertEqual(differences(held, lead_and_rhythm()), [])
        # The chain survives being written down and read again.
        self.assertEqual(Chain.from_record(held.to_record()).digest(), held.digest())

    def test_the_digest_follows_the_sound_and_not_the_routing_or_names(self):
        chain = lead_and_rhythm()
        self.assertEqual(chain.digest(), chain.with_routes([(1, 19)] * 4).digest())
        self.assertNotEqual(chain.digest(), chain.with_block(0, 1, chain.block(0, 1).set_display(0, 8.)).digest())

    def test_inputs_are_disconnected_before_blocks_change_and_connected_last(self):
        live = Chain((Row((Block(PLEXI),), inlet=1, outlet=19),))
        ops = plan_structure(live, lead_and_rhythm())
        self.assertEqual(ops[0].kind, "route")
        self.assertEqual(ops[0].args, (0, 0, 19))
        unit = fast(SimUnit())
        unit.device.recall(**BUILD)
        unit.apply(live)
        unit.device.log.clear()
        unit.apply(lead_and_rhythm().with_routes([(1, 19), (0, 0), (0, 0), (0, 0)]))
        calls = [call for call, _ in unit.device.log]
        self.assertEqual(calls[0], "route")
        self.assertEqual(calls[-1], "route")
        self.assertLess(calls.index("delete"), calls.index("add"))


class UnitTest(unittest.TestCase):
    def test_a_block_the_unit_leaves_out_is_named(self):
        unit = fast(SimUnit(refuse=lambda grid, model_id: model_id == PLEXI))
        unit.device.recall(**BUILD)
        with self.assertRaisesRegex(Refused, "no processing left for Brit Plexi 50 Normal"):
            unit.apply(lead_and_rhythm())

    def test_nothing_lands_on_a_preset_the_player_switched_to(self):
        whole = SimUnit()
        fast(whole).device.recall(**BUILD)
        count = len(whole.log)
        unit = fast(whole)
        unit.slot = BUILD
        unit.apply(lead_and_rhythm())
        writes = len(whole.log) - count
        self.assertGreater(writes, 20)
        for after in range(count + 1, count + writes):
            device = Player(leave_after=after)
            unit = fast(device)
            device.recall(**BUILD)
            unit.slot = BUILD
            with self.assertRaises(Moved):
                unit.apply(lead_and_rhythm())
            # The unit is asked which preset is selected before every write, so the write under way when the
            # player switched is the last one: wherever in the build they switch, nothing more is written.
            self.assertEqual(device.stray, [], after)

    def test_a_row_is_never_turned_up_past_the_ceiling(self):
        unit = fast(SimUnit())
        unit.device.recall(**BUILD)
        loud = Chain((Row((Block(PLEXI),), inlet=12, outlet=14,
                          output=((catalog.LANE_VOLUME, leveling.norm_of(12.)),)),))
        held = unit.apply(loud)
        self.assertAlmostEqual(held.rows[0].output[catalog.LANE_VOLUME][1], leveling.norm_of(6.), places=4)


class SlotsTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.device = SimUnit()
        self.unit = fast(self.device)
        self.slots = Slots(self.unit, self.folder)
        self.rig = Rig(self.unit, self.slots)

    def test_a_chain_is_built_once_and_recalled_after(self):
        chain = lead_and_rhythm()
        with self.slots.borrowed():
            slot, held = self.rig.ready(chain)
            self.assertIn(slot["position"], CACHE_SLOTS)
            self.assertEqual(slot["folder_key"], TONE_LAB)
            built = [call for call, _ in self.device.log].count("add")
            self.device.log.clear()
            again, held = self.rig.ready(chain)
        self.assertEqual(again, slot)
        self.assertEqual([call for call, _ in self.device.log if call == "add"], [])
        self.assertEqual(built, 7)
        # Stored silent: nothing is connected when it is recalled.
        self.assertEqual([row.inlet for row in held.rows], [0, 0, 0, 0])
        # And the player's preset is selected again.
        self.assertEqual(self.unit.position()["folder_key"], FACTORY)

    def test_connecting_sets_the_output_before_the_input(self):
        with self.slots.borrowed():
            slot, held = self.rig.ready(lead_and_rhythm())
            self.device.log.clear()
            self.rig.connect(held, [(1, 19), (0, 0), (0, 0), (0, 0)],
                             output={0: ((catalog.LANE_MUTE, 1.),)})
        calls = [(call, args) for call, args in self.device.log if call in ("lane", "route")]
        self.assertEqual(calls[0][0], "lane")
        self.assertEqual(calls[-1], ("route", (0, 1, 19)))

    def test_a_slot_holding_someone_elses_preset_is_never_written(self):
        self.device.save_slot(TONE_LAB, max(CACHE_SLOTS), "My Jam", [(PLEXI, {})])
        with self.assertRaises(NotOurs):
            self.slots.claim(self.slots.tone_lab_slot(max(CACHE_SLOTS)), "TL 123")
        self.device.save_slot(TONE_LAB, 0, "Europa Bloom", [(PLEXI, {})])
        self.assertEqual(self.slots.free_for_saving()["position"], 1)
        with self.assertRaises(NotOurs):
            self.slots.claim({"folder_key": MY_PRESETS, "position": 254}, "anything")

    def test_after_a_crash_the_borrowed_preset_is_selected_again(self):
        self.device.save_slot(MY_PRESETS, 5, "Vai for The Love God", [(PLEXI, {})])
        self.device.recall(MY_PRESETS, 5)
        self.slots.note_borrowed()
        self.rig.ready(lead_and_rhythm())   # ... and the app dies here.
        fresh = Slots(fast(self.device), self.folder)
        record = fresh.recover()
        self.assertEqual(record["name"], "Vai for The Love God")
        self.assertEqual(self.device.position["position"], 5)
        self.assertIsNone(fresh.recover())  # Done once.

    def test_unsaved_changes_on_the_players_preset_are_not_discarded(self):
        self.device.dirty = True
        with self.assertRaisesRegex(RuntimeError, "unsaved changes"):
            with self.slots.borrowed():
                pass



class DirectoryTest(unittest.TestCase):
    def test_a_slot_missing_from_the_listing_is_never_saved_over(self):
        """(review 2026-11-20)"""
        device = SimUnit()
        device.save_slot(TONE_LAB, 0, "Already here", [(PLEXI, {})])
        listing = device.list_directory

        def incomplete(**options):
            found = listing(**options)
            for folder in found["presets"]:
                if folder["key"] == TONE_LAB:
                    folder["files"] = [row for row in folder["files"] if row["index"] != 0]
            return found

        device.list_directory = incomplete
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        engine = engine_on(device, folder.name)
        engine.open()
        with self.assertRaisesRegex(RuntimeError, "incompletely"):
            engine.save(one_row(1063, V30), "Mine")
        self.assertEqual(device.slots[(TONE_LAB, 0)].name, "Already here")
        self.assertNotIn("Mine", [grid.name for grid in device.slots.values()])


class RoutingTest(unittest.TestCase):
    def test_a_preset_spread_over_rows_keeps_its_inner_routing_when_fed_from_elsewhere(self):
        from lab.sound.chain import routed
        # As the player's Pink Floyd Time: row 1 from the guitar into row 2 inside the preset, rows 3 and 4 out.
        floyd = Chain((Row((Block(PLEXI),), inlet=1, outlet=16), Row((Block(PLEXI),), inlet=1, outlet=0),
                       Row((Block(DELAY),), inlet=0, outlet=19), Row((Block(DELAY),))))
        self.assertEqual(routed(floyd, 12, 14).routes, [(12, 16), (12, 0), (0, 14), (0, 0)])
        bare = Chain((Row((Block(PLEXI),)),))
        self.assertEqual(routed(bare, 12, 14).routes[0], (12, 14))


if __name__ == "__main__":
    unittest.main()
