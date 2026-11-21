"""What the Guitar Learner says of its checks and of an estimate: counts that say what they are out of, and
words that say what was measured."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import unittest

from lab.learner import report
from lab.learner.report import guess_lines, purpose, report_score, training_coverage


def recorded(pickup, wiring="Parallel", tone=10, volume=10, **more):
    return {"configuration": {"pickup": pickup, "wiring": wiring, "tone": tone, "volume": volume}, **more}


def knob(value, low, high):
    return {"value": value, "low": low, "high": high}


class ScoreLineTest(unittest.TestCase):
    def test_untaught_switch_combinations_are_separate_from_blind_accuracy(self):
        examples = [recorded(4), recorded(5), recorded(5, "Series")]
        checks = [(recorded(5), {"pickup": 5, "wiring": None}),
                  (recorded(2), {"pickup": None, "wiring": None}),
                  (recorded(3), {"pickup": None, "wiring": None})]
        result = report_score(checks, "dk24_hh_toggle", examples)
        self.assertIn("Pickup 1/1 correct", result)
        self.assertIn("Mini-toggle uncertain", result)
        self.assertNotIn("Wiring", result)
        self.assertIn("2 outside taught setups", result)

    def test_every_count_says_what_it_is_out_of(self):
        # Before: "Tone error 0.0 · in range 2/5" for a tone estimated twice and right both times.
        examples = [recorded(4), recorded(5)]
        guess = {"pickup": 5, "wiring": None, "tone": knob(10., 9., 10.), "volume": None}
        nothing = {"pickup": None, "wiring": None, "tone": None, "volume": None}
        checks = [(recorded(5, separate_session=True), guess), (recorded(5), guess), (recorded(5), nothing),
                  (recorded(4), nothing), (recorded(4), nothing)]
        parts = report_score(checks, "dk24_hh_toggle", examples).split("  ·  ")
        self.assertEqual(parts, ["5 checks on taught setups, 1 from another recording session",
                                 "Pickup 2/2 correct, 3 uncertain",
                                 "Mini-toggle uncertain",
                                 "Tone in range 2/2, mean error 0.0, 3 uncertain"])

    def test_a_volume_score_says_it_is_read_from_the_level(self):
        examples = [recorded(5, volume=4), recorded(5)]
        checks = [(recorded(5, volume=6), {"pickup": None, "wiring": None, "tone": None, "volume": knob(8., 7., 9.)})]
        self.assertIn("Volume by dry level in range 0/1, mean error 2.0", report_score(checks, "dk24_hh_toggle", examples))

    def test_coverage_exposes_fixed_knobs_and_taught_positions(self):
        result = training_coverage([recorded(5), recorded(4, tone=5)])
        self.assertIn("Taught blade positions 4, 5", result)
        self.assertIn("Tone 5–10", result)
        self.assertIn("Volume 10", result)

    def test_a_check_is_called_independent_only_from_another_recording_session(self):
        self.assertEqual(purpose(None), "Untagged")
        self.assertEqual(purpose({"role": "example"}), "Teaching example")
        self.assertEqual(purpose({"role": "test", "separate_session": True}), "Independent check · another day")
        self.assertEqual(purpose({"role": "test", "separate_session": False}), "Check · same day as teaching")
        self.assertEqual(purpose({"role": "test", "separate_session": True, "score_eligible": False}),
                         "Check · heard in training")


class EstimateWordsTest(unittest.TestCase):
    def setUp(self):
        self.examples = [recorded(5, tone=10, volume=10), recorded(4, tone=4, volume=5)]

    def lines(self, **estimate):
        guess = {"pickup": None, "wiring": None, "tone": None, "volume": None, "match": "none", "reason": "Why.",
                 "evidence": {}, "nearest": None, **estimate}
        return guess_lines(guess, "dk24_hh_toggle", self.examples)

    def test_a_match_is_said_as_how_close_it_is_never_as_strong_or_confident(self):
        for match, words in (("close", "Close match"), ("near", "Near match"), ("far", "Far match")):
            headline, details, notes = self.lines(pickup=5, wiring="Parallel", match=match)
            self.assertEqual(headline, f"{words} to the taught examples")
            self.assertEqual(details, "Pickup  5 · Neck  ·  Mini-toggle  Parallel  ·  Volume and Tone uncertain")
        headline, _, _ = self.lines(pickup=5, match="close")
        self.assertEqual(headline, "Close match · pickup")
        for text in (*report.MATCH_NAMES.values(), headline, report.VOLUME_NOTE):
            for word in ("strong", "confiden", "tentative", "good"):
                self.assertNotIn(word, text.casefold())

    def test_volume_is_said_as_read_from_the_dry_level_with_what_it_depends_on(self):
        # Before: "Strong estimate · Volume 6.5–9", for a range that picking 4 dB harder alone had moved.
        headline, details, notes = self.lines(pickup=5, wiring="Parallel", volume=knob(8., 6.5, 9.), match="close")
        self.assertIn("Volume  6.5–9 by dry level", details)
        self.assertIn(report.VOLUME_NOTE, notes)
        self.assertIn("pick as hard as in the examples", report.VOLUME_NOTE)
        headline, details, _ = self.lines(volume=knob(8., 6.5, 9.), match="close")
        self.assertEqual(headline, "Volume read from the dry level")
        _, details, notes = self.lines(pickup=5, tone=knob(7., 6., 8.), match="near")
        self.assertIn("Tone  6–8", details)
        self.assertNotIn("dry level", details)
        self.assertNotIn(report.VOLUME_NOTE, notes)

    def test_no_estimate_and_a_remembered_one(self):
        headline, details, notes = self.lines(nearest={"pickup": 4, "wiring": "Parallel"})
        self.assertEqual((headline, details), ("Insufficient evidence", ""))
        self.assertIn("Closest taught setup · 4 · Inner coils · Parallel", notes)
        guess = {"pickup": 5, "wiring": None, "tone": None, "volume": None, "match": "close", "reason": "Why."}
        self.assertEqual(guess_lines(guess, "dk24_hh_toggle", self.examples, stale=True)[0], "Last estimate")
        label = recorded(2)
        self.assertIn("outside taught setups", guess_lines(guess, "dk24_hh_toggle", self.examples, label)[2][0])


if __name__ == "__main__":
    unittest.main()
