"""What the tests of the app share: sounds, a simulated unit with an engine on it, a studio over a simulated
host, the real desk kept off the screen, waiting for work to finish, and listening that is worked out once.

**Waiting.** `settle` runs the studio's clock until every job is back; `settle_until` until one thing is true;
`until` waits for something that needs no clock. They wait for the work itself to end where they can (and look
every 2 ms where they cannot), so a test waits no longer than the work takes and does not keep the worker
from it by looking.

**Listening.** The app listens to every design by itself: three passes through the unit and an analysis of ten
seconds of test signals, about 0.2 s of work each time. The tests ask for the same few sounds over and over, so
`remember_what_was_heard` (on when this module is imported) keeps what the simulated unit returned for a signal
(for the last few dozen sounds) and what the analysis made of it, by what went in: the first time it is
worked out for real, by the app's own code; after that the same answer comes back at once. The unit is still
driven every time, and nothing a test sees differs. `listening_worked_out_each_time`
turns it off for a test that is about the working out itself.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen and the keyboard with whoever is typing)

from concurrent.futures import FIRST_COMPLETED, Future, wait  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from collections import OrderedDict  # noqa: E402
import copy  # noqa: E402
import gc  # noqa: E402
import hashlib  # noqa: E402
from pathlib import Path  # noqa: E402
from tempfile import TemporaryDirectory  # noqa: E402
import time  # noqa: E402
import tkinter as tk  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

import numpy as np  # noqa: E402

from lab.hearing import listen as listening, probe  # noqa: E402
from lab.qc.engine import MULTI_OUT, Engine  # noqa: E402
from lab.qc.host import Host  # noqa: E402
from lab.qc.sim import MY_PRESETS, SimAudio, SimUnit  # noqa: E402
from lab.qc.slots import Slots  # noqa: E402
from lab.qc.unit import Unit  # noqa: E402
from lab.sound import catalog  # noqa: E402
from lab.sound.chain import Block, Chain, Row  # noqa: E402

PLEXI, HIWATT, V30, GREENBACK, DELAY = 1081, 1063, 12001, 12006, 6011
DEFAULT_SIZE, SMALLEST_SIZE = (1100, 680), (980, 620)      # the desk's window as it opens, and at its minimum


# ---- sounds and phrases ----

def one_row(*models, output=None):
    """A sound on one row, from the guitar to Multi Out."""
    return Chain((Row(tuple(Block(model) for model in models), inlet=1, outlet=MULTI_OUT, output=output or {}),))


def chain(*models, gain_db=None, **settings):
    """A one-row chain from the guitar to Multi Out, with named settings (`**{"1081": {"GAIN": 7}}`) and a Gain
    block at the end when `gain_db` is given."""
    blocks = []
    for model_id in models:
        block = Block(model_id)
        for name, value in settings.get(str(model_id), {}).items():
            block = block.set_display(catalog.model(model_id).index(name), value)
        blocks.append(block)
    if gain_db is not None:
        blocks.append(Block(catalog.GAIN_BLOCK).set_display(0, gain_db))
    return Chain((Row(tuple(blocks), inlet=1, outlet=MULTI_OUT),))


def note(seconds=.5, level=.2):
    """A steady G as a dry phrase: one column of samples."""
    return (np.sin(np.arange(round(48000 * seconds)) / 48000 * 2 * np.pi * 196) * level)[:, None].astype(np.float32)


def phrase(seconds=1.5, level=.2):
    """A G plucked every three quarters of a second."""
    time_ = np.arange(round(48000 * seconds)) / 48000
    plucked = level * np.sin(2 * np.pi * 196 * time_) * np.exp(-1.5 * (time_ % .75))
    return plucked[:, None].astype(np.float32)


# ---- the unit ----

def inputs(device):
    """The external inputs connected on the simulated unit's working preset."""
    return [lane.in_portid for lane in device.working.chains if lane.in_portid not in (0, 16, 17, 18)]


def fast(device):
    """The guarded unit over a device, without the waits a real unit needs."""
    return Unit(device, settle=.05, sleep=lambda seconds: None)


