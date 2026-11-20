"""What the side tools share: a window in the desk's look, work sent to the engine, and silent renders.

A tool's window talks to the unit only through `Window.call`, which sends one
of the tool's own operations to the engine (`host.call("lab.tools.x.op")`)
and hands the result back on the window's thread through the studio's clock.

`render` is the operation most tools need: the phrase played through a chain
on the unit and returned over USB, with nothing routed to the unit's outputs.
It builds the chain once and keeps it in the cache like any other, so a sound
rendered by a tool plays at once on the desk afterwards, and the reverse.

Rendered sound is heard on the computer's own output (`play`), level-matched
by the tool that made it, as the old workbenches did. The guitar and the
desk's phrase still go through the unit.
"""

from pathlib import Path
import tempfile
import tkinter as tk

import numpy as np

from lab.hearing.audio import RATE
from lab.sound.chain import routed
from lab.qc.rig import USB
from lab.desk.theme import DARK, DIM, FONT, INK, PAPER, RULE, SOFT, SUBTLE, TILE, WHITE

SCENE_LETTERS = "ABCDEFGH"


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
            raise RuntimeError("Stopped.")
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
    return getattr(studio, "phrase", None)


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


# ---- the window ----

def label(parent, text="", *, size=10, color=INK, bold=False, wrap=0, variable=None):
    return tk.Label(parent, text=text, textvariable=variable, bg=parent["bg"], fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), justify="left", anchor="w", wraplength=wrap)


def button(parent, text, action, *, quiet=False):
    return tk.Button(parent, text=text, command=action, relief="flat", borderwidth=0, padx=10, pady=5,
                     cursor="hand2", font=(FONT, 9, "bold"), bg=WHITE if quiet else TILE, fg=INK,
                     activebackground=RULE, activeforeground=INK, disabledforeground=DIM)


def section(parent, title, note=""):
    """A dark card with a title and a quiet note under it."""
    card = tk.Frame(parent, bg=DARK, padx=14, pady=10)
    card.pack(fill="x", pady=(0, 10))
    label(card, title, size=11, bold=True).pack(fill="x")
    if note:
        label(card, note, size=9, color=SUBTLE, wrap=520).pack(fill="x", pady=(2, 6))
    return card


def menu_choice(parent, variable, options, *, command=None):
    """A quiet drop-down of strings."""
    chooser = tk.Menubutton(parent, textvariable=variable, bg=TILE, fg=INK, relief="flat", font=(FONT, 9),
                            cursor="hand2", padx=8, pady=4, activebackground=RULE, activeforeground=INK)
    menu = tk.Menu(chooser, tearoff=False, bg=WHITE, fg=INK)
    for option in options:
        menu.add_radiobutton(label=option, value=option, variable=variable, command=command)
    chooser.configure(menu=menu)
    return chooser


def listbox(parent, height=10):
    return tk.Listbox(parent, bg=PAPER, fg=INK, selectbackground=TILE, selectforeground=INK, relief="flat",
                      highlightthickness=1, highlightbackground=RULE, highlightcolor=RULE, font=(FONT, 9),
                      height=height, activestyle="none", exportselection=False)


class Window:
    """One tool's window over the desk. Opening a tool that is open brings it forward."""

    TITLE = "Tool"
    SIZE = "600x560"

    @classmethod
    def open(cls, desk):
        windows = getattr(desk, "tool_windows", None)
        if windows is None:
            windows = {}
            try:
                desk.tool_windows = windows
            except AttributeError:
                pass
        found = windows.get(cls.TITLE)
        if found is not None and found.alive():
            found.top.deiconify()
            found.top.lift()
            return found
        window = cls(desk)
        windows[cls.TITLE] = window
        return window

    def __init__(self, desk):
        self.desk, self.studio = desk, desk.studio
        self.folder = Path(getattr(self.studio, "folder", Path(tempfile.gettempdir())))
        self.pending = 0
        self.top = tk.Toplevel(desk.root)
        self.top.title(self.TITLE)
        self.top.configure(bg=PAPER)
        self.top.geometry(self.SIZE)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        outer = tk.Frame(self.top, bg=PAPER, padx=18, pady=14)
        outer.pack(fill="both", expand=True)
        self.status = tk.StringVar(master=self.top)
        label(outer, variable=self.status, size=9, color=SOFT, wrap=560).pack(side="bottom", fill="x", pady=(6, 0))
        self.body = tk.Frame(outer, bg=PAPER)
        self.body.pack(fill="both", expand=True)
        from lab.tools import usage

        usage.record(self.folder, "open", self.TITLE)
        self.build()

    def build(self):
        raise NotImplementedError

    def alive(self):
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def say(self, text):
        if self.alive():
            self.status.set(text)

    @property
    def busy(self):
        return self.pending > 0

    def call(self, name, *args, done, working="Working on the Quad Cortex…", failed=None):
        """Run a tool operation on the engine; `done(result)` runs on the window's thread when it is back."""
        self.pending += 1
        self.say(working)
        self.studio.set(working=working) if hasattr(self.studio, "set") else None

        def finished(result):
            self.pending -= 1
            self._idle()
            if self.alive():
                self.say("")
                done(result)

        def broke(error):
            self.pending -= 1
            self._idle()
            (failed or (lambda error: self.say(str(error))))(error)

        self.studio.later(self.studio.host.call(name, *args), finished, broke)

    def _idle(self):
        if not self.pending and hasattr(self.studio, "set"):
            self.studio.set(working="")

    def close(self):
        from lab.tools import usage

        usage.record(self.folder, "close", self.TITLE)
        silence()
        try:
            self.top.destroy()
        except tk.TclError:
            pass
