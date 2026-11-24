"""The phrase, drawn small in the row under the tiles: the take's shape, the part of it that loops, and, while it
loops, a line where the loop is now. A click opens the Phrase panel, where the loop's edges are dragged.

While a take is recorded the strip is the recording's: edged in the colour of recording while the input opens,
then filled with that colour, which drains from left to right over the seconds the recording lasts. A count can
be followed while playing; a sentence cannot be read.

Where the loop is now is worked out from when it started and how long it is, not read from the audio driver:
the line follows it closely enough to see where in the phrase a sound changes, and starts again with the loop.
The desk's clock moves it (`place_head`).
"""

import time
import tkinter as tk

import numpy as np

from lab.desk.theme import ACCENT, DIM, FONT, LIVE, RECORDING, SOFT, TILE, mix

HEIGHT = 30
RATE = 48000


class PhraseStrip(tk.Canvas):
    def __init__(self, parent, *, open_panel):
        super().__init__(parent, height=HEIGHT, bg=TILE, highlightthickness=0, borderwidth=0, cursor="hand2",
                         width=120)
        self.audio = None           # the whole take, mono
        self.edges = (0, 0)         # where the loop starts and ends, in samples
        self.since = None           # when the loop started, while it plays
        self.taking = None          # while a take is recorded: (when it began, or None while the input opens; seconds)
        self.drawn_for = None       # what the strip was last drawn for
        self.head = None            # the line where the loop is now
        self.left = None            # the part of the recording still to come
        self.bind("<Configure>", lambda _: self.redraw())
        self.bind("<ButtonRelease-1>", lambda _: open_panel())

    def show(self, whole, since, taking=None):
        """`whole` is (the take, where the loop starts, where it ends) or None; `since` when it began to loop,
        or None while it does not play; `taking` the recording under way, as (when it began or None, seconds)."""
        audio, edges = (None, (0, 0)) if whole is None else (whole[0], (int(whole[1]), int(whole[2])))
        if audio is not self.audio or edges != self.edges:
            self.audio, self.edges = audio, edges
            self.drawn_for = None
        self.since, self.taking = since, taking
        self.redraw()

    def redraw(self):
        width = max(self.winfo_width(), 40)
        key = (id(self.audio), width, self.edges, None if self.taking is None else self.taking[0] is None)
        if self.drawn_for != key:
            self.drawn_for = key
            self.delete("all")
            self.head = self.left = None
            if self.taking is not None:
                self.create_rectangle(1, 1, width - 1, HEIGHT - 1, outline=RECORDING, width=2, tags="recording")
            elif self.audio is None or not len(self.audio):
                self.create_text(8, HEIGHT / 2, text="No phrase", anchor="w", fill=DIM, font=(FONT, 9))
                return
            else:
                self.draw_take(width)
        self.place_head(width)

    def draw_take(self, width):
        total = len(self.audio)
        first, last = (edge / total * width for edge in self.edges)
        self.create_rectangle(first, 0, last, HEIGHT, fill=mix(ACCENT, TILE, .22), width=0)
        columns = np.array_split(np.abs(self.audio[:, 0]), width)
        peak = max(float(np.abs(self.audio).max()), 1e-6)
        middle = HEIGHT / 2
        for x, part in enumerate(columns):
            value = (float(part.max()) / peak if len(part) else 0.) * (middle - 3)
            self.create_line(x, middle - value, x, middle + value, fill=SOFT)

    def place_head(self, width=None):
        """Move the line to where the loop is now, and drain the recording, by the clock; or take them away."""
        width = width or max(self.winfo_width(), 40)
        if self.taking is not None and self.taking[0] is not None:
            began, seconds = self.taking
            done = min(1., (time.monotonic() - began) / max(seconds, .1))
            if self.left is None:
                self.left = self.create_rectangle(0, 0, 0, HEIGHT, fill=RECORDING, width=0, tags="left")
            self.coords(self.left, done * width, 0, width, HEIGHT)
        if self.audio is None or self.since is None or self.taking is not None:
            if self.head is not None:
                self.delete(self.head)
                self.head = None
            return
        first, last = self.edges
        length = max(last - first, 1)
        at = first + ((time.monotonic() - self.since) * RATE) % length
        x = at / len(self.audio) * width
        if self.head is None:
            self.head = self.create_line(x, 0, x, HEIGHT, fill=LIVE, width=2)
        else:
            self.coords(self.head, x, 0, x, HEIGHT)
