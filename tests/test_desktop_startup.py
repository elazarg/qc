"""The earlier app's own desktop integration (`tone_lab.desktop`), reached with `--old`.

These tests are the earlier app's and go when it goes: without `tone_lab/` they are skipped. The launcher's own
side, which does not need the earlier app, is tested in test_lab_launcher.py.
"""

from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import Mock, patch

try:
    from tone_lab import desktop
except ImportError:         # the earlier app has been removed
    desktop = None


@unittest.skipUnless(sys.platform == "win32" and desktop is not None, "the earlier app, on Windows")
class DesktopStartupTest(unittest.TestCase):
    def test_second_launch_raises_the_existing_workspace(self):
        import win32event

        with (patch.object(win32event, "CreateMutex") as create,
              patch.object(win32event, "WaitForSingleObject", return_value=win32event.WAIT_TIMEOUT),
              patch.object(win32event, "ReleaseMutex") as release,
              patch.object(desktop, "workspace_window", return_value=42),
              patch.object(desktop, "bring_forward") as raise_window):
            with desktop.claim_workspace() as acquired:
                self.assertFalse(acquired)
            raise_window.assert_called_once_with(42)
            release.assert_not_called()
            create.return_value.Close.assert_called_once()

    def test_old_opens_the_earlier_desk_and_a_failure_of_it_is_logged_and_shown(self):
        bootstrap = runpy.run_path(str(desktop.ROOT / "desktop_launcher" / "start.pyw"))
        main = bootstrap["main"]
        log = Path("startup.log")
        dialog = Mock()
        with (patch.dict(main.__globals__, {"write_log": Mock(return_value=log), "take_identity": Mock()}),
              patch.object(sys, "path", list(sys.path)),
              patch.object(desktop, "open_desk", side_effect=ValueError("Initialization failed")),
              patch.object(bootstrap["ctypes"].windll.user32, "MessageBoxW", dialog)):
            self.assertEqual(main(["--old"]), 2)
            failure = main.__globals__["write_log"].call_args.args[0]
            self.assertIn("ValueError: Initialization failed", failure)
        self.assertIn("Initialization failed", dialog.call_args.args[1])
        self.assertIn(str(log), dialog.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
