"""The launcher's Python side (desktop_launcher/start.pyw) and the shortcut's taskbar repair.

Nothing here opens a desk, shows a message box or touches the real data folder: the desk, the box and the
folder are stand-ins."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import os
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from lab import cli, setup

START = setup.REPO / "desktop_launcher" / "start.pyw"


class StartTest(unittest.TestCase):
    def setUp(self):
        self.launcher = runpy.run_path(str(START))
        self.main = self.launcher["main"]
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "Tone Lab"
        patcher = patch.dict(os.environ, {"TONE_LAB_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.box = Mock()
        self.identity = Mock()

    def run_main(self, arguments, **stand_ins):
        """`main` with the desk, the message box and the taskbar identity replaced."""
        ctypes = self.launcher["ctypes"]
        with patch.dict(self.main.__globals__, stand_ins), patch.object(sys, "path", list(sys.path)), \
                patch.object(ctypes.windll.user32, "MessageBoxW", self.box), \
                patch.object(ctypes.windll.shell32, "SetCurrentProcessExplicitAppUserModelID", self.identity):
            return self.main(arguments)

    def test_the_process_that_owns_the_window_takes_tone_lab_s_taskbar_identity(self):
        # Regression: only the launcher had it, and the launcher is gone seconds after it starts this process.
        opened = Mock()
        self.assertEqual(self.run_main(["--simulated"], open_desk=opened), 0)
        self.identity.assert_called_once_with(cli.APP_ID)
        self.assertEqual(self.launcher["APP_ID"], cli.APP_ID)
        opened.assert_called_once_with(["--simulated"])
        self.box.assert_not_called()

    def test_the_start_is_logged_in_the_data_folder_once_there_is_one(self):
        # Regression: the log was kept in the earlier app's folder inside the program folder.
        self.assertEqual(self.launcher["log_files"](os.environ)[0].parent, Path(self.launcher["tempfile"].gettempdir()))
        self.assertFalse(self.home.exists())            # finding the log's place creates nothing
        self.home.mkdir()
        self.assertEqual(self.launcher["log_files"](os.environ)[0], self.home / "startup.log")
        self.assertEqual(self.run_main([], open_desk=Mock()), 0)
        log = (self.home / "startup.log").read_text(encoding="utf-8")
        self.assertIn("Opening Tone Lab", log)
        self.assertIn("Launch finished", log)
        self.assertNotIn("tone_sessions", START.read_text(encoding="utf-8"))

    def test_the_log_does_not_grow_without_end(self):
        self.home.mkdir()
        limit = self.launcher["LOG_LIMIT"]
        (self.home / "startup.log").write_text("an old line\n" * (limit // 12 + 100), encoding="utf-8")
        self.launcher["write_log"]("a new line", os.environ)
        log = (self.home / "startup.log").read_text(encoding="utf-8")
        self.assertLess(len(log), limit)
        self.assertTrue(log.startswith("an old line\n"))        # cut at a line, not in the middle of one
        self.assertTrue(log.endswith("a new line\n"))

    def test_a_desk_that_cannot_open_is_logged_with_its_trace_and_shown(self):
        self.home.mkdir()

        def broken(arguments):
            raise ValueError("Initialization failed")

        self.assertEqual(self.run_main([], open_desk=broken), 2)
        self.assertIn("ValueError: Initialization failed", (self.home / "startup.log").read_text(encoding="utf-8"))
        message = self.box.call_args.args[1]
        self.assertIn("Initialization failed", message)
        self.assertIn(str(self.home / "startup.log"), message)

    def test_asking_for_the_earlier_app_says_it_is_gone_and_how_to_go_on(self):
        self.assertEqual(self.run_main(["--old"]), 2)
        message = self.box.call_args.args[1]
        self.assertIn("no longer part of Tone Lab", message)
        self.assertIn("without --old", message)

    def test_a_shortcut_made_while_there_were_two_desks_still_opens_the_desk(self):
        from unittest.mock import patch as replace

        opened = Mock()
        with replace.dict(sys.modules, {"lab.desk.window": Mock(main=opened)}):
            self.assertEqual(self.run_main(["--new", "--simulated"]), 0)
        opened.assert_called_once_with(["--simulated"])

    def test_nothing_needs_the_earlier_app(self):
        """No part of the program imports `tone_lab`, and there is no such folder to import."""
        self.assertFalse((setup.REPO / "tone_lab").exists())
        sources = [START, *(setup.REPO / "lab").rglob("*.py"), *(setup.REPO / "research").glob("*.py")]
        needing = [path.name for path in sources
                   if any(sign in path.read_text(encoding="utf-8")
                          for sign in ("import tone_lab", "from tone_lab", '"tone_sessions" / "recordings"'))]
        self.assertEqual(needing, [])


@unittest.skipUnless(sys.platform == "win32", "Windows desktop integration")
class ShortcutTest(unittest.TestCase):
    def test_repair_updates_only_pinned_shortcuts_with_tone_lab_s_id(self):
        from win32com.propsys import propsys
        from win32com.shell import shell

        with TemporaryDirectory() as folder:
            appdata = Path(folder)
            pins = appdata / "Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar"
            pins.mkdir(parents=True)
            ours, other = pins / "Python.lnk", pins / "Another App.lnk"
            ours.touch()
            other.touch()

            def candidate(path):
                store = Mock()
                store.GetValue.return_value.GetValue.return_value = cli.APP_ID if Path(path) == ours else "OtherApp"
                return store

            file = Mock()
            with patch.object(propsys, "SHGetPropertyStoreFromParsingName", side_effect=candidate), \
                    patch.object(shell, "SHChangeNotify") as notify:
                cli._repair_taskbar_links(file, appdata)
            file.Save.assert_called_once_with(str(ours), False)
            self.assertEqual(notify.call_args.args[2], str(ours))


if __name__ == "__main__":
    unittest.main()
