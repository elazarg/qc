"""A tone-oriented browser for the Quad Cortex assets relevant to an audition."""

import tkinter as tk

from . import qc_library


class Browser(tk.Frame):
    def __init__(self, parent, desk):
        from .listening_room import DARK, INK, PAPER, RULE, SUBTLE, control, menu, text

        super().__init__(parent, bg=PAPER)
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
        head = tk.Frame(self, bg=PAPER)
        head.pack(fill="x", pady=(0, 6))
        for name in ("Presets", "Captures", "IRs"):
            control(head, name, lambda choice=name: self.change(choice)).pack(side="left", padx=(0, 3))
        field = tk.Entry(head, textvariable=self.query, bg=DARK, fg=INK, insertbackground=INK,
                         relief="flat", highlightthickness=1, highlightbackground=RULE)
        field.pack(side="left", fill="x", expand=True, padx=7, ipady=6)
        self.refresh = control(head, "Refresh QC", self.read)
        self.refresh.pack(side="right")
        text(self, variable=self.summary, size=9, color=SUBTLE).pack(fill="x", pady=(0, 4))
        body = tk.Frame(self, bg=DARK)
        body.pack(fill="both", expand=True)
        self.list = tk.Listbox(body, bg=DARK, fg=INK, selectbackground="#3e4c68", selectforeground=INK,
                               font=("Segoe UI", 11), borderwidth=0, highlightthickness=1,
                               highlightcolor=RULE, highlightbackground=RULE, exportselection=False)
        self.list.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(body, orient="vertical", command=self.list.yview)
        scroll.pack(side="right", fill="y")
        self.list.configure(yscrollcommand=scroll.set)
        self.list.bind("<<ListboxSelect>>", lambda _: self.sync())
        self.list.bind("<Double-Button-1>", lambda _: self.apply())
        self.buttons = tk.Frame(self, bg=PAPER)
        self.buttons.pack(fill="x", pady=(6, 0))
        self.use = control(self.buttons, "Use as B", self.apply, primary=True)
        self.use.pack(side="left", padx=(0, 4))
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
        text(self, variable=self.detail, color=SUBTLE, size=9, wrap=800).pack(fill="x", pady=(4, 0))
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
        for button in (self.reference, self.scene_menu, self.version_menu, self.cab_menu):
            button.pack_forget()
        if kind == "Presets":
            self.reference.pack(side="left", padx=(0, 4))
            self.scene_menu.pack(side="left")
        elif kind == "Captures":
            self.version_menu.pack(side="left", padx=(0, 4))
            self.cab_menu.pack(side="left")
        self.use.configure(text={"Presets": "Use as B", "Captures": "Try capture", "IRs": "Try IR"}[kind])
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
            all_rows = sorted(all_rows, key=lambda row: (row.setlist != qc_library.SETLIST_NAME, row.setlist, row.index))
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
        self.sync()

    def item(self):
        index = self.list.curselection()
        return self.selected[index[0]] if index else None

    def sync(self):
        row = self.item()
        kind = self.kind.get()
        ready = self.desk.job is None
        choice = row is not None
        if kind == "Captures" and row is not None:
            choice = (self.cab.get() != "Cabinet in capture?"
                      and (row.capture_version is not None or self.version.get() != "Capture version"))
        self.use.configure(state="normal" if choice and ready else "disabled")
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

    def apply(self):
        if self.use.cget("state") != "normal":
            return
        row = self.item()
        if self.kind.get() == "Presets":
            self.desk.use_as_tone(row, self.scene_index())
        else:
            version = None if self.version.get() == "Capture version" else int(self.version.get()[-1])
            self.desk.use_asset(row, capture_has_cab=self.cab.get() == "Amp + cabinet", capture_version=version)
