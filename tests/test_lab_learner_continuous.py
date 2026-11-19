import unittest

import numpy as np

from lab import audio
from lab.learner import configuration as guitar_configuration, continuous as guitar_continuous


def held_note(states, *, frequency=110, attack=False, gap=False, repick_phase=0):
    t = np.arange(2 * audio.RATE) / audio.RATE
    split = 1.0
    partials = np.arange(1, 13)
    gains = partials.astype(float) ** (-.65)
    def shape(state):
        result = gains.copy()
        if state == 5:
            result[[2, 4, 7, 9]] *= .11
        else:
            result[[1, 3, 6, 10]] *= .11
        return result / np.linalg.norm(result)

    tones = [shape(state) @ np.sin(2 * np.pi * frequency * partials[:, None] * t + phase * partials[:, None])
             for state, phase in zip(states, (0, repick_phase))]
    signal = .08 * np.where(t < split, *tones) * np.exp(-.22 * t)
    if attack:
        signal += np.where(t >= split, .24 * np.sin(2 * np.pi * 733 * t) * np.exp(-170 * np.abs(t - split)), 0)
    if gap:
        signal[np.abs(t - split) < .05] = 0
    return signal.astype(np.float32)


def replay(tracker, signal):
    return [tracker.update(signal[max(0, edge - 3 * audio.RATE):edge], at=edge / audio.RATE)
            for edge in range(audio.RATE // 4, len(signal) + 1, audio.RATE // 4)]


class GuitarContinuousTest(unittest.TestCase):
    def test_sustained_shape_change_is_reported_once_with_context(self):
        outputs = replay(guitar_continuous.Tracker(), held_note((1, 5)))
        changes = [row["change"] for row in outputs if row["change"]]
        self.assertEqual(len(changes), 1, outputs)
        change = changes[0]
        self.assertAlmostEqual(change["at"], 1, delta=.14)
        self.assertTrue(change["complete"])
        self.assertEqual(change["confirmation"], "possible")
        self.assertGreaterEqual(change["pre_seconds"], .7)
        self.assertGreaterEqual(change["post_seconds"], .32)
        self.assertIsNone(change["after"])

    def test_taught_pickup_states_support_the_observation(self):
        examples = [{"pickup": pickup, "tone": 7, "volume": 10, "wav": held_note((pickup, pickup))}
                    for pickup in (1, 5)]
        profile = guitar_configuration.calibrate("Charvel", examples)
        rows = replay(guitar_continuous.Tracker(profile), held_note((1, 5)))
        changes = [row["change"] for row in rows if row["change"]]
        self.assertEqual(len(changes), 1, rows)
        change = changes[0]
        self.assertEqual(change["confirmation"], "supported")
        self.assertEqual(change["changed"], ["pickup"])
        self.assertEqual((change["before"]["pickup"], change["after"]["pickup"]), (1, 5))
        self.assertEqual(rows[-1]["estimate"]["pickup"], 5)

    def test_short_preceding_audio_is_marked_partial(self):
        signal = held_note((1, 5))[round(.52 * audio.RATE):]
        changes = [row["change"] for row in replay(guitar_continuous.Tracker(), signal) if row["change"]]
        self.assertEqual(len(changes), 1)
        self.assertFalse(changes[0]["complete"])
        self.assertAlmostEqual(changes[0]["start"], 0.0)

    def test_one_live_match_waits_for_a_second_look(self):
        examples = [{"pickup": pickup, "tone": 7, "volume": 10, "wav": held_note((pickup, pickup))}
                    for pickup in (1, 5)]
        tracker = guitar_continuous.Tracker(guitar_configuration.calibrate("Charvel", examples))
        rows = replay(tracker, held_note((5, 5)))
        self.assertIsNone(rows[0]["estimate"]["pickup"])
        self.assertEqual(rows[0]["estimate"]["nearest"]["pickup"], 5)
        self.assertEqual(rows[-1]["estimate"]["pickup"], 5)

    def test_no_change_from_different_notes_pick_attack_gain_or_silence(self):
        level = held_note((1, 1))
        level[audio.RATE:] *= 2.4
        strong_and_bright = held_note((1, 5))
        strong_and_bright[audio.RATE:] *= 2.4
        different_notes = np.r_[held_note((1, 1))[:audio.RATE], held_note((5, 5), frequency=146.83)[:audio.RATE]]
        cases = {"strong pick": held_note((1, 5), attack=True), "new vibrational phase": held_note((1, 5), repick_phase=2.5),
                 "between notes": held_note((1, 5), gap=True), "level only": level, "different pitch": different_notes,
                 "large gain and shape shift": strong_and_bright}
        for case, playing in cases.items():
            with self.subTest(signal=case):
                self.assertFalse(any(row["change"] for row in replay(guitar_continuous.Tracker(), playing)))
        end = np.r_[held_note((5, 5))[:audio.RATE], np.zeros(audio.RATE)]
        outputs = replay(guitar_continuous.Tracker(), end)
        self.assertEqual(outputs[-1]["mode"], "silent")
        self.assertIsNone(outputs[-1]["estimate"])
