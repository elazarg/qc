"""Choosing in place: a few choices as chips, and a file from a list of the ones found where such files are kept,
or by a path typed in. One test for each place a file dialog or a drop-down used to open.

The player's own folders are made up (`DeskCase.player`); nothing of the real Downloads or Documents is read.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
import unittest
import zipfile

import support  # noqa: E402
from lab.hearing.audio import write_wave
from lab.sound.sounds import Sound
from lab.tools import common

from support import PLEXI, V30, DeskCase, chain, phrase


def wave(path, level=.2, seconds=1.5):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_wave(path, phrase(seconds, level))
    return path


class FilesTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_files_are_found_in_the_folders_and_one_folder_down_newest_first(self):
        old = wave(self.folder / "Downloads" / "old.wav")
        new = wave(self.folder / "Music" / "takes" / "new.wav")
        wave(self.folder / "Music" / "a" / "b" / "too-deep.wav")
        (self.folder / "Downloads" / "notes.txt").write_text("not a recording", encoding="utf-8")
        os.utime(old, (1_000_000, 1_000_000))
        found = common.files_in([self.folder / "Downloads", self.folder / "Music", self.folder / "missing"], ("*.wav",))
        self.assertEqual([path.name for path in found], ["new.wav", "old.wav"])
        self.assertEqual(found[0], new.resolve())

    def test_a_path_typed_in_is_checked_and_a_bad_one_is_refused_with_the_reason(self):
        good = wave(self.folder / "take.wav")
        (self.folder / "song.mp3").write_text("x", encoding="utf-8")
        self.assertEqual(common.checked_file(f'  "{good}"  ', ("*.wav",)), good.resolve())
        self.assertEqual(common.checked_file(str(good).upper(), ("*.WAV",)), Path(str(good).upper()).resolve())
        for text, why in (("", "Type or paste"), (str(self.folder / "missing.wav"), "There is no file at"),
                          (str(self.folder), "is a folder"), (str(self.folder / "song.mp3"), "is not one of ")):
            with self.assertRaisesRegex(ValueError, why):
                common.checked_file(text, ("*.wav",))

    def test_a_test_is_never_shown_the_player_s_own_folders(self):
        self.assertEqual(common.player_home(), isolated.HOME / "player")
        self.assertNotEqual(common.player_home(), Path.home())
        for kind in ("wav", "pod", "spotify"):
            for folder in common.usual_folders(kind, self.folder):
                self.assertTrue(self.folder in folder.parents or isolated.HOME in folder.parents, folder)

    def test_where_each_kind_of_file_is_looked_for(self):
        from unittest import mock

        with mock.patch.object(common, "player_home", lambda: self.folder / "me"):
            pod = common.usual_folders("pod", self.folder / "data")
            self.assertEqual(pod[0], self.folder / "me" / "Documents" / "Line 6" / "POD Go" / "Backups")
            self.assertIn(self.folder / "me" / "Downloads", common.usual_folders("spotify", self.folder / "data"))
            self.assertEqual(common.usual_folders("wav", self.folder / "data")[0], self.folder / "data" / "imports")


class WidgetsTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.root = tk.Tk()
        self.addCleanup(support.free_now)
        self.addCleanup(self.root.destroy)

    def test_chips_show_every_choice_light_the_chosen_one_and_wrap_within_their_width(self):
        from lab.sound.knobs import KNOBS

        names = tuple(spec[0] for spec in KNOBS.values())
        chosen = tk.StringVar(master=self.root, value=names[0])
        changed = []
        chips = common.Chips(self.root, chosen, names, command=lambda: changed.append(chosen.get()))
        chips.pack()
        self.root.update()
        self.assertEqual(set(chips.chips), set(names))
        self.assertLessEqual(chips.winfo_reqwidth(), common.WIDTH + 6)
        self.assertGreater(len({int(chip.grid_info()["row"]) for chip in chips.chips.values()}), 2)
        chips.chips[names[3]].event_generate("<ButtonRelease-1>")
        self.root.update()
        self.assertEqual((chosen.get(), changed), (names[3], [names[3]]))
        self.assertNotEqual(chips.chips[names[3]]["bg"], chips.chips[names[0]]["bg"])      # the chosen one is lit
        chips.enable(False)
        chips.chips[names[5]].event_generate("<ButtonRelease-1>")
        self.root.update()
        self.assertEqual(chosen.get(), names[3])
        chips.offer(("One", "Two"))
        self.assertEqual(set(chips.chips), {"One", "Two"})

    def test_a_file_is_chosen_from_the_list_or_by_its_path_and_a_bad_path_says_why(self):
        first = wave(self.folder / "Downloads" / "first.wav")
        chosen = []
        choice = common.FileChoice(self.root, folders=[self.folder / "Downloads"], patterns=("*.wav",),
                                   choose=chosen.append)
        choice.pack()
        self.root.update()
        self.assertEqual(choice.list.get(0, "end"), ("first.wav  ·  Downloads",))
        choice.use()
        self.assertEqual(choice.said.cget("text"), "Choose one in the list first, or paste its path.")
        choice.list.selection_set(0)
        choice.use()
        self.assertEqual(chosen, [first.resolve()])
        second = wave(self.folder / "elsewhere" / "second.wav")
        choice.path.insert(0, str(self.folder / "elsewhere" / "missing.wav"))
        choice.path.event_generate("<Return>")
        choice.typed()
        self.assertIn("There is no file at", choice.said.cget("text"))
        self.assertEqual(len(chosen), 1)
        choice.path.delete(0, "end")
        choice.path.insert(0, str(second))
        choice.typed()
        self.assertEqual((chosen[-1], choice.said.cget("text")), (second.resolve(), ""))
        wave(self.folder / "Downloads" / "third.wav")
        choice.look()
        self.assertEqual(choice.list.size(), 2)

    def test_with_nothing_found_the_list_says_where_it_looked(self):
        choice = common.FileChoice(self.root, folders=lambda: [self.folder / "Backups"], patterns=("*.pgb",),
                                   choose=lambda path: None)
        self.assertIn("None found in", choice.said.cget("text"))
        self.assertIn("Backups", choice.said.cget("text"))


class InPlaceOfEachDialogTest(DeskCase):
    def test_tone_search_takes_a_wav_as_its_reference_from_the_list_or_a_path(self):
        ref = wave(self.player / "Downloads" / "reference.wav")
        tool = self.tool("search")
        tool.offer("wav")
        self.assertEqual(tool.wave_choice.winfo_manager(), "pack")
        self.assertEqual(tool.wave_choice.list.get(0), "reference.wav  ·  Downloads")
        tool.wave_choice.path.insert(0, str(self.player / "Downloads" / "nothing.wav"))
        tool.wave_choice.typed()
        self.assertIn("There is no file at", tool.wave_choice.said.cget("text"))
        self.assertIsNone(tool.reference)
        tool.wave_choice.list.selection_set(0)
        tool.wave_choice.use()
        self.assertEqual(tool.reference["name"], ref.name)
        self.assertEqual(tool.wave_choice.winfo_manager(), "")          # put away once it is chosen

    def test_tone_search_lists_the_captured_presets_in_place(self):
        from lab.tools import library

        tool = self.tool("search")
        tool.offer("captured")
        self.assertIn("No preset has been captured yet", tool.status.get())
        library.keep_reference(self.home, {"slot": {"position": 0}, "setlist": "My Presets"}, None,
                               {"name": "Vai", "wet": phrase(), "digest": "d"}, "phrase-1")
        tool.offer("captured")          # put away
        tool.offer("captured")          # and shown again, read again
        self.assertEqual(tool.captured_list.get(0), "Vai · My Presets")
        tool.use_captured()
        self.assertIn("Choose a captured preset", tool.status.get())
        tool.captured_list.selection_set(0)
        tool.use_captured()
        self.assertEqual((tool.reference["name"], tool.reference["phrase"]), ("Vai", "phrase-1"))

    def test_the_workbench_compares_recordings_chosen_in_place(self):
        wave(self.player / "Music" / "quiet.wav", level=.05)
        wave(self.player / "Music" / "loud.wav", level=.4)
        bench = self.tool("workbench")
        bench.kind.set("WAV files")
        bench.kind_changed()
        self.assertEqual(bench.files.winfo_manager(), "pack")
        self.assertEqual(bench.render_button.winfo_manager(), "")       # nothing to render: the list has its own button
        self.assertEqual(bench.files.list.size(), 2)
        bench.files.list.selection_set(0)
        bench.files.use()
        self.assertIn("at least two", bench.status.get())
        bench.files.list.selection_set(0, 1)
        bench.files.use()
        self.assertEqual(set(bench.auditions), {"A", "B"})
        self.assertEqual({row["name"] for row in bench.record["auditions"].values()}, {"quiet", "loud"})

    def test_the_pod_backup_is_chosen_from_where_pod_go_edit_writes_them(self):
        from test_lab_podgo import time_preset, write_backup

        backups = self.player / "Documents" / "Line 6" / "POD Go" / "Backups"
        backups.mkdir(parents=True)
        write_backup(backups, {"User": [("Time", time_preset())]})
        tool = self.tool("pod")
        self.assertEqual(tool.backup.winfo_manager(), "pack")           # none chosen yet: the choice is on show
        self.assertEqual(tool.files.list.get(0), "backup.pgb  ·  Backups")
        tool.files.path.insert(0, str(backups / "other.pgb"))
        tool.files.typed()
        self.assertIn("There is no file at", tool.files.said.cget("text"))
        tool.files.list.selection_set(0)
        tool.files.use()
        self.assertEqual(tool.list.size(), 1)
        self.assertEqual(json.loads((self.home / "podgo.json").read_text(encoding="utf-8"))["backup"],
                         str((backups / "backup.pgb").resolve()))
        self.assertEqual(tool.backup.winfo_manager(), "")

    def test_the_spotify_export_is_chosen_from_downloads_and_read_away_from_the_window(self):
        rows = [{"ts": f"2026-0{m}-0{d}T20:00:00Z", "ms_played": 200_000, "master_metadata_track_name": "Song One",
                 "master_metadata_album_artist_name": "Band A", "master_metadata_album_album_name": "Album",
                 "reason_start": "clickrow", "reason_end": "trackdone", "shuffle": False, "skipped": False}
                for m in range(1, 4) for d in range(1, 4)]
        downloads = self.player / "Downloads"
        downloads.mkdir()
        with zipfile.ZipFile(downloads / "my_spotify_data.zip", "w") as archive:
            archive.writestr("Streaming_History_Audio_2026.json", json.dumps(rows))
        self.studio.store.save(Sound("Song One lead", chain(PLEXI, V30), {"kind": "designed"}))
        tool = self.tool("listening")
        self.assertEqual(tool.export.winfo_manager(), "pack")
        self.assertEqual(tool.files.list.get(0), "my_spotify_data.zip  ·  Downloads")
        tool.files.list.selection_set(0)
        tool.files.use()
        self.assertIsNotNone(tool.reading)                      # being read on another thread, not in the click
        self.assertEqual(tool.status.get(), "Reading the export…")
        self.pump()
        self.assertIsNone(tool.reading)
        self.assertIn("Band A – Song One", tool.list.get(0))
        self.assertIn("chosen 9×", tool.list.get(0))
        self.assertIn("preset", tool.list.get(0))
        self.assertTrue((self.home / "personal" / "listening.json").exists())
        self.assertTrue((self.home / "personal" / "listening.md").exists())
        self.assertEqual(tool.export.winfo_manager(), "")
        tool.list.selection_set(0)
        tool.describe()
        self.assertIn("Chosen 9 times", tool.note.cget("text"))
        tool.start()
        self.assertEqual(self.studio.state.song, "Band A - Song One")

    def test_an_export_that_does_not_record_how_a_play_started_says_so(self):
        from lab.tools import listening

        song = {"artist": "Rush", "title": "Limelight", "chosen": None, "days": 3, "hours": 1.5}
        self.assertEqual(listening.chosen_words(song), "chosen: not recorded")
        self.assertEqual(listening.chosen_words({**song, "chosen": 4}), "chosen 4×")
        tool = self.tool("listening")
        tool.show({"summary": {"songs": [song], "hours": 2, "years": {2026: 2}}, "guitar_context": None})
        self.assertIn("(chosen: not recorded, 3 days)", tool.list.get(0))
        tool.list.selection_set(0)
        tool.describe()
        self.assertIn("How often it was chosen is not recorded in this export", tool.note.cget("text"))


if __name__ == "__main__":
    unittest.main()
