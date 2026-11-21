import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from lab.hearing import audio
from lab.learner import dataset as data, folders
from lab.sound.sounds import INSTRUMENTS


class GuitarDatasetTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        folders.use(self.root)
        self.addCleanup(folders.use, None)
        self.layout = "dk24_hh_toggle"
        self.config = {"pickup": 5, "wiring": " parallel ", "volume": 9.5, "tone": 10}

    def wave(self, filename, *, frequency=220, stereo=False):
        frames = np.arange(audio.RATE * 2, dtype=np.float32)
        signal = (.05 * np.sin(frames * (frequency * 2 * np.pi / audio.RATE)))[:, None]
        audio.write_wave(filename, np.repeat(signal, 2, axis=1) if stereo else signal)
        return filename

    def take(self, key, *, guitar="Charvel", unresolved=False, day="20261118"):
        folder = self.root / "recordings" / f"clip-{day}-{key}-abcdef"
        folder.mkdir(parents=True)
        self.wave(folder / "dry.wav")
        folders.write_json(folder / "clip.json", {"guitar": guitar, "duration_seconds": 2,
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
        data.unlabel("Charvel", self.layout, row["id"])
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
        self.assertEqual(data.records("Charvel", self.layout, role="test"), [{**kept, "separate_session": False}])
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

    def test_edit_promote_and_unlabel_keep_score_partition_and_audio_bytes(self):
        original = self.wave(self.root / "original.wav")
        first = data.add("Charvel", self.layout, original, self.config, role="test", start=0, end=.8)
        second = data.add("Charvel", self.layout, original, self.config, role="test", start=1, end=1.8)
        source_bytes = original.read_bytes()
        excerpt_bytes = Path(first["wav"]).read_bytes()
        corrected = {**self.config, "tone": 6}
        edited = data.retag("Charvel", self.layout, first["id"], corrected)
        self.assertEqual(edited["history"][0]["configuration"], first["configuration"])
        self.assertEqual(edited["configuration"]["tone"], 6.)
        self.assertEqual(len(data.records("Charvel", self.layout, role="test")), 2)

        with self.assertRaisesRegex(ValueError, "explicitly"):
            data.retag("Charvel", self.layout, first["id"], corrected, role="example")
        promoted = data.retag("Charvel", self.layout, first["id"], corrected, role="example", promote=True)
        self.assertEqual(promoted["id"], first["id"])
        self.assertEqual(promoted["history"][-1]["action"], "promote")
        self.assertEqual(data.records("Charvel", self.layout, role="example"), [promoted])
        self.assertEqual(data.records("Charvel", self.layout, role="test"), [])
        earlier_check = data.find_label("Charvel", self.layout, data.inspect(original, start=1, end=1.8))
        self.assertEqual(earlier_check["id"], second["id"])
        self.assertFalse(earlier_check["score_eligible"])
        with self.assertRaisesRegex(ValueError, "new performance"):
            data.retag("Charvel", self.layout, first["id"], corrected, role="test")

        data.unlabel("Charvel", self.layout, first["id"])
        self.assertFalse(data.find_label("Charvel", self.layout, data.inspect(original, start=0, end=.8)))
        self.assertFalse(data.never_taught("Charvel", self.layout, data.inspect(original)))
        history = next(row for row in data.label_history("Charvel", self.layout) if row["id"] == first["id"])
        self.assertFalse(history["active"])
        self.assertEqual(history["history"][-1]["action"], "unlabel")
        self.assertEqual(original.read_bytes(), source_bytes)
        self.assertEqual(Path(first["wav"]).read_bytes(), excerpt_bytes)

    def test_dataset_shows_unlabeled_takes_and_retired_imports(self):
        take = self.take("012300")
        unknown = self.take("012400", guitar="")
        original = self.wave(self.root / "original.wav", frequency=340)
        label = data.add("Charvel", self.layout, original, self.config)
        library = dict((source.path, labels) for source, labels in data.dataset_sources("Charvel", self.layout,
                                                                                      include_unknown=True))
        self.assertEqual(library[take / "dry.wav"], ())
        self.assertEqual(library[unknown / "dry.wav"], ())
        self.assertEqual(library[original], (label,))
        data.unlabel("Charvel", self.layout, label["id"])
        self.assertEqual(dict((source.path, labels) for source, labels in data.dataset_sources("Charvel", self.layout))[original], ())

    def test_saved_excerpt_fallback_retains_origin_for_score_and_label_reuse(self):
        original = self.wave(self.root / "original.wav")
        example = data.add("Charvel", self.layout, original, self.config, start=0, end=.8)
        probe = data.add("Charvel", self.layout, original, self.config, start=1, end=1.8)
        data.unlabel("Charvel", self.layout, probe["id"])
        original.unlink()
        source, active = next((source, labels) for source, labels in data.dataset_sources("Charvel", self.layout)
                              if source.path == Path(probe["wav"]))
        self.assertFalse(active)
        self.assertEqual(data.inspect(source).source_id, example["source_id"])
        self.assertFalse(data.never_taught("Charvel", self.layout, data.inspect(source)))
        added = data.add("Charvel", self.layout, source, self.config)
        self.assertEqual(added["source_offset"], 1.)
        library = {source.path: labels for source, labels in data.dataset_sources("Charvel", self.layout)}
        self.assertEqual(library[Path(probe["wav"])], (added,))

    # ---- one performance, one source ----

    def kept_clip(self, key, run, begins, *, seconds=2):
        folder = self.root / "guitar_learner" / "live_recordings" / f"clip-20261118-{key}-abcdef"
        folder.mkdir(parents=True)
        frames = np.arange(round(audio.RATE * (begins + seconds)), dtype=np.float32)
        playing = (.05 * np.sin(frames * (220 * 2 * np.pi / audio.RATE)) * (1 + .3 * np.sin(frames / 9000)))[:, None]
        audio.write_wave(folder / "dry.wav", playing[round(audio.RATE * begins):])
        folders.write_json(folder / "clip.json", {"guitar": "Charvel", "duration_seconds": seconds,
                                                  "live_run": run, "run_start_seconds": begins})
        return data.source_from_take("Charvel", folder)

    def test_clips_kept_from_one_run_of_live_listening_are_one_performance(self):
        first, second = self.kept_clip("010000", "run-a", 0., seconds=5), self.kept_clip("010001", "run-a", 1., seconds=5)
        self.assertNotEqual(first.path.read_bytes(), second.path.read_bytes())
        self.assertEqual(data.inspect(first).source_id, data.inspect(second).source_id)
        taught = data.add("Charvel", self.layout, first, self.config, start=0, end=3)
        self.assertEqual(taught["source_offset"], 0.)
        self.assertFalse(data.never_taught("Charvel", self.layout, data.inspect(second)))
        with self.assertRaisesRegex(ValueError, "other role"):
            data.add("Charvel", self.layout, second, self.config, role="test")
        with self.assertRaisesRegex(ValueError, "already has a configuration"):
            data.add("Charvel", self.layout, second, self.config, start=0, end=1.5)      # 1.0-2.5 s of the run
        later = data.add("Charvel", self.layout, second, self.config, start=2.5, end=4)      # 3.5-5.0 s of the run
        self.assertEqual(later["source_offset"], 1.)
        other_run = self.kept_clip("010002", "run-b", 0.)
        self.assertTrue(data.never_taught("Charvel", self.layout, data.inspect(other_run)))
        data.add("Charvel", self.layout, other_run, self.config, role="test")

    def test_a_check_is_from_a_separate_session_only_when_recorded_on_another_day_than_all_teaching(self):
        taught = data.add("Charvel", self.layout, data.source_from_take("Charvel", self.take("010000")), self.config)
        self.assertEqual((taught["session"], data.session_of(taught)), ("20261118", "20261118"))
        same_day = self.take("020000")
        self.wave(same_day / "dry.wav", frequency=233)
        next_day = self.take("030000", day="20261119")
        self.wave(next_day / "dry.wav", frequency=247)
        for folder in (same_day, next_day):
            data.add("Charvel", self.layout, data.source_from_take("Charvel", folder), self.config, role="test")
        checks = {row["session"]: row["separate_session"] for row in data.records("Charvel", self.layout, role="test")}
        self.assertEqual(checks, {"20261118": False, "20261119": True})
        # A label written before sessions were recorded takes the day from its take's name.
        self.assertEqual(data.session_of({"source": "C:/old/tone_sessions/recordings/clip-20261101-101010-abcdef/dry.wav"}),
                         "20261101")
        # Teaching from a recording of unknown day leaves no check known to be separate.
        data.add("Charvel", self.layout, self.wave(self.root / "imported.wav", frequency=262), self.config)
        self.assertEqual({row["separate_session"] for row in data.records("Charvel", self.layout, role="test")}, {False})

    def test_a_label_names_its_take_relative_to_the_data_folder(self):
        take = self.take("010000")
        row = data.add("Charvel", self.layout, data.source_from_take("Charvel", take), self.config)
        self.assertEqual(row["source"], "recordings/clip-20261118-010000-abcdef/dry.wav")
        library = dict(data.dataset_sources("Charvel", self.layout))
        self.assertEqual([labels[0]["id"] for source, labels in library.items() if source.path == take / "dry.wav"],
                         [row["id"]])

    def test_pickup_settings_are_named_as_the_desk_names_them(self):
        charvel = next(item for item in INSTRUMENTS if item["name"] == "Charvel")
        for layout in ("dk24_hh_toggle", "dk24_blade"):
            self.assertEqual(list(data.LAYOUTS[layout]["pickups"]), charvel["settings"])
        self.assertEqual(list(data.LAYOUTS["dk24_hh_toggle"]["wiring"]), charvel["wiring"])
        self.assertEqual(data.layouts_for(" charvel "), ["dk24_hh_toggle", "dk24_blade"])
        self.assertEqual(data.layouts_for("Bass"), [])
        self.assertEqual(data.setting_name("dk24_hh_toggle", {"pickup": 5, "wiring": "Series"}), "Neck · Series")
        self.assertEqual(data.setting_name("dk24_blade", {"pickup": 2}), "Outer coils")
        self.assertEqual(data.pickup_settings("Les Paul"), ("Bridge", "Both", "Neck"))


if __name__ == "__main__":
    unittest.main()
