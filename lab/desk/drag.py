"""Dragging something onto A or B: a tile, a design of the round, a sound from History, an IR or a capture.

    drags = Drags(root, targets={"a": tile_a, "b": tile_b}, dropped=lambda side, what: ...)
    drags.source(widget, lambda event: what, lambda what: "Plexi Crunch")
    widget.bind("<ButtonRelease-1>", drags.click(lambda: ...))     # a click that was not a drag

A press that moves more than a few pixels is a drag: its name follows the pointer, both tiles light up as places
to drop it, and letting go over one hands it to `dropped`. Let go anywhere else and nothing happens. A press that
does not move is the click it always was. Everything is drawn inside the one window: the name that follows the
pointer is a label placed over the window's own content, never a window of its own.
"""

import tkinter as tk

from lab.desk.theme import FONT, INK, RULE, TILE

FAR_ENOUGH = 6      # pixels the pointer moves before a press is a drag


class Drags:
    def __init__(self, root, *, targets, dropped, lit=None):
        """`targets` are the widgets dropped on, by side; `dropped(side, what)` is called with what was let go
        over one; `lit(on)` is called as a drag begins and ends, for the targets to show they take it."""
        self.root, self.targets, self.dropped, self.lit = root, targets, dropped, lit
        self.pressed = None         # (x, y, what) of the press under way
        self.dragging = False       # the press has moved far enough to be a drag
        self.ghost = None           # the name that follows the pointer

    def source(self, widget, what, name):
        """Make `widget` something to drag. `what(event)` is what is dragged (None for nothing, as over an empty
        row); `name(what)` the few words that follow the pointer."""
        widget.bind("<ButtonPress-1>", lambda event: self.press(event, what(event), name), add="+")
        widget.bind("<B1-Motion>", self.move, add="+")
        widget.bind("<ButtonRelease-1>", self.release, add="+")

    def click(self, action):
        """A handler for a click on a source: it does nothing when the press became a drag."""
        def clicked(_=None):
            if not self.dragging:
                action()
        return clicked

    def press(self, event, what, name):
        self.dragging = False
        self.pressed = None if what is None else (event.x_root, event.y_root, what, name)

    def move(self, event):
        if self.pressed is None:
            return
        x, y, what, name = self.pressed
        if not self.dragging and max(abs(event.x_root - x), abs(event.y_root - y)) < FAR_ENOUGH:
            return
        if not self.dragging:
            self.dragging = True
            self.ghost = tk.Label(self.root, text=name(what), bg=TILE, fg=INK, font=(FONT, 9), padx=8, pady=3,
                                  highlightthickness=1, highlightbackground=RULE)
            if self.lit:
                self.lit(True)
        self.ghost.place(x=event.x_root - self.root.winfo_rootx() + 14, y=event.y_root - self.root.winfo_rooty() + 8)
        self.ghost.lift()

    def over(self, x, y):
        """The side whose target lies under a point of the screen, or None."""
        for side, target in self.targets.items():
            left, top = target.winfo_rootx(), target.winfo_rooty()
            if left <= x < left + target.winfo_width() and top <= y < top + target.winfo_height():
                return side
        return None

    def release(self, event):
        pressed, dragged = self.pressed, self.dragging
        self.pressed = None
        if self.ghost is not None:
            self.ghost.destroy()
            self.ghost = None
        if not dragged:
            return
        if self.lit:
            self.lit(False)
        side = self.over(event.x_root, event.y_root)
        if side is not None and pressed is not None:
            self.dropped(side, pressed[2])
        # `dragging` stays set until the next press, so that a click handler of the same release, wherever
        # it runs in the order, sees that this was a drag.
