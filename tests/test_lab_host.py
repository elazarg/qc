"""The engine behind the host: one command at a time, stopping, and the unit given back."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lab.host import Host

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


if __name__ == "__main__":
    unittest.main()
