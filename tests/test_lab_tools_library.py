"""The library: filtering the unit's presets, reading them, capturing a reference and leaving one selected."""

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
        self.assertEqual(library.slot_name(rows[1]["slot"]), "32G")
        self.assertEqual([found["name"] for found in library.filtered(rows, "vai")], ["Vai for The Love God", "Lead Vai"])
        self.assertEqual([found["name"] for found in library.filtered(rows, "vai gigs")], ["Lead Vai"])
        self.assertEqual([found["name"] for found in library.filtered(rows, "32g")], ["Clean Strat"])
        self.assertEqual([found["name"] for found in library.ordered(rows)],
                         ["Vai for The Love God", "Lead Vai", "Clean Strat"])

    def test_nothing_read_yet_is_an_empty_list(self):
        with TemporaryDirectory() as folder:
            self.assertEqual(library.cached(folder), [])


class OnTheUnitTest(unittest.TestCase):
    def test_reading_capturing_and_leaving_a_preset_on_the_unit(self):
        with TemporaryDirectory() as folder:
            unit = host(folder)
            try:
                unit.call("open").result(10)
                rows = unit.call("lab.tools.library.directory").result(30)
                self.assertIsInstance(rows, list)
                self.assertEqual(library.cached(folder), rows)      # kept in the desk's data folder
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


if __name__ == "__main__":
    unittest.main()
