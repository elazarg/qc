"""The session engine on the simulated unit: A and B on one preset, playing, knobs, listening, saving."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
from pathlib import Path  # noqa: E402
from tempfile import TemporaryDirectory  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

import numpy as np  # noqa: E402
from qc_mcp import leveling  # noqa: E402

import lab.hearing.audio  # noqa: E402
from lab.sound import catalog  # noqa: E402
from lab.sound.chain import Block, Chain, Row  # noqa: E402
from lab.qc.engine import MULTI_OUT, PREVIEW_DB, USB_IN, Engine  # noqa: E402
from lab.qc.sim import MY_PRESETS, TONE_LAB, SimAudio, SimUnit  # noqa: E402
from lab.qc.slots import CACHE_SLOTS, NotOurs, Slots  # noqa: E402
from lab.qc.unit import Mismatch, Moved, Unit  # noqa: E402

from support import EngineCase, engine_on, inputs, note, one_row  # noqa: E402

PLEXI, HIWATT, V30, GREENBACK, DELAY, FUZZ = 1081, 1063, 12001, 12006, 6011, 22


def sound(*models, **settings):
    blocks = []
    for model_id in models:
        block = Block(model_id)
        for name, value in settings.get(str(model_id), {}).items():
            block = block.set_display(catalog.model(model_id).index(name), value)
        blocks.append(block)
    return Chain((Row(tuple(blocks), inlet=1, outlet=MULTI_OUT),))


class Phrase:
    def __init__(self, audio, dry):
        self.started = True

    def start(self):
        return self

    def stop(self):
        self.started = False


class EngineTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.device = SimUnit()
        self.device.save_slot(MY_PRESETS, 5, "Vai for The Love God", [(PLEXI, {}), (V30, {})])
        self.device.recall(MY_PRESETS, 5)
        self.unit = Unit(self.device, settle=.05, sleep=lambda seconds: None)
        self.slots = Slots(self.unit, Path(folder.name))
        audio = SimAudio(self.device)
        self.engine = Engine(self.unit, self.slots, audio)
        opened = self.engine.open()
        self.assertEqual(opened["borrowed"]["name"], "Vai for The Love God")

    def routes(self):
        return self.unit.read().routes

    def test_two_sounds_sit_on_one_preset_and_switching_moves_only_the_input(self):
        a, b = sound(PLEXI, V30), sound(HIWATT, GREENBACK)
        self.assertEqual(self.engine.show(a, b), "pair")
        held = self.unit.read()
        self.assertEqual([block.model for _, _, block in held.blocks], [PLEXI, V30, HIWATT, GREENBACK])
        self.assertEqual(held.rows[2].cells[0].model, HIWATT)
        self.engine.play("a", "guitar")
        self.assertEqual(self.routes()[0], (1, MULTI_OUT))
        self.assertEqual(self.routes()[2][0], 0)
        self.device.log.clear()
        self.engine.play("b", "guitar")
        calls = [call for call, _ in self.device.log]
        self.assertNotIn("add", calls)
        self.assertNotIn("delete", calls)
        self.assertNotIn("recall", calls)
        self.assertEqual((self.routes()[0][0], self.routes()[2][0]), (0, 1))
        # The side not heard is muted, and its level is set before its input is connected.
        held = self.unit.read()
        self.assertEqual(dict(held.rows[0].output)[catalog.LANE_MUTE], 1.)
        lanes = [index for index, (call, _) in enumerate(self.device.log) if call == "lane"]
        routes = [index for index, (call, _) in enumerate(self.device.log) if call == "route"]
        self.assertLess(max(lanes), max(routes))

    def test_performance_assignments_and_the_splitter_survive_audition_and_save(self):
        amp = Block(PLEXI, expression=((1, 1, .2, .8),), stomp=((3, "primary", True),))
        chain = Chain((Row((amp, Block(V30)), inlet=1, outlet=MULTI_OUT, split=(0, 3),
                           splitter=((1, .66),)),))
        self.assertEqual(self.engine.show(chain, sound(HIWATT)), "recall")
        self.engine.play("a", "guitar")
        held = self.unit.read()
        self.assertEqual(held.block(0, 0).expression, amp.expression)
        self.assertEqual(held.block(0, 0).stomp, amp.stomp)
        self.assertAlmostEqual(dict(held.rows[0].splitter)[1], .66)
        slot, saved = self.engine.save(Chain.from_record(held.to_record()), "Performance copy")
        actual, _ = self.engine.read(slot)
        self.assertEqual(actual.block(0, 0).expression, amp.expression)
        self.assertEqual(actual.block(0, 0).stomp, amp.stomp)
        self.assertEqual(actual.rows[0].splitter, saved.rows[0].splitter)

    def test_a_pair_is_built_once(self):
        a, b = sound(PLEXI, V30), sound(HIWATT, GREENBACK)
        self.engine.show(a, b)
        self.device.log.clear()
        self.engine.show(a, b)
        self.assertNotIn("add", [call for call, _ in self.device.log])

    def test_knobs_move_the_values_of_the_side_they_belong_to(self):
        a, b = sound(PLEXI, V30), sound(PLEXI, V30)
        self.engine.show(a, b)
        self.engine.play("b", "guitar")
        moves = self.engine.turn("b", {"drive": .6})
        self.assertTrue(any(move["name"] == "GAIN" for move in moves))
        held = self.unit.read()
        gain = catalog.model(PLEXI).index("GAIN")
        self.assertGreater(held.block(2, 0).get(gain), held.block(0, 0).get(gain))
        self.engine.turn("b", {})
        self.assertAlmostEqual(self.unit.read().block(2, 0).get(gain), held.block(0, 0).get(gain), places=3)

    def test_the_phrase_plays_from_the_computer_and_stops_with_the_session(self):
        import lab.hearing.audio
        original = lab.hearing.audio.Loop
        lab.hearing.audio.Loop = Phrase
        self.addCleanup(setattr, lab.hearing.audio, "Loop", original)
        self.engine.show(sound(PLEXI, V30), None)
        self.engine.play("a", "phrase", phrase=np.zeros((4800, 1), dtype=np.float32))
        self.assertEqual(self.routes()[0][0], USB_IN)
        loop = self.engine.loop
        self.engine.close()
        self.assertFalse(loop.started)
        self.assertEqual(self.unit.position()["position"], 5)  # The player's preset is back.

    def test_a_large_saved_preset_is_compared_by_recalling_it(self):
        big = Chain(tuple(Row((Block(PLEXI),), inlet=1 if r == 0 else 0, outlet=MULTI_OUT if r == 0 else 0)
                          for r in range(3)))
        self.assertEqual(self.engine.show(big, sound(HIWATT, GREENBACK)), "recall")
        self.engine.play("a", "guitar")
        self.assertEqual(len(self.unit.read().blocks), 3)
        self.engine.play("b", "guitar")
        self.assertEqual([block.model for _, _, block in self.unit.read().blocks], [HIWATT, GREENBACK])

    def test_a_round_trip_that_has_changed_is_measured_again_and_the_sound_heard(self):
        dry = (np.sin(np.arange(48000) / 48000 * 2 * np.pi * 196) * .2)[:, None].astype(np.float32)
        self.engine.measure_round_trip()
        self.engine.offset = SimAudio.LATENCY + 480           # measured under another buffer size: 10 ms too long
        found = self.engine.hear(sound(PLEXI, V30), dry)
        self.assertEqual(self.engine.offset, SimAudio.LATENCY)
        self.assertIsNotNone(found.level_db)

    def test_hearing_a_sound_and_saving_it(self):
        dry = (np.sin(np.arange(48000) / 48000 * 2 * np.pi * 196) * .2)[:, None].astype(np.float32)
        self.engine.measure_round_trip()
        self.assertEqual(self.engine.offset, SimAudio.LATENCY)
        found = self.engine.hear(sound(FUZZ, PLEXI, V30, **{str(FUZZ): {"FUZZ": 10}}), dry)
        self.assertIn(found.distortion, ("crunch", "overdriven", "saturated"))
        clean = self.engine.hear(sound(DELAY), dry)
        self.assertEqual(clean.distortion, "clean")
        self.assertNotEqual(clean.tail, "dry")
        slot, stored = self.engine.save(sound(PLEXI, V30), "Stone Pulse")
        self.assertEqual(slot["folder_key"], TONE_LAB)
        self.assertEqual(self.device.slots[(TONE_LAB, slot["position"])].name, "Stone Pulse")
        self.assertEqual(stored.rows[0].route, (1, MULTI_OUT))
        self.engine.close()
        self.assertEqual(self.unit.position()["position"], 5)


    def test_a_sound_is_heard_with_its_knobs_applied_to_every_value_the_unit_states(self):
        dry = (np.sin(np.arange(48000) / 48000 * 2 * np.pi * 196) * .2)[:, None].astype(np.float32)
        design = sound(PLEXI, V30)                       # states no GAIN: the knob moves the unit's own value
        plain = self.engine.hear(design, dry)
        same = self.engine.hear_shaped(design, {}, dry)
        driven = self.engine.hear_shaped(design, {"drive": .8}, dry)
        self.assertAlmostEqual(same.probe["distortion_db"], plain.probe["distortion_db"], places=3)
        self.assertGreater(driven.probe["distortion_db"], plain.probe["distortion_db"] + 1)
        self.assertIsNone(self.engine.shown)
        self.engine.close()
        self.assertEqual(self.unit.position()["position"], 5)

    def test_a_saved_design_keeps_its_knob_moves_on_values_it_left_to_the_unit(self):
        design = sound(PLEXI, V30)                       # states no GAIN
        gain = catalog.model(PLEXI).index("GAIN")
        self.assertIsNone(design.block(0, 0).get(gain))
        plain_slot, plain = self.engine.save(design, "Plain")
        slot, stored = self.engine.save(design, "Driven", {"drive": .6})
        self.assertGreater(stored.block(0, 0).get(gain), plain.block(0, 0).get(gain) + .05)

    def test_the_listening_level_maps_what_is_asked_and_stays_under_the_ceiling(self):
        self.engine.level("a", -12)
        self.engine.listen_at(lambda asked: asked + 30)
        self.assertEqual(self.engine.levels["a"], 6.)
        self.engine.listen_at(None)
        self.assertEqual(self.engine.levels["a"], -12)


    def test_reading_a_preset_and_closing_gives_the_players_preset_back(self):
        self.device.save_slot(MY_PRESETS, 7, "Another", [(HIWATT, {}), (V30, {})])
        self.engine.read({"folder_key": MY_PRESETS, "position": 7, "is_factory": False})
        self.engine.close()
        self.assertEqual(self.unit.position()["position"], 5)

    def test_a_preset_the_player_chose_meanwhile_is_left_alone(self):
        self.device.save_slot(MY_PRESETS, 7, "Another", [(HIWATT, {}), (V30, {})])
        self.device.save_slot(MY_PRESETS, 8, "Chosen", [(PLEXI, {})])
        self.engine.read({"folder_key": MY_PRESETS, "position": 7, "is_factory": False})
        self.device.recall(MY_PRESETS, 8)
        self.engine.close()
        self.assertEqual(self.unit.position()["position"], 8)


    def test_saving_keeps_a_preset_spread_over_rows_routed_as_it_was(self):
        spread = Chain((Row((Block(PLEXI),), inlet=1, outlet=16), Row((Block(V30),), inlet=0, outlet=0),
                        Row((Block(DELAY),), inlet=0, outlet=MULTI_OUT), Row()))
        slot, stored = self.engine.save(spread, "Spread")
        self.assertEqual([row.route for row in stored.rows][:3], [(1, 16), (0, 0), (0, MULTI_OUT)])

    def test_a_knob_moves_every_scene_of_a_scene_switched_sound(self):
        gain = catalog.model(PLEXI).index("GAIN")
        switched = Chain((Row((Block(PLEXI).set(gain, (.3, .3, .8) + (.3,) * 5), Block(V30)), inlet=1,
                              outlet=MULTI_OUT),))
        slot, stored = self.engine.save(switched, "Switched", {"drive": .5})
        held = dict(stored.block(0, 0).params)[gain]
        self.assertGreater(held[0], .35)
        self.assertGreater(held[2], .8)


# ---- Faults the reviews of 2026-11-20 found (docs/CODEBASE_REVIEW.md), each reproduced as it was found ----

class Loops:
    """Stands in for the phrase loop (`lab.hearing.audio.Loop`): keeps every loop started, and can refuse to
    start one."""

    def __init__(self, test):
        self.made, self.fail = [], False
        loops = self

        class Loop:
            def __init__(self, audio, dry):
                self.dry, self.running = dry, False

            def start(self):
                if loops.fail:
                    raise RuntimeError("ASIO error")
                self.running = True
                loops.made.append(self)
                return self

            def stop(self):
                self.running = False

        patch = mock.patch.object(lab.hearing.audio, "Loop", Loop)
        patch.start()
        test.addCleanup(patch.stop)


def three_rows(*models):
    """A sound over three rows: compared by recalling its own preset, not on the shared A/B preset."""
    return Chain(tuple(Row((Block(model),), inlet=1, outlet=MULTI_OUT) for model in models))


def lane(device, row, index):
    return device.working.chains[row].output_control[0].params[index].param_values[0].float_value


class UnsavedEditsAtOpeningTest(unittest.TestCase):
    """(review 2026-11-20)"""

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.device = SimUnit()
        self.device.save_slot(MY_PRESETS, 5, "Mine", [(PLEXI, {}), (V30, {})])
        self.device.recall(MY_PRESETS, 5)
        self.engine = engine_on(self.device, folder.name)

    def test_an_edited_preset_is_never_left_and_its_edit_survives(self):
        gain = catalog.model(PLEXI).index("GAIN")
        self.device.set_param(0, 0, gain, .88)
        self.assertTrue(self.device.dirty)
        self.device.log.clear()
        with self.assertRaisesRegex(RuntimeError, "unsaved changes"):
            self.engine.open()
        self.assertNotIn("recall", [call for call, _ in self.device.log])
        held = self.device.get_current_preset().chains[0].models[0].params[gain].param_values[0].float_value
        self.assertAlmostEqual(held, .88)

    def test_an_unknown_edit_state_counts_as_edits(self):
        self.device.log.clear()
        self.device.read_state = lambda name, **_: None
        with self.assertRaisesRegex(RuntimeError, "unsaved changes"):
            self.engine.open()
        self.assertEqual(self.device.log, [])


class UnsavedEditsTest(EngineCase):
    """(review 2026-11-20)"""

    def test_closing_keeps_an_edit_made_to_a_preset_the_app_read(self):
        self.device.save_slot(MY_PRESETS, 7, "Other", [(PLEXI, {}), (V30, {})])
        self.engine.read({"folder_key": MY_PRESETS, "position": 7, "is_factory": False})
        self.device.set_param(0, 0, 0, .88)
        self.engine.close()
        self.assertEqual(self.unit.position()["position"], 7)
        self.assertTrue(self.device.dirty)

    def test_an_edited_preset_saved_into_the_build_slot_is_not_recalled_away(self):
        self.device.save_slot(MY_PRESETS, 255, "Theirs", [(PLEXI, {})])
        self.device.player_selects(MY_PRESETS, 255)
        self.device.set_param(0, 0, 0, .88)
        with self.assertRaisesRegex(RuntimeError, "unsaved"):
            self.engine.show(one_row(HIWATT, V30), None)
        self.assertEqual(self.unit.position()["position"], 255)
        self.assertTrue(self.device.dirty)


class LevelsTest(EngineCase):
    """A level before an input, and a sound's own level kept. (review 2026-11-20)"""

    def test_a_level_the_unit_ignores_leaves_the_input_disconnected(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        setting = self.device.set_lane_param

        def ignores_volume(row, field, index, value, column=0):
            if index != catalog.LANE_VOLUME:
                setting(row, field, index, value, column)

        self.device.set_lane_param = ignores_volume
        self.engine.levels["b"] = -35.
        with self.assertRaises(Mismatch):
            self.engine.play("b", "guitar")
        self.assertEqual([row.in_portid for row in self.device.working.chains], [0, 0, 0, 0])

    def test_a_sound_with_its_own_output_gain_keeps_it_under_the_audition_trim(self):
        quiet = one_row(HIWATT, V30, output={catalog.LANE_VOLUME: leveling.norm_of(-18.)})
        self.engine.show(one_row(PLEXI, V30), quiet)
        self.engine.play("b", "guitar")
        self.assertAlmostEqual(leveling.db_of(lane(self.device, 2, catalog.LANE_VOLUME)), -18. + PREVIEW_DB, places=1)

    def test_a_lane_its_author_muted_stays_muted_when_played(self):
        muted = Chain((Row((Block(HIWATT),), inlet=1, outlet=MULTI_OUT, output={catalog.LANE_MUTE: 1.}),
                       Row((Block(V30),), inlet=1, outlet=MULTI_OUT)))
        self.engine.show(one_row(PLEXI, V30), muted)
        self.engine.play("b", "guitar")
        self.assertEqual(lane(self.device, 2, catalog.LANE_MUTE), 1.)
        self.assertEqual(lane(self.device, 3, catalog.LANE_MUTE), 0.)

    def test_saving_keeps_the_sound_s_own_level(self):
        quieter = one_row(HIWATT, V30, output={catalog.LANE_VOLUME: leveling.norm_of(-6.)})
        slot, stored = self.engine.save(quieter, "Quieter")
        self.assertAlmostEqual(leveling.db_of(dict(stored.rows[0].output)[catalog.LANE_VOLUME]), -6., places=1)


class RowsTest(EngineCase):
    """Sounds over more than one row. (review 2026-11-20)"""

    def test_a_sound_on_two_joined_rows_is_heard_whole_in_the_pair(self):
        joined = Chain((Row((Block(PLEXI),), inlet=1, outlet=16), Row((Block(V30),), inlet=0, outlet=MULTI_OUT)))
        self.assertEqual(self.engine.show(one_row(HIWATT, GREENBACK), joined), "pair")
        self.engine.play("b", "guitar")
        routes = [(row.in_portid, row.out_portid) for row in self.device.working.chains]
        self.assertEqual(routes[2:], [(1, 16), (0, MULTI_OUT)])
        self.assertEqual(routes[0][0], 0)                    # A is not fed

    def test_turning_a_while_b_is_loaded_leaves_b_alone_and_goes_on_a_when_a_is_heard(self):
        a, b = three_rows(PLEXI, V30, GREENBACK), three_rows(HIWATT, V30, GREENBACK)
        self.assertEqual(self.engine.show(a, b), "recall")
        self.engine.play("b", "guitar")
        before = self.unit.read()
        self.engine.turn("a", {"drive": .5})
        after = self.unit.read()
        self.assertEqual([[cell.values if cell else None for cell in row.cells] for row in after.rows],
                         [[cell.values if cell else None for cell in row.cells] for row in before.rows])
        self.engine.play("a", "guitar")
        gain = catalog.model(PLEXI).index("GAIN")
        base = self.engine.bases["a"].block(0, 0).values.get(gain)
        self.assertNotEqual(self.unit.read().block(0, 0).values.get(gain), base)


class MovedAwayTest(EngineCase):
    """The player chooses another preset on the unit while the app works. (review 2026-11-20)"""

    def switch_after_the_first(self, call):
        """Have the player select a preset of their own right after the first `call` to the device."""
        self.device.save_slot(MY_PRESETS, 9, "Theirs", [(PLEXI, {})])
        write, calls = getattr(self.device, call), []

        def write_then_switch(*args, **options):
            write(*args, **options)
            calls.append(args)
            if len(calls) == 1:
                self.device.player_selects(MY_PRESETS, 9)

        setattr(self.device, call, write_then_switch)
        return calls

    def test_choosing_another_preset_on_the_unit_stops_the_phrase_and_scene_changes_leave_it_alone(self):
        loops = Loops(self)
        self.device.save_slot(MY_PRESETS, 9, "Theirs", [(PLEXI, {})], routes=((12, 19),))
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        self.engine.play("a", "phrase", note())
        self.device.player_selects(MY_PRESETS, 9)
        self.assertEqual(self.engine.watch(), {"moved": True})
        self.assertFalse(loops.made[0].running)
        self.engine.scene(3)
        self.assertEqual(self.device.scene, 0)

    def test_a_switch_between_two_route_writes_writes_nothing_to_the_new_preset(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        calls = self.switch_after_the_first("set_routing")
        with self.assertRaises(Moved):
            self.engine.play("b", "guitar")
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.device.dirty)

    def test_a_switch_after_the_first_level_write_writes_nothing_more(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        calls = self.switch_after_the_first("set_lane_param")
        with self.assertRaises(Moved):
            self.engine.play("b", "guitar")
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.device.dirty)

    def test_closing_leaves_a_saved_preset_the_player_chose_and_edited(self):
        slot, _ = self.engine.save(one_row(HIWATT, V30), "Kept")
        self.device.player_selects(TONE_LAB, slot["position"])
        self.device.set_param(0, 0, 0, .77)
        self.engine.close()
        self.assertEqual(self.unit.position()["position"], slot["position"])
        self.assertTrue(self.device.dirty)


class PhraseLoopTest(EngineCase):
    """(review 2026-11-20)"""

    def test_a_new_phrase_replaces_the_one_looping(self):
        loops = Loops(self)
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        first = note()
        self.engine.play("a", "phrase", first)
        self.engine.play("b", "phrase", first.copy())             # the same take: the loop goes on
        self.assertEqual(len(loops.made), 1)
        self.engine.play("b", "phrase", first[:12000].copy())     # another part of it: a new loop
        self.assertEqual(len(loops.made), 2)
        self.assertFalse(loops.made[0].running)

    def test_a_failed_new_loop_leaves_nothing_cached_and_the_old_take_starts_again(self):
        loops = Loops(self)
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        old = note()
        self.engine.play("a", "phrase", old)
        loops.fail = True
        with self.assertRaisesRegex(RuntimeError, "ASIO"):
            self.engine.play("a", "phrase", old[:12000].copy())
        loops.fail = False
        self.engine.play("a", "phrase", old.copy())
        self.assertEqual(len(loops.made), 2)

    def test_audio_that_will_not_start_leaves_nothing_connected(self):
        loops = Loops(self)
        loops.fail = True
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        with self.assertRaisesRegex(RuntimeError, "ASIO"):
            self.engine.play("a", "phrase", note())
        self.assertEqual(inputs(self.device), [])


class FailedOperationsTest(EngineCase):
    """What failed half way leaves nothing connected. (review 2026-11-20)"""

    def test_a_save_refused_at_the_last_check_leaves_nothing_connected(self):
        build = self.engine.rig.build

        def build_then_someone_saves(chain, **options):
            found = build(chain, **options)
            self.device.save_slot(TONE_LAB, 0, "Theirs", [(PLEXI, {})])     # the destination, taken meanwhile
            return found

        self.engine.rig.build = build_then_someone_saves
        with self.assertRaises(NotOurs):
            self.engine.save(one_row(HIWATT, V30), "Mine too")
        self.assertEqual(inputs(self.device), [])
        self.assertEqual(self.device.slots[(TONE_LAB, 0)].name, "Theirs")

    def test_a_save_written_but_not_read_back_is_said_and_leaves_nothing_connected(self):
        recall, failed = self.unit.recall, []

        def readback_fails_once(slot):
            if slot.get("folder_key") == TONE_LAB and slot.get("position") == 0 and not failed:
                failed.append(slot)
                raise RuntimeError("The Quad Cortex did not return its preset.")
            return recall(slot)

        self.unit.recall = readback_fails_once
        with self.assertRaisesRegex(RuntimeError, "may hold"):
            self.engine.save(one_row(HIWATT, V30), "Half saved")
        self.assertEqual(len(failed), 1)
        self.assertEqual(self.unit.position()["position"], 0)
        self.assertEqual(inputs(self.device), [])

    def test_a_record_that_cannot_read_the_channels_connects_nothing(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        self.device.read_message = lambda name, **_: None
        with self.assertRaisesRegex(RuntimeError, "Dry/Wet"):
            self.engine.record(1., "b")
        self.assertEqual(inputs(self.device), [])
        self.assertIsNone(self.engine.mode)

    def test_a_scene_refused_while_recording_leaves_nothing_connected(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        self.engine.chosen_scene = 3

        def refuse(scene):
            raise RuntimeError("scene refused")

        self.unit.select_scene = refuse
        with self.assertRaisesRegex(RuntimeError, "scene refused"):
            self.engine.record(1., "b")
        self.assertEqual(inputs(self.device), [])

    def test_the_wet_take_is_the_sound_with_its_knobs(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        turned = []
        turn = self.engine.turn
        self.engine.turn = lambda side, values, scene=None: turned.append((side, values)) or turn(side, values, scene)
        self.engine.record(1., "b", {"drive": .5})
        self.assertEqual(turned, [("b", {"drive": .5})])
        self.assertEqual(inputs(self.device), [])

    def test_after_a_failed_listen_playing_makes_a_and_b_ready_again(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        pair_slot = self.engine.shown[1]
        dry = note()
        self.engine.offset = SimAudio.LATENCY
        self.engine.audio.reamp = lambda dry, **options: np.zeros((len(dry) + 48000, 2), dtype=np.float32)
        with self.assertRaisesRegex(RuntimeError, "Nothing came back"):
            self.engine.hear(one_row(HIWATT), dry)
        self.engine.play("b", "guitar")
        self.assertEqual(self.unit.position()["position"], pair_slot["position"])
        self.assertEqual(self.device.working.chains[2].in_portid, 1)


class PlayersPresetsTest(EngineCase):
    """(review 2026-11-20)"""

    def test_a_preset_saved_into_the_cache_after_opening_is_not_built_over(self):
        self.device.save_slot(TONE_LAB, min(CACHE_SLOTS), "Mine in the cache", [(PLEXI, {})])
        self.engine.show(one_row(HIWATT, V30), None)
        self.assertEqual(self.device.slots[(TONE_LAB, min(CACHE_SLOTS))].name, "Mine in the cache")


if __name__ == "__main__":
    unittest.main()
