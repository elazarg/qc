from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from tone_lab import audio, guitar_dataset as data, session


class GuitarDatasetTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        guard = patch.object(session, "SESSIONS", self.root)
        guard.start()
        self.addCleanup(guard.stop)
        self.layout = "dk24_hh_toggle"
        self.config = {"pickup": 5, "wiring": " parallel ", "volume": 9.5, "tone": 10}

    def wave(self, filename, *, frequency=220, stereo=False):
        frames = np.arange(audio.RATE * 2, dtype=np.float32)
        signal = (.05 * np.sin(frames * (frequency * 2 * np.pi / audio.RATE)))[:, None]
        audio.write_wave(filename, np.repeat(signal, 2, axis=1) if stereo else signal)
        return filename

    def take(self, key, *, guitar="Charvel", unresolved=False):
        folder = self.root / "recordings" / f"clip-20261118-{key}-abcdef"
        folder.mkdir(parents=True)
        self.wave(folder / "dry.wav")
        session.write_json(folder / "clip.json", {"guitar": guitar, "duration_seconds": 2,
                                                  "selected_passage_seconds": [.4, 1.4],
                                                  "routing_unresolved": unresolved, "song": "Opening"})
        return folder

    def test_precise_recorder_result_stays_pinned_and_scope_checks_existing_takes(self):
        captured = self.take("012300")
        later = self.take("012400")
        other = self.take("012500", guitar="Les Paul")
        self.take("012600", unresolved=True)
        source = data.source_from_result("Charvel", (captured, "Clip saved"))
        self.assertEqual(source.path, captured / "dry.wav")
        self.assertEqual(source.passage, (.4, 1.4))
        self.assertEqual(source.duration, 2)
        self.assertEqual([item.path.parent for item in data.take_sources("charvel")], [later, captured])
        with self.assertRaisesRegex(ValueError, "used Les Paul"):
            data.source_from_take("Charvel", other)
        with self.assertRaisesRegex(ValueError, "used Les Paul"):
            data.add("Charvel", self.layout, data.source_from_wav(other / "dry.wav"), self.config)
        wet = self.wave(other / "wet.wav")
        with self.assertRaisesRegex(ValueError, "verified dry WAV"):
            data.source_from_wav(wet)

    def test_copies_only_the_selected_span_and_keeps_layouts_and_guitars_separate(self):
        take = self.take("012300")
        source = data.source_from_take("Charvel", take)
        before = source.path.read_bytes()
        row = data.add("Charvel", self.layout, source, self.config)
        self.assertEqual(row["configuration"], {"pickup": 5, "wiring": "Parallel", "volume": 9.5, "tone": 10.})
        self.assertEqual((row["source_start"], row["source_end"]), (.4, 1.4))
        self.assertEqual(len(audio.read_wave(row["wav"])), audio.RATE)
        self.assertEqual(data.records(" CHARVEL ", self.layout, role="example"), [row])
        self.assertEqual(data.records("Les Paul", self.layout), [])
        self.assertEqual(data.records("Charvel", "dk24_blade"), [])
        self.assertEqual(source.path.read_bytes(), before)
        data.remove("Charvel", self.layout, row["id"])
        self.assertFalse(data.records("Charvel", self.layout))
        self.assertEqual(source.path.read_bytes(), before)

    def test_same_source_content_cannot_feed_both_roles_or_overlap_labels(self):
        original = self.wave(self.root / "original.wav")
        copy = self.root / "renamed.wav"
        copy.write_bytes(original.read_bytes())
        data.add("Charvel", self.layout, original, self.config, start=0, end=.8)
        with self.assertRaisesRegex(ValueError, "other role"):
            data.add("Charvel", self.layout, copy, self.config, role="test", start=1, end=1.8)
        with self.assertRaisesRegex(ValueError, "already has a configuration"):
            data.add("Charvel", self.layout, copy, self.config, start=.4, end=1.2)
        data.add("Charvel", self.layout, copy, {**self.config, "volume": 5}, start=1, end=1.8)
        fresh = self.wave(self.root / "fresh.wav", frequency=232)
        kept = data.add("Charvel", self.layout, fresh, self.config, role="test")
        self.assertEqual(data.records("Charvel", self.layout, role="test"), [kept])
        self.assertEqual(len(data.records("Charvel", self.layout, role="example")), 2)

    def test_blind_reading_is_unlabeled_and_recognizes_an_exported_training_excerpt(self):
        original = self.wave(self.root / "original.wav")
        excerpt = data.inspect(original, start=.2, end=1.0)
        self.assertFalse(data.records("Charvel", self.layout))
        data.check_test("Charvel", self.layout, excerpt)
        self.assertEqual(excerpt.samples.shape, (round(.8 * audio.RATE), 1))
        example = data.add("Charvel", self.layout, original, self.config, start=.2, end=1.0)
        self.assertEqual(example["excerpt_id"], excerpt.excerpt_id)
        exported = self.root / "exported.wav"
        audio.write_wave(exported, excerpt.samples)
        reopened = data.inspect(exported)
        self.assertNotEqual(excerpt.source_id, reopened.source_id)
        self.assertEqual(excerpt.excerpt_id, reopened.excerpt_id)
        with self.assertRaisesRegex(ValueError, "new performance"):
            data.check_test("Charvel", self.layout, reopened)
        self.assertEqual(data.records("Charvel", self.layout), [example])

    def test_requires_valid_controls_and_an_unclipped_mono_excerpt(self):
        with self.assertRaisesRegex(ValueError, "selector positions"):
            data.configuration(self.layout, {**self.config, "pickup": 5.5})
        with self.assertRaisesRegex(ValueError, "mini-toggle"):
            data.configuration(self.layout, {**self.config, "wiring": "Up?"})
        with self.assertRaisesRegex(ValueError, "all controls"):
            data.configuration("dk24_blade", self.config)
        stereo = self.wave(self.root / "stereo.wav", stereo=True)
        with self.assertRaisesRegex(ValueError, "mono dry WAV"):
            data.add("Charvel", self.layout, stereo, self.config)
        silent = self.root / "quiet.wav"
        audio.write_wave(silent, np.zeros((audio.RATE, 1), dtype=np.float32))
        with self.assertRaisesRegex(ValueError, "too quiet"):
            data.add("Charvel", self.layout, silent, self.config)
        known_quiet = data.add("Charvel", self.layout, silent, self.config, role="test")
        self.assertTrue(known_quiet["quiet"])


if __name__ == "__main__":
    unittest.main()
