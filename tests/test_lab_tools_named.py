"""Reading Neural DSP's named settings for each model from the unit, on the simulated unit."""

import gc
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest import mock

from lab import knowledge
from lab.qc.engine import Engine
from lab.qc.host import Host
from lab.qc.sim import MY_PRESETS, SimAudio, SimUnit
from lab.qc.slots import BUILD, Slots
from lab.tools import named
from lab.qc.unit import Unit, same_slot

AMP, DELAY, EQ = 1001, 6011, 4002


def engine_on(device, folder):
    unit = Unit(device, settle=.05, sleep=lambda seconds: None)
    return Engine(unit, Slots(unit, folder), SimAudio(device))


class NamedTest(unittest.TestCase):
    def setUp(self):
        gc.collect()    # what other modules left of Tk is collected here, on its own thread, not the engine's
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.path = self.folder / "device-presets.json"
        self.device = SimUnit(refuse=lambda grid, model_id: model_id == DELAY)
        self.device.save_slot(MY_PRESETS, 3, "Player's own", [(1081, {}), (12001, {})])
        self.device.player_selects(MY_PRESETS, 3)
        self.engine = engine_on(self.device, self.folder / "lab")
        # Where each write landed: nothing may be written anywhere but the build slot.
        self.where = []
        original = self.device._write

        def noting(call, *args):
            if call != "recall":
                self.where.append((call, dict(self.device.position)))
            original(call, *args)

        self.device._write = noting

    def record(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_every_named_setting_is_read_with_its_values(self):
        self.engine.open()
        found = named.harvest(self.engine, path=self.path)
        self.assertEqual((found["read"], found["recorded"], found["left"]), (3, 3, 0))
        amp = self.record()["models"][str(AMP)]
        self.assertTrue(amp["available"])
        self.assertEqual([preset["name"] for preset in amp["presets"]], ["Default", "Tight Modern", "Hot Lead"])
        tight = dict(amp["presets"][1]["values"])
        self.assertEqual((tight["GAIN"], tight["BASS"], tight["MID"]), (3., 6., 5.))
        self.assertEqual(dict(amp["presets"][0]["values"])["GAIN"], 5.)   # the default, as the block is added
        self.assertEqual(self.record()["firmware"], "4.1.1")

    def test_a_model_the_unit_refuses_is_recorded_as_unavailable(self):
        self.engine.open()
        found = named.harvest(self.engine, path=self.path)
        self.assertEqual(found["unavailable"], [DELAY])
        self.assertEqual(self.record()["models"][str(DELAY)], {"name": "Digital Delay (ST)", "category": "Delay",
                                                                "available": False, "presets": []})

    def test_writes_only_on_the_build_slot_and_saves_nothing(self):
        self.engine.open()
        named.harvest(self.engine, path=self.path)
        self.assertTrue(self.where)
        self.assertTrue(all(same_slot(position, BUILD) for _, position in self.where), self.where)
        self.assertNotIn("save", [call for call, _ in self.where])
        add_routes = [args for call, args in self.device.log if call == "route"]
        self.assertTrue(all(args[1] in (0, None) for args in add_routes))   # no input connected: nothing heard

    def test_the_players_preset_is_given_back_when_the_session_closes(self):
        self.engine.open()
        named.harvest(self.engine, path=self.path)
        self.engine.close()
        self.assertEqual(self.device.position["position"], 3)
        self.assertEqual(self.device.get_current_preset().name, "Player's own")

    def test_without_a_session_it_borrows_and_gives_back_by_itself(self):
        named.harvest(self.engine, path=self.path)
        self.assertEqual(self.device.position["position"], 3)
        self.assertFalse((self.folder / "lab" / "borrowed.json").exists())

    def test_a_preset_with_unsaved_changes_is_refused_before_anything_is_loaded(self):
        self.device.dirty = True
        with self.assertRaisesRegex(RuntimeError, "unsaved changes"):
            named.harvest(self.engine, path=self.path)
        self.device.dirty = False
        self.engine.open()
        self.device.dirty = True
        with self.assertRaisesRegex(RuntimeError, "unsaved changes"):
            named.harvest(self.engine, path=self.path)
        self.assertEqual(self.where, [])

    def test_a_partial_file_is_resumed_and_another_firmware_starts_again(self):
        self.engine.open()
        named.harvest(self.engine, path=self.path, only=[AMP])
        self.assertEqual(list(self.record()["models"]), [str(AMP)])
        again = named.harvest(self.engine, path=self.path)
        self.assertEqual((again["read"], again["recorded"]), (2, 3))
        self.assertEqual(named.harvest(self.engine, path=self.path)["read"], 0)
        self.engine.unit.firmware = "4.2.0"
        self.assertEqual(named.harvest(self.engine, path=self.path)["read"], 3)

    def test_stopping_keeps_what_was_read(self):
        self.engine.open()
        stop = threading.Event()
        said = []

        def report(text):
            said.append(text)
            if text.startswith("1/"):
                stop.set()

        found = named.harvest(self.engine, path=self.path, stop=stop, report=report)
        self.assertTrue(found["stopped"])
        self.assertEqual((found["read"], found["left"]), (1, 2))
        self.assertEqual(len(self.record()["models"]), 1)
        self.assertEqual(said[0], "3 models to read, 0 already recorded")

    def test_the_player_moving_on_the_unit_ends_it(self):
        self.engine.open()
        original = named._one

        def moving(engine, model_id, rows):
            self.device.player_selects(MY_PRESETS, 3)
            return original(engine, model_id, rows)

        with mock.patch.object(named, "_one", moving):
            with self.assertRaisesRegex(RuntimeError, "selected preset changed"):
                named.harvest(self.engine, path=self.path)
        self.assertNotIn("add", [call for call, position in self.where if position["position"] == 3])

    def test_written_where_knowledge_looks_and_read_back_at_once(self):
        self.engine.open()
        with mock.patch.object(knowledge, "REPO", self.folder):
            knowledge.table.cache_clear()
            knowledge.locked.cache_clear()
            try:
                found = named.harvest(self.engine)
                self.assertEqual(Path(found["path"]), self.folder / "tone_sessions" / "device-presets.json")
                self.assertEqual(knowledge.named_settings(AMP)["Tight Modern"]["GAIN"], 3.)
                self.assertNotIn("Default", knowledge.named_settings(AMP))
                self.assertIn(DELAY, knowledge.locked())
            finally:
                knowledge.table.cache_clear()
                knowledge.locked.cache_clear()

    def test_runs_where_the_unit_is_held(self):
        host = Host(self.folder / "host", simulated=True, local=True)
        try:
            host.ready.result(10)
            host.call("open").result(10)
            found = host.call("lab.tools.named.harvest", path=str(self.path), only=[AMP, EQ]).result(30)
            self.assertEqual((found["read"], found["unavailable"]), (2, []))
        finally:
            host.close()


class ValuesTest(unittest.TestCase):
    def test_a_stock_cab_of_another_shape_is_not_read_by_index(self):
        from lab.sound import catalog

        cab = catalog.model(12001)
        cell = mock.Mock(params=[object()] * 3)
        self.assertFalse(named.stored_as_listed(cell, cab))
        cell.params = [object()] * (len(cab.params) + 1)
        self.assertTrue(named.stored_as_listed(cell, cab))


if __name__ == "__main__":
    unittest.main()
