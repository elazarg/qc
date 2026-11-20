"""The Songs window on a made-up export: ranked by choice, presets marked, results kept in personal/."""

import gc
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import tkinter as tk
import unittest
import zipfile

from lab.tools import listening as window

ROWS = [{"ts": f"2026-0{m}-0{d}T20:00:00Z", "ms_played": 200_000, "master_metadata_track_name": "Song One",
         "master_metadata_album_artist_name": "Band A", "master_metadata_album_album_name": "Album",
         "reason_start": "clickrow", "reason_end": "trackdone", "shuffle": False, "skipped": False}
        for m in range(1, 4) for d in range(1, 4)]


class SongsWindowTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"No display: {error}")
        self.root.withdraw()
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.export = self.folder / "export.zip"
        with zipfile.ZipFile(self.export, "w") as archive:
            archive.writestr("Streaming_History_Audio_2026.json", json.dumps(ROWS))
        store = SimpleNamespace(sounds=lambda: [SimpleNamespace(name="Song One lead")])
        self.studio = SimpleNamespace(folder=self.folder, store=store, set=lambda **_: None)
        self.desk = SimpleNamespace(root=self.root, studio=self.studio)

    def tearDown(self):
        self.root.destroy()
        self.root = None
        gc.collect()

    def test_songs_are_ranked_marked_and_kept_in_the_personal_folder(self):
        songs = window.open(self.desk)
        songs.load([self.export])
        self.assertIn("Band A – Song One", songs.list.get(0))
        self.assertIn("preset", songs.list.get(0))
        self.assertTrue((self.folder / "personal" / "listening.json").exists())
        self.assertTrue((self.folder / "personal" / "listening.md").exists())
        songs.top.destroy()


if __name__ == "__main__":
    unittest.main()
