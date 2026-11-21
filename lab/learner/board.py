"""The guitar's controls for labelling dry recordings: a photograph of the Charvel with its controls marked,
two dials, the pickup blade and the mini-toggle.

It is drawn for the width of the desk's side panel, where the Guitar Learner is shown, in the desk's colours
and type (`lab.desk.theme`). The photograph is the part of the catalog picture that holds the controls, at
its own scale, so the marks on it are as large as they were.

Everything is set with the mouse: a dial is dragged or scrolled over, a mark on the photograph is dragged
(a knob) or clicked (the blade, the toggle), and the blade and the toggle have buttons of their own. Nothing is
bound to a key.
"""

import math
from pathlib import Path
import tkinter as tk

from lab.desk import theme
from lab.learner.dataset import LAYOUTS
from lab.learner.report import setting

FONT = theme.FONT
BG = theme.PAPER
PANEL = theme.DARK
SURFACE = theme.TILE
LINE = theme.RULE
INK = theme.INK
MUTED = theme.SUBTLE
DIM = theme.DIM
ACCENT = theme.ACCENT
ACTIVE_INK = theme.ACCENT_INK
ALERT = theme.SOFT
WIDTH = 380         # what fits across the desk's side panel (`lab.tools.common.WIDTH`)
LAYOUTS_SHOWN = ("dk24_hh_toggle", "dk24_blade")
LAYOUT_LABELS = ("Blade + toggle", "Blade only")
# Each layout's photograph, and where its controls are on it (in the whole picture's pixels).
PHOTO = {
    "dk24_hh_toggle": ("charvel-dk24-hh.png", {"volume": (165, 260), "tone": (118, 312),
                                              "pickup": (192, 276), "wiring": (129, 274)}),
    "dk24_blade": ("charvel-dk24-blade.png", {"volume": (184, 262), "tone": (132, 318),
                                             "pickup": (212, 280)}),
}
# The part of the picture shown: the body's lower half, with the pickups and every control (left, top, right,
# bottom).
SHOWN = (0, 100, WIDTH, 356)


def word(parent, title=None, *, variable=None, size=10, color=INK, bold=False, wrap=0):
    return tk.Label(parent, text=title, textvariable=variable, anchor="w", justify="left",
                    font=(FONT, size, "bold" if bold else "normal"), wraplength=wrap,
                    background=parent["background"], foreground=color)


def button(parent, title, command, *, primary=False, compact=False):
    return tk.Button(parent, text=title, command=command, cursor="hand2",
                     background=ACCENT if primary else SURFACE, foreground=ACTIVE_INK if primary else INK,
                     activebackground=theme.mix(ACCENT, "#ffffff", .6) if primary else LINE,
                     activeforeground=ACTIVE_INK if primary else INK,
                     disabledforeground=DIM, borderwidth=0, relief="flat", padx=8 if compact else 12,
                     pady=4 if compact else 7, font=(FONT, 9 if compact else 10, "bold"))


def mark_button(control, selected):
    control.configure(background=ACCENT if selected else SURFACE, foreground=ACTIVE_INK if selected else INK)


def defaults(layout):
    value = {"pickup": 5, "tone": 10.0, "volume": 10.0}
    if layout == "dk24_hh_toggle":
        value["wiring"] = "Parallel"
    return value


