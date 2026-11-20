"""Test signals: what a chain is, measured directly, on chains whose answer is known."""

import unittest

import numpy as np
from numpy.polynomial.chebyshev import chebval
from scipy import signal

from lab.hearing.audio import RATE
from lab.hearing.probe import analyse, held_note_peak, test_signal

TONES = test_signal(.25)[:, 0].astype(np.float64)


def through(chain):
    return analyse(chain(TONES)[:, None])


def delay(sound, seconds=.35, gain=.5):
    late = round(seconds * RATE)
    out = sound.copy()
    out[late:] += gain * sound[:-late]
    return out


def reverb(sound, seconds=1.4):
    length = round(1.6 * RATE)
    response = np.random.default_rng(1).standard_normal(length) * np.exp(-np.arange(length) / RATE * 6.9 / seconds)
    response[0] = 3
    return signal.fftconvolve(sound, response)[:len(sound)] / 20


class DistortionTest(unittest.TestCase):
    def test_linear_chains_add_no_harmonics(self):
        cab = lambda s: signal.sosfilt(signal.butter(4, 5000, fs=RATE, output="sos"), s)
        for chain in (lambda s: s * .3, cab, delay, reverb):
            found = through(chain)
            self.assertLessEqual(found.distortion_db, -55)
            self.assertLessEqual(found.intermod_db, -40)

    def test_more_clipping_more_harmonics_and_softer_playing_cleans_up(self):
        levels = [through(lambda s, g=g: np.tanh(g * s)).distortion_db for g in (2, 10, 40)]
        self.assertEqual(levels, sorted(levels))
        soft_clip = through(lambda s: np.tanh(10 * s))
        self.assertLess(soft_clip.distortion_soft_db, soft_clip.distortion_db - 10)
        self.assertGreater(through(lambda s: np.clip(30 * s, -.5, .5)).distortion_db, -10)

    def test_symmetric_clipping_is_odd_and_lopsided_clipping_adds_even(self):
        self.assertLess(through(lambda s: np.tanh(8 * s)).even_odd_db, -40)
        self.assertGreater(through(lambda s: np.tanh(8 * s + .3) - np.tanh(.3)).even_odd_db, -10)

    def test_a_gate_is_caught_by_the_softer_tone(self):
        follow = lambda s: signal.sosfilt(signal.butter(1, 20, fs=RATE, output="sos"), np.abs(s))
        self.assertTrue(through(lambda s: np.where(follow(s) > .05, s, 0)).gated)
        self.assertFalse(through(lambda s: s).gated)


def adding(harmonics):
    """A chain that adds exactly the given harmonics {n: dB against the tone} to the held tone (peak .25): the n-th
    Chebyshev polynomial of sin turns it into a pure n-th harmonic of the same peak."""
    return lambda s: s + sum(10 ** (level / 20) * .25 * chebval(s / .25, [0] * n + [1]) for n, level in harmonics.items())


