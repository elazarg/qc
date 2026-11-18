from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from scipy.io import wavfile

from tone_lab import audio, session, tone_search


class ToneSearchTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        scope = patch.object(session, "SESSIONS", self.root)
        self.addCleanup(scope.stop)
        scope.start()
        time = np.arange(audio.RATE * 2) / audio.RATE
        self.passage = (.18 * np.sin(2 * np.pi * 220 * time) * np.exp(-8 * (time % .5))).astype(np.float32)
        self.reference = np.column_stack((self.passage, self.passage))

    def recorded(self, name):
        folder = self.root / "recordings" / name
        folder.mkdir(parents=True)
        session.write_json(folder / "clip.json", {"wet_preset": "Recorded reference"})
        path = folder / "wet.wav"
        audio.write_wave(path, self.reference)
        return path

    def render(self, folder, name, samples, *, take=None, kind="style"):
        root = self.root / folder
        root.mkdir()
        audio.write_wave(root / "render.wav", samples)
        session.write_json(root / "session.json", {"state": "ready", "kind": kind, "source_clip": take,
                           "candidates": [{"name": name, "file": "render.wav"}]})

    def test_match_ignores_gain_and_calibrated_recording_region(self):
        path = self.recorded("this-take")
        info = path.with_name("clip.json")
        session.write_json(info, {"selected_passage_seconds": [0.25, 1.75]})
        segment = self.reference[audio.RATE // 4:7 * audio.RATE // 4]
        self.render("other", "Other take", self.reference)
        self.render("quiet", "Same tone", segment * .001, take="this-take")
        results = tone_search.search(reference=path, same_take_only=True)
        self.assertEqual([row["title"] for row in results], ["Same tone"])
        self.assertEqual(results[0]["similarity"], 100.)
        self.assertTrue(results[0]["same_take"])

    def test_matching_priorities_change_ranking(self):
        path = self.recorded("this-take")
        wide = np.column_stack((self.passage, -self.passage))
        time = np.arange(len(self.passage)) / audio.RATE
        added_harmonic = .08 * np.sin(2 * np.pi * 1900 * time) * np.exp(-8 * (time % .5))
        brighter = self.reference + added_harmonic[:, None]
        self.render("wide", "Wide same spectrum", wide, take="this-take")
        self.render("darker", "Narrow brighter", brighter, take="this-take")
        self.assertEqual(tone_search.search(reference=path, priorities="Tone")[0]["title"], "Wide same spectrum")
        self.assertEqual(tone_search.search(reference=path, priorities={"space": 1})[0]["title"], "Narrow brighter")

    def test_search_and_preview_exclude_demo_and_unplayable_wavs(self):
        self.render("real", "Jazz Lead", self.reference * .1)
        self.render("demo", "Jazz demo", self.reference, kind="demo")
        self.render("silent", "Jazz no signal", np.zeros_like(self.reference))
        results = tone_search.search("find jazz")
        self.assertEqual([row["title"] for row in results], ["Jazz Lead"])
        preview = tone_search.audition(results[0])
        rate, data = wavfile.read(preview)
        self.assertEqual(rate, audio.RATE)
        self.assertEqual(data.dtype, np.dtype("int16"))
        self.assertEqual(preview, tone_search.audition(results[0]))


if __name__ == "__main__":
    unittest.main()
