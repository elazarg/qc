"""One window: no dialogs, no pop-up windows or menus, no keyboard shortcuts, nothing opened outside it.

Two kinds of check. The code of the app is read: nothing in `lab/` may name a second window, a dialog, a menu,
a drop-down or the browser, and a key may be bound only where this file says so, with the reason. And the
things that used to have windows of their own (every side tool, the Guitar Learner, the teaching of a control)
are opened on the real desk, kept off the screen: each is shown in the desk's own window in the place of the
side panel, fits it across at the size the desk opens with and at its smallest, leaves both tiles on the
screen, and goes back to the panel it was opened from.

What the reading cannot see is a caption that advertises a key, and what the measuring cannot see is how any
of it looks.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import ast
import json
from pathlib import Path
import re
from threading import Event
import unittest

from lab.qc.sim import FACTORY
from lab.sound.sounds import Sound
from lab.tools.menu import TOOLS

from test_lab_tools_support import DELAY, PLEXI, V30, DeskCase, chain

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
    "learner/teach.py": [("<Return>", 1, "Return in the line a new guitar's name is typed into takes that name")],
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


def read(path):
    """What a file of the app names and binds: (forbidden names with their lines, Tk roots made, events bound)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    named, roots, bound = [], 0, []
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
                elif isinstance(event, ast.JoinedStr):
                    bound.append(("an event put together when it runs", node.lineno))
                elif not isinstance(event, ast.Tuple):          # a tuple is a socket's address, not an event
                    bound.append(("an event that cannot be read here", node.lineno))
    return named, roots, bound


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
            named, roots, bound = read(sample)
        self.assertEqual({word for word, _ in named}, {"filedialog", "webbrowser", "Toplevel", "Menu"})
        self.assertEqual(roots, 1)
        self.assertEqual([event for event, _ in bound if not NOT_A_KEY.match(event)],
                         ["<Escape>", "an event put together when it runs"])


class HostedTest(DeskCase):
    """Each thing that had a window of its own, in the desk's."""

    def setUp(self):
        super().setUp()
        self.put("a", Sound("Clean", chain(gain_db=0.), {"kind": "designed"}))
        self.put("b", Sound("Plexi", chain(PLEXI, V30, DELAY), {"kind": "designed"}))

    def learner(self):
        from lab.learner import folders

        self.addCleanup(folders.log_to, None)       # the seat points the learner's folders and log at this desk
        self.addCleanup(folders.use, None)
        self.desk.toggle("Guitar")
        self.desk.open_learner()
        seat = self.desk.seat
        self.addCleanup(seat.worker.shutdown, wait=True)
        self.addCleanup(self.desk.learner.worker.shutdown, wait=True)
        for _ in range(200):
            self.studio.poll()
            seat.tick()
            self.root.update()
            if self.desk.learner.job is None and not self.desk.learner.queued:
                break
        return self.desk.learner

    def teaching(self):
        from lab.learner import folders

        self.addCleanup(folders.log_to, None)
        self.addCleanup(folders.use, None)
        self.desk.toggle("Guitar")
        self.desk.open_teach()
        self.addCleanup(self.desk.seat.worker.shutdown, wait=True)
        self.addCleanup(self.desk.teaching.worker.shutdown, wait=True)
        self.root.update()
        return self.desk.teaching

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

    def test_the_guitar_learner_is_shown_in_the_desks_window_and_going_back_ends_a_recording(self):
        learner = self.learner()
        self.assert_fits(learner, "Guitar Learner")
        self.assertEqual(self.desk.tiles["a"][0].winfo_toplevel(), learner.frame.winfo_toplevel())
        learner.toggle_dataset()
        learner.toggle_guide()
        self.assert_fits(learner, "Guitar Learner")     # with the dataset and the guide on show too
        learner.toggle_dataset()
        learner.toggle_takes()
        self.assert_fits(learner, "Guitar Learner")     # and with the takes and WAVs to choose from
        learner.stop, learner.live_stop = Event(), Event()      # as while a take is recorded, or it listens live
        stop, live = learner.stop, learner.live_stop
        self.desk.back()
        self.assertTrue(stop.is_set() and live.is_set())
        self.assertIsNone(self.desk.hosted)
        self.assertEqual((self.desk.panel, self.desk.tray.winfo_manager()), ("Guitar", "grid"))
        learner.stop = learner.live_stop = None
        self.desk.open_learner()
        self.assertIs(self.desk.hosted, learner)        # the same one, as it was left
        self.assertTrue(learner.guide_open)

    def test_the_teaching_of_a_control_is_shown_in_the_desks_window_and_going_back_ends_a_recording(self):
        teaching = self.teaching()
        self.assert_fits(teaching, "Teach a control")
        panel = teaching.panel
        for content in (child for child in self.all_widgets(teaching.frame) if hasattr(child, "show")):
            content.show(True)                          # every part shown on request, open
        self.assert_fits(teaching, "Teach a control")
        panel.recording = {"kind": "example", "setting": "Neck", "guitar": "Charvel", "stop": Event(),
                           "ready": Event(), "started": True, "said": None}
        stop = panel.recording["stop"]
        self.desk.back()
        self.assertTrue(stop.is_set())
        self.assertEqual((self.desk.panel, self.desk.tray.winfo_manager()), ("Guitar", "grid"))
        panel.recording = None
        self.desk.open_teach()
        self.assertIs(self.desk.hosted, teaching)

    def all_widgets(self, widget):
        found = []
        for child in widget.winfo_children():
            found.append(child)
            found += self.all_widgets(child)
        return found

    def test_every_part_shown_on_request_still_fits_across(self):
        for title, name in TOOLS:
            with self.subTest(tool=title):
                tool = self.tool(name)
                for content in (child for child in self.all_widgets(tool.frame) if hasattr(child, "show")):
                    content.show(True)
                self.assert_fits(tool, title)

    def test_the_way_the_app_was_left_comes_back_as_the_panel_a_tool_was_opened_from(self):
        self.desk.toggle("More")
        self.tool("usage")
        self.assertEqual(self.studio.desk_session.get("panel"), "More")


class DeskPanelsTest(DeskCase):
    """The desk's own panels at the size the desk opens with and at its smallest."""

    def test_nothing_a_panel_shows_is_pushed_out_of_it(self):
        from lab.desk.window import PANELS
        from test_lab_tools_support import DEFAULT_SIZE, SMALLEST_SIZE, inside

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
        from test_lab_tools_support import DEFAULT_SIZE, SMALLEST_SIZE, inside

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
