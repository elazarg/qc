"""The tray of panels beside the tones: every open panel stays in reach, however small the window."""

import tkinter as tk
import unittest
from unittest.mock import patch

from tone_lab import desk_panels


class Desk:
    def __init__(self):
        self.changes = 0

    def panels_changed(self):
        self.changes += 1

    def remember(self):
        pass

    def situation(self):
        return {}


class TrayTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"No display for Tk: {error}")
        self.addCleanup(self.root.destroy)
        quiet = patch.object(desk_panels.usage, "record")
        quiet.start()
        self.addCleanup(quiet.stop)
        self.root.geometry("900x560+0+0")
        self.root.rowconfigure(0, weight=1)
        self.tray = desk_panels.Tray(self.root, Desk())
        self.fill = {}
        for name, key, height, grow in (("Shape", "S", 300, False), ("Phrase", "T", 170, False),
                                        ("History", "H", 400, True), ("Levels", "L", 90, False)):
            body = self.tray.add(name, key, grow=grow)
            self.fill[name] = tk.Frame(body, width=50, height=height)
            self.fill[name].pack()

    def settle(self):
        for _ in range(4):
            self.root.update()

    def visible(self, name):
        """Whether the whole of a panel's title lies inside the tray."""
        head = self.tray.heads[name]
        top = head.winfo_rooty() - self.tray.winfo_rooty()
        return head.winfo_ismapped() and 0 <= top and top + head.winfo_height() <= self.tray.winfo_height()

    def shown(self, name):
        return bool(self.tray.bodies[name].winfo_ismapped())

    def test_panels_that_fit_are_left_whole(self):
        self.tray.pin("Phrase", True)
        self.tray.open("Levels")
        self.settle()
        self.assertEqual(self.tray.folded, frozenset())
        self.assertTrue(self.shown("Phrase") and self.shown("Levels"))

    def test_older_panels_fold_to_their_titles_when_the_window_is_too_small_for_all(self):
        self.tray.pin("Shape", True)
        self.tray.pin("Phrase", True)
        self.tray.open("History")
        self.settle()
        # Shape, Phrase and History need far more than 560 pixels. The newest stays whole.
        self.assertTrue(self.shown("History"))
        self.assertIn("Shape", self.tray.folded)
        for name in ("Shape", "Phrase", "History"):
            self.assertTrue(self.visible(name), name)
        self.assertEqual(self.tray.marks["Shape"]["text"], "▸ ")

    def test_a_folded_panel_comes_forward_when_its_title_or_key_is_used(self):
        self.tray.pin("Shape", True)
        self.tray.pin("Phrase", True)
        self.tray.open("History")
        self.settle()
        self.tray.bring_forward("Shape")
        self.settle()
        self.assertTrue(self.shown("Shape"))
        self.assertNotIn("Shape", self.tray.folded)
        self.assertTrue(all(self.visible(name) for name in ("Shape", "Phrase", "History")))
        folded = sorted(self.tray.folded)[-1]
        self.tray.toggle(folded)  # The panel's key asks for it. It is shown, not closed.
        self.settle()
        self.assertTrue(self.tray.is_open(folded) and self.shown(folded))

    def test_a_panel_that_grows_makes_the_others_give_way_and_a_taller_window_brings_them_back(self):
        self.fill["Shape"].configure(height=120)
        self.tray.pin("Phrase", True)
        self.tray.pin("Levels", True)
        self.tray.open("Shape")
        self.settle()
        self.assertEqual(self.tray.folded, frozenset())
        self.fill["Shape"].configure(height=420)  # As when Shape is expanded to every control.
        self.settle()
        self.assertTrue(self.tray.folded)
        self.assertTrue(self.shown("Shape"))
        self.assertTrue(all(self.visible(name) for name in ("Phrase", "Levels", "Shape")))
        self.root.geometry("900x900+0+0")
        self.settle()
        self.assertEqual(self.tray.folded, frozenset())


def tearDownModule():
    # Tk objects left as garbage here must be freed on the main thread: if a later test's worker thread runs
    # the collector, Tcl aborts the whole interpreter ("Tcl_AsyncDelete").
    import gc

    gc.collect()


if __name__ == "__main__":
    unittest.main()
