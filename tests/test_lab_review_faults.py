"""The faults found by the codebase review of 20 November 2026 (docs/CODEBASE_REVIEW.md), each reproduced as it was
found, on the simulated unit."""

from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest

import numpy as np

from lab.desk.studio import Studio
from lab.qc import engine as engine_module
from lab.qc.engine import MULTI_OUT, Engine
from lab.qc.host import Host
from lab.qc.sim import MY_PRESETS, TONE_LAB, SimAudio, SimUnit
from lab.qc.slots import Slots
from lab.qc.unit import Mismatch, Unit
from lab.sound import catalog
from lab.sound.chain import Block, Chain, Row
from lab.sound.sounds import Sound

PLEXI, HIWATT, V30, GREENBACK = 1081, 1063, 12001, 12006


def one_row(*models):
    return Chain((Row(tuple(Block(model) for model in models), inlet=1, outlet=MULTI_OUT),))


def engine_on(device):
    folder = TemporaryDirectory()
    unit = Unit(device, settle=.05, sleep=lambda seconds: None)
    return Engine(unit, Slots(unit, Path(folder.name)), SimAudio(device)), folder


class StopTest(unittest.TestCase):
    """Stop ends the command running and drops the earlier ones waiting; what is sent after it always runs."""

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.release = threading.Event()
        original = Engine.watch

        def slow(engine):
            self.release.wait(5)
            return {"moved": False}

        Engine.watch = slow
        self.addCleanup(setattr, Engine, "watch", original)
        self.host = Host(Path(folder.name), simulated=True, local=True)
        self.host.ready.result(10)
        self.addCleanup(self.host.close)

    def test_the_command_sent_after_stop_runs_and_the_waiting_one_before_it_does_not(self):
        running = self.host.call("watch")
        time.sleep(.1)
        waiting = self.host.call("scene", 1)
        self.host.stop()
        quiet = self.host.call("quiet")
        self.release.set()
        running.result(5)
        with self.assertRaisesRegex(RuntimeError, "Stopped before it began"):
            waiting.result(5)
        self.assertIsNone(quiet.result(5))


class StartupTest(unittest.TestCase):
    def test_a_worker_that_could_not_start_fails_every_command_at_once(self):
        original = engine_module.Engine.__init__

        def broken(*args, **options):
            raise RuntimeError("ASIO could not be opened")

        engine_module.Engine.__init__ = broken
        self.addCleanup(setattr, engine_module.Engine, "__init__", original)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        host = Host(Path(folder.name), simulated=True, local=True)
        with self.assertRaises(RuntimeError):
            host.ready.result(5)
        with self.assertRaisesRegex(RuntimeError, "ASIO could not be opened"):
            host.call("open").result(1)


class UnsavedEditsTest(unittest.TestCase):
    def setUp(self):
        self.device = SimUnit()
        self.device.save_slot(MY_PRESETS, 5, "Mine", [(PLEXI, {}), (V30, {})])
        self.device.recall(MY_PRESETS, 5)
        self.engine, folder = engine_on(self.device)
        self.addCleanup(folder.cleanup)

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
        self.device.read_state = lambda name, **_: None
        with self.assertRaisesRegex(RuntimeError, "unsaved changes"):
            self.engine.open()


class LevelBeforeInputTest(unittest.TestCase):
    def test_a_level_the_unit_ignores_leaves_the_input_disconnected(self):
        device = SimUnit()
        engine, folder = engine_on(device)
        self.addCleanup(folder.cleanup)
        engine.open()
        engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        setting = device.set_lane_param

        def ignores_volume(row, field, index, value, column=0):
            if index != catalog.LANE_VOLUME:
                setting(row, field, index, value, column)

        device.set_lane_param = ignores_volume
        engine.levels["b"] = -35.
        with self.assertRaises(Mismatch):
            engine.play("b", "guitar")
        self.assertEqual([lane.in_portid for lane in device.working.chains], [0, 0, 0, 0])


class PairedRoutingTest(unittest.TestCase):
    def test_a_sound_on_two_joined_rows_is_heard_whole_in_the_pair(self):
        device = SimUnit()
        engine, folder = engine_on(device)
        self.addCleanup(folder.cleanup)
        engine.open()
        joined = Chain((Row((Block(PLEXI),), inlet=1, outlet=16), Row((Block(V30),), inlet=0, outlet=MULTI_OUT)))
        self.assertEqual(engine.show(one_row(HIWATT, GREENBACK), joined), "pair")
        engine.play("b", "guitar")
        routes = [(lane.in_portid, lane.out_portid) for lane in device.working.chains]
        self.assertEqual(routes[2:], [(1, 16), (0, MULTI_OUT)])
        self.assertEqual(routes[0][0], 0)                    # A is not fed


