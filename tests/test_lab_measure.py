"""One measurement for everything, and the words for what a chain did to a phrase."""

import unittest

import numpy as np
from scipy import signal as dsp

from lab import measure
from lab.audio import RATE, onset, click


def note(seconds=1.5, pitch=196.):
    time = np.arange(round(seconds * RATE)) / RATE
    return (.25 * sum(np.sin(2 * np.pi * pitch * k * time) / k for k in (1, 2, 3)) * np.exp(-3 * time))[:, None]


def through(chain, dry):
    silence = np.full((RATE // 2, 1), 1e-7)
    return dry, chain(dry), chain(dry * measure.SOFTER), silence


class MeasureTest(unittest.TestCase):
    def test_level_peak_and_spectrum_of_a_sine(self):
        time = np.arange(RATE) / RATE
        sine = (.5 * np.sin(2 * np.pi * 1000 * time))[:, None]
        found = measure.measure(sine)
        self.assertAlmostEqual(found.rms_db, -9.03, places=1)
        self.assertAlmostEqual(found.peak_db, -6.02, places=1)
        self.assertAlmostEqual(found.crest_db, 3.01, places=1)
        self.assertEqual(int(np.argmax(found.bands_db)), measure.CENTRES.index(1000.))
        self.assertAlmostEqual(found.centroid_hz, 1000, delta=20)

    def test_opposite_channels_are_measured_and_called_wide(self):
        dry = note()
        found = measure.measure(np.hstack([dry, -dry]))
        self.assertAlmostEqual(found.rms_db, measure.level(dry), places=1)
        self.assertGreater(measure.measure(np.hstack([dry, np.roll(dry, 4800)])).width, .5)
        self.assertLess(found.width, .01)  # Opposite phase is still one source: not wide.

    def test_the_gap_between_two_sounds_ignores_their_level(self):
        dry = np.random.default_rng(1).normal(0, .05, (RATE, 1))
        dark = dsp.sosfilt(dsp.butter(2, 800, "low", fs=RATE, output="sos"), dry, axis=0)
        self.assertLess(min(measure.gap(measure.measure(dry), measure.measure(dark * 4))), -6)
        self.assertLess(max(map(abs, measure.gap(measure.measure(dry), measure.measure(dry * 4)))), .05)


class HeardTest(unittest.TestCase):
    def test_clean_saturated_and_the_steps_between(self):
        self.assertEqual(measure.heard(*through(lambda sound: sound * .5, note())).drive, "clean")
        self.assertEqual(measure.heard(*through(lambda sound: np.clip(sound * 300, -.3, .3), note())).drive,
                         "saturated")
        words = [measure.heard(*through(lambda sound: np.tanh(sound * gain), note())).drive
                 for gain in (.5, 1.6, 3., 6., 4000.)]
        order = measure.WORDS["drive"]
        self.assertEqual(sorted(words, key=order.index), words)

    def test_a_compressor_that_leaves_the_peaks_standing_is_not_distortion(self):
        squeeze = lambda sound: sound * (.02 / max(float(np.sqrt(np.mean(sound ** 2))), 1e-9))
        found = measure.heard(*through(squeeze, note(4.)))
        self.assertGreater(found.squash, .82)
        self.assertEqual(found.drive, "edge of breakup")

    def test_what_rings_on_is_ambience_and_a_short_echo_counts(self):
        dry = note()
        echo = np.zeros((RATE, 1))
        echo[round(.08 * RATE):round(.2 * RATE)] = dry[:round(.12 * RATE)] * .5
        self.assertIn(measure.heard(dry, dry, dry * measure.SOFTER, echo).space, ("ambient", "wet"))
        self.assertEqual(measure.heard(*through(lambda sound: sound, note())).space, "dry")

    def test_dark_is_dark(self):
        lows = dsp.butter(2, 300, "low", fs=RATE, output="sos")
        found = measure.heard(*through(lambda sound: dsp.sosfilt(lows, sound, axis=0), note()))
        self.assertEqual((found.tone, found.drive), ("dark", "clean"))


class MatchTest(unittest.TestCase):
    def test_matching_cuts_to_the_quietest_and_lifts_only_when_asked(self):
        quiet, loud = measure.measure(note() * .1), measure.measure(note() * .5)
        trims = measure.match([quiet, loud])
        self.assertAlmostEqual(trims[0], 0, places=1)
        self.assertAlmostEqual(trims[1], -(loud.lufs - quiet.lufs), places=1)
        lifted = measure.match([quiet, loud], offset_db=3., max_boost_db=6.)
        self.assertAlmostEqual(lifted[0], 3, places=1)
        self.assertLessEqual(max(measure.match([quiet], target_lufs=0, offset_db=40, max_boost_db=40)), 6.01)


class RoundTripTest(unittest.TestCase):
    def test_a_clicks_delay_is_found(self):
        sent = click()
        back = np.zeros((len(sent), 2))
        back[7750:] = sent[:len(sent) - 7750]
        self.assertEqual(onset(back), 7750)


if __name__ == "__main__":
    unittest.main()
