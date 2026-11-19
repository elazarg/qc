"""Songs you listen to, as songs to build sounds for.

Reads a Spotify account export (Spotify → Privacy → Download your data;
the files are `StreamingHistory_music_*.json` or the extended
`Streaming_History_Audio_*.json`, or the zip) with `lab.spotify`, and lists
the songs by time listened, leaving out those already in the player's songs.
**Start this song** names the song on the desk; **Make it** asks for a round
for it (one call to the assistant).

The export's path is remembered in the data folder.
"""

import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

from .common import Window, button, label, listbox

REMEMBERED = "spotify.json"


class ListeningWindow(Window):
    TITLE = "Songs you listen to"
    SIZE = "560x600"

    def build(self):
        row = tk.Frame(self.body, bg=self.body["bg"])
        row.pack(fill="x")
        button(row, "Choose the export…", self.choose).pack(side="left")
        self.where = label(row, size=9, wrap=360)
        self.where.pack(side="left", padx=10)
        self.list = listbox(self.body, height=24)
        self.list.pack(fill="both", expand=True, pady=(10, 6))
        actions = tk.Frame(self.body, bg=self.body["bg"])
        actions.pack(fill="x")
        button(actions, "Start this song", self.start).pack(side="left")
        button(actions, "Make it", self.make).pack(side="left", padx=6)
        self.tracks = []
        paths = self.remembered()
        if paths and all(Path(path).exists() for path in paths):
            self.load(paths)
        else:
            self.say("Choose your Spotify export: the streaming-history JSON files, or the zip.")

    def remembered(self):
        try:
            return json.loads((self.folder / REMEMBERED).read_text(encoding="utf-8")).get("paths")
        except (OSError, ValueError):
            return None

    def choose(self):
        paths = filedialog.askopenfilenames(parent=self.top, title="Spotify export",
                                            filetypes=(("Spotify export", "*.json *.zip"),))
        if paths:
            (self.folder / REMEMBERED).write_text(json.dumps({"paths": list(paths)}), encoding="utf-8")
            self.load(list(paths))

    def load(self, paths):
        from .. import spotify

        try:
            self.tracks = spotify.proposals(paths, store=self.studio.store, limit=60)
        except (OSError, ValueError) as error:
            self.say(str(error))
            return
        self.where.configure(text=", ".join(Path(path).name for path in paths)[:80])
        self.list.delete(0, "end")
        for track in self.tracks:
            self.list.insert("end", track.text)
        self.say(f"{len(self.tracks)} songs, most listened first; songs you already have are left out.")

    def chosen(self):
        picked = self.list.curselection()
        return self.tracks[picked[0]] if picked else None

    def start(self):
        track = self.chosen()
        if track is not None:
            self.studio.set(song=f"{track.artist} - {track.title}")
            self.say(f"Working on {track.artist} – {track.title}. Its parts collect in the desk's Song panel.")

    def make(self):
        track = self.chosen()
        if track is not None:
            self.start()
            self.studio.new_round(f"make {track.artist} - {track.title}")


def open(desk):  # noqa: A001 - every tool opens with `open(desk)`
    return ListeningWindow.open(desk)
