"""The tone field: points as knob positions, sweeps, and the A/B contrast measured on the simulated unit.

Ported from test_tone_space."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from lab.sound.knobs import KNOBS
from lab.tools import field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import DELAY, chain, host, phrase  # noqa: E402


class FieldTest(unittest.TestCase):
    def test_the_centre_restores_the_anchor_and_the_axes_move_separately(self):
        plane = field.PLANES["Gain & color"]
        anchor = {"jazz": .35, "drive": -.1, "attack": .4}
        self.assertEqual(field.position_values(anchor, plane, 0, 0), anchor)
        horizontal = field.position_values(anchor, plane, 1, 0)
        vertical = field.position_values(anchor, plane, 0, 1)
        self.assertGreater(horizontal["drive"], anchor["drive"])
        self.assertEqual(vertical["drive"], anchor["drive"])
        self.assertGreater(vertical["brightness"], 0)
        self.assertEqual(vertical["attack"], .4)

    def test_corners_stay_within_each_knob_and_unavailable_knobs_stay_put(self):
        for plane in field.PLANES.values():
            for key, _ in plane.horizontal.moves + plane.vertical.moves:
                self.assertIn(key, KNOBS)
            for anchor in ({key: 1. for key in KNOBS}, {key: row[1] for key, row in KNOBS.items()}):
                for x, y in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
                    for key, value in field.position_values(anchor, plane, x, y).items():
                        self.assertLessEqual(KNOBS[key][1], value)
                        self.assertLessEqual(value, 1.)
        plane = field.PLANES["Depth & width"]
        anchor = {"drive": -.25, "width": .5}
        result = field.position_values(anchor, plane, 1., 1., {"space"})
        self.assertEqual(result["width"], .5)
        self.assertEqual(result["drive"], -.25)
        self.assertGreater(result["space"], 0.)
        self.assertEqual(field.position_values(anchor, plane, 1., 1., set()), anchor)

    def test_a_sweep_keeps_its_ends_with_different_knobs(self):
        start, end = {"drive": -.8, "attack": .6}, {"drive": .8, "jazz": .2}
        self.assertEqual(field.interpolate(start, end, 0.), start)
        self.assertEqual(field.interpolate(start, end, 1.), end)
        self.assertEqual(field.interpolate(start, end, .5), {"drive": 0., "attack": .3, "jazz": .1})
        with self.assertRaises(ValueError):
            field.interpolate(start, end, float("nan"))

    def test_the_fingerprint_ignores_level_and_shows_a_change_of_colour(self):
        time = np.arange(48000, dtype=float) / 48000
        low = np.sin(2 * np.pi * 220 * time)
        high = low + .32 * np.sin(2 * np.pi * 2400 * time)
        np.testing.assert_allclose(field.difference(low, low * .08).spectrum_db, 0., atol=1e-8)
        measured = field.difference(low, high)
        self.assertGreater(measured.perceptual_delta[0][1], .5)
        self.assertEqual(len(measured.spectrum_db), len(field.BAND_NAMES))


class OnTheUnitTest(unittest.TestCase):
    def test_what_the_contrast_found_is_drawn_as_signed_bars_in_the_colour_of_the_side_that_has_more(self):
        import tkinter as tk

        from lab.desk.theme import TONE_A, TONE_B

        root = tk.Tk()
        try:
            chart = tk.Canvas(root)
            field.draw_deltas(chart, [("Spectral centre", .5, "octave"), ("Carry-over after notes", -80., "points"),
                                      ("Loudness spread", 0., "dB")], width=400)
            texts = [chart.itemcget(item, "text") for item in chart.find_all() if chart.type(item) == "text"]
            self.assertIn("+0.5 octave", texts)
            self.assertIn("Carry-over after notes", texts)
            bars = [chart.coords(item) + [chart.itemcget(item, "fill")] for item in chart.find_all()
                    if chart.type(item) == "rectangle"]
            self.assertEqual(len(bars), 2)                                  # nothing drawn for no change
            centre = [chart.coords(item) for item in chart.find_all() if chart.type(item) == "line"][0][0]
            (b_left, _, b_right, _, first), (a_left, _, a_right, _, second) = bars
            self.assertEqual((first, second), (TONE_B, TONE_A))
            self.assertEqual(b_left, centre)                                # B's to the right of the centre
            self.assertEqual(a_right, centre)                               # A's to the left
            self.assertAlmostEqual((b_right - b_left) * 2, a_right - a_left)  # half the reach against all of it
        finally:
            root.destroy()

    def test_the_contrast_of_a_and_b_is_measured_silently(self):
        with TemporaryDirectory() as folder:
            unit = host(folder)
            try:
                found = unit.call("lab.tools.field.contrast", chain(gain_db=0.), chain(DELAY), phrase()).result(60)
            finally:
                unit.close()
        deltas = {name: value for name, value, _ in found["perceptual_delta"]}
        self.assertGreater(deltas["Carry-over after notes"], 0)
        self.assertEqual(len(found["spectrum_db"]), len(field.BAND_NAMES))


if __name__ == "__main__":
    unittest.main()
