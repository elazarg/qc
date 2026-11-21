"""What the side tools share: a place in the desk's window, work sent to the engine, and silent renders.

A tool is shown in the desk's one window, in the place of its side panel (`Tool`, `Desk.show`). It talks to
the unit only through `Tool.call`, which sends one of the tool's own operations to the engine
(`host.call("lab.tools.x.op")`) and hands the result back on the window's thread through the studio's clock.

Nothing here opens a window, a dialog or a menu, and nothing is bound to a key: a choice among a few is a
row of chips (`Chips`), a file is chosen from a list in place (`FileChoice`), and what is rarely needed is
shown on request, in place (`disclosure`).

`render` is the operation most tools need: the phrase played through a chain
on the unit and returned over USB, with nothing routed to the unit's outputs.
It builds the chain once and keeps it in the cache like any other, so a sound
rendered by a tool plays at once on the desk afterwards, and the reverse.

Rendered sound is heard on the computer's own output (`play`), level-matched
by the tool that made it, as the old workbenches did. The guitar and the
desk's phrase still go through the unit.
"""

import os
from pathlib import Path
import tempfile
import tkinter as tk
from weakref import WeakKeyDictionary

import numpy as np

from lab.hearing.audio import RATE
from lab.sound.chain import routed
from lab.qc.library import SCENES as SCENE_LETTERS   # "ABCDEFGH": the one place the scene letters are spelled
from lab.qc.rig import USB
from lab.qc.unit import Stopped
from lab.desk.theme import ACCENT, DIM, FONT, INK, PAPER, RULE, SOFT, SUBTLE, TILE, WHITE


# ---- operations, run where the unit is held ----

def render(engine, chain, dry, *, scene=None, tail=1., stop=None):
    """What `chain` returns for the mono phrase `dry`, stereo, lined up with it, plus `tail` seconds after.

    Silent in the room: the chain is fed from the computer and returned to it. Whatever the desk was playing
    stops, and the desk shows its sounds again the next time it plays.
    """
    engine.quiet()
    if engine.offset is None:
        engine.measure_round_trip()
    try:
        slot, held = engine.rig.ready(chain, stop=stop)
        engine.rig.connect(held, routed(chain, *USB).routes)
        if scene is not None and engine.unit.scene() != scene:
            engine.unit.select_scene(scene)
        engine.unit.guard()
        # A little more than the tail asked for, so that the round trip's delay can be taken off.
        raw = engine.audio.reamp(np.asarray(dry, dtype=np.float32), tail=tail + .3, cancel=stop)
        engine.unit.guard()
    finally:
        engine.shown = None
    wanted = len(dry) + round(tail * RATE)
    returned = np.asarray(raw[engine.offset:engine.offset + wanted], dtype=np.float32)
    if len(returned) < len(dry):
        raise RuntimeError("The unit returned less audio than was sent.")
    return returned


def render_many(engine, items, dry, *, tail=1., stop=None):
    """`render` for each (chain, scene) in turn. Stops between two renders when asked to."""
    found = []
    for chain, scene in items:
        if stop is not None and stop.is_set():
            raise Stopped("Stopped.")
        found.append(render(engine, chain, dry, scene=scene, tail=tail, stop=stop))
    return found


# ---- what the desk holds ----

def stated(chain):
    """The chain with every value it leaves to the unit stated at the catalog's default.

    The knobs move stated values only. On the desk they work on the chain as the unit holds it, where every
    value is stated; a tool that applies knobs to a design must state its values first.
    """
    from dataclasses import replace

    from lab.sound import catalog

    for row, column, block in chain.blocks:
        defaults = catalog.defaults(block.model)
        if set(defaults) - set(block.values):
            chain = chain.with_block(row, column, replace(block, params={**defaults, **block.values}))
    return chain


def with_knobs(chain, values):
    from lab.sound.knobs import apply

    return apply(stated(chain), values) if values else chain


def heard_chain(sound):
    """The chain of a sound as it is heard on the desk: with its Shape knobs applied."""
    if sound is None:
        return None
    return with_knobs(sound.chain, sound.knobs)


