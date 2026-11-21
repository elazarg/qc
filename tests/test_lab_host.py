"""The engine behind the host: one command at a time, stopping, and the unit given back."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lab.qc.host import Host

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lab_engine import GREENBACK, HIWATT, PLEXI, V30, sound


class HostTest(unittest.TestCase):
    def test_commands_run_in_order_and_errors_come_back(self):
        with TemporaryDirectory() as folder:
            host = Host(Path(folder), simulated=True, local=True)
            self.assertEqual(host.ready.result(10), "ready")
            opened = host.call("open").result(10)
            self.assertEqual(opened["borrowed"]["name"], "Brit 2203")
            self.assertEqual(host.call("show", sound(PLEXI, V30), sound(HIWATT, GREENBACK)).result(10), "pair")
            self.assertEqual(host.call("play", "b", "guitar").result(10), {"side": "b", "mode": "guitar"})
            with self.assertRaisesRegex(RuntimeError, "no attribute|unknown"):
                host.call("nonsense").result(10)
            host.close()
            self.assertFalse(host.worker.is_alive())

    def test_the_real_process_starts_and_answers(self):
        with TemporaryDirectory() as folder:
            host = Host(Path(folder), simulated=True)
            self.assertEqual(host.ready.result(60), "ready")
            self.assertEqual(host.call("open").result(30)["calibrated"], False)
            host.close()


    def test_a_side_tool_runs_its_own_operation_where_the_unit_is_held(self):
        from tempfile import TemporaryDirectory
        from lab.qc.host import Host

        with TemporaryDirectory() as folder:
            host = Host(folder, simulated=True, local=True)
            host.ready.result(10)
            self.assertEqual(host.call("lab.tools.probe.firmware").result(10), "4.1.1")
            with self.assertRaises(RuntimeError):
                host.call("os.getcwd").result(10)
            host.close()

    def test_a_failure_comes_back_with_its_kind_and_the_trace_from_where_the_unit_is_held(self):
        from lab.qc.host import Fault

        with TemporaryDirectory() as folder:
            host = Host(Path(folder), simulated=True, local=True)
            host.ready.result(10)
            with self.assertRaises(Fault) as raised:
                host.call("nonsense").result(10)
            self.assertEqual(raised.exception.kind, "AttributeError")
            self.assertIn("host.py", raised.exception.trace)          # the worker's own trace, not the desk's
            self.assertFalse(raised.exception.expected)
            # An outcome of ordinary use is told apart from a fault by its kind.
            host.call("open").result(10)
            host.call("show", sound(PLEXI, V30), None).result(10)
            waiting = host.call("show", sound(HIWATT, GREENBACK), None)
            host.stop()
            try:
                waiting.result(10)
            except Fault as error:
                self.assertEqual(error.kind, "Stopped")
                self.assertTrue(error.expected)
            host.close()

    def test_unsaved_edits_on_the_players_preset_are_an_expected_refusal(self):
        from lab.qc.engine import Engine
        from lab.qc.host import EXPECTED
        from lab.qc.sim import SimAudio, SimUnit
        from lab.qc.slots import Slots, Unsaved
        from lab.qc.unit import Unit

        with TemporaryDirectory() as folder:
            device = SimUnit()
            device.set_param(0, 0, 0, .9)                             # the player edits their preset
            unit = Unit(device, settle=.05, sleep=lambda seconds: None)
            with self.assertRaises(Unsaved):
                Engine(unit, Slots(unit, Path(folder)), SimAudio(device)).open()
            self.assertIn("Unsaved", EXPECTED)


if __name__ == "__main__":
    unittest.main()
