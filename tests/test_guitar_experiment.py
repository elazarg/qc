import unittest

import numpy as np
from scipy import signal

from tone_lab import audio, guitar_compensation, guitar_runtime


class GuitarExperimentTest(unittest.TestCase):
    def test_window_preserves_sample_order_and_drops_discontinuous_history(self):
        window = guitar_runtime.DryWindow(5)
        window.append(np.array([1., 2., 3.]), 1.)
        self.assertIsNone(window.latest(0))
        window.append(np.array([4., 5., 6.]), 2.)
        generation, timestamp, sample = window.latest(0)
        np.testing.assert_equal(sample, [2, 3, 4, 5, 6])
        self.assertEqual(timestamp, 2.)
        self.assertIsNone(window.latest(generation))
        window.append(np.array([7.]), 6., gap=True)
        self.assertIsNone(window.latest(generation))
        window.append(np.array([8., 9., 10., 11.]), 7.)
        np.testing.assert_equal(window.latest(generation)[2], [7, 8, 9, 10, 11])

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
