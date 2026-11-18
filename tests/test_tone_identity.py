from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tone_lab import listening, session
from tone_lab.preset_library import SavedPreset
from tone_lab.tone_identity import Tone


class ToneIdentityTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        sessions = patch.object(session, "SESSIONS", self.root)
        sessions.start()
        self.addCleanup(sessions.stop)
        self.slow = self.take("slow")
        self.fast = self.take("fast")
        self.draft = {"name": "Goldtop", "id": "draft123"}
        self.setlist = "/media/p4/Presets/Tone Lab"
        self.preset = SavedPreset("Goldtop", "Tone Lab", self.setlist, 65)

    def take(self, name, section=(0.3, 1.6)):
        folder = self.root / "recordings" / name
        folder.mkdir(parents=True)
        wave = folder / "wet.wav"
        wave.touch()
        return listening.Performance(folder, {"wet": str(wave), "wet_preset": name, "selected_passage_seconds": section})

    def archive(self, take, *, values=None, scene="A", version=1):
        folder = self.root / f"{take.folder.name}-draft-{version}"
        folder.mkdir()
        for filename in ("original.wav", "draft.wav"):
            (folder / filename).touch()
        session.write_json(folder / "session.json", {
            "source_clip": take.folder.name, "state": "ready", "selection_seconds": take.section,
            "draft_id": self.draft["id"], "knob_values": values or {}, "draft_scene": scene,
            "auditions": {"A": {"render": "original.wav"}, "B": {"render": "draft.wav"}},
            "candidates": [
                {"file": "original.wav", "audition_role": "original", "name": "Original"},
                {"file": "draft.wav", "audition_role": "draft", "name": self.draft["name"]},
            ],
        })
        return listening.draft_comparison(take, self.draft["id"])[1]

    def capture(self, clip, slot, scene="A", fingerprint=None):
        folder = self.root / f"{clip.folder.name}-preset-reference-{slot.index}-{scene}"
        folder.mkdir()
        path = folder / "reference.wav"
        path.touch()
        session.write_json(folder / "session.json", {
            "state": "ready", "source_clip": clip.folder.name, "selection_seconds": clip.section,
            "qc_reference": {"name": slot.name, "setlist": slot.setlist, "position": slot.position,
                             "scene": scene, "fingerprint": fingerprint},
        })
        return listening.Sound(f"{slot.name} · {scene}", path, clip.folder.name, clip.section, folder=folder)

    def test_original_follows_phrase_while_draft_keeps_identity(self):
        original = Tone.from_sound(self.slow.original)
        self.assertEqual(original.kind, "original")
        self.assertEqual(original.asset(self.fast), self.fast.original)
        old_audio = self.archive(self.slow)
        draft = Tone.from_sound(old_audio)
        self.assertEqual(draft.kind, "draft")
        self.assertEqual(draft.asset(self.slow), old_audio)
        self.assertIsNone(draft.asset(self.fast))
        self.assertEqual(draft.capture, old_audio)
        fresh_audio = self.archive(self.fast)
        self.assertEqual(draft.asset(self.fast), fresh_audio)
        self.assertEqual(Tone.from_record(draft.to_record()), draft)

    def test_saved_draft_prefers_device_and_reuses_archived_audio(self):
        audio = self.archive(self.fast)
        receipt = self.root / "drafts" / f"{self.draft['id']}.kept.json"
        receipt.parent.mkdir()
        session.write_json(receipt, {"label": "Goldtop · Tone Lab 9B",
                                     "preset": {"name": self.preset.name, "setlist": self.preset.setlist,
                                                "position": self.preset.position},
                                     "fingerprint": "a", "scene": "A"})
        saved = Tone.from_draft(self.draft)
        self.assertEqual(saved.kind, "preset")
        self.assertEqual(saved.preset, self.preset)
        self.assertEqual(saved.asset(self.fast), audio)
        self.assertIsNone(saved.asset(self.slow))
        self.assertIsNone(saved.with_preset(self.preset, "A", fingerprint="replaced").asset(self.fast))
        self.assertEqual(Tone.from_record(saved.to_record()), saved)

    def test_saved_macro_and_scene_values_exclude_other_renders_of_the_draft(self):
        self.archive(self.slow)
        shaped = self.archive(self.fast, values={"warmth": .3}, scene="B")
        self.archive(self.fast, version=2)
        receipt = self.root / "drafts" / f"{self.draft['id']}.kept.json"
        receipt.parent.mkdir()
        session.write_json(receipt, {"label": "Goldtop · Tone Lab 9B", "scene": "B", "knob_values": {"warmth": .3},
                                     "preset": {"name": self.preset.name, "setlist": self.preset.setlist,
                                                "position": self.preset.position, "fingerprint": "a"}})
        saved = Tone.from_draft(self.draft)
        self.assertEqual(saved.asset(self.fast), shaped)
        self.assertIsNone(saved.asset(self.slow))
        self.assertIsNone(saved.with_values({"warmth": -.3}).asset(self.fast))
        self.assertEqual(Tone.from_record(saved.to_record()), saved)

    def test_legacy_receipt_retains_draft_provenance(self):
        receipt = self.root / "drafts" / f"{self.draft['id']}.kept.json"
        receipt.parent.mkdir()
        session.write_json(receipt, {"label": "Goldtop · My Presets 9B"})
        tone = Tone.from_draft(self.draft)
        self.assertEqual((tone.preset.setlist, tone.preset.index, tone.draft_id), ("My Presets", 65, self.draft["id"]))

    def test_reference_cache_identifies_exact_preset_scene_and_passage(self):
        duplicate = SavedPreset("Goldtop", "Tone Lab", self.setlist, 64)
        self.capture(self.slow, duplicate, "E", "current")
        self.capture(self.slow, self.preset, "F", "current")
        preferred = self.capture(self.slow, self.preset, "E", "current")
        current = Tone.from_preset(self.preset, "E", fingerprint="current")
        self.assertEqual(current.asset(self.slow), preferred)
        self.assertIsNone(current.asset(self.fast))
        current = Tone.from_sound(preferred)
        self.assertEqual((current.preset, current.scene, current.capture), (self.preset, 4, preferred))
        self.assertIsNone(current.with_preset(self.preset, "A").asset(self.slow))
        self.assertIsNone(current.with_preset(self.preset, "E", fingerprint="new").asset(self.slow))
        changed_section = listening.Performance(self.slow.folder,
                                                {**self.slow.info, "selected_passage_seconds": (0.4, 1.6)})
        self.assertIsNone(current.asset(changed_section))

    def test_audio_only_reference_survives_serialization_without_becoming_a_new_take(self):
        wave = self.root / "reference.wav"
        wave.touch()
        tone = Tone.from_sound(listening.Sound("External reference", wave))
        self.assertEqual(tone.kind, "capture")
        self.assertIsNone(tone.asset(self.slow))
        self.assertEqual(Tone.from_record(tone.to_record()), tone)


if __name__ == "__main__":
    unittest.main()
