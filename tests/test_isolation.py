"""The tests cannot reach the player's things: the real unit, its audio driver, Codex, the data folder, the screen
or the keyboard.

Two kinds of check. The guards themselves are tried: with `isolated` imported, each door to the real world
refuses, in this process and in the engine's own. And the test files are read, to see that each one asks for
the guards before it imports the app.

What reading the files cannot see:

- an import made at run time by name (`import_module`), or a module of the app reached only through a helper;
- a window made by app code that a test reaches through more than one import (a test file is asked for
  `hidden_tk` when it imports tkinter or a module of the app that does);
- a command line whose words are put together at run time. Only calls of `run(...)`, `result(...)` and
  `main([...])` with the command written out are read.

For those the guards are what holds: a test that gets to the real unit, driver or Codex fails with a message
saying so, and `hidden_tk`, once any test module has imported it, covers every window of the run.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402  (keeps every window off the screen and gives the keyboard back)

import ast  # noqa: E402
from functools import lru_cache  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402
import tempfile  # noqa: E402
from tempfile import TemporaryDirectory  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from lab import cli, knowledge  # noqa: E402
from lab.hearing import audio  # noqa: E402
from lab.qc import device  # noqa: E402
from lab.qc.host import Host  # noqa: E402
from lab.sound.assistant import Assistant  # noqa: E402

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent

# Still to be done (part 2 of the review's fixes): the test files of the desk, the studio, the command line, the
# side tools and the Guitar Learner were being worked on by someone else when the guards were added, so they
# are let off here. Part 2 puts them right and empties this pattern; from then on no file is let off.
NOT_YET = re.compile(r"test_lab_(studio|desk|cli|tools_|learner_)")


def still_to_do(names):
    return {name for name in names if not NOT_YET.match(name)}


# Commands that take --simulated but only read the data folder, which is the tests' own.
ONLY_READ_THE_DATA_FOLDER = {"latest-clip", "presets"}


# ---- reading the files ----

@lru_cache(maxsize=None)
def text_of(path):
    return Path(path).read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def parsed(path):
    return ast.parse(text_of(path), str(path))


def named(node):
    """The modules an import statement names: `import a.b` gives a.b; `from a import b` gives a and a.b."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and not node.level:
        return [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
    return []


def at_the_top(tree):
    """(line, module) of every import a file makes while it is being imported, in order."""
    return [(node.lineno, name) for node in tree.body for name in named(node)]


def anywhere(tree):
    return {name for node in ast.walk(tree) for name in named(node)}


def of(name, *roots):
    return any(name == root or name.startswith(root + ".") for root in roots)


def helper(name):
    """A module of the tests themselves that brings the app in."""
    return name == "support" or name.startswith("test_")


def first(imports, wanted):
    return next((line for line, name in imports if wanted(name)), None)


def window_modules():
    """The app's modules that import tkinter."""
    found = set()
    for root in ("lab", "tone_lab"):
        for path in (REPO / root).rglob("*.py"):
            if "tkinter" in text_of(path) and any(of(name, "tkinter") for name in anywhere(parsed(path))):
                dotted = ".".join(path.relative_to(REPO).with_suffix("").parts)
                found.add(dotted[:-len(".__init__")] if dotted.endswith(".__init__") else dotted)
    return found


def not_isolated():
    """Test modules of the new app, and the helpers they share, that import the app before `isolated`."""
    short = set()
    for path in [*TESTS.glob("test_lab_*.py"), TESTS / "support.py"]:
        imports = at_the_top(parsed(path))
        guard = first(imports, lambda name: name == "isolated")
        app = first(imports, lambda name: of(name, "lab") or helper(name))
        if guard is None or (app is not None and app < guard):
            short.add(path.name)
    return short


def not_hidden():
    """Test files that import tkinter, or a module of the app that does, before `hidden_tk` or without it."""
    windows = window_modules()
    short = set()
    for path in TESTS.glob("*.py"):
        if path.name == "hidden_tk.py":
            continue
        tree = parsed(path)
        if not any(of(name, "tkinter") or name in windows for name in anywhere(tree)):
            continue
        imports = at_the_top(tree)
        guard = first(imports, lambda name: name == "hidden_tk")
        risk = first(imports, lambda name: of(name, "tkinter", "lab", "tone_lab") or helper(name))
        if guard is None or (risk is not None and risk < guard):
            short.add(path.name)
    return short


def unit_commands():
    """The commands that open the unit: those that take --simulated, less the ones that only read the data folder."""
    subparsers = next(action for action in cli.parser()._actions if getattr(action, "choices", None)
                      and isinstance(action.choices, dict))
    return {name for name, parser in subparsers.choices.items()
            if "--simulated" in parser._option_string_actions} - ONLY_READ_THE_DATA_FOLDER


def command_lines(tree):
    """(line, words written out) of every command line a file runs: `run("x", ...)`, `result("x", ...)`,
    `main(["x", ...])`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        words = node.args
        if called == "main" and words and isinstance(words[0], (ast.List, ast.Tuple)):
            words = words[0].elts
        elif called not in ("run", "result"):
            continue
        yield node.lineno, [word.value for word in words if isinstance(word, ast.Constant)
                            and isinstance(word.value, str)]


def not_simulated():
    """{file: lines} of command lines that name a command that opens the unit without --simulated."""
    commands = unit_commands()
    found = {}
    for path in TESTS.glob("test_*.py"):
        lines = [line for line, words in command_lines(parsed(path))
                 if words and words[0] in commands and "--simulated" not in words]
        if lines:
            found[path.name] = lines
    return found


class FilesTest(unittest.TestCase):
    def test_every_test_module_of_the_app_imports_isolated_before_the_app(self):
        self.assertEqual(still_to_do(not_isolated()), set())

    def test_every_test_file_that_can_make_a_window_imports_hidden_tk_first(self):
        self.assertEqual(still_to_do(not_hidden()), set())

    def test_no_command_that_opens_the_unit_is_run_without_simulated(self):
        self.assertIn("reamp", unit_commands())                 # the reading of the parser still works
        self.assertNotIn("pod", unit_commands())
        found = not_simulated()
        self.assertEqual(still_to_do(found), set(), found)

    def test_the_reading_catches_what_it_is_for(self):
        tree = ast.parse("import unittest\nfrom lab.qc import sim\nimport isolated\n")
        imports = at_the_top(tree)
        self.assertLess(first(imports, lambda name: of(name, "lab")), first(imports, lambda name: name == "isolated"))
        lines = list(command_lines(ast.parse(
            "run('reamp', dry, '--slot', '2F')\nresult('reamp', 'x.wav', '--simulated')\n"
            "cli.main(['inspect'])\nrun(*words)\n")))
        self.assertEqual(lines, [(1, ["reamp", "--slot", "2F"]), (2, ["reamp", "x.wav", "--simulated"]),
                                 (3, ["inspect"]), (4, [])])


# ---- trying the guards ----

class GuardsTest(unittest.TestCase):
    def test_the_data_folder_is_the_tests_own_and_goes_when_they_end(self):
        self.assertEqual(knowledge.home(), isolated.HOME)
        self.assertTrue(isolated.HOME.is_relative_to(Path(tempfile.gettempdir()).resolve())
                        or isolated.HOME.is_relative_to(tempfile.gettempdir()))
        self.assertEqual(knowledge.device_path(), isolated.HOME / "device-presets.json")
        self.assertEqual(knowledge.table("device"), {})         # not the player's, copied in from the program

    def test_the_real_unit_is_not_opened(self):
        with self.assertRaisesRegex(RuntimeError, "real Quad Cortex"):
            device._library(True)
        with self.assertRaisesRegex(RuntimeError, "real Quad Cortex"):
            with device.connect(write=True, editor=lambda: False):
                self.fail("the real unit was opened")

    def test_the_real_audio_driver_is_not_opened(self):
        with self.assertRaisesRegex(RuntimeError, "audio driver"):
            audio.Audio()

    def test_the_real_codex_is_not_run_and_a_stand_in_is(self):
        with mock.patch.dict(os.environ), mock.patch("subprocess.Popen") as started:
            os.environ.pop("TONE_LAB_CODEX", None)
            with TemporaryDirectory() as folder, self.assertRaisesRegex(RuntimeError, "real Codex"):
                Assistant(folder).ask("round", "a request", {}, by="player")
            started.assert_not_called()
        # With a stand-in named, the same call goes as far as starting it (tests/test_lab_sound_assistant.py).

    def test_an_engine_asked_for_the_real_unit_does_not_get_it_on_a_thread_or_in_its_own_process(self):
        for local in (True, False):
            with TemporaryDirectory() as folder:
                host = Host(Path(folder), simulated=False, local=local)
                with self.assertRaises(RuntimeError) as refused:
                    host.ready.result(60)
                host.close(timeout=5)
            # Cortex Control, if it is open on this computer, is seen first and refuses as well.
            self.assertRegex(str(refused.exception), "A test tried to open the real Quad Cortex|Cortex Control")

    def test_a_window_made_in_a_test_does_not_keep_the_keyboard(self):
        import tkinter as tk

        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"No display: {error}")
        self.addCleanup(root.destroy)
        window = tk.Toplevel(root)
        window.deiconify()
        root.update()
        self.assertEqual(float(root.attributes("-alpha")), 0.)
        self.assertTrue(window.winfo_ismapped())
        self.assertFalse(hidden_tk.foreground_is_ours())


if __name__ == "__main__":
    unittest.main()
