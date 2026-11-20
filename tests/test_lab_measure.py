"""One measurement for everything, and the words for what a chain did to a phrase."""

import unittest

import numpy as np
from scipy import signal as dsp

from lab import measure, probe
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
    """From the phrase: compression, brightness, tail. Distortion comes only from the test tones."""

    def test_compression_is_how_much_of_a_12_db_drop_fails_to_come_back(self):
        self.assertEqual(measure.heard(*through(lambda sound: sound * .5, note())).compressed, "none")
        squeeze = lambda sound: sound * (.02 / max(float(np.sqrt(np.mean(sound ** 2))), 1e-9))
        found = measure.heard(*through(squeeze, note(4.)))
        self.assertGreater(found.compression, .82)
        self.assertEqual(found.compressed, "squashed")

    def test_a_compressor_is_not_called_distorted(self):
        # Squashed, but without test tones there is no distortion word at all.
        squeeze = lambda sound: sound * (.02 / max(float(np.sqrt(np.mean(sound ** 2))), 1e-9))
        self.assertIsNone(measure.heard(*through(squeeze, note(4.))).distortion)

    def test_distortion_words_follow_the_measured_harmonics(self):
        # As measured on the unit: Twin, Plexi at gain 2, 5, 8 and 10 (loud, then 12 dB softer).
        ladder = [(-45.4, -64.0), (-29.7, -49.4), (-18.0, -39.4), (-17.9, -21.4), (-18.1, -18.7)]
        words = [measure.heard(*through(lambda sound: sound, note()),
                               probe={"distortion_db": loud, "distortion_soft_db": soft}).distortion
                 for loud, soft in ladder]
        self.assertEqual(words, list(measure.WORDS["distortion"]))
        # A compressor alone, however hard it squeezes, adds no harmonics: clean.
        self.assertEqual(measure.distortion_word(-67.6, -70.), "clean")

    def test_what_rings_on_is_the_tail_and_a_short_echo_counts(self):
        dry = note()
        echo = np.zeros((RATE, 1))
        echo[round(.08 * RATE):round(.2 * RATE)] = dry[:round(.12 * RATE)] * .5
        self.assertIn(measure.heard(dry, dry, dry * measure.SOFTER, echo).tail, ("ambient", "wet"))
        self.assertEqual(measure.heard(*through(lambda sound: sound, note())).tail, "dry")

    def test_dark_is_dark(self):
        lows = dsp.butter(2, 300, "low", fs=RATE, output="sos")
        found = measure.heard(*through(lambda sound: dsp.sosfilt(lows, sound, axis=0), note()))
        self.assertEqual((found.tone, found.compressed), ("dark", "none"))

    def test_a_summary_reads_current_and_earlier_names(self):
        self.assertEqual(measure.summary({"distortion": "crunch", "brightness": "bright", "tail": "dry"}),
                         "crunch, bright, dry")
        self.assertEqual(measure.summary({"drive": "crunch", "tone": "bright", "space": "dry"}), "crunch, bright, dry")


NOTE_STARTS = (.25, .75, 1.25, 1.75, 2.25, 2.75)


def phrase(ramp_s=0., partials=60):
    """Notes of a bright, many-partial tone starting at NOTE_STARTS, each decaying, each reaching full level along a
    straight ramp of `ramp_s` (0 for at once): a 10%-90% rise of 0.8 * ramp_s."""
    total = round(3.5 * RATE)
    out = np.zeros(total)
    for start in NOTE_STARTS:
        begin = round(start * RATE)
        time = np.arange(total - begin) / RATE
        rise = np.minimum(1, time / ramp_s) if ramp_s else 1
        tone = sum(np.sin(2 * np.pi * 196 * k * time) / k for k in range(1, partials + 1))
        out[begin:] += .15 * tone * np.exp(-3 * time) * rise
    return out[:, None]


