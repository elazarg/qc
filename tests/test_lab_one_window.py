"""One window: no dialogs, no pop-up windows or menus, no keyboard shortcuts, nothing opened outside it.

Two kinds of check. The code of the app is read: nothing in `lab/` may name a second window, a dialog, a menu,
a drop-down or the browser; a key may be bound only where this file says so, with the reason; and so may the
mouse be watched over the whole window, or the pointer resting on something. And the things that used to have
windows of their own are opened on the real desk, kept off the screen. Every side tool is shown in the desk's
own window in the place of the side panel, fits it across at the size the desk opens with and at its smallest,
leaves both tiles on the screen, and goes back to the panel it was opened from. The Guitar Learner and Teach a
control are the two views of the learning screen, which takes the place of everything below the top row and
gives the desk back as it was.

What the reading cannot see is a caption that advertises a key, and what the measuring cannot see is how any
of it looks.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import ast
from functools import lru_cache
import json
from pathlib import Path
import re
from threading import Event
import tkinter as tk
import unittest

from lab.qc.sim import FACTORY
from lab.sound.sounds import Sound
from lab.tools.menu import TOOLS

from support import DEFAULT_SIZE, DELAY, PLEXI, SMALLEST_SIZE, V30, DeskCase, chain, inside, wide_children

LAB = Path(__file__).resolve().parents[1] / "lab"

# What may not be named anywhere in the app, and why.
FORBIDDEN = {
    "Toplevel": "a second window",
    "filedialog": "a file dialog: choose the file in place (lab.tools.common.FileChoice)",
    "messagebox": "a dialog: say it on the line",
    "simpledialog": "a dialog: type it in place",
    "Menu": "a pop-up menu: use chips or a list in place",
    "Menubutton": "a pop-up menu: use chips or a list in place",
    "OptionMenu": "a drop-down: use chips (lab.tools.common.Chips)",
    "Combobox": "a drop-down: use chips, with an entry for something new",
    "webbrowser": "the browser: show the address as text, in place",
}
# The one place a Tk root is made: the desk's own window.
ROOTS = {"desk/window.py": 1}
# Events of the mouse, of a widget's own life and of a list's selection: none of them is a key.
NOT_A_KEY = re.compile(r"^<(Button(Press|Release)?(-\d)?|Double-Button-\d|B\d-Motion|Motion|MouseWheel|Enter|Leave|"
                       r"Configure|Destroy)>$|^<<\w+>>$")
# Every key bound in the app, by file: (the event, how many times, why it is not a shortcut).
KEYS = {
    "desk/window.py": [
        ("<Return>", 2, "Return in the Ask line sends the request; Return in the name typed for a save saves it"),
        ("<KeyRelease>", 2, "the lists of presets and of IRs and captures follow what is typed in their filter"),
    ],
    "tools/common.py": [("<Return>", 1, "Return in the line a file's path is typed into checks and takes that path")],
    "tools/search.py": [("<Return>", 1, "Return in the line the words are typed into finds by them")],
    "tools/workbench.py": [("<Return>", 1, "Return in the line a request or values are typed into renders them")],
}
# The mouse watched over the whole window (bound on the window itself, not on the thing clicked), by file: the
# event and why. Anything else that wants a click is bound on what is clicked.
WINDOW_WIDE = {
    "desk/chooser.py": [("<ButtonPress-1>", "a press anywhere but on the instrument chooser or in its list puts "
                                            "the list away")],
    "desk/hints.py": [("<ButtonPress-1>", "a press anywhere takes away the words a click on a ? left on show")],
    "desk/window.py": [("<Destroy>", "the desk's clock is stopped when the window goes")],
}
# The pointer resting on something (<Enter> and <Leave>), by file, and what it does: it never does more than show.
HOVER = {
    "desk/chooser.py": "the row of the instrument list under the pointer is lit",
    "desk/hints.py": "a ? shows its words, and a drawn symbol its name, while the pointer rests on it",
}


# Every double click in the app, by file, with the button on show that does the same: none is the only way.
DOUBLE_CLICKS = {
    "desk/window.py": ["Work on this"],
    "learner/window.py": ["Whole clip", "Open this take", "Open recording"],
    "tools/common.py": ["Use this"],            # a file chooser's own button, whatever each tool names it
    "tools/workbench.py": ["Open"],
}


def name_of(node):
    return node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None


@lru_cache(maxsize=None)
def read(path):
    """What a file of the app names and binds: (forbidden names with their lines, Tk roots made, events bound,
    the events among them that are bound on the window itself: on something called `top` or `root`).
    Each file is read once for all the tests here."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    named, roots, bound, wide = [], 0, [], []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                for part in alias.name.split("."):
                    if part in FORBIDDEN:
                        named.append((part, node.lineno))
        elif isinstance(node, (ast.Attribute, ast.Name)) and name_of(node) in FORBIDDEN:
            named.append((name_of(node), node.lineno))
        if isinstance(node, ast.Call):
            called = name_of(node.func)
            if called == "Tk":
                roots += 1
            if called in ("bind", "bind_all", "bind_class") and node.args:
                event = node.args[1] if called == "bind_class" and len(node.args) > 1 else node.args[0]
                if isinstance(event, ast.Constant) and isinstance(event.value, str):
                    bound.append((event.value, node.lineno))
                    if isinstance(node.func, ast.Attribute) and name_of(node.func.value) in ("top", "root"):
                        wide.append((event.value, node.lineno))
                elif isinstance(event, ast.JoinedStr):
                    bound.append(("an event put together when it runs", node.lineno))
                elif not isinstance(event, ast.Tuple):          # a tuple is a socket's address, not an event
                    bound.append(("an event that cannot be read here", node.lineno))
    return named, roots, bound, wide


