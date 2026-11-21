import unittest

from lab.sound import knobs, words


def said(text):
    reading = words.read(text)
    return None if reading is None else (reading.said, reading.remedies[0].moves)


class LexiconTest(unittest.TestCase):
    def test_every_remedy_uses_a_knob_within_its_range(self):
        for word, entry in words.LEXICON.items():
            remedies = (*entry.away, *entry.toward, *(item for group in (entry.when or {}).values() for item in group))
            self.assertTrue(remedies, word)
            for remedy in remedies:
                self.assertTrue(remedy.moves and set(remedy.moves) <= set(knobs.KNOBS), (word, remedy))
                self.assertTrue(all(knobs.KNOBS[knob][1] <= step <= 1 for knob, step in remedy.moves.items()))
        self.assertTrue(set(words.ALIASES.values()) <= set(words.LEXICON))

    def test_microphones_serve_brightness_and_texture_in_the_measured_direction(self):
        # Measured on the unit: a higher POSITION is toward the edge, darker and smoother. Brightness and Texture
        # turned up lower it (toward the centre); no knob is the microphone itself.
        for knob in ("brightness", "texture"):
            moves = [delta for _, name, delta in knobs.KNOBS[knob][2] if name == "POSITION"]
            self.assertTrue(moves and all(delta < 0 for delta in moves), knob)
        self.assertNotIn("mic", knobs.KNOBS)

    def test_warm_is_round_against_rough_and_bright_is_its_own_axis(self):
        self.assertEqual(said("warmer"), ("Warmer", {"texture": -.3}))
        self.assertEqual(said("rougher"), ("Rougher", {"texture": .3}))
        self.assertEqual(said("too harsh")[1], {"presence": -.3})
        self.assertEqual(words.read("too harsh").remedies[1].moves, {"texture": -.3})
        self.assertEqual(said("darker"), ("Darker", {"brightness": -.35}))

    def test_nothing_from_the_old_package_is_imported(self):
        source = words.__file__
        with open(source, encoding="utf-8") as handle:
            self.assertNotIn("tone_lab", handle.read())


class ReadTest(unittest.TestCase):
    def test_a_complaint_is_read_with_the_way_of_playing_it_names(self):
        self.assertEqual(said("The low end gets muddy when I palm mute."), ("Muddy on palm mutes", {"tightness": .35}))
        self.assertEqual(said("the chords get muddy"), ("Muddy on chords", {"drive": -.25}))
        self.assertEqual(said("my leads sound thin"), ("Thin on single notes", {"mids": .3}))
        self.assertEqual(said("high notes are piercing"), ("Harsh on single notes", {"presence": -.3, "mids": .2}))
        self.assertEqual(said("it's harsh when I dig in"),
                         ("Harsh when you dig in", {"compression": .3, "presence": -.15}))
        self.assertEqual(said("muddy"), ("Muddy", {"tightness": .3}))

    def test_wanting_and_complaining_point_opposite_ways(self):
        self.assertEqual(said("bright"), ("Brighter", {"brightness": .3}))
        self.assertEqual(said("it sounds bright"), ("Bright", {"brightness": -.3}))
        self.assertEqual(said("too tight"), ("Tight", {"tightness": -.3}))
        self.assertEqual(said("not tight enough"), ("Tighter", {"tightness": .3}))
        self.assertEqual(said("thin"), ("Thin", {"body": .3}))
        self.assertEqual(said("make it thin"), ("Thinner", {"body": -.3}))
        self.assertEqual(said("less harsh"), ("Harsh", {"presence": -.3}))
        self.assertEqual(said("more scooped"), ("More scooped", {"mids": -.3}))

    def test_slang_and_phrases_reach_the_same_word(self):
        self.assertEqual(said("ice pick"), ("Harsh", {"presence": -.3}))
        self.assertEqual(said("it gets lost in the mix"), ("Buried", {"band_fit": .5}))
        self.assertEqual(said("the notes die too fast"), ("More sustain", {"sustain": .3}))
        self.assertEqual(said("make it sing"), ("More singing", {"sustain": .3, "mids": .2}))
        self.assertEqual(said("needs more clarity"), ("Clearer", {"drive": -.2}))
        self.assertEqual(said("too much fizz"), ("Fizzy", {"texture": -.4}))

    def test_amounts_scale_the_step(self):
        self.assertEqual(said("a bit fizzy"), ("Fizzy", {"texture": -.2}))
        self.assertEqual(said("way too dark"), ("Dark", {"brightness": .6}))

    def test_questions_and_longer_requests_are_left_to_the_assistant(self):
        for text in ("why is it muddy", "play something muddy for me", "muddy but only in the second verse",
                     "make a tight metal tone", "much less reverb", "undo"):
            self.assertIsNone(words.read(text), text)

    def test_no_of_a_fault_asks_for_less_and_no_of_a_quality_says_it_is_missing(self):
        for text, name, move in (("no fizz", "Fizzy", {"texture": -.4}), ("no mud", "Muddy", {"tightness": .3}),
                                 ("no boom", "Boomy", {"body": -.3}), ("no bite", "More bite", {"punch": .3}),
                                 ("no sustain", "More sustain", {"sustain": .3})):
            reading = words.read(text)
            self.assertEqual((reading.said, dict(reading.remedies[0].moves)), (name, move), text)
        self.assertEqual(words.read("no fizz").remedies, words.read("less fizz").remedies)