def silence():
    return np.full((RATE // 2, 1), 1e-7)


class AttackAndBandsTest(unittest.TestCase):
    """How fast notes reach full level, and the energy by band, against the dry phrase."""

    def test_notes_are_found_in_the_dry_phrase(self):
        found = [index * measure.ENVELOPE_HOP_S for index in measure.onsets(phrase())]
        self.assertEqual(len(found), len(NOTE_STARTS))
        for at, start in zip(found, NOTE_STARTS):
            self.assertAlmostEqual(at, start, delta=.005)

    def test_a_slower_rise_reads_as_a_slower_attack(self):
        dry = phrase()
        found = measure.heard(dry, phrase(.04), phrase(.08) * measure.SOFTER, silence())
        self.assertAlmostEqual(found.attack_ms, 32, delta=4)
        self.assertAlmostEqual(found.attack_soft_ms, 64, delta=6)
        same = measure.heard(*through(lambda sound: sound * .5, dry))
        self.assertLess(same.attack_ms, 4)
        self.assertLess(same.attack_soft_ms, 4)

    def test_attack_is_timed_on_the_dry_phrases_notes_even_through_an_echo(self):
        dry = phrase(.04)
        late = round(.12 * RATE)
        echo = lambda sound: sound + .5 * np.vstack([np.zeros((late, 1)), sound[:-late]])
        found = measure.heard(*through(echo, dry))
        self.assertAlmostEqual(found.attack_ms, 32, delta=4)

    def test_a_silent_phrase_has_no_attack(self):
        quiet = np.zeros((RATE, 1))
        self.assertEqual(measure.onsets(quiet), [])
        self.assertIsNone(measure.attack_ms(quiet, []))

    def test_energy_by_band_follows_a_low_pass_and_ignores_level(self):
        dry = phrase()
        lows = dsp.butter(4, 2500, "low", fs=RATE, output="sos")
        found = measure.heard(*through(lambda sound: dsp.sosfilt(lows, sound, axis=0), dry)).brightness_bands_db
        self.assertEqual(set(found), {"1-3 kHz", "3-6 kHz", "6-12 kHz"})
        self.assertGreater(found["1-3 kHz"], -3)
        self.assertLess(found["3-6 kHz"], -10)
        self.assertLess(found["6-12 kHz"], found["3-6 kHz"] - 10)
        louder = measure.heard(*through(lambda sound: sound * 3, dry)).brightness_bands_db
        self.assertLess(max(map(abs, louder.values())), .05)

    def test_the_record_carries_the_new_values(self):
        record = measure.heard(*through(lambda sound: sound, phrase())).to_record()
        for key in ("attack_ms", "attack_soft_ms", "brightness_bands_db", "texture", "compression", "probe"):
            self.assertIn(key, record)
        lines = measure.measured_lines(record)
        self.assertTrue(any(line.startswith("Attack: notes reach full level in") for line in lines))
        self.assertTrue(any(line.startswith("Energy by band") for line in lines))


class TextureTest(unittest.TestCase):
    """Round to rough from the probe's harmonic spread: an inference, with provisional thresholds."""

    def test_texture_follows_how_far_the_harmonics_spread(self):
        tones = probe.test_signal(.25)[:, 0].astype(np.float64)
        chains = (lambda s: s, lambda s: np.tanh(4 * s), lambda s: np.tanh(10 * s), lambda s: np.clip(30 * s, -.5, .5))
        words = [measure.heard(*through(lambda sound: sound, note()),
                               probe=probe.analyse(chain(tones)[:, None]).to_record()).texture for chain in chains]
        self.assertEqual(words, list(measure.TEXTURE))
        self.assertEqual(measure.WORDS["texture"], measure.TEXTURE)

    def test_no_probe_or_an_old_probe_gives_no_texture(self):
        self.assertIsNone(measure.heard(*through(lambda sound: sound, note())).texture)
        old = {"distortion_db": -18., "distortion_soft_db": -39.}
        found = measure.heard(*through(lambda sound: sound, note()), probe=old)
        self.assertIsNone(found.texture)
        self.assertIn("texture", found.words())
        self.assertEqual(found.distortion, "crunch")

    def test_records_made_before_the_new_values_still_display(self):
        old = {"compression": .5, "crest_db": 12., "brightness_oct": .1, "low_end_db": 0., "tail_db": -40.,
               "level_db": -20., "probe": {"distortion_db": -18., "distortion_soft_db": -39., "even_odd_db": 1.,
                                           "intermod_db": -12., "gated": False, "level_wobble": 0., "pitch_wobble": 0.,
                                           "echo_ms": None, "echo_db": None, "decay_s": 0.},
               "distortion": "crunch", "compressed": "moderate", "brightness": "balanced", "tail": "a touch of room"}
        lines = measure.measured_lines(old)
        self.assertTrue(lines[0].startswith("Distortion:"))
        self.assertFalse(any(line.startswith(("Attack", "Harmonic", "Energy by band")) for line in lines))
        self.assertEqual(measure.summary(old), "crunch, balanced, a touch of room")

    def test_new_records_show_harmonic_orders_and_the_texture_word(self):
        tones = probe.test_signal(.25)[:, 0].astype(np.float64)
        found = measure.heard(*through(lambda sound: sound, phrase()),
                              probe=probe.analyse(np.tanh(10 * tones)[:, None]).to_record())
        lines = measure.measured_lines(found.to_record())
        orders = next(line for line in lines if line.startswith("Harmonic orders:"))
        self.assertIn("low 2nd-3rd", orders)
        self.assertIn("high 5th-10th", orders)
        self.assertTrue(any("texture edged" in line for line in lines))


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
