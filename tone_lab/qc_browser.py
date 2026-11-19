"""A tone-oriented browser for the Quad Cortex assets relevant to an audition."""

import json
import tkinter as tk
from tkinter import ttk

from . import diagnostics, qc_library, session

NOTES = "capture-notes.json"
SETLIST_ORDER = (qc_library.SETLIST_NAME, "My Presets")


def capture_notes():
    """What the player has already told Tone Lab about each capture."""
    try:
        notes = json.loads((session.SESSIONS / NOTES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return notes if isinstance(notes, dict) else {}


def remember_capture(key, version, cabinet):
    notes = capture_notes()
    notes[key] = {"version": version, "cabinet": cabinet}
    try:
        session.write_json(session.SESSIONS / NOTES, notes)
    except OSError:
        diagnostics.LOG.warning("Could not remember the capture details", exc_info=True)


class Browser(tk.Frame):
    def __init__(self, parent, desk):
        from .listening_room import DARK, INK, PAPER, RULE, SUBTLE, control, menu, text

        super().__init__(parent, bg=parent["bg"])
        ground = self["bg"]
        self.desk = desk
        self.library = qc_library.cached()
        self.selected = []
        self.kind = tk.StringVar(master=self, value="Presets")
        self.query = tk.StringVar(master=self)
        self.summary = tk.StringVar(master=self)
        self.detail = tk.StringVar(master=self)
        self.scene = tk.StringVar(master=self, value="Default")
        self.version = tk.StringVar(master=self, value="Capture version")
        self.cab = tk.StringVar(master=self, value="Cabinet in capture?")
        self.notes = capture_notes()
        self.noted = None
        head = tk.Frame(self, bg=ground)
        head.pack(fill="x", pady=(0, 6))
        self.kinds = {}
        for name in ("Presets", "Captures", "IRs"):
            self.kinds[name] = control(head, name, lambda choice=name: self.change(choice))
            self.kinds[name].pack(side="left", padx=(0, 3))
        self.refresh = control(head, "Refresh QC", self.read)
        self.refresh.pack(side="right")
        self.field = field = tk.Entry(self, textvariable=self.query, bg=PAPER, fg=INK, insertbackground=INK,
                                      font=("Segoe UI", 11), relief="flat", highlightthickness=1,
                                      highlightbackground=RULE)
        field.pack(fill="x", pady=(0, 6), ipady=6)
        text(self, variable=self.summary, size=9, color=SUBTLE).pack(fill="x", pady=(0, 4))
        body = tk.Frame(self, bg=PAPER)
        body.pack(fill="both", expand=True)
        self.list = tk.Listbox(body, bg=PAPER, fg=INK, selectbackground="#3e4c68", selectforeground=INK,
                               font=("Segoe UI", 10), borderwidth=0, highlightthickness=1, activestyle="none",
                               highlightcolor=RULE, highlightbackground=RULE, exportselection=False)
        self.list.pack(side="left", fill="both", expand=True)
        from .widgets import theme as dress

        dress(self)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.list.yview,
                               style="ToneLab.Vertical.TScrollbar")
        scroll.pack(side="right", fill="y")
        self.list.configure(yscrollcommand=scroll.set)
        self.list.bind("<<ListboxSelect>>", lambda _: self.selected_row())
        self.list.bind("<Double-Button-1>", lambda _: self.apply())
        self.buttons = tk.Frame(self, bg=ground)
        self.buttons.pack(fill="x", pady=(6, 0))
        self.use = control(self.buttons, "Use as B", self.apply, primary=True)
        self.use.pack(side="left", padx=(0, 4))
        self.use_a = control(self.buttons, "Use as A", lambda: self.apply("a"))
        self.reference = control(self.buttons, "Use as reference", self.use_reference)
        self.scene_menu, scene_menu = menu(self.buttons, "Default scene")
        self.scene_menu.configure(textvariable=self.scene)
        for scene in ("Default", *"ABCDEFGH"):
            scene_menu.add_radiobutton(label=scene, variable=self.scene, value=scene)
        self.version_menu, version_menu = menu(self.buttons, "")
        self.version_menu.configure(textvariable=self.version)
        for version in ("Capture version", "V1", "V2"):
            version_menu.add_radiobutton(label=version, variable=self.version, value=version, command=self.sync)
        self.cab_menu, cab_menu = menu(self.buttons, "")
        self.cab_menu.configure(textvariable=self.cab)
        for cab in ("Cabinet in capture?", "Amp only", "Amp + cabinet"):
            cab_menu.add_radiobutton(label=cab, variable=self.cab, value=cab, command=self.sync)
        text(self, variable=self.detail, color=SUBTLE, size=9, wrap=400).pack(fill="x", pady=(4, 0))
        self.query.trace_add("write", lambda *_: self.filter())
        self.change("Presets")

    def read(self):
        self.desk.submit("library", "Reading preset, capture and IR directories…", qc_library.refresh,
                         done=self.loaded)

    def loaded(self, library):
        self.library = library
        self.desk.set_status("Quad Cortex library updated.")
        self.filter()

    def changed(self):
        self.library = qc_library.cached() or self.library
        self.filter()

    def change(self, kind):
        self.kind.set(kind)
        for button in (self.use_a, self.reference, self.scene_menu, self.version_menu, self.cab_menu):
            button.pack_forget()
        if kind == "Presets":
            self.use_a.pack(side="left", padx=(0, 4))
            self.reference.pack(side="left", padx=(0, 4))
            self.scene_menu.pack(side="left")
        elif kind == "Captures":
            self.version_menu.pack(side="left", padx=(0, 4))
            self.cab_menu.pack(side="left")
        self.use.configure(text={"Presets": "Use as B", "Captures": "Try capture", "IRs": "Try IR"}[kind])
        for name, button in self.kinds.items():
            button.configure(bg="#3e4c68" if name == kind else "#252834")
        self.filter()

    def filter(self):
        self.list.delete(0, tk.END)
        kind = self.kind.get()
        terms = self.query.get().casefold().split()
        if self.library is None:
            self.selected = []
            self.summary.set("Read the Quad Cortex library once to browse your sounds here.")
            self.sync()
            return
        all_rows = {"Presets": self.library.presets, "Captures": self.library.captures, "IRs": self.library.irs}[kind]
        if kind == "Presets":
            def place(row):
                rank = SETLIST_ORDER.index(row.setlist) if row.setlist in SETLIST_ORDER else len(SETLIST_ORDER)
                return rank, row.setlist, row.index

            all_rows = sorted(all_rows, key=place)
        result = []
        for item in all_rows:
            prefix = item.name
            suffix = (f"{item.setlist} {item.slot}" if kind == "Presets" else
                      "Imported" if item.source == "user" else f"Factory V{item.capture_version or '?'}")
            search = f"{prefix} {suffix}".casefold()
            if all(term in search for term in terms):
                result.append(item)
                self.list.insert(tk.END, f"{prefix}  ·  {suffix}")
        self.selected = result
        self.summary.set(f"{len(result)} of {len(all_rows)} on-device {kind.lower()}")
        if result:
            self.list.selection_set(0)
        self.selected_row()

    def selected_row(self):
        """Each capture keeps its own answers. Nothing carries over from the previous one."""
        row = self.item()
        if self.kind.get() == "Captures" and row is not None and row.key != self.noted:
            self.noted = row.key
            note = self.notes.get(row.key, {})
            version = row.capture_version or note.get("version")
            self.version.set(f"V{version}" if version in (1, 2) else "Capture version")
            cabinet = note.get("cabinet")
            self.cab.set("Cabinet in capture?" if cabinet is None else "Amp + cabinet" if cabinet else "Amp only")
        self.sync()

    def item(self):
        index = self.list.curselection()
        return self.selected[index[0]] if index else None

    def sync(self):
        row = self.item()
        kind = self.kind.get()
        ready = self.desk.job is None
        steady = ready or self.desk.task_if_active("monitor")  # A tone can change while the input keeps running.
        choice = row is not None
        if kind == "Captures" and row is not None:
            choice = (self.cab.get() != "Cabinet in capture?"
                      and (row.capture_version is not None or self.version.get() != "Capture version"))
        self.use.configure(state="normal" if choice and steady else "disabled")
        self.use_a.configure(state="normal" if row and steady else "disabled")
        self.reference.configure(state="normal" if row and ready else "disabled")
        self.refresh.configure(state="normal" if ready else "disabled")
        if kind == "Captures" and row is not None:
            version = f"V{row.capture_version}" if row.capture_version else "choose version in the controls above"
            self.detail.set(f"{row.name} · {version}. Specify whether a cabinet is part of the capture.")
        elif kind == "IRs" and row is not None:
            self.detail.set(f"{row.name} · substitutes the speaker and mic response in a new tone version.")
        else:
            self.detail.set("")

    def scene_index(self):
        value = self.scene.get()
        return None if value == "Default" else "ABCDEFGH".index(value)

    def use_reference(self):
        if row := self.item():
            self.desk.use_preset(row, self.scene_index())

    def apply(self, side="b"):
        if self.use.cget("state") != "normal":
            return
        row = self.item()
        if self.kind.get() == "Presets":
            self.desk.use_as_tone(row, self.scene_index(), side=side)
        else:
            version = None if self.version.get() == "Capture version" else int(self.version.get()[-1])
            cabinet = self.cab.get() == "Amp + cabinet"
            if self.kind.get() == "Captures":
                self.notes[row.key] = {"version": version, "cabinet": cabinet}
                remember_capture(row.key, version, cabinet)
            self.desk.use_asset(row, capture_has_cab=cabinet, capture_version=version)
