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
        self.assertEqual(hearing.describe(dry, dry, dry * hearing.SOFTER, ring * .1).space, "ambient")
        # An echo 80 ms after the phrase stops is heard, although nothing rings half a second later.
        echo = np.zeros((audio.RATE, 1), dtype=np.float32)
        echo[round(.08 * audio.RATE):round(.2 * audio.RATE)] = dry[:round(.12 * audio.RATE)] * .5
        self.assertIn(hearing.describe(dry, dry, dry * hearing.SOFTER, echo).space, ("ambient", "wet"))
        # Delay and reverb follow level exactly, so they never read as drive.
        self.assertAlmostEqual(heard.squash, 0., places=2)

    def test_a_darker_chain_is_called_dark(self):
        from scipy import signal

        lows = signal.butter(2, 300, "low", fs=audio.RATE, output="sos")
        heard = hearing.describe(*through(lambda sound: signal.sosfilt(lows, sound, axis=0), note()))
        self.assertEqual((heard.tone, heard.drive), ("dark", "clean"))
        self.assertEqual(heard.to_record()["tone"], "dark")
        self.assertEqual(heard.said(), "clean, dark, dry")


    def test_a_compressor_that_leaves_the_peaks_standing_is_not_called_distortion(self):
        def squeeze(sound):  # Holds the level down as a whole, as a slow compressor does. Nothing is clipped.
            return sound * (.02 / max(float(np.sqrt(np.mean(sound ** 2))), 1e-9))

        heard = hearing.describe(*through(squeeze, note(4.)))
        self.assertGreater(heard.squash, .82)
        self.assertGreater(heard.crest_db, hearing.PEAKS_INTACT)
        self.assertEqual(heard.drive, "edge of breakup")

    def test_two_channels_in_opposite_phase_are_heard_and_not_cancelled(self):
        dry = note()
        wide = np.hstack([dry, -dry])
        heard = hearing.describe(dry, wide, wide * hearing.SOFTER, np.full((audio.RATE // 2, 2), 1e-7))
        self.assertEqual((heard.tone, heard.drive), ("balanced", "clean"))
        self.assertAlmostEqual(heard.level_db, hearing._level(dry), places=1)

    def test_a_pass_is_refused_once_the_unit_has_left_the_draft(self):
        from unittest.mock import patch

        dry = note()
        played = []

        def reamp(sound, *, tail, cancel=None):
            played.append(len(sound))
            return np.zeros((len(sound) + round(tail * audio.RATE) + 100, 2), dtype=np.float32) + .01

        positions = iter([hearing.draft_render.WORK_SLOT, {"position": 7}])
        with patch.object(hearing.device, "live_position", side_effect=lambda qc: next(positions)), \
                patch.object(hearing.device, "active_scene", return_value=0), \
                patch.object(hearing.device, "read_preset"), \
                patch.object(hearing.iterate, "routes", return_value=hearing.iterate.USB_ROUTES), \
                patch.object(hearing.audio, "reamp", reamp):
            with self.assertRaises(hearing.Moved):
                hearing.proof(object(), dry, 0)
        self.assertEqual(len(played), 1)  # The quieter pass was never sent into the preset the player chose.

if __name__ == "__main__":
    unittest.main()
