from pathlib import Path
from queue import Queue
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tone_lab.listening_room import Desk
from tone_lab.preset_library import SavedPreset
from tone_lab.tone_identity import Tone


class DeskTransportTest(unittest.TestCase):
    def desk(self, *, running=True):
        desk = Desk.__new__(Desk)
        desk.job = object() if running else None
        desk.task = "monitor"
        desk.stop_signal = Event()
        desk.side = "b"
        desk.active_side = "b" if running else None
        desk.active_revision = None
        desk.tone_revisions = {"a": 0, "b": 1}
        desk.tone_revision = 1
        desk.confirmed_tones = {}
        desk.tone_requests = {}
        desk.shapes = {"a": {"warmth": .2}, "b": {"drive": .5}}
        default = SavedPreset("Clean", "My Presets", "/media/p4/Presets/My Presets", 5)
        desk.tone_a = Tone.from_preset(default, "A")
        desk.tone_b = None
        desk.baseline = desk.alternative = None
        desk.take = SimpleNamespace(folder=Path("a-take"))
        desk.qc_name = "Clean"
        desk.input_channel = 1
        desk.requested_source = "phrase"
        desk.active_source = "phrase" if running else None
        desk.base_bypassed = False
        desk.control_mail = Queue()
        desk.monitor_controls = {}
        desk.level_feedback = {}
        desk.root = Mock()
        desk.output = Mock()
        desk.update = Mock()
        desk.submit = Mock()
        desk.remember = Mock()
        desk.set_status = Mock()
        desk.send_levels = Mock()
        desk.stop_audio = Mock()
        desk.resolve_audio = Mock()
        return desk

    def test_selecting_a_tone_without_playback_does_not_start_input(self):
        desk = self.desk(running=False)
        desk.select_side("a")
        self.assertEqual(desk.side, "a")
        self.assertTrue(desk.control_mail.empty())
        desk.submit.assert_not_called()

    def test_tone_shape_and_source_commands_remain_independent(self):
        desk = self.desk()
        desk.select_side("a")
        switch = desk.control_mail.get_nowait()
        self.assertEqual(switch["event"], "tone")
        self.assertEqual(switch["config"]["tone_id"], "a")
        self.assertEqual(switch["config"]["values"], {"warmth": .2})
        desk.set_shape({"warmth": -.6})
        self.assertEqual(desk.control_mail.get_nowait(), {
            "event": "knobs", "values": {"warmth": -.6}, "tone_id": "a",
        })
        desk.start_source("guitar")
        self.assertEqual(desk.control_mail.get_nowait(), {
            "event": "source", "mode": "guitar", "take": str(desk.take.folder),
        })
        self.assertEqual(desk.shapes, {"a": {"warmth": -.6}, "b": {"drive": .5}})
        self.assertEqual(desk.side, "a")
        self.assertTrue(desk.control_mail.empty())

    def test_stale_tone_acknowledgment_does_not_reset_selection(self):
        desk = self.desk()
        desk.select_side("a")
        desk.monitor_event({"event": "controls", "tone_id": "b", "available": ["drive"]})
        desk.monitor_event({"event": "tone", "tone_id": "b", "name": "Lead"})
        self.assertEqual(desk.side, "a")
        self.assertIsNone(desk.available)
        desk.monitor_event({"event": "controls", "tone_id": "a", "available": ["warmth"]})
        desk.monitor_event({"event": "tone", "tone_id": "a", "name": "Clean"})
        self.assertEqual(desk.available, {"warmth"})
        desk.output.set.assert_called_with("Phrase → A · Clean · Quad Cortex")

    def test_new_chat_draft_enters_b_without_restarting_the_phrase(self):
        desk = self.desk()
        desk.side = "a"
        draft = {"id": "new-recipe", "name": "Bright Lead", "target": "Lead"}
        desk.intent = "refine"
        desk.drafts = {}
        a = desk.tone_a
        desk.answered(draft)
        update = desk.control_mail.get_nowait()
        self.assertEqual(update["event"], "tone")
        self.assertEqual(update["config"]["take"], str(desk.take.folder))
        self.assertEqual(update["config"]["draft"], draft)
        self.assertEqual(desk.tone_a, a)
        self.assertEqual(desk.shapes["a"], {"warmth": .2})
        self.assertTrue(desk.control_mail.empty())

    def test_rejected_new_b_restores_the_previous_b_title_and_shape(self):
        desk = self.desk()
        old_tone = Tone.from_preset(SavedPreset("Old", "My Presets", "/media/p4/Presets/My Presets", 7))
        desk.tone_b = old_tone
        desk.confirmed_tones = {"b": desk.tone_snapshot("b")}
        new_tone = Tone.from_preset(SavedPreset("New", "My Presets", "/media/p4/Presets/My Presets", 8))
        desk.set_tone(new_tone)
        desk.monitor_event({"event": "tone", "tone_id": "b", "tone_revision": 1,
                            "requested_revision": 2, "name": "Old", "reason": "Could not load New."})
        self.assertEqual(desk.tone_b, old_tone)
        self.assertEqual(desk.shapes["b"], {"drive": .5})
        desk.set_status.assert_called_with("Could not load New.")


if __name__ == "__main__":
    unittest.main()
