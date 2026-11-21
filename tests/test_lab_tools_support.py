"""What the side tools' tests share: a simulated host, sounds, a phrase, and the real desk, kept off the screen,
to show the tools in."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import gc
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

import numpy as np

from lab.sound import catalog
from lab.sound.chain import Block, Chain, Row
from lab.qc.engine import MULTI_OUT
from lab.qc.host import Host

PLEXI, HIWATT, V30, GREENBACK, DELAY = 1081, 1063, 12001, 12006, 6011


def keep_update_from_waiting(every=.1):
    """Keep `update()` from waiting for ever, by posting an empty message to the main thread ten times a second.

    Found while the tools were moved into the desk's window (measured with the native stack of the stopped
    thread, in the scratch folder): on Windows `update` sometimes does not come back. Tcl's notifier looks
    whether a message is there (`PeekMessage`), and if one is, fetches it with `GetMessage`, which waits until
    one comes. When the message it saw is gone by then (a paint or mouse message that Windows only makes up
    while it is still wanted, taken away by a change of the active window), `GetMessage` waits for the next
    one, and with nobody at the computer none comes: the run stood still inside `root.update()` with no
    processor time used, in about half of the runs of test_lab_one_window. The app itself is not at risk: its
    `mainloop` waits there on purpose. An empty message (`WM_NULL` to the thread, which no window gets) is
    enough to end the wait, and Tk does nothing with it.

    Called once, when this module is imported, which is before any test of the run. The thread is a daemon and
    ends with the process."""
    if os.name != "nt" or getattr(keep_update_from_waiting, "started", False):
        return
    import ctypes

    post, main = ctypes.windll.user32.PostThreadMessageW, threading.main_thread().native_id

    def beat():
        while True:
            time.sleep(every)
            post(main, 0, 0, 0)

    keep_update_from_waiting.started = True
    threading.Thread(target=beat, name="keeps-update-from-waiting", daemon=True).start()


keep_update_from_waiting()
DEFAULT_SIZE, SMALLEST_SIZE = (1100, 680), (980, 620)      # the desk's window as it opens, and at its minimum


def chain(*models, gain_db=None, **settings):
    """A one-row chain from the guitar to Multi Out, with a Gain block at the end when `gain_db` is given."""
    blocks = []
    for model_id in models:
        block = Block(model_id)
        for name, value in settings.get(str(model_id), {}).items():
            block = block.set_display(catalog.model(model_id).index(name), value)
        blocks.append(block)
    if gain_db is not None:
        blocks.append(Block(catalog.GAIN_BLOCK).set_display(0, gain_db))
    return Chain((Row(tuple(blocks), inlet=1, outlet=MULTI_OUT),))


def phrase(seconds=1.5, level=.2):
    time_ = np.arange(round(48000 * seconds)) / 48000
    note = level * np.sin(2 * np.pi * 196 * time_) * np.exp(-1.5 * (time_ % .75))
    return note[:, None].astype(np.float32)


def host(folder):
    found = Host(Path(folder), simulated=True, local=True)
    found.ready.result(10)
    return found


def pump(studio, root=None, seconds=30):
    """Run the studio's clock until every job sent to the engine is back."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        studio.poll()
        if root is not None:
            root.update()
        if not studio.jobs and not studio.host.busy:
            studio.poll()
            if not studio.jobs:
                return
        time.sleep(.02)
    raise TimeoutError("The engine did not answer.")


def hosting(desk, root):
    """Give a stand-in for the desk the place where the real one shows a tool, the Guitar Learner or the
    teaching of a control (`Desk.host_frame`, `show`, `back`, `hosted`), the way the real one does it."""
    desk.side = tk.Frame(root)
    desk.side.pack(fill="both", expand=True)
    desk.hosted = None

    def hide():
        thing, desk.hosted = desk.hosted, None
        if thing is not None:
            thing.frame.pack_forget()
            thing.hidden()

    def show(thing):
        if desk.hosted is thing:
            return
        hide()
        desk.hosted = thing
        thing.frame.pack(fill="both", expand=True)
        thing.shown()

    desk.host_frame, desk.show, desk.back = (lambda: tk.Frame(desk.side)), show, hide
    return desk


