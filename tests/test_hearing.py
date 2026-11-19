"""What the app can say about a chain from its return, without anyone listening."""

import unittest

import numpy as np

from tone_lab import audio, hearing


def note(seconds=1.5, pitch=196.):
    """A plucked note: a few harmonics that die away."""
    time = np.arange(round(seconds * audio.RATE)) / audio.RATE
    tone = sum(np.sin(2 * np.pi * pitch * k * time) / k for k in (1, 2, 3))
    return (.25 * tone * np.exp(-3 * time))[:, None].astype(np.float32)


def through(chain, dry):
    """The four things a proof hears: the phrase, the same 12 dB quieter, and what rings on after it."""
    silence = np.full((audio.RATE // 2, 1), 1e-7, dtype=np.float32)
    return dry, chain(dry), chain(dry * hearing.SOFTER), silence


class HearingTest(unittest.TestCase):
    def test_a_chain_that_only_changes_level_is_clean(self):
        heard = hearing.describe(*through(lambda sound: sound * .5, note()))
        self.assertAlmostEqual(heard.squash, 0., places=2)
        self.assertEqual((heard.drive, heard.tone, heard.space), ("clean", "balanced", "dry"))
        self.assertAlmostEqual(heard.level_db, hearing._level(note()) - 6.02, places=1)

    def test_a_chain_that_clips_is_saturated_whatever_its_equalization_or_level(self):
        fuzz = lambda sound: np.clip(sound * 200, -.3, .3)
        heard = hearing.describe(*through(fuzz, note()))
        self.assertGreater(heard.squash, .82)
        self.assertEqual(heard.drive, "saturated")
        self.assertLess(heard.crest_db, hearing.describe(*through(lambda sound: sound, note())).crest_db)
        self.assertGreater(heard.brightness, .45)  # Clipping adds harmonics above the note.
        quieter = hearing.describe(*through(lambda sound: fuzz(sound) * .1, note()))
        self.assertAlmostEqual(quieter.squash, heard.squash, places=3)

    def test_drive_between_the_extremes_is_named_in_steps(self):
        words = []
        for gain in (.5, 1.6, 3., 6., 4000.):
            words.append(hearing.describe(*through(lambda sound: np.tanh(sound * gain), note())).drive)
        self.assertEqual(words[0], "clean")
        self.assertEqual(words[-1], "saturated")
        order = [word for _, word in hearing.DRIVE][::-1]
        self.assertEqual(sorted(words, key=order.index), words)  # More gain never reads as less drive.

    def test_what_rings_on_after_the_phrase_is_the_ambience(self):
        dry = note()
        ring = (dry[:audio.RATE // 2] * .2).astype(np.float32)
        heard = hearing.describe(dry, dry, dry * hearing.SOFTER, ring)
        self.assertEqual(heard.space, "wet")
        self.assertEqual(hearing.describe(dry, dry, dry * hearing.SOFTER, ring * .05).space, "ambient")
        # Delay and reverb follow level exactly, so they never read as drive.
        self.assertAlmostEqual(heard.squash, 0., places=2)

    def test_a_darker_chain_is_called_dark(self):
        from scipy import signal

        lows = signal.butter(2, 300, "low", fs=audio.RATE, output="sos")
        heard = hearing.describe(*through(lambda sound: signal.sosfilt(lows, sound, axis=0), note()))
        self.assertEqual((heard.tone, heard.drive), ("dark", "clean"))
        self.assertEqual(heard.to_record()["tone"], "dark")
        self.assertEqual(heard.said(), "clean, dark, dry")


if __name__ == "__main__":
    unittest.main()
