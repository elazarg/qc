"""First run on a new computer: plain findings for each check, and the data moved out of the program folder."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lab import knowledge, setup

QC = ("Quad Cortex (NeuralDSP USB Audio)", "ASIO", 8, 8)


class PackagesTest(unittest.TestCase):
    def test_the_requirements_name_the_modules_to_import(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "requirements.txt"
            path.write_text("numpy>=2.1,<3\n# a comment\n\nsounddevice>=0.5.3\n"
                            "qc-mcp @ git+https://github.com/x/qc-mcp.git@abc\n", encoding="utf-8")
            self.assertEqual(setup.requirements(path),
                             [("numpy", "numpy"), ("sounddevice", "sounddevice"), ("qc-mcp", "qc_mcp")])

    def test_the_real_requirements_are_read(self):
        self.assertIn(("qc-mcp", "qc_mcp"), setup.requirements())

    def test_every_package_loads(self):
        found = setup.check_packages(load=lambda module: None)
        self.assertEqual((found["check"], found["ok"]), ("packages", True))

    def test_a_package_that_does_not_load_is_named_with_how_to_install(self):
        def load(module):
            if module == "scipy":
                raise ImportError("No module named 'scipy'")

        found = setup.check_packages(load=load)
        self.assertFalse(found["ok"])
        self.assertIn("scipy", found["say"])
        self.assertIn("pip install -r requirements.txt", found["say"])

    def test_a_missing_list_is_said(self):
        found = setup.check_packages(Path("no-such-requirements.txt"))
        self.assertFalse(found["ok"])


class AudioTest(unittest.TestCase):
    def test_the_unit_on_its_asio_driver_passes(self):
        found = setup.check_audio([("Speakers", "MME", 0, 2), QC])
        self.assertEqual((found["check"], found["ok"]), ("audio", True))
        self.assertIn("Quad Cortex", found["say"])

    def test_the_unit_without_its_asio_driver_says_to_install_cortex_control(self):
        found = setup.check_audio([("Quad Cortex", "MME", 2, 2)])
        self.assertFalse(found["ok"])
        self.assertIn("ASIO", found["say"])
        self.assertIn("Cortex Control", found["say"])

    def test_no_unit_says_to_connect_it(self):
        found = setup.check_audio([("Speakers", "Windows WASAPI", 0, 2)])
        self.assertFalse(found["ok"])
        self.assertIn("Connect it", found["say"])


class CodexTest(unittest.TestCase):
    def test_missing_codex_says_how_to_install_and_sign_in(self):
        def locate():
            raise RuntimeError("Could not find Codex.")

        found = setup.check_codex(locate=locate, run=None)
        self.assertEqual((found["check"], found["ok"]), ("assistant", False))
        self.assertIn("npm install -g @openai/codex", found["say"])
        self.assertIn("codex login", found["say"])

    def test_signed_in(self):
        ran = []

        def run(command):
            ran.append(command)
            return SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr="")

        found = setup.check_codex(locate=lambda: "C:/codex.exe", run=run)
        self.assertTrue(found["ok"])
        self.assertEqual(ran, [["C:/codex.exe", "login", "status"]])

    def test_not_signed_in(self):
        found = setup.check_codex(locate=lambda: "codex",
                                  run=lambda command: SimpleNamespace(returncode=1, stdout="Not logged in", stderr=""))
        self.assertFalse(found["ok"])
        self.assertIn("codex login", found["say"])

    def test_a_sign_in_that_cannot_be_checked_does_not_block(self):
        def run(command):
            raise OSError("cannot run")

        found = setup.check_codex(locate=lambda: "codex", run=run)
        self.assertTrue(found["ok"])
        self.assertIn("codex login", found["say"])


class HomeTest(unittest.TestCase):
    def test_a_writable_folder_passes_and_leaves_nothing_behind(self):
        with TemporaryDirectory() as folder:
            found = setup.check_home(Path(folder) / "Tone Lab")
            self.assertEqual((found["check"], found["ok"]), ("home", True))
            self.assertEqual(list((Path(folder) / "Tone Lab").iterdir()), [])

    def test_a_folder_that_cannot_be_made_says_so(self):
        with TemporaryDirectory() as folder:
            blocker = Path(folder) / "file"
            blocker.write_text("x", encoding="utf-8")
            found = setup.check_home(blocker / "Tone Lab")
            self.assertFalse(found["ok"])
            self.assertIn("TONE_LAB_HOME", found["say"])

    def test_checks_run_in_order_and_each_is_a_plain_finding(self):
        with TemporaryDirectory() as folder:
            found = setup.checks(folder, packages=lambda: setup.finding("packages", True, "ok"),
                                 audio=lambda: setup.finding("audio", False, "Connect it."),
                                 assistant=lambda: setup.finding("assistant", True, "ok"))
        self.assertEqual([item["check"] for item in found], ["packages", "tables", "audio", "assistant", "home"])
        for item in found:
            self.assertEqual(set(item), {"check", "ok", "say"})

    def test_a_missing_table_is_said_with_how_to_restore_it(self):
        from lab import knowledge

        self.assertTrue(setup.check_tables()["ok"])
        with TemporaryDirectory() as folder, patch.object(knowledge, "SHARED", Path(folder)):
            found = setup.check_tables()
        self.assertEqual((found["check"], found["ok"]), ("tables", False))
        self.assertIn("gear.json", found["say"])
        self.assertIn("git checkout", found["say"])

    def test_the_shortcut_s_package_is_listed_and_checked(self):
        # Regression: `python -m lab shortcut` needs pywin32, which no list named and no check looked for.
        self.assertIn(("pywin32", "pythoncom"), setup.requirements())
        loaded = []
        setup.check_packages(load=loaded.append)
        self.assertIn("pythoncom", loaded)


class FirstRunTest(unittest.TestCase):
    def checking(self, made, ok=False):
        """`setup.checks` replaced by checks that count how often they are made."""
        def one(name):
            def check():
                made.append(name)
                return setup.finding(name, ok, f"{name} is missing.")
            return check
        real = setup.checks
        return patch.object(setup, "checks", lambda home, again=False: real(
            home, again=again, packages=one("packages"), tables=one("tables"), audio=one("audio"),
            assistant=one("assistant")))

    def test_the_checks_are_made_once_and_their_outcome_recorded(self):
        # Regression: every start repeated the checks (seconds, before the window) until all of them passed.
        from lab import migrate

        with TemporaryDirectory() as folder, patch.object(migrate, "OLD", Path(folder) / "none"), \
                patch.object(migrate.migrate, "__defaults__", (Path(folder) / "none",)):
            home, made = Path(folder) / "home", []
            with self.checking(made):
                self.assertTrue(setup.first_run_needed(home))
                said = setup.first_run(home)
                self.assertIn("audio is missing.", said)
                self.assertEqual(made, ["packages", "tables", "audio", "assistant"])
                self.assertFalse(setup.first_run_needed(home))          # although a check failed
                self.assertEqual(setup.first_run(home), "")
                self.assertEqual(len(made), 4)
                self.assertEqual([item["check"] for item in setup.checks(home)][:2], ["packages", "tables"])
                self.assertEqual(len(made), 4)                          # what was recorded, not checked again
                setup.checks(home, again=True)                          # as `doctor` asks
                self.assertEqual(len(made), 8)
            noted = json.loads((home / setup.MARKER).read_text(encoding="utf-8"))
            self.assertEqual((noted["passed"], noted["findings"][0]["check"]), (False, "packages"))

    def test_nothing_is_said_about_an_earlier_app_there_never_was(self):
        # Regression: a first start with no earlier history said "0 designs and 0 presets as sounds, 0 rounds…".
        from lab import migrate

        with TemporaryDirectory() as folder, patch.object(migrate, "OLD", Path(folder) / "none"), \
                patch.object(migrate.migrate, "__defaults__", (Path(folder) / "none",)):
            with self.checking([], ok=True):
                self.assertEqual(setup.first_run(Path(folder) / "home"), "")
            self.assertEqual(migrate.summary(migrate.migrate(Path(folder) / "other"))[0], "")

    def test_bringing_the_history_in_is_tried_again_after_it_failed_and_the_checks_are_not(self):
        # Regression: a failure was never retried once the checks had passed; and until they had, both were.
        from lab import migrate

        with TemporaryDirectory() as folder:
            home, made, runs = Path(folder) / "home", [], []
            real = migrate.migrate

            def failing(home_, *args, **options):
                runs.append(home_)
                if len(runs) == 1:
                    raise OSError("the disk was busy")
                return real(home_, Path(folder) / "none")

            with self.checking(made, ok=True), patch.object(migrate, "migrate", failing):
                self.assertIn("tried again at the next start", setup.first_run(home))
                self.assertTrue(setup.first_run_needed(home))
                self.assertEqual(setup.first_run(home), "")
                self.assertEqual((len(runs), len(made)), (2, 4))
                self.assertFalse(setup.first_run_needed(home))
                setup.first_run(home)
                self.assertEqual(len(runs), 2)

    def test_a_damaged_note_means_a_first_run(self):
        with TemporaryDirectory() as folder:
            (Path(folder) / setup.MARKER).write_text("{ broken", encoding="utf-8")
            self.assertTrue(setup.first_run_needed(folder))


class MoveTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.old, self.new = root / "repo" / "tone_sessions" / "lab", root / "profile" / "Tone Lab"
        (self.old / "sounds").mkdir(parents=True)
        (self.old / "sounds" / "a.json").write_text('{"name": "A"}', encoding="utf-8")
        (self.old / "calibration.json").write_text('{"offset": 123}', encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_the_data_is_copied_and_a_note_left_where_it_was(self):
        self.assertTrue(setup.move_old_home(self.old, self.new))
        self.assertEqual((self.new / "sounds" / "a.json").read_text(encoding="utf-8"), '{"name": "A"}')
        self.assertTrue((self.new / "calibration.json").is_file())
        self.assertTrue((self.old / "sounds" / "a.json").is_file())          # never deleted
        self.assertIn(str(self.new), (self.old / setup.MOVED_NOTE).read_text(encoding="utf-8"))
        self.assertFalse((self.new / setup.MOVED_NOTE).exists())
        self.assertFalse(self.new.with_name("Tone Lab.copying").exists())

    def test_only_once(self):
        setup.move_old_home(self.old, self.new)
        (self.new / "sounds" / "a.json").write_text('{"name": "changed since"}', encoding="utf-8")
        self.assertFalse(setup.move_old_home(self.old, self.new))
        self.assertEqual((self.new / "sounds" / "a.json").read_text(encoding="utf-8"), '{"name": "changed since"}')

    def test_an_existing_home_is_never_overwritten(self):
        self.new.mkdir(parents=True)
        self.assertFalse(setup.move_old_home(self.old, self.new))
        self.assertEqual(list(self.new.iterdir()), [])
        self.assertFalse((self.old / setup.MOVED_NOTE).exists())

    def test_nothing_to_move(self):
        self.assertFalse(setup.move_old_home(Path(self.temporary.name) / "missing", self.new))
        self.assertFalse(self.new.exists())

    def test_home_is_in_the_profile_and_moves_the_old_data_on_first_use(self):
        root = Path(self.temporary.name)
        environment = {key: value for key, value in os.environ.items() if key != "TONE_LAB_HOME"}
        environment["LOCALAPPDATA"] = str(root / "profile")
        with patch.dict(os.environ, environment, clear=True), patch.object(knowledge, "REPO", root / "repo"):
            self.assertEqual(knowledge.home(), self.new)
            self.assertTrue((self.new / "sounds" / "a.json").is_file())
            self.assertTrue((self.old / setup.MOVED_NOTE).is_file())
            self.assertEqual(knowledge.home(), self.new)

    def test_tone_lab_home_still_wins(self):
        root = Path(self.temporary.name)
        with patch.dict(os.environ, {"TONE_LAB_HOME": str(root / "chosen"), "LOCALAPPDATA": str(root / "profile")}), \
                patch.object(knowledge, "REPO", root / "repo"):
            self.assertEqual(knowledge.home(), root / "chosen")
        self.assertFalse(self.new.exists())
        self.assertFalse((self.old / setup.MOVED_NOTE).exists())


class LauncherTest(unittest.TestCase):
    """desktop_launcher/start.pyw opens the new desk unless told to open the earlier one."""

    def setUp(self):
        import runpy

        self.launcher = runpy.run_path(str(setup.REPO / "desktop_launcher" / "start.pyw"))
        self.temporary = TemporaryDirectory()
        self.environ = {"LOCALAPPDATA": self.temporary.name}

    def tearDown(self):
        self.temporary.cleanup()

    def choose(self, arguments=(), **environ):
        return self.launcher["chosen_desk"](list(arguments), {**self.environ, **environ})

    def test_the_new_desk_by_default(self):
        self.assertEqual(self.choose(), "new")

    def test_the_command_line_wins(self):
        self.assertEqual(self.choose(["--new"], TONE_LAB_DESK="old"), "new")
        self.assertEqual(self.choose(["--old"], TONE_LAB_DESK="new"), "old")

    def test_then_the_environment(self):
        self.assertEqual(self.choose(TONE_LAB_DESK="NEW"), "new")
        self.assertEqual(self.choose(TONE_LAB_DESK="something else"), "new")

    def test_then_the_setting_in_the_data_folder(self):
        folder = Path(self.temporary.name) / "Tone Lab"
        folder.mkdir()
        (folder / "launcher.json").write_text('{"desk": "old"}', encoding="utf-8")
        self.assertEqual(self.choose(), "old")
        (folder / "launcher.json").write_text("{ broken", encoding="utf-8")
        self.assertEqual(self.choose(), "new")

    def test_the_setting_follows_tone_lab_home(self):
        chosen = Path(self.temporary.name) / "chosen"
        chosen.mkdir()
        (chosen / "launcher.json").write_text('{"desk": "old"}', encoding="utf-8")
        self.assertEqual(self.choose(TONE_LAB_HOME=str(chosen)), "old")
        self.assertFalse((Path(self.temporary.name) / "Tone Lab").exists())   # finding it creates nothing

    def test_the_new_desk_gets_the_other_arguments(self):
        from unittest.mock import Mock

        main = self.launcher["main"]
        opened = Mock()
        with patch.dict(main.__globals__, {"write_log": Mock(return_value=None), "open_new_desk": opened,
                                           "take_identity": Mock()}), \
                patch("sys.path", list(__import__("sys").path)):
            self.assertEqual(main(["--new", "--simulated"]), 0)
        opened.assert_called_once_with(["--new", "--simulated"])


if __name__ == "__main__":
    unittest.main()
