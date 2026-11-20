from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from lab.hearing import audio
from lab.learner import configuration


def clip(pickup, tone, volume, *, wiring="Series", frequency=110, pick=1):
    time = np.arange(round(.65 * audio.RATE)) / audio.RATE
    harmonics = np.arange(1, 13)
    brightness = harmonics ** (-.55) * np.exp(-harmonics * (.36 - .031 * tone))
    if pickup == 5:
        brightness[[2, 4, 7, 9]] *= .12
    else:
        brightness[[1, 3, 6, 10]] *= .12
    if wiring == "Parallel":
        brightness[[2, 3, 4, 5]] *= .2
    partials = np.sin(harmonics[:, None] * (2 * np.pi * frequency * time))
    return ((brightness @ partials) * .10 * 10 ** ((volume - 10) / 10) * pick * np.exp(-1.1 * time)).astype(np.float32)


class GuitarConfigurationTest(unittest.TestCase):
    def test_infers_wiring_and_interpolates_knobs_on_held_out_settings(self):
        examples = [{"configuration": {"pickup": pickup, "wiring": wiring, "tone": tone, "volume": volume},
                     "wav": clip(pickup, tone, volume, wiring=wiring), "source_id": f"capture-{index}"}
                    for index, (pickup, wiring, tone, volume) in enumerate(
                        (p, w, t, v) for p in (1, 5) for w in ("Series", "Parallel")
                        for t, v in ((2, 2), (2, 10), (10, 2), (10, 10), (6, 6)))]
        profile = configuration.calibrate("Charvel", examples)
        held_out = clip(5, 6.8, 7.2, wiring="Parallel", pick=1.04)
        with TemporaryDirectory() as folder:
            path = configuration.save_profile(Path(folder) / "profile.json", profile)
            restored = configuration.load_profile(path)
        estimate = configuration.estimate(restored, held_out, source_id="held-out")
        self.assertEqual(estimate["pickup"], 5, estimate)
        self.assertEqual(estimate["wiring"], "Parallel", estimate)
        for key, actual in (("tone", 6.8), ("volume", 7.2)):
            knob = estimate[key]
            self.assertIsNotNone(knob, estimate)
            self.assertLessEqual(knob["low"], actual)
            self.assertGreaterEqual(knob["high"], actual)
            self.assertLessEqual(abs(knob["value"] - actual), 1.5)
        with self.assertRaisesRegex(ValueError, "used for calibration"):
            configuration.estimate(restored, held_out, source_id="capture-0")

    def test_does_not_guess_a_pickup_or_disentangle_controls_moved_together(self):
        examples = [{"pickup": pickup, "tone": setting, "volume": setting, "wav": clip(1, setting, setting)}
                    for pickup in (1, 5) for setting in (2, 6, 10)]
        profile = configuration.calibrate("Charvel", examples)
        estimate = configuration.estimate(profile, clip(1, 6, 6))
        for key in ("pickup", "tone", "volume"):
            self.assertIsNone(estimate[key], estimate)
        self.assertEqual(estimate["confidence"], "uncertain")

    def test_abstains_on_silence_and_frets_missing_from_training(self):
        profile = configuration.calibrate("Charvel", [
            {"pickup": pickup, "tone": 7, "volume": 10, "wav": clip(pickup, 7, 10)} for pickup in (1, 5)])
        for samples in (np.zeros(audio.RATE), clip(1, 7, 10, frequency=246.94)):
            result = configuration.estimate(profile, samples)
            self.assertIsNone(result["pickup"], result)
            self.assertEqual(result["confidence"], "uncertain")

    def test_does_not_separate_pickup_and_wiring_if_always_changed_together(self):
        profile = configuration.calibrate("Charvel", [
            {"pickup": pickup, "wiring": wiring, "tone": 6, "volume": 6,
             "wav": clip(pickup, 6, 6, wiring=wiring)} for pickup, wiring in ((1, "Series"), (5, "Parallel"))])
        result = configuration.estimate(profile, clip(5, 6, 6, wiring="Parallel"))
        self.assertIsNone(result["pickup"], result)
        self.assertIsNone(result["wiring"], result)

    def test_does_not_confuse_different_tone_settings_for_pickup_evidence(self):
        profile = configuration.calibrate("Charvel", [
            {"pickup": pickup, "tone": tone, "volume": 6, "wav": clip(1, tone, 6)}
            for pickup, tone in ((1, 2), (5, 10))])
        result = configuration.estimate(profile, clip(1, 10, 6))
        self.assertIsNone(result["pickup"], result)
        self.assertEqual(result["nearest"]["pickup"], 5, result)

    def test_supported_pickup_uses_notes_shared_with_its_relevant_alternative(self):
        def phrase(pickup, wiring, frequencies):
            notes = [np.r_[clip(pickup, 6, 6, wiring=wiring, frequency=hz), np.zeros(audio.RATE // 10)]
                     for hz in frequencies]
            return np.concatenate(notes)

        frequencies = (110, 146.83, 164.81, 196)
        profile = configuration.calibrate("Charvel", [
            {"pickup": pickup, "tone": 6, "volume": 6, "wiring": wiring,
             "wav": phrase(pickup, wiring, notes)}
            for pickup, wiring, notes in ((1, "Parallel", frequencies), (5, "Parallel", frequencies),
                                          (5, "Series", frequencies[-1:]))])
        result = configuration.estimate(profile, phrase(5, "Parallel", frequencies))
        self.assertEqual(result["pickup"], 5, result)
        self.assertGreaterEqual(len(result["evidence"]["pickup"]["notes_used"]), 3)
        self.assertIsNone(result["wiring"], result)
        self.assertIsNone(result["tone"], result)
        self.assertEqual(result["nearest"]["wiring"], "Parallel", result)

    def test_abstains_when_settings_change_during_the_check(self):
        profile = configuration.calibrate("Charvel", [
            {"pickup": pickup, "tone": 6, "volume": 6, "wav": clip(pickup, 6, 6)} for pickup in (1, 5)])
        result = configuration.estimate(profile, np.concatenate([clip(1, 6, 6), clip(5, 6, 6)]))
        self.assertIsNone(result["pickup"], result)
        self.assertIn("within the clip", result["reason"])

    def test_constant_or_disconnected_knobs_are_left_unknown(self):
        def row(pickup, volume, other):
            knobs = {"neck_volume": volume if pickup == 3 else other,
                     "bridge_volume": volume if pickup == 1 else other,
                     "neck_tone": 7, "bridge_tone": 7}
            return {"configuration": {"pickup": pickup, **knobs}, "wav": clip(pickup if pickup == 1 else 5, 7, volume)}

        profile = configuration.calibrate("Les Paul", [row(pickup, volume, other)
                                          for pickup in (1, 3) for volume, other in ((2, 7), (6, 2), (10, 6))])
        estimate = configuration.estimate(profile, clip(5, 7, 7.2))
        self.assertEqual(estimate["pickup"], 3, estimate)
        self.assertIsNone(estimate["bridge_volume"], estimate)
        self.assertIsNone(estimate["bridge_tone"], estimate)
        self.assertIsNone(estimate["neck_tone"], estimate)
        self.assertIsNotNone(estimate["neck_volume"], estimate)
