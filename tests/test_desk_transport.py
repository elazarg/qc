import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tone_lab import listening, usage
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
        desk.active_monitor_name = "Clean"
        desk.base_bypassed = False
        desk.control_mail = Queue()
        desk.monitor_controls = {}
        desk.level_feedback = {}
        desk.root = Mock()
        desk.tray = Mock()
        desk.knobs = Mock()
        desk.undo, desk.journal, desk.touched = [], [], []
        desk.chain_seen = set()
        desk.draft = None
        desk.asked = desk.followup = desk.kept_live = None
        desk.blind = desk.blind_panels = desk.cancel_signal = desk.return_peak = None
        desk.drafts, desk.static_cache = {}, {}
        desk.available = None
        desk.reference = None
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
        desk.asked = {"text": "brighter", "action": "refine", "side": "b", "revision": 1, "name": "B"}
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

    def test_a_late_sound_is_offered_and_never_replaces_a_tone_chosen_since(self):
        desk = self.desk()
        desk.drafts = {}
        desk.asked = {"text": "like a stone", "action": "create", "side": "b", "revision": 1, "name": "B"}
        chosen = Tone.from_preset(SavedPreset("Chosen", "My Presets", "/media/p4/Presets/My Presets", 9))
        desk.set_tone(chosen)
        desk.control_mail.get_nowait()
        desk.answered({"id": "late", "name": "Stone Pulse", "target": "Verse"})
        self.assertEqual(desk.tone_b, chosen)
        self.assertTrue(desk.control_mail.empty())
        self.assertIn("late", desk.drafts)
        message, options = desk.set_status.call_args.args[0], desk.set_status.call_args.kwargs["actions"]
        self.assertIn("left alone", message)
        self.assertEqual(options[0][0], "Put on B")

    def test_a_late_knob_suggestion_is_not_applied_to_a_different_tone(self):
        desk = self.desk()
        desk.asked = {"text": "tighter", "action": "adjust-live", "side": "b", "revision": 1, "name": "Old"}
        desk.set_tone(Tone.from_preset(SavedPreset("Chosen", "My Presets", "/media/p4/Presets/My Presets", 9)))
        desk.answered({"reply": "Try tighter lows.", "knobs": {"tightness": .3}, "intent": "adjust"})
        self.assertEqual(desk.shapes["b"], {})
        self.assertIn("no longer on B", desk.set_status.call_args.args[0])

    def test_a_request_changes_the_tone_it_was_asked_about_and_one_undo_restores_it(self):
        desk = self.desk()
        desk.asked = {"text": "tighter", "action": "adjust-live", "side": "b", "revision": 1, "name": "B"}
        desk.answered({"reply": "", "knobs": {"tightness": .3}, "intent": "adjust"})
        self.assertEqual(desk.shapes["b"], {"drive": .5, "tightness": .3})
        self.assertEqual(desk.touched, ["tightness"])
        self.assertEqual([title for title, _ in desk.set_status.call_args.kwargs["actions"]][:2],
                         ["Undo", "What changed"])
        desk.undo_last()
        self.assertEqual(desk.shapes["b"], {"drive": .5})
        self.assertEqual(desk.shapes["a"], {"warmth": .2})

    def test_an_answer_that_calls_for_a_new_sound_is_followed_up_without_touching_the_tone(self):
        desk = self.desk()
        desk.asked = {"text": "plini", "action": "adjust-live", "side": "b", "revision": 1, "name": "B"}
        desk.answered({"reply": "I'd build a Plini lead.", "knobs": {}, "intent": "new_sound"})
        self.assertEqual(desk.followup, "plini")
        self.assertEqual(desk.shapes["b"], {"drive": .5})
        self.assertTrue(desk.control_mail.empty())

    def test_undo_brings_back_the_tone_that_a_new_one_replaced(self):
        desk = self.desk()
        old = Tone.from_preset(SavedPreset("Old", "My Presets", "/media/p4/Presets/My Presets", 7))
        desk.tone_b = old
        desk.set_tone(Tone.from_preset(SavedPreset("New", "My Presets", "/media/p4/Presets/My Presets", 8)))
        self.assertEqual(desk.shapes["b"], {})
        desk.undo_last()
        self.assertEqual(desk.tone_b, old)
        self.assertEqual(desk.shapes["b"], {"drive": .5})
        self.assertEqual(desk.undo, [])

    def test_knob_turns_within_one_gesture_are_one_undo_step(self):
        desk = self.desk()
        for value in (.1, .2, .3):
            desk.set_shape({"drive": value})
        self.assertEqual(len(desk.undo), 1)
        desk.undo_last()
        self.assertEqual(desk.shapes["b"], {"drive": .5})

    def test_keep_records_the_tone_on_air_with_its_shape(self):
        desk = self.desk()
        desk.reference = None
        desk.take = None
        with patch.object(listening, "keep_tone") as keep:
            desk.keep(note="for the solo")
        record, shape, _ = keep.call_args.args
        self.assertEqual((record["kind"], shape), ("current", {"drive": .5}))
        self.assertEqual(keep.call_args.kwargs, {"source": "phrase", "reference": "", "note": "for the solo"})
        self.assertTrue(desk.is_kept())
        desk.set_shape({"drive": .7})
        self.assertFalse(desk.is_kept())

    def test_a_blind_comparison_hides_which_tone_is_which_until_one_is_kept(self):
        desk = self.desk()
        desk.tone_b = Tone.from_preset(SavedPreset("Lead", "My Presets", "/media/p4/Presets/My Presets", 7))
        desk.take = None
        with patch("tone_lab.listening_room.random.sample", return_value=["b", "a"]):
            desk.toggle_blind()
        self.assertEqual(desk.blind, {"a": "b", "b": "a"})
        desk.tray.put_away.assert_called_once()
        desk.pick("b")  # The right-hand tile is A while shuffled.
        self.assertEqual(desk.side, "a")
        self.assertEqual(desk.control_mail.get_nowait()["config"]["tone_id"], "a")
        desk.toggle_panel("Chain")
        desk.tray.toggle.assert_not_called()
        desk.active_side = "a"
        with patch.object(listening, "keep_tone"):
            desk.keep()
        self.assertIsNone(desk.blind)
        told = desk.set_status.call_args.args[0]
        self.assertTrue(told.startswith("You kept A · Clean."), told)
        self.assertIn("Left was B · Lead. Right was A · Clean.", told)

    def test_a_plain_direction_moves_the_knob_at_once_without_the_assistant(self):
        desk = self.desk()
        desk.request = Mock()
        desk.request.get.return_value = "a bit tighter"
        desk.thinking = None
        desk.browser = SimpleNamespace(library=None)
        desk.advisor = Mock()
        desk.guitar = Mock()
        desk.take = None
        desk.ask()
        desk.advisor.submit.assert_not_called()
        self.assertEqual(desk.shapes["b"], {"drive": .5, "tightness": .15})
        self.assertEqual(desk.control_mail.get_nowait()["values"], {"drive": .5, "tightness": .15})
        self.assertIn("Tightness +15% on B", desk.set_status.call_args.args[0])
        desk.available = {"reverb"}  # A chain with no remedy for the low end leaves it to the assistant.
        self.assertIsNone(desk.instant_moves("tighter"))
        desk.available = {"drive"}
        self.assertEqual(desk.instant_moves("less gain"), {"drive": -.3})
        # With only gain to work with, the remedy players try third is the one that applies.
        self.assertEqual(desk.instant_moves("tighter"), {"drive": -.15})

    def test_a_remark_applies_the_first_remedy_and_offers_the_next_in_its_place(self):
        desk = self.desk()
        desk.request = Mock()
        desk.request.get.return_value = "the low end gets muddy when I palm mute"
        desk.thinking = None
        desk.browser = SimpleNamespace(library=None)
        desk.advisor = Mock()
        desk.guitar = Mock()
        desk.take = None
        desk.ask()
        desk.advisor.submit.assert_not_called()
        self.assertEqual(desk.shapes["b"], {"drive": .5, "tightness": .35})
        told = desk.set_status.call_args
        self.assertTrue(told.args[0].startswith("Muddy on palm mutes: leaner lows in the drive and amp"), told.args[0])
        self.assertIn("a boost in front", told.kwargs["hint"])  # What no knob here does stays one hover away.
        actions = dict(told.kwargs["actions"])
        self.assertEqual(list(actions), ["Undo", "Another way"])
        actions["Another way"]()
        # The second remedy replaces the first. It starts from the shape the remark was made about.
        self.assertEqual(desk.shapes["b"], {"drive": .3, "tightness": .15})
        self.assertIn("less gain", desk.set_status.call_args.args[0])
        self.assertIn("Drive +30%, Tightness +15% on B", desk.set_status.call_args.args[0])
        desk.tone_revisions["b"] += 1
        dict(desk.set_status.call_args.kwargs["actions"])["Another way"]()
        self.assertEqual(desk.shapes["b"], {"drive": .3, "tightness": .15})
        self.assertIn("has changed since then", desk.set_status.call_args.args[0])

    def test_cancelling_a_request_signals_the_assistant_and_ignores_its_answer(self):
        desk = self.desk()
        desk.request = Mock()
        desk.request.get.return_value = ""
        desk.abandoned, desk.sent = [], "plini"
        desk.cancel_signal = Event()
        desk.thinking = flight = object()
        desk.cancel_request()
        self.assertTrue(desk.cancel_signal.is_set())
        self.assertIsNone(desk.thinking)
        self.assertEqual(desk.abandoned, [flight])
        desk.request.set.assert_called_with("plini")

    def test_trimming_the_loop_rewrites_the_selection_and_reloads_a_playing_phrase(self):
        desk = self.desk()
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "clip.json").write_text(json.dumps({"duration_seconds": 10., "song": "Riff",
                                                          "selected_passage_seconds": [2., 8.]}), encoding="utf-8")

            def read():
                info = json.loads((folder / "clip.json").read_text(encoding="utf-8"))
                return listening.Performance(folder, info)

            desk.take = read()
            desk.select_take = Mock(side_effect=lambda take: setattr(desk, "take", take))
            with patch.object(listening, "takes", side_effect=lambda: [read()]):
                desk.trim_take(3., 3.2)
                self.assertEqual(desk.take.section, (2., 8.))
                desk.trim_take(3., 7.5)
                self.assertEqual(desk.take.section, (3., 7.5))
                self.assertEqual(desk.control_mail.get_nowait(), {"event": "source", "mode": "phrase",
                                                                 "take": str(folder), "reload": True})
                desk.trim_take(0., 10.)
            self.assertEqual(desk.take.section, (0, 10.))
            self.assertNotIn("selected_passage_seconds", json.loads((folder / "clip.json").read_text(encoding="utf-8")))

    def test_a_layout_is_offered_only_after_it_has_been_used_repeatedly(self):
        desk = self.desk()
        desk.offered_layouts = set()
        desk.tray = SimpleNamespace(order=[], cards={"Shape": 1, "Difference": 1}, pin=Mock())
        with patch.object(usage, "companions", return_value={("live", ("Difference", "Shape")): 2}):
            desk.offer_layout()
        desk.set_status.assert_not_called()
        desk.offered_layouts.clear()
        with patch.object(usage, "companions", return_value={("live", ("Difference", "Shape")): 4,
                                                             ("idle", ("Chain", "Shape")): 9}):
            desk.offer_layout()
            desk.offer_layout()
        desk.set_status.assert_called_once()
        self.assertEqual(desk.set_status.call_args.args[0], "You usually have Difference and Shape open here.")
        desk.set_status.call_args.kwargs["actions"][0][1]()
        self.assertEqual([call.args for call in desk.tray.pin.call_args_list],
                         [("Difference", True), ("Shape", True)])

    def test_the_active_input_button_stops_and_the_other_switches(self):
        desk = self.desk()
        desk.stop = Mock()
        desk.toggle_source("phrase")
        desk.stop.assert_called_once()
        self.assertTrue(desk.control_mail.empty())
        desk.toggle_source("guitar")
        self.assertEqual(desk.control_mail.get_nowait()["mode"], "guitar")
        desk.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
