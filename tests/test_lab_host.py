"""The engine behind the host: one command at a time, stopping, starting up, and the unit given back."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
from pathlib import Path  # noqa: E402
from tempfile import TemporaryDirectory  # noqa: E402
import threading  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from lab.qc import engine as engine_module  # noqa: E402
from lab.qc.engine import Engine  # noqa: E402
from lab.qc.host import EXPECTED, Fault, Host  # noqa: E402
from lab.qc.sim import SimAudio, SimUnit  # noqa: E402
from lab.qc.slots import Slots, Unsaved  # noqa: E402
from lab.qc.unit import Unit  # noqa: E402

from test_lab_engine import GREENBACK, HIWATT, PLEXI, V30, sound  # noqa: E402


class HostCase(unittest.TestCase):
    def host(self, **options):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        found = Host(Path(folder.name), simulated=True, **{"local": True, **options})
        self.addCleanup(found.close)
        return found


class HostTest(HostCase):
    def test_commands_run_in_order_and_errors_come_back(self):
        host = self.host()
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
        host = self.host(local=False)
        self.assertEqual(host.ready.result(60), "ready")
        self.assertEqual(host.call("open").result(30)["calibrated"], False)

    def test_a_side_tool_runs_its_own_operation_where_the_unit_is_held(self):
        host = self.host()
        host.ready.result(10)
        self.assertEqual(host.call("lab.tools.probe.firmware").result(10), "4.1.1")
        with self.assertRaisesRegex(RuntimeError, "Not an operation of this app"):
            host.call("os.getcwd").result(10)

    def test_a_failure_comes_back_with_its_kind_and_the_trace_from_where_the_unit_is_held(self):
        host = self.host()
        host.ready.result(10)
        with self.assertRaises(Fault) as raised:
            host.call("nonsense").result(10)
        self.assertEqual(raised.exception.kind, "AttributeError")
        self.assertIn("host.py", raised.exception.trace)          # the worker's own trace, not the desk's
        self.assertFalse(raised.exception.expected)

    def test_unsaved_edits_on_the_players_preset_are_an_expected_refusal(self):
        with TemporaryDirectory() as folder:
            device = SimUnit()
            device.set_param(0, 0, 0, .9)                             # the player edits their preset
            unit = Unit(device, settle=.05, sleep=lambda seconds: None)
            with self.assertRaises(Unsaved):
                Engine(unit, Slots(unit, Path(folder)), SimAudio(device)).open()
            self.assertIn("Unsaved", EXPECTED)


class StopTest(HostCase):
    """Stop ends the command running and drops the earlier ones waiting; what is sent after it always runs."""

    def test_the_command_sent_after_stop_runs_and_the_waiting_one_before_it_does_not(self):
        """(review 2026-11-20)"""
        running, release = threading.Event(), threading.Event()

        def slow(engine):
            running.set()
            release.wait(5)
            return {"moved": False}

        with mock.patch.object(Engine, "watch", slow):
            host = self.host()
            host.ready.result(10)
            first = host.call("watch")
            self.assertTrue(running.wait(5))          # the worker is in the first command
            waiting = host.call("scene", 1)
            host.stop()
            quiet = host.call("quiet")
            release.set()
            self.assertEqual(first.result(5), {"moved": False})
            with self.assertRaisesRegex(RuntimeError, "Stopped before it began"):
                waiting.result(5)
            self.assertIsNone(quiet.result(5))

    def test_stop_ends_a_build_between_two_writes_and_nothing_is_left_connected(self):
        """`Unit.run` looks at the stop flag before every write. Stopped after the first block of four went on,
        the build writes no further block, the command comes back as stopped (an outcome of ordinary use, not a
        fault), and the next command runs."""
        added, reached, release = [], threading.Event(), threading.Event()
        add_block = SimUnit.add_block

        def add_then_wait(device, model_id, row=0, column=0, **options):
            found = add_block(device, model_id, row=row, column=column, **options)
            added.append(model_id)
            if len(added) == 1:
                reached.set()
                release.wait(5)
            return found

        with mock.patch.object(SimUnit, "add_block", add_then_wait):
            host = self.host()
            host.ready.result(10)
            host.call("open").result(10)
            building = host.call("show", sound(PLEXI, V30), sound(HIWATT, GREENBACK))
            self.assertTrue(reached.wait(5))          # the build is between its first and second block
            host.stop()
            release.set()
            with self.assertRaises(Fault) as stopped:
                building.result(10)
            self.assertEqual(stopped.exception.kind, "Stopped")
            self.assertTrue(stopped.exception.expected)
            self.assertEqual(added, [PLEXI])          # none of the other three blocks was written
            held = host.call("lab.tools.probe.held").result(10)
            self.assertEqual([row["inlet"] for row in held["rows"]], [0, 0, 0, 0])
            # What is asked next is done, from the start.
            self.assertEqual(host.call("show", sound(PLEXI, V30), sound(HIWATT, GREENBACK)).result(10), "pair")
            self.assertEqual(added[1:], [PLEXI, V30, HIWATT, GREENBACK])


class StartupTest(HostCase):
    def test_a_worker_that_could_not_start_fails_every_command_at_once(self):
        """(review 2026-11-20)"""
        def broken(*args, **options):
            raise RuntimeError("ASIO could not be opened")

        with mock.patch.object(engine_module.Engine, "__init__", broken):
            host = self.host()
            with self.assertRaisesRegex(RuntimeError, "could not be opened: RuntimeError: ASIO could not be opened"):
                host.ready.result(5)
            with self.assertRaisesRegex(RuntimeError, "ASIO could not be opened"):
                host.call("open").result(1)


if __name__ == "__main__":
    unittest.main()
