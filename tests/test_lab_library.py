"""The presets saved on the unit, read where the unit is held and remembered for the desk."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lab import library
from lab.host import Host
from lab.sim import FACTORY, MY_PRESETS, TONE_LAB, SimUnit
from lab.slots import CACHE_SLOTS

PLEXI = 1001


def folder(name, key, files, **flags):
    return {"name": name, "key": key, "is_factory": False, "is_downloads": False,
            "files": [{"index": index, "name": text, **extra} for index, text, *more in files
                      for extra in (more[0] if more else {},)], **flags}


def table(*named):
    """A setlist's 256 slots, empty but for `named`: (position, name[, extra fields])."""
    rows = {row[0]: row for row in named}
    return [rows.get(index, (index, "")) for index in range(256)]


def engine_on(device):
    return SimpleNamespace(unit=SimpleNamespace(device=device))


class EntriesTest(unittest.TestCase):
    def test_user_setlists_keep_their_slot_addresses_and_leave_out_the_rest(self):
        rows = library.entries({"presets": [
            folder("My Presets", MY_PRESETS, table((0, ""), (5, "Named"), (7, "Locked", {"is_readonly": True}))),
            folder("Band", "1_q", table((19, "Another"))),
            folder("Downloads", "cloud-0-1", table((0, "Cloud")), is_downloads=True),
            folder("Plugin", "/opt/neuraldsp/Plugins/User", table((0, "Plugin"))),
            folder("Factory Library", FACTORY, table((0, "Brit 2203")), is_factory=True),
        ]})
        self.assertEqual(rows, [
            {"slot": {"folder_key": MY_PRESETS, "position": 5, "is_factory": False}, "name": "Named",
             "setlist": "My Presets"},
            {"slot": {"folder_key": "1_q", "position": 19, "is_factory": False}, "name": "Another",
             "setlist": "Band"},
        ])
        self.assertEqual(json.loads(json.dumps(rows)), rows)

    def test_factory_presets_are_flagged_when_asked_for(self):
        rows = library.entries({"presets": [
            folder("Factory Library", FACTORY, table((0, "Brit 2203")), is_factory=True),
            folder("My Presets", MY_PRESETS, table((1, "Mine"))),
        ]}, factory=True)
        self.assertEqual([(row["name"], row["slot"]["is_factory"]) for row in rows],
                         [("Mine", False), ("Brit 2203", True)])

    def test_the_apps_working_slots_are_hidden_only_where_the_app_works(self):
        cache = min(CACHE_SLOTS)
        rows = library.entries({"presets": [
            folder("My Presets", MY_PRESETS, table((3, "Tone Lab Style"), (254, "Tone Lab Style"),
                                                   (255, "Tone Lab Preview"))),
            folder("Tone Lab", TONE_LAB, table((0, "TL 0123456789ab"), (cache, "TL 0123456789ab"),
                                               (cache + 1, "Kept"), (255, "Tone Lab Live B"))),
        ]})
        self.assertEqual([(row["setlist"], row["slot"]["position"]) for row in rows],
                         [("My Presets", 3), ("Tone Lab", 0), ("Tone Lab", cache + 1)])

    def test_a_player_preset_in_a_working_slot_is_shown(self):
        rows = library.entries({"presets": [folder("My Presets", MY_PRESETS, table((254, "Crunch"))),
                                            folder("Tone Lab", TONE_LAB, table((255, "Lead")))]})
        self.assertEqual([row["name"] for row in rows], ["Crunch", "Lead"])

    def test_my_presets_come_first_then_setlists_by_name_then_slot(self):
        rows = library.entries({"presets": [
            folder("Zed", "2_q", table((1, "Z1"))),
            folder("Tone Lab", TONE_LAB, table((9, "T9"), (2, "T2"))),
            folder("My Presets", MY_PRESETS, table((4, "M4"))),
        ]})
        self.assertEqual([row["name"] for row in rows], ["M4", "T2", "T9", "Z1"])

    def test_names_and_positions_out_of_range_are_dropped(self):
        listing = {"presets": [folder("My Presets", MY_PRESETS, [(0, "  "), (300, "Far"), (1, " Padded ")])]}
        self.assertEqual([(row["name"], row["slot"]["position"]) for row in library.entries(listing)],
                         [("Padded", 1)])

    def test_a_slot_has_a_footswitch_place(self):
        self.assertEqual(library.place(0), "1A")
        self.assertEqual(library.place(19), "3D")
        self.assertEqual(library.place(255), "32H")


class DirectoryTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(TemporaryDirectory()))

    def test_the_simulated_unit_is_read_and_remembered(self):
        sim = SimUnit()
        sim.save_slot(MY_PRESETS, 2, "Crunch", [(PLEXI, {})])
        sim.save_slot(TONE_LAB, 0, "Lead", [(PLEXI, {})])
        rows = library.directory(engine_on(sim), folder=self.folder)
        self.assertEqual(rows, [
            {"slot": {"folder_key": MY_PRESETS, "position": 2, "is_factory": False}, "name": "Crunch",
             "setlist": "My Presets"},
            {"slot": {"folder_key": TONE_LAB, "position": 0, "is_factory": False}, "name": "Lead",
             "setlist": "Tone Lab"},
        ])
        self.assertEqual(library.cached(self.folder), rows)
        record = json.loads((self.folder / library.FILE).read_text(encoding="utf-8"))
        self.assertEqual(record["firmware"], "4.1.1")

    def test_the_app_home_is_the_default_place(self):
        with patch.dict(os.environ, {"TONE_LAB_HOME": str(self.folder)}):
            self.assertIsNone(library.cached())
            library.directory(engine_on(SimUnit()))
            self.assertEqual(library.cached(), [])
        self.assertTrue((self.folder / library.FILE).exists())

    def test_a_listing_without_my_presets_keeps_what_was_read_before(self):
        sim = SimUnit()
        sim.save_slot(MY_PRESETS, 2, "Crunch", [(PLEXI, {})])
        before = library.directory(engine_on(sim), folder=self.folder)
        broken = SimpleNamespace(list_directory=lambda: {"presets": []}, firmware="4.1.1")
        with self.assertRaisesRegex(RuntimeError, "did not list"):
            library.directory(engine_on(broken), folder=self.folder)
        self.assertEqual(library.cached(self.folder), before)

    def test_a_stopped_read_does_not_ask_the_unit(self):
        stop = Event()
        stop.set()
        asked = []
        device = SimpleNamespace(list_directory=lambda: asked.append(1))
        with self.assertRaises(RuntimeError):
            library.directory(engine_on(device), stop=stop, folder=self.folder)
        self.assertEqual(asked, [])

    def test_a_damaged_file_reads_as_not_read(self):
        (self.folder / library.FILE).write_text("{not json", encoding="utf-8")
        self.assertIsNone(library.cached(self.folder))
        (self.folder / library.FILE).write_text('{"presets": 3}', encoding="utf-8")
        self.assertIsNone(library.cached(self.folder))

    def test_the_host_runs_it_where_the_unit_is_held(self):
        with patch.dict(os.environ, {"TONE_LAB_HOME": str(self.folder / "home")}):
            host = Host(self.folder / "engine", simulated=True, local=True)
            try:
                self.assertEqual(host.ready.result(10), "ready")
                self.assertEqual(host.call("lab.library.directory").result(10), [])
                self.assertEqual(library.cached(), [])
            finally:
                host.close()


if __name__ == "__main__":
    unittest.main()
