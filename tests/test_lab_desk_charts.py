"""Charts and gestures in place of words (docs/UX_REVIEW.md, the table of marks, G2 and W6): what the instrument
puts out as a bar per pickup setting, the wheel stepping through the round, and the ask line's conventions behind
its "?". On the real desk over a simulated unit, kept off the screen."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
from types import SimpleNamespace
import tkinter as tk
import unittest

from lab.desk import guitar_chart
from lab.desk.hints import Hint
from lab.desk.window import CONVENTIONS
from lab.hearing.profile import TYPICAL_PEAK_DB

from support import DeskCase, settle_until
from test_lab_desk_faults import every, texts
from test_lab_desk_symbols import three
from test_lab_desk_top import take


class GuitarChartTest(unittest.TestCase):
    def test_each_setting_is_a_bar_to_its_held_peak_against_the_line_presets_assume(self):
        root = tk.Tk()
        self.addCleanup(root.destroy)
        canvas = tk.Canvas(root)
        found = {"Bridge · Series": {"held_peak_db": -18., "peak_db": -10., "noise_db": -70.},
                 "Neck · Parallel": {"held_peak_db": -30., "peak_db": -22., "noise_db": -40.}}
        guitar_chart.draw(canvas, found, width=400, chosen="Neck · Parallel")
        typical = canvas.coords(canvas.find_withtag("typical")[0])[0]
        self.assertEqual(typical, guitar_chart.x_of(TYPICAL_PEAK_DB, 400))
        bridge, neck = [canvas.coords(item)[2] for item in canvas.find_withtag("held")]
        self.assertAlmostEqual(bridge, typical)                       # as hot as presets assume: ends on the line
        self.assertLess(neck, typical)                                # weaker: short of it
        self.assertEqual(len(canvas.find_withtag("peak")), 2)
        self.assertEqual(len(canvas.find_withtag("noise")), 1)        # -70 dBFS is off the scale: no mark
        said = [canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text"]
        self.assertIn("-30 dBFS", said)


class GuitarPanelTest(DeskCase):
    def test_once_worked_out_the_guitar_panel_draws_the_settings_and_keeps_the_sentences_a_click_away(self):
        instrument = self.studio.state.instrument
        take(self.home, "one", instrument, "Bridge · Series", "2026-11-20T10:00:00+00:00")
        take(self.home, "two", instrument, "Neck · Parallel", "2026-11-20T10:01:00+00:00")
        self.desk.toggle("Guitar")
        settle_until(self.studio, lambda: self.studio.guitar_summary(), 30, "the takes to be read", root=self.root)
        self.desk.fill_panel()
        charts = [widget for widget in every(self.desk.tray) if widget.winfo_class() == "Canvas"
                  and widget.find_withtag("held")]
        self.assertEqual(len(charts), 1)
        self.assertEqual(len(charts[0].find_withtag("held")), 2)
        self.assertFalse(any(text.startswith("Bridge · Series: in this take") for text in texts(self.desk.tray)))
        opener = [widget for widget in every(self.desk.tray) if widget.winfo_class() == "Label"
                  and widget.cget("text") == "In words ▾"][0]
        opener.event_generate("<ButtonRelease-1>")
        self.assertTrue(any(text.startswith("Bridge · Series: in this take") for text in texts(self.desk.tray)))


class WheelTest(DeskCase):
    def test_the_wheel_over_the_rounds_chips_steps_through_its_designs_and_stops_at_the_ends(self):
        self.script.answers.append(three())
        self.studio.ask("make a classic rock crunch")
        self.pump()
        ids = [item["sound"] for item in self.studio.round()["candidates"]]
        self.assertEqual(self.studio.state.b, ids[0])
        away, towards = SimpleNamespace(delta=-120), SimpleNamespace(delta=120)
        self.desk.wheel_round(away)
        self.assertEqual(self.studio.state.b, ids[1])
        self.desk.wheel_round(away)
        self.desk.wheel_round(away)
        self.assertEqual(self.studio.state.b, ids[2])                 # stops at the last
        self.desk.wheel_round(towards)
        self.assertEqual(self.studio.state.b, ids[1])
        self.root.update()
        chips = [chip for chip in self.desk.chips.winfo_children()
                 if str(chip.cget("text")).endswith(("  One", "  Two", "  Three"))]
        self.assertEqual(len(chips), 3)
        self.assertTrue(all(chip.bind("<MouseWheel>") for chip in chips))


class ConventionsTest(DeskCase):
    def test_what_the_ask_line_takes_besides_a_request_is_behind_its_question_mark(self):
        hints = [widget for widget in every(self.desk.request.master) if isinstance(widget, Hint)]
        self.assertEqual([found.words for found in hints], [CONVENTIONS])
        for convention in ("exact name", "Next", "add parallel space", "Solo +2 dB"):
            self.assertIn(convention, CONVENTIONS)


if __name__ == "__main__":
    unittest.main()
