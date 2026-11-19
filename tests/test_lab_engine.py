"""The session engine on the simulated unit: A and B on one preset, playing, knobs, listening, saving."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from lab import catalog
from lab.chain import Block, Chain, Row
from lab.engine import MULTI_OUT, USB_IN, Engine, pair
from lab.sim import FACTORY, MY_PRESETS, TONE_LAB, SimAudio, SimUnit
from lab.slots import Slots
from lab.unit import Unit

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
        import lab.audio
        original = lab.audio.Loop
        lab.audio.Loop = Phrase
        self.addCleanup(setattr, lab.audio, "Loop", original)
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

    def test_hearing_a_sound_and_saving_it(self):
        dry = (np.sin(np.arange(48000) / 48000 * 2 * np.pi * 196) * .2)[:, None].astype(np.float32)
        self.engine.measure_round_trip()
        self.assertEqual(self.engine.offset, SimAudio.LATENCY)
        found = self.engine.hear(sound(FUZZ, PLEXI, V30, **{str(FUZZ): {"FUZZ": 10}}), dry)
        self.assertIn(found.drive, ("overdriven", "saturated"))
        clean = self.engine.hear(sound(DELAY), dry)
        self.assertEqual(clean.drive, "clean")
        self.assertNotEqual(clean.space, "dry")
        slot, stored = self.engine.save(sound(PLEXI, V30), "Stone Pulse")
        self.assertEqual(slot["folder_key"], TONE_LAB)
        self.assertEqual(self.device.slots[(TONE_LAB, slot["position"])].name, "Stone Pulse")
        self.assertEqual(stored.rows[0].route, (1, MULTI_OUT))
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


if __name__ == "__main__":
    unittest.main()