def engine_on(device, folder):
    """An engine on a device, keeping its records in `folder`. Not yet opened."""
    unit = fast(device)
    return Engine(unit, Slots(unit, Path(folder)), SimAudio(device))


def host(folder):
    """A simulated unit held on a thread of this process, ready."""
    found = Host(Path(folder), simulated=True, local=True)
    found.ready.result(10)
    return found


# ---- waiting ----

def until(test, seconds=10, what="what was waited for"):
    """Wait for `test()` to be true and return what it gave. Fails the test when it does not come."""
    deadline = time.monotonic() + seconds
    while True:
        found = test()
        if found:
            return found
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} did not happen within {seconds} s")
        time.sleep(.002)


def _a_moment(studio, root):
    """Wait until one of the studio's jobs ends, or 2 ms when there is none to wait on (20 ms at most, and 5 ms
    with a window, whose own clock must go on)."""
    waiting = [job[0] for job in studio.jobs if isinstance(job[0], Future) and type(job[0]).done is Future.done]
    if waiting:
        wait(waiting, timeout=.005 if root is not None else .02, return_when=FIRST_COMPLETED)
    else:
        time.sleep(.002)


def settle(studio, seconds=15, root=None):
    """Run the studio's clock (and the window's, given its `root`) until every job the studio started is back
    and the unit's worker is idle."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        studio.poll()
        if root is not None:
            root.update()
        if not studio.jobs and not studio.host.busy:
            studio.poll()
            if not studio.jobs:
                return
        _a_moment(studio, root)
    raise AssertionError("work did not finish")


def settle_until(studio, test, seconds=15, what="what was waited for", root=None):
    """Run the studio's clock until `test()` is true, whatever else is still on its way."""
    deadline = time.monotonic() + seconds
    while not test():
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} did not come within {seconds} s")
        studio.poll()
        if root is not None:
            root.update()
        _a_moment(studio, root)


def pump(studio, root=None, seconds=30):
    """`settle` for a studio shown in a window."""
    settle(studio, seconds, root)


# ---- freeing what is no longer used ----

_set_aside = []


def free_now():
    """Free what is no longer used, here and now. A window and its Tk variables may only be freed on the thread
    of the window, so a test that made one lets go of it and calls this before a worker thread can get to it.

    A collection looks at everything the process holds, and by the time the tests run that is a great deal that
    will never be freed (the app, its tables, the tests themselves): a third of a second each time, some 150
    times in a run. So the first time, what is there is set aside for good (`gc.freeze`), and from then on a
    collection looks only at what was made since."""
    gc.collect()
    if not _set_aside:
        _set_aside.append(True)
        gc.freeze()


# ---- listening that is worked out once ----

_heard = {}             # (what was asked, a digest of what went in) -> what came back


def _digest(*parts):
    found = hashlib.blake2b(digest_size=16)
    for part in parts:
        found.update(np.ascontiguousarray(part).tobytes() if isinstance(part, np.ndarray) else repr(part).encode())
    return found.digest()


def _once(name, key, work):
    if (name, key) not in _heard:
        _heard[name, key] = work()
    return copy.deepcopy(_heard[name, key])


class _ProbeOnce:
    """`lab.hearing.probe` as `lab.hearing.listen` sees it: the test signals and what is made of their return are
    worked out once for each input. Everything else is the module's own."""

    def __getattr__(self, name):
        return getattr(probe, name)

    def test_signal(self, *args, **options):
        return _once("test signal", _digest(args, sorted(options.items())), lambda: probe.test_signal(*args, **options))

    def analyse(self, returned, *args, **options):
        key = _digest(returned, args, sorted(options.items()))
        return _once("analyse", key, lambda: probe.analyse(returned, *args, **options))


_made_of, _reamp = listening.heard, SimAudio.reamp
_returned = OrderedDict()       # what the simulated unit returned, for the sounds asked for last
RETURNS_KEPT = 48               # ten seconds of test signals are 4 MB each time


