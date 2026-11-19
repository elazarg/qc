"""The desk's actions on the simulated unit, with a scripted assistant: no window, no hardware, no calls."""

from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lab.assistant import Assistant
from lab.host import Host
from lab.sim import MY_PRESETS
from lab.studio import Studio
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
        from lab import sim
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
        self.assertIn('"heard": {"drive"', prompt)
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
        self.assertEqual(self.studio.sound("a").id, self.studio.sound("b").id)
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
        import lab.studio as studio_module
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
        from lab.chain import Block, Chain, Row
        from lab.sounds import Sound
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


if __name__ == "__main__":
    unittest.main()
