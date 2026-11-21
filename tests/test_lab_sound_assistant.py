"""The assistant's own program handling and ledger, against a stand-in for Codex. The real Codex is never run."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest import mock

from lab.sound import assistant
from lab.sound.assistant import Assistant, Cancelled

STAND_IN = r'''
import os, sys, time
mode = os.environ.get("STAND_IN_MODE", "ok")
args = sys.argv[1:]
out = args[args.index("-o") + 1]
with open(os.environ["STAND_IN_PID"], "w") as note:
    note.write(str(os.getpid()))
if mode == "deaf":                      # never reads what it is sent
    time.sleep(30)
    sys.exit(0)
asked = sys.stdin.buffer.read().decode("utf-8")
if mode == "ok":
    with open(out, "w", encoding="utf-8") as reply:
        reply.write('{"characters": %d, "last": "%s"}' % (len(asked), asked[-1]))
elif mode == "hang":
    time.sleep(30)
elif mode == "bytes":                   # an error in the console's code page, not UTF-8
    sys.stderr.buffer.write(b"warming up\nERROR: nicht angemeldet \xe9\xe8\xff\n  at somewhere\n")
    sys.exit(1)
elif mode == "list":
    with open(out, "w", encoding="utf-8") as reply:
        reply.write("[1, 2]")
elif mode == "junk":
    with open(out, "w", encoding="utf-8") as reply:
        reply.write("Sure! Here is the design: {")
'''


def running(pid):
    if os.name == "nt":
        found = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return str(pid) in found.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class StandIn(unittest.TestCase):
    """Codex stood in for by a script behind a launcher, as an npm install is: ending the launcher alone would
    leave the script running."""

    def setUp(self):
        folder = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        script = self.folder / "stand_in.py"
        script.write_text(STAND_IN, encoding="utf-8")
        if os.name == "nt":
            launcher = self.folder / "codex.cmd"
            launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            launcher = self.folder / "codex"
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            launcher.chmod(0o755)
        self.pid = self.folder / "pid.txt"
        patch = mock.patch.dict(os.environ, {"TONE_LAB_CODEX": str(launcher), "STAND_IN_PID": str(self.pid)})
        patch.start()
        self.addCleanup(patch.stop)
        self.assistant = Assistant(self.folder / "home")

    def ask(self, mode, prompt="hello", **options):
        with mock.patch.dict(os.environ, {"STAND_IN_MODE": mode}):
            return self.assistant.ask("test", prompt, {"type": "object"}, by="player", **options)

    def started(self):
        for _ in range(100):
            if self.pid.is_file() and self.pid.read_text().strip():
                return int(self.pid.read_text())
            time.sleep(.05)
        self.fail("The stand-in did not start.")

    def gone(self, pid):
        for _ in range(40):
            if not running(pid):
                return True
            time.sleep(.1)
        return False


class ProgramTest(StandIn):
    def test_the_whole_request_reaches_the_program_and_its_reply_comes_back(self):
        self.assertEqual(self.ask("ok", "x" * 200_000 + "é"), {"characters": 200_001, "last": "é"})

    def test_the_time_allowed_ends_the_program_and_what_it_started_even_one_that_never_reads(self):
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "did not answer in time"):
            self.ask("deaf", "x" * 200_000, seconds=1)
        self.assertLess(time.monotonic() - started, 12)
        self.assertTrue(self.gone(self.started()), "the program was left running")

    def test_cancel_ends_the_program_and_what_it_started(self):
        cancel = threading.Event()
        threading.Thread(target=lambda: self.started() and cancel.set(), daemon=True).start()   # once it runs
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            self.ask("hang", seconds=60, cancel=cancel)
        self.assertLess(time.monotonic() - started, 12)
        self.assertTrue(self.gone(self.started()), "the program was left running")
        entry = json.loads((self.folder / "home" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(entry["outcome"], "cancelled")

    def test_the_line_that_says_error_is_shown_whatever_its_encoding(self):
        with self.assertRaises(RuntimeError) as raised:
            self.ask("bytes")
        self.assertIn("ERROR: nicht angemeldet", str(raised.exception))
        self.assertNotIn("at somewhere", str(raised.exception))

    def test_a_reply_that_is_not_a_json_object_is_a_failure(self):
        with self.assertRaisesRegex(RuntimeError, "not a JSON object"):
            self.ask("list")
        with self.assertRaisesRegex(RuntimeError, "not JSON"):
            self.ask("junk")


class LookupTest(unittest.TestCase):
    def test_the_program_is_looked_for_once(self):
        with TemporaryDirectory() as folder:
            program = Path(folder) / "codex.exe"
            program.write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ), mock.patch.object(assistant, "_FOUND", []), \
                    mock.patch.object(assistant.shutil, "which", return_value=None), \
                    mock.patch.object(assistant, "_packaged", return_value=str(program)) as asked:
                os.environ.pop("TONE_LAB_CODEX", None)
                self.assertEqual(assistant.codex(), str(program))
                self.assertEqual(assistant.codex(), str(program))
                self.assertEqual(asked.call_count, 1)
                program.unlink()                          # uninstalled meanwhile: looked for again
                asked.return_value = None
                with self.assertRaisesRegex(RuntimeError, "Could not find Codex"):
                    assistant.codex()


class LedgerTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.ledger = self.folder / "ledger.jsonl"

    def test_a_day_is_the_players_own_and_a_call_is_counted_however_its_line_is_written(self):
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        early, late = midnight + timedelta(seconds=1), midnight - timedelta(seconds=1)
        lines = [json.dumps({"at": early.astimezone(timezone.utc).isoformat(), "kind": "round"}),     # today, as UTC
                 json.dumps({"kind": "round", "at": early.isoformat()}),                              # "at" not first
                 json.dumps({"at": late.astimezone(timezone.utc).isoformat(), "kind": "round"}),      # yesterday
                 "not a line of the ledger", json.dumps([1, 2]), json.dumps({"at": "sometime"}), ""]
        self.ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(Assistant(self.folder).spent_today(), 2)

    def test_a_call_is_written_with_the_local_time(self):
        helper = Assistant(self.folder, run=lambda prompt, schema, **options: {"ok": True})
        helper.ask("round", "p", {}, by="player")
        entry = json.loads(self.ledger.read_text(encoding="utf-8"))
        self.assertEqual(entry["at"][:10], datetime.now().astimezone().date().isoformat())
        self.assertEqual(helper.spent_today(), 1)

    def test_a_ledger_that_cannot_be_written_does_not_cost_the_reply_and_the_call_still_counts(self):
        helper = Assistant(self.folder, run=lambda prompt, schema, **options: {"ok": True})
        self.ledger.mkdir()                               # nothing can be appended to a folder
        self.assertEqual(helper.ask("round", "p", {}, by="player"), {"ok": True})
        self.assertEqual(helper.spent_today(), 1)
        self.ledger.rmdir()
        self.assertEqual(helper.ask("round", "p", {}, by="player"), {"ok": True})
        self.assertEqual(len(self.ledger.read_text(encoding="utf-8").splitlines()), 2)   # the one that waited too
        self.assertEqual(helper.spent_today(), 2)


class QuotaTest(unittest.TestCase):
    def test_two_calls_started_together_cannot_both_take_the_last_place(self):
        """(review 2026-11-20)"""
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        ledger = Path(folder.name) / "ledger.jsonl"
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ledger.write_text("".join(json.dumps({"at": today, "kind": "x"}) + "\n" for _ in range(assistant.DAILY - 1)),
                          encoding="utf-8")
        answering, gate = threading.Event(), threading.Event()

        def slow(prompt, schema, **options):
            answering.set()
            gate.wait(5)
            return {}

        asked = Assistant(folder.name, run=slow)
        results = []

        def call():
            try:
                results.append(asked.ask("round", "p", {}, by="player"))
            except assistant.Refused as error:
                results.append(error)

        first = threading.Thread(target=call)
        first.start()
        self.assertTrue(answering.wait(5))        # the first call holds the day's last place and is not back yet
        call()
        gate.set()
        first.join(5)
        self.assertEqual([type(item).__name__ for item in results], ["Refused", "dict"])
        self.assertEqual(len(ledger.read_text(encoding="utf-8").splitlines()), assistant.DAILY)


if __name__ == "__main__":
    unittest.main()
