"""The instrument chooser at the top left of the desk: which instrument is played, on every screen.

    chooser = InstrumentChooser(top_row, instruments=store.instruments, current=lambda: state.instrument,
                                choose=lambda name: studio.set(instrument=name))
    chooser.refresh()       # after the instrument changed some other way

A tile with the instrument's picture, its name and a ▾. A click opens the list of the player's instruments
directly beneath it, each with its picture and the current one marked; choosing one closes the list, and so
does a click anywhere else. The list is a frame placed over the window's own content, never a window or a menu
of its own, and nothing is bound to a key.
"""

import tkinter as tk

from lab.desk.pictures import instrument_picture
from lab.desk.theme import ACCENT, FONT, INK, RULE, SOFT, TILE, mix

TILE_PICTURE = (64, 28)     # the picture on the tile, and in a row of the list
ROW_PICTURE = (84, 36)
LIT = mix(INK, TILE, .1)    # a row of the list under the pointer


class InstrumentChooser(tk.Frame):
    def __init__(self, parent, *, instruments, current, choose):
        """`instruments()` gives the player's instruments (records with "name" and "kind"), `current()` the name
        of the one in use, and `choose(name)` is called with the one picked in the list."""
        super().__init__(parent, bg=TILE, highlightthickness=1, highlightbackground=RULE, cursor="hand2", padx=8,
                         pady=3)
        self.instruments, self.current, self.choose = instruments, current, choose
        self.list = None            # the list of instruments, while it is open
        self.rows = {}              # name -> its row in the open list
        self.shown = None           # the instrument the tile shows
        self.picture = tk.Label(self, bg=TILE, borderwidth=0)
        self.picture.pack(side="left")
        self.name = tk.Label(self, bg=TILE, fg=INK, font=(FONT, 12, "bold"), padx=8)
        self.name.pack(side="left")
        self.arrow = tk.Label(self, text="▾", bg=TILE, fg=SOFT, font=(FONT, 12))
        self.arrow.pack(side="left")
        for widget in (self, self.picture, self.name, self.arrow):
            widget.bind("<ButtonRelease-1>", lambda _: self.toggle())
        self.bind("<Destroy>", lambda event: event.widget is self and self.close(), add="+")
        self.watching = False       # whether clicks elsewhere in the window are watched, to close the list
        self.refresh()

    def record(self, name):
        """An instrument's record; one the list does not hold (a name kept from before) is taken as an electric."""
        found = next((item for item in self.instruments() if item["name"] == name), None)
        return found or {"name": name or "", "kind": "electric"}

    def refresh(self):
        """Show the instrument in use on the tile, and mark it in the list if that is open."""
        name = self.current()
        if name != self.shown:
            self.shown = name
            self.image = instrument_picture(self, self.record(name), *TILE_PICTURE, paper=TILE, body=INK)
            self.picture.configure(image=self.image)
            self.name.configure(text=name or "No instrument")
        if self.list is not None:
            self.fill()

    # ---- the list ----

    def toggle(self):
        if self.list is None:
            self.open()
        else:
            self.close()

    def open(self):
        top = self.winfo_toplevel()
        if not self.watching:
            # One watch for the window: a press anywhere but on the tile or in the list puts the list away.
            self.watching = True
            top.bind("<ButtonPress-1>", self.pressed, add="+")
        self.list = tk.Frame(top, bg=TILE, highlightthickness=1, highlightbackground=RULE)
        self.fill()
        self.list.update_idletasks()
        # Directly beneath the tile, as wide as the tile at least, and kept inside the window.
        width = max(self.list.winfo_reqwidth(), self.winfo_width())
        x = self.winfo_rootx() - top.winfo_rootx()
        y = self.winfo_rooty() - top.winfo_rooty() + self.winfo_height() + 2
        x = max(4, min(x, top.winfo_width() - width - 4))
        self.list.place(x=x, y=y, width=width)
        self.list.lift()
        self.arrow.configure(text="▴")
        self.configure(highlightbackground=SOFT)

    def fill(self):
        """One row for each instrument: a mark for the one in use, its picture, its name."""
        for child in self.list.winfo_children():
            child.destroy()
        self.rows, self.row_images = {}, {}
        current = self.current()
        for item in self.instruments():
            name, chosen = item["name"], item["name"] == current
            row = tk.Frame(self.list, bg=TILE, cursor="hand2")
            row.pack(fill="x")
            mark = tk.Frame(row, bg=ACCENT if chosen else TILE, width=3)
            mark.pack(side="left", fill="y")
            # Drawn for the row as it lies and as it is under the pointer: a picture's edges are blended into
            # what is behind it.
            images = [instrument_picture(self, item, *ROW_PICTURE, paper=paper, body=INK if chosen else SOFT)
                      for paper in (TILE, LIT)]
            picture = tk.Label(row, image=images[0], bg=TILE, borderwidth=0)
            picture.pack(side="left", padx=(9, 0), pady=5)
            title = tk.Label(row, text=name, bg=TILE, fg=INK if chosen else SOFT, anchor="w", padx=12,
                             font=(FONT, 10, "bold" if chosen else "normal"))
            title.pack(side="left", fill="x", expand=True)
            parts = (row, picture, title)
            self.rows[name], self.row_images[name] = row, images

            def light(lit, parts=parts, picture=picture, images=images):
                for part in parts:
                    part.configure(bg=LIT if lit else TILE)
                picture.configure(image=images[1 if lit else 0])

            for part in parts:
                part.bind("<Enter>", lambda _, light=light: light(True))
                part.bind("<Leave>", lambda _, light=light: light(False))
                part.bind("<ButtonRelease-1>", lambda _, name=name: self.pick(name))
            mark.bind("<ButtonRelease-1>", lambda _, name=name: self.pick(name))

    def pick(self, name):
        self.close()
        if name != self.current():
            self.choose(name)
        self.refresh()

    def close(self):
        if self.list is None:
            return
        self.list.destroy()
        self.list, self.rows, self.row_images = None, {}, {}
        try:
            self.arrow.configure(text="▾")
            self.configure(highlightbackground=RULE)
        except tk.TclError:
            pass                    # the tile itself is going

    def pressed(self, event):
        """A press somewhere in the window while the list is open: unless it is on the tile (which closes the list
        itself) or in the list, the list is put away."""
        if self.list is None:
            return
        widget = event.widget
        while isinstance(widget, tk.Misc):
            if widget is self or widget is self.list:
                return
            widget = getattr(widget, "master", None)
        self.close()
