"""The faults found by the independent re-review of 20 November 2026 (docs/CODEBASE_REVIEW.md, "Independent
re-review"), each reproduced as it was found, on the simulated unit."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest

import numpy as np
from qc_mcp import leveling

sys.path.insert(0, str(Path(__file__).parent))

from lab.desk.studio import Studio  # noqa: E402
from lab.qc.engine import MULTI_OUT, PREVIEW_DB, Engine  # noqa: E402
from lab.qc.host import Host  # noqa: E402
from lab.qc.sim import MY_PRESETS, TONE_LAB, SimAudio, SimUnit  # noqa: E402
from lab.qc.slots import CACHE_SLOTS, NotOurs, Slots  # noqa: E402
from lab.qc.unit import Moved, Unit  # noqa: E402
from lab.sound import catalog  # noqa: E402
from lab.sound.assistant import Assistant  # noqa: E402
from lab.sound.chain import Block, Chain, Row  # noqa: E402
from lab.sound.sounds import Sound  # noqa: E402
from test_lab_design import GREENBACK, PLEXI, answer, block, reply  # noqa: E402

HIWATT, V30 = 1063, 12001


def one_row(*models, output=None):
    return Chain((Row(tuple(Block(model) for model in models), inlet=1, outlet=MULTI_OUT, output=output or {}),))


def inputs(device):
    """The external inputs connected on the unit's working preset."""
    return [lane.in_portid for lane in device.working.chains if lane.in_portid not in (0, 16, 17, 18)]


