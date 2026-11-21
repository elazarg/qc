"""The library: presets saved on the Quad Cortex, searched by name, setlist or slot.

Carried over from the old saved-preset panel (`preset_library_ui`), on `lab.qc.library` (`directory(engine)`
reads the unit's directory, `cached(folder)` gives back the last one read, as rows {"slot", "name",
"setlist"}). For a chosen preset:

- **Work on this** brings it to the desk: as saved on A, and a copy to change on B (`Studio.work_on`).
- **Use as reference** plays the desk's phrase through it silently, in a chosen scene, and keeps the return
  under `references/` in the data folder, for Tone search to match against.
- **Leave it on the unit** selects it, and makes it the preset the app gives back when it closes. On the old
  desk "Load on QC" simply recalled it; on the new core the engine borrows the player's preset for the
  session and selects it again at the end, so loading one means changing what is given back.
"""

from datetime import datetime, timezone
import json
import tkinter as tk
from uuid import uuid4

from lab.desk.theme import FONT, INK, SUBTLE, TILE
from lab.qc import library
from lab.qc.library import place
from lab.tools.common import SCENE_LETTERS, Window, button, label, listbox, menu_choice, phrase, render, section

SCENE_CHOICES = ("Default scene", *(f"Scene {letter}" for letter in SCENE_LETTERS))


def text_of(row):
    return f"{row['name']} {row.get('setlist', '')} {place(int(row['slot']['position']))}"


def filtered(rows, query):
    """Rows whose name, setlist and slot hold every word of the query."""
    words = query.casefold().split()
    return [row for row in rows if all(word in text_of(row).casefold() for word in words)]


def ordered(rows):
    return sorted(rows, key=lambda row: (row.get("setlist") != "My Presets", row.get("setlist") or "",
                                         int(row["slot"].get("position", 0))))


def cached(folder):
    """The presets from the last directory read, or none when none has been read."""
    return list(library.cached(folder) or [])


# ---- operations, run where the unit is held ----

def directory(engine, *, stop=None):
    """Every preset in the unit's user setlists, read now."""
    engine.quiet()
    # Kept beside the engine's own records, which is the desk's data folder, for `cached` to read there.
    return list(library.directory(engine, stop=stop, folder=engine.slots.folder))


def capture(engine, slot, dry, scene=None, *, stop=None):
    """A saved preset's return for the phrase, in a scene or its own default, played silently."""
    chain, name = engine.read(slot)
    wet = render(engine, chain, dry, scene=scene, tail=1., stop=stop)
    return {"name": name, "wet": wet, "digest": chain.digest()}


def leave(engine, slot, *, stop=None):
    """Select a saved preset and make it the one given back when the app closes.

    As everywhere the app moves the unit: a preset with unsaved edits (one the player chose and changed on the
    unit meanwhile) is not recalled over."""
    engine.quiet()
    engine.slots.may_leave()
    engine.unit.slot = None
    engine.unit.recall(slot)
    record = {"position": engine.unit.position(), "scene": engine.unit.scene(), "name": engine.unit.name()}
    engine.borrowed = record
    engine.slots.marker.write_text(json.dumps(record), encoding="utf-8")
    engine.shown = None
    return record


def keep_reference(folder, row, scene, captured, phrase_name):
    from lab.hearing.audio import write_wave

    stamp = datetime.now(timezone.utc)
    target = folder / "references" / f"{stamp:%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
    target.mkdir(parents=True)
    write_wave(target / "wet.wav", captured["wet"])
    record = {"name": captured["name"], "setlist": row.get("setlist", ""), "slot": row["slot"],
              "scene": scene, "phrase": phrase_name, "digest": captured["digest"],
              "created": stamp.isoformat(timespec="seconds")}
    (target / "reference.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return target


# ---- the window ----

class LibraryWindow(Window):
    TITLE = "Library"
    SIZE = "560x620"

    def build(self):
        self.rows, self.visible = ordered(cached(self.folder)), []
        top = section(self.body, "Presets on the Quad Cortex", "Filter by preset, setlist or slot (such as 12B).")
        row = tk.Frame(top, bg=top["bg"])
        row.pack(fill="x")
        self.query = tk.StringVar(master=self.top)
        entry = tk.Entry(row, textvariable=self.query, font=(FONT, 11), relief="flat", bg=TILE, fg=INK,
                         insertbackground=INK)
        entry.pack(side="left", fill="x", expand=True, ipady=5)
        button(row, "Refresh", self.refresh, quiet=True).pack(side="right", padx=(6, 0))
        self.query.trace_add("write", lambda *_: self.filter())
        self.box = listbox(self.body, height=16)
        self.box.pack(fill="both", expand=True)
        self.box.bind("<Double-Button-1>", lambda _: self.work_on())
        actions = tk.Frame(self.body, bg=self.body["bg"])
        actions.pack(fill="x", pady=(8, 0))
        button(actions, "Work on this", self.work_on).pack(side="left")
        button(actions, "Use as reference", self.reference, quiet=True).pack(side="left", padx=6)
        self.scene = tk.StringVar(master=self.top, value=SCENE_CHOICES[0])
        menu_choice(actions, self.scene, SCENE_CHOICES).pack(side="left")
        button(actions, "Leave it on the unit", self.leave, quiet=True).pack(side="right")
        self.hint = label(self.body, size=9, color=SUBTLE, wrap=520)
        self.hint.pack(fill="x", pady=(6, 0))
        self.filter()
        if not self.rows:
            self.hint.configure(text="Refresh to read the setlists saved on the Quad Cortex.")

    def filter(self):
        self.visible = filtered(self.rows, self.query.get())
        self.box.delete(0, "end")
        for row in self.visible:
            self.box.insert("end", f"{row['name']}  ·  {row.get('setlist', '')} {place(int(row['slot']['position']))}")

    def selected(self):
        chosen = self.box.curselection()
        return self.visible[chosen[0]] if chosen else None

    def refresh(self):
        def listed(rows):
            self.rows = ordered(rows)
            self.filter()
            self.hint.configure(text=f"{len(rows)} saved presets." if rows else "No saved presets in user setlists.")

        self.call("lab.tools.library.directory", done=listed, working="Reading the presets saved on the Quad Cortex…")

    def work_on(self):
        row = self.selected()
        if row:
            self.studio.work_on(row["slot"], row["name"])

    def reference(self):
        row, taken = self.selected(), phrase(self.studio)
        if row is None:
            return
        if taken is None:
            self.say("Record or choose a phrase on the desk first.")
            return
        choice = self.scene.get()
        scene = None if choice == SCENE_CHOICES[0] else SCENE_LETTERS.index(choice[-1])

        def captured(found):
            keep_reference(self.folder, row, scene, found, taken[0])
            self.hint.configure(text=f"Reference ready · {found['name']}" + (f" · {choice}" if scene is not None else "")
                                     + ". Tone search can match against it.")

        self.call("lab.tools.library.capture", row["slot"], taken[1], scene, done=captured,
                  working=f"Playing the phrase silently through {row['name']}…", renders=True)

    def leave(self):
        row = self.selected()
        if row:
            self.call("lab.tools.library.leave", row["slot"],
                      done=lambda record: self.hint.configure(
                          text=f"{record['name']} is selected and stays selected when the app closes."),
                      working=f"Selecting {row['name']}…")


def open(desk):  # noqa: A001
    return LibraryWindow.open(desk)
