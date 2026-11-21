"""Songs you listen to, on a made-up export: ranked by choice, presets marked, results kept in personal/.

Choosing the export from the list of the ones found is in test_lab_tools_choices.py; here its path is typed in.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import json
import threading
import unittest
import zipfile

from lab.sound.sounds import Sound
from lab.tools import listening

from support import PLEXI, V30, DeskCase, chain

ROWS = [{"ts": f"2026-0{m}-0{d}T20:00:00Z", "ms_played": 200_000, "master_metadata_track_name": "Song One",
         "master_metadata_album_artist_name": "Band A", "master_metadata_album_album_name": "Album",
         "reason_start": "clickrow", "reason_end": "trackdone", "shuffle": False, "skipped": False}
        for m in range(1, 4) for d in range(1, 4)]


class SongsTest(DeskCase):
    def setUp(self):
        super().setUp()
        self.export = self.player / "elsewhere" / "my_spotify_data_2026.zip"
        self.export.parent.mkdir(parents=True)
        with zipfile.ZipFile(self.export, "w") as archive:
            archive.writestr("Streaming_History_Audio_2026.json", json.dumps(ROWS))
        self.studio.store.save(Sound("Song One lead", chain(PLEXI, V30), {"kind": "designed"}))

    def test_songs_are_ranked_marked_and_kept_in_the_personal_folder(self):
        tool = self.tool("listening")
        other = self.export.with_name("holiday.zip")
        other.write_bytes(b"x")
        tool.files.path.insert(0, str(other))               # not an export by its name: refused, and why
        tool.files.typed()
        self.assertIn("is not one of", tool.files.said.cget("text"))
        self.assertIsNone(tool.reading)
        tool.files.path.delete(0, "end")
        tool.files.path.insert(0, str(self.export))         # not where exports are looked for: its path, typed
        tool.files.typed()
        self.pump()
        self.assertIn("Band A – Song One", tool.list.get(0))
        self.assertIn("preset", tool.list.get(0))
        self.assertTrue((self.home / "personal" / "listening.json").exists())
        self.assertTrue((self.home / "personal" / "listening.md").exists())
        # Shown again, it reads what it kept, without the export.
        self.export.unlink()
        del self.desk.tools[tool.TITLE]
        again = self.tool("listening")
        self.assertIsNot(again, tool)
        self.assertIn("Band A – Song One", again.list.get(0))

    def test_the_export_is_read_away_from_the_window_s_thread(self):
        seen = []
        real = listening.songs

        def reading(paths, names):
            seen.append(threading.current_thread() is threading.main_thread())
            return real(paths, names)

        with unittest.mock.patch.object(listening, "songs", reading):
            tool = self.tool("listening")
            tool.load([self.export])
            self.assertIsNotNone(tool.reading)
            tool.load([self.export])                        # asked again while it reads: once is enough
            self.pump()
        self.assertEqual(seen, [False])
        self.assertIsNone(tool.reading)

    def test_an_export_that_cannot_be_read_says_so_and_leaves_the_tool_free(self):
        bad = self.player / "elsewhere" / "spotify_bad.zip"
        bad.write_bytes(b"not a zip")
        tool = self.tool("listening")
        tool.load([bad])
        self.pump()
        self.assertIsNone(tool.reading)
        self.assertNotEqual(tool.status.get(), "Reading the export…")
        self.assertEqual(tool.list.size(), 0)

    def test_the_tool_and_the_command_line_rank_an_export_the_same_way(self):
        summary, ranked = listening.songs([self.export], ["Song One lead"])
        self.assertEqual((ranked[0]["title"], ranked[0]["preset"], ranked[0]["chosen"]), ("Song One", "Song One lead", 9))
        self.assertEqual(summary["songs"][0]["title"], "Song One")
        self.assertEqual(listening.preset_names(self.studio.store, self.home), ["Song One lead"])


if __name__ == "__main__":
    unittest.main()
