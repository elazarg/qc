import unittest

from tone_lab import lore, styles


class ReadingRemarksTest(unittest.TestCase):
    def read(self, text):
        reading = lore.read(text)
        return None if reading is None else (reading.said, reading.remedies[0].moves)

    def test_every_remedy_uses_a_knob_the_desk_has(self):
        for word, entry in lore.LEXICON.items():
            remedies = (*entry.away, *entry.toward, *(item for group in (entry.when or {}).values() for item in group))
            self.assertTrue(remedies, word)
            for remedy in remedies:
                self.assertTrue(remedy.moves and set(remedy.moves) <= set(styles.KNOBS), (word, remedy))
                self.assertTrue(all(styles.KNOBS[knob][1] <= step <= 1 for knob, step in remedy.moves.items()))
        self.assertTrue(set(lore.ALIASES.values()) <= set(lore.LEXICON))

    def test_a_complaint_is_read_with_the_way_of_playing_it_names(self):
        self.assertEqual(self.read("The low end gets muddy when I palm mute."),
                         ("Muddy on palm mutes", {"tightness": .35}))
        self.assertEqual(self.read("the chords get muddy"), ("Muddy on chords", {"drive": -.25}))
        self.assertEqual(self.read("my leads sound thin"), ("Thin on single notes", {"mids": .3}))
        self.assertEqual(self.read("high notes are piercing"),
                         ("Harsh on single notes", {"presence": -.3, "mids": .2}))
        self.assertEqual(self.read("it's harsh when I dig in"),
                         ("Harsh when you dig in", {"compression": .3, "presence": -.15}))
        self.assertEqual(self.read("muddy"), ("Muddy", {"tightness": .3}))

    def test_wanting_and_complaining_point_opposite_ways(self):
        self.assertEqual(self.read("bright"), ("Brighter", {"warmth": -.3}))
        self.assertEqual(self.read("it sounds bright"), ("Bright", {"warmth": .3}))
        self.assertEqual(self.read("too tight"), ("Tight", {"tightness": -.3}))
        self.assertEqual(self.read("not tight enough"), ("Tighter", {"tightness": .3}))
        self.assertEqual(self.read("thin"), ("Thin", {"body": .3}))
        self.assertEqual(self.read("make it thin"), ("Thinner", {"body": -.3}))
        self.assertEqual(self.read("less harsh"), ("Harsh", {"presence": -.3}))
        self.assertEqual(self.read("more scooped"), ("More scooped", {"mids": -.3}))

    def test_slang_and_phrases_reach_the_same_word(self):
        self.assertEqual(self.read("ice pick"), ("Harsh", {"presence": -.3}))
        self.assertEqual(self.read("it gets lost in the mix"), ("Buried", {"band_fit": .5}))
        self.assertEqual(self.read("the notes die too fast"), ("More sustain", {"sustain": .3}))
        self.assertEqual(self.read("make it sing"), ("More singing", {"sustain": .3, "mids": .2}))
        self.assertEqual(self.read("needs more clarity"), ("Clearer", {"drive": -.2}))
        self.assertEqual(self.read("too much fizz"), ("Fizzy", {"soft_top": .4}))

    def test_amounts_scale_the_step(self):
        self.assertEqual(self.read("a bit fizzy"), ("Fizzy", {"soft_top": .2}))
        self.assertEqual(self.read("way too dark"), ("Dark", {"warmth": -.6}))

    def test_questions_and_longer_requests_are_left_to_the_assistant(self):
        for text in ("why is it muddy", "play something muddy for me", "muddy but only in the second verse",
                     "make a tight metal tone", "much less reverb", "undo"):
            self.assertIsNone(lore.read(text), text)

    def test_only_remedies_the_chain_can_perform_are_offered(self):
        reading = lore.read("fizzy")
        self.assertEqual([tuple(remedy.moves) for remedy in lore.usable(reading, None)],
                         [("soft_top",), ("mic",), ("drive",), ("presence",)])
        self.assertEqual([tuple(remedy.moves) for remedy in lore.usable(reading, {"drive", "presence"})],
                         [("drive",), ("presence",)])
        self.assertEqual(lore.usable(reading, {"reverb"}), ())

    def test_microphone_moves_follow_the_measured_direction(self):
        # Measured on the unit: a higher POSITION is toward the edge and darker, and the knob raises POSITION.
        self.assertTrue(all(delta > 0 for _, name, delta in styles.KNOBS["mic"][2] if name == "POSITION"))
        darker = [remedy.moves["mic"] for word, side in (("fizzy", "away"), ("bright", "away"), ("dark", "toward"))
                  for remedy in getattr(lore.LEXICON[word], side) if "mic" in remedy.moves]
        brighter = [remedy.moves["mic"] for word, side in (("bright", "toward"), ("dark", "away"))
                    for remedy in getattr(lore.LEXICON[word], side) if "mic" in remedy.moves]
        self.assertEqual((len(darker), len(brighter)), (3, 2))
        self.assertTrue(all(move > 0 for move in darker) and all(move < 0 for move in brighter))


if __name__ == "__main__":
    unittest.main()