class Dial(tk.Canvas):
    """A knob with physical 0–10 markings, turned by dragging it or by the wheel over it."""

    def __init__(self, parent, name, turn):
        super().__init__(parent, width=104, height=124, background=PANEL, borderwidth=0, highlightthickness=0,
                         cursor="hand2")
        self.name, self.turn = name, turn
        self.value = 10.0
        self.drag_start = None
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<MouseWheel>", lambda event: self.nudge(.5 if event.delta > 0 else -.5))
        self.paint(self.value)

    def press(self, event):
        self.drag_start = event.x_root, event.y_root, self.value

    def release(self, _):
        self.drag_start = None

    def drag(self, event):
        if self.drag_start:
            x, y, value = self.drag_start
            self.turn(value + (y - event.y_root + event.x_root - x) / 9)

    def nudge(self, step):
        self.turn(self.value + step)
        return "break"

    def paint(self, value):
        self.value = value
        self.delete("all")
        x, y = 52, 70
        self.create_text(x, 10, text=self.name.upper(), fill=INK, font=(FONT, 9, "bold"))
        self.create_oval(x-28, y-24, x+28, y+30, fill="#111216", outline="")
        for n in range(11):
            angle = math.radians(140 + 26 * n)
            dx, dy = math.cos(angle), math.sin(angle)
            inner = 34 if n % 5 == 0 else 35
            self.create_line(x+inner*dx, y+inner*dy, x+39*dx, y+39*dy,
                             fill=ACCENT if n <= value else LINE, width=1)
            if n % 5 == 0:
                self.create_text(x+47*dx, y+46*dy, text=str(n), fill=MUTED, font=(FONT, 7))
        self.create_oval(x-28, y-28, x+28, y+28, fill="#988d7e", outline="#d0c3b2")
        for n in range(34):
            angle = n * math.tau / 34
            dx, dy = math.cos(angle), math.sin(angle)
            self.create_line(x+23*dx, y+23*dy, x+27*dx, y+27*dy, fill="#c4b7a4", width=1)
        self.create_oval(x-23, y-23, x+23, y+23, fill="#e4d9c9", outline="#f3e8d6")
        angle = math.radians(140 + 26 * value)
        dx, dy = math.cos(angle), math.sin(angle)
        self.create_line(x+14*dx, y+14*dy, x+21*dx, y+21*dy, fill="#3f493e", width=3, capstyle="round")
        self.create_text(x, y, text=setting(value), fill="#282726", font=(FONT, 15, "bold"))


