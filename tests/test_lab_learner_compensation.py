import unittest

import numpy as np
from scipy import signal

from lab import audio
from lab.learner import compensation as guitar_compensation


class GuitarCompensationTest(unittest.TestCase):
    def test_compensation_improves_a_held_out_bright_shelf_fixture(self):
        rng = np.random.default_rng(19)
        source = rng.normal(0, .04, (2 * audio.RATE, 1))
        b, a = guitar_compensation.high_shelf(1400, -5)
        dark = signal.lfilter(b, a, source, axis=0)
        result, fit = guitar_compensation.compare(source, dark)
        self.assertTrue(fit["enabled"])
        self.assertEqual(result.shape, source.shape)
        self.assertLess(fit["held_out_error_after_db"], .03)
        self.assertGreater(fit["held_out_error_before_db"], 1)

    def test_no_shelf_for_gain_difference_alone(self):
        source = np.random.default_rng(8).normal(0, .04, (2 * audio.RATE, 1))
        _, fit = guitar_compensation.compare(source, .4 * source)
        self.assertFalse(fit["enabled"])


if __name__ == "__main__":
    unittest.main()
