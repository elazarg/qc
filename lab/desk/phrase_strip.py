"""The phrase, drawn small in the row under the tiles: the take's shape, the part of it that loops, and, while it
loops, a line where the loop is now. A click opens the Phrase panel, where the loop's edges are dragged.

Where the loop is now is worked out from when it started and how long it is, not read from the audio driver:
the line follows it closely enough to see where in the phrase a sound changes, and starts again with the loop.
"""

import time
import tkinter as tk

import numpy as np

from lab.desk.theme import ACCENT, DIM, FONT, LIVE, SOFT, TILE, mix

HEIGHT = 30
RATE = 48000


class PhraseStrip(tk.Canvas):
    def __init__(self, parent, *, open_panel):
        super().__init__(parent, height=HEIGHT, bg=TILE, highlightthickness=0, borderwidth=0, cursor="hand2",
                         width=120)
        self.audio = None           # the whole take, mono
        self.edges = (0, 0)         # where the loop starts and ends, in samples
        self.since = None           # when the loop started, while it plays
        self.drawn_for = None       # (take, width) the shape was last drawn for
        self.head = None            # the line where the loop is now
        self.bind("<Configure>", lambda _: self.redraw())
        self.bind("<ButtonRelease-1>", lambda _: open_panel())

    def show(self, whole, since):
        """`whole` is (the take, where the loop starts, where it ends) or None; `since` when it began to loop,
        or None while it does not play."""
        audio, edges = (None, (0, 0)) if whole is None else (whole[0], (int(whole[1]), int(whole[2])))
        if audio is not self.audio or edges != self.edges:
            self.audio, self.edges = audio, edges
            self.drawn_for = None
        self.since = since
        self.redraw()

    def redraw(self):
        width = max(self.winfo_width(), 40)
        if self.drawn_for != (id(self.audio), width, self.edges):
            self.drawn_for = (id(self.audio), width, self.edges)
            self.delete("all")
            self.head = None
            if self.audio is None or not len(self.audio):
                self.create_text(8, HEIGHT / 2, text="No phrase", anchor="w", fill=DIM, font=(FONT, 9))
                return
            total = len(self.audio)
            first, last = (edge / total * width for edge in self.edges)
            self.create_rectangle(first, 0, last, HEIGHT, fill=mix(ACCENT, TILE, .22), width=0)
            columns = np.array_split(np.abs(self.audio[:, 0]), width)
            peak = max(float(np.abs(self.audio).max()), 1e-6)
            middle = HEIGHT / 2
            for x, part in enumerate(columns):
                value = (float(part.max()) / peak if len(part) else 0.) * (middle - 3)
                self.create_line(x, middle - value, x, middle + value, fill=SOFT)
        self.place_head(width)

    def place_head(self, width=None):
        """Move the line to where the loop is now (called by the desk's clock), or take it away."""
        if self.audio is None or self.since is None:
            if self.head is not None:
                self.delete(self.head)
                self.head = None
            return
        width = width or max(self.winfo_width(), 40)
        first, last = self.edges
        length = max(last - first, 1)
        at = first + ((time.monotonic() - self.since) * RATE) % length
        x = at / len(self.audio) * width
        if self.head is None:
            self.head = self.create_line(x, 0, x, HEIGHT, fill=LIVE, width=2)
        else:
            self.coords(self.head, x, 0, x, HEIGHT)
