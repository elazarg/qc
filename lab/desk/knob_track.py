"""One Shape knob drawn as a track: a mark where it rests (the middle for a knob that goes both ways, the left
end for one that goes one way), a bar from there to where it stands in the side's colour, a handle, and the
position in figures. Dragged, it follows the pointer on steps of .05; let go, it says where it ended."""

import tkinter as tk

from lab.desk.theme import DIM, FONT, RULE, SUBTLE

LENGTH = 200        # pixels of the track
FIGURES = 44        # pixels for the position, at the right
HEIGHT = 20
RADIUS = 6
STEP = .05


class KnobTrack(tk.Canvas):
    """A knob from `low` (-1 or 0) to 1. `pressed()` is called when a drag starts and `released(value)` when it
    ends."""

    def __init__(self, parent, low, value, color, *, pressed, released):
        super().__init__(parent, width=LENGTH + FIGURES + 2 * RADIUS, height=HEIGHT, bg=parent["bg"],
                         highlightthickness=0, borderwidth=0, cursor="sb_h_double_arrow")
        self.low, self.value, self.color = float(low), float(value), color
        self.pressed, self.released = pressed, released
        self.bind("<Button-1>", self.press)
        self.bind("<B1-Motion>", self.move)
        self.bind("<ButtonRelease-1>", self.release)
        self.draw()

    def x_of(self, value):
        return RADIUS + (value - self.low) / (1 - self.low) * LENGTH

    def value_at(self, x):
        value = self.low + (x - RADIUS) / LENGTH * (1 - self.low)
        return round(max(self.low, min(1., round(value / STEP) * STEP)), 2)

    def draw(self):
        self.delete("all")
        y = HEIGHT / 2
        rest = self.x_of(max(self.low, 0.))
        at = self.x_of(self.value)
        self.create_line(RADIUS, y, RADIUS + LENGTH, y, fill=RULE, width=3)
        self.create_line(rest, y - 6, rest, y + 6, fill=DIM, width=1, tags="rest")
        if abs(at - rest) >= 1:
            self.create_line(rest, y, at, y, fill=self.color, width=3, tags="bar")
        self.create_oval(at - RADIUS, y - RADIUS, at + RADIUS, y + RADIUS, fill=self.color, outline="",
                         tags="handle")
        self.create_text(LENGTH + 2 * RADIUS + FIGURES, y, text=f"{self.value:+.2f}", anchor="e",
                         fill=SUBTLE if self.value else DIM, font=(FONT, 9), tags="figures")

    def press(self, event):
        self.pressed()
        self.move(event)

    def move(self, event):
        self.value = self.value_at(event.x)
        self.draw()

    def release(self, event):
        self.move(event)
        self.released(self.value)
