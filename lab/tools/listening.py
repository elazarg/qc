"""Songs you listen to, as songs to build sounds for.

Reads a Spotify account export (Spotify → Privacy → Download your data; the
zip, or the `Streaming_History_Audio_*.json` / `StreamingHistory_music_*.json`
files) with `lab.player.listening`, and lists the songs you chose and came back to
most, which says more than time alone. Songs that already have a preset or a
sound of yours are marked. **Ask about the artists** asks the assistant once
what kind of guitar music the most listened artists make; its answer is kept
and shown, and the designer uses it as a background for open requests.
**Start this song** names the song on the desk; **Make it** asks for a round.

The export is chosen in place: the zips and history files found in Downloads
and in the data folder's `imports`, or a path typed in. It is read away from
the window, since a long history takes seconds.

Everything this tool works out is personal: it is kept in the data
folder's `personal` folder, never in the project. The export's path is
remembered there too.

`songs` is the one ranking of an export's songs; `python -m lab history`
prints it.
"""

import json
from pathlib import Path
import tkinter as tk

from lab.desk.hints import hint
from lab.desk.theme import SUBTLE
from lab.tools.common import FileChoice, Tool, button, disclosure, listbox, room, said, sized, usual_folders

REMEMBERED = "spotify.json"
EXPORTS = ("my_spotify_data*.zip", "*spotify*.zip", "Streaming_History_Audio_*.json", "StreamingHistory_music_*.json")


def personal(folder):
    path = Path(folder) / "personal"
    path.mkdir(parents=True, exist_ok=True)
    return path


