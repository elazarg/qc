"""One measurement for everything, and the words for what a chain did to a phrase."""

import unittest

import numpy as np
from scipy import signal as dsp

from lab.hearing import measure, probe
from lab.hearing.audio import RATE, onset, click


def note(seconds=1.5, pitch=196.):
    time = np.arange(round(seconds * RATE)) / RATE
    return (.25 * sum(np.sin(2 * np.pi * pitch * k * time) / k for k in (1, 2, 3)) * np.exp(-3 * time))[:, None]


def through(chain, dry):
    silence = np.full((RATE // 2, 1), 1e-7)
    return dry, chain(dry), chain(dry * measure.SOFTER), silence


def sine(pitch, peak=.5, seconds=1.):
    return (peak * np.sin(2 * np.pi * pitch * np.arange(round(seconds * RATE)) / RATE))[:, None]


def shelf(corner, gain_db, high=True):
    """A shelving filter (RBJ): `gain_db` above the corner, or below it."""
    a = 10 ** (gain_db / 40)
    w = 2 * np.pi * corner / RATE
    alpha, c = np.sin(w) / 2 * np.sqrt(2), np.cos(w)
    root = 2 * np.sqrt(a) * alpha
    sign = 1 if high else -1
    b = [a * ((a + 1) + sign * (a - 1) * c + root), -sign * 2 * a * ((a - 1) + sign * (a + 1) * c),
         a * ((a + 1) + sign * (a - 1) * c - root)]
    d = [(a + 1) - sign * (a - 1) * c + root, sign * 2 * ((a - 1) - sign * (a + 1) * c), (a + 1) - sign * (a - 1) * c - root]
    return lambda sound: dsp.lfilter(np.array(b) / d[0], np.array(d) / d[0], sound, axis=0)


def guitar(seconds=3.5, pitches=(110., 146.8, 196., 246.9, 329.6, 196.)):
    """A made-up dry guitar: notes half a second apart, partials falling off to 15 kHz."""
    total = round(seconds * RATE)
    out = np.zeros(total)
    for start, pitch in zip((.25, .75, 1.25, 1.75, 2.25, 2.75), pitches):
        begin = round(start * RATE)
        time = np.arange(total - begin) / RATE
        tone = sum(np.sin(2 * np.pi * pitch * k * time + k * k) / k ** 1.5 for k in range(1, 41) if pitch * k < 15000)
        out[begin:] += .1 * tone * np.exp(-3 * time)
    return out[:, None]


class MeasureTest(unittest.TestCase):
    def test_level_peak_and_spectrum_of_a_sine(self):
        found = measure.measure(sine(1000))
        self.assertAlmostEqual(found.rms_db, -9.03, places=1)
        self.assertAlmostEqual(found.peak_db, -6.02, places=1)
        self.assertAlmostEqual(found.crest_db, 3.01, places=1)
        self.assertEqual(int(np.argmax(found.bands_db)), measure.CENTRES.index(1000.))
        self.assertAlmostEqual(found.centroid_hz, 1000, delta=20)
        self.assertAlmostEqual(found.lufs, -9.05, delta=.1)

    def test_opposite_channels_are_measured_and_called_wide(self):
        dry = note()
        found = measure.measure(np.hstack([dry, -dry]))
        self.assertAlmostEqual(found.rms_db, measure.level(dry), places=1)
        self.assertGreater(measure.measure(np.hstack([dry, np.roll(dry, 4800)])).width, .5)
        self.assertLess(found.width, .01)  # Opposite phase is still one source: not wide.

    def test_one_convention_for_several_channels_and_one_crest(self):
        # B6: the level was the average channel's while the spectrum added the channels (3 dB apart for the same
        # sound on both), and there were two crests (one read a sine on one side of a stereo pair as 6 dB).
        one, both, side = sine(1000), np.hstack([sine(1000)] * 2), np.hstack([sine(1000), 0 * sine(1000)])
        mono, stereo, sided = (measure.measure(item) for item in (one, both, side))
        total = lambda found: 10 * np.log10(np.sum(10 ** (np.array(found.bands_db) / 10)))
        self.assertAlmostEqual(stereo.rms_db, mono.rms_db, places=2)
        self.assertAlmostEqual(total(stereo), stereo.rms_db, delta=.1)
        self.assertAlmostEqual(total(mono), mono.rms_db, delta=.1)
        for found in (mono, stereo, sided):
            self.assertAlmostEqual(found.crest_db, 3.01, places=1)
        self.assertAlmostEqual(measure.crest(side), measure.heard(one, side, side * measure.SOFTER, one[:4800]).crest_db,
                               places=2)
        self.assertAlmostEqual(stereo.lufs - mono.lufs, 3.01, delta=.05)        # the loudness standard adds channels

    def test_a_dc_offset_is_not_level(self):
        self.assertAlmostEqual(measure.level(sine(1000, .1) + .2), measure.level(sine(1000, .1)), places=2)

    def test_nothing_to_measure_is_measured_as_nothing(self):
        # C4: an empty array raised IndexError.
        found = measure.measure(np.zeros((0, 1)))
        self.assertEqual((found.seconds, found.lufs, found.clipped), (0., None, 0))
        self.assertEqual(measure.level(np.zeros((0, 2))), -200.)
        self.assertIsNone(measure.measure(sine(1000, seconds=.2)).lufs)          # under 0.4 s there is no loudness

    def test_the_gap_between_two_sounds_ignores_their_level(self):
        dry = np.random.default_rng(1).normal(0, .05, (RATE, 1))
        dark = dsp.sosfilt(dsp.butter(2, 800, "low", fs=RATE, output="sos"), dry, axis=0)
        self.assertLess(min(measure.gap(measure.measure(dry), measure.measure(dark * 4))), -6)
        self.assertLess(max(map(abs, measure.gap(measure.measure(dry), measure.measure(dry * 4)))), .05)
        named = measure.band_gaps(measure.measure(dry), measure.measure(dark * 4))
        self.assertEqual(list(named), [name for name, _, _, _ in measure.BANDS])
        self.assertLess(named["6-12 kHz"], named["60-200 Hz"] - 20)

    def test_every_band_is_named_by_its_edges(self):
        # B5: one table. A tone just inside an edge is in the band, one just outside is in the next.
        for name, low, high, _ in measure.BANDS:
            for pitch in (low * 1.06, high / 1.06):
                levels = measure.band_levels_db(sine(pitch, seconds=2.))
                levels.pop("highs")
                self.assertEqual(max(levels, key=levels.get), name, pitch)
        self.assertEqual([measure.CENTRES[i] for i in range(len(measure.CENTRES))
                          if measure.CENTRES[i] < measure.LOW_END_BELOW_HZ][-1], 198.4)   # low_end_db reaches 223 Hz


class HeardTest(unittest.TestCase):
    """From the phrase: compression, brightness, tail. Distortion comes only from the test tones."""

    def test_compression_is_how_much_of_a_12_db_drop_fails_to_come_back(self):
        self.assertEqual(measure.heard(*through(lambda sound: sound * .5, note())).compressed, "none")
        squeeze = lambda sound: sound * (.02 / max(float(np.sqrt(np.mean(sound ** 2))), 1e-9))
        found = measure.heard(*through(squeeze, note(4.)))
        self.assertGreater(found.compression, .82)
        self.assertEqual(found.compressed, "squashed")

    def test_a_chain_that_widens_the_drop_reads_below_zero(self):
        # B3: an expander's 18 dB for 12 read 0.00, "none".
        expander = lambda sound: sound * np.abs(sound) ** .5 * 3
        found = measure.heard(*through(expander, note()))
        self.assertAlmostEqual(found.compression, -.5, delta=.05)
        self.assertEqual(found.compressed, "expanding")
        self.assertTrue(any("18 dB comes back: expanding" in line for line in measure.measured_lines(found.to_record())))

    def test_a_softer_pass_that_did_not_come_through_is_a_gate_closing(self):
        dry = note()
        found = measure.heard(dry, dry, np.zeros_like(dry), np.zeros((RATE // 2, 1)), soft_gated=True)
        self.assertEqual((found.compression, found.soft_gated, found.compressed), (-1., True, "expanding"))
        self.assertIsNone(found.attack_soft_ms)
        self.assertIsNone(found.first_rise_soft_ms)
        lines = measure.measured_lines(found.to_record())
        self.assertIn("Gate: the phrase played 12 dB softer did not come through.", lines)
        self.assertTrue(any("24 dB or more comes back" in line for line in lines))

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

    def test_the_tail_word_is_the_chains_not_the_phrase_endings(self):
        # A6: one reverb (60 dB in 1 s, 10 dB under the direct sound) was "wet" after a phrase cut mid-note, "a
        # touch of room" after one whose last note rang 0.75 s, and "dry" after one that ended in silence.
        length = round(1.6 * RATE)
        response = np.random.default_rng(1).standard_normal(length) * np.exp(-np.arange(length) / RATE * 6.9)
        response *= 10 ** (-10 / 20) / np.sqrt(np.sum(response ** 2))
        response[0] += 1
        room = lambda sound: dsp.fftconvolve(sound, response)[:len(sound)]
        record = probe.analyse(room(probe.test_signal(.25)[:, 0].astype(np.float64))[:, None]).to_record()
        whole = guitar()[:, 0]
        endings = {"cut mid-note": whole[:round(2.9 * RATE)], "rings 0.75 s": whole,
                   "then a second of silence": np.concatenate([whole, np.zeros(RATE)])}
        words, tails = set(), []
        for dry in endings.values():
            full = room(np.concatenate([dry, np.zeros(round(1.5 * RATE))]))
            loud, after = full[:len(dry), None], full[len(dry):, None]
            found = measure.heard(dry[:, None], loud, loud * measure.SOFTER, after, probe=record)
            words.add(found.tail)
            tails.append(found.tail_db)
        self.assertEqual(words, {"ambient"})
        self.assertGreater(max(tails) - min(tails), 30)          # tail_db is of this phrase's ending, and says so
        lines = measure.measured_lines(found.to_record())
        self.assertTrue(any(line.startswith("Echo and reverb: after a click") and "ambient" in line and
                            "1.0 s to fall 60 dB" in line for line in lines))
        self.assertTrue(any("depends on how the phrase ends" in line for line in lines))
        # The same chain with no test signals played: the word falls back to what follows the phrase.
        self.assertEqual(measure.heard(dry[:, None], loud, loud * measure.SOFTER, np.zeros((RATE, 1))).tail, "dry")

    def test_dark_is_dark(self):
        lows = dsp.butter(2, 300, "low", fs=RATE, output="sos")
        found = measure.heard(*through(lambda sound: dsp.sosfilt(lows, sound, axis=0), note()))
        self.assertEqual((found.tone, found.compressed), ("dark", "none"))

    def test_the_brightness_word_follows_the_highs_not_the_low_end(self):
        # A8: from the spectral centre, 12 dB more above 3 kHz was "balanced" and 6 dB more below 200 Hz was "dark".
        dry = guitar()
        bright = measure.heard(*through(shelf(3000, 12), dry))
        self.assertGreater(bright.brightness_db, 3)
        self.assertEqual(bright.tone, "bright")
        fuller = measure.heard(*through(shelf(200, 6, high=False), dry))
        self.assertAlmostEqual(fuller.brightness_db, 0, delta=1.5)
        self.assertEqual(fuller.tone, "balanced")
        self.assertLess(fuller.brightness_oct, -.25)             # the spectral centre did fall: it is kept, as that
        thinner = measure.heard(*through(shelf(200, -6, high=False), dry))
        self.assertEqual(thinner.tone, "balanced")
        # Levels against the 200 Hz to 1 kHz band, not shares: the bands above the lows stay where they were.
        for found in (fuller, thinner):
            for name in ("1-3 kHz", "3-6 kHz", "6-12 kHz"):
                self.assertAlmostEqual(found.band_levels_db[name], 0, delta=1.5, msg=name)
        self.assertGreater(fuller.band_levels_db["60-200 Hz"], 2.5)
        self.assertLess(thinner.band_levels_db["60-200 Hz"], -2.5)
        self.assertGreater(bright.band_levels_db["6-12 kHz"], 9)
        line = next(line for line in measure.measured_lines(bright.to_record()) if line.startswith("Brightness:"))
        self.assertIn("energy above 1 kHz against 200 Hz to 1 kHz", line)
        self.assertIn("bright", line)

    def test_a_dry_phrase_with_nothing_above_1_khz_gives_no_brightness_figure(self):
        low = sine(150, .2, 2.) + sine(300, .1, 2.)
        found = measure.heard(low, low * .5, low * .5 * measure.SOFTER, np.zeros((RATE // 2, 1)))
        self.assertIsNone(found.brightness_db)
        self.assertEqual(found.tone, "balanced")                 # then, and in old records, from the spectral centre

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
    """How fast notes reach their peak, and the level by band, against the dry phrase."""

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
        self.assertAlmostEqual(found.first_rise_ms, 32, delta=5)
        self.assertAlmostEqual(found.first_rise_soft_ms, 64, delta=8)
        same = measure.heard(*through(lambda sound: sound * .5, dry))
        self.assertLess(same.attack_ms, 4)
        self.assertLess(same.attack_soft_ms, 4)
        self.assertLess(same.first_rise_ms, 4)

    def test_attack_is_timed_on_the_dry_phrases_notes_even_through_an_echo(self):
        dry = phrase(.04)
        late = round(.12 * RATE)
        echo = lambda sound: sound + .5 * np.vstack([np.zeros((late, 1)), sound[:-late]])
        found = measure.heard(*through(echo, dry))
        self.assertAlmostEqual(found.attack_ms, 32, delta=4)

    def test_the_picks_attack_is_the_first_rise_whatever_arrives_later(self):
        # B2: a repeat as loud as the note, landing in step with it 122 ms later, made "attack" 148 ms for 35.5,
        # and a reverb made an instant onset 76 ms: the time to the highest peak. The first rise is the pick's.
        dry = phrase(.04)
        starts = measure.onsets(dry)
        late = round(24 / 196 * RATE)                              # 24 periods of the notes' 196 Hz
        behind = dry + np.vstack([np.zeros((late, 1)), dry[:-late]])
        self.assertGreater(measure.attack_ms(behind, starts), 100)
        self.assertAlmostEqual(measure.first_rise_ms(behind, starts), measure.first_rise_ms(dry, starts), delta=4)
        self.assertAlmostEqual(measure.first_rise_ms(dry, starts), 32, delta=5)
        sharp = phrase()
        length = round(.3 * RATE)
        response = np.random.default_rng(0).standard_normal(length) * np.exp(-np.arange(length) / RATE * 6.9 / .5)
        response /= np.sqrt(np.sum(response ** 2))
        response[0] += 1
        washed = dsp.fftconvolve(sharp[:, 0], response)[:len(sharp), None]
        self.assertGreater(measure.attack_ms(washed, measure.onsets(sharp)), 20)
        self.assertLess(measure.first_rise_ms(washed, measure.onsets(sharp)), 8)

    def test_a_silent_phrase_has_no_attack(self):
        quiet = np.zeros((RATE, 1))
        self.assertEqual(measure.onsets(quiet), [])
        self.assertIsNone(measure.attack_ms(quiet, []))
        self.assertIsNone(measure.first_rise_ms(quiet, []))

    def test_level_by_band_follows_a_low_pass_and_ignores_level(self):
        dry = phrase()
        lows = dsp.butter(4, 2500, "low", fs=RATE, output="sos")
        found = measure.heard(*through(lambda sound: dsp.sosfilt(lows, sound, axis=0), dry)).band_levels_db
        self.assertEqual(set(found), {"60-200 Hz", "1-3 kHz", "3-6 kHz", "6-12 kHz"})
        self.assertGreater(found["1-3 kHz"], -3)
        self.assertLess(found["3-6 kHz"], -10)
        self.assertLess(found["6-12 kHz"], found["3-6 kHz"] - 10)
        louder = measure.heard(*through(lambda sound: sound * 3, dry)).band_levels_db
        self.assertLess(max(map(abs, louder.values())), .05)

    def test_the_record_carries_the_new_values(self):
        record = measure.heard(*through(lambda sound: sound, phrase())).to_record()
        for key in ("attack_ms", "attack_soft_ms", "first_rise_ms", "first_rise_soft_ms", "brightness_db",
                    "band_levels_db", "soft_gated", "texture", "compression", "probe"):
            self.assertIn(key, record)
        lines = measure.measured_lines(record)
        self.assertTrue(any(line.startswith("Pick attack: notes rise to their first peak in") for line in lines))
        self.assertTrue(any(line.startswith("Time to the highest peak within 250 ms of a note:") for line in lines))
        self.assertTrue(any(line.startswith("Level by band against your dry guitar") for line in lines))
        self.assertFalse(any(line.startswith("Attack") for line in lines))


class TextureTest(unittest.TestCase):
    """Round to rough from the probe's harmonic spread: an inference, calibrated on the unit's ladder."""

    def test_texture_follows_how_far_the_harmonics_spread(self):
        tones = probe.test_signal(.25)[:, 0].astype(np.float64)
        # Synthetic: a pure tone, soft clipping (mostly the 3rd harmonic) and hard clipping.
        chains = (lambda s: s, lambda s: np.tanh(4 * s), lambda s: np.clip(30 * s, -.5, .5))
        words = [measure.heard(*through(lambda sound: sound, note()),
                               probe=probe.analyse(chain(tones)[:, None]).to_record()).texture for chain in chains]
        self.assertEqual(words, ["round", "warm", "rough"])
        # Measured on the unit (tristimulus 3, order-weighted, low orders, high orders): Twin, Plexi at gain 5,
        # Plexi at gain 10, CA Duo Ch3 Modern, Solo 100 Lead.
        ladder = [(0, -42.5, -45.4, -70.), (.003, -11.8, -20.4, -25.2), (.0071, -8.8, -21.8, -21.4),
                  (.0056, -9.6, -25.4, -22.5), (.0072, -9.6, -25.6, -21.4)]
        self.assertEqual([measure.texture_word(*values) for values in ladder], list(measure.TEXTURE))
        self.assertEqual(measure.WORDS["texture"], measure.TEXTURE)

    def test_no_probe_or_an_old_probe_gives_no_texture(self):
        self.assertIsNone(measure.heard(*through(lambda sound: sound, note())).texture)
        old = {"distortion_db": -18., "distortion_soft_db": -39.}
        found = measure.heard(*through(lambda sound: sound, note()), probe=old)
        self.assertIsNone(found.texture)
        self.assertIn("texture", found.words())
        self.assertEqual(found.distortion, "crunch")

    def test_new_records_show_harmonic_orders_and_the_texture_word(self):
        tones = probe.test_signal(.25)[:, 0].astype(np.float64)
        found = measure.heard(*through(lambda sound: sound, phrase()),
                              probe=probe.analyse(np.tanh(10 * tones)[:, None]).to_record())
        lines = measure.measured_lines(found.to_record())
        orders = next(line for line in lines if line.startswith("Harmonic orders:"))
        self.assertIn("low 2nd-3rd", orders)
        self.assertIn("high 5th-10th", orders)
        self.assertTrue(any("texture warm" in line for line in lines))
        self.assertTrue(any("Played softer the harmonics fall" in line for line in lines))
        self.assertTrue(any(line.startswith("Distortion of a higher tone (740 Hz)") and "no word" in line
                            for line in lines))


class RecordsOfEveryAgeTest(unittest.TestCase):
    """A record made before a value was measured, or holding None for one that could not be, still displays."""

    OLD = {"compression": .5, "crest_db": 12., "brightness_oct": .1, "low_end_db": 0., "tail_db": -40.,
           "level_db": -20., "probe": {"distortion_db": -18., "distortion_soft_db": -39., "even_odd_db": 1.,
                                       "intermod_db": -12., "gated": False, "level_wobble": 0., "pitch_wobble": 0.,
                                       "echo_ms": None, "echo_db": None, "decay_s": 0.},
           "distortion": "crunch", "compressed": "moderate", "brightness": "balanced", "tail": "a touch of room"}

    def test_records_made_before_the_new_values_still_display(self):
        lines = measure.measured_lines(self.OLD)
        self.assertTrue(lines[0].startswith("Distortion:"))
        self.assertFalse(any(line.startswith(("Attack", "Pick attack", "Harmonic", "Level by band", "Brightness:"))
                             for line in lines))
        self.assertTrue(any(line.startswith("Spectral centre: +0.1 octave") and "balanced" in line for line in lines))
        self.assertTrue(any(line.startswith("Tail: 40 dB below the playing") and "a touch of room" in line
                            for line in lines))
        self.assertEqual(measure.summary(self.OLD), "crunch, balanced, a touch of room")

    def test_the_first_layouts_decay_is_shown_as_what_it_was_and_its_sentinel_as_words(self):
        wet = {**self.OLD, "probe": {**self.OLD["probe"], "decay_s": 1.05}}
        self.assertTrue(any(line == "Decay (the earlier measure, direct sound counted in): about 1.1 s."
                            for line in measure.measured_lines(wet)))
        ringing = {**self.OLD, "probe": {**self.OLD["probe"], "decay_s": 6.0}}
        self.assertTrue(any(line.endswith("still ringing after 2 s.") for line in measure.measured_lines(ringing)))
        shares = {**self.OLD, "brightness_bands_db": {"1-3 kHz": -3.1}}
        self.assertTrue(any(line.startswith("Share of the energy by band") for line in measure.measured_lines(shares)))

    def test_what_could_not_be_measured_is_said_not_printed_as_a_number(self):
        behind = {**self.OLD, "attack_ms": None, "brightness_db": None,
                  "probe": {"version": 2, "distortion_db": -70., "distortion_soft_db": -70., "cleans_up_db": None,
                            "distortion_740hz_db": None, "even_odd_db": None, "intermod_db": None, "gated": False,
                            "level_wobble": None, "pitch_wobble": None, "echo_ms": 500., "echo_db": -6.,
                            "late_energy_db": -4.4, "decay_rt60_s": None, "rings_on": True}}
        lines = measure.measured_lines(behind)
        self.assertIn("Intermodulation (two notes together): not measurable behind this echo or reverb.", lines)
        self.assertIn("Movement on a held note: not measurable behind this echo or reverb.", lines)
        self.assertTrue(any("still ringing two seconds on" in line for line in lines))
        self.assertFalse(any("None" in line for line in lines))
        old_heard = measure.Heard(.5, 12., .1, 0., -40., -20., probe=self.OLD["probe"])
        self.assertEqual((old_heard.tone, old_heard.tail), ("balanced", "a touch of room"))
        self.assertEqual(measure.measured_lines({}), measure.measured_lines(None))


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
        self.assertEqual(onset(-back), 7750)

    def test_a_click_that_does_not_stand_out_of_the_noise_is_refused(self):
        # C4: with a click 8 dB over the noise the "delay" came out as -4797 samples.
        back = np.random.default_rng(0).normal(0, .02, (RATE // 2, 2))
        back[RATE // 10 + 7750] = .05
        with self.assertRaisesRegex(RuntimeError, "did not stand out"):
            onset(back)
        back = np.random.default_rng(0).normal(0, .0005, (RATE // 2, 2))
        back[RATE // 10 + 7750] = .5
        self.assertEqual(onset(back), 7750)
        with self.assertRaisesRegex(RuntimeError, "Nothing came back"):
            onset(np.zeros((RATE // 2, 2)))


if __name__ == "__main__":
    unittest.main()
