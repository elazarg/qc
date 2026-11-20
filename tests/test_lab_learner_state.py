from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from lab.hearing import audio
from lab.learner import state as guitar_state


def note(frequency, state, *, seconds=.65, level=.08):
    t = np.arange(round(seconds * audio.RATE)) / audio.RATE
    partials = np.arange(1, 13)
    gains = partials ** (-.6)
    if state == "neck":
        gains = gains * np.exp(-.26 * partials)
        gains[3:5] *= .18
    else:
        gains[1:3] *= .28
    phases = partials[:, None] * 2 * np.pi * frequency * t
    return (np.sum(gains[:, None] * np.sin(phases), axis=0) * level * np.exp(-.8 * t)).astype(np.float32)


class GuitarStateTest(unittest.TestCase):
    def profile(self):
        examples = [{"label": label, "wav": np.concatenate([note(110, label), np.zeros(4000), note(146.83, label)])}
                    for label in ("neck", "bridge")]
        return guitar_state.calibrate("Charvel", "pickup", examples)

    def test_matched_pickup_estimates_unchanged_with_level(self):
        profile = self.profile()
        for label in ("neck", "bridge"):
            for level in (.02, .11):
                estimate = guitar_state.estimate(profile, note(110, label, seconds=.35, level=level))
                self.assertEqual(estimate["label"], label, estimate)
                self.assertGreater(estimate["confidence"], .8, estimate)

    def test_abstains_on_unfamiliar_pitch_silence_and_noise(self):
        profile = self.profile()
        clips = [note(315, "neck"), np.zeros(audio.RATE // 2),
                 np.random.default_rng(7).normal(0, .03, audio.RATE // 2)]
        for clip in clips:
            estimate = guitar_state.estimate(profile, clip)
            self.assertIsNone(estimate["label"], estimate)
            self.assertEqual(estimate["confidence"], 0)

    def test_json_round_trip_and_labeled_wav_slices(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            di = root / "labels.wav"
            audio.write_wave(di, np.concatenate([note(110, "neck"), note(110, "bridge")]))
            profile = guitar_state.calibrate("Les Paul", "tone", [
                {"label": "rolled back", "wav": di, "start": 0, "end": .65},
                {"label": "open", "wav": di, "start": .65, "end": 1.3},
            ])
            path = guitar_state.save_profile(root / "profiles" / "les-paul-tone.json", profile)
            self.assertEqual(guitar_state.load_profile(path), profile)
            self.assertEqual(guitar_state.estimate(profile, di, start=.7, end=1.2)["label"], "open")

    def test_volume_uses_separately_calibrated_levels(self):
        profile = guitar_state.calibrate("Charvel", "volume", [
            {"label": "3", "wav": note(110, "neck", level=.023)},
            {"label": "10", "wav": note(110, "neck", level=.12)},
        ])
        low = guitar_state.estimate(profile, note(110, "neck", level=.022))
        self.assertEqual(low["label"], "3", low)
        self.assertEqual(guitar_state.estimate(profile, note(110, "neck", level=.12))["label"], "10")
        with self.assertRaisesRegex(ValueError, "distinguishable"):
            guitar_state.calibrate("Charvel", "volume", [
                {"label": label, "wav": note(110, "neck")} for label in ("3", "10")
            ])

    def test_track_resolves_confirmed_states_and_keeps_silence(self):
        profile = self.profile()
        clips = np.concatenate([note(110, "neck", seconds=.7), note(110, "bridge", seconds=.7),
                                note(110, "neck", seconds=.7), np.zeros(audio.RATE // 2)])
        result = guitar_state.track(profile, clips)
        stable = [(row["time"], row["label"]) for row in result if row["confidence"] >= .8]
        self.assertEqual(stable[0], (.35, "neck"))
        self.assertTrue(any(label == "bridge" and 1 < time < 1.4 for time, label in stable))
        self.assertEqual(stable[-1][1], "neck")
        self.assertIsNone(result[-1]["label"])
