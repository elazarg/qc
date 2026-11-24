"""The three qualities a round aims for, drawn on a sound's tile: for each, a short scale of its words (clean to
saturated, dark to bright, dry to wet), a ring where the round aimed and a dot where listening placed the sound.
A miss is a gap between ring and dot, seen without reading.

What the dot stands for is listening's word for the sound, an inference from what was measured
(`lab.hearing.measure`); the words themselves are said while the pointer rests on the scales.
"""

import tkinter as tk

from lab.desk.difference import QUALITIES
from lab.desk.theme import DIM, FONT, RULE, SUBTLE
from lab.hearing.measure import WORDS

ROW = 15            # pixels a quality takes, top to bottom
NAME = 64           # pixels for its name, at the left
RADIUS = 4


def place(key, word):
    """Where a word stands on its quality's scale, 0 to 1, or None for a word the scale does not have."""
    words = WORDS.get(key) or ()
    if word not in words or len(words) < 2:
        return None
    return words.index(word) / (len(words) - 1)


class Scales(tk.Canvas):
    """The scales of one tile. `show(aim, heard, color)` draws them; with neither, they take no room."""

    def __init__(self, parent):
        super().__init__(parent, height=len(QUALITIES) * ROW + 2, bg=parent["bg"], highlightthickness=0,
                         borderwidth=0)
        self.drawn = None
        self.words = ""
        self.bind("<Configure>", lambda _: self.redraw())

    def show(self, aim, heard, color):
        """Draw `aim` (a round's aim, or None) and `heard` (listening's words for the sound, or None)."""
        self.drawn = (dict(aim or {}), dict(heard or {}), color)
        self.redraw()

    def redraw(self):
        self.delete("all")
        self.configure(bg=self.master["bg"])
        if self.drawn is None:
            return
        aim, heard, color = self.drawn
        if not any(aim.get(key) or heard.get(key) for key, _ in QUALITIES):
            self.configure(height=1)            # nothing aimed for or heard yet: no empty scales
            self.words = ""
            return
        self.configure(height=len(QUALITIES) * ROW + 2)
        width = max(self.winfo_width(), 120)
        left, right = NAME, min(width - 8, NAME + 260)
        said = []
        for row, (key, short) in enumerate(QUALITIES):
            y = row * ROW + ROW / 2 + 1
            self.create_text(0, y, text=short, anchor="w", fill=SUBTLE, font=(FONT, 8))
            self.create_line(left, y, right, y, fill=RULE, width=2)
            at = place(key, aim.get(key))
            if at is not None:
                x = left + at * (right - left)
                self.create_oval(x - RADIUS - 1, y - RADIUS - 1, x + RADIUS + 1, y + RADIUS + 1, outline=DIM, width=2)
            got = place(key, heard.get(key))
            if got is not None:
                x = left + got * (right - left)
                self.create_oval(x - RADIUS, y - RADIUS, x + RADIUS, y + RADIUS, fill=color, outline="")
            if aim.get(key) or heard.get(key):
                said.append(f"{short}: aim {aim.get(key) or '–'}, heard as {heard.get(key) or '–'}")
        self.words = "\n".join(said)
