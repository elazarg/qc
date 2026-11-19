"""Rounds of designs on the desk: stepping, what each design carries, listening when free, the next three."""

from concurrent.futures import Future
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_rounds import Ear, reply  # noqa: E402
from tone_lab import advisor, rounds, session  # noqa: E402
from tone_lab.suggestions import RoundStrip, Suggestions  # noqa: E402


class Desk:
    """As much of the listening desk as the rounds touch."""

    def __init__(self):
        self.drafts, self.shapes = {}, {"a": {}, "b": {}}
        self.tone_b = self.draft = self.job = self.task = self.thinking = self.leaving = self.playing = None
        self.stop_signal = None
        self.takes, self.live = ["a take"], None
        self.said, self.timers, self.submitted, self.asked_for = [], [], [], []
        self.root = SimpleNamespace(after=lambda ms, action: self.timers.append((ms, action)) or len(self.timers),
                                    after_cancel=lambda timer: None, after_idle=lambda action: action())
        self.advisor = SimpleNamespace(submit=self.ask_assistant)
        self.tray = SimpleNamespace(open=lambda name: None)

    def ask_assistant(self, work, *args):
        self.asked_for.append((work, args))
        return Future()

    def set_status(self, text, *, kind="info", actions=(), hint=""):
        self.said.append((text, dict(actions)))

    def set_tone(self, tone, *, shape=None, label=None):
        self.tone_b, self.shapes["b"] = tone, dict(shape or {})

    def set_shape(self, values, *, side="b", checkpoint=True):
        self.shapes[side] = dict(values)

    def submit(self, task, message, work, *args, done):
        self.job, self.task = object(), task
        self.submitted.append((task, work, args, done))

    def finish(self, outcome):
        """The job on the unit has ended."""
        _, _, _, done = self.submitted[-1]
        self.job = self.task = self.stop_signal = None
        done(outcome)

    def task_if_active(self, name):
        return self.job if self.job is not None and self.task == name else None

    def on_air(self):
        return self.live

    def undo_last(self):
        pass

    def focus_request(self):
        pass

    def update(self):
        pass

    def remember(self):
        pass


class SuggestionsTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for target, value in ((session, Path(folder.name)),):
            home = patch.object(target, "SESSIONS", value)
            home.start()
            self.addCleanup(home.stop)
        asked = patch.object(advisor, "request", return_value=reply())
        self.request = asked.start()
        self.addCleanup(asked.stop)
        self.desk = Desk()
        self.suggestions = Suggestions(self.desk)
        self.found = rounds.start("the solo of Time", "Charvel")

    def arrive(self):
        self.suggestions.arrived(self.found, True)
        return self.found["candidates"]

    def test_the_first_design_goes_on_b_at_once_and_the_aim_is_said(self):
        first, *_ = self.arrive()
        self.assertEqual(self.desk.tone_b.draft_id, first["draft"])
        self.assertEqual((self.suggestions.shown, self.suggestions.place()), (self.found["id"], 0))
        text, actions = self.desk.said[-1]
        self.assertEqual(text, "Aiming for saturated, bright, ambient. 1 of 3 is on B · By the book")
        self.assertIn("Undo", actions)
        self.assertEqual(len(self.desk.drafts), 3)
        # The unit is left alone for a while, in case the player wants to play straight away.
        self.assertEqual(self.desk.timers[-1][0], Suggestions.AFTER_ARRIVAL)
        self.assertEqual(self.desk.submitted, [])

    def test_a_round_that_arrives_after_b_changed_is_offered_and_not_applied(self):
        self.suggestions.arrived(self.found, False)
        self.assertIsNone(self.desk.tone_b)
        text, actions = self.desk.said[-1]
        self.assertIn("3 designs are ready, aiming for saturated, bright, ambient", text)
        actions["Show them"]()
        self.assertEqual(self.suggestions.place(), 0)

    def test_each_design_keeps_the_knobs_it_was_left_with_and_knows_it_was_heard(self):
        first, second, _ = self.arrive()
        self.desk.shapes["b"] = {"drive": .7}
        self.desk.live = "b"
        self.suggestions.step(1)
        self.assertEqual((first["shape"], first["played"]), ({"drive": .7}, True))
        self.assertEqual((self.desk.tone_b.draft_id, self.desk.shapes["b"]), (second["draft"], {}))
        self.desk.live = None
        self.desk.shapes["b"] = {"warmth": -.3}
        self.suggestions.step(0)
        self.assertEqual((second["shape"], second["played"]), ({"warmth": -.3}, False))
        self.assertEqual(self.desk.shapes["b"], {"drive": .7})
        self.assertEqual(rounds.load(self.found["id"])["candidates"][0]["shape"], {"drive": .7})
        self.suggestions.step(0)  # Already there: nothing happens.
        self.suggestions.step(7)

    def test_the_app_listens_when_the_unit_is_free_and_its_knobs_go_only_to_designs_nobody_heard(self):
        first, second, third = self.arrive()
        self.desk.live = "b"
        self.desk.shapes["b"] = {"drive": .3}  # The player is on the first design and has turned a knob.
        self.suggestions.sync()
        self.desk.live = None
        self.suggestions.idle()
        task, work, (copy, stop), _ = self.desk.submitted[-1]
        self.assertEqual(task, "listen")
        self.assertIs(self.desk.stop_signal, stop)
        self.assertIsNot(copy, self.found)  # The worker has its own copy. The desk's stays the player's.
        checked = rounds.check(copy, hear=Ear(), reorder=False)
        self.desk.finish((checked, None))
        # The one the player heard keeps their knob. The others take the knobs the app found for them.
        self.assertEqual(first["shape"], {"drive": .3})
        self.assertEqual(first["tags"][0], "crunch")
        self.assertEqual(second["shape"], {"space": .4})
        self.assertEqual(second["tags"], ["saturated", "bright", "ambient", "on the aim"])
        self.assertIn("Listened to all 3. Closest to the aim: 2 · Measured (on the aim).", self.desk.said[-1][0])
        # Something missed, so the next three are prepared once, without being asked for.
        self.assertEqual([work for work, _ in self.desk.asked_for], [rounds.again])
        self.assertTrue(self.found["followed"])
        self.suggestions.consider()
        self.assertEqual(len(self.desk.asked_for), 1)

    def test_the_knobs_the_app_finds_for_the_design_on_b_are_applied_if_nobody_has_played_it(self):
        self.arrive()
        self.suggestions.idle()
        checked = rounds.check(self.desk.submitted[-1][2][0], hear=Ear(), reorder=False)
        self.desk.finish((checked, None))
        self.assertEqual(self.desk.shapes["b"], {"drive": 1., "warmth": -.8})

    def test_the_player_gets_the_unit_back_and_what_they_asked_for_happens_next(self):
        self.arrive()
        self.suggestions.idle()
        stop = self.desk.submitted[-1][2][1]
        done = []
        self.suggestions.interrupt(lambda: done.append("guitar"))
        self.assertTrue(stop.is_set())
        self.assertEqual(self.desk.said[-1][0], "Giving the Quad Cortex back…")
        self.desk.finish((self.desk.submitted[-1][2][0], None))
        self.assertEqual(done, ["guitar"])
        self.assertEqual(self.desk.asked_for, [])  # Nothing is prepared behind an interrupted check.
        # Stepping while the app listens does the same.
        self.suggestions.idle()
        self.suggestions.step(2)
        self.desk.finish((self.desk.submitted[-1][2][0], None))
        self.assertEqual(self.suggestions.place(), 2)

    def test_nothing_is_listened_to_while_the_unit_or_the_assistant_is_busy(self):
        self.arrive()
        for change in ({"job": object()}, {"thinking": object()}, {"takes": []}, {"leaving": "exit"}):
            desk = self.desk
            before = {key: getattr(desk, key) for key in change}
            vars(desk).update(change)
            self.suggestions.idle()
            self.assertEqual(desk.submitted, [], change)
            vars(desk).update(before)

    def test_a_failed_listen_is_said_once_and_not_retried(self):
        self.arrive()
        self.suggestions.idle()
        self.desk.finish((self.found, RuntimeError("The draft returned silence.")))
        self.assertIn("could not be listened to. The draft returned silence.", self.desk.said[-1][0])
        self.assertEqual(len(self.desk.timers), 1)

    def test_a_review_and_marks_stay_with_the_design_and_ask_for_the_next_three(self):
        candidates = self.arrive()
        for candidate in candidates:  # As if listened to, and all on the aim: nothing to learn from yet.
            candidate.update(heard={}, served={}, tags=[], miss=0)
        self.suggestions.consider()
        self.assertEqual(self.desk.asked_for, [])
        self.suggestions.review(1)
        self.assertEqual(self.suggestions.reviewing, (self.found["id"], 1))
        self.assertIn("Say what you think of 2 · Measured", self.desk.said[-1][0])
        self.suggestions.reviewed("right fuzz, too dark")
        self.assertEqual(candidates[1]["review"], "right fuzz, too dark")
        self.assertIsNone(self.suggestions.reviewing)
        self.assertEqual(len(self.desk.asked_for), 1)  # Now there is something to learn from.
        work, (told,) = self.desk.asked_for[0]
        self.assertEqual(told["candidates"][1]["review"], "right fuzz, too dark")
        self.suggestions.mark(0, "Not it")
        self.suggestions.mark(0, "Closer")
        self.suggestions.mark(0, "Not it")
        self.assertEqual(candidates[0]["user_tags"], ["Closer"])
        self.suggestions.more()
        self.assertEqual(self.desk.said[-1][0], "Three more are already being prepared.")
        self.suggestions.review(0)
        self.suggestions.cancel_review()
        self.assertIsNone(self.suggestions.reviewing)

    def test_the_next_three_are_offered_when_they_arrive_and_earlier_rounds_are_a_step_back(self):
        self.arrive()
        self.suggestions.more()
        second = rounds.start("the solo of Time", "Charvel", parent=self.found["id"])
        self.suggestions.pending.set_result(second)
        self.suggestions.poll()
        text, actions = self.desk.said[-1]
        self.assertEqual(text, "Three more are ready, aiming for saturated, bright, ambient.")
        self.assertEqual(self.suggestions.shown, self.found["id"])  # Nothing moves until the player asks.
        actions["Show them"]()
        self.assertEqual((self.suggestions.shown, self.suggestions.place()), (second["id"], 0))
        self.suggestions.browse(-1)
        self.assertEqual((self.suggestions.shown, self.suggestions.place()), (self.found["id"], None))
        self.assertIn("Round 1 of 2", self.desk.said[-1][0])
        self.suggestions.browse(-1)
        self.suggestions.browse(5)
        self.assertEqual(self.suggestions.shown, second["id"])
        # A fresh desk finds the rounds on disk and returns to the one that was on show.
        again = Suggestions(Desk())
        again.restore(second["id"])
        self.assertEqual((len(again.rounds), again.shown), (2, second["id"]))
        again.restore("gone")
        self.assertEqual(again.shown, second["id"])

    def test_a_round_that_could_not_be_prepared_says_so(self):
        self.arrive()
        self.suggestions.more()
        self.suggestions.pending.set_exception(RuntimeError("The AI reply timed out."))
        with self.assertLogs("tone_lab", level="ERROR"):
            self.suggestions.poll()
        self.assertIn("Three more could not be prepared. The AI reply timed out.", self.desk.said[-1][0])
        self.assertIsNone(self.suggestions.pending)

    def test_one_line_says_what_a_design_is_what_was_heard_and_what_the_player_said(self):
        candidate = {"differs": "A pedal into a Plexi.", "served": None, "tags": [], "user_tags": [], "review": ""}
        self.assertEqual(RoundStrip.about(self.found, candidate), "A pedal into a Plexi.  ·  not listened to yet")
        candidate.update(served={}, tags=["saturated", "bright", "dry", "drier than the aim"], user_tags=["Closer"],
                         review="more echo")
        self.assertEqual(RoundStrip.about(self.found, candidate),
                         "A pedal into a Plexi.  ·  heard: saturated, bright, dry, drier than the aim  ·  "
                         "you: Closer, “more echo”")


if __name__ == "__main__":
    unittest.main()