class HarmonicSpreadTest(unittest.TestCase):
    def test_a_pure_tone_has_no_spread(self):
        found = through(lambda s: s * .5)
        self.assertEqual(len(found.harmonics_db), 9)
        self.assertLessEqual(max(found.harmonics_db), -60)
        self.assertLessEqual(found.weighted_distortion_db, -55)
        self.assertAlmostEqual(found.harmonic_centroid, 1, places=3)
        self.assertLess(found.tristimulus3, 1e-5)
        self.assertGreater(found.hnr_db, 80)

    def test_known_harmonics_are_measured_order_by_order(self):
        found = through(adding({3: -20, 7: -30}))
        self.assertAlmostEqual(found.harmonics_db[3 - 2], -20, delta=.3)
        self.assertAlmostEqual(found.harmonics_db[7 - 2], -30, delta=.3)
        others = [level for n, level in enumerate(found.harmonics_db, start=2) if n not in (3, 7)]
        self.assertLessEqual(max(others), -60)
        self.assertAlmostEqual(found.low_order_db, -20, delta=.3)
        self.assertAlmostEqual(found.high_order_db, -30, delta=.3)
        # Shorter's weighting: .01 * 9/4 + .001 * 49/4 against the tone.
        self.assertAlmostEqual(found.weighted_distortion_db, 10 * np.log10(.01 * 9 / 4 + .001 * 49 / 4), delta=.3)
        self.assertAlmostEqual(found.harmonic_centroid, (1 + 3 * .01 + 7 * .001) / 1.011, delta=.002)
        self.assertAlmostEqual(found.tristimulus3, .001 / 1.011, delta=.0001)

    def test_the_same_energy_weighs_more_in_high_orders(self):
        low, high = through(adding({2: -20})), through(adding({9: -20}))
        self.assertAlmostEqual(low.distortion_db, high.distortion_db, delta=.5)
        self.assertAlmostEqual(high.weighted_distortion_db - low.weighted_distortion_db, 10 * np.log10(81 / 4), delta=.5)
        self.assertGreater(high.tristimulus3, .009)
        self.assertLess(low.tristimulus3, 1e-4)
        self.assertLess(low.high_order_db, -60)
        self.assertLess(high.low_order_db, -60)

    def test_hard_clipping_spreads_further_than_soft(self):
        soft, harder, square = (through(lambda s, g=g: np.tanh(g * s)) for g in (2, 10, 40))
        clipped = through(lambda s: np.clip(30 * s, -.5, .5))
        self.assertLess(soft.harmonic_centroid, harder.harmonic_centroid)
        self.assertLess(harder.harmonic_centroid, square.harmonic_centroid)
        self.assertLess(soft.tristimulus3, harder.tristimulus3)
        # Nearly a square wave: odd harmonics at 1/n, so tristimulus 3 about .06 and weighted distortion about 0 dB.
        self.assertAlmostEqual(clipped.tristimulus3, .06, delta=.01)
        self.assertAlmostEqual(clipped.weighted_distortion_db, 0, delta=1.5)

    def test_noise_lowers_the_harmonics_to_noise_ratio(self):
        noise = np.random.default_rng(2).normal(0, .01, len(TONES))
        found = through(lambda s: s + noise)
        # The tone (.25 peak) against white noise of sigma .01 in 60 Hz to 4 kHz.
        expected = 10 * np.log10((.25 ** 2 / 2) / (.01 ** 2 * (4000 - 60) / (RATE / 2)))
        self.assertAlmostEqual(found.hnr_db, expected, delta=1.5)

    def test_the_record_keeps_every_harmonic(self):
        record = through(adding({3: -20})).to_record()
        self.assertEqual(len(record["harmonics_db"]), 9)
        for key in ("low_order_db", "high_order_db", "weighted_distortion_db", "harmonic_centroid", "tristimulus3",
                    "hnr_db", "distortion_db"):
            self.assertIn(key, record)


class TimeTest(unittest.TestCase):
    def test_an_echo_is_found_with_its_delay_and_level(self):
        found = through(delay)
        self.assertAlmostEqual(found.echo_ms, 350, delta=3)
        self.assertAlmostEqual(found.echo_db, -6, delta=1)
        self.assertIsNone(through(lambda s: s).echo_ms)

    def test_reverb_decay_is_measured_and_not_taken_for_an_echo(self):
        found = through(reverb)
        self.assertAlmostEqual(found.decay_s, 1.4, delta=.15)
        self.assertIsNone(found.echo_ms)

    def test_tremolo_and_chorus_wobble_and_a_plain_tone_does_not(self):
        n = np.arange(len(TONES))
        tremolo = through(lambda s: s * (1 - .25 * (1 + np.sin(2 * np.pi * 5 * n / RATE))))
        chorus = through(lambda s: .5 * s + .5 * np.interp(n - (.007 + .002 * np.sin(2 * np.pi * .8 * n / RATE)) * RATE,
                                                            n, s))
        plain = through(lambda s: s)
        self.assertGreater(tremolo.level_wobble, 30)
        self.assertGreater(chorus.pitch_wobble, 5)
        self.assertLess(plain.level_wobble, 1)
        self.assertLess(plain.pitch_wobble, 1)


class LevelTest(unittest.TestCase):
    def test_the_tone_is_played_at_a_firmly_held_note(self):
        t = np.arange(RATE * 2) / RATE
        phrase = (.3 * np.sin(2 * np.pi * 196 * t) * np.exp(-2 * (t % .5)))[:, None]
        self.assertAlmostEqual(held_note_peak(phrase), .3, delta=.06)
        self.assertAlmostEqual(float(np.abs(test_signal(.3)).max()), .3, delta=.01)


if __name__ == "__main__":
    unittest.main()
