"""Faults the review of the desk found in pictures of every screen (docs/UX_REVIEW.md, section 1), each on the
real desk over a simulated unit, kept off the screen. Each test failed on the desk as the review found it."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import json
import threading
import tkinter as tk
import unittest

from lab.desk.theme import FAULT, INK, SUBTLE
from lab.qc.assets import Asset
from lab.sound.sounds import Sound

from support import DEFAULT_SIZE, SMALLEST_SIZE, DELAY, PLEXI, V30, DeskCase, chain, inside, settle_until
from test_lab_desk_top import take
from test_lab_design import GREENBACK, answer, block, given_back, reply


def every(widget):
    found = []
    for child in widget.winfo_children():
        found.append(child)
        found += every(child)
    return found


def texts(widget):
    return [str(child.cget("text")) for child in every(widget) if child.winfo_class() in ("Label", "Button")
            and child.winfo_manager()]


def three():
    return reply(*[answer(name, [block(PLEXI, 0, gain=gain), block(GREENBACK, 1)], differs=f"{name} differs")
                   for name, gain in (("One", 3), ("Two", 6), ("Three", 9))])


class RequestInFlightTest(DeskCase):
    """F1: while a request was with the assistant, Cancel was cut to "Canc" at the size the desk opens with and
    was gone at its smallest; the working line covered the panel names."""

    def test_cancel_and_the_working_line_are_whole_and_above_the_ask_line_at_either_size(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        answered = three()
        self.studio.assistant.run = lambda *args, **options: (gate.wait(20), answered)[1]
        self.desk.toggle("Shape")
        self.studio.ask("Comfortably Numb, the second solo")
        self.root.update()
        for size in (DEFAULT_SIZE, SMALLEST_SIZE):
            self.resize(size)
            cancel = self.desk.cancel_link
            self.assertTrue(cancel.winfo_ismapped(), size)
            self.assertGreaterEqual(cancel.winfo_width(), cancel.winfo_reqwidth(), size)    # not cut
            self.assertTrue(inside(cancel, self.root), size)
            # Under the tiles, just above the line the request was typed in: not in the top row.
            self.assertGreater(cancel.winfo_rooty(), self.desk.tiles["b"][0].winfo_rooty(), size)
            self.assertLess(cancel.winfo_rooty(), self.desk.request.winfo_rooty(), size)
            for name in self.desk.names.winfo_children():
                self.assertGreaterEqual(name.winfo_width(), name.winfo_reqwidth(), (size, name.cget("text")))
        self.assertIn("Comfortably Numb", self.desk.working.cget("text"))
        gate.set()
        self.pump()
        self.assertFalse(self.desk.cancel_link.winfo_ismapped())

    def test_the_working_line_says_how_long_it_has_been_after_a_few_seconds(self):
        self.studio.begin("x", "Designing three ways to get there…")
        self.root.update()
        self.assertEqual(self.desk.working.cget("text"), "Designing three ways to get there…")
        self.desk.working_since -= 12.4
        self.desk.show_working()
        self.assertEqual(self.desk.working.cget("text"), "Designing three ways to get there…  12 s")
        self.studio.end("x")
        self.assertEqual(self.desk.working.cget("text"), "")


class LinesThatWrapTest(DeskCase):
    """F2: with a panel open the status line and the note under the round were cut at the column's edge: they
    wrapped at a width fixed for the widest window."""

    def test_the_status_line_and_the_rounds_note_wrap_to_their_column(self):
        self.script.answers.append(three())
        self.studio.ask("make a classic rock crunch")
        self.pump()
        self.desk.toggle("Shape")
        self.studio.say("Tighter: leaner lows in the drive and amp, then less gain for a firmer attack, and after "
                        "that a narrower band in the cab, which is what players reach for next.")
        for size in (DEFAULT_SIZE, SMALLEST_SIZE):
            self.resize(size)
            column = self.desk.line.master
            for line in (self.desk.line, self.desk.strip_note):
                self.assertLessEqual(line.winfo_reqwidth(), column.winfo_width(), (size, line.cget("text")[:30]))

    def test_the_aim_is_said_once_with_what_was_heard_and_what_misses(self):
        from lab.desk.difference import aim_against

        aim = {"distortion": "crunch", "brightness": "balanced", "tail": "dry"}
        self.assertEqual(aim_against(aim, {"distortion": "saturated", "brightness": "bright", "tail": "dry"}),
                         "aim crunch, balanced, dry · heard as saturated, bright, dry · misses on distortion, brightness")
        self.assertEqual(aim_against(aim, dict(aim)), "aim crunch, balanced, dry · heard as crunch, balanced, dry")
        self.assertEqual(aim_against(aim, None), "aim crunch, balanced, dry · not listened to yet")
        self.assertEqual(aim_against(aim, None, "Nothing came back."),
                         "aim crunch, balanced, dry · could not be listened to")
        self.script.answers.append(three())
        self.studio.ask("make a classic rock crunch")
        self.pump()
        said = " ".join(texts(self.desk.chips) + [self.desk.strip_note.cget("text")])
        self.assertNotIn("aim", said)                   # the aim is drawn on B's tile, and said there on hover
        self.assertEqual(self.desk.scales["b"].drawn[0]["distortion"], "saturated")          # once, in the note, not also at the right


class PanelsThatOverflowTest(DeskCase):
    """F3, F4: the Chain panel of a sound that had been listened to, and the Guitar panel with takes recorded,
    were cut off at the bottom with no way to scroll: the Record button could not be reached at the smallest
    size. (The test of every panel's fit did not see it: its sounds were never listened to and it had no takes.)"""

    def setUp(self):
        super().setUp()
        for name, setting in (("one", "Bridge · Series"), ("two", "Neck · Series")):
            take(self.home, name, "Charvel", setting, "2026-11-20T10:00:00+00:00")
        self.put("a", Sound("Clean", chain(gain_db=0.), {"kind": "designed"}))
        self.put("b", Sound("Plexi", chain(PLEXI, V30, DELAY), {"kind": "designed"}))
        self.studio.idle_seconds = 0.
        settle_until(self.studio, lambda: all("probe" in (self.studio.sound(side).heard or {}) for side in "ab"),
                     30, "A and B to be listened to", root=self.root)
        self.studio.idle_seconds = 1e9
        self.pump()

    def reachable(self, text):
        """Whether a control is in the open panel and can be brought into view: it lies inside the panel, or in
        the part of it that scrolls."""
        found = [widget for widget in every(self.desk.tray) if widget.winfo_class() in ("Label", "Button")
                 and str(widget.cget("text")).startswith(text)]
        self.assertTrue(found, text)
        holder = found[0]
        while holder is not self.desk.tray:
            if hasattr(holder, "inner"):
                return True
            holder = holder.master
        return inside(found[0], self.desk.tray)

    def test_the_chain_panel_scrolls_to_its_last_part(self):
        self.desk.toggle("Chain")
        for size in (DEFAULT_SIZE, SMALLEST_SIZE):
            self.resize(size)
            self.assertTrue(self.reachable("Nano Cortex sheet"), size)

    def test_the_guitar_panel_scrolls_to_its_record_button(self):
        self.desk.toggle("Guitar")
        self.pump()
        self.desk.fill_panel()
        for size in (DEFAULT_SIZE, SMALLEST_SIZE):
            self.resize(size)
            self.assertTrue(self.reachable("Choose the setting first"), size)


class TileOnAirTest(DeskCase):
    """F5: the tile's letter kept a dark box behind it while the tile was on air."""

    def test_everything_on_a_tile_on_air_has_the_tiles_colour(self):
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        self.studio.play("guitar", "b")
        self.pump()
        tile = self.desk.tiles["b"][0]
        self.assertNotEqual(tile.cget("bg"), self.desk.tiles["a"][0].cget("bg"))
        blocks = every(self.desk.blocks["b"])               # the chain's blocks are chips of their own colour
        for widget in every(tile):
            if widget.winfo_class() == "Label" and widget.winfo_manager() and widget not in blocks:
                self.assertEqual(widget.cget("bg"), tile.cget("bg"), widget.cget("text"))


class ListsCutShortTest(DeskCase):
    """F6: the Phrase panel listed the newest eight takes and no more, and the list of captures stopped at 400,
    neither saying so."""

    def test_every_take_can_be_reached_from_the_phrase_panel(self):
        for index in range(11):
            take(self.home, f"take-{index:02}", "Charvel", "Neck", f"2026-11-{index + 10:02}T10:00:00+00:00")
        self.desk.toggle("Phrase")
        rows = [text for text in texts(self.desk.tray) if text.startswith("Charvel · Neck")]
        self.assertEqual(len(rows), 8)
        more = [widget for widget in every(self.desk.tray) if widget.winfo_class() == "Label"
                and widget.cget("text") == "All 11 takes ▾"]
        self.assertEqual(len(more), 1)
        more[0].event_generate("<ButtonRelease-1>")
        self.root.update()
        self.assertEqual(len([text for text in texts(self.desk.tray) if text.startswith("Charvel · Neck")]), 11)

    def test_a_list_of_captures_cut_short_says_so(self):
        records = [Asset("capture", f"{index:064x}", f"Capture {index}").to_record() for index in range(450)]
        (self.home / "assets.json").write_text(json.dumps({"read": "2026-11-21", "assets": records}), encoding="utf-8")
        self.desk.sounds_kind = "Captures"
        self.desk.toggle("Sounds")
        self.assertIn("The first 400 of 450: type to narrow them.", texts(self.desk.tray))


class WhatAChangeActsOnTest(DeskCase):
    """F7: Add parallel space read the selected side and wrote to B; an IR or a capture read B and wrote to B
    whichever side was selected; a change in words always revised B. A change acts on the side selected, and
    its result goes back there."""

    def setUp(self):
        super().setUp()
        self.put("a", Sound("Lead", chain(PLEXI, V30), {"kind": "designed"}))
        self.put("b", Sound("Rhythm", chain(PLEXI, V30), {"kind": "designed"}))
        self.studio.select("a")
        self.before = {side: self.studio.state.a if side == "a" else self.studio.state.b for side in "ab"}

    def assert_only_a_changed(self):
        self.pump()
        self.assertNotEqual(self.studio.state.a, self.before["a"])
        self.assertEqual(self.studio.sound("a").parent, self.before["a"])
        self.assertEqual(self.studio.state.b, self.before["b"])

    def test_parallel_space_goes_on_the_side_it_was_added_to(self):
        self.studio.add_parallel_space()
        self.assert_only_a_changed()

    def test_an_ir_goes_into_the_selected_sound(self):
        self.studio.use_asset(Asset("ir", "CIR_1ab", "Test IR"))
        self.assert_only_a_changed()

    def test_a_change_in_words_revises_the_selected_sound(self):
        self.script.answers.append(given_back(self.studio.sound("a").chain))
        self.studio.ask("more sustain from the amp, without more mud")
        self.assert_only_a_changed()


class TeachAClassicalTest(DeskCase):
    """F8: Teach a control offered a classical guitar a pickup selector, with Neck, Bridge and Middle."""

    def test_an_instrument_without_a_pickup_selector_is_not_offered_one(self):
        from lab.learner import taught

        self.assertEqual(list(taught.controls("Classical")), ["Volume knob", "Tone knob"])
        self.assertEqual(list(taught.controls("Charvel")), ["Pickup selector", "Volume knob", "Tone knob"])
        self.assertEqual(list(taught.controls("A guitar the app does not know")), list(taught.CONTROLS))
        self.desk.choose_instrument("Classical")
        screen = self.learn("Teach a control")
        panel = screen.teaching.panel
        shown = [title for title, widget in panel.control_buttons.items() if widget.winfo_manager()]
        self.assertEqual(shown, ["Volume knob", "Tone knob"])
        self.assertEqual(panel.control.get(), "Volume knob")
        self.desk.choose_instrument("Charvel")
        self.settle_learning()
        panel.set_buttons()
        shown = [title for title, widget in panel.control_buttons.items() if widget.winfo_manager()]
        self.assertEqual(shown, ["Pickup selector", "Volume knob", "Tone knob"])


class StatusLineTest(DeskCase):
    """F9: the status line went stale and nothing marked it ("Ready." through a minute's request, "5 IRs and
    captures on the unit." through nine tools); a fault looked like any other remark."""

    def test_the_line_dims_once_something_happened_since_and_a_fault_has_its_own_colour(self):
        self.put("a", Sound("Lead", chain(PLEXI, V30), {"kind": "designed"}))
        self.put("b", Sound("Rhythm", chain(PLEXI, V30), {"kind": "designed"}))
        self.studio.say("Saved · Tone Lab 3 · Rhythm")
        self.assertEqual(str(self.desk.line.cget("fg")), INK)
        self.studio.select("a")
        self.assertEqual(self.desk.line.cget("text"), "Saved · Tone Lab 3 · Rhythm")
        self.assertEqual(str(self.desk.line.cget("fg")), SUBTLE)
        self.studio.fault("The unit did not answer.")
        self.assertEqual(str(self.desk.line.cget("fg")), FAULT)
        self.studio.say("Undone.")
        self.assertEqual(str(self.desk.line.cget("fg")), INK)


if __name__ == "__main__":
    unittest.main()
