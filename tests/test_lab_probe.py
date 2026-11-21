"""Test signals: what a chain is, measured directly, on chains whose answer is known."""

import unittest

import numpy as np
from numpy.polynomial.chebyshev import chebval
from scipy import signal

from lab.hearing import measure
from lab.hearing.audio import RATE
from lab.hearing.probe import F1, F2, analyse, held_note_peak, layout, test_signal

PEAK = .25
TONES = test_signal(PEAK)[:, 0].astype(np.float64)
N = np.arange(len(TONES))


def through(chain):
    return analyse(chain(TONES)[:, None])


def delay(sound, seconds=.35, gain=.5, feedback=0.):
    """A delay line: one repeat `gain` down, and with feedback further ones, each `feedback` of the last."""
    late = round(seconds * RATE)
    out = sound.copy()
    repeat, level = 1, gain
    while repeat * late < len(sound) and level > 1e-5:
        out[repeat * late:] += level * sound[:-repeat * late]
        if not feedback:
            break
        repeat, level = repeat + 1, level * feedback
    return out


def delayed(seconds, gain, feedback=0.):
    return lambda sound: delay(sound, seconds, gain, feedback)


def reverb(sound, seconds=1.4, wet_db=None):
    """Noise decaying 60 dB in `seconds`. `wet_db` is its energy against the direct sound's; left out, a strong
    one (as this file always had)."""
    length = round(min(seconds * 1.6, 6.) * RATE)
    response = np.random.default_rng(1).standard_normal(length) * np.exp(-np.arange(length) / RATE * 6.9 / seconds)
    if wet_db is None:
        response[0] = 3
        return signal.fftconvolve(sound, response)[:len(sound)] / 20
    response *= 10 ** (wet_db / 20) / np.sqrt(np.sum(response ** 2))
    response[0] += 1
    return signal.fftconvolve(sound, response)[:len(sound)]


def reverberant(seconds, wet_db):
    return lambda sound: reverb(sound, seconds, wet_db)


def noisy(dbfs, seed=3):
    noise = np.random.default_rng(seed).normal(0, 10 ** (dbfs / 20), len(TONES))
    return lambda sound: sound + noise


class LayoutTest(unittest.TestCase):
    def test_the_softer_tone_is_played_before_the_firm_one_and_the_pass_stays_short(self):
        places = layout()
        self.assertLess(places["click"], places["soft"])
        self.assertLess(places["soft"], places["tone"])
        self.assertLess(places["tone"], places["pair"])
        self.assertEqual(places["end"], len(TONES))
        self.assertLessEqual(len(TONES) / RATE, 11.)

    def test_no_intermodulation_product_falls_on_a_multiple_of_50_hz(self):
        for product in (F2 - F1, 2 * F1 - F2, 2 * F2 - F1, F1 + F2):
            self.assertGreater(abs(product - 50 * round(product / 50)), 7., product)


