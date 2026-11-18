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
        desk.shapes = {"a": {"warmth": .2}, "b": {"drive": .5}}
        default = SavedPreset("Clean", "My Presets", "/media/p4/Presets/My Presets", 5)
        desk.tone_a = Tone.from_preset(default, "A")
        desk.tone_b = None
        desk.take = SimpleNamespace(folder=Path("a-take"))
        desk.qc_name = "Clean"
        desk.input_channel = 1
        desk.requested_source = "phrase"
        desk.active_source = "phrase" if running else None
        desk.base_bypassed = False
        desk.control_mail = Queue()
        desk.monitor_controls = {}
        desk.root = Mock()
        desk.output = Mock()
        desk.update = Mock()
        desk.submit = Mock()
        desk.remember = Mock()
        desk.set_status = Mock()
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


if __name__ == "__main__":
    unittest.main()
