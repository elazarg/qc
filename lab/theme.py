"""One colour, type and small-widget vocabulary for the desk. Carried over unchanged from the old app."""

import tkinter as tk

PAPER = "#14151a"
DARK = "#1e2028"
WHITE = "#252834"
TILE = "#2c303c"
RULE = "#383c4a"
INK = "#f0ede9"
LIGHT = "#f5f1ee"
SOFT = "#bcbcc9"
SUBTLE = "#a5a6b5"
DIM = "#71758a"
ACCENT = "#f7b889"
ACCENT_INK = "#1a1718"
FONT = "Segoe UI"

# Colour carries identity and state. It is never used for emphasis alone.
TONE_A = "#8fb2ff"
TONE_B = "#f7b889"
REFERENCE = "#c9b5f5"
LIVE = "#6fdd9f"
WARN = "#f0c95c"
ALERT = "#ff6b5e"
SIDE = {"a": TONE_A, "b": TONE_B, "reference": REFERENCE}


def mix(color, base, amount):
    """Blend `amount` of color into base."""
    front = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
    back = [int(base[index:index + 2], 16) for index in (1, 3, 5)]
    return "#" + "".join(f"{round(b + (f - b) * amount):02x}" for f, b in zip(front, back))


class Tooltip:
    """A short label shown after the pointer rests on a control."""

    def __init__(self, widget, text, *, wrap=0):
        self.widget, self.text, self.wrap = widget, text, wrap
        self.job = self.window = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _=None):
        self.hide()
        self.job = self.widget.after(450, self.show)

    def show(self):
        self.job = None
        value = self.text() if callable(self.text) else self.text
        if not value:
            return
        self.window = window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.attributes("-topmost", True)
        tk.Label(window, text=value, bg=TILE, fg=INK, font=(FONT, 9), padx=8, pady=4, justify="left",
                 wraplength=self.wrap).pack()
        window.update_idletasks()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2 - window.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        window.wm_geometry(f"+{max(0, x)}+{y}")

    def hide(self, _=None):
        if self.job is not None:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


def _shape(canvas, color):
    canvas.create_oval(9, 7, 23, 21, outline=color, width=2)
    canvas.create_line(16, 14, 20, 10, fill=color, width=2, capstyle="round")


def _difference(canvas, color):
    canvas.create_line(7, 17, 12, 8, 17, 14, 21, 17, 25, 9, fill=color, width=2, smooth=True, capstyle="round")
    canvas.create_line(7, 21, 13, 17, 18, 20, 25, 18, fill=color, width=2, smooth=True, capstyle="round")


def _chain(canvas, color):
    for left in (6, 13, 20):
        canvas.create_rectangle(left, 10, left + 5, 18, outline=color, width=2)


def _history(canvas, color):
    canvas.create_oval(9, 7, 23, 21, outline=color, width=2)
    canvas.create_line(16, 10, 16, 14, 19, 16, fill=color, width=2, capstyle="round")


def _sounds(canvas, color):
    for y, right in ((9, 25), (14, 25), (19, 18)):
        canvas.create_line(7, y, right, y, fill=color, width=2, capstyle="round")


def _more(canvas, color):
    for x in (9, 16, 23):
        canvas.create_oval(x - 1.5, 12.5, x + 1.5, 15.5, fill=color, outline=color)


GLYPHS = {"Shape": _shape, "Difference": _difference, "Chain": _chain, "History": _history,
          "Sounds": _sounds, "More": _more}


class IconButton(tk.Canvas):
    """A glyph that brightens under the pointer and stays lit while its panel is open."""

    def __init__(self, parent, glyph, command, *, tip=None):
        super().__init__(parent, width=32, height=28, bg=parent["bg"], highlightthickness=1,
                         highlightbackground=parent["bg"], highlightcolor=ACCENT, cursor="hand2", takefocus=True)
        self.glyph, self.command = glyph, command
        self.rest = parent["bg"]
        self.open = self.pinned = self.hover = False
        self.bind("<Enter>", lambda _: self.set_hover(True))
        self.bind("<Leave>", lambda _: self.set_hover(False))
        self.bind("<ButtonRelease-1>", lambda _: self.command())
        self.bind("<Return>", lambda _: self.command())
        if tip:
            Tooltip(self, tip)
        self.paint()

    def set_hover(self, value):
        self.hover = value
        self.paint()

    def mark(self, *, open=False, pinned=False):
        if (open, pinned) != (self.open, self.pinned):
            self.open, self.pinned = open, pinned
            self.paint()

    def paint(self):
        self.delete("all")
        self.configure(bg=TILE if self.open else WHITE if self.hover else self.rest)
        GLYPHS[self.glyph](self, INK if self.open or self.hover else SUBTLE)
        if self.pinned:
            self.create_oval(25, 3, 29, 7, fill=ACCENT, outline=ACCENT)


def link(parent, title, action):
    """A quiet inline action that reads as text until the pointer reaches it."""
    button = tk.Button(parent, text=title, command=action, relief="flat", borderwidth=0, cursor="hand2",
                       font=(FONT, 9, "bold"), bg=TILE, fg=INK, activebackground=RULE, activeforeground=INK,
                       padx=8, pady=2, takefocus=True, highlightthickness=1, highlightbackground=parent["bg"],
                       highlightcolor=ACCENT)
    button.bind("<Enter>", lambda _: button.configure(bg=RULE), add="+")
    button.bind("<Leave>", lambda _: button.configure(bg=TILE), add="+")
    return button


class Line(tk.Frame):
    """The one place the desk speaks: a single message with at most a few actions."""

    COLORS = {"info": SUBTLE, "reading": INK, "progress": SOFT, "warn": WARN, "error": ALERT}

    def __init__(self, parent):
        super().__init__(parent, bg=parent["bg"])
        self.kind = "info"
        self.label = tk.Label(self, bg=self["bg"], fg=SUBTLE, font=(FONT, 10), justify="left", anchor="w")
        self.label.pack(side="left", fill="x", expand=True)
        self.hint = ""
        Tooltip(self.label, lambda: self.hint, wrap=420)
        self.buttons = tk.Frame(self, bg=self["bg"])
        self.buttons.pack(side="right", padx=(8, 0))
        tk.Frame(self, bg=self["bg"], height=30, width=1).pack(side="right")  # Keeps the row from collapsing.
        self.bind("<Configure>", self.wrap)

    def wrap(self, _=None):
        self.update_idletasks()
        self.label.configure(wraplength=max(200, self.winfo_width() - self.buttons.winfo_reqwidth() - 24))

    def show(self, value, *, kind="info", actions=(), hint=""):
        """`hint` is a further remark shown when the pointer rests on the line. A small mark says it is there."""
        self.kind, self.hint = kind, hint
        self.label.configure(text=f"{value}  ⓘ" if hint else value, fg=self.COLORS[kind])
        for child in self.buttons.winfo_children():
            child.destroy()
        for title, action in actions:
            link(self.buttons, title, action).pack(side="left", padx=(4, 0))
        self.wrap()

    def clear(self):
        self.show("")