class EngineCase(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.device = SimUnit()
        self.device.save_slot(MY_PRESETS, 5, "Mine", [(PLEXI, {}), (V30, {})])
        self.device.recall(MY_PRESETS, 5)
        self.unit = Unit(self.device, settle=.05, sleep=lambda seconds: None)
        self.slots = Slots(self.unit, Path(folder.name))
        self.engine = Engine(self.unit, self.slots, SimAudio(self.device))
        self.engine.open()


class FailedOperationsTest(EngineCase):
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

    def test_a_record_that_cannot_read_the_channels_connects_nothing(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        self.device.read_message = lambda name, **_: None
        with self.assertRaisesRegex(RuntimeError, "Dry/Wet"):
            self.engine.record(1., "b")
        self.assertEqual(inputs(self.device), [])
        self.assertIsNone(self.engine.mode)

    def test_the_wet_take_is_the_sound_with_its_knobs(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        turned = []
        turn = self.engine.turn
        self.engine.turn = lambda side, values, scene=None: turned.append((side, values)) or turn(side, values, scene)
        self.engine.record(1., "b", {"drive": .5})
        self.assertEqual(turned, [("b", {"drive": .5})])
        self.assertEqual(inputs(self.device), [])


class PlayersPresetsTest(EngineCase):
    def test_a_preset_saved_into_the_cache_after_opening_is_not_built_over(self):
        self.device.save_slot(TONE_LAB, min(CACHE_SLOTS), "Mine in the cache", [(PLEXI, {})])
        self.engine.show(one_row(HIWATT, V30), None)
        self.assertEqual(self.device.slots[(TONE_LAB, min(CACHE_SLOTS))].name, "Mine in the cache")

    def test_closing_leaves_a_saved_preset_the_player_chose_and_edited(self):
        slot, _ = self.engine.save(one_row(HIWATT, V30), "Kept")
        self.device.player_selects(TONE_LAB, slot["position"])
        self.device.set_param(0, 0, 0, .77)
        self.engine.close()
        self.assertEqual(self.unit.position()["position"], slot["position"])
        self.assertTrue(self.device.dirty)

    def test_a_switch_between_two_route_writes_writes_nothing_to_the_new_preset(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        self.device.save_slot(MY_PRESETS, 9, "Theirs", [(PLEXI, {})])
        routing = self.device.set_routing
        calls = []

        def route_then_switch(row, in_portid=None, out_portid=None):
            routing(row, in_portid, out_portid)
            calls.append(row)
            if len(calls) == 1:
                self.device.player_selects(MY_PRESETS, 9)

        self.device.set_routing = route_then_switch
        with self.assertRaises(Moved):
            self.engine.play("b", "guitar")
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.device.dirty)


class FailedAuditionTest(EngineCase):
    def test_after_a_failed_listen_playing_makes_a_and_b_ready_again(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        pair_slot = self.engine.shown[1]
        dry = (np.sin(np.arange(24000) / 30) * .2)[:, None].astype(np.float32)
        self.engine.offset = SimAudio.LATENCY
        self.engine.audio.reamp = lambda dry, **options: np.zeros((len(dry) + 48000, 2), dtype=np.float32)
        with self.assertRaisesRegex(RuntimeError, "Nothing came back"):
            self.engine.hear(one_row(HIWATT), dry)
        self.engine.play("b", "guitar")
        self.assertEqual(self.unit.position()["position"], pair_slot["position"])
        self.assertEqual(self.device.working.chains[2].in_portid, 1)


class OutputGainTest(EngineCase):
    def test_a_sound_with_its_own_output_gain_keeps_it_under_the_audition_trim(self):
        quiet = one_row(HIWATT, V30, output={catalog.LANE_VOLUME: leveling.norm_of(-18.)})
        self.engine.show(one_row(PLEXI, V30), quiet)
        self.engine.play("b", "guitar")
        volume = self.device.working.chains[2].output_control[0].params[catalog.LANE_VOLUME].param_values[0]
        self.assertAlmostEqual(leveling.db_of(volume.float_value), -18. + PREVIEW_DB, places=1)


class LoopTest(EngineCase):
    def test_a_failed_new_loop_leaves_nothing_cached_and_the_old_take_starts_again(self):
        import lab.hearing.audio as audio_module

        started, fail = [], [False]

        class Loop:
            def __init__(self, audio, dry):
                self.dry = dry

            def start(self):
                if fail[0]:
                    raise RuntimeError("ASIO error")
                started.append(self)
                return self

            def stop(self):
                pass

        original = audio_module.Loop
        audio_module.Loop = Loop
        self.addCleanup(setattr, audio_module, "Loop", original)
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        old = (np.sin(np.arange(24000) / 30) * .2)[:, None].astype(np.float32)
        self.engine.play("a", "phrase", old)
        fail[0] = True
        with self.assertRaises(RuntimeError):
            self.engine.play("a", "phrase", old[:12000].copy())
        fail[0] = False
        self.engine.play("a", "phrase", old.copy())
        self.assertEqual(len(started), 2)


class StudioCase(unittest.TestCase):
    script = None

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.host = Host(self.folder / "unit", simulated=True, local=True)
        self.host.ready.result(10)
        self.studio = Studio(self.host, self.folder,
                             assistant=Assistant(self.folder, automatic=False, run=self.script or (lambda *a, **k: {})),
                             phrase=("take", (np.sin(np.arange(24000) / 48000 * 2 * np.pi * 196) * .2)[:, None]))
        self.addCleanup(self.studio.close)
        self.studio.open()
        self.settle()

    def settle(self, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.studio.poll()
            if not self.studio.jobs and not self.host.busy:
                self.studio.poll()
                if not self.studio.jobs:
                    return
            time.sleep(.02)
        raise AssertionError("work did not finish")

    def put_two(self):
        self.studio.put("a", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.studio.put("b", Sound("Hiwatt", one_row(HIWATT, GREENBACK), {"kind": "designed"}))
        self.settle()


class UndoTest(StudioCase):
    def test_undo_keeps_the_side_that_is_heard(self):
        self.put_two()
        self.studio.turn({"drive": .4}, "b")
        self.studio.play("guitar", "b")
        self.settle()
        self.studio.select("a")
        self.settle()
        self.studio.undo()
        self.settle()
        self.assertEqual(self.studio.state.side, "a")
        self.assertEqual(self.studio.state.mode, "guitar")

    def test_undoing_the_only_sound_stops_the_sound(self):
        self.studio.put("b", Sound("Hiwatt", one_row(HIWATT, GREENBACK), {"kind": "designed"}))
        self.settle()
        self.studio.play("guitar", "b")
        self.settle()
        self.studio.undo()
        self.settle()
        self.assertIsNone(self.studio.state.mode)
        self.assertIsNone(self.studio.state.b)


class ShapeCheckTest(StudioCase):
    def test_a_phrase_changed_during_the_check_changes_no_knob(self):
        self.put_two()
        self.studio.settle_seconds = 0.
        call = self.host.call

        def change_take_meanwhile(name, *args, **options):
            if name == "hear_shaped":
                self.studio.phrase_whole = (self.studio.phrase[1], 0, 12000)
            return call(name, *args, **options)

        self.host.call = change_take_meanwhile
        self.studio.turn({"brightness": .3}, "b")
        self.settle(30)
        sound = self.studio.sound("b")
        self.assertEqual(sound.knobs, {"brightness": .3})
        self.assertNotIn("shaped", sound.heard or {})


class SceneEvidenceTest(StudioCase):
    def test_a_measurement_of_the_first_scene_does_not_hold_for_another(self):
        gain = catalog.model(PLEXI).index("GAIN")
        switched = Chain((Row((Block(PLEXI).set(gain, (.3, .9) + (.3,) * 6), Block(V30)), inlet=1,
                              outlet=MULTI_OUT),))
        sound = Sound("Switched", switched, {"kind": "designed"})
        sound.heard = {"phrase": self.studio.phrase_key(), "digest": switched.digest(), "scene": 0, "level_db": -20.}
        self.assertIsNotNone(self.studio.evidence(sound))
        self.studio.set(scene=1)
        self.assertIsNone(self.studio.evidence(sound))


class BlindTest(StudioCase):
    def test_keep_on_a_sound_already_kept_is_still_a_blind_choice(self):
        from lab.player import taste

        self.put_two()
        self.studio.mark("keeper", "a")
        self.studio.blind()
        self.settle()
        kept = self.studio.state.a
        self.studio.mark("keeper", "a")
        self.assertFalse(self.studio.state.blind)
        self.assertIn("keeper", self.studio.store.sound(kept).marks)
        self.assertEqual(taste.choices(self.folder)[-1]["how"], "blind")


class RequestsTest(StudioCase):
    gate = threading.Event()

    @staticmethod
    def script(prompt, schema, **options):
        name = "Late" if "first request" in prompt else "Soon"
        if name == "Late":
            RequestsTest.gate.wait(10)
        return reply(*[answer(f"{name} {n}", [block(PLEXI, 0, gain=n * 3), block(GREENBACK, 1)], differs="d")
                       for n in (1, 2, 3)])

    def test_cancel_shows_at_once_and_an_older_answer_does_not_take_b(self):
        RequestsTest.gate.clear()
        shown = []
        self.studio.listeners.append(lambda state: shown.append(bool(state.working) and self.studio.cancellable()))
        self.studio.ask("make the first request")
        self.assertTrue(shown and shown[-1])
        self.studio.ask("make the second request")
        self.settle(30)
        self.assertIsNotNone(self.studio.sound("b"), self.studio.state.status)
        self.assertTrue(self.studio.sound("b").name.startswith("Soon"))
        RequestsTest.gate.set()
        self.settle(30)
        self.assertTrue(self.studio.sound("b").name.startswith("Soon"))
        self.assertIn("earlier request", self.studio.state.status)


class AuditionedTasteTest(unittest.TestCase):
    def test_a_choice_between_shaped_sounds_is_judged_on_the_shaped_measurements(self):
        from lab.player import taste
        from lab.sound.sounds import Store

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = Store(Path(folder.name))

        def heard(brightness, shaped_brightness, knobs):
            base = {"phrase": "p@0-1", "brightness_oct": brightness, "level_db": -20., "probe": {}}
            return {**base, "shaped": {"phrase": "p@0-1", "knobs": knobs, "brightness_oct": shaped_brightness,
                                       "level_db": -20., "probe": {}}}

        store.save(Sound("K", Chain(), {"kind": "designed"}, id="k", knobs={"brightness": .5},
                         heard=heard(-.5, .5, {"brightness": .5})))
        store.save(Sound("O", Chain(), {"kind": "designed"}, id="o", heard=heard(.0, .0, {})))
        taste.record_choice(folder.name, "k", "o", "a/b",
                            context={"phrase": "p@0-1", "knobs": {"k": {"brightness": .5}, "o": {}}})
        pairs, rows = taste.evidence(folder.name)
        kept = rows[pairs[0]["kept"]]
        self.assertEqual(kept["brightness_oct"], .5)          # as heard, shaped; not the base -0.5


if __name__ == "__main__":
    unittest.main()