def phrase(studio):
    """The desk's phrase as (name, mono audio), or None."""
    return studio.phrase


# ---- hearing on the computer ----

def pcm16(samples):
    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim == 1:
        samples = samples[:, None]
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16)


def write_pcm16(path, samples):
    from scipy.io import wavfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    wavfile.write(part, RATE, pcm16(samples))
    part.replace(path)
    return path


def play(samples, folder=None):
    """Play audio on the computer's own output, asynchronously. Returns the file played."""
    import winsound

    folder = Path(folder) if folder else Path(tempfile.gettempdir()) / "tone-lab-previews"
    path = write_pcm16(folder / "preview.wav", samples)
    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    return path


def silence():
    try:
        import winsound

        winsound.PlaySound(None, 0)
    except (ImportError, RuntimeError):
        pass


def gained(samples, db):
    return (np.asarray(samples, dtype=np.float32) * 10 ** (db / 20)).astype(np.float32)


# ---- widgets, in the desk's look, for the width of the desk's side panel ----

WIDTH = 380     # what fits across a tool: the side panel's 430, less its padding and a scrollbar


def label(parent, text="", *, size=10, color=INK, bold=False, wrap=0, variable=None):
    return tk.Label(parent, text=text, textvariable=variable, bg=parent["bg"], fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), justify="left", anchor="w", wraplength=wrap)


def button(parent, text, action, *, quiet=False):
    return tk.Button(parent, text=text, command=action, relief="flat", borderwidth=0, padx=8, pady=4,
                     cursor="hand2", font=(FONT, 9, "bold"), bg=WHITE if quiet else TILE, fg=INK,
                     activebackground=RULE, activeforeground=INK, disabledforeground=DIM)


def section(parent, title, note=""):
    """A part of a tool: a title, a quiet note under it, and what is packed into it."""
    part = tk.Frame(parent, bg=parent["bg"])
    part.pack(fill="x", pady=(0, 10))
    label(part, title, size=10, bold=True).pack(fill="x")
    if note:
        label(part, note, size=9, color=SUBTLE, wrap=WIDTH).pack(fill="x", pady=(2, 4))
    return part


def flow(parent, widgets, *, width=WIDTH, gap=4, unit=6):
    """Lay widgets (children of `parent`) out left to right, starting a new row when the next would pass `width`.

    Each row is its own: the grid's columns are a few pixels wide and a widget spans as many as it needs, so a
    wide widget in one row does not push the ones under it apart."""
    parent.update_idletasks()       # so that each widget knows the width it asks for
    row = column = most = 0
    for widget in widgets:
        span = -(-(widget.winfo_reqwidth() + gap) // unit)
        if column and (column + span) * unit > width:
            row, column = row + 1, 0
        widget.grid(row=row, column=column, columnspan=span, sticky="w", pady=1)
        column += span
        most = max(most, column)
    for index in range(most):
        parent.grid_columnconfigure(index, minsize=unit)
    return row + 1


def listbox(parent, height=10):
    return tk.Listbox(parent, bg=PAPER, fg=INK, selectbackground=TILE, selectforeground=INK, relief="flat",
                      highlightthickness=1, highlightbackground=RULE, highlightcolor=RULE, font=(FONT, 9),
                      height=height, activestyle="none", exportselection=False)


class Chips(tk.Frame):
    """One choice among a few, all on show: the chosen one is lit. In place of a drop-down.

    The choice is `variable`'s value; `command()` is called when it changes by a click. `offer` replaces the
    choices (for a list that depends on something else).
    """

    def __init__(self, parent, variable, options, *, command=None, width=WIDTH):
        super().__init__(parent, bg=parent["bg"])
        self.variable, self.command, self.width, self.chips = variable, command, width, {}
        self.enabled = True
        self.offer(options)
        self.watch = variable.trace_add("write", lambda *_: self.paint())
        self.bind("<Destroy>", lambda event: event.widget is self and variable.trace_remove("write", self.watch), add="+")

    def offer(self, options):
        for chip in self.chips.values():
            chip.destroy()
        self.chips = {}
        for option in options:
            chip = tk.Label(self, text=option, font=(FONT, 9), padx=7, pady=3, cursor="hand2")
            chip.bind("<ButtonRelease-1>", lambda _, option=option: self.choose(option))
            self.chips[option] = chip
        flow(self, self.chips.values(), width=self.width)
        self.paint()

    def choose(self, option):
        if self.enabled and option != self.variable.get():
            self.variable.set(option)
            if self.command:
                self.command()

    def enable(self, enabled):
        self.enabled = bool(enabled)
        self.paint()

    def paint(self):
        chosen = self.variable.get()
        for option, chip in self.chips.items():
            lit = option == chosen
            chip.configure(bg=TILE if lit else self["bg"], fg=(INK if lit else SOFT) if self.enabled else DIM)


def scrolled(parent):
    """A frame that scrolls up and down inside `parent` when what it holds is taller: (the canvas to place, the
    frame to build into). The bar is there only while there is something to scroll to; the wheel scrolls it
    (`wheel`, once the frame is built)."""
    from tkinter import ttk

    canvas = tk.Canvas(parent, bg=parent["bg"], highlightthickness=0, borderwidth=0)
    style = ttk.Style(parent)
    style.configure("ToneLab.Vertical.TScrollbar", background=RULE, troughcolor=parent["bg"], bordercolor=parent["bg"],
                    darkcolor=RULE, lightcolor=RULE, arrowcolor=SUBTLE, arrowsize=11)
    bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview, style="ToneLab.Vertical.TScrollbar")
    inner = tk.Frame(canvas, bg=parent["bg"])
    held = canvas.create_window((0, 0), anchor="nw", window=inner)

    def show(first, last):
        bar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            bar.pack_forget()
        elif not bar.winfo_manager():
            bar.pack(side="right", fill="y", before=canvas)

    canvas.configure(yscrollcommand=show)
    inner.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(held, width=event.width))
    canvas.inner = inner
    return canvas, inner


