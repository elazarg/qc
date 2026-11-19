"""POD Go presets as starting points.

The player's POD Go backup is read once (`lab.podgo`), and each of its presets
is listed with how it came across. A preset whose amps and effects have
counterparts on the Quad Cortex arrives as a chain: **Put on B** to play it.
One that does not arrives as a description of the original chain: **Ask for
this** sends that description to the designer, which makes a round from it
(one call to the assistant).

The backup's path is remembered in the data folder.
"""

import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

from .common import Window, button, label, listbox

REMEMBERED = "podgo.json"


class PodWindow(Window):
    TITLE = "POD Go presets"
    SIZE = "640x600"

    def build(self):
        row = tk.Frame(self.body, bg=self.body["bg"])
        row.pack(fill="x")
        button(row, "Choose a backup…", self.choose).pack(side="left")
        self.where = label(row, size=9, wrap=420)
        self.where.pack(side="left", padx=10)
        self.list = listbox(self.body, height=22)
        self.list.pack(fill="both", expand=True, pady=(10, 6))
        self.list.bind("<<ListboxSelect>>", lambda _: self.describe())
        self.note = label(self.body, size=9, wrap=600)
        self.note.pack(fill="x")
        actions = tk.Frame(self.body, bg=self.body["bg"])
        actions.pack(fill="x", pady=(6, 0))
        button(actions, "Put on B", self.put).pack(side="left")
        button(actions, "Ask for this", self.ask).pack(side="left", padx=6)
        self.items = []
        path = self.remembered()
        if path and Path(path).is_file():
            self.load(path)
        else:
            self.say("Choose your POD Go backup (.pgb): POD Go Edit → Backup, usually under Documents\\Line 6.")

    def remembered(self):
        try:
            return json.loads((self.folder / REMEMBERED).read_text(encoding="utf-8")).get("backup")
        except (OSError, ValueError):
            return None

    def choose(self):
        start = Path.home() / "Documents" / "Line 6" / "POD Go" / "Backups"
        path = filedialog.askopenfilename(parent=self.top, title="POD Go backup", initialdir=str(start),
                                          filetypes=(("POD Go backup", "*.pgb"),))
        if path:
            (self.folder / REMEMBERED).write_text(json.dumps({"backup": path}), encoding="utf-8")
            self.load(path)

    def load(self, path):
        from .. import podgo

        try:
            self.items = podgo.import_backup(path)
        except (OSError, ValueError) as error:
            self.say(str(error))
            return
        self.where.configure(text=Path(path).name)
        self.list.delete(0, "end")
        for item in self.items:
            self.list.insert("end", f"{item.slot}  {item.name}" + ("" if item.confident else "  · as a request"))
        chains = sum(item.confident for item in self.items)
        self.say(f"{len(self.items)} presets: {chains} came across as chains, {len(self.items) - chains} as "
                 "descriptions to design from.")

    def chosen(self):
        picked = self.list.curselection()
        return self.items[picked[0]] if picked else None

    def describe(self):
        item = self.chosen()
        if item is None:
            return
        text = item.sound.explanation if item.confident else item.description
        if item.problems:
            text += "\n\nNot a chain because: " + "; ".join(item.problems[:3])
        self.note.configure(text=text[:900])

    def put(self):
        item = self.chosen()
        if item is None:
            return
        if not item.confident:
            self.say(f"{item.name} has no chain of its own here; Ask for this designs one from it.")
            return
        self.studio.put("b", item.sound)
        self.say(f"{item.name} is on B.")

    def ask(self):
        item = self.chosen()
        if item is None:
            return
        self.studio.new_round(item.request)
        self.say(f"Designing from {item.name}. About a minute.")


def open(desk):  # noqa: A001 - every tool opens with `open(desk)`
    return PodWindow.open(desk)
