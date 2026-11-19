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
        self.studio = Studio(self.host, self.folder, assistant=Assistant(self.folder, run=self.script),
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


if __name__ == "__main__":
    unittest.main()
