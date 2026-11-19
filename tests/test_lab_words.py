import unittest

from lab import knobs, words


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

    def test_microphone_moves_follow_the_measured_direction(self):
        # Measured on the unit: a higher POSITION is toward the edge and darker, and the knob raises POSITION.
        self.assertTrue(all(delta > 0 for _, name, delta in knobs.KNOBS["mic"][2] if name == "POSITION"))
        darker = [remedy.moves["mic"] for word, side in (("fizzy", "away"), ("bright", "away"), ("dark", "toward"))
                  for remedy in getattr(words.LEXICON[word], side) if "mic" in remedy.moves]
        brighter = [remedy.moves["mic"] for word, side in (("bright", "toward"), ("dark", "away"))
                    for remedy in getattr(words.LEXICON[word], side) if "mic" in remedy.moves]
        self.assertEqual((len(darker), len(brighter)), (3, 2))
        self.assertTrue(all(move > 0 for move in darker) and all(move < 0 for move in brighter))

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
        self.assertEqual(said("bright"), ("Brighter", {"warmth": -.3}))
        self.assertEqual(said("it sounds bright"), ("Bright", {"warmth": .3}))
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
        self.assertEqual(said("too much fizz"), ("Fizzy", {"soft_top": .4}))

    def test_amounts_scale_the_step(self):
        self.assertEqual(said("a bit fizzy"), ("Fizzy", {"soft_top": .2}))
        self.assertEqual(said("way too dark"), ("Dark", {"warmth": -.6}))

    def test_questions_and_longer_requests_are_left_to_the_assistant(self):
        for text in ("why is it muddy", "play something muddy for me", "muddy but only in the second verse",
                     "make a tight metal tone", "much less reverb", "undo"):
            self.assertIsNone(words.read(text), text)

    def test_no_of_a_fault_asks_for_less_and_no_of_a_quality_says_it_is_missing(self):
        for text, name, move in (("no fizz", "Fizzy", {"soft_top": .4}), ("no mud", "Muddy", {"tightness": .3}),
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
                         [{"low_cut": .4}, {"drive": -.2}, {"body": -.3}])
        self.assertIn("a boost in front", reading.tip)  # What no knob here does.

    def test_only_remedies_the_chain_can_perform_are_offered(self):
        reading = words.read("fizzy")
        self.assertEqual([tuple(remedy.moves) for remedy in words.usable(reading, None)],
                         [("soft_top",), ("mic",), ("drive",), ("presence",)])
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


class BriefTest(unittest.TestCase):
    def test_the_assistant_is_briefed_on_the_chain_and_the_words(self):
        found = words.brief([4, 1011, 16005], "it gets muddy and harsh")
        self.assertEqual([block["model"] for block in found["loaded_gear"]], ["Rodent Drive", "UK C30 TopBoost"])
        self.assertIn("INVERTED", found["loaded_gear"][1]["controls"]["TONE CUT"])
        self.assertEqual([remark["word"] for remark in found["remarks"]], ["harsh", "muddy"])
        self.assertTrue(found["remarks"][0]["players_try_in_order"][0].startswith("less presence"))
        self.assertEqual(words.brief([16005], "hello"), {})


if __name__ == "__main__":
    unittest.main()