class NothingOpensOutsideTheWindowTest(unittest.TestCase):
    def files(self):
        found = sorted(LAB.rglob("*.py"))
        self.assertGreater(len(found), 60)
        return [(path.relative_to(LAB).as_posix(), path) for path in found]

    def test_nothing_in_the_app_names_a_second_window_a_dialog_a_menu_a_drop_down_or_the_browser(self):
        found = [f"lab/{name}:{line}: {word} ({FORBIDDEN[word]})"
                 for name, path in self.files() for word, line in read(path)[0]]
        self.assertEqual(found, [])

    def test_the_only_tk_root_is_the_desks_own_window(self):
        self.assertEqual({name: read(path)[1] for name, path in self.files() if read(path)[1]}, ROOTS)

    def test_a_key_is_bound_only_where_it_is_listed_here_with_the_reason(self):
        found = {}
        for name, path in self.files():
            for event, line in read(path)[2]:
                if not NOT_A_KEY.match(event):
                    found.setdefault(name, []).append(event)
        allowed = {name: sorted(event for event, count, _ in rows for _ in range(count)) for name, rows in KEYS.items()}
        self.assertEqual({name: sorted(events) for name, events in found.items()}, allowed)
        for rows in KEYS.values():
            for event, _, why in rows:
                self.assertIn(event, ("<Return>", "<KeyRelease>"))      # never a shortcut: only an entry's own keys
                self.assertGreater(len(why), 20)

    def test_the_mouse_is_watched_over_the_whole_window_only_where_it_is_listed_here_with_the_reason(self):
        found = {name: sorted(event for event, _ in read(path)[3]) for name, path in self.files() if read(path)[3]}
        self.assertEqual(found, {name: sorted(event for event, _ in rows) for name, rows in WINDOW_WIDE.items()})
        for rows in WINDOW_WIDE.values():
            for event, why in rows:
                self.assertTrue(NOT_A_KEY.match(event) and len(why) > 20, event)

    def test_the_pointer_resting_on_something_does_something_only_where_it_is_listed_here(self):
        found = {name for name, path in self.files()
                 if any(event in ("<Enter>", "<Leave>") for event, _ in read(path)[2])}
        self.assertEqual(found, set(HOVER))
        self.assertTrue(all(len(why) > 20 for why in HOVER.values()))

    def test_every_double_click_has_a_button_on_show_that_does_the_same(self):
        found = {}
        for name, path in self.files():
            doubles = [event for event, _ in read(path)[2] if event.startswith("<Double-")]
            if doubles:
                found[name] = len(doubles)
                source = path.read_text(encoding="utf-8")
                for caption in DOUBLE_CLICKS.get(name, []):
                    self.assertIn(f'"{caption}"', source, (name, caption))
        self.assertEqual(found, {name: len(buttons) for name, buttons in DOUBLE_CLICKS.items()})

    def test_the_reading_itself_sees_what_it_is_for(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / "sample.py"
            sample.write_text("import tkinter as tk\nfrom tkinter import filedialog\nimport webbrowser\n"
                              "top = tk.Toplevel()\nmenu = tk.Menu(top)\nroot = tk.Tk()\n"
                              "top.bind('<Escape>', print)\ntop.bind('<Button-1>', print)\n"
                              "top.bind(f'<{key}>', print)\ndoor.bind(('127.0.0.1', 1))\n", encoding="utf-8")
            named, roots, bound, wide = read(sample)
        self.assertEqual([event for event, _ in wide], ["<Escape>", "<Button-1>"])       # both bound on `top`
        self.assertEqual({word for word, _ in named}, {"filedialog", "webbrowser", "Toplevel", "Menu"})
        self.assertEqual(roots, 1)
        self.assertEqual([event for event, _ in bound if not NOT_A_KEY.match(event)],
                         ["<Escape>", "an event put together when it runs"])


def all_widgets(widget):
    found = []
    for child in widget.winfo_children():
        found.append(child)
        found += all_widgets(child)
    return found


def open_every_part(widget):
    """Open every part shown on request (`lab.tools.common.disclosure`: a frame with a `show`) inside a widget."""
    for part in all_widgets(widget):
        if isinstance(part, tk.Frame) and callable(getattr(part, "show", None)):
            part.show(True)


def pushed_out(widget, window):
    """What is on show inside `widget` and does not lie wholly inside the window, as (class, text)."""
    found = []
    for part in all_widgets(widget):
        if part.winfo_class() in ("Button", "Label", "Listbox", "Entry", "Canvas", "Checkbutton") \
                and part.winfo_ismapped() and not inside(part, window):
            found.append((part.winfo_class(), str(part.cget("text"))[:30] if part.winfo_class() != "Canvas"
                          and part.winfo_class() != "Listbox" and part.winfo_class() != "Entry" else ""))
    return found


class HostedTest(DeskCase):
    """Each side tool, which had a window of its own, in the desk's."""

    def setUp(self):
        super().setUp()
        self.put("a", Sound("Clean", chain(gain_db=0.), {"kind": "designed"}))
        self.put("b", Sound("Plexi", chain(PLEXI, V30, DELAY), {"kind": "designed"}))

    def test_every_tool_is_shown_in_the_desks_window_fits_and_goes_back_to_the_more_panel(self):
        self.desk.toggle("More")
        for title, name in TOOLS:
            with self.subTest(tool=title):
                tool = self.tool(name)
                self.assert_fits(tool, title)
                self.assertEqual(self.desk.tray.winfo_manager(), "")        # in the side panel's place
                self.desk.back()
                self.root.update()
                self.assertIsNone(self.desk.hosted)
                self.assertEqual(self.desk.side.winfo_manager(), "")
                self.assertEqual((self.desk.panel, self.desk.tray.winfo_manager()), ("More", "grid"))
        self.assertEqual(self.root.winfo_children()[0].winfo_toplevel(), self.root)
        self.assertEqual([child.winfo_class() for child in self.root.winfo_children()], ["Frame"])   # one page

    def test_the_tools_main_part_fits_without_scrolling_where_it_was_made_to(self):
        """At the size the desk opens with, these show all of what they open with. The others (the choice of a
        backup or an export while none is chosen yet, the Guitar Learner, the teaching) scroll."""
        for name in ("levels", "search", "field", "workbench", "controls", "soundcheck", "usage"):
            with self.subTest(tool=name):
                tool = self.tool(name)
                self.assertLessEqual(tool.body.winfo_reqheight(), tool.scroller.winfo_height())

    def test_one_thing_at_a_time_and_each_comes_back_as_it_was_left(self):
        search = self.tool("search")
        search.query.insert(0, "plexi")
        search.find()
        levels = self.tool("levels")                    # opening another replaces it
        self.assertIs(self.desk.hosted, levels)
        self.assertEqual(search.frame.winfo_manager(), "")
        self.assertEqual(levels.frame.winfo_manager(), "pack")
        again = self.tool("search")
        self.assertIs(again, search)                    # kept, not made again
        self.assertEqual((search.query.get(), search.box.size()), ("plexi", 1))
        self.desk.toggle("Shape")                       # a panel of the desk takes the place back
        self.assertIsNone(self.desk.hosted)
        self.assertEqual((self.desk.panel, self.desk.tray.winfo_manager()), ("Shape", "grid"))

    def test_back_is_a_thing_to_click_in_the_window(self):
        self.tool("usage")
        back = [child for child in self.desk.side.winfo_children()[0].winfo_children() if child.cget("text") == "‹ Back"]
        self.assertEqual(len(back), 1)
        back[0].event_generate("<ButtonRelease-1>")
        self.root.update()
        self.assertIsNone(self.desk.hosted)

    def test_what_a_tool_asked_of_the_unit_goes_on_when_it_is_left_and_what_it_plays_stops(self):
        from lab.tools import common

        bench = self.tool("workbench")
        bench.kind.set("Strengths")
        bench.kind_changed()
        bench.argument.insert(0, "darker")
        bench.render()
        silenced = []
        with unittest.mock.patch.object(common, "silence", lambda: silenced.append(True)):
            self.desk.back()
        self.assertEqual(silenced, [True])              # a preview playing on the computer is stopped
        self.assertTrue(bench.busy)                     # the render is not: it is with the unit
        self.pump()
        self.assertEqual(len(bench.auditions), 3)       # and its result is there when the tool is opened again

    def test_every_part_shown_on_request_still_fits_across(self):
        for title, name in TOOLS:
            with self.subTest(tool=title):
                tool = self.tool(name)
                open_every_part(tool.frame)
                self.assert_fits(tool, title)

    def test_the_way_the_app_was_left_comes_back_as_the_panel_a_tool_was_opened_from(self):
        self.desk.toggle("More")
        self.tool("usage")
        self.assertEqual(self.studio.desk_session.get("panel"), "More")


class LearningScreenTest(DeskCase):
    """Learning the guitar is a screen of its own: the Guitar Learner and Teach a control, in the place of
    everything below the top row."""

    def setUp(self):
        super().setUp()
        self.put("a", Sound("Clean", chain(gain_db=0.), {"kind": "designed"}))
        self.put("b", Sound("Plexi", chain(PLEXI, V30, DELAY), {"kind": "designed"}))

    def test_it_takes_the_place_of_everything_below_the_top_row_and_the_panel_names_go(self):
        desk = self.desk
        desk.toggle("Shape")
        screen = self.learn()
        for size in (DEFAULT_SIZE, SMALLEST_SIZE):
            self.resize(size)
            self.assertTrue(desk.learning_on)
            for gone in (desk.body, desk.names, desk.tiles["a"][0], desk.tray, desk.request, desk.line):
                self.assertFalse(gone.winfo_ismapped(), (size, gone))
            for stays in (desk.learn_symbol, desk.chooser):         # the top row: the symbol and the instrument
                self.assertTrue(stays.winfo_ismapped(), size)
            top = desk.chooser.master
            self.assertEqual(screen.frame.winfo_toplevel(), self.root)
            self.assertEqual(screen.frame.winfo_width(), top.winfo_width())             # the window's whole width
            self.assertGreaterEqual(screen.frame.winfo_rooty(), top.winfo_rooty() + top.winfo_height())
            self.assertTrue(inside(screen.frame, self.root), size)
            self.assertGreater(screen.frame.winfo_height(), size[1] - 110, size)        # and down to its bottom
        self.resize(DEFAULT_SIZE)
        self.assertEqual([child.winfo_class() for child in self.root.winfo_children()], ["Frame"])   # still one page
        # What the desk's status line says is said on the screen's own line.
        self.studio.say("Something the desk says.")
        self.assertEqual(screen.line.cget("text"), "Something the desk says.")
        self.assertEqual(desk.working.cget("text"), "")

    def test_the_desk_comes_back_exactly_as_it_was(self):
        desk = self.desk
        desk.toggle("More")
        tool = self.tool("usage")
        self.studio.play("phrase", "b")
        self.pump()
        screen = self.learn()
        self.assertEqual(self.studio.state.mode, "phrase")          # what plays goes on behind the screen
        desk.learn_symbol.event_generate("<ButtonRelease-1>")       # pressed again
        self.root.update()
        self.assertFalse(desk.learning_on)
        self.assertFalse(screen.frame.winfo_ismapped())
        self.assertIs(desk.hosted, tool)
        self.assertEqual((desk.panel, self.studio.state.mode, self.studio.state.side), ("More", "phrase", "b"))
        for back in (desk.body, desk.names, desk.side, tool.frame, desk.tiles["a"][0], desk.tiles["b"][0], desk.request):
            self.assertTrue(back.winfo_ismapped(), back)
        desk.back()
        self.assertEqual(desk.tray.winfo_manager(), "grid")
        desk.toggle_learning()
        self.assertIs(desk.learning, screen)                        # the same screen, as it was left
        self.assertEqual(screen.view, "Guitar Learner")

    def test_the_symbol_is_lit_while_learning_and_says_what_a_press_does(self):
        from lab.desk.theme import ACCENT, RULE

        desk, symbol = self.desk, self.desk.learn_symbol

        def said():
            symbol.event_generate("<Enter>")
            self.root.update()
            words = [child.cget("text") for child in self.root.winfo_children() if child.winfo_class() == "Label"]
            symbol.event_generate("<Leave>")
            self.root.update()
            return words

        self.assertEqual((symbol.cget("highlightbackground"), said()), (RULE, ["Learn the guitar"]))
        self.learn()
        self.assertEqual((symbol.cget("highlightbackground"), said()), (ACCENT, ["Back to the desk"]))
        self.assertEqual([child.winfo_class() for child in self.root.winfo_children()], ["Frame"])   # the name is gone
        desk.toggle_learning()
        self.assertEqual(symbol.cget("highlightbackground"), RULE)

    def test_it_follows_the_instrument_and_offers_teach_a_control_alone_for_one_with_no_layout(self):
        desk = self.desk
        screen = self.learn()
        self.assertEqual((screen.view, screen.learner.guitar), ("Guitar Learner", "Charvel"))
        self.assertEqual([tab.winfo_ismapped() for tab in screen.tabs.values()], [True, True, True])
        desk.choose_instrument("Acoustic")                          # changed while the screen is on
        self.settle_learning()
        self.assertEqual(screen.view, "Teach a control")
        self.assertEqual([tab.winfo_ismapped() for tab in screen.tabs.values()], [False, True, False])
        self.assertFalse(screen.learner.frame.winfo_ismapped())
        self.assertEqual(screen.why.cget("text"), "The Guitar Learner has no picture of the Acoustic's controls.")
        self.assertTrue(screen.why.winfo_ismapped())
        self.assertEqual(screen.teaching.panel.instrument.get(), "Acoustic")
        screen.choose("Guitar Learner")                             # not offered: nothing happens
        self.assertEqual(screen.view, "Teach a control")
        desk.choose_instrument("Charvel")
        self.settle_learning()
        self.assertEqual(screen.view, "Guitar Learner")             # the view chosen before is back with the guitar
        self.assertFalse(screen.why.winfo_ismapped())
        self.assertLess(screen.tabs["Guitar Learner"].winfo_rootx(), screen.tabs["Teach a control"].winfo_rootx())
        screen.tabs["Teach a control"].event_generate("<ButtonRelease-1>")
        self.root.update()
        self.assertEqual((screen.view, screen.teaching.panel.instrument.get()), ("Teach a control", "Charvel"))
        self.assertFalse(screen.learner.frame.winfo_ismapped())

    def test_opened_on_an_instrument_with_no_layout_it_shows_teach_a_control_and_says_why(self):
        self.desk.choose_instrument("Bass")
        screen = self.learn()
        self.assertEqual((screen.view, screen.learner), ("Teach a control", None))
        self.assertIn("no picture of the Bass's controls", screen.why.cget("text"))
        self.assertEqual(screen.teaching.panel.instrument.get(), "Bass")
        self.assertEqual(self.desk.chooser.name.cget("text"), "Bass")

    def test_nothing_is_wider_than_the_screen_or_pushed_out_of_the_window_at_either_size(self):
        screen = self.learn()
        learner = screen.learner

        def fits(what, scrolls=False):
            for size in (DEFAULT_SIZE, SMALLEST_SIZE):
                self.resize(size)
                across = screen.frame.winfo_width()
                self.assertEqual(across, size[0] - 40, (what, size))
                self.assertEqual(wide_children(screen.frame, across), [], (what, size))
                self.assertTrue(inside(screen.frame, self.root), (what, size))
                if not scrolls:
                    self.assertEqual(pushed_out(screen.frame, self.root), [], (what, size))
            self.resize(DEFAULT_SIZE)

        fits("the Guitar Learner")
        # Every control of the guitar is on the photograph as it is shown, also in the smallest window.
        self.resize(SMALLEST_SIZE)
        art = learner.board.art
        self.assertEqual(set(learner.board.hotspots), {"volume", "tone", "pickup", "wiring"})
        for key, (x, y) in learner.board.hotspots.items():
            self.assertTrue(15 < x < art.winfo_width() - 15 and 15 < y < art.winfo_height() - 15, key)
        self.resize(DEFAULT_SIZE)
        learner.toggle_takes()
        self.root.update()
        self.assertTrue(learner.chooser.winfo_ismapped() and not learner.board.winfo_ismapped())
        fits("its takes and WAVs")
        learner.toggle_takes()
        learner.toggle_dataset()
        fits("its dataset")
        learner.toggle_dataset()
        self.root.update()
        self.assertTrue(learner.board.winfo_ismapped())
        screen.choose("Teach a control")
        self.root.update()
        fits("Teach a control")
        open_every_part(screen.teaching.frame)          # with every part shown on request open, it scrolls
        fits("Teach a control with every part open", scrolls=True)
        self.assertTrue(inside(screen.teaching.scroller, self.root))

    def test_leaving_the_screen_or_the_view_ends_a_recording_and_a_live_listening(self):
        desk = self.desk
        screen = self.learn()
        learner = screen.learner
        learner.stop, learner.live_stop = Event(), Event()      # as while a take is recorded, or it listens live
        stop, live = learner.stop, learner.live_stop
        desk.toggle_learning()
        self.assertTrue(stop.is_set() and live.is_set())
        learner.stop = learner.live_stop = None
        self.learn("Teach a control")
        panel = screen.teaching.panel
        learner.stop = Event()
        panel.recording = {"kind": "example", "setting": "Neck", "guitar": "Charvel", "stop": Event(),
                           "ready": Event(), "started": True, "said": None}
        panel.live_running = True
        stop = panel.recording["stop"]
        desk.toggle_learning()
        self.assertTrue(stop.is_set() and learner.stop.is_set())
        self.assertTrue(panel.live_running)             # gesture listening goes on: the guitar is played on the desk
        panel.recording, panel.live_running, learner.stop = None, False, None
        self.learn()
        learner.stop = Event()
        stop = learner.stop
        screen.choose("Guitar Learner")
        screen.choose("Teach a control")                # the other view: the one that is left ends its recording
        self.assertTrue(stop.is_set())
        learner.stop = None


class DeskPanelsTest(DeskCase):
    """The desk's own panels at the size the desk opens with and at its smallest."""

    def test_nothing_a_panel_shows_is_pushed_out_of_it(self):
        from lab.desk.window import PANELS
        from support import DEFAULT_SIZE, SMALLEST_SIZE, inside

        (self.home / "directory.json").write_text(json.dumps(
            {"read": "2026-11-21T10:00:00+00:00", "firmware": "4.1.1",
             "presets": [FoldedIntoTheDeskTest.PRESET]}), encoding="utf-8")
        self.put("a", Sound("Clean", chain(gain_db=0.), {"kind": "designed"}))
        self.put("b", Sound("Plexi", chain(PLEXI, V30, DELAY), {"kind": "designed"}))

        def shown(widget, scrolls=False):
            """The buttons, lists, lines and words of a panel, apart from what is inside a part that scrolls."""
            found = []
            for child in widget.winfo_children():
                moves = scrolls or (child.winfo_class() == "Canvas" and hasattr(child, "inner"))
                if not moves and child.winfo_class() in ("Button", "Label", "Listbox", "Entry", "Scale", "Canvas"):
                    found.append(child)
                found += shown(child, moves)
            return found

        for panel in PANELS:
            if self.desk.panel != panel:
                self.desk.toggle(panel)
            for size in (DEFAULT_SIZE, SMALLEST_SIZE):
                self.resize(size)
                out = [(widget.winfo_class(), str(widget.cget("text"))[:30] if widget.winfo_class() in ("Button", "Label")
                        else "") for widget in shown(self.desk.tray)
                       if not (widget.winfo_ismapped() and inside(widget, self.desk.tray))]
                self.assertEqual(out, [], (panel, size))
            for side in ("a", "b"):
                self.assertTrue(inside(self.desk.tiles[side][0], self.root), panel)


class FoldedIntoTheDeskTest(DeskCase):
    """What the Library tool and the field's measuring did, now in the Sounds and Difference panels."""

    PRESET = {"slot": {"folder_key": FACTORY, "position": 0, "is_factory": True}, "name": "Brit 2203",
              "setlist": "Factory"}

    def presets(self, *rows):
        (self.home / "directory.json").write_text(json.dumps({"read": "2026-11-21T10:00:00+00:00", "firmware": "4.1.1",
                                                              "presets": list(rows)}), encoding="utf-8")

    def widgets(self, kind):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == kind:
                    found.append(child)
                walk(child)

        walk(self.desk.tray)
        return found

    def assert_all_on_show(self):
        """Nothing in the open panel is pushed out of it, at the desk's smallest size either."""
        from support import DEFAULT_SIZE, SMALLEST_SIZE, inside

        for size in (DEFAULT_SIZE, SMALLEST_SIZE):
            self.resize(size)
            for kind in ("Button", "Label", "Listbox", "Entry"):
                for widget in self.widgets(kind):
                    self.assertTrue(widget.winfo_ismapped() and inside(widget, self.desk.tray),
                                    (size, kind, widget.cget("text") if kind in ("Button", "Label") else ""))
        self.resize(DEFAULT_SIZE)

    def clickable(self, text):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() in ("Label", "Button") and str(child.cget("text")).startswith(text):
                    found.append(child)
                walk(child)

        walk(self.desk.tray)
        found = [widget for widget in found if str(widget.cget("text")) == text] or found    # the thing itself first
        self.assertEqual(len(found), 1, text)
        return found[0]

    def press(self, text):
        widget = self.clickable(text)
        if widget.winfo_class() == "Button":
            widget.invoke()
        else:
            widget.event_generate("<ButtonRelease-1>")
        self.root.update()

    def test_the_sounds_panel_filters_by_every_word_and_by_the_slot_as_the_unit_shows_it(self):
        other = {"slot": {"folder_key": "/media/p4/Presets/Gigs", "position": 254, "is_factory": False},
                 "name": "Clean Strat", "setlist": "Gigs"}
        self.presets(self.PRESET, other)
        self.desk.toggle("Sounds")
        box, = self.widgets("Listbox")
        self.assertEqual(box.size(), 2)
        self.desk.sounds_query = "32g"                  # as typed in the filter
        self.desk.fill_panel()
        box, = self.widgets("Listbox")
        self.assertEqual(box.get(0, "end"), ("Clean Strat  ·  Gigs 32G",))
        self.desk.sounds_query = "brit factory"
        self.desk.fill_panel()
        box, = self.widgets("Listbox")
        self.assertEqual(box.size(), 1)

    def test_a_preset_is_used_as_a_reference_from_the_sounds_panel(self):
        from lab.tools import search

        self.presets(self.PRESET)
        self.desk.toggle("Sounds")
        self.root.update()
        self.assert_all_on_show()
        self.press("More with this preset")
        self.assert_all_on_show()                       # with the rest shown too, down to the smallest desk
        self.press("Use as reference")
        self.assertEqual(self.studio.state.status, "Choose a preset in the list first.")
        self.press("B")                                 # scene B, among the chips
        self.assertEqual(self.desk.reference_scene, "B")
        box, = self.widgets("Listbox")
        box.selection_set(0)
        self.press("Use as reference")
        self.pump()
        self.assertIn("Reference ready · Brit 2203 · scene B", self.studio.state.status)
        found = search.references(self.home)
        self.assertEqual((found[0]["name"], found[0]["scene"], found[0]["phrase"]), ("Brit 2203", 1, "phrase-1"))

    def test_a_preset_is_left_on_the_unit_from_the_sounds_panel(self):
        self.presets(self.PRESET)
        self.desk.toggle("Sounds")
        self.root.update()
        self.press("More with this preset")
        box, = self.widgets("Listbox")
        box.selection_set(0)
        self.press("Leave it on the unit")
        self.pump()
        self.assertEqual(self.studio.state.status, "Brit 2203 is selected and stays selected when the app closes.")
        self.assertEqual((self.studio.state.mode, self.studio.state.shown), (None, None))

    def test_a_and_b_are_measured_against_each_other_from_the_difference_panel(self):
        self.put("a", Sound("Clean", chain(gain_db=0.), {"kind": "designed"}))
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        self.desk.toggle("Difference")
        self.assertEqual(self.widgets("Canvas"), [])
        self.press("Measure A and B now")
        self.pump()
        self.root.update()
        texts = self.texts(self.desk.tray)
        self.assertTrue(any(text.startswith("B against A, level aside") for text in texts), texts)
        self.assertTrue(any("Brightness" in text and "Tail continuity" in text for text in texts))
        chart, = self.widgets("Canvas")
        self.assertGreater(len(chart.find_all()), 8)        # a bar for each octave band
        self.assertEqual(len(self.desk.contrast["spectrum_db"]), 8)
        self.studio.turn({"brightness": .5}, "b")           # B is not the sound that was measured any more
        self.pump()
        self.assertEqual(self.widgets("Canvas"), [])

    def test_the_field_no_longer_measures_and_says_where_that_is(self):
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        tool = self.tool("field")
        self.assertFalse(hasattr(tool, "measure"))
        self.assertTrue(any("Difference, Measure A and B now" in text for text in self.texts(tool.frame)))


if __name__ == "__main__":
    unittest.main()
