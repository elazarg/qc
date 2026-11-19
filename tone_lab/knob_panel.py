"""The keyboard- and mouse-playable controls shared by Shape and the Tone field."""

import tkinter as tk

from . import styles, theme
from .live_ui import Knob


COLORS = ("#20232c", "#f7b889", "#f0ede9", "#a5a6b5", "#383c4a", "#484d60", "#2c303c")
UNAVAILABLE = (COLORS[0], "#888394", "#9291a0", "#84839a", COLORS[4], COLORS[5], COLORS[6])
ESSENTIALS = (*styles.PRIMARY_KNOBS, "tightness", "attack", "sustain", "bloom", "band_fit")
RESTING = "Drag, scroll or use the arrow keys. Double-click resets a knob."


class KnobPanel(tk.Frame):
    """A few controls that work on the selected chain, with the rest one step away."""

    COLUMNS = 3
    SHORT = 6

    def __init__(self, parent, desk):
        super().__init__(parent, bg=parent["bg"])
        self.desk = desk
        self.available = None
        self.moves = {}
        self.expanded = False
        self.active = "Response"
        self.shown = ()
        self.current = tk.StringVar(master=self)
        self.hint = tk.StringVar(master=self, value=RESTING)
        surface, accent, light, muted, line, *_ = COLORS
        tk.Label(self, textvariable=self.current, bg=self["bg"], fg=muted, font=(theme.FONT, 9),
                 anchor="w").pack(fill="x", pady=(0, 5))
        self.group_row = tk.Frame(self, bg=self["bg"])
        self.tabs = {}
        for group in styles.KNOB_GROUPS:
            button = tk.Button(self.group_row, text=group, command=lambda mode=group: self.show(mode),
                               bg=surface, fg=light, activebackground=line, activeforeground=light, relief="flat",
                               font=(theme.FONT, 9), borderwidth=0, padx=8, pady=4, takefocus=True)
            button.pack(side="left", padx=(0, 3))
            self.tabs[group] = button
        self.region = tk.Frame(self, bg=self["bg"])
        self.region.pack(fill="x")
        for index in range(self.COLUMNS):
            self.region.columnconfigure(index, weight=1, uniform="controls")
        self.empty = tk.Label(self.region, bg=self["bg"], fg=muted, font=(theme.FONT, 10), justify="left",
                              anchor="w", wraplength=380)
        self.tiles = {}
        self.knobs = {}
        for key, (title, floor, _) in styles.KNOBS.items():
            tile = tk.Frame(self.region, bg=surface, padx=4, pady=4, highlightbackground=line, highlightthickness=1)
            tk.Label(tile, text=title, bg=surface, fg=light, font=(theme.FONT, 10, "bold")).pack()
            knob = Knob(tile, key, floor, self.change, colors=COLORS)
            knob.pack()
            for event in ("<Enter>", "<FocusIn>"):
                knob.bind(event, lambda _, name=key: self.describe(name))
            knob.bind("<Leave>", lambda _: self.hint.set(RESTING))
            knob.bind("<Double-Button-1>", lambda _, dial=knob: dial.turn(0))
            self.tiles[key], self.knobs[key] = tile, knob
        hint = tk.Label(self, textvariable=self.hint, bg=self["bg"], fg=muted, font=(theme.FONT, 9), anchor="w",
                        justify="left", wraplength=400, height=3)
        hint.pack(fill="x", pady=(4, 2))
        self.bind("<Configure>", lambda event: hint.configure(wraplength=max(200, event.width - 12)))
        footer = tk.Frame(self, bg=self["bg"])
        footer.pack(fill="x")

        def small(title, action):
            return tk.Button(footer, text=title, command=action, bg=surface, fg=light, disabledforeground=muted,
                             activebackground=line, activeforeground=light, relief="flat", borderwidth=0,
                             padx=8, pady=3, font=(theme.FONT, 9))

        self.more = small("", self.toggle_more)
        self.more.pack(side="left")
        small("Reset", lambda: desk.set_shape({})).pack(side="right")
        self.compare = small("Hear base", desk.toggle_base)
        self.compare.pack(side="right", padx=4)
        self.expand = small("Add shaping controls", desk.expand_tone)
        self.expand.configure(fg=accent)
        self.show()

    def describe(self, key):
        if self.available is not None and key not in self.available:
            self.hint.set("This direction needs a compatible block in the current tone.")
            return
        blocks = {}
        for model, parameter in self.moves.get(key, ()):
            blocks.setdefault(model, []).append(parameter)
        detail = "\nMoves " + " · ".join(f"{model} {', '.join(names)}" for model, names in blocks.items()) if blocks else ""
        self.hint.set(styles.KNOB_HINTS.get(key, "Drag or use the arrow keys to shape the sound.") + detail)

    def change(self, key):
        dial = self.knobs[key]
        if self.available is not None and key not in self.available:
            dial.set(self.desk.values.get(key, 0))
            self.describe(key)
            return
        values = dict(self.desk.values)
        values[key] = dial.value
        self.desk.touch(key)
        self.desk.set_shape(values)

    def usable(self, keys):
        return [key for key in keys if self.available is None or key in self.available]

    def relevant(self):
        """The controls this player reaches for first on this chain."""
        recent = [key for key in getattr(self.desk, "touched", ()) if key in styles.KNOBS]
        moved = [key for key, value in self.desk.values.items() if value]
        return self.usable(dict.fromkeys((*moved, *recent, *ESSENTIALS, *styles.KNOBS)))[:self.SHORT]

    def toggle_more(self):
        self.expanded = not self.expanded
        self.show()

    def show(self, group=None):
        if group:
            self.active = group
        keys = self.usable(styles.KNOB_GROUPS[self.active]) if self.expanded else self.relevant()
        if self.expanded and not self.group_row.winfo_manager():
            self.group_row.pack(fill="x", pady=(0, 5), before=self.region)
        elif not self.expanded and self.group_row.winfo_manager():
            self.group_row.pack_forget()
        if tuple(keys) != self.shown:
            for tile in self.tiles.values():
                tile.grid_remove()
            self.empty.grid_remove()
            for index, key in enumerate(keys):
                row, column = divmod(index, self.COLUMNS)
                self.tiles[key].grid(row=row, column=column, sticky="ew", padx=(0, 5), pady=(0, 5))
            if not keys:
                self.empty.configure(text=f"This chain has nothing to move for {self.active.lower()}."
                                     if self.expanded else "This tone has no controls Tone Lab can move.")
                self.empty.grid(row=0, column=0, columnspan=self.COLUMNS, sticky="ew", pady=8)
            self.shown = tuple(keys)
        _, accent, light, *_ = COLORS
        for name, button in self.tabs.items():
            button.configure(bg=accent if name == self.active else COLORS[0],
                             fg="#16151a" if name == self.active else light)
        total = len(self.usable(styles.KNOBS))
        rest = total - len(self.shown)
        self.more.configure(text="Fewer" if self.expanded else f"{rest} more" if rest > 0 else "All shown",
                            state="normal" if self.expanded or rest > 0 else "disabled")

    def sync(self, name, values, available, *, expandable=False, moves=None):
        self.current.set(name)
        can_compare = values and self.desk.active_source and self.desk.active_side == self.desk.side
        self.compare.configure(text="Hear shape" if self.desk.base_bypassed else "Hear base",
                               state="normal" if can_compare else "disabled")
        if expandable and not self.expand.winfo_manager():
            self.expand.pack(side="right", padx=4)
        elif not expandable and self.expand.winfo_manager():
            self.expand.pack_forget()
        self.available = available
        self.moves = moves or {}
        for key, knob in self.knobs.items():
            colors = COLORS if available is None or key in available else UNAVAILABLE
            value = values.get(key, 0)
            if knob.colors != colors or knob.value != value:
                knob.colors = colors
                knob.set(value)
        # A knob stays put while it is being turned. The order settles when the panel is next opened.
        if self.expanded or not self.shown or any(key not in self.usable(self.shown) for key in self.shown):
            self.show()

    def refresh(self):
        self.shown = ()
        self.show()
