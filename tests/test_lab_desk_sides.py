"""A and B on the desk (docs/UX_REVIEW.md, section 3): which side a panel acts on, blind comparison putting
things back, what B has that A has not, and what Undo would take back. On the real desk over a simulated unit,
kept off the screen."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import unittest
from unittest import mock

from lab.desk.difference import against
from lab.desk.theme import SIDE
from lab.sound.sounds import Sound

from support import DELAY, GREENBACK, HIWATT, PLEXI, V30, DeskCase, chain, settle_until


class PanelSideTest(DeskCase):
    def test_a_panel_that_acts_on_one_sound_names_its_side_in_its_colour(self):
        self.put("a", Sound("Lead", chain(PLEXI, V30), {"kind": "designed"}))
        self.put("b", Sound("Rhythm", chain(PLEXI, V30), {"kind": "designed"}))
        for panel in ("Shape", "Chain", "Sounds"):
            for side in ("a", "b"):
                self.studio.select(side)
                if self.desk.panel != panel:
                    self.desk.toggle(panel)
                self.desk.fill_panel()
                head = self.desk.tray.winfo_children()[0]
                letters = [label for label in head.winfo_children() if label.cget("text").strip() == side.upper()]
                self.assertEqual(len(letters), 1, (panel, side))
                self.assertEqual(str(letters[0].cget("fg")), SIDE[side])
        self.desk.toggle("History")
        head = self.desk.tray.winfo_children()[0]
        self.assertEqual([label.cget("text") for label in head.winfo_children()], ["History"])

    def test_shape_has_no_undo_of_its_own_and_the_desks_says_what_it_would_take_back(self):
        self.put("b", Sound("Rhythm", chain(PLEXI, V30), {"kind": "designed"}))
        self.assertEqual(self.studio.undo_words(), "Undo Rhythm on B")
        self.desk.toggle("Shape")
        self.assertNotIn("Undo", self.texts(self.desk.tray))
        self.studio.turn({"brightness": .3})
        self.assertEqual(self.studio.undo_words(), "Undo Brightness on B")
        self.studio.undo()
        self.assertEqual(self.studio.undo_words(), "Undo Rhythm on B")
        self.studio.undo()
        self.assertEqual(self.studio.undo_words(), "Nothing to undo")


class PanelNamesTest(DeskCase):
    def test_the_open_panels_name_is_lit_and_goes_out_when_it_is_closed(self):
        from lab.desk.theme import INK, SUBTLE

        self.desk.toggle("Chain")
        lit = {name for name, opener in self.desk.openers.items() if str(opener.cget("fg")) == INK}
        self.assertEqual(lit, {"Chain"})
        self.desk.toggle("Chain")
        self.assertTrue(all(str(opener.cget("fg")) == SUBTLE for opener in self.desk.openers.values()))

    def test_what_was_measured_is_drawn_in_the_chain_panel_and_said_one_click_away(self):
        self.put("b", Sound("Rhythm", chain(PLEXI, V30), {"kind": "designed"}))
        self.studio.idle_seconds = 0.
        settle_until(self.studio, lambda: "probe" in (self.studio.sound("b").heard or {}), 30, "B to be listened to",
                     root=self.root)
        self.studio.idle_seconds = 1e9
        self.desk.toggle("Chain")
        said = self.texts(self.desk.tray)
        self.assertIn("In words ▾", said)
        self.assertFalse(any(text.startswith("Distortion: harmonics") for text in said))
        charts = [widget for widget in self.desk.into.winfo_children() if widget.winfo_class() == "Canvas"]
        harmonics = [chart for chart in charts if chart.find_withtag("harmonic")]
        bands = [chart for chart in charts if chart.find_withtag("band")]
        self.assertEqual((len(harmonics), len(bands)), (1, 1))
        self.assertEqual(harmonics[0].itemcget(harmonics[0].find_withtag("harmonic")[0], "fill"), SIDE["b"])
        self.root.update()                              # drawn inside the part that scrolls before it is clicked
        opener = [widget for widget in self.desk.into.winfo_children()
                  if widget.winfo_class() == "Label" and widget.cget("text") == "In words ▾"][0]
        opener.event_generate("<ButtonRelease-1>")
        self.root.update()
        self.assertTrue(any(text.startswith("Distortion: harmonics") for text in self.texts(self.desk.tray)))


class ToneFieldFromShapeTest(DeskCase):
    def test_the_tone_field_opens_from_beside_shapes_pad(self):
        self.put("b", Sound("Rhythm", chain(PLEXI, V30), {"kind": "designed"}))
        self.desk.toggle("Shape")
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == "Label" and child.cget("text") == "Tone field ›":
                    found.append(child)
                walk(child)

        walk(self.desk.tray)
        self.assertEqual(len(found), 1)
        self.root.update()
        found[0].event_generate("<ButtonRelease-1>")
        self.assertEqual(self.desk.hosted.TITLE, "Tone field")


class BlindPutsBackTest(DeskCase):
    """Comparing blind left A and B swapped half the time, after the names were shown: the sound being worked
    on was then on A, the round's chips lit none of their three, and the next change in words went to the
    reference."""

    def setUp(self):
        super().setUp()
        self.put("a", Sound("Reference", chain(PLEXI, V30), {"kind": "designed"}))
        self.put("b", Sound("Work", chain(HIWATT, GREENBACK), {"kind": "designed"}))
        self.ids = (self.studio.state.a, self.studio.state.b)

    def blind_swapped(self):
        with mock.patch("random.random", return_value=.1):
            self.studio.blind()
        self.pump()
        self.assertEqual((self.studio.state.a, self.studio.state.b), self.ids[::-1])

    def test_showing_which_was_which_puts_them_back(self):
        self.blind_swapped()
        self.studio.blind()
        self.pump()
        self.assertEqual((self.studio.state.a, self.studio.state.b), self.ids)
        self.assertFalse(self.studio.state.blind)
        self.assertEqual(self.studio.state.status, "A was Work, B was Reference; they are back on their own sides.")

    def test_a_choice_made_blind_puts_them_back_and_the_selection_follows_the_sound(self):
        self.blind_swapped()
        self.studio.select("a")                         # Work, while it sits on A
        self.studio.mark("keeper")
        self.pump()
        self.assertEqual((self.studio.state.a, self.studio.state.b), self.ids)
        self.assertEqual(self.studio.state.side, "b")
        self.assertIn("keeper", self.studio.sound("b").marks)
        self.assertTrue(self.studio.state.status.startswith("A: keeper. A was Work, B was Reference"))

    def test_unswapped_it_says_which_was_which_and_moves_nothing(self):
        with mock.patch("random.random", return_value=.9):
            self.studio.blind()
        self.studio.blind()
        self.assertEqual((self.studio.state.a, self.studio.state.b), self.ids)
        self.assertEqual(self.studio.state.status, "A was Reference, B was Work.")


class WhatBHasTest(DeskCase):
    def test_bs_tile_says_what_it_has_that_a_has_not(self):
        base = Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"})
        self.assertEqual(against(base, None, "A"), "")
        self.assertEqual(against(base, base, "A"), "")
        echo = Sound("Plexi", chain(PLEXI, V30, DELAY), {"kind": "designed"})
        self.assertTrue(against(echo, base, "A").startswith("against A: + "))
        hotter = Sound("Plexi", chain(PLEXI, V30, **{str(PLEXI): {"GAIN": 9}}), {"kind": "designed"})
        self.assertEqual(against(hotter, base, "A"), "against A: Brit Plexi 50 Normal set differently")
        shaped = Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}, knobs={"brightness": .15})
        self.assertEqual(against(shaped, base, "A"), "against A: Brightness +0.15")
        self.put("a", base)
        self.studio.copy_to_a()
        self.put("b", shaped)
        caption = self.desk.tiles["b"][2].cget("text")
        self.assertTrue(caption.startswith("against A: "), caption)
        self.assertNotIn("a copy to compare against", self.desk.tiles["a"][2].cget("text"))


if __name__ == "__main__":
    unittest.main()
