"""What the Sounds panel does with a saved preset beside working on it: filtering the unit's presets, capturing
one as a reference and leaving one selected. (The panel itself is in test_lab_one_window.py.)"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from lab.qc.sim import FACTORY, MY_PRESETS
from lab.tools import library, search

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lab_tools_support import PLEXI, V30, chain, host, phrase  # noqa: E402


def row(name, setlist, position, key=MY_PRESETS):
    return {"slot": {"folder_key": key, "position": position, "is_factory": False}, "name": name, "setlist": setlist}


class FilterTest(unittest.TestCase):
    def test_every_word_must_be_in_the_name_the_setlist_or_the_slot(self):
        rows = [row("Vai for The Love God", "My Presets", 5), row("Clean Strat", "Gigs", 254, "/media/p4/Presets/Gigs"),
                row("Lead Vai", "Gigs", 9, "/media/p4/Presets/Gigs")]
        self.assertIn("32G", library.text_of(rows[1]))          # the slot as the unit shows it (`qc.library.place`)
        self.assertFalse(hasattr(library, "slot_name"))         # the tool's own copy of `place` is gone
        self.assertEqual([found["name"] for found in library.filtered(rows, "vai")], ["Vai for The Love God", "Lead Vai"])
        self.assertEqual([found["name"] for found in library.filtered(rows, "vai gigs")], ["Lead Vai"])
        self.assertEqual([found["name"] for found in library.filtered(rows, "32g")], ["Clean Strat"])
        self.assertEqual(library.filtered(rows, ""), rows)

    def test_the_tool_that_listed_the_presets_is_gone_and_its_operations_are_kept(self):
        # The list, its filter and Work on this are the Sounds panel's; a second window did the same.
        for name in ("LibraryWindow", "open", "ordered", "cached", "directory"):
            self.assertFalse(hasattr(library, name), name)
        for name in ("filtered", "capture", "leave", "keep_reference"):
            self.assertTrue(callable(getattr(library, name)), name)


class OnTheUnitTest(unittest.TestCase):
    def test_reading_capturing_and_leaving_a_preset_on_the_unit(self):
        with TemporaryDirectory() as folder:
            unit = host(folder)
            try:
                unit.call("open").result(10)
                factory = {"folder_key": FACTORY, "position": 0, "is_factory": True}
                captured = unit.call("lab.tools.library.capture", factory, phrase(), 1).result(60)
                self.assertEqual(captured["name"], "Brit 2203")
                target = library.keep_reference(Path(folder), {"slot": factory, "setlist": "Factory"}, 1, captured, "p1")
                found = search.references(folder)
                self.assertEqual(found[0]["name"], "Brit 2203")
                self.assertEqual(found[0]["scene"], 1)
                self.assertTrue(Path(found[0]["file"]).is_file())
                self.assertEqual(json.loads((target / "reference.json").read_text(encoding="utf-8"))["phrase"], "p1")
                left = unit.call("lab.tools.library.leave", factory).result(30)
                self.assertEqual(left["name"], "Brit 2203")
            finally:
                unit.close()

    def test_a_preset_left_on_the_unit_is_the_one_given_back(self):
        from lab.qc.engine import Engine
        from lab.qc.sim import SimAudio, SimUnit
        from lab.qc.slots import Slots
        from lab.qc.unit import Unit

        with TemporaryDirectory() as folder:
            device = SimUnit()
            device.save_slot(MY_PRESETS, 5, "Played before", [(PLEXI, {}), (V30, {})])
            device.save_slot(MY_PRESETS, 7, "Wanted after", [(PLEXI, {})])
            device.recall(MY_PRESETS, 5)
            unit = Unit(device, settle=.05, sleep=lambda seconds: None)
            engine = Engine(unit, Slots(unit, Path(folder)), SimAudio(device))
            engine.open()
            library.leave(engine, {"folder_key": MY_PRESETS, "position": 7, "is_factory": False})
            engine.show(chain(PLEXI, V30), None)          # the desk goes on working on its own slots
            self.assertNotEqual(unit.position()["position"], 7)
            engine.close()
            self.assertEqual(unit.position()["position"], 7)
            self.assertEqual(unit.name(), "Wanted after")

    def test_a_preset_the_player_is_editing_is_not_recalled_over(self):
        # Regression: "Leave it on the unit" recalled the chosen preset whatever the unit held, losing unsaved edits.
        from lab.qc.engine import Engine
        from lab.qc.sim import SimAudio, SimUnit
        from lab.qc.slots import Unsaved, Slots
        from lab.qc.unit import Unit

        with TemporaryDirectory() as folder:
            device = SimUnit()
            device.save_slot(MY_PRESETS, 5, "Being edited", [(PLEXI, {}), (V30, {})])
            device.save_slot(MY_PRESETS, 7, "Wanted after", [(PLEXI, {})])
            device.recall(MY_PRESETS, 5)
            unit = Unit(device, settle=.05, sleep=lambda seconds: None)
            engine = Engine(unit, Slots(unit, Path(folder)), SimAudio(device))
            engine.open()
            device.player_selects(MY_PRESETS, 5)        # the player goes back to it on the unit, and turns a knob
            device.dirty = True
            with self.assertRaises(Unsaved):
                library.leave(engine, {"folder_key": MY_PRESETS, "position": 7, "is_factory": False})
            self.assertEqual(unit.position()["position"], 5)
            self.assertTrue(device.dirty)


if __name__ == "__main__":
    unittest.main()