class RemedyOrderTest(unittest.TestCase):
    def test_the_way_of_playing_puts_its_own_remedies_first_and_the_rest_follow(self):
        reading = words.read("the low end gets muddy when I palm mute")
        self.assertEqual((reading.word, reading.technique, reading.wanted), ("muddy", "palm", False))
        self.assertTrue(reading.remedies[0].say.startswith("leaner lows in the drive and amp"))
        self.assertEqual(reading.remedies[1].moves, {"drive": -.2, "tightness": .15})
        self.assertIn("less gain", reading.remedies[1].say)
        # The general remedies follow, less any that move exactly the same knobs as one already offered.
        self.assertEqual([remedy.moves for remedy in reading.remedies[2:]],
                         [{"drive": -.2}, {"body": -.3}])
        self.assertIn("a boost in front", reading.tip)  # What no knob here does.

    def test_only_remedies_the_chain_can_perform_are_offered(self):
        reading = words.read("fizzy")
        self.assertEqual([tuple(remedy.moves) for remedy in words.usable(reading, None)],
                         [("texture",), ("drive",), ("presence",)])
        self.assertEqual([tuple(remedy.moves) for remedy in words.usable(reading, {"drive", "presence"})],
                         [("drive",), ("presence",)])
        self.assertEqual(words.usable(reading, {"reverb"}), ())
        # With only gain to work with, the remedy players try third is the one that applies.
        self.assertEqual(words.usable(words.read("tighter"), {"drive"})[0].moves, {"drive": -.15})
        self.assertEqual(words.usable(words.read("tighter"), {"reverb"}), ())

    def test_a_tip_comes_with_a_complaint_but_not_with_a_wish(self):
        self.assertIn("listening level", words.read("thin").tip)
        self.assertEqual(words.read("make it thin").tip, "")
        self.assertIn("Headphones", words.read("more sustain").tip)


