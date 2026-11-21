"""The desk's actions on the simulated unit, with a scripted assistant: no window, no hardware, no calls."""

from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lab.sound.assistant import Assistant
from lab.qc.host import Host
from lab.qc.sim import MY_PRESETS
from lab.sound import catalog
from lab.desk.studio import Studio
from test_lab_design import CHARVEL, FUZZ, GREENBACK, HIWATT, PLEXI, V30, Scripted, answer, block, reply


def settle(studio, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        studio.poll()
        if not studio.jobs and not studio.host.busy:
            studio.poll()
            if not studio.jobs:
                return
        time.sleep(.02)
    raise AssertionError("work did not finish")


class StudioTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.host = Host(self.folder / "unit", simulated=True, local=True)
        self.host.ready.result(10)
        self.script = Scripted()
        self.studio = Studio(self.host, self.folder, assistant=Assistant(self.folder, automatic=False, run=self.script),
                             phrase=("take", (np.sin(np.arange(24000) / 48000 * 2 * np.pi * 196) * .2)[:, None]))
        self.studio.idle_seconds = 0.        # silent listening starts at once, not after the unit was left alone
        self.addCleanup(self.studio.close)
        self.studio.open()
        settle(self.studio)

    def test_a_request_for_a_new_sound_brings_a_round_and_the_first_goes_on_b(self):
        self.script.answers.append(reply(
            answer("Fuzz", [block(FUZZ, 0, fuzz=10), block(HIWATT, 1), block(V30, 2)], differs="Documented"),
            answer("Plexi", [block(PLEXI, 0, gain=8), block(GREENBACK, 1)], differs="Plexi"),
            answer("Hot Plexi", [block(PLEXI, 0, gain=10), block(V30, 1)], differs="Hotter")))
        self.studio.ask("make David Gilmour - Time (Solo)")
        settle(self.studio, 20)
        state = self.studio.state
        self.assertEqual(self.studio.sound("b").name, "Fuzz")
        self.assertEqual(state.shown, "pair")
        self.assertIn("Aiming for saturated, balanced, ambient", state.status)
        found = self.studio.round()
        self.assertEqual(len(found["candidates"]), 3)
        heard = [self.studio.store.sound(c["sound"]).heard for c in found["candidates"]]
        self.assertTrue(all(heard))           # Listened to silently while the unit was free.
        self.studio.step(1)
        settle(self.studio)
        self.assertEqual(self.studio.sound("b").name, "Plexi")

    def test_a_remark_moves_the_knobs_and_is_kept_with_the_sound(self):
        self.script.answers.append(reply(
            answer("Plexi", [block(PLEXI, 0, gain=5), block(GREENBACK, 1)]),
            answer("Vox", [block(1011, 0), block(V30, 1)])))  # Both everyday models: offered for any request.
        self.studio.ask("make a classic rock crunch")
        settle(self.studio, 20)
        self.studio.play("guitar")
        settle(self.studio)
        self.studio.ask("a bit tighter")
        settle(self.studio)
        sound = self.studio.sound("b")
        self.assertTrue(sound.knobs)    # The first remedy players try that this chain can perform.
        self.assertEqual(sound.notes[-1]["text"], "a bit tighter")
        self.assertEqual(len(self.script.asked), 1)   # No call for a remark the app understands.
        self.studio.undo()
        settle(self.studio)

    def test_a_saved_preset_is_brought_in_revised_and_saved_back(self):
        unit = None
        # Put a preset of the player's on the simulated unit, as if saved there.
        from lab.qc import sim
        device = self.host  # local host: reach the engine's simulated unit through a command
        self.host.call("read", {"folder_key": sim.FACTORY, "position": 0, "is_factory": True}).result(10)
        self.studio.work_on({"folder_key": sim.FACTORY, "position": 0, "is_factory": True}, "Brit 2203")
        settle(self.studio)
        a, b = self.studio.sound("a"), self.studio.sound("b")
        self.assertEqual((a.name, a.origin["kind"], b.parent), ("Brit 2203", "preset", a.id))
        changed = {"name": "x", "target": "t", "explanation": "e", "differs": "", "scenes": [],
                   "blend": {"level_db": 0, "spread": 0}, "sources": [],
                   "rows": [{"row": 0, "split_at": -1, "join_at": -1,
                             "blocks": [{"column": 0, "model_id": 1001, "settings": [{"name": "GAIN", "value": 3}],
                                         "scene_settings": [], "off_in_scenes": []},
                                        {"column": 1, "model_id": 12001, "settings": [],
                                         "scene_settings": [], "off_in_scenes": []}]}]}
        self.script.answers.append(changed)
        self.studio.ask("cleaner, it breaks up too early")
        settle(self.studio, 20)
        revised = self.studio.sound("b")
        self.assertEqual(revised.parent, b.id)
        self.assertIn("current_chain", self.script.asked[-1][0])
        self.studio.save("Brit Cleaner")
        settle(self.studio, 20)
        self.assertEqual(self.studio.sound("b").saved["name"], "Brit Cleaner")
        self.assertIn("Saved · Tone Lab", self.studio.state.status)

    def test_a_song_collects_its_parts(self):
        self.studio.work_on({"folder_key": "/opt/neuraldsp/Factory Library", "position": 0, "is_factory": True},
                            "Brit 2203")
        settle(self.studio)
        self.studio.set(song="Time")
        self.studio.add_part("Lead")
        song = self.studio.store.song("Time")
        self.assertEqual(song["parts"][0]["name"], "Lead")
        self.assertEqual(song["instrument"], "Charvel")


    def test_phrases_recorded_per_pickup_setting_make_a_profile_of_the_guitar(self):
        _, lines = self.studio.guitar_profile()
        self.assertIn("No phrase has been recorded", lines[0])
        for setting in ("Bridge · Series", "Neck · Parallel"):
            self.studio.record(setting, seconds=1.)
            settle(self.studio)
        self.assertEqual(self.studio.phrase[0], self.studio.recorded_phrases()[0]["id"])
        summary, lines = self.studio.guitar_profile()
        self.assertEqual(set(summary), {"Bridge · Series", "Neck · Parallel"})
        self.assertTrue(any("than Bridge · Series" in line for line in lines))


    def three(self, *names):
        return reply(*[answer(name, [block(PLEXI, 0, gain=gain), block(GREENBACK, 1)], differs=name)
                       for name, gain in zip(names, (3, 6, 9))])

    def test_three_more_are_informed_by_what_was_heard_and_marked_and_rounds_can_be_paged(self):
        self.script.answers += [self.three("One", "Two", "Three"), self.three("Four", "Five", "Six")]
        self.studio.ask("make a classic rock crunch")
        settle(self.studio, 20)
        first = self.studio.state.round
        self.studio.mark("not this")
        self.studio.more()
        settle(self.studio, 20)
        prompt = self.script.asked[-1][0]
        self.assertIn('"player_marks": ["not this"]', prompt)
        self.assertIn('"heard": {"distortion"', prompt)
        self.assertEqual(self.studio.sound("b").name, "Four")
        self.assertEqual(self.studio.round()["previous"], first)
        self.studio.round_back()
        settle(self.studio)
        self.assertEqual((self.studio.state.round, self.studio.sound("b").name), (first, "One"))
        self.studio.round_forward()                      # already made: no new call
        settle(self.studio)
        self.assertEqual((self.studio.sound("b").name, len(self.script.asked)), ("Four", 2))

    def test_an_automatic_assistant_prepares_three_more_once_the_round_is_heard_and_no_further(self):
        self.studio.assistant.automatic = True
        self.script.answers += [self.three("One", "Two", "Three"), self.three("Four", "Five", "Six")]
        self.studio.ask("make a classic rock crunch")
        settle(self.studio, 30)
        found = self.studio.round()
        self.assertTrue(found.get("next"))
        self.assertEqual(self.studio.sound("b").name, "One")       # Prepared, not put on show.
        self.assertIn("Three more designs are ready", self.studio.state.status)
        self.assertEqual(self.studio.round(found["next"])["by"], "app")
        self.studio.round_forward()
        settle(self.studio, 30)
        self.assertEqual(len(self.script.asked), 2)       # The app's own round leads to no further call.

    def test_marks_copy_and_blind_comparison(self):
        self.script.answers.append(self.three("One", "Two", "Three"))
        self.studio.ask("make a classic rock crunch")
        settle(self.studio, 20)
        self.studio.mark("keeper")
        self.studio.mark("not this")
        self.assertEqual(self.studio.sound("b").marks, ["not this"])
        self.studio.copy_to_a()
        settle(self.studio)
        a, b = self.studio.sound("a"), self.studio.sound("b")
        self.assertNotEqual(a.id, b.id)                  # a sound of its own
        self.assertEqual((a.chain.digest(), a.knobs, a.parent), (b.chain.digest(), b.knobs, b.id))
        self.studio.turn({"drive": .4}, "b")
        self.assertEqual(self.studio.sound("a").knobs, a.knobs)   # turning B leaves A as it was
        self.studio.step(2)
        settle(self.studio)
        names = {self.studio.sound("a").name, self.studio.sound("b").name}
        self.studio.blind()
        settle(self.studio)
        self.assertTrue(self.studio.state.blind)
        self.assertEqual({self.studio.sound("a").name, self.studio.sound("b").name}, names)
        self.studio.blind()
        self.assertFalse(self.studio.state.blind)
        self.assertIn("A was", self.studio.state.status)

    def test_a_preset_name_typed_as_a_request_brings_that_preset_in(self):
        import json
        (self.folder / "directory.json").write_text(json.dumps([
            {"slot": {"folder_key": "/opt/neuraldsp/Factory Library", "position": 0, "is_factory": False},
             "name": "Brit 2203", "setlist": "My Presets"}]), encoding="utf-8")
        import lab.desk.studio as studio_module
        original = studio_module.Studio._preset_named

        def named(studio, text):
            found = json.loads((studio.folder / "directory.json").read_text(encoding="utf-8"))
            return next(((item["slot"], item["name"]) for item in found
                         if item["name"].casefold() == text.strip().casefold()), None)

        studio_module.Studio._preset_named = named
        self.addCleanup(setattr, studio_module.Studio, "_preset_named", original)
        self.studio.ask("brit 2203")
        settle(self.studio)
        self.assertEqual(self.studio.sound("a").origin["kind"], "preset")
        self.assertEqual(self.script.asked, [])
        self.assertIn('ask "make Brit 2203"', self.studio.state.status)

    def test_a_part_can_be_one_scene_of_a_sound(self):
        self.studio.work_on({"folder_key": "/opt/neuraldsp/Factory Library", "position": 0, "is_factory": True},
                            "Brit 2203")
        settle(self.studio)
        self.studio.set(song="Time")
        self.studio.add_part("Lead", scene=2)
        self.assertEqual(self.studio.store.song("Time")["parts"][0]["scene"], 2)


    def test_a_scene_is_chosen_and_kept_when_a_and_b_are_made_ready_again(self):
        from lab.sound.chain import Block, Chain, Row
        from lab.sound.sounds import Sound
        drive = Block(PLEXI).set(0, (.2, .2, .9) + (.2,) * 5)
        chain = Chain((Row((drive, Block(GREENBACK)), inlet=1, outlet=19),))
        self.assertEqual(chain.scenes_in_use(), 3)
        self.studio.put("b", Sound("Scenes", chain, {"kind": "designed"}))
        settle(self.studio)
        self.assertEqual([scene for scene, _ in self.studio.scenes()], [0, 1, 2])
        self.studio.scene(2)
        settle(self.studio)
        self.studio.play("guitar")
        settle(self.studio)
        self.assertEqual(self.host.call("lab.tools.probe.scene").result(10), 2)

    def test_a_remark_said_again_takes_back_the_first_remedy_and_tries_the_next(self):
        self.script.answers.append(self.three("One", "Two", "Three"))
        self.studio.ask("make a classic rock crunch")
        settle(self.studio, 20)
        self.studio.ask("muddy")
        first = dict(self.studio.sound("b").knobs)
        self.assertIn("Say it again for", self.studio.state.status)
        self.studio.ask("muddy")
        second = dict(self.studio.sound("b").knobs)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.script.asked), 1)

    def test_after_a_turn_settles_the_shaped_sound_is_heard_and_a_knob_that_did_too_little_is_corrected_once(self):
        self.script.answers.append(self.three("One", "Two", "Three"))
        self.studio.ask("make a classic rock crunch")
        settle(self.studio, 20)
        self.studio.settle_seconds = 0.
        # The simulated unit's sound follows only an amp's gain, so turning the treble up measures as nothing.
        self.studio.ask("brighter")
        settle(self.studio, 30)
        sound = self.studio.sound("b")
        shaped = sound.heard["shaped"]
        self.assertEqual(shaped["knobs"], {"brightness": .3})
        self.assertEqual(shaped["check"]["brightness"]["verdict"], "too small")
        self.assertEqual(shaped["corrected"], ["brightness", .3, .48])
        self.assertEqual(sound.knobs, {"brightness": .48})
        status = self.studio.state.status
        self.assertIn("Measured with the knobs", status)
        self.assertIn("Brightness: spectral centre", status)
        self.assertIn("from +0.30 to +0.48, once; not measured again", status)
        self.assertIsNone(self.studio.shaping)           # the correction is not checked again: no loop
        self.assertEqual(self.studio.state.shown, "pair")  # A and B are back on the unit

    def test_a_knob_that_did_its_job_is_left_alone_and_nothing_is_heard_while_playing(self):
        self.script.answers.append(self.three("One", "Two", "Three"))
        self.studio.ask("make a classic rock crunch")
        settle(self.studio, 20)
        self.studio.settle_seconds = 0.
        self.studio.play("guitar")
        settle(self.studio)
        self.studio.ask("more gain")
        settle(self.studio)
        self.assertNotIn("shaped", self.studio.sound("b").heard)   # never while playing
        self.assertIsNotNone(self.studio.shaping)
        self.studio.stop()
        settle(self.studio, 30)
        sound = self.studio.sound("b")
        self.assertEqual(sound.heard["shaped"]["check"]["drive"]["verdict"], "as intended")
        self.assertIsNone(sound.heard["shaped"]["corrected"])
        self.assertEqual(sound.knobs, {"drive": .3})
        self.assertIn("Distortion: harmonics", self.studio.state.status)
        self.assertNotIn("turned", self.studio.state.status)

    def test_a_cancelled_request_leaves_b_alone(self):
        import threading
        release = threading.Event()
        script = self.script

        def slow(prompt, schema, *, web, seconds, cancel):
            release.wait(10)
            return script(prompt, schema, web=web, seconds=seconds, cancel=cancel)

        self.studio.assistant.run = slow
        self.script.answers.append(self.three("One", "Two", "Three"))
        self.studio.ask("make a classic rock crunch")
        self.assertTrue(self.studio.cancel())
        release.set()
        settle(self.studio, 20)
        self.assertIsNone(self.studio.sound("b"))
        self.assertEqual(self.studio.state.status, "Request cancelled.")


    def test_a_named_setting_makes_a_copy_with_that_block_changed(self):
        from unittest.mock import patch
        self.script.answers.append(self.three("One", "Two", "Three"))
        self.studio.ask("make a classic rock crunch")
        settle(self.studio, 20)
        before = self.studio.sound("b")
        with patch("lab.knowledge.named_settings", return_value={"Hot": {"GAIN": 9.0}}):
            self.studio.named(0, 0, "Hot")
        settle(self.studio)
        after = self.studio.sound("b")
        gain = catalog.model(PLEXI).index("GAIN")
        self.assertEqual((after.parent, round(after.chain.block(0, 0).display(gain))), (before.id, 9))
        self.studio.undo()
        self.assertEqual(self.studio.state.b, before.id)

    def test_the_difference_between_a_and_b_in_words(self):
        self.script.answers.append(self.three("One", "Two", "Three"))
        self.studio.ask("make a classic rock crunch")
        settle(self.studio, 20)
        self.studio.copy_to_a()
        settle(self.studio)
        self.studio.step(2)
        settle(self.studio)
        lines = self.studio.difference()
        self.assertTrue(lines)
        self.assertNotIn("need to have been listened to", lines[0])


    def test_an_ir_goes_into_bs_cab_and_a_capture_in_place_of_its_amp(self):
        self.script.answers.append(self.three("One", "Two", "Three"))
        self.studio.ask("make a classic rock crunch")
        settle(self.studio, 20)
        found = []
        self.studio.read_assets(found.extend)
        settle(self.studio)
        from lab.qc.assets import cached
        irs = [item for item in cached(self.studio.folder) if item.kind == "ir" and item.source == "user"]
        captures = [item for item in cached(self.studio.folder) if item.kind == "capture"]
        self.assertTrue(found and irs and captures)
        before = self.studio.sound("b")
        self.studio.use_asset(irs[0])
        settle(self.studio)
        with_ir = self.studio.sound("b")
        self.assertEqual(with_ir.parent, before.id)
        self.assertIn(29001, [block.model for _, _, block in with_ir.chain.blocks])
        self.assertEqual(self.studio.state.shown, "pair")      # built and held on the unit
        self.studio.use_asset(captures[0], has_cab=True, version=captures[0].capture_version or 2)
        settle(self.studio)
        models = [block.model for _, _, block in self.studio.sound("b").chain.blocks]
        self.assertTrue({14000, 14001} & set(models))
        self.assertNotIn(PLEXI, models)


    def test_a_keeper_given_while_comparing_is_a_choice_over_the_other_side(self):
        import json
        self.script.answers.append(self.three("One", "Two", "Three"))
        self.studio.ask("make a classic rock crunch")
        settle(self.studio, 20)
        self.studio.copy_to_a()
        settle(self.studio)
        self.studio.step(1)
        settle(self.studio)
        a, b = self.studio.sound("a"), self.studio.sound("b")
        self.studio.mark("keeper", "b")
        lines = (self.folder / "choices.jsonl").read_text(encoding="utf-8").splitlines()
        choice = json.loads(lines[-1])
        self.assertEqual((choice["kept"], choice["over"], choice["how"]), (b.id, a.id, "a/b"))
        self.assertTrue(self.studio.taste_lines())

    def test_what_the_player_listens_to_reaches_an_open_request(self):
        import json
        (self.folder / "personal").mkdir(exist_ok=True)
        (self.folder / "personal" / "listening.json").write_text(json.dumps({
            "summary": {"artists": [["Band A", 10.0]]},
            "guitar_context": [{"artist": "Band A", "guitar_led": True, "guitarists": ["Y"], "tones": ["crunch"]}]}),
            encoding="utf-8")
        self.script.answers.append(self.three("One", "Two", "Three"))
        self.studio.ask("make a lead tone")
        settle(self.studio, 20)
        self.assertIn('"player_listening"', self.script.asked[-1][0])


    def test_the_next_start_comes_back_to_what_was_on_show(self):
        self.script.answers.append(self.three("One", "Two", "Three"))
        self.studio.ask("make a classic rock crunch")
        settle(self.studio, 20)
        self.studio.copy_to_a()
        settle(self.studio)
        self.studio.step(2)
        settle(self.studio)
        self.studio.set(song="Time", side="a")
        self.studio.phrase_whole = (self.studio.phrase[1], 100, 2000)
        self.studio.remember(panel="Chain")
        before = self.studio.state
        self.studio.close()

        host = Host(self.folder / "unit", simulated=True, local=True)
        host.ready.result(10)
        again = Studio(host, self.folder, assistant=Assistant(self.folder, automatic=False, run=self.script))
        self.addCleanup(again.close)
        record = again.restore()
        for key in ("a", "b", "side", "round", "song", "instrument", "scene"):
            self.assertEqual(getattr(again.state, key), getattr(before, key), key)
        self.assertEqual(record["phrase"], {"id": "take", "start": 100, "end": 2000})
        self.assertEqual(record["desk"]["panel"], "Chain")
        again.open()
        settle(again, 20)
        self.assertEqual(again.state.shown, "pair")                 # A and B made ready on the unit again
        self.assertIn("Back where you left off", again.state.status)
        self.assertEqual(again.round()["candidates"][2]["sound"], again.state.b)

    def test_sounds_that_no_longer_exist_are_not_put_back(self):
        import json
        (self.folder / "session.json").write_text(json.dumps({"a": "gone", "b": "gone too", "side": "a"}),
                                                  encoding="utf-8")
        self.studio.restore()
        self.assertEqual((self.studio.state.a, self.studio.state.b, self.studio.state.side), (None, None, "a"))