def wide_children(widget, limit):
    """The widgets inside `widget` that ask for more width than `limit`, as (class, text or name, width asked)."""
    found = []
    for child in widget.winfo_children():
        if child.winfo_manager() and child.winfo_reqwidth() > limit:
            try:
                said = child.cget("text")
            except tk.TclError:
                said = ""
            found.append((child.winfo_class(), str(said)[:40] or str(child), child.winfo_reqwidth()))
        found += wide_children(child, limit)
    return found


def inside(widget, outer):
    """Whether a mapped widget lies wholly inside another, on the screen's coordinates."""
    left, top = widget.winfo_rootx(), widget.winfo_rooty()
    right, bottom = left + widget.winfo_width(), top + widget.winfo_height()
    return (left >= outer.winfo_rootx() and top >= outer.winfo_rooty()
            and right <= outer.winfo_rootx() + outer.winfo_width()
            and bottom <= outer.winfo_rooty() + outer.winfo_height())


class DeskCase(unittest.TestCase):
    """The real desk on a simulated unit, kept off the screen. Its data folder is `self.home`; the player's own
    folders (Downloads, Documents, Music), where files are looked for, are made up under `self.player`."""

    def setUp(self):
        from lab.tools import common

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home, self.player = Path(folder.name) / "data", Path(folder.name) / "player"
        self.home.mkdir()
        self.player.mkdir()
        patcher = mock.patch.object(common, "player_home", lambda: self.player)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The window and its Tk variables are freed here, on this thread, not later on another one: the workers
        # are waited for and the test lets go of the desk first, so that the collection that follows finds it.
        self.studio = self.desk = self.root = None
        self.addCleanup(gc.collect)
        self.addCleanup(self.let_go)
        self.open()

    def let_go(self):
        if self.studio is not None:
            self.studio.thinking.shutdown(wait=True)
            self.studio.reading.shutdown(wait=True)
        self.studio = self.desk = self.root = None

    def open(self):
        from lab.desk.studio import Studio
        from lab.desk.window import Desk
        from lab.sound.assistant import Assistant
        from test_lab_design import Scripted

        self.script = Scripted()
        self.studio = Studio(host(self.home), self.home, assistant=Assistant(self.home, automatic=False, run=self.script))
        self.studio.idle_seconds = 1e9          # nothing is listened to by itself while a test looks at a tool
        self.root = tk.Tk()
        self.desk = Desk(self.root, self.studio)
        self.addCleanup(self.root.destroy)
        self.addCleanup(self.studio.close)
        self.studio.phrase = ("phrase-1", phrase())
        self.pump()

    def pump(self):
        pump(self.studio, self.root)

    def put(self, side, sound):
        self.studio.put(side, sound)
        self.pump()

    def tool(self, name):
        """Open a side tool as the More panel does."""
        from lab.tools.menu import opener

        tool = opener(name)(self.desk)
        self.root.update()
        return tool

    def resize(self, size):
        self.root.geometry(f"{size[0]}x{size[1]}")
        self.root.update()

    def assert_fits(self, thing, title):
        """What is hosted is in the desk's own window, fits the side panel's place across, lies inside the window,
        and leaves both tiles on the screen: at the size the desk opens with and at its smallest."""
        self.assertIs(self.desk.hosted, thing)
        self.assertEqual(self.desk.side_title.cget("text"), title)
        self.assertEqual(thing.frame.winfo_toplevel(), self.root)
        for size in (DEFAULT_SIZE, SMALLEST_SIZE):
            self.resize(size)
            # What is shown scrolls up and down when it is taller than its place; across, everything must fit
            # beside the scrollbar.
            across = thing.scroller.winfo_width()
            self.assertGreater(across, 300, size)
            self.assertEqual(wide_children(thing.scroller, across), [], size)
            self.assertTrue(inside(self.desk.side, self.root), size)
            self.assertTrue(inside(thing.frame, self.desk.side), size)
            for side in ("a", "b"):
                tile = self.desk.tiles[side][0]
                self.assertTrue(tile.winfo_ismapped() and inside(tile, self.root), (size, side))
                self.assertGreater(tile.winfo_width(), 120, (size, side))
        self.resize(DEFAULT_SIZE)

    def texts(self, widget):
        """Every text shown inside a widget."""
        found = []
        for child in widget.winfo_children():
            if isinstance(child, (tk.Label, tk.Button)) and child.winfo_manager():
                found.append(str(child.cget("text")))
            found += self.texts(child)
        return found