def _reamp_once(audio, dry, *, tail=2., cancel=None):
    if type(audio).reamp is not _reamp_once or (cancel is not None and cancel.is_set()):
        return _reamp(audio, dry, tail=tail, cancel=cancel)        # another unit's audio, or a replay to end
    key = _digest(audio.unit.working.SerializeToString(), audio.unit.scene, tail, dry)
    if key in _returned:
        _returned.move_to_end(key)
    else:
        _returned[key] = _reamp(audio, dry, tail=tail, cancel=cancel)
        while len(_returned) > RETURNS_KEPT:
            _returned.popitem(last=False)
    return _returned[key].copy()


def _made_of_once(dry, loud, soft, tail, **options):
    key = _digest(dry, loud, soft, tail, sorted((name, repr(value)) for name, value in options.items()))
    return _once("heard", key, lambda: _made_of(dry, loud, soft, tail, **options))


def remember_what_was_heard(on=True):
    """Keep what the simulated unit returns and what `lab.hearing.listen.listen` works out from it, by what
    went into it (see the top of this module). Only that function is given the remembering analysis; the tests
    of the analysis itself call the app's own. `on=False` gives the app's own back."""
    listening.probe = _ProbeOnce() if on else probe
    listening.heard = _made_of_once if on else _made_of
    SimAudio.reamp = _reamp_once if on else _reamp


@contextmanager
def listening_worked_out_each_time():
    """For a test about the working out itself: nothing is remembered while this lasts."""
    remember_what_was_heard(False)
    try:
        yield
    finally:
        remember_what_was_heard(True)


remember_what_was_heard()


# ---- cases ----

class EngineCase(unittest.TestCase):
    """A simulated unit with a preset of the player's selected, and an engine opened on it."""

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.device = SimUnit()
        self.device.save_slot(MY_PRESETS, 5, "Mine", [(PLEXI, {}), (V30, {})])
        self.device.recall(MY_PRESETS, 5)
        self.unit = fast(self.device)
        self.slots = Slots(self.unit, Path(folder.name))
        self.engine = Engine(self.unit, self.slots, SimAudio(self.device))
        self.engine.open()


class StudioCase(unittest.TestCase):
    """A studio over a simulated host, with a phrase and a scripted assistant (`self.script`: its `answers` are
    handed out in order and `asked` keeps what it was asked). No window.

    - `automatic`: whether the app may ask the assistant on its own.
    - `idle_seconds`: how long the unit is left alone before the app listens by itself; None leaves it as the
      app has it (2 s, so a short test is over before any listening starts), 0 listens at once.
    - `answers`: a function to answer in the script's place, for a test that holds an answer back.

    The desk is imported here and not at the top, so that the tests of the unit and the engine, which share this
    module, do not depend on it.
    """

    automatic = False
    idle_seconds = None
    answers = None

    def setUp(self):
        from lab.desk.studio import Studio
        from lab.sound.assistant import Assistant
        from test_lab_design import Scripted

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.host = host(self.folder / "unit")
        self.script = Scripted()
        answering = type(self).answers or self.script
        self.studio = Studio(self.host, self.folder,
                             assistant=Assistant(self.folder, automatic=self.automatic, run=answering),
                             phrase=("take", (np.sin(np.arange(24000) / 48000 * 2 * np.pi * 196) * .2)[:, None]))
        if self.idle_seconds is not None:
            self.studio.idle_seconds = self.idle_seconds
        self.addCleanup(self.studio.close)
        self.studio.open()
        self.settle()

    def settle(self, seconds=20):
        settle(self.studio, seconds)

    def until(self, test, seconds=10, what="what was waited for"):
        settle_until(self.studio, test, seconds, what)

    def put_two(self):
        from lab.sound.sounds import Sound

        self.studio.put("a", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.studio.put("b", Sound("Hiwatt", one_row(HIWATT, GREENBACK), {"kind": "designed"}))
        self.settle()


# ---- the desk's window ----

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
    """The real desk on a simulated unit, kept off the screen, with a scripted assistant. Its data folder is
    `self.home`; the player's own folders (Downloads, Documents, Music), where files are looked for, are made up
    under `self.player`."""

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
        self.addCleanup(free_now)
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
        settle(self.studio, 30, self.root)

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
