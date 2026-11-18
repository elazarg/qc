import unittest

import numpy as np

from tone_lab import audio, styles, tone_space


class ToneSpaceTest(unittest.TestCase):
    def test_center_restores_anchor_and_axes_have_independent_targets(self):
        plane = tone_space.PLANES["Gain & color"]
        anchor = {"jazz": .35, "drive": -.1, "attack": .4}
        self.assertEqual(tone_space.position_values(anchor, plane, 0, 0), anchor)
        horizontal = tone_space.position_values(anchor, plane, 1, 0)
        vertical = tone_space.position_values(anchor, plane, 0, 1)
        self.assertGreater(horizontal["drive"], anchor["drive"])
        self.assertEqual(vertical["drive"], anchor["drive"])
        self.assertLess(vertical["warmth"], 0)
        self.assertEqual(vertical["attack"], .4)

    def test_corners_never_exceed_knob_bounds_and_support_filters(self):
        for plane in tone_space.PLANES.values():
            for anchor in ({key: 1. for key in styles.KNOBS}, {key: row[1] for key, row in styles.KNOBS.items()}):
                for x, y in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
                    result = tone_space.position_values(anchor, plane, x, y)
                    for key, value in result.items():
                        self.assertLessEqual(styles.KNOBS[key][1], value)
                        self.assertLessEqual(value, 1.)
        plane = tone_space.PLANES["Depth & width"]
        anchor = {"drive": -.25, "width": .5}
        result = tone_space.position_values(anchor, plane, 1., 1., {"space"})
        self.assertEqual(result["width"], .5)
        self.assertEqual(result["drive"], -.25)
        self.assertGreater(result["space"], 0.)
        self.assertEqual(tone_space.position_values(anchor, plane, 1., 1., set()), anchor)

    def test_sweep_preserves_endpoints_with_different_control_keys(self):
        start, end = {"drive": -.8, "attack": .6}, {"drive": .8, "jazz": .2}
        self.assertEqual(tone_space.interpolate(start, end, 0.), start)
        self.assertEqual(tone_space.interpolate(start, end, 1.), end)
        self.assertEqual(tone_space.interpolate(start, end, .5), {"drive": 0., "attack": .3, "jazz": .1})
        with self.assertRaises(ValueError):
            tone_space.interpolate(start, end, float("nan"))

    def test_fingerprint_ignores_gain_and_exposes_color_change(self):
        time = np.arange(audio.RATE, dtype=float) / audio.RATE
        low = np.sin(2 * np.pi * 220 * time)
        high = low + .32 * np.sin(2 * np.pi * 2400 * time)
        level = tone_space.difference(low, low * .08)
        np.testing.assert_allclose(level.spectrum_db, 0., atol=1e-8)
        measured = tone_space.difference(low, high)
        self.assertGreater(measured.perceptual_delta[0][1], .5)
        self.assertEqual(len(measured.spectrum_db), len(tone_space.BAND_NAMES))


if __name__ == "__main__":
    unittest.main()
