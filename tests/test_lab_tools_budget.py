"""Reading each model's processing cost from the unit, on the simulated unit."""

import gc
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock

from lab import knowledge
from lab.qc.engine import Engine
from lab.qc.host import Host
from lab.qc.sim import SimAudio, SimUnit
from lab.qc.slots import Slots
from lab.tools import budget
from lab.qc.unit import Unit


def engine_on(device, folder):
    unit = Unit(device, settle=.05, sleep=lambda seconds: None)
    return Engine(unit, Slots(unit, folder), SimAudio(device))


class BudgetTest(unittest.TestCase):
    def setUp(self):
        gc.collect()    # what other modules left of Tk is collected here, on its own thread, not the engine's
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.device = SimUnit()
        self.engine = engine_on(self.device, self.folder / "lab")

    def test_the_table_is_written_in_the_form_knowledge_reads(self):
        path = self.folder / "costs.json"
        found = budget.measure(self.engine, path=path)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(found["models"], len(record["models"]))
        self.assertEqual(record["firmware"], "4.1.1")
        self.assertEqual(set(record["core"]), set(budget.FIELDS))
        amp = record["models"]["1001"]
        self.assertAlmostEqual(amp["cpu"], round(1 + 1001 % 97 / 10, 3))
        self.assertNotIn("dmda", amp)                     # a measure of zero is left out, as before
        self.assertIn("memory", record["models"]["6011"])  # a delay uses delay memory
        self.assertNotIn("11000", record["models"])       # the mixer costs nothing and is not listed

    def test_each_processor_measure_is_the_smaller_of_the_two(self):
        record = budget.table_of(self.device.read_message("ModuleStats"), "4.1.1")
        self.assertEqual(record["core"]["dm"], 180000)
        self.assertEqual(record["core"]["cpu"], 92.)

    def test_written_where_knowledge_looks_and_read_back_at_once(self):
        shared = self.folder / "knowledge"
        with mock.patch.object(knowledge, "SHARED", shared):
            knowledge.table.cache_clear()
            try:
                found = budget.measure(self.engine)
                self.assertEqual(Path(found["path"]), shared / "costs.json")
                self.assertEqual(knowledge.cost(1001)["cpu"], round(1 + 1001 % 97 / 10, 3))
                self.assertTrue(knowledge.weigh([1001, 12001]).fits)
                self.assertEqual(knowledge.stale("4.1.1"), [])
                self.assertIn("costs", [name for name, _, _ in knowledge.stale("4.2.0")])
            finally:
                knowledge.table.cache_clear()

    def test_nothing_is_written_to_the_unit(self):
        budget.measure(self.engine, path=self.folder / "costs.json")
        self.assertEqual(self.device.log, [])

    def test_a_unit_that_does_not_answer_leaves_the_old_table(self):
        path = self.folder / "costs.json"
        path.write_text('{"kept": true}', encoding="utf-8")
        with mock.patch.object(self.device, "read_message", lambda name, **_: None):
            with self.assertRaisesRegex(RuntimeError, "table of processing costs"):
                budget.measure(self.engine, path=path)
        with mock.patch.object(self.device, "read_message", lambda name, **_: SimpleNamespace(stats=[], core_stats=[])):
            with self.assertRaises(RuntimeError):
                budget.measure(self.engine, path=path)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"kept": True})

    def test_stopped_before_it_begins(self):
        stop = SimpleNamespace(is_set=lambda: True)
        with self.assertRaisesRegex(RuntimeError, "Stopped"):
            budget.measure(self.engine, stop=stop, path=self.folder / "costs.json")

    def test_runs_where_the_unit_is_held(self):
        host = Host(self.folder / "lab", simulated=True, local=True)
        try:
            host.ready.result(10)
            path = self.folder / "costs.json"
            found = host.call("lab.tools.budget.measure", path=str(path)).result(10)
            self.assertEqual(found["firmware"], "4.1.1")
            self.assertTrue(path.is_file())
        finally:
            host.close()


if __name__ == "__main__":
    unittest.main()
