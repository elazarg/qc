"""A "?" beside something, which says more while the pointer rests on it.

The desk shows what is done with a thing, not a paragraph about it. What is worth knowing once, and not worth
reading every time, goes behind a `hint`: a small "?" that shows its words while the pointer is over it, or
after a click on it until the next click anywhere. The words are a label placed over the window's own
content, never a window of their own.

    from lab.desk.hints import hint, named
    hint(row, "A return gesture taught on a guitar control turns its Shape knob.").pack(side="left")
    named(symbol, "Learn the guitar")       # a drawn symbol says its name while the pointer rests on it
"""

import tkinter as tk
from weakref import WeakKeyDictionary, WeakSet

from lab.desk.theme import DIM, FONT, INK, RULE, SOFT, TILE

WIDTH = 300         # the widest a hint's words are set, in pixels
MARGIN = 8          # kept between the words and the window's edge


class Hint(tk.Label):
    """The "?" itself. `words` is what it says; change it with `say`."""

    def __init__(self, parent, words, *, color=DIM):
        super().__init__(parent, text="?", bg=parent["bg"], fg=color, font=(FONT, 10, "bold"),
                         cursor="question_arrow", padx=7, pady=1, borderwidth=0)
        self.words, self.color = words, color
        self.shown = None           # the label showing the words, while it shows
        self.pinned = False         # shown by a click: it stays until the next click
        self.bind("<Enter>", lambda _: self.show())
        self.bind("<Leave>", lambda _: self.pinned or self.hide())
        self.bind("<ButtonRelease-1>", self.click)
        self.bind("<Destroy>", lambda _: self.gone(), add="+")

    def say(self, words):
        self.words = words
        if self.shown is not None:
            self.shown.configure(text=words)

    def click(self, _):
        if self.pinned:
            self.unpin()
            return
        top = self.winfo_toplevel()
        other = _pinned.get(top)
        if other is not None:
            other.unpin()
        elif top not in _watched:
            # One watch for the whole window: the next click anywhere takes a pinned hint's words away again.
            _watched.add(top)
            top.bind("<ButtonPress-1>", lambda event, top=top: _elsewhere(top, event), add="+")
        _pinned[top] = self
        self.pinned = True
        self.show()

    def unpin(self):
        self.pinned = False
        _pinned.pop(self.winfo_toplevel(), None) if self.winfo_exists() else None
        self.hide()

    def show(self):
        if self.shown is not None or not self.words:
            return
        self.configure(fg=INK)
        self.shown = words_beside(self, self.words)

    def gone(self):
        for top, shown in list(_pinned.items()):
            if shown is self:
                del _pinned[top]
        self.hide()

    def hide(self):
        if self.shown is not None:
            self.shown.destroy()
            self.shown = None
        try:
            self.configure(fg=self.color)
        except tk.TclError:
            pass                    # the "?" itself is going


_pinned, _watched = WeakKeyDictionary(), WeakSet()     # per window: the hint a click shows; whether clicks are watched


def words_beside(widget, words):
    """A label with `words`, placed over the window's content just below `widget`: starting at its left edge,
    and moved left, or above it, as far as needed to stay inside the window. The caller destroys it."""
    top = widget.winfo_toplevel()
    shown = tk.Label(top, text=words, bg=TILE, fg=SOFT, font=(FONT, 9), justify="left", anchor="w",
                     wraplength=WIDTH, padx=10, pady=8, highlightthickness=1, highlightbackground=RULE)
    shown.update_idletasks()
    width, height = shown.winfo_reqwidth(), shown.winfo_reqheight()
    x = widget.winfo_rootx() - top.winfo_rootx()
    y = widget.winfo_rooty() - top.winfo_rooty() + widget.winfo_height() + 4
    x = max(MARGIN, min(x, top.winfo_width() - width - MARGIN))
    if y + height > top.winfo_height() - MARGIN:
        y = max(MARGIN, widget.winfo_rooty() - top.winfo_rooty() - height - 4)
    shown.place(x=x, y=y)
    shown.lift()
    return shown


def named(widget, words):
    """Have a drawn symbol, or anything else without words of its own, say what it is while the pointer rests on
    it. `words` is the name, or a function that gives it (asked each time, for a name that changes with what
    the thing would do). A press takes the words away: the thing is there to be clicked."""
    shown = []

    def show(_):
        said = words() if callable(words) else words
        if said and not shown:
            shown.append(words_beside(widget, said))

    def hide(_):
        while shown:
            shown.pop().destroy()

    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<ButtonPress-1>", hide, add="+")
    widget.bind("<Destroy>", hide, add="+")
    return widget


def _elsewhere(top, event):
    shown = _pinned.get(top)
    if shown is not None and event.widget is not shown:
        _pinned.pop(top, None)
        shown.pinned = False
        shown.hide()


def hint(parent, words, *, color=DIM):
    """A "?" in `parent` that shows `words` while the pointer rests on it, and after a click until the next."""
    return Hint(parent, words, color=color)
