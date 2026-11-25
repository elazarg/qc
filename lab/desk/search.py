"""A small in-window search over the desk's local sounds and cached presets."""

import tkinter as tk

from lab.desk.hints import named
from lab.desk.theme import ACCENT, DARK, DIM, FONT, GLYPH, INK, PAPER, RULE, SOFT, SUBTLE, TILE, icon_font

VISIBLE = 8


def search_line(parent, query, *, variable=None):
    """A filter entry with a magnifying glass, and its frame."""
    frame = tk.Frame(parent, bg=TILE)
    tk.Label(frame, text=GLYPH["search"], font=icon_font(10), bg=TILE, fg=SUBTLE, padx=6).pack(side="left")
    entry = tk.Entry(frame, textvariable=variable, font=(FONT, 10), bg=TILE, fg=INK, relief="flat", insertbackground=INK)
    if query:
        entry.insert(0, query)
    entry.pack(side="left", fill="x", expand=True, ipady=4)
    return frame, entry


def matches(items, query):
    query = " ".join(query.casefold().split())
    if not query:
        return []
    words = query.split()
    found = [item for item in items if all(word in item[2] for word in words)]
    return sorted(found, key=lambda item: (item[0].casefold() != query,
                                           not all(word in item[0].casefold() for word in words)))


class DeskSearch:
    def __init__(self, top, studio, collect, choose):
        self.studio, self.collect, self.choose = studio, collect, choose
        self.root = top.winfo_toplevel()
        self.panel = None
        self.icon = tk.Label(top, text=GLYPH["search"], font=icon_font(14), fg=SOFT, bg=PAPER,
                             cursor="hand2", padx=9, pady=5)
        self.icon.bind("<ButtonRelease-1>", lambda _: self.toggle())
        named(self.icon, lambda: "Search presets and sounds" if not studio.state.blind else "Search after comparing blind")
        self.root.bind("<ButtonPress-1>", self.pressed, add="+")
        self.show_icon()

    def show_icon(self):
        self.icon.place(relx=.5, rely=.5, anchor="center")

    def follow(self):
        if self.studio.state.blind:
            self.close()
        self.icon.configure(fg=self.icon_color())

    def icon_color(self):
        if self.studio.state.blind:
            return DIM
        return ACCENT if self.panel is not None else SOFT

    def pressed(self, event):
        widget = event.widget
        while isinstance(widget, tk.Misc):
            if widget is self.panel or widget is self.icon:
                return
            widget = widget.master
        self.close()

    def toggle(self):
        if self.panel is not None:
            self.close()
        else:
            self.open()

    def open(self):
        if self.panel is not None or self.studio.state.blind:
            return
        panel = self.panel = tk.Frame(self.root, bg=DARK, padx=10, pady=9, highlightbackground=RULE,
                                     highlightthickness=1)
        self.icon.configure(fg=ACCENT, bg=TILE)
        self.items, self.shown = None, []
        self.query = tk.StringVar(master=panel)
        line, self.entry = search_line(panel, "", variable=self.query)
        line.pack(fill="x")
        self.box = tk.Listbox(panel, bg=DARK, fg=INK, selectbackground=TILE, relief="flat", borderwidth=0,
                              highlightthickness=0, exportselection=False, activestyle="none", font=(FONT, 10),
                              cursor="hand2")
        self.box.bind("<ButtonRelease-1>", self.picked)
        self.status = tk.Label(panel, bg=DARK, fg=SUBTLE, font=(FONT, 9), anchor="w")
        self.status.pack(side="bottom", fill="x", pady=(6, 0))
        self.trace = self.query.trace_add("write", self.fill)
        y = self.icon.winfo_rooty() - self.root.winfo_rooty() + self.icon.winfo_height() + 5
        panel.place(relx=.5, y=y, anchor="n", width=460)
        panel.lift()
        self.entry.focus_set()
        self.fill()

        def loaded(items):
            if self.panel is panel:
                self.items = items
                self.fill()

        def failed(_):
            if self.panel is panel:
                self.items = ()
                self.status.configure(text="Could not read the search library. Reopen search to try again.")
                self.status.pack(side="bottom", fill="x", pady=(6, 0))

        self.studio.later(self.studio.reading.submit(self.collect), loaded, failed)

    def fill(self, *_):
        query = self.query.get().strip()
        found = matches(self.items or (), query)
        self.shown = found[:VISIBLE]
        self.box.delete(0, "end")
        for name, detail, _, _ in self.shown:
            self.box.insert("end", f"{name}  ·  {detail}")
        if self.shown:
            self.box.configure(height=len(self.shown))
            self.box.pack(fill="x", pady=(5, 0))
        else:
            self.box.pack_forget()
        if not query:
            note = "Presets and sounds"
        elif self.items is None:
            note = "Searching…"
        elif not found:
            note = "No matches"
        else:
            note = f"{len(found)} matches · type to narrow" if len(found) > VISIBLE else ""
        self.status.configure(text=note)
        if note:
            self.status.pack(side="bottom", fill="x", pady=(6, 0))
        else:
            self.status.pack_forget()

    def picked(self, event):
        index = self.box.nearest(event.y)
        row = self.box.bbox(index)
        if index >= len(self.shown) or not row or not row[1] <= event.y < row[1] + row[3]:
            return
        _, _, _, target = self.shown[index]
        self.close()
        if not self.studio.state.blind:
            self.choose(target)

    def close(self):
        if self.panel is not None:
            self.query.trace_remove("write", self.trace)
            self.panel.destroy()
            self.panel = None
            self.icon.configure(fg=self.icon_color(), bg=PAPER)
