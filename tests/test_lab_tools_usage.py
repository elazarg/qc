"""Usage and feedback: the ledger day by day, tool opens, notes kept with the desk, and the unit's share."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from lab.sound.assistant import DAILY, Assistant
from lab.sound.sounds import Sound
from lab.tools import usage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lab_tools_support import PLEXI, V30, chain, host  # noqa: E402


def ledger(folder, rows):
    with (Path(folder) / "ledger.jsonl").open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row) + "\n")


class LedgerTest(unittest.TestCase):
    def test_calls_are_counted_by_day_by_whom_and_by_how_they_ended(self):
        with TemporaryDirectory() as folder:
            ledger(folder, [
                {"at": "2026-11-18T10:00:00+00:00", "kind": "round", "by": "player", "web": False, "characters": 1200,
                 "seconds": 30.5, "outcome": "ok"},
                {"at": "2026-11-19T09:00:00+00:00", "kind": "round", "by": "player", "web": True, "characters": 2000,
                 "seconds": 40., "outcome": "ok"},
                {"at": "2026-11-19T09:05:00+00:00", "kind": "revise", "by": "app", "web": False, "characters": 800,
                 "seconds": 3., "outcome": "failed: timeout"},
                {"at": "2026-11-19T09:06:00+00:00", "kind": "revise", "by": "player", "web": False,
                 "characters": 900, "seconds": 1., "outcome": "cancelled"},
            ])
            with (Path(folder) / "ledger.jsonl").open("a", encoding="utf-8") as file:
                file.write("not json\n")
            days = usage.daily(folder, today="2026-11-19")
            self.assertEqual([day["day"] for day in days], ["2026-11-19", "2026-11-18"])
            today = days[0]
            self.assertEqual((today["calls"], today["player"], today["app"], today["web"]), (3, 2, 1, 1))
            self.assertEqual(today["kinds"], {"round": 1, "revise": 2})
            self.assertEqual((today["ok"], today["failed"], today["cancelled"]), (1, 1, 1))
            self.assertEqual(today["left"], DAILY - 3)
            self.assertIsNone(days[1]["left"])
            self.assertEqual(today["seconds"], 44.)
            self.assertEqual(today["characters"], 3700)
            self.assertIn(f"3 of {DAILY}", usage.today_line(folder, today="2026-11-19"))

    def test_the_ledger_the_assistant_writes_is_the_one_read(self):
        with TemporaryDirectory() as folder:
            assistant = Assistant(folder, run=lambda prompt, schema, **_: {"ok": True})
            assistant.ask("round", "a request", {}, by="player")
            days = usage.daily(folder)
            self.assertEqual(days[0]["calls"], 1)
            self.assertEqual(days[0]["kinds"], {"round": 1})


class UsageAndFeedbackTest(unittest.TestCase):
    def test_opens_are_counted_and_views_open_together_are_paired(self):
        with TemporaryDirectory() as folder:
            usage.record(folder, "open", "Levels")
            usage.record(folder, "open", "Levels")
            usage.record(folder, "close", "Levels")
            usage.record(folder, "open", "Library", open=["Library", "Levels"], live=True)
            self.assertEqual(usage.opens(folder), {"Levels": 2, "Library": 1})

    def test_a_note_is_kept_with_what_was_on_the_desk(self):
        a, b = Sound("Clean", chain(PLEXI, V30), {"kind": "designed"}), Sound("Lead", chain(PLEXI), {})
        studio = SimpleNamespace(state=SimpleNamespace(a=a.id, b=b.id, round="r1", song="Time", instrument="Charvel"),
                                 sound=lambda side: {"a": a, "b": b}[side], phrase=("phrase-1", None))
        with TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                usage.save_feedback(folder, "  ")
            usage.save_feedback(folder, "B is too fizzy", usage.situation(studio))
            notes = usage.feedback_notes(folder)
            self.assertEqual(notes[0]["text"], "B is too fizzy")
            self.assertEqual(notes[0]["sounds"]["b"]["name"], "Lead")
            self.assertEqual(notes[0]["phrase"], "phrase-1")
            self.assertEqual(notes[0]["song"], "Time")


class OnTheUnitTest(unittest.TestCase):
    def test_what_the_app_keeps_on_the_unit(self):
        with TemporaryDirectory() as folder:
            unit = host(folder)
            try:
                state = unit.call("lab.tools.probe.unit_state").result(10)
            finally:
                unit.close()
        self.assertEqual(state["firmware"], "4.1.1")
        self.assertEqual(state["cached"], 0)
        self.assertEqual(state["cache_slots"], 32)
        # The one answer about the unit, for this window, the sound check and `doctor --unit`.
        self.assertEqual((state["tone_lab"], state["problem"], state["calibrated"]), (True, None, False))
        self.assertEqual(state["preset"], "Brit 2203")
        self.assertIsInstance(state["dry_channel"], int)
        self.assertFalse(hasattr(usage, "unit_state"))


if __name__ == "__main__":
    unittest.main()
