"""The faults found by the re-review of 68cbd78 (docs/CODEBASE_REVIEW.md, "Re-review of 68cbd78"), each reproduced
as it was found, on the simulated unit."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import unittest

import numpy as np
from qc_mcp import leveling

sys.path.insert(0, str(Path(__file__).parent))

from lab.qc.engine import MULTI_OUT  # noqa: E402
from lab.qc.sim import MY_PRESETS, TONE_LAB  # noqa: E402
from lab.qc.unit import Moved  # noqa: E402
from lab.sound import catalog  # noqa: E402
from lab.sound.chain import Block, Chain, Row  # noqa: E402
from lab.sound.sounds import Sound  # noqa: E402
from test_lab_design import GREENBACK, PLEXI, answer, block, reply  # noqa: E402
from test_lab_rereview_faults import HIWATT, V30, EngineCase, StudioCase, inputs, one_row  # noqa: E402


def three_rows(*models):
    """A sound over three rows: compared by recalling its own preset, not on the shared A/B preset."""
    return Chain(tuple(Row((Block(model),), inlet=1, outlet=MULTI_OUT) for model in models))


def lane(device, row, index):
    return device.working.chains[row].output_control[0].params[index].param_values[0].float_value


class WriteGuardTest(EngineCase):
    def test_a_switch_after_the_first_level_write_writes_nothing_more(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        self.device.save_slot(MY_PRESETS, 9, "Theirs", [(PLEXI, {})])
        write = self.device.set_lane_param
        calls = []

        def level_then_switch(*args, **options):
            write(*args, **options)
            calls.append(args)
            if len(calls) == 1:
                self.device.player_selects(MY_PRESETS, 9)

        self.device.set_lane_param = level_then_switch
        with self.assertRaises(Moved):
            self.engine.play("b", "guitar")
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.device.dirty)


class UnsavedEditsTest(EngineCase):
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


class SaveReadbackTest(EngineCase):
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
        self.assertTrue(failed)
        self.assertEqual(self.unit.position()["position"], 0)
        self.assertEqual(inputs(self.device), [])


class StartupFailureTest(EngineCase):
    def test_a_scene_refused_while_recording_leaves_nothing_connected(self):
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        self.engine.chosen_scene = 3

        def refuse(scene):
            raise RuntimeError("scene refused")

        self.unit.select_scene = refuse
        with self.assertRaisesRegex(RuntimeError, "scene refused"):
            self.engine.record(1., "b")
        self.assertEqual(inputs(self.device), [])

    def test_audio_that_will_not_start_leaves_nothing_connected(self):
        import lab.hearing.audio as audio_module

        class Loop:
            def __init__(self, audio, dry):
                pass

            def start(self):
                raise RuntimeError("ASIO error")

        original = audio_module.Loop
        audio_module.Loop = Loop
        self.addCleanup(setattr, audio_module, "Loop", original)
        self.engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        with self.assertRaisesRegex(RuntimeError, "ASIO"):
            self.engine.play("a", "phrase", (np.sin(np.arange(24000) / 30) * .2)[:, None].astype(np.float32))
        self.assertEqual(inputs(self.device), [])


class RecallShapeTest(EngineCase):
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


class AuthoredLevelsTest(EngineCase):
    def test_a_lane_its_author_muted_stays_muted_when_played(self):
        sound = Chain((Row((Block(HIWATT),), inlet=1, outlet=MULTI_OUT, output={catalog.LANE_MUTE: 1.}),
                       Row((Block(V30),), inlet=1, outlet=MULTI_OUT)))
        self.engine.show(one_row(PLEXI, V30), sound)
        self.engine.play("b", "guitar")
        self.assertEqual(lane(self.device, 2, catalog.LANE_MUTE), 1.)
        self.assertEqual(lane(self.device, 3, catalog.LANE_MUTE), 0.)

    def test_saving_keeps_the_sound_s_own_level(self):
        sound = one_row(HIWATT, V30, output={catalog.LANE_VOLUME: leveling.norm_of(-6.)})
        slot, stored = self.engine.save(sound, "Quieter")
        self.assertAlmostEqual(leveling.db_of(dict(stored.rows[0].output)[catalog.LANE_VOLUME]), -6., places=1)


def switched():
    gain = catalog.model(PLEXI).index("GAIN")
    return Chain((Row((Block(PLEXI).set(gain, (.3, .9) + (.3,) * 6), Block(V30)), inlet=1, outlet=MULTI_OUT),))


class ShapeSceneTest(StudioCase):
    def test_the_shape_check_measures_the_scene_heard(self):
        self.studio.put("b", Sound("Switched", switched(), {"kind": "designed"}))
        self.settle()
        self.studio.scene(1)
        self.settle()
        self.studio.settle_seconds = 0.
        asked, call = [], self.host.call

        def note(name, *args, **options):
            if name in ("hear", "hear_shaped"):
                asked.append((name, args[-1]))
            return call(name, *args, **options)

        self.host.call = note
        self.studio.turn({"brightness": .3}, "b")
        self.settle(30)
        self.assertEqual(asked, [("hear", 1), ("hear_shaped", 1)])
        self.assertEqual(self.studio.sound("b").heard["scene"], 1)
        self.assertEqual(self.studio.sound("b").heard["shaped"]["scene"], 1)

    def test_a_check_is_current_only_for_its_own_sound(self):
        self.studio.put("a", Sound("Same", one_row(PLEXI, V30), {"kind": "designed"}))
        self.settle()
        elsewhere = Sound("Same elsewhere", one_row(PLEXI, V30), {"kind": "designed"})
        self.studio.store.save(elsewhere)
        inputs_ = (self.studio.phrase_key(), self.studio.phrase[1], self.studio.state.scene,
                   elsewhere.chain.digest(), elsewhere.id)
        self.assertFalse(self.studio._current(inputs_))


class DifferenceTest(StudioCase):
    def test_difference_reads_the_sounds_as_they_are_set_now(self):
        key = self.studio.phrase_key()

        def heard(brightness, digest, shaped=None):
            record = {"phrase": key, "digest": digest, "scene": 0, "level_db": -20., "compression": .3,
                      "brightness_oct": brightness, "low_end_db": 0., "tail_db": -40., "distortion": "clean",
                      "probe": {"distortion_db": -40.}}
            if shaped is not None:
                record["shaped"] = {**record, "phrase": key, "knobs": {"brightness": -.5},
                                    "brightness_oct": shaped}
            return record

        a, b = one_row(PLEXI, V30), one_row(HIWATT, GREENBACK)
        self.studio.put("a", Sound("A", a, {"kind": "designed"}, heard=heard(.0, a.digest())))
        self.studio.put("b", Sound("B", b, {"kind": "designed"}, knobs={"brightness": -.5},
                                   heard=heard(.1, b.digest(), shaped=-.4)))
        self.settle()
        lines = " ".join(self.studio.difference())
        self.assertIn("0.4 octave darker", lines)


class UndoSceneTest(StudioCase):
    def test_undo_keeps_the_scene_the_unit_is_on(self):
        self.studio.put("b", Sound("Switched", switched(), {"kind": "designed"}))
        self.settle()
        self.studio.turn({"drive": .4}, "b")
        self.studio.scene(1)
        self.settle()
        self.studio.undo()
        self.settle()
        self.assertEqual(self.studio.state.scene, 1)
        self.assertEqual(self.host.call("scene", 1).result(5), 1)


class OlderRequestTest(StudioCase):
    gate = threading.Event()

    @staticmethod
    def script(prompt, schema, **options):
        first = "first request" in prompt
        if first:
            OlderRequestTest.gate.wait(10)
            if "fails" in prompt:
                raise RuntimeError("the assistant failed")
        found = reply(*[answer(f"{'Late' if first else 'Soon'} {n}", [block(PLEXI, 0, gain=n * 3),
                                                                     block(GREENBACK, 1)], differs="d")
                        for n in (1, 2, 3)])
        if first:
            found["song"] = {"artist": "Old Band", "title": "Old Song", "part": "solo"}
        return found

    def test_an_older_answer_does_not_change_the_song(self):
        OlderRequestTest.gate.clear()
        self.studio.ask("make the first request")
        self.studio.ask("make the second request")
        import time

        deadline = time.monotonic() + 8          # the latest answers while the older one is still held back
        while self.studio.sound("b") is None and time.monotonic() < deadline:
            self.studio.poll()
            time.sleep(.02)
        self.assertTrue(self.studio.sound("b").name.startswith("Soon"))
        song = self.studio.state.song
        self.assertNotIn("Old Song", song or "")
        OlderRequestTest.gate.set()
        self.settle(30)
        self.assertEqual(self.studio.state.song, song)
        self.assertTrue(self.studio.sound("b").name.startswith("Soon"))

    def test_an_older_failure_keeps_cancel_while_the_latest_is_on_its_way(self):
        OlderRequestTest.gate.clear()
        latest = threading.Event()
        script = self.studio.assistant.run

        def second_waits(prompt, schema, **options):
            if "second request" in prompt:
                latest.wait(10)
            return script(prompt, schema, **options)

        self.studio.assistant.run = second_waits
        self.studio.ask("make the first request, which fails")
        self.studio.ask("make the second request")
        OlderRequestTest.gate.set()
        import time

        deadline = time.monotonic() + 10
        while "earlier request failed" not in (self.studio.state.status or "") and time.monotonic() < deadline:
            self.studio.poll()
            time.sleep(.02)
        self.assertIn("earlier request failed", self.studio.state.status)
        self.assertTrue(self.studio.state.working)
        self.assertTrue(self.studio.cancellable())
        latest.set()
        self.settle(30)


class TasteSceneTest(unittest.TestCase):
    def setUp(self):
        from lab.sound.sounds import Store

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        self.store = Store(Path(folder.name))

    def test_a_measurement_of_scene_a_does_not_stand_for_a_choice_made_in_scene_b(self):
        from lab.player import taste

        chain = switched()
        for name in ("k", "o"):
            self.store.save(Sound(name, chain, {"kind": "designed"}, id=name,
                                  heard={"phrase": "p@0-1", "scene": 0, "brightness_oct": .1, "level_db": -20.,
                                         "probe": {}}))
        taste.record_choice(self.folder, "k", "o", "a/b", context={"phrase": "p@0-1", "scene": 1,
                                                                    "knobs": {"k": {}, "o": {}}})
        pairs, rows = taste.evidence(self.folder)
        self.assertFalse(any(pair["kept"] in rows for pair in pairs))

    def test_a_repeated_choice_in_another_audition_is_kept_too(self):
        from lab.player import taste

        for name in ("k", "o"):
            self.store.save(Sound(name, one_row(PLEXI, V30), {"kind": "designed"}, id=name,
                                  heard={"phrase": "p2@0-1", "scene": 0, "brightness_oct": .1, "level_db": -20.,
                                         "probe": {}}))
        for phrase in ("p1@0-1", "p2@0-1"):
            taste.record_choice(self.folder, "k", "o", "a/b", context={"phrase": phrase, "scene": 0,
                                                                        "knobs": {"k": {}, "o": {}}})
        pairs, rows = taste.evidence(self.folder)
        self.assertEqual(sum(pair["kept"] in rows for pair in pairs), 1)     # the one measured on p2


if __name__ == "__main__":
    unittest.main()