def wheel(canvas, within=None):
    """Let the mouse wheel scroll `canvas` from anywhere over what it holds that has no use of the wheel of its
    own (frames, labels, buttons): lists and sliders keep theirs."""
    def turn(event):
        first, last = canvas.yview()
        if first > 0 or last < 1:
            canvas.yview_scroll(-int(event.delta / 120), "units")

    def reach(widget):
        if isinstance(widget, (tk.Frame, tk.Label, tk.Button)) or widget is canvas:
            widget.bind("<MouseWheel>", turn)
        for child in widget.winfo_children():
            reach(child)

    reach(within or canvas)


# ---- choosing a file, in place ----

def player_home():
    """The player's own folder, under which Downloads, Music and Documents are looked in."""
    if os.environ.get("TONE_LAB_TESTING"):    # set by tests/isolated.py: no test lists the player's own folders
        return Path(os.environ["TONE_LAB_HOME"]) / "player"
    return Path.home()


def usual_folders(kind, folder):
    """Where files of a kind are usually kept, for the lists files are chosen from: the data folder `folder`
    first, then the player's own folders.

    "wav": recordings (the app's renders, and Downloads and Music); "pod": POD Go backups (where POD Go Edit
    writes them); "spotify": account exports (Downloads).
    """
    home, data = player_home(), Path(folder)
    return {"wav": [data / "imports", data / "renders", home / "Downloads", home / "Music"],
            "pod": [home / "Documents" / "Line 6" / "POD Go" / "Backups", data / "imports", home / "Downloads"],
            "spotify": [data / "imports", home / "Downloads"]}[kind]


def files_in(folders, patterns, *, limit=60):
    """The files matching `patterns` in `folders` and one folder down, newest first, each once."""
    found = {}
    for folder in folders:
        for pattern in patterns:
            for depth in ("", "*/"):
                try:
                    matches = list(Path(folder).glob(depth + pattern))
                except OSError:
                    continue
                for path in matches:
                    try:
                        if path.is_file():
                            found[path.resolve()] = path.stat().st_mtime
                    except OSError:
                        continue
    return sorted(found, key=found.get, reverse=True)[:limit]