class DistortionTest(unittest.TestCase):
    def test_linear_chains_add_no_harmonics(self):
        cab = lambda s: signal.sosfilt(signal.butter(4, 5000, fs=RATE, output="sos"), s)
        for chain in (lambda s: s * .3, cab, delay, reverb):
            found = through(chain)
            self.assertLessEqual(found.distortion_db, -55)
            self.assertLessEqual(found.distortion_740hz_db, -55)
            self.assertLessEqual(found.intermod_db, -40)

    def test_more_clipping_more_harmonics_and_softer_playing_cleans_up(self):
        levels = [through(lambda s, g=g: np.tanh(g * s)).distortion_db for g in (2, 10, 40)]
        self.assertEqual(levels, sorted(levels))
        soft_clip = through(lambda s: np.tanh(10 * s))
        self.assertLess(soft_clip.distortion_soft_db, soft_clip.distortion_db - 10)
        self.assertAlmostEqual(soft_clip.cleans_up_db, soft_clip.distortion_db - soft_clip.distortion_soft_db, places=1)
        self.assertGreater(through(lambda s: np.clip(30 * s, -.5, .5)).distortion_db, -10)
        self.assertIsNone(through(lambda s: s).cleans_up_db)            # no harmonics: nothing to clean up

    def test_symmetric_clipping_is_odd_and_lopsided_clipping_adds_even(self):
        self.assertLessEqual(through(lambda s: np.tanh(8 * s)).even_odd_db, -40)
        self.assertGreater(through(lambda s: np.tanh(8 * s + .3) - np.tanh(.3)).even_odd_db, -10)

    def test_a_gate_is_caught_by_the_softer_tone(self):
        follow = lambda s: signal.sosfilt(signal.butter(1, 20, fs=RATE, output="sos"), np.abs(s))
        gated = through(lambda s: np.where(follow(s) > .05, s, 0))
        self.assertTrue(gated.gated)
        self.assertIsNone(gated.cleans_up_db)
        self.assertFalse(through(lambda s: s).gated)

    def test_a_delay_after_a_clipper_does_not_hide_that_the_softer_tone_cleans_up(self):
        # A3: the firm tone's repeats used to fall inside the softer tone and read as its harmonics (-34 became -23).
        alone = through(lambda s: np.tanh(8 * s))
        self.assertLess(alone.distortion_soft_db, -30)
        for effect in (delayed(.4, .316, .35), delayed(.5, .5, .5), reverberant(2., -12)):
            found = through(lambda s: effect(np.tanh(8 * s)))
            self.assertLess(found.distortion_soft_db, -30)
            self.assertAlmostEqual(found.cleans_up_db, alone.cleans_up_db, delta=1.)
            self.assertEqual(measure.distortion_word(found.distortion_db, found.distortion_soft_db), "crunch")

    def test_what_comes_after_the_clipper_moves_the_harmonics_but_not_how_far_they_clean_up(self):
        # B1: the same clipper, with and without 8 dB more of the tone itself after it.
        low, high = signal.iirpeak(F1, 2., fs=RATE)
        bump = lambda s: s + 1.5 * signal.lfilter(low, high, s)
        plain = through(lambda s: np.tanh(8 * s) * .3)
        lifted = through(lambda s: bump(np.tanh(8 * s)) * .3)
        self.assertLess(lifted.distortion_db, plain.distortion_db - 5)
        self.assertAlmostEqual(lifted.cleans_up_db, plain.cleans_up_db, delta=.5)

    def test_a_drive_that_cuts_lows_before_it_clips_shows_in_the_higher_tone(self):
        # B1: one 196 Hz tone reads such a pedal as nearly clean; the 740 Hz tone is its own, separately named figure.
        cut = signal.butter(1, 720, "high", fs=RATE, output="sos")
        found = through(lambda s: np.tanh(8 * signal.sosfilt(cut, s)))
        self.assertLess(found.distortion_db, -30)
        self.assertGreater(found.distortion_740hz_db, found.distortion_db + 10)
        same = through(lambda s: np.tanh(8 * s))
        self.assertAlmostEqual(same.distortion_740hz_db, same.distortion_db, delta=1.)


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

    def test_even_against_odd_is_bounded_and_none_without_harmonics(self):
        # A9: with no odd harmonics at all this read +116 dB, and 0 ("balanced") with none of either.
        self.assertEqual(through(adding({2: -30})).even_odd_db, 40.)
        self.assertEqual(through(adding({3: -30})).even_odd_db, -40.)
        self.assertAlmostEqual(through(adding({2: -30, 3: -40})).even_odd_db, 10., delta=.3)
        self.assertIsNone(through(lambda s: s).even_odd_db)

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
        found = through(noisy(-40, seed=2))
        # The tone (.25 peak) against white noise of sigma .01 in 60 Hz to 4 kHz.
        expected = 10 * np.log10((.25 ** 2 / 2) / (.01 ** 2 * (4000 - 60) / (RATE / 2)))
        self.assertAlmostEqual(found.hnr_db, expected, delta=1.5)

    def test_the_record_keeps_every_harmonic_and_says_which_layout_made_it(self):
        record = through(adding({3: -20})).to_record()
        self.assertEqual(len(record["harmonics_db"]), 9)
        for key in ("low_order_db", "high_order_db", "weighted_distortion_db", "harmonic_centroid", "tristimulus3",
                    "hnr_db", "distortion_db", "cleans_up_db", "distortion_740hz_db", "late_energy_db",
                    "decay_rt60_s", "click_ms"):
            self.assertIn(key, record)
        self.assertEqual(record["version"], 2)
        self.assertNotIn("decay_s", record)       # the first layout's figure is another quantity, under its own name


