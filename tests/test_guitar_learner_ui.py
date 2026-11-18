import unittest

from tone_lab.guitar_learner_ui import report_score, training_coverage


def recorded(pickup, wiring="Parallel", tone=10, volume=10):
    return {"configuration": {"pickup": pickup, "wiring": wiring, "tone": tone, "volume": volume}}


class GuitarLearnerReportTest(unittest.TestCase):
    def test_untaught_switch_combinations_are_separate_from_blind_accuracy(self):
        examples = [recorded(4), recorded(5), recorded(5, "Series")]
        checks = [(recorded(5), {"pickup": 5, "wiring": None}),
                  (recorded(2), {"pickup": None, "wiring": None}),
                  (recorded(3), {"pickup": None, "wiring": None})]
        result = report_score(checks, "dk24_hh_toggle", examples)
        self.assertIn("Pickup 1/1 correct", result)
        self.assertIn("Wiring uncertain", result)
        self.assertIn("2 outside taught setups", result)

    def test_coverage_exposes_fixed_knobs_and_taught_positions(self):
        result = training_coverage([recorded(5), recorded(4, tone=5)])
        self.assertIn("Taught blade positions 4, 5", result)
        self.assertIn("Tone 5–10", result)
        self.assertIn("Volume 10", result)