def saved_analysis(folder):
    try:
        return json.loads((personal(folder) / "listening.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def preset_names(store, folder):
    """The names a song may already have a sound under: the player's sounds and songs, and the presets on the unit."""
    from lab.qc.library import cached

    names = {sound.name for sound in store.sounds()} | {song["name"] for song in store.songs() if song.get("name")}
    return sorted(names | {entry["name"] for entry in cached(folder) or []})


def songs(paths, names=(), *, limit=40):
    """(summary, songs) of an export: the songs chosen and come back to most, each marked with the preset or
    sound of the player's it already has (`names`), as the tool lists them."""
    from lab.player import listening

    summary = listening.summarize(listening.plays(paths), limit=limit)
    return summary, listening.with_presets(summary["songs"], names)


def chosen_words(song):
    """How often a song was chosen, in words: an export that does not record how a play started says so."""
    return "chosen: not recorded" if song.get("chosen") is None else f"chosen {song['chosen']}×"


class ListeningTool(Tool):
    TITLE = "Songs you listen to"

    def build(self):
        self.songs, self.context = [], {}
        self.reading = None             # the export being read, away from the window
        self.where = said(self.body, color=SUBTLE, pady=0)
        # The songs of the export read, and what is done with one: there once an export was read.
        self.found = room(self.body)
        self.list = listbox(self.found, height=11)
        self.list.pack(fill="x", pady=(4, 6))
        self.list.bind("<<ListboxSelect>>", lambda _: self.describe())
        self.note = said(self.found, pady=0)
        actions = tk.Frame(self.found, bg=self.body["bg"])
        actions.pack(fill="x", pady=(6, 0))
        button(actions, "Start this song", self.start).pack(side="left")
        button(actions, "Make it", self.make).pack(side="left", padx=6)
        button(actions, "Ask about the artists", self.ask_about_artists, quiet=True).pack(side="left")
        hint(actions, "The songs are in the order you chose and came back to them. Start this song: the sounds you "
                      "keep or save from now on become its parts. Make it: that, and three designs for it. Ask "
                      "about the artists: the assistant says who plays guitar for the artists you listen to most, "
                      "and how they sound (one call, about a minute).").pack(side="left")
        # The export is chosen once; choosing another is shown on request.
        self.export = disclosure(self.body, "The Spotify export",
                                 note="The zip Spotify sends when you ask for your data, or the streaming-history "
                                      "files inside it: click each in the list, or paste a path.")
        self.files = FileChoice(self.export, folders=lambda: usual_folders("spotify", self.folder),
                                patterns=EXPORTS, choose=self.choose, several=True, action="Read this export")
        self.files.pack(fill="x")
        saved = saved_analysis(self.folder)
        if saved:
            self.show(saved)
        paths = self.remembered()
        if not saved and paths and all(Path(path).exists() for path in paths):
            self.load(paths)
        elif not saved:
            self.export.show(True)          # the chooser, open, says what it wants

    def remembered(self):
        try:
            return json.loads((personal(self.folder) / REMEMBERED).read_text(encoding="utf-8")).get("paths")
        except (OSError, ValueError):
            return None

    def choose(self, paths):
        paths = [str(path) for path in paths]
        (personal(self.folder) / REMEMBERED).write_text(json.dumps({"paths": paths}), encoding="utf-8")
        self.load(paths)

    def load(self, paths):
        """Read the export away from the window (a long history takes seconds), then list its songs."""
        if self.reading is not None:
            self.say("Still reading the export.")
            return
        names = preset_names(self.studio.store, self.folder)
        self.say("Reading the export…")
        self.reading = self.studio.reading.submit(songs, paths, names)

        def read(result):
            self.reading = None
            summary, ranked = result
            record = {"summary": {**summary, "songs": ranked},
                      "guitar_context": (saved_analysis(self.folder) or {}).get("guitar_context")}
            self.save(record)
            if self.alive():
                self.export.show(False)
                self.show(record)

        def failed(error):
            self.reading = None
            self.say(f"The export could not be read: {error}")

        self.studio.later(self.reading, read, failed)

    def save(self, record):
        from lab.player import listening

        out = personal(self.folder)
        (out / "listening.json").write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str),
                                            encoding="utf-8")
        (out / "listening.md").write_text("# Listening history (personal)\n\n" + "\n".join(
            listening.report(record["summary"], record.get("guitar_context"), record["summary"]["songs"])) + "\n",
            encoding="utf-8")

    def show(self, record):
        summary = record["summary"]
        self.songs = summary["songs"]
        self.context = {item["artist"].casefold(): item for item in record.get("guitar_context") or []}
        self.list.delete(0, "end")
        for song in self.songs:
            mark = "  · preset" if song.get("preset") else ""
            self.list.insert("end", f"{song['artist']} – {song['title']}  ({chosen_words(song)}, {song['days']} days)"
                                    f"{mark}")
        years = len(summary["years"])
        self.where.configure(text=f"{summary['hours']} hours over {years} year{'s' if years != 1 else ''}")
        sized(self.list, 11)
        self.found.show(bool(self.songs))
        self.say("")

    def chosen(self):
        picked = self.list.curselection()
        if not picked:
            self.say("Choose a song in the list first.")
        return self.songs[picked[0]] if picked else None

    def describe(self):
        picked = self.list.curselection()
        if not picked:
            return
        song = self.songs[picked[0]]
        often = ("How often it was chosen is not recorded in this export" if song.get("chosen") is None
                 else f"Chosen {song['chosen']} times")
        parts = [f"{often}; played on {song['days']} days, {song['hours']} hours."]
        if song.get("both_versions"):
            parts.append("You listen to it live and in the studio: say which version when you ask.")
        if song.get("preset"):
            parts.append(f"You have a preset for it: {song['preset']}.")
        context = self.context.get(song["artist"].casefold())
        if context:
            who = ", ".join(context.get("guitarists", [])[:3])
            parts.append(f"Guitar: {who}. Tones: {', '.join(context.get('tones', []))}. {context.get('note', '')}")
        self.note.configure(text=" ".join(parts))

    def ask_about_artists(self):
        from lab.player import listening

        record = saved_analysis(self.folder)
        if not record:
            self.say("Choose the export first.")
            return
        summary = record["summary"]
        self.say("Asking about the most listened artists. About a minute…")
        future = self.studio.thinking.submit(listening.guitar_context, self.studio.assistant,
                                             summary["artists"][:30], summary["songs"][:40])

        def done(context):
            record["guitar_context"] = context
            self.save(record)
            self.show(record)
            self.say(f"{sum(item['guitar_led'] for item in context)} of {len(context)} artists make guitar-led music.")

        self.studio.later(future, done, lambda error: self.say(str(error)))

    def start(self):
        song = self.chosen()
        if song is not None:
            self.studio.set(song=f"{song['artist']} - {song['title']}")
            self.say(f"Working on {song['artist']} – {song['title']}.")

    def make(self):
        song = self.chosen()
        if song is not None:
            self.start()
            self.studio.new_round(f"make {song['artist']} - {song['title']}")


def open(desk):  # noqa: A001 - every tool opens with `open(desk)`
    return ListeningTool.open(desk)