class ClickTest(unittest.TestCase):
    def test_an_echo_is_found_with_its_delay_and_level(self):
        found = through(delay)
        self.assertAlmostEqual(found.echo_ms, 350, delta=3)
        self.assertAlmostEqual(found.echo_db, -6, delta=1)
        self.assertAlmostEqual(found.last_echo_ms, 350, delta=3)
        self.assertIsNone(through(lambda s: s).echo_ms)

    def test_decay_is_the_tails_own_however_loud_the_tail_is(self):
        # A5: counted from the direct click, a reverb 20 dB under it read a quarter of its decay time.
        for seconds in (.5, 1., 2.):
            for wet_db in (0., -10., -20.):
                found = through(reverberant(seconds, wet_db))
                self.assertAlmostEqual(found.decay_rt60_s, seconds, delta=.1 * seconds, msg=(seconds, wet_db))
                self.assertFalse(found.rings_on)
                self.assertIsNone(found.echo_ms)

    def test_the_energy_after_the_direct_sound_is_reported_by_itself(self):
        self.assertAlmostEqual(through(reverberant(1., -10.)).late_energy_db, -12.6, delta=1.5)   # less the first 40 ms
        self.assertAlmostEqual(through(reverberant(1., -20.)).late_energy_db, -22.5, delta=1.5)
        self.assertAlmostEqual(through(delayed(.5, .1)).late_energy_db, -20., delta=.5)
        self.assertEqual(through(lambda s: s).late_energy_db, -70.)
        cab = np.random.default_rng(5).standard_normal(960) * np.exp(-np.arange(960) / 150.)
        self.assertEqual(through(lambda s: signal.fftconvolve(s, cab)[:len(s)] * .1).late_energy_db, -70.)

    def test_one_repeat_has_no_decay_time(self):
        # A5: a single echo read three times its delay ("about 1.5 s to die away" for one quiet repeat at 500 ms).
        for chain in (delay, delayed(.5, .1)):
            found = through(chain)
            self.assertIsNone(found.decay_rt60_s)
            self.assertFalse(found.rings_on)

    def test_repeats_that_die_away_have_one(self):
        found = through(delayed(.35, .5, .5))                  # 6 dB a repeat: 60 dB in 3.5 s
        self.assertAlmostEqual(found.decay_rt60_s, 3.5, delta=.5)
        self.assertAlmostEqual(found.last_echo_ms, 1400, delta=5)       # -6, -12, -18, -24 dB: within 30 dB

    def test_noise_is_not_a_decay(self):
        # A5: a dry chain with a steady noise floor read 4.7 s (-80 dBFS) and 5.9 s (-70 dBFS).
        for dbfs in (-90, -80, -70, -60):
            found = through(noisy(dbfs))
            self.assertIsNone(found.decay_rt60_s, dbfs)
            self.assertEqual(found.late_energy_db, -70., dbfs)
            self.assertIsNone(found.echo_ms)

    def test_a_chain_still_ringing_after_two_seconds_says_so_and_gives_no_number(self):
        found = through(reverberant(8., -10.))
        self.assertIsNone(found.decay_rt60_s)
        self.assertTrue(found.rings_on)
        self.assertIsNone(found.intermod_db)
        self.assertIsNone(found.level_wobble)

    def test_the_clicks_arrival_is_measured_and_times_are_counted_from_it(self):
        sent = delay(np.tanh(8 * TONES))
        late = np.concatenate([np.zeros(480), sent])[:len(sent)]            # a chain 10 ms slow
        early = np.concatenate([sent[240:], np.zeros(240)])                  # 5 ms before the round trip allows
        self.assertAlmostEqual(analyse(sent[:, None]).click_ms, 0., delta=.1)
        found = analyse(late[:, None])
        self.assertAlmostEqual(found.click_ms, 10., delta=.1)
        self.assertAlmostEqual(found.echo_ms, 350, delta=3)
        self.assertAlmostEqual(analyse(early[:, None]).click_ms, -5., delta=.1)

    def test_a_click_lost_in_what_is_already_sounding_measures_nothing(self):
        found = through(noisy(-20))
        for value in (found.click_ms, found.echo_ms, found.late_energy_db, found.decay_rt60_s, found.rings_on):
            self.assertIsNone(value)