class GuitarBoard(tk.Frame):
    def __init__(self, parent, on_layout, changed):
        super().__init__(parent, background=PANEL)
        self.on_layout, self.changed = on_layout, changed
        self.layout = None
        self.values = defaults(LAYOUTS_SHOWN[0])
        self.photo_drag = None
        self.hotspots = {}
        self.images = {layout: self.part_shown(filename) for layout, (filename, _) in PHOTO.items()}
        head = tk.Frame(self, background=PANEL)
        head.pack(fill="x")
        word(head, "Charvel DK24", size=9, bold=True).pack(side="left")
        self.layout_buttons = []
        for key, label in reversed(tuple(zip(LAYOUTS_SHOWN, LAYOUT_LABELS))):
            btn = button(head, label, lambda picked=key: self.on_layout(picked), compact=True)
            btn.pack(side="right", padx=(4, 0))
            self.layout_buttons.append((key, btn))
        left, top, right, bottom = SHOWN
        self.art = tk.Canvas(self, background=PANEL, highlightthickness=0, width=right - left, height=bottom - top,
                             borderwidth=0)
        self.art.pack(pady=(6, 1))
        self.art.bind("<Configure>", lambda _: self.draw_photo())
        self.art.bind("<Motion>", lambda event: self.art.configure(cursor="hand2" if self.point_at(event) else ""))
        self.art.bind("<ButtonPress-1>", self.press_photo)
        self.art.bind("<B1-Motion>", self.drag_photo)
        self.art.bind("<ButtonRelease-1>", lambda _: self.end_photo())

        dock = tk.Frame(self, background=PANEL)
        dock.pack(fill="x", pady=(2, 0))
        self.dials = {}
        for key in ("volume", "tone"):
            dial = Dial(dock, key, lambda value, name=key: self.set_knob(name, value))
            dial.pack(side="left", padx=(0, 6))
            self.dials[key] = dial
        beside = tk.Frame(dock, background=PANEL)        # right of the knobs: the mini-toggle, and the knobs to 10
        beside.pack(side="left", anchor="n", fill="y", padx=(8, 0))
        self.full_button = button(beside, "Knobs to 10", self.knobs_full, compact=True)
        self.full_button.pack(side="bottom", anchor="w", pady=(0, 6))
        self.toggle = tk.Frame(beside, background=PANEL)
        self.toggle_buttons = []
        word(self.toggle, "MINI-TOGGLE", size=9, bold=True).pack(anchor="w", pady=(5, 3))
        for wiring, title in (("Parallel", "↑  Parallel"), ("Series", "↓  Series")):
            switch = button(self.toggle, title, lambda value=wiring: self.set("wiring", value), compact=True)
            switch.pack(fill="x", pady=1)
            self.toggle_buttons.append((wiring, switch))
        pickup = tk.Frame(self, background=PANEL)
        pickup.pack(fill="x", pady=(0, 4))
        word(pickup, "PICKUP BLADE", size=9, bold=True).pack(side="left")
        blade = tk.Frame(pickup, background=BG, padx=3, pady=3)
        blade.pack(side="left", padx=8)
        self.positions = []
        for n in range(1, 6):
            switch = button(blade, str(n), lambda index=n: self.set("pickup", index), compact=True)
            switch.pack(side="left", padx=2)
            self.positions.append(switch)
        self.pickup_name = tk.StringVar(master=self)
        word(pickup, variable=self.pickup_name, color=MUTED, size=9).pack(side="left")
        self.refresh()

    def part_shown(self, filename):
        """The part of a layout's photograph that holds the controls (`SHOWN`), as an image of its own."""
        whole = tk.PhotoImage(master=self, file=str(Path(__file__).with_name("assets") / filename))
        left, top, right, bottom = SHOWN
        part = tk.PhotoImage(master=self)
        self.tk.call(part, "copy", whole, "-from", left, top, min(right, whole.width()), min(bottom, whole.height()))
        return part

    def show(self, layout, values=None):
        self.layout = layout
        self.values = dict(values or defaults(layout or LAYOUTS_SHOWN[0]))
        self.refresh()

    def set_enabled(self, enabled):
        for _, widget in self.layout_buttons:
            widget.configure(state="normal" if enabled else "disabled")

    def set_knob(self, key, value):
        self.set(key, max(0., min(10., round(value * 2) / 2)))

    def knobs_full(self):
        """Both knobs to 10, as most takes are played (a double click on a knob did this, with nothing to say so)."""
        if all(self.values[key] == 10. for key in self.dials):
            return
        for key in self.dials:
            self.values[key] = 10.
        self.refresh()
        self.changed()

    def set(self, key, value):
        if self.values.get(key) == value:
            return
        self.values[key] = value
        self.refresh()
        self.changed()

    def refresh(self):
        for key, widget in self.layout_buttons:
            mark_button(widget, key == self.layout)
        for key, dial in self.dials.items():
            dial.paint(self.values[key])
        layout = LAYOUTS[self.layout or LAYOUTS_SHOWN[0]]
        pickup = self.values["pickup"]
        self.pickup_name.set(f"{pickup} · {layout['pickups'][pickup - 1]}")
        for index, widget in enumerate(self.positions, 1):
            mark_button(widget, index == pickup)
        if "wiring" in self.values:
            if not self.toggle.winfo_manager():
                self.toggle.pack(side="top", anchor="w")
            for key, widget in self.toggle_buttons:
                mark_button(widget, key == self.values["wiring"])
        elif self.toggle.winfo_manager():
            self.toggle.pack_forget()
        self.draw_photo()

    def draw_photo(self):
        canvas = self.art
        canvas.delete("all")
        if canvas.winfo_width() <= 1:
            return
        layout = self.layout or LAYOUTS_SHOWN[0]
        photo = self.images[layout]
        left, top = SHOWN[:2]
        x = max(0, (canvas.winfo_width() - photo.width()) / 2)
        y = max(0, (canvas.winfo_height() - photo.height()) / 2)
        canvas.create_image(x, y, anchor="nw", image=photo)
        canvas.create_text(canvas.winfo_width()-7, canvas.winfo_height()-6, anchor="se",
                           text="Charvel catalog reference", fill=DIM, font=(FONT, 8))
        self.hotspots = {key: (x + px - left, y + py - top) for key, (px, py) in PHOTO[layout][1].items()}
        for key, (a, b) in self.hotspots.items():
            radius = 13 if key in self.dials else 11
            canvas.create_oval(a-radius, b-radius, a+radius, b+radius, outline=ACCENT, width=1)
            if key in self.dials:
                angle = math.radians(140 + 26 * self.values[key])
                canvas.create_line(a+radius*math.cos(angle), b+radius*math.sin(angle),
                                   a+(radius+5)*math.cos(angle), b+(radius+5)*math.sin(angle),
                                   fill=ACCENT, width=2)

    def point_at(self, event):
        distances = {key: math.hypot(x - event.x, y - event.y) for key, (x, y) in self.hotspots.items()}
        if distances:
            key = min(distances, key=distances.get)
            return key if distances[key] <= 17 else None

    def press_photo(self, event):
        key = self.point_at(event)
        if key in self.dials:
            self.photo_drag = key, event.x_root, event.y_root, self.values[key]
        elif key == "pickup":
            self.set("pickup", self.values["pickup"] % 5 + 1)
        elif key == "wiring":
            self.set("wiring", "Parallel" if self.values["wiring"] == "Series" else "Series")

    def drag_photo(self, event):
        if self.photo_drag:
            knob, x, y, value = self.photo_drag
            self.set_knob(knob, value + (y - event.y_root + event.x_root - x) / 9)

    def end_photo(self):
        self.photo_drag = None
