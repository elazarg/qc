"""What a side tool shows in the open, and what it keeps behind a "?".

In the open: the names of controls, what was measured or found, the status line, one short prompt where there
is nothing yet. What is worth knowing once (how something works, what a control is for) is behind a hint
(`lab.desk.hints`) beside the heading or the control: `section`'s and `disclosure`'s note, `caption`.

The rule measured here for "no paragraphs": in a tool as it opens, with a sound on A and on B, no line of any
label on show is longer, set on one line, than one and a half times the width of the tool's place.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import tkinter as tk
from tkinter import font as tkfont
import unittest

from lab.desk.hints import Hint
from lab.sound.sounds import Sound
from lab.tools import common
from lab.tools.menu import TOOLS

from support import DELAY, PLEXI, V30, DeskCase, chain


def inside(widget, kind):
    """Every widget of a kind inside another."""
    found = []
    for child in widget.winfo_children():
        if isinstance(child, kind):
            found.append(child)
        found += inside(child, kind)
    return found


def on_show(widget):
    """The labels and buttons a tool shows now, the "?" of a hint aside."""
    return [item for item in inside(widget, (tk.Label, tk.Button))
            if item.winfo_viewable() and not isinstance(item, Hint)]


class WidgetsTest(DeskCase):
    def part(self):
        frame = tk.Frame(self.root)
        frame.pack()
        return frame

    def test_a_section_s_note_is_behind_a_hint_beside_its_title(self):
        part = common.section(self.part(), "Listening level", "Both sides together, up to the app's ceiling.")
        self.root.update()
        self.assertEqual([item.cget("text") for item in inside(part, tk.Label)], ["Listening level", "?"])
        self.assertIsInstance(part.hint, Hint)
        self.assertEqual(part.hint.words, "Both sides together, up to the app's ceiling.")
        head = part.winfo_children()[0]
        self.assertEqual(part.hint.master, head)                    # beside the title, on its line
        title = inside(head, tk.Label)[0]
        self.assertGreater(part.hint.winfo_x(), title.winfo_x())
        self.assertLess(part.hint.winfo_y(), title.winfo_y() + title.winfo_height())
        part.hint.show()                                            # as when the pointer rests on it
        self.assertEqual(part.hint.shown.cget("text"), part.hint.words)
        self.assertEqual(part.hint.shown.winfo_toplevel(), self.root)   # over the window, not a window of its own
        part.hint.hide()
        plain = common.section(self.part(), "Results")
        self.assertIsNone(plain.hint)
        self.assertEqual([item.cget("text") for item in inside(plain, tk.Label)], ["Results"])

    def test_a_disclosure_and_a_caption_keep_their_notes_behind_hints_too(self):
        frame = self.part()
        content = common.disclosure(frame, "Stage EQ", note="Two small EQ moves, in a blind order.")
        named = common.caption(frame, "Weighing", "What counts most in how close a sound is.")
        named.pack()
        self.root.update()
        self.assertEqual(sorted(item.cget("text") for item in inside(frame, tk.Label)),
                         ["?", "?", "Stage EQ ▾", "Weighing"])
        self.assertEqual((content.hint.words, named.hint.words),
                         ("Two small EQ moves, in a blind order.", "What counts most in how close a sound is."))
        self.assertIsNone(common.disclosure(frame, "Earlier comparisons").hint)
        self.assertIsNone(common.caption(frame, "Among").hint)

    def test_a_line_with_nothing_to_say_and_a_part_with_nothing_in_it_take_no_room(self):
        frame = self.part()
        tk.Label(frame, text="above").pack()
        line = common.said(frame)
        part = common.room(frame)
        common.listbox(part, height=6).pack()
        below = tk.Label(frame, text="below")
        below.pack()
        self.root.update()
        empty = below.winfo_y()
        self.assertEqual((line.winfo_manager(), part.winfo_manager()), ("", ""))
        line.configure(text="Dry peak -14 dBFS")
        part.show(True)
        self.root.update()
        self.assertGreater(below.winfo_y(), empty + 60)             # both between what was packed around them
        self.assertLess(line.winfo_rooty(), part.winfo_rooty())
        line.configure(text="")
        part.show(False)
        self.root.update()
        self.assertLessEqual(below.winfo_y(), empty + 1)            # and gone again, with their room

    def test_a_list_is_as_tall_as_what_it_holds_up_to_its_most(self):
        box = common.listbox(self.part(), height=9)
        for count, tall in ((0, 1), (3, 3), (9, 9), (30, 9)):
            box.delete(0, "end")
            box.insert("end", *[f"row {index}" for index in range(count)])
            common.sized(box, 9)
            self.assertEqual(int(box.cget("height")), tall, count)

    def test_the_scrollbar_is_there_only_while_there_is_more_and_moves_the_page(self):
        holder = tk.Frame(self.root, width=200, height=120)
        holder.pack_propagate(False)
        holder.pack()
        canvas, page = common.scrolled(holder)
        canvas.pack(side="left", fill="both", expand=True)
        tk.Label(page, text="one line").pack()
        self.root.update()
        bar = next(child for child in holder.winfo_children() if child is not canvas)
        self.assertEqual(bar.winfo_manager(), "")
        for index in range(30):
            tk.Label(page, text=f"line {index}").pack()
        self.root.update()
        self.assertEqual(bar.winfo_manager(), "pack")
        self.assertLessEqual(bar.winfo_width(), 8)                  # thin, in the desk's colours: a frame, no ttk
        self.assertEqual(bar.winfo_class(), "Frame")
        bar.event_generate("<Button-1>", x=2, y=bar.winfo_height() - 2)
        self.root.update()
        self.assertGreater(canvas.yview()[0], .5)

    def test_a_file_choice_with_nothing_found_shows_no_empty_list_and_keeps_where_it_looked_behind_a_hint(self):
        files = self.home / "files"
        choice = common.FileChoice(self.part(), folders=lambda: [files / "Backups"], patterns=("*.pgb",),
                                   choose=lambda path: None, action="Read this backup")
        choice.pack()
        self.root.update()
        self.assertEqual(choice.said.cget("text"), "None found. Paste the path of one.")
        self.assertIn("Backups", choice.where.words)                # where it looked, on request
        self.assertFalse(choice.list.winfo_viewable())
        self.assertFalse(choice.action.winfo_viewable())            # nothing to use yet
        (files / "Backups").mkdir(parents=True)
        (files / "Backups" / "mine.pgb").write_bytes(b"x")
        choice.look()
        self.root.update()
        self.assertTrue(choice.list.winfo_viewable() and choice.action.winfo_viewable())
        self.assertEqual((choice.list.size(), int(choice.list.cget("height")), choice.said.cget("text")), (1, 1, ""))


class ToolsWordsTest(DeskCase):
    def setUp(self):
        super().setUp()
        first = Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}, explanation="A plain Plexi.")
        self.put("a", Sound("Echo Lead", chain(PLEXI, V30, DELAY), {"kind": "designed"}))
        self.put("b", first)
        self.put("b", Sound("Plexi darker", chain(PLEXI, V30, DELAY), {"kind": "revised", "request": "darker"},
                            parent=first.id))

    def test_a_tool_as_it_opens_shows_no_paragraph_and_every_hint_has_something_to_say(self):
        hinted = {}
        for title, name in TOOLS:
            with self.subTest(tool=title):
                tool = self.tool(name)
                self.root.update()
                room = 1.5 * tool.scroller.winfo_width()
                for item in on_show(tool.frame):
                    measure = tkfont.Font(root=self.root, font=item.cget("font")).measure
                    for line in str(item.cget("text")).split("\n"):
                        self.assertLessEqual(measure(line), room, (title, line))
                hints = inside(tool.frame, Hint)
                self.assertTrue(all(hint.words.strip() for hint in hints), title)
                hinted[name] = len(hints)
        # Every tool keeps something behind a "?", apart from none: each has a heading or a part to explain.
        self.assertTrue(all(hinted.values()), hinted)

    def test_what_the_tools_used_to_say_in_the_open_is_what_their_hints_say(self):
        levels = self.tool("levels")
        told = " ".join(hint.words for hint in inside(levels.frame, Hint))
        for words in ("the app's ceiling", "A trim is applied after the blocks that make the tone",
                      "A and B are matched by the desk itself", "returned over USB"):
            self.assertIn(words, told)
        shown = " ".join(str(item.cget("text")) for item in on_show(levels.frame))
        self.assertNotIn("ceiling", shown)
        self.assertNotIn("returned over USB", shown)

        field = self.tool("field")
        self.assertIn("Find the edge of breakup", field.description.words)          # the plane's own words
        self.assertIn("Distortion", field.controls.cget("text"))                    # the knobs it moves: in the open
        field.plane.set("Depth & width")
        field.recenter()
        self.assertIn("Move the sound back into a room", field.description.words)

        bench = self.tool("workbench")
        self.assertIn("Each scene the selected sound names", bench.help.words)
        self.assertIn("level-matched and shuffled", bench.help.words)
        bench.kind.set("Strengths")
        bench.kind_changed()
        self.assertIn("40 % and 85 %", bench.help.words)
        self.assertEqual(bench.asked.cget("text"), "A request, such as darker")     # the entry's name stays
        bench.kind.set("WAV files")
        bench.kind_changed()
        self.assertIn("No unit needed", bench.help.words)
        self.assertNotIn("level-matched", bench.help.words)                         # nothing is rendered for those

        check = self.tool("soundcheck")
        told = " ".join(hint.words for hint in inside(check.frame, Hint))
        self.assertIn("play your hardest", told)
        self.assertIn("rehearsal level", told)

        search = self.tool("search")
        told = " ".join(hint.words for hint in inside(search.frame, Hint))
        self.assertIn("punchy lead no delay", told)
        self.assertIn("played silently through the unit", told)

    def test_lists_and_results_are_there_once_there_is_something_in_them(self):
        search = self.tool("search")
        self.assertFalse(search.box.winfo_viewable())               # no results yet: no empty list, no Put on
        search.query.insert(0, "plexi")
        search.find()
        self.root.update()
        self.assertTrue(search.box.winfo_viewable())
        self.assertEqual(int(search.box.cget("height")), search.box.size())
        search.query.delete(0, "end")
        search.query.insert(0, "qqqq")
        search.find()
        self.root.update()
        self.assertFalse(search.box.winfo_viewable())
        self.assertEqual(search.status.get(), "No sound answers those words.")

        levels = self.tool("levels")
        self.assertFalse(levels.rows.winfo_viewable())
        used = self.tool("usage")
        self.assertFalse(used.days.winfo_viewable() or used.notes.winfo_viewable())
        used.note.insert("1.0", "B sounds boxy next to A")
        used.keep()
        self.root.update()
        self.assertTrue(used.notes.winfo_viewable())
        used.notes.selection_set(0)
        used.describe_note()
        self.assertEqual(used.said_of.cget("text"), "B sounds boxy next to A  ·  Echo Lead / Plexi darker")

        bench = self.tool("workbench")
        self.assertEqual(bench.none_earlier.cget("text"), "None yet.")
        pod = self.tool("pod")
        self.assertFalse(pod.list.winfo_viewable())                 # no backup read: its choice, and nothing else
        self.assertEqual(pod.status.get(), "")                      # the open choice says what it wants
        songs = self.tool("listening")
        self.assertFalse(songs.list.winfo_viewable())
        songs.show({"summary": {"songs": [{"artist": "Rush", "title": "Limelight", "chosen": 4, "days": 3,
                                           "hours": 1.5}], "hours": 2, "years": {2026: 2}}, "guitar_context": None})
        self.root.update()
        self.assertTrue(songs.list.winfo_viewable())
        self.assertEqual(songs.where.cget("text"), "2 hours over 1 year")


if __name__ == "__main__":
    unittest.main()