class SteadyTest(unittest.TestCase):
    """Intermodulation and wobble are read only once a tone's level has stopped changing."""

    def test_a_delay_alone_has_no_intermodulation_and_no_wobble(self):
        # A4: a clean 503 ms repeat at -6 dB read intermodulation -45 dB, level wobble 28 % and pitch wobble 6 cents.
        for seconds, gain in ((.503, .5), (.6, .5), (.45, .5), (.503, .112), (.8, .35), (.25, .5)):
            found = through(delayed(seconds, gain))
            self.assertLessEqual(found.intermod_db, -65, (seconds, gain))
            self.assertLess(found.level_wobble, 2., (seconds, gain))
            self.assertLess(found.pitch_wobble, 1., (seconds, gain))
            self.assertLessEqual(found.distortion_db, -65)

    def test_behind_a_repeat_too_late_for_the_tone_it_is_not_measurable(self):
        found = through(delayed(1.0, .5))                      # the two notes last 1.5 s
        self.assertIsNone(found.intermod_db)
        self.assertLess(found.level_wobble, 2.)                # the held tone is long enough
        self.assertLess(found.wobble_s, 3.)
        long = through(delayed(.5, .5, .7))                    # repeats within 30 dB for seconds
        self.assertIsNone(long.intermod_db)
        self.assertIsNone(long.level_wobble)
        self.assertIsNone(long.pitch_wobble)

    def test_a_reverb_alone_has_none_either(self):
        for seconds, wet_db in ((1., 0.), (2., -10.), (4., -10.)):
            found = through(reverberant(seconds, wet_db))
            if found.intermod_db is not None:
                self.assertLessEqual(found.intermod_db, -65, (seconds, wet_db))
            self.assertLess(found.level_wobble, 6., (seconds, wet_db))
            self.assertLess(found.pitch_wobble, 1., (seconds, wet_db))

    def test_real_intermodulation_is_still_found_behind_a_delay(self):
        alone = through(lambda s: np.tanh(8 * s))
        behind = through(lambda s: delay(np.tanh(8 * s), .25, .3))
        self.assertGreater(alone.intermod_db, -25)
        self.assertAlmostEqual(behind.intermod_db, alone.intermod_db, delta=3.)

    def test_mains_hum_is_not_intermodulation(self):
        # C2: with the notes a third apart, f2 - f1 was 50.9 Hz, and hum at -50 dBFS read intermodulation -34 dB.
        hum = sum(10 ** (-50 / 20) / k * np.sin(2 * np.pi * 50 * k * N / RATE + k) for k in range(1, 9))
        found = through(lambda s: s + hum)
        self.assertLessEqual(found.intermod_db, -65)
        self.assertLessEqual(found.distortion_db, -65)


class WobbleTest(unittest.TestCase):
    def test_tremolo_and_chorus_wobble_and_a_plain_tone_does_not(self):
        tremolo = through(lambda s: s * (1 - .25 * (1 + np.sin(2 * np.pi * 5 * N / RATE))))
        chorus = through(lambda s: .5 * s + .5 * np.interp(N - (.007 + .002 * np.sin(2 * np.pi * .8 * N / RATE)) * RATE,
                                                            N, s))
        plain = through(lambda s: s)
        self.assertGreater(tremolo.level_wobble, 30)
        self.assertGreater(chorus.pitch_wobble, 5)
        self.assertLess(plain.level_wobble, 1)
        self.assertLess(plain.pitch_wobble, 1)
        self.assertEqual(plain.wobble_s, 3.)

    def test_a_slow_tremolo_reads_as_deep_as_a_fast_one(self):
        # A7: over half a second with a curve taken off, 0.5 Hz read 1 %, 1 Hz 6 % and 2 Hz 22 % of a true 66.7 %.
        for rate in (.5, 1., 2., 3., 5., 8., 12.):
            for phase in (0., 1., 2.5):
                swing = 1 - .25 * (1 + np.sin(2 * np.pi * rate * N / RATE + phase))      # between 0.5 and 1
                self.assertAlmostEqual(through(lambda s: s * swing).level_wobble, 66.7, delta=7., msg=(rate, phase))

    def test_a_slow_vibrato_reads_its_true_depth(self):
        for rate in (.5, 1., 2., 5.):
            bent = lambda s: np.interp(N - (.005 + .0005 * np.sin(2 * np.pi * rate * N / RATE)) * RATE, N, s)
            true = 1200 * np.log2(1 + .0005 * 2 * np.pi * rate) / np.sqrt(2)
            self.assertAlmostEqual(through(bent).pitch_wobble, true, delta=.1 * true + .1, msg=rate)


class LevelTest(unittest.TestCase):
    def test_the_tone_is_played_at_a_firmly_held_note(self):
        t = np.arange(RATE * 2) / RATE
        phrase = (.3 * np.sin(2 * np.pi * 196 * t) * np.exp(-2 * (t % .5)))[:, None]
        self.assertAlmostEqual(held_note_peak(phrase), .3, delta=.06)
        self.assertAlmostEqual(float(np.abs(test_signal(.3)).max()), .3, delta=.01)


if __name__ == "__main__":
    unittest.main()
