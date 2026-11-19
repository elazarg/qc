"""Tone search: words, matching a reference regardless of level, and describing sounds rendered on the simulated unit.

Ported from test_tone_search. The old library was recordings in session folders; the new one is the
player's sounds, described once rendered with the phrase."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from scipy.io import wavfile

from lab.audio import RATE, write_wave
from lab.tools import search

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lab_tools_support import DELAY, PLEXI, V30, chain, host, phrase  # noqa: E402


def item(name, sound_id, tags=(), heard=None, saved=False):
    return {"title": name, "sound": sound_id, "digest": sound_id, "target": "", "tags": list(tags), "heard": heard,
            "saved": saved}


class SearchTest(unittest.TestCase):
    def setUp(self):
        time = np.arange(RATE * 2) / RATE
        self.passage = (.18 * np.sin(2 * np.pi * 220 * time) * np.exp(-8 * (time % .5))).astype(np.float32)
        self.reference = np.column_stack((self.passage, self.passage))

    def test_a_match_ignores_level_and_compares_the_same_passage(self):
        segment = self.reference[RATE // 4:7 * RATE // 4]
        reference = search.describe(segment)
        tones = {"same": search.describe(segment * .001), "other": search.describe(self.reference[:, :1] ** 3)}
        entries = [item("Other take", "other"), item("Same tone", "same"), item("Never rendered", "none")]
        results = search.search(entries, reference=reference, tones=tones, same_take=True)
        self.assertEqual([row["title"] for row in results], ["Same tone", "Other take"])
        self.assertEqual(results[0]["similarity"], 100.)
        self.assertTrue(results[0]["same_take"])

    def test_matching_priorities_change_the_ranking(self):
        reference = search.describe(self.reference)
        wide = np.column_stack((self.passage, -self.passage))
        time = np.arange(len(self.passage)) / RATE
        harmonic = .08 * np.sin(2 * np.pi * 1900 * time) * np.exp(-8 * (time % .5))
        brighter = self.reference + harmonic[:, None]
        tones = {"wide": search.describe(wide), "bright": search.describe(brighter)}
        entries = [item("Wide same spectrum", "wide"), item("Narrow brighter", "bright")]
        self.assertEqual(search.search(entries, reference=reference, tones=tones, priorities="Tone")[0]["title"],
                         "Wide same spectrum")
        self.assertEqual(search.search(entries, reference=reference, tones=tones, priorities={"space": 1})[0]["title"],
                         "Narrow brighter")
        with self.assertRaises(ValueError):
            search.search(entries, reference=reference, tones=tones, priorities="Loud")

    def test_words_find_names_and_measured_qualities(self):
        entries = [item("Jazz Lead", "1", heard={"brightness_oct": -.6, "crest_db": 18, "tail_db": -50}),
                   item("Clean Rhythm", "2", tags=["designed", "jazzy comping"],
                        heard={"brightness_oct": .7, "crest_db": 12, "tail_db": -20}),
                   item("Metal", "3", tags=["overdriven"], heard={"brightness_oct": .2, "crest_db": 9, "tail_db": -45})]
        self.assertEqual({row["title"] for row in search.search(entries, "find jazz")}, {"Jazz Lead", "Clean Rhythm"})
        self.assertEqual(search.search(entries, "bright")[0]["title"], "Clean Rhythm")
        self.assertEqual(search.search(entries, "dark")[0]["title"], "Jazz Lead")
        self.assertEqual(search.search(entries, "no reverb")[0]["title"], "Jazz Lead")
        self.assertEqual([row["title"] for row in search.search(entries, "crunch")], ["Metal"])

    def test_a_reference_preview_is_level_normalized_16_bit_and_made_once(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "reference.wav"
            write_wave(source, self.reference * .1)
            preview = search.audition(source, folder)
            rate, data = wavfile.read(preview)
            self.assertEqual(rate, RATE)
            self.assertEqual(data.dtype, np.dtype("int16"))
            self.assertEqual(preview, search.audition(source, folder))
            write_wave(source, np.zeros_like(self.reference))
            with self.assertRaisesRegex(ValueError, "quiet"):
                search.audition(source, Path(folder) / "other")

    def test_a_silent_excerpt_cannot_be_described(self):
        with self.assertRaisesRegex(ValueError, "silent"):
            search.describe(np.zeros((RATE, 2)))


class OnTheUnitTest(unittest.TestCase):
    def test_sounds_are_rendered_silently_and_described(self):
        with TemporaryDirectory() as folder:
            unit = host(folder)
            try:
                found = unit.call("lab.tools.search.describe_sounds",
                                  {"dry": chain(gain_db=0.), "echo": chain(DELAY), "driven": chain(PLEXI, V30)},
                                  phrase()).result(60)
            finally:
                unit.close()
        tones = {key: search.Tone.from_record(record) for key, record in found.items()}
        self.assertGreater(tones["echo"].tails, tones["dry"].tails)
        self.assertLess(tones["driven"].crest, tones["dry"].crest)
        reference = tones["echo"]
        ranked = search.search([item("Dry", "dry"), item("Echo", "echo"), item("Driven", "driven")],
                               tones=tones, reference=reference, priorities="Space")
        self.assertEqual(ranked[0]["title"], "Echo")


if __name__ == "__main__":
    unittest.main()
