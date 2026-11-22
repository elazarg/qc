"""The learning screen: learning the guitar that is played, in the place of the whole desk below its top row.

    screen = LearningScreen(seat, parent)       # the desk makes one, the first time the learning symbol is pressed
    screen.frame                                 # what the desk shows in the place of its tiles, panel and ask line
    screen.shown() / screen.hidden()             # called as the desk puts it on and takes it off
    screen.follow()                              # the instrument on the desk has changed
    screen.say(text)                             # its own line at the bottom, for what the desk's status line says

One screen with two views, chosen in place at its top: the Guitar Learner (`lab.learner.window`), for a guitar
whose controls it has a photograph and a layout of, and Teach a control (`lab.learner.teach`), with the gesture
mapping, for any instrument. For an instrument the learner has no layout for, only Teach a control is offered,
and the screen says so in a few words. Both views work on the instrument chosen on the desk and follow it when
it changes. Each is made the first time it is shown and kept, so the screen comes back as it was left.

Leaving the screen, or changing the view, ends a recording or a live listening of the view that is left;
gesture listening goes on, so that the guitar can be played on the desk.
"""

import tkinter as tk

from lab.desk.theme import DARK, DIM, FONT, INK, PAPER, SOFT, SUBTLE, TILE

LEARNER, TEACH = "Guitar Learner", "Teach a control"


class LearningScreen:
    def __init__(self, seat, parent):
        self.seat = seat
        self.frame = tk.Frame(parent, bg=PAPER)
        self.learner = self.teaching = None     # the two views, each made when it is first shown
        self.view = None                        # the one on show
        self.wanted = LEARNER                   # the one the player chose last; shown when the instrument has it
        self.on = False
        head = tk.Frame(self.frame, bg=PAPER)
        head.pack(fill="x", pady=(0, 10))
        self.tabs = {}
        for title in (LEARNER, TEACH):
            tab = tk.Label(head, text=title, font=(FONT, 11), padx=12, pady=5, cursor="hand2")
            tab.bind("<ButtonRelease-1>", lambda _, title=title: self.choose(title))
            self.tabs[title] = tab
        self.why = tk.Label(head, bg=PAPER, fg=SUBTLE, font=(FONT, 9), anchor="w", padx=10)
        # At the right of the views' names: what the view on show keeps at the top (its `header`, if it has one).
        self.beside = tk.Frame(head, bg=PAPER)
        self.beside.pack(side="right")
        # What the desk's status line says, here: the desk's own is not on show while this screen is.
        self.line = tk.Label(self.frame, bg=PAPER, fg=INK, font=(FONT, 10), anchor="w", justify="left")
        self.line.pack(side="bottom", fill="x", pady=(8, 0))
        self.stage = tk.Frame(self.frame, bg=PAPER)
        self.stage.pack(fill="both", expand=True)

    # ---- coming and going ----

    def shown(self):
        self.on = True
        self.follow()

    def hidden(self):
        self.on = False
        for view in (self.learner, self.teaching):
            if view is not None:
                view.hidden()

    def offered(self):
        """The views there are for the instrument chosen on the desk."""
        from lab.learner.window import offered_for

        return (LEARNER, TEACH) if offered_for(self.seat.guitar) else (TEACH,)

    def follow(self):
        """Show what there is for the instrument chosen on the desk: called when the screen is put on and when the
        instrument changes while it is on."""
        offered = self.offered()
        for part in (*self.tabs.values(), self.why):
            part.pack_forget()
        for title in offered:
            self.tabs[title].pack(side="left", padx=(0, 4))
        if LEARNER not in offered:
            self.why.configure(text=f"The Guitar Learner has no picture of the {self.seat.guitar}'s controls.")
            self.why.pack(side="left")
        self.show(self.wanted if self.wanted in offered else TEACH)

    def choose(self, title):
        """A view was chosen at the top."""
        if title in self.offered():
            self.wanted = title
            self.show(title)

    def show(self, title):
        current = self.learner if self.view == LEARNER else self.teaching if self.view == TEACH else None
        if title != self.view and current is not None:
            current.frame.pack_forget()
            if current.header is not None:
                current.header.pack_forget()
            current.hidden()
        self.view = title
        if title == LEARNER:
            if self.learner is None:
                from lab.learner.window import GuitarLearner

                self.learner = GuitarLearner(self.seat, self.stage, self.beside)
            view = self.learner
        else:
            if self.teaching is None:
                from lab.learner.teach import Teaching

                self.teaching = Teaching(self.seat, self.stage, self.beside)
            view = self.teaching
        if not view.frame.winfo_manager():
            view.frame.pack(fill="both", expand=True)
            if view.header is not None:
                view.header.pack(side="right")
        view.shown()
        for name, tab in self.tabs.items():
            chosen = name == title
            tab.configure(bg=TILE if chosen else DARK, fg=INK if chosen else SOFT,
                          font=(FONT, 11, "bold" if chosen else "normal"))

    def say(self, text, working=""):
        """The line at the bottom: what the desk is working on, if anything, then what its status line says."""
        self.line.configure(text=f"{working}   {text}" if working and text else working or text,
                            fg=INK if text else DIM)
