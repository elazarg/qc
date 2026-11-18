from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from tone_lab import desktop


@unittest.skipUnless(sys.platform == "win32", "Windows desktop integration")
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

    def test_repair_updates_only_shortcuts_with_tone_labs_id(self):
        from win32com.propsys import propsys
        from win32com.shell import shell

        with TemporaryDirectory() as folder:
            appdata = Path(folder)
            pins = appdata / "Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar"
            pins.mkdir(parents=True)
            old_pin, other_pin = pins / "Python.lnk", pins / "Another App.lnk"
            old_pin.touch()
            other_pin.touch()

            def candidate(path):
                store = Mock()
                store.GetValue.return_value.GetValue.return_value = (
                    desktop.APP_ID if Path(path) == old_pin else "OtherApp"
                )
                return store

            file = Mock()
            with (patch.object(propsys, "SHGetPropertyStoreFromParsingName", side_effect=candidate),
                  patch.object(shell, "SHChangeNotify") as notify):
                desktop.repair_taskbar_links(file, appdata)
                file.Save.assert_called_once_with(str(old_pin), False)
                self.assertEqual(notify.call_args.args[2], str(old_pin))

    def test_bootstrap_logs_and_displays_an_initialization_failure(self):
        bootstrap = runpy.run_path(str(desktop.ROOT / "desktop_launcher" / "start.pyw"))
        main = bootstrap["main"]
        log = Path("startup.log")
        dialog = Mock()
        with (patch.dict(main.__globals__, {"write_log": Mock(return_value=log)}),
              patch.object(sys, "path", list(sys.path)),
              patch.object(desktop, "open_desk", side_effect=ValueError("Initialization failed")),
              patch.object(bootstrap["ctypes"].windll.user32, "MessageBoxW", dialog)):
            self.assertEqual(main(), 2)
            failure = main.__globals__["write_log"].call_args.args[0]
            self.assertIn("ValueError: Initialization failed", failure)
        self.assertIn("Initialization failed", dialog.call_args.args[1])
        self.assertIn(str(log), dialog.call_args.args[1])
