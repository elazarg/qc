"""Tone search: words, matching a reference regardless of level, and describing sounds rendered on the simulated unit.

Ported from test_tone_search. The old library was recordings in session folders; the new one is the
player's sounds, described once rendered with the phrase."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from lab.hearing.audio import RATE
from lab.hearing.measure import measure
from lab.sound.sounds import Sound
from lab.tools import search

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lab_tools_support import DELAY, HIWATT, PLEXI, V30, chain, host, phrase  # noqa: E402


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

    def test_a_reference_is_played_at_the_level_of_the_app_s_previews_and_never_louder(self):
        # Regression: "Hear the reference" played a chosen recording at whatever level it was mastered to.
        hot = self.reference / np.abs(self.reference).max() * .95
        self.assertGreater(measure(hot).lufs, -15.)
        played = search.at_preview_level(hot)
        self.assertAlmostEqual(measure(played).lufs, -23., delta=.2)
        self.assertLessEqual(measure(played).true_peak_db, -3.)
        quiet = self.reference * .01
        np.testing.assert_array_equal(search.at_preview_level(quiet), quiet)      # never turned up
        silent = np.zeros_like(self.reference)
        np.testing.assert_array_equal(search.at_preview_level(silent), silent)
        self.assertFalse(hasattr(search, "audition"))       # the normalising preview nobody called

    def test_a_sound_is_cached_as_it_is_heard_with_its_knobs(self):
        # Regression: the cache was keyed by the chain without its knobs, though the render had them applied.
        plain = Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"})
        turned = Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}, knobs={"brightness": 1.})
        self.assertEqual(plain.chain.digest(), turned.chain.digest())
        self.assertNotEqual(search.entry(plain)["digest"], search.entry(turned)["digest"])
        self.assertEqual(search.entry(plain)["digest"], plain.chain.digest())

    def test_the_narrowest_scope_is_the_default(self):
        self.assertEqual(search.SCOPES[0], "The last 20")
        self.assertEqual(search.SCOPES[-1], "Every sound")

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
            self.assertEqual(found, {"described": 3, "issues": {}})
            cache = search.load_cache(folder)       # written where the unit is held, sound by sound
        tones = {key: search.Tone.from_record(record) for key, record in cache.items()}
        self.assertGreater(tones["echo"].tails, tones["dry"].tails)
        self.assertLess(tones["driven"].crest, tones["dry"].crest)
        reference = tones["echo"]
        ranked = search.search([item("Dry", "dry"), item("Echo", "echo"), item("Driven", "driven")],
                               tones=tones, reference=reference, priorities="Space")
        self.assertEqual(ranked[0]["title"], "Echo")

    def refusing_engine(self, folder):
        """An engine on a simulated unit that will not load the Hiwatt, as the real one refuses an unlicensed plugin."""
        from lab.qc.engine import Engine
        from lab.qc.sim import SimAudio, SimUnit
        from lab.qc.slots import Slots
        from lab.qc.unit import Unit

        device = SimUnit(refuse=lambda grid, model_id: model_id == HIWATT)
        unit = Unit(device, settle=.05, sleep=lambda seconds: None)
        return Engine(unit, Slots(unit, Path(folder)), SimAudio(device))

    def test_one_sound_the_unit_will_not_build_costs_nothing_already_described(self):
        # Regression: one such sound ended the batch with nothing kept, every time it was tried.
        with TemporaryDirectory() as folder:
            engine = self.refusing_engine(folder)
            chains = {"first": chain(PLEXI, V30), "refused": chain(HIWATT, V30), "last": chain(gain_db=-3.)}
            found = search.describe_sounds(engine, chains, phrase())
            self.assertEqual(found["described"], 2)
            self.assertEqual(list(found["issues"]), ["refused"])
            cache = search.load_cache(folder)
            self.assertEqual(set(cache), {"first", "refused", "last"})
            self.assertIn("issue", cache["refused"])                    # remembered, so it is not built again
            self.assertNotIn("issue", cache["first"])

    def test_stopping_keeps_what_was_described_before(self):
        # Regression: the descriptions were kept only if the whole batch got to its end.
        import threading

        from lab.qc.unit import Stopped
        from lab.tools import common

        with TemporaryDirectory() as folder:
            engine = self.refusing_engine(folder)
            stop, real = threading.Event(), common.render

            def render_then_stop(*args, **options):
                try:
                    return real(*args, **options)
                finally:
                    stop.set()

            from unittest import mock

            with mock.patch.object(search, "render", render_then_stop), self.assertRaises(Stopped):
                search.describe_sounds(engine, {"first": chain(PLEXI, V30), "second": chain(gain_db=-3.)}, phrase(),
                                       stop=stop)
            self.assertEqual(set(search.load_cache(folder)), {"first"})


if __name__ == "__main__":
    unittest.main()
