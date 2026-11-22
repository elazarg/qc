"""POD Go presets as starting points.

The player's POD Go backup is read once (`lab.gear.podgo`), and each of its presets
is listed with how it came across. A preset whose amps and effects have
counterparts on the Quad Cortex arrives as a chain: **Put on B** to play it.
One that does not arrives as a description of the original chain: **Ask for
this** sends that description to the designer, which makes a round from it
(one call to the assistant).

Selecting a preset says what became of each of its blocks: the Quad Cortex
model that stands in, how close a match that is (same gear, same family, same
kind) and why, which settings were set from the POD Go's values, and what was
left out. A chain made this way is a starting point, not a copy.

The backup is chosen in place: the `.pgb` files found where POD Go Edit writes
them (Documents, Line 6, POD Go, Backups), or a path typed in. Its path is
remembered in the data folder.
"""

import json
from pathlib import Path
import tkinter as tk

from lab.desk.hints import hint
from lab.desk.theme import SUBTLE
from lab.tools.common import FileChoice, Tool, button, disclosure, listbox, room, said, sized, usual_folders

REMEMBERED = "podgo.json"


def account(item):
    """What a preset became, in words, as (the summary, and why there is no chain if there is none; each block
    and why, one to a line)."""
    text = item.sound.explanation if item.confident else item.description
    if item.problems:
        text += "\n\nNot a chain because: " + "; ".join(item.problems[:3]) + "."
    return text, "\n".join(f"· {line}" for line in item.lines())


class PodTool(Tool):
    TITLE = "POD Go presets"

    def build(self):
        self.items = []
        self.where = said(self.body, color=SUBTLE, pady=0)
        # The presets of the backup read, and what is done with one: there once a backup was read.
        self.presets = room(self.body)
        self.list = listbox(self.presets, height=9)
        self.list.pack(fill="x", pady=(4, 6))
        self.list.bind("<<ListboxSelect>>", lambda _: self.describe())
        actions = tk.Frame(self.presets, bg=self.body["bg"])
        actions.pack(fill="x", pady=(0, 6))
        button(actions, "Put on B", self.put).pack(side="left")
        button(actions, "Ask for this", self.ask).pack(side="left", padx=6)
        hint(actions, "Put on B: the preset as a chain of the Quad Cortex's own models, where it came across as "
                      "one. Ask for this: the assistant designs three sounds from what the preset "
                      "is.").pack(side="left")
        # What the preset chosen became: in a few words in the open, and block by block on request.
        self.summary = said(self.presets, pady=(0, 4))
        self.blocks = room(self.presets)
        self.note = said(disclosure(self.blocks, "Block by block"), color=SUBTLE, pady=0)
        # The backup is chosen once; choosing another is shown on request.
        self.backup = disclosure(self.body, "The POD Go backup",
                                 note="A .pgb backup, made in POD Go Edit with Backup. Usually under Documents, "
                                      "Line 6, POD Go, Backups.")
        self.files = FileChoice(self.backup, folders=lambda: usual_folders("pod", self.folder), patterns=("*.pgb",),
                                choose=self.choose, action="Read this backup")
        self.files.pack(fill="x")
        path = self.remembered()
        if path and Path(path).is_file():
            self.load(path)
        else:
            self.backup.show(True)
            self.say("Choose your POD Go backup.")

    def remembered(self):
        try:
            return json.loads((self.folder / REMEMBERED).read_text(encoding="utf-8")).get("backup")
        except (OSError, ValueError):
            return None

    def choose(self, path):
        (self.folder / REMEMBERED).write_text(json.dumps({"backup": str(path)}), encoding="utf-8")
        if self.load(path):
            self.backup.show(False)

    def load(self, path):
        """Read a backup and list its presets. False, with the reason said, when it cannot be read."""
        from lab.gear import podgo

        try:
            self.items = podgo.import_backup(path, instruments=self.studio.store.instruments())
        except (OSError, ValueError) as error:
            self.items = []
            self.list.delete(0, "end")
            self.presets.show(False)
            self.backup.show(True)
            self.say(f"{Path(path).name}: {error}")
            return False
        self.where.configure(text=Path(path).name)
        self.presets.show(True)
        self.list.delete(0, "end")
        for item in self.items:
            mark = "" if item.confident else "  · as a request" if item.pieces else "  · could not be read"
            self.list.insert("end", f"{item.slot}  {item.name}{mark}")
        sized(self.list, 9)
        self.describe()
        chains = sum(item.confident for item in self.items)
        unread = sum(not item.pieces for item in self.items)
        self.say(f"{len(self.items)} presets: {chains} as chains, {len(self.items) - chains - unread} "
                 "as requests" + (f", {unread} could not be read." if unread else "."))
        return True

    def chosen(self):
        picked = self.list.curselection()
        if not picked:
            self.say("Choose a preset in the list first.")
        return self.items[picked[0]] if picked else None

    def describe(self):
        picked = self.list.curselection()
        summary, blocks = account(self.items[picked[0]]) if picked else ("", "")
        self.summary.configure(text=summary[:900])
        self.note.configure(text=blocks[:2400])
        self.blocks.show(bool(blocks))

    def put(self):
        item = self.chosen()
        if item is None:
            return
        if not item.confident:
            self.say(f"{item.name} has no chain of its own here; Ask for this designs one from it.")
            return
        # The same preset is the same sound each time it is read: one already in History, with whatever was
        # done to it since, is the one that goes on B.
        try:
            sound = self.studio.store.sound(item.sound.id)
        except (OSError, ValueError):
            sound = item.sound
        self.studio.put("b", sound)
        self.say(f"{item.name} is on B, as a starting point.")

    def ask(self):
        item = self.chosen()
        if item is None:
            return
        if not item.pieces:
            self.say(f"{item.name} could not be read, so there is nothing to describe to the designer.")
            return
        self.studio.new_round(item.request)
        self.say(f"Designing from {item.name}. About a minute.")


def open(desk):  # noqa: A001 - every tool opens with `open(desk)`
    return PodTool.open(desk)