class DegreeTest(unittest.TestCase):
    """How much, before a plain word, says how the sound is; before a comparative, or after "make it", how much
    is wanted."""

    def test_a_degree_before_a_plain_word_describes_a_fault_when_the_word_can_be_one(self):
        for text, name, move in (("really dark", "Dark", {"brightness": .45}),
                                 ("very bright", "Bright", {"brightness": -.45}),
                                 ("a bit bright", "Bright", {"brightness": -.15}),
                                 ("so dark", "Dark", {"brightness": .3}),
                                 ("kind of dark", "Dark", {"brightness": .3}),
                                 ("it's really harsh", "Harsh", {"presence": -.45}),
                                 ("very clean", "Clean", {"drive": .45}),
                                 ("slightly tight", "Tight", {"tightness": -.15})):
            self.assertEqual(said(text), (name, move), text)
        # A word with no cure cannot be a fault: it is asked for.
        self.assertEqual(said("really airy"), ("Airier", {"brightness": .6, "presence": .3}))

    def test_a_degree_before_a_comparative_or_after_make_it_scales_a_wish(self):
        for text, name, move in (("much darker", "Darker", {"brightness": -.59}),
                                 ("a bit brighter", "Brighter", {"brightness": .15}),
                                 ("make it really bright", "Brighter", {"brightness": .45}),
                                 ("make it very clean", "Cleaner", {"drive": -.45}),
                                 ("I want it really tight", "Tighter", {"tightness": .45}),
                                 ("make it a bit dark", "Darker", {"brightness": -.17})):
            self.assertEqual(said(text), (name, move), text)


class TurnedOverTest(unittest.TestCase):
    def test_every_knob_has_its_words_down_and_up(self):
        self.assertEqual(set(words.TURNS), set(knobs.KNOBS))
        self.assertTrue(all(len(pair) == 2 and all(pair) for pair in words.TURNS.values()))

    def test_a_remedy_made_by_turning_another_over_says_what_it_does(self):
        for text, moves, say in (("too smooth", {"bloom": -.3, "texture": .2}, "less bloom and a rougher top"),
                                 ("too chunky", {"tightness": -.25, "drive": -.15}, "fuller lows and less gain"),
                                 ("too dynamic", {"drive": .2}, "more gain"),
                                 ("more fizzy", {"texture": .4}, "a rougher top"),
                                 ("too airy", {"brightness": -.4, "presence": -.2}, "a darker top and less presence"),
                                 ("more noisy", {"gate": -.3}, "a lower gate threshold")):
            remedy = words.read(text).remedies[0]
            self.assertEqual((remedy.moves, remedy.say), (moves, say), text)
        # Whatever the word, a remedy turned over is said in the words of its own moves.
        for word, entry in words.LEXICON.items():
            for text, missing in ((f"too {word}", not entry.away), (f"more {word}", not entry.toward)):
                if missing:
                    remedy = words.read(text).remedies[0]
                    self.assertEqual(remedy.say, words.saying(remedy.moves), text)
                    self.assertNotIn("opposite", remedy.say)


class OneTableTest(unittest.TestCase):
    def test_what_read_reads_direction_leaves_alone(self):
        forms = ("more {}", "less {}", "too much {}", "not enough {}", "a bit more {}", "way too much {}")
        for quantity in words.QUANTITIES:
            for form in forms:
                text = form.format(quantity)
                self.assertIsNone(words.read(text), text)             # no word of the lexicon is also a quantity
                self.assertIsNotNone(words.direction(text), text)
        for word in words.ALIASES:
            for form in ("{}", "too {}", "more {}", "less {}", "not {} enough", "too much {}"):
                text = form.format(word)
                if words.read(text) is not None:
                    self.assertIsNone(words.direction(text), text)

    def test_words_the_second_table_alone_knew_are_read_here(self):
        self.assertEqual(said("narrower"), ("Narrower", {"width": -.3}))
        self.assertEqual(said("too distorted"), ("Dirty", {"drive": -.3}))
        self.assertEqual(said("heavier"), ("Fatter", {"body": .3}))     # one reading, the lexicon's
        self.assertEqual(said("more attack"), ("More bite", {"punch": .3}))
        self.assertEqual(words.direction("way too much gain"), {"drive": -.6})
        self.assertEqual(words.direction("more warmth"), {"texture": -.3})


if __name__ == "__main__":
    unittest.main()