class MovedAwayTest(unittest.TestCase):
    def test_choosing_another_preset_on_the_unit_stops_the_phrase_and_scene_changes_leave_it_alone(self):
        device = SimUnit()
        device.save_slot(MY_PRESETS, 9, "Theirs", [(PLEXI, {})], routes=((12, 19),))
        engine, folder = engine_on(device)
        self.addCleanup(folder.cleanup)
        started = []

        class Loop:
            def __init__(self, audio, dry):
                started.append(self)
                self.running = True

            def start(self):
                return self

            def stop(self):
                self.running = False

        import lab.hearing.audio as audio_module
        original = audio_module.Loop
        audio_module.Loop = Loop
        self.addCleanup(setattr, audio_module, "Loop", original)
        engine.open()
        engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        engine.play("a", "phrase", (np.sin(np.arange(24000) / 30) * .2)[:, None].astype(np.float32))
        device.player_selects(MY_PRESETS, 9)
        self.assertEqual(engine.watch(), {"moved": True})
        self.assertFalse(started[0].running)
        engine.scene(3)
        self.assertEqual(device.scene, 0)

    def test_a_new_phrase_replaces_the_one_looping(self):
        device = SimUnit()
        engine, folder = engine_on(device)
        self.addCleanup(folder.cleanup)
        loops = []

        class Loop:
            def __init__(self, audio, dry):
                self.dry, self.running = dry, True
                loops.append(self)

            def start(self):
                return self

            def stop(self):
                self.running = False

        import lab.hearing.audio as audio_module
        original = audio_module.Loop
        audio_module.Loop = Loop
        self.addCleanup(setattr, audio_module, "Loop", original)
        engine.open()
        engine.show(one_row(PLEXI, V30), one_row(HIWATT, GREENBACK))
        first = (np.sin(np.arange(24000) / 30) * .2)[:, None].astype(np.float32)
        engine.play("a", "phrase", first)
        engine.play("b", "phrase", first.copy())             # the same take: the loop goes on
        self.assertEqual(len(loops), 1)
        engine.play("b", "phrase", first[:12000].copy())     # another part of it: a new loop
        self.assertEqual(len(loops), 2)
        self.assertFalse(loops[0].running)


class DirectoryTest(unittest.TestCase):
    def test_a_slot_missing_from_the_listing_is_never_saved_over(self):
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
        engine, folder = engine_on(device)
        self.addCleanup(folder.cleanup)
        engine.open()
        with self.assertRaisesRegex(RuntimeError, "incompletely"):
            engine.save(one_row(HIWATT, V30), "Mine")
        self.assertEqual(device.slots[(TONE_LAB, 0)].name, "Already here")


class StudioFaultsTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.host = Host(self.folder / "unit", simulated=True, local=True)
        self.host.ready.result(10)
        self.studio = Studio(self.host, self.folder,
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

    def test_a_job_finishing_between_two_looks_still_has_its_result_handled(self):
        handled = []

        class Flicker(Future):
            looks = 0

            def done(self):
                Flicker.looks += 1
                if Flicker.looks == 1:
                    return False
                if not super().done():
                    self.set_result("late")
                return True

        self.studio.later(Flicker(), handled.append)
        for _ in range(3):
            self.studio.poll()
        self.assertEqual(handled, ["late"])

    def test_undo_after_stopping_does_not_start_playing_again_and_puts_the_knob_back(self):
        self.put_two()
        self.studio.play("guitar")
        self.settle()
        self.studio.turn({"drive": .4})
        self.studio.stop()
        self.settle()
        self.studio.undo()
        self.settle()
        self.assertIsNone(self.studio.state.mode)
        self.assertEqual(self.studio.sound("b").knobs, {})

    def test_the_name_given_is_saved_with_the_sound_it_was_given_for(self):
        self.put_two()
        b = self.studio.sound("b")
        self.studio.select("a")
        self.studio.save("Kept B", sound_id=b.id, knob_values={})
        self.settle(30)
        self.assertEqual(self.studio.store.sound(b.id).saved["name"], "Kept B")
        self.assertIsNone(self.studio.sound("a").saved)

    def test_blind_play_keeps_the_names_hidden_and_a_choice_shows_them(self):
        self.put_two()
        self.studio.blind()
        self.settle()
        self.studio.play("guitar", "a")
        self.settle()
        names = {"Plexi", "Hiwatt"}
        self.assertFalse(any(name in self.studio.state.status for name in names))
        self.studio.mark("keeper", "a")
        self.assertFalse(self.studio.state.blind)
        self.assertIn("A was", self.studio.state.status)

    def test_a_measurement_belongs_to_the_part_of_the_phrase_it_was_made_on(self):
        self.put_two()
        b = self.studio.sound("b")
        b.heard = {"phrase": self.studio.phrase_key(), "digest": b.chain.digest(), "level_db": -20.,
                   "loudness_lufs": -20.}
        self.studio.store.save(b)
        self.assertIsNotNone(self.studio.evidence(self.studio.store.sound(b.id)))
        self.studio.phrase_whole = (self.studio.phrase[1], 0, 12000)
        self.assertIsNone(self.studio.evidence(self.studio.store.sound(b.id)))

    def test_recording_keeps_the_dry_and_wet_takes_and_says_when_to_play(self):
        import json

        self.put_two()
        said = []
        self.studio.listeners.append(lambda state: said.append(state.working))
        self.studio.record("Neck", 1.)
        self.settle()
        self.assertTrue(any("play now" in text for text in said))
        phrase_id = self.studio.phrase[0]
        folder = self.folder / "phrases" / phrase_id
        self.assertTrue((folder / "dry.wav").is_file() and (folder / "wet.wav").is_file())
        info = json.loads((folder / "phrase.json").read_text(encoding="utf-8"))
        self.assertEqual(info["wet"]["name"], "Hiwatt")
        self.assertEqual(len(self.studio.phrase[1]), 48000)

    def test_a_silent_return_is_not_measured(self):
        from lab.hearing.listen import usable

        with self.assertRaisesRegex(RuntimeError, "Nothing came back"):
            usable(np.zeros((4800, 2)), "the phrase")
        with self.assertRaisesRegex(RuntimeError, "clipped"):
            usable(np.ones((4800, 2)), "the phrase")


class ProbeStereoTest(unittest.TestCase):
    def test_opposite_phase_sides_measure_as_the_same_distortion_as_in_phase(self):
        from lab.hearing import probe

        tones = probe.test_signal(.3)
        driven = np.tanh(tones[:, 0] * 8) * .3
        in_phase = probe.analyse(np.stack([driven, driven], axis=1))
        opposite = probe.analyse(np.stack([driven, -driven], axis=1))
        self.assertAlmostEqual(opposite.distortion_db, in_phase.distortion_db, places=3)


class QuotaTest(unittest.TestCase):
    def test_two_calls_started_together_cannot_both_take_the_last_place(self):
        from lab.sound.assistant import DAILY, Assistant, Refused
        import json
        from datetime import datetime, timezone

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        ledger = Path(folder.name) / "ledger.jsonl"
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ledger.write_text("".join(json.dumps({"at": today, "kind": "x"}) + "\n" for _ in range(DAILY - 1)),
                          encoding="utf-8")
        gate = threading.Event()

        def slow(prompt, schema, **options):
            gate.wait(5)
            return {}

        assistant = Assistant(folder.name, run=slow)
        results = []

        def call():
            try:
                results.append(assistant.ask("round", "p", {}, by="player"))
            except Refused as error:
                results.append(error)

        first = threading.Thread(target=call)
        first.start()
        time.sleep(.1)
        call()
        gate.set()
        first.join(5)
        self.assertEqual(sum(isinstance(item, Refused) for item in results), 1)
        self.assertEqual(len(ledger.read_text(encoding="utf-8").splitlines()), DAILY)


class CancelTest(unittest.TestCase):
    def test_cancel_drops_every_request_in_flight(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        host = Host(Path(folder.name) / "unit", simulated=True, local=True)
        studio = Studio(host, Path(folder.name))
        self.addCleanup(studio.close)
        first, second = studio._request(), studio._request()
        self.assertTrue(studio.cancel())
        self.assertTrue(first.is_set() and second.is_set())


class MixedHistoryTest(unittest.TestCase):
    def test_standard_and_extended_dates_compare(self):
        from lab.player.listening import Play, summarize

        plays = [Play(artist="A", title="x", album="", ms=200000, ts="2024-03-01 21:05", deliberate=True,
                      complete=True, skipped=False),
                 Play(artist="B", title="y", album="", ms=200000, ts="2024-03-02T21:05:11Z", deliberate=True,
                      complete=True, skipped=False)]
        self.assertIn(2024, summarize(plays)["years"])


if __name__ == "__main__":
    unittest.main()
