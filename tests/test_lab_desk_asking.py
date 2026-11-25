"""Asking on the desk (docs/UX_REVIEW.md, section 2): what the ask line says while empty, what is shown while a
request is out, and the next remedy of a remark offered as something to click. On the real desk over a
simulated unit, kept off the screen."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import threading
import unittest

from lab.sound.sounds import Sound

from support import GREENBACK, PLEXI, V30, DeskCase, chain


class AskLineTest(DeskCase):
    def test_the_empty_line_says_what_it_is_for_and_the_words_go_while_something_is_typed(self):
        from lab.desk.window import PLACEHOLDER

        desk = self.desk
        self.assertEqual(desk.placeholder.cget("text"), PLACEHOLDER)
        self.assertEqual(desk.placeholder.winfo_manager(), "place")
        desk.request.insert(0, "a bit")
        desk.show_placeholder()
        self.assertEqual(desk.placeholder.winfo_manager(), "")
        desk.request.delete(0, "end")
        desk.tick()
        self.assertEqual(desk.placeholder.winfo_manager(), "place")

    def test_a_request_sent_to_the_assistant_clears_the_line_and_the_working_line_names_it(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.put("b", Sound("Rhythm", chain(PLEXI, V30), {"kind": "designed"}))
        self.studio.say("Ready.")
        self.studio.assistant.run = lambda *args, **options: (gate.wait(20), {})[1]
        self.desk.request.insert(0, "more sustain from the amp, without more mud")
        self.desk.ask()
        self.root.update()
        self.assertEqual(self.desk.line.cget("text"), "")               # not "Ready." for the minute it takes
        self.assertEqual(self.desk.request.get(), "")
        self.assertIn("Revising B · “more sustain from the amp, without more mud”", self.desk.working.cget("text"))
        self.studio.cancel()


class NextRemedyTest(DeskCase):
    def test_after_a_remark_the_next_remedy_is_offered_and_a_click_tries_it(self):
        self.put("b", Sound("Rhythm", chain(PLEXI, GREENBACK), {"kind": "designed"}))
        self.studio.ask("muddy")
        self.root.update()
        first = dict(self.studio.sound("b").knobs)
        link = self.desk.next_link
        self.assertTrue(link.winfo_manager())
        self.assertTrue(link.cget("text").startswith("Next: "))
        self.assertNotIn("Say it again", self.desk.line.cget("text"))
        link.event_generate("<ButtonRelease-1>")
        self.root.update()
        self.assertNotEqual(self.studio.sound("b").knobs, first)
        self.studio.turn({})                    # the knobs moved by hand: the remark is not the last thing done
        self.desk.show_working()
        self.assertEqual(self.desk.next_link.winfo_manager(), "")


class UsageTest(DeskCase):
    def test_what_is_used_on_the_desk_is_noted_in_the_usage_log(self):
        import json

        self.put("b", Sound("Rhythm", chain(PLEXI, GREENBACK), {"kind": "designed"}))
        self.desk.toggle("Shape")
        self.studio.ask("muddy")
        self.desk.pause_or_play()
        self.desk.pause_or_play()
        self.studio.undo()
        self.desk.toggle("Chain")
        rows = [json.loads(line) for line in (self.home / "ui-usage.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(row["event"], row["view"]) for row in rows],
                         [("open", "Shape"), ("ask", "remark"), ("pause", "guitar"), ("play", "guitar"),
                          ("undo", "Tightness on B"), ("close", "Shape"), ("open", "Chain")])
        self.assertEqual(rows[3]["side"], "b")


if __name__ == "__main__":
    unittest.main()
