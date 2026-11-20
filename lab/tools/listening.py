"""Songs you listen to, as songs to build sounds for.

Reads a Spotify account export (Spotify → Privacy → Download your data; the
zip, or the `Streaming_History_Audio_*.json` / `StreamingHistory_music_*.json`
files) with `lab.listening`, and lists the songs you chose and came back to
most, which says more than time alone. Songs that already have a preset or a
sound of yours are marked. **Ask about the artists** asks the assistant once
what kind of guitar music the most listened artists make; its answer is kept
and shown, and the designer uses it as a background for open requests.
**Start this song** names the song on the desk; **Make it** asks for a round.

Everything this window works out is personal: it is kept in the data
folder's `personal` folder, never in the project. The export's path is
remembered there too.
"""

import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

from .common import Window, button, label, listbox

REMEMBERED = "spotify.json"


def personal(folder):
    path = Path(folder) / "personal"
    path.mkdir(parents=True, exist_ok=True)
    return path


def saved_analysis(folder):
    try:
        return json.loads((personal(folder) / "listening.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class ListeningWindow(Window):
    TITLE = "Songs you listen to"
    SIZE = "600x640"

    def build(self):
        row = tk.Frame(self.body, bg=self.body["bg"])
        row.pack(fill="x")
        button(row, "Choose the export…", self.choose).pack(side="left")
        button(row, "Ask about the artists", self.ask_about_artists).pack(side="left", padx=6)
        self.where = label(row, size=9, wrap=260)
        self.where.pack(side="left", padx=10)
        self.list = listbox(self.body, height=20)
        self.list.pack(fill="both", expand=True, pady=(10, 6))
        self.list.bind("<<ListboxSelect>>", lambda _: self.describe())
        self.note = label(self.body, size=9, wrap=560)
        self.note.pack(fill="x")
        actions = tk.Frame(self.body, bg=self.body["bg"])
        actions.pack(fill="x", pady=(6, 0))
        button(actions, "Start this song", self.start).pack(side="left")
        button(actions, "Make it", self.make).pack(side="left", padx=6)
        self.songs, self.context = [], {}
        saved = saved_analysis(self.folder)
        if saved:
            self.show(saved)
        paths = self.remembered()
        if not saved and paths and all(Path(path).exists() for path in paths):
            self.load(paths)
        elif not saved:
            self.say("Choose your Spotify export: the zip, or its streaming-history files.")

    def remembered(self):
        try:
            return json.loads((personal(self.folder) / REMEMBERED).read_text(encoding="utf-8")).get("paths")
        except (OSError, ValueError):
            return None

    def choose(self):
        paths = filedialog.askopenfilenames(parent=self.top, title="Spotify export",
                                            filetypes=(("Spotify export", "*.zip *.json"),))
        if paths:
            (personal(self.folder) / REMEMBERED).write_text(json.dumps({"paths": list(paths)}), encoding="utf-8")
            self.load(list(paths))

    def preset_names(self):
        names = {sound.name for sound in self.studio.store.sounds()}
        try:
            from ..library import cached

            names |= {entry["name"] for entry in cached(self.folder) or []}
        except ImportError:
            pass
        return sorted(names)

    def load(self, paths):
        from .. import listening

        try:
            summary = listening.summarize(listening.plays(paths))
        except (OSError, ValueError) as error:
            self.say(str(error))
            return
        record = {"summary": {**summary, "songs": listening.with_presets(summary["songs"], self.preset_names())},
                  "guitar_context": (saved_analysis(self.folder) or {}).get("guitar_context")}
        self.save(record)
        self.show(record)

    def save(self, record):
        from .. import listening

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
            self.list.insert("end", f"{song['artist']} – {song['title']}  ({song['chosen']}× on {song['days']} days){mark}")
        self.where.configure(text=f"{summary['hours']} hours over {len(summary['years'])} years")
        self.say("Most chosen and returned to first. Select one for what is known about it.")

    def chosen(self):
        picked = self.list.curselection()
        return self.songs[picked[0]] if picked else None

    def describe(self):
        song = self.chosen()
        if song is None:
            return
        parts = [f"Chosen {song['chosen']} times on {song['days']} days, {song['hours']} hours."]
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
        from .. import listening

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
            self.say(f"Working on {song['artist']} – {song['title']}. Its parts collect in the desk's Song panel.")

    def make(self):
        song = self.chosen()
        if song is not None:
            self.start()
            self.studio.new_round(f"make {song['artist']} - {song['title']}")


def open(desk):  # noqa: A001 - every tool opens with `open(desk)`
    return ListeningWindow.open(desk)