def checked_file(text, patterns):
    """The file a typed or pasted path names. `ValueError` says why when it is not one that can be used."""
    from fnmatch import fnmatch

    text = text.strip().strip('"')
    if not text:
        raise ValueError("Type or paste the file's path, then press Return.")
    path = Path(text).expanduser()
    if path.is_dir():
        raise ValueError(f"{path} is a folder. Give the file itself.")
    if not path.is_file():
        raise ValueError(f"There is no file at {path}.")
    if not any(fnmatch(path.name.casefold(), pattern.casefold()) for pattern in patterns):
        raise ValueError(f"{path.name} is not one of {', '.join(patterns)}.")
    return path.resolve()


class FileChoice(tk.Frame):
    """Choosing a file in place, where a file dialog used to open: the files of the kind found where such files
    are usually kept, newest first, and a line to type or paste a path into, checked when Return is pressed in
    it. `choose(path)` is called with the file chosen (with a list of them when `several`).
    """

    def __init__(self, parent, *, folders, patterns, choose, several=False, height=4, action="Use this",
                 width=WIDTH):
        super().__init__(parent, bg=parent["bg"])
        self.folders, self.patterns, self.choose, self.several = folders, tuple(patterns), choose, several
        self.found = []
        self.list = listbox(self, height=height)
        if several:
            self.list.configure(selectmode="multiple")
        self.list.pack(fill="x")
        row = tk.Frame(self, bg=self["bg"])
        row.pack(fill="x", pady=(4, 0))
        button(row, action, self.use).pack(side="left")
        button(row, "Look again", self.look, quiet=True).pack(side="left", padx=4)
        label(self, "Or its path:", size=9, color=SUBTLE).pack(fill="x", pady=(4, 0))
        self.path = tk.Entry(self, font=(FONT, 9), bg=TILE, fg=INK, relief="flat", insertbackground=INK)
        self.path.pack(fill="x", ipady=3)
        self.path.bind("<Return>", lambda _: self.typed())
        self.said = label(self, size=9, color=SUBTLE, wrap=width)
        self.said.pack(fill="x")
        self.list.bind("<Double-Button-1>", lambda _: self.use())     # the same as the button beside it
        self.look()

    def look(self):
        folders = self.folders() if callable(self.folders) else self.folders
        self.found = files_in(folders, self.patterns)
        self.list.delete(0, "end")
        for path in self.found:
            self.list.insert("end", f"{path.name}  ·  {path.parent.name}")
        self.said.configure(text="" if self.found else "None found in " + ", ".join(str(folder) for folder in folders)
                            + ". Paste the path of one below.")

    def use(self):
        picked = [self.found[index] for index in self.list.curselection()]
        if not picked:
            self.said.configure(text="Choose one in the list first, or paste its path.")
            return
        self.said.configure(text="")
        self.choose(picked if self.several else picked[0])

    def typed(self):
        try:
            path = checked_file(self.path.get(), self.patterns)
        except ValueError as error:
            self.said.configure(text=str(error))
            return
        self.said.configure(text="")
        self.choose([path] if self.several else path)


def disclosure(parent, title, *, opened=False):
    """A part shown on request, in place: a line to click, and the frame it shows and hides."""
    content = tk.Frame(parent, bg=parent["bg"])
    control = tk.Label(parent, bg=parent["bg"], fg=ACCENT, font=(FONT, 9), cursor="hand2", anchor="w")

    def show(wanted):
        if wanted:
            content.pack(fill="x", pady=(4, 6), after=control)
        else:
            content.pack_forget()
        control.configure(text=f"{title} {'▴' if wanted else '▾'}")

    control.bind("<ButtonRelease-1>", lambda _: show(not content.winfo_manager()))
    control.pack(fill="x", pady=(2, 0))
    show(opened)
    content.show = show
    return content


# ---- work sent to the engine ----

_waiting = WeakKeyDictionary()     # studio -> how many operations sent here are not back yet