if __name__ == "__main__":
    unittest.main()


class SongPartTest(unittest.TestCase):
    """A sound kept from a round the assistant understood as a song's part becomes that part, with no naming."""

    def test_a_kept_sound_from_a_song_round_is_filed_under_the_song_and_part(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from lab.desk.studio import Studio
        from lab.qc.host import Host
        from lab.sound.chain import Block, Chain, Row
        from lab.sound.sounds import Sound

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        home = Path(folder.name)
        host = Host(home / "unit", simulated=True, local=True)
        studio = Studio(host, home)
        self.addCleanup(studio.close)
        sound = studio.store.save(Sound("Gilmour Lead", Chain((Row((Block(GREENBACK),), inlet=1, outlet=19),)), {"kind": "designed"}))
        (home / "rounds").mkdir(exist_ok=True)
        (home / "rounds" / "r1.json").write_text(json.dumps({
            "id": "r1", "request": "comfortably numb second solo", "candidates": [{"sound": sound.id, "differs": ""}],
            "aim": {"song": {"artist": "Pink Floyd", "title": "Comfortably Numb", "part": "solo"}}}), encoding="utf-8")
        studio.set(b=sound.id, side="b")
        studio.mark("keeper")
        song = studio.store.song("Pink Floyd - Comfortably Numb")
        self.assertEqual(song["parts"], [{"name": "Solo", "sound": sound.id}])
        self.assertEqual(studio.state.song, "Pink Floyd - Comfortably Numb")
