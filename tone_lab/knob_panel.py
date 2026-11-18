"""The keyboard- and mouse-playable controls shared by Shape and the Tone field."""

import tkinter as tk

from . import styles
from .live_ui import Knob


COLORS = ("#20232c", "#f7b889", "#f0ede9", "#a5a6b5", "#383c4a", "#484d60", "#2c303c")
UNAVAILABLE = (COLORS[0], "#888394", "#9291a0", "#84839a", COLORS[4], COLORS[5], COLORS[6])


class KnobPanel(tk.Frame):
    def __init__(self, parent, desk):
        super().__init__(parent, bg=parent["bg"])
        self.desk = desk
        self.available = None
        self.active = "Essentials"
        essentials = (*styles.PRIMARY_KNOBS, "tightness", "attack", "sustain", "bloom", "band_fit")
        self.groups = {"Essentials": essentials, **styles.KNOB_GROUPS}
        self.current = tk.StringVar(master=self)
        self.hint = tk.StringVar(master=self, value="Drag, scroll or use the arrow keys. Double-click to reset a knob.")
        surface, accent, light, muted, line, *_ = COLORS
        heading = tk.Frame(self, bg=self["bg"])
        heading.pack(fill="x", pady=(2, 7))
        tk.Label(heading, textvariable=self.current, bg=self["bg"], fg=light, font=("Segoe UI", 13, "bold"),
                 anchor="w").pack(side="left")
        self.tabs = {}
        for group in reversed(self.groups):
            button = tk.Button(heading, text=group, command=lambda mode=group: self.show(mode),
                               bg=surface, fg=light, activebackground=line, activeforeground=light, relief="flat",
                               font=("Segoe UI", 9), borderwidth=0, padx=8, pady=4, takefocus=True)
            button.pack(side="right", padx=2)
            self.tabs[group] = button
        self.region = tk.Frame(self, bg=self["bg"])
        self.region.pack(fill="x")
        self.tiles = {}
        self.knobs = {}
        for key, (title, floor, _) in styles.KNOBS.items():
            tile = tk.Frame(self.region, bg=surface, padx=4, pady=4, highlightbackground=line, highlightthickness=1)
            tk.Label(tile, text=title, bg=surface, fg=light, font=("Segoe UI", 10, "bold")).pack()
            knob = Knob(tile, key, floor, self.change, colors=COLORS)
            knob.pack()
            for event in ("<Enter>", "<FocusIn>"):
                knob.bind(event, lambda _, name=key: self.describe(name))
            knob.bind("<Double-Button-1>", lambda _, dial=knob: dial.turn(0))
            self.tiles[key], self.knobs[key] = tile, knob
        footer = tk.Frame(self, bg=self["bg"])
        footer.pack(fill="x", pady=(4, 0))
        tk.Label(footer, textvariable=self.hint, bg=self["bg"], fg=muted, font=("Segoe UI", 9), anchor="w").pack(side="left")
        tk.Button(footer, text="Reset shape", command=lambda: desk.set_shape({}), bg=surface, fg=light,
                  activebackground=line, relief="flat", borderwidth=0, padx=8, pady=3).pack(side="right")
        self.compare = tk.Button(footer, text="Hear base", command=desk.toggle_base, bg=surface, fg=light,
                                 disabledforeground=muted, activebackground=line, relief="flat",
                                 borderwidth=0, padx=8, pady=3)
        self.compare.pack(side="right", padx=4)
        self.show(self.active)

    def describe(self, key):
        if self.available is not None and key not in self.available:
            self.hint.set("This direction needs a compatible block in the current tone.")
        else:
            self.hint.set(styles.KNOB_HINTS.get(key, "Drag or use the arrow keys to shape the sound."))

    def change(self, key):
        dial = self.knobs[key]
        if self.available is not None and key not in self.available:
            dial.set(self.desk.values.get(key, 0))
            self.describe(key)
            return
        values = dict(self.desk.values)
        values[key] = dial.value
        self.desk.set_shape(values)

    def show(self, group):
        self.active = group
        for tile in self.tiles.values():
            tile.grid_remove()
        for index in range(5):
            self.region.columnconfigure(index, weight=1, uniform="controls")
        for index, key in enumerate(self.groups[group]):
            row, column = divmod(index, 5)
            self.tiles[key].grid(row=row, column=column, sticky="ew", padx=(0, 6), pady=(0, 4))
        _, accent, light, _, _, _, _ = COLORS
        for name, button in self.tabs.items():
            button.configure(bg=accent if name == group else COLORS[0], fg="#16151a" if name == group else light)

    def sync(self, name, values, available):
        self.current.set(f"Shape  ·  {name}")
        self.compare.configure(text="Hear shape" if self.desk.base_bypassed else "Hear base",
                               state="normal" if values and self.desk.active_source else "disabled")
        self.available = available
        for key, knob in self.knobs.items():
            colors = COLORS if available is None or key in available else UNAVAILABLE
            value = values.get(key, 0)
            if knob.colors != colors or knob.value != value:
                knob.colors = colors
                knob.set(value)
