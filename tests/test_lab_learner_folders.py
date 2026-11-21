"""Where the learner keeps its data: the copy out of the program folder, and paths written into records.

Everything here is made up in a temporary folder; the player's own folders are never touched.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from lab.learner import folders


class LearnerFoldersTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.addCleanup(folders.use, None)

    def old_data(self):
        old = self.base / "program" / "tone_sessions"
        for path, text in (("guitar_learner/charvel-1/dk24_blade/abc/record.json", "{}"),
                           ("guitar_learner/charvel-layout.json", '{"layout": "dk24_blade"}'),
                           ("recordings/clip-20261118-120000-abcdef/dry.wav", "audio"),
                           ("guitar_input/charvel-1/pickup.json", "{}"),
                           ("lab/sounds/x.json", "not the learner's"),
                           ("desk.json", "not the learner's")):
            (old / path).parent.mkdir(parents=True, exist_ok=True)
            (old / path).write_text(text, encoding="utf-8")
        return old

    def test_the_folder_is_resolved_when_asked_and_follows_the_data_folder(self):
        with patch("lab.knowledge.home", return_value=self.base / "data"):
            self.assertEqual(folders.root(), self.base / "data" / "learner")
        folders.use(self.base / "simulated" / "learner")
        self.assertEqual(folders.recordings(), self.base / "simulated" / "learner" / "recordings")
        self.assertEqual(folders.taught("Charvel").parent.name, "guitar_input")
        self.assertEqual(folders.slug(" CHARVEL "), folders.slug("charvel"))
        self.assertNotEqual(folders.slug("Les Paul"), folders.slug("Les-Paul"))

    def test_the_earlier_data_is_copied_once_and_left_as_it_was(self):
        old, new = self.old_data(), self.base / "data" / "learner"
        before = sorted(str(path.relative_to(old)) for path in old.rglob("*"))
        (new.parent).mkdir()
        (new.parent / "learner.copying").mkdir()                 # left by a copy that was interrupted
        (new.parent / "learner.copying" / "half").write_text("x", encoding="utf-8")
        self.assertTrue(folders.bring_old(old, new))
        self.assertEqual((new / "recordings" / "clip-20261118-120000-abcdef" / "dry.wav").read_text(encoding="utf-8"), "audio")
        self.assertTrue((new / "guitar_learner" / "charvel-layout.json").is_file())
        self.assertTrue((new / "guitar_input" / "charvel-1" / "pickup.json").is_file())
        self.assertFalse((new / "lab").exists())                  # only the learner's own folders
        self.assertFalse((new / "half").exists())
        self.assertFalse((new.parent / "learner.copying").exists())
        after = sorted(str(path.relative_to(old)) for path in old.rglob("*"))
        self.assertEqual(after, sorted([*before, folders.MOVED_NOTE]))          # nothing moved, nothing deleted
        self.assertIn(str(new), (old / folders.MOVED_NOTE).read_text(encoding="utf-8"))
        (old / "recordings" / "later.wav").write_text("new", encoding="utf-8")
        self.assertFalse(folders.bring_old(old, new))             # never a second time
        self.assertFalse((new / "recordings" / "later.wav").exists())

    def test_nothing_is_made_when_there_was_no_earlier_data(self):
        (self.base / "program" / "tone_sessions" / "lab").mkdir(parents=True)
        new = self.base / "data" / "learner"
        self.assertFalse(folders.bring_old(self.base / "program" / "tone_sessions", new))
        self.assertFalse(new.exists())

    def test_paths_in_records_are_relative_and_earlier_absolute_ones_find_the_copy(self):
        old, new = self.old_data(), self.base / "data" / "learner"
        folders.bring_old(old, new)
        folders.use(new)
        take = new / "recordings" / "clip-20261118-120000-abcdef" / "dry.wav"
        self.assertEqual(folders.stored(take), "recordings/clip-20261118-120000-abcdef/dry.wav")
        self.assertEqual(folders.found("recordings/clip-20261118-120000-abcdef/dry.wav"), take)
        outside = self.base / "elsewhere.wav"
        self.assertEqual(folders.found(folders.stored(outside)), outside)
        # What the earlier app wrote: an absolute path into its own folder, wherever the program was then.
        earlier = old / "recordings" / "clip-20261118-120000-abcdef" / "dry.wav"
        self.assertEqual(folders.found(str(earlier)), take)
        gone = Path("C:/somewhere/else/qc/tone_sessions/recordings/clip-20261118-120000-abcdef/dry.wav")
        self.assertEqual(folders.found(str(gone)), take)
        only_there = old / "recordings" / "never-copied.wav"
        only_there.write_text("x", encoding="utf-8")
        self.assertEqual(folders.found(str(only_there)), only_there)


if __name__ == "__main__":
    unittest.main()