def send(studio, name, *args, done, failed, working="Working on the Quad Cortex…", renders=False):
    """Send an operation to the engine for something beside the desk's own work (a tool, a panel that borrows a
    tool's operation); `done(result)` or `failed(error)` runs on the window's thread. False when it was not
    sent: something else has the audio input (`studio.held_words()` says who).

    `renders` says the operation plays through the unit. Such an operation is not started while something else
    holds the audio input (the Guitar Learner, gestures); and since it stops whatever the desk was playing and
    takes A and B off the unit, the desk is told so at once: it shows nothing as playing, and makes A and B
    ready again the next time it plays.

    `working` is on the desk's working line, among whatever the desk itself is doing, until every operation
    sent here is back.
    """
    if renders:
        if studio.audio_held_by:
            return False
        studio.set(mode=None, shown=None)
    _waiting[studio] = _waiting.get(studio, 0) + 1
    studio.set(working=working)

    def back(handler):
        def handle(value):
            _waiting[studio] = _waiting.get(studio, 1) - 1
            if not _waiting[studio]:
                studio.set(working="")      # the tools' line only: what the desk itself is doing stays
            handler(value)
        return handle

    studio.later(studio.host.call(name, *args), back(done), back(failed))
    return True


# ---- a tool, in the desk's window ----

class Tool:
    """One side tool, shown in the desk's window in the place of its side panel (`Desk.show`).

    A tool builds into `self.body`, says things with `self.say`, reaches the unit with `self.call` and keeps
    time with `self.after`. It is made once and kept: going back to the desk hides it, and opening it again
    shows it as it was left, with whatever arrived meanwhile. What it asked of the unit goes on when it is
    hidden; what it plays on the computer stops.
    """

    TITLE = "Tool"

    @classmethod
    def open(cls, desk):
        tool = desk.tools.get(cls.TITLE)
        if tool is None:
            tool = desk.tools[cls.TITLE] = cls(desk)
        desk.show(tool)
        return tool

    def __init__(self, desk):
        self.desk, self.studio = desk, desk.studio
        self.folder = Path(self.studio.folder)
        self.pending = 0
        self.frame = desk.host_frame()
        self.status = tk.StringVar(master=self.frame)
        label(self.frame, variable=self.status, size=9, color=SOFT, wrap=WIDTH).pack(side="bottom", fill="x",
                                                                                      pady=(4, 0))
        self.scroller, self.body = scrolled(self.frame)
        self.scroller.pack(side="left", fill="both", expand=True)
        try:
            self.build()
        except Exception:
            self.frame.destroy()        # a tool that could not be built leaves nothing behind
            raise
        wheel(self.scroller)

    def build(self):
        raise NotImplementedError

    def alive(self):
        try:
            return bool(self.frame.winfo_exists())
        except tk.TclError:
            return False

    def showing(self):
        return self.desk.hosted is self

    def shown(self):
        """The desk has put the tool on show."""
        from lab.tools import usage

        usage.record(self.folder, "open", self.TITLE)

    def hidden(self):
        """The desk has gone back from the tool: what it plays on the computer stops."""
        from lab.tools import usage

        usage.record(self.folder, "close", self.TITLE)
        silence()

    def close(self):
        """Go back to the desk, if this tool is the one on show."""
        if self.showing():
            self.desk.back()

    def say(self, text):
        if self.alive():
            self.status.set(text)

    def after(self, milliseconds, action):
        return self.frame.after(milliseconds, action)

    def after_cancel(self, job):
        self.frame.after_cancel(job)

    @property
    def busy(self):
        return self.pending > 0

    def call(self, name, *args, done, working="Working on the Quad Cortex…", failed=None, renders=False):
        """Run a tool operation on the engine (`send`); `done(result)` runs on the window's thread when it is
        back, whether the tool is on show or not. Returns whether the operation was sent."""
        def finished(result):
            self.pending -= 1
            if self.alive():
                self.say("")
                done(result)

        def broke(error):
            self.pending -= 1
            (failed or (lambda error: self.say(str(error))))(error)

        if not send(self.studio, name, *args, done=finished, failed=broke, working=working, renders=renders):
            self.say(f"{self.studio.held_words()} Stop it there first.")
            return False
        self.pending += 1
        self.say(working)
        return True

    def stop(self):
        """End what the unit is doing for this tool at its next safe point (and whatever the desk was playing)."""
        self.studio.stop()
