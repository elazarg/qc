from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

import numpy as np

from tone_lab import (advisor, audio, desk_panels, listening, listening_room, qc_browser, session, styles,
                      tone_space, usage)


STONE_PULSE = {"chain": [
    {"model_id": 5005, "settings": [{"name": "MIX", "value": 0}], "reserved": True},
    {"model_id": 1081, "settings": [{"name": "GAIN", "value": 2.3}]},
    {"model_id": 12006, "settings": []},
    {"model_id": 4005, "settings": [{"name": "HPF", "value": 85}]},
    {"model_id": 7004, "settings": [{"name": "RATE", "value": 3.6}]},
    {"model_id": 8000, "settings": [{"name": "MIX", "value": 9}]},
]}


class QuietDeskTest(unittest.TestCase):
    def test_a_draft_reports_the_controls_its_chain_can_move_before_it_is_loaded(self):
        controls = styles.draft_controls(STONE_PULSE)
        self.assertEqual(controls["drive"], (("Brit Plexi 50 Normal", "Gain"),))
        # The equalizer's high-pass is set and the cab's is parked, so the equalizer takes the move.
        self.assertEqual(controls["low_cut"], (("Graphic-9", "Hpf"),))
        self.assertEqual(controls["soft_top"], (("412 Brit 60B GB 71 (M)", "Lpf"),))
        self.assertIn(("Tremolo", "Rate"), controls["motion"])
        self.assertNotIn("gate", controls)
        self.assertNotIn("delay", controls)
        self.assertEqual(styles.draft_controls({"chain": [{"model_id": 16005, "settings": []}]}), {})

    def test_proper_names_ask_for_a_sound_and_product_names_do_not(self):
        self.assertTrue(listening.names_something("John Mayer Gravity"))
        self.assertTrue(listening.names_something("something from Queens of the Stone Age"))
        self.assertFalse(listening.names_something("the low end gets muddy when I palm mute"))
        self.assertFalse(listening.names_something("The Quad Cortex is too quiet in Cortex Control"))
        self.assertFalse(listening.names_something("It is harsh on the high E string"))

    def test_the_advisor_can_say_that_a_request_wants_a_different_sound(self):
        reply = {"reply": " A Plini lead. ", "knobs": {key: None for key in styles.KNOBS}, "intent": "new_sound"}
        with patch.object(advisor, "request", return_value=reply) as request:
            result = advisor.read("plini", {"guitar": "Charvel"}, {})
        self.assertEqual(result, {"reply": "A Plini lead.", "knobs": {}, "intent": "new_sound"})
        prompt, schema = request.call_args.args
        self.assertIn("intent", schema["required"])
        self.assertIn("An observation about the current sound is always adjust", prompt)
        with patch.object(advisor, "request", return_value={**reply, "intent": "surprise"}):
            self.assertEqual(advisor.read("plini", {}, {})["intent"], "adjust")
        with patch.object(advisor, "request", return_value={"reply": "Darker.", "knobs": {"warmth": .3}}) as request:
            self.assertEqual(advisor.ask("darker please", {}, {}), ("Darker.", {"warmth": .3}))
        self.assertNotIn("intent", request.call_args.args[1]["required"])

    def test_keeping_a_live_tone_stores_its_identity_and_shape(self):
        take = SimpleNamespace(folder=Path("clip-one"), info={"guitar": "Charvel"})
        with TemporaryDirectory() as folder, patch.object(session, "SESSIONS", Path(folder)):
            path = listening.keep_tone({"kind": "draft", "name": "Stone Pulse", "draft_id": "abc"},
                                       {"tightness": .3}, take, source="guitar", note="for the verse")
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((saved["kind"], saved["name"], saved["draft_id"]), ("live", "Stone Pulse", "abc"))
        self.assertEqual((saved["shape"], saved["heard_with"], saved["take"]), ({"tightness": .3}, "guitar", "clip-one"))
        self.assertEqual(saved["note"], "for the verse")

    def test_usage_log_counts_views_that_were_open_together(self):
        with TemporaryDirectory() as folder, patch.object(session, "SESSIONS", Path(folder)):
            usage.record("open", "Shape", live=True, has_reference=True, open=["Shape", "Difference"])
            usage.record("pin", "Difference", live=True, has_reference=True, open=["Difference", "Shape"])
            usage.record("open", "Chain", live=False, has_reference=False, open=["Chain"])
            usage.record("close", "Chain", live=False, has_reference=False, open=[])
            counts = usage.companions()
        self.assertEqual(dict(counts), {("reference", ("Difference", "Shape")): 2})

    def test_each_capture_keeps_its_own_version_and_cabinet_answer(self):
        with TemporaryDirectory() as folder, patch.object(session, "SESSIONS", Path(folder)):
            self.assertEqual(qc_browser.capture_notes(), {})
            qc_browser.remember_capture("a" * 64, 2, True)
            qc_browser.remember_capture("b" * 64, None, False)
            notes = qc_browser.capture_notes()
        self.assertEqual(notes, {"a" * 64: {"version": 2, "cabinet": True},
                                 "b" * 64: {"version": None, "cabinet": False}})

    def test_plain_directions_become_bounded_knob_moves(self):
        for words, move in (("tighter", {"tightness": .3}), ("A bit tighter.", {"tightness": .15}),
                            ("much brighter", {"warmth": -.51}), ("more jazz", {"jazz": .4}),
                            ("less delay", {"delay": -.3}), ("darker", {"warmth": .35}),
                            ("a little less reverb", {"reverb": -.15}), ("too bright", {"warmth": .3}),
                            ("it's too harsh", {"warmth": .3}), ("too much gain", {"drive": -.3}),
                            ("not enough mids", {"mids": .3}), ("make it warmer", {"warmth": .3}),
                            ("needs more sustain", {"sustain": .3}), ("brighter please", {"warmth": -.3})):
            self.assertEqual(styles.direction(words), move, words)
        for words in ("Less bite, keep the attack.", "the low end gets muddy when I palm mute", "tighter and brighter",
                      "more Andy Timmons", "more", "too", "John Mayer Gravity", "undo"):
            self.assertIsNone(styles.direction(words), words)

    def test_measurements_of_the_captured_tone_reach_the_assistant(self):
        time = np.arange(audio.RATE) / audio.RATE
        with TemporaryDirectory() as folder:
            root = Path(folder)
            dark = listening.Sound("B", root / "b.wav", "clip", (0, 1))
            bright = listening.Sound("R", root / "r.wav", "clip", (0, 1))
            audio.write_wave(dark.path, .2 * np.sin(2 * np.pi * 220 * time))
            audio.write_wave(bright.path, .2 * np.sin(2 * np.pi * 220 * time) + .1 * np.sin(2 * np.pi * 5000 * time))
            facts = listening_room.measured(dark, None, bright, {"warmth": .2})
            self.assertIsNone(listening_room.measured(None, None, bright, {}))
            self.assertIsNone(listening_room.measured(dark, None, None, {}))
        gap = facts["selected_minus_reference"]
        self.assertEqual(facts["captured_with_knobs"], {"warmth": .2})
        self.assertEqual(len(gap["spectrum_db"]), len(facts["bands_hz"]))
        self.assertLess(min(gap["spectrum_db"]), -6)
        self.assertTrue(gap["same_performance"])
        reply = {"reply": "Brighter.", "knobs": {key: None for key in styles.KNOBS}, "intent": "adjust"}
        with patch.object(advisor, "request", return_value=reply) as request:
            advisor.read("closer to the reference", {}, {}, heard=facts)
        prompt = request.call_args.args[0]
        self.assertIn('"selected_minus_reference"', prompt)
        self.assertNotIn("You receive text only", prompt)
        with patch.object(advisor, "request", return_value=reply) as request:
            advisor.read("closer to the reference", {}, {})
        self.assertIn("You receive text only", request.call_args.args[0])

    def test_a_cancelled_request_ends_the_assistant_process(self):
        class Slow:
            returncode = None
            killed = False

            def __init__(self, *args, **kwargs):
                self.calls = 0

            def communicate(self, value=None, timeout=None):
                self.calls += 1
                if self.killed:
                    return "", ""
                if self.calls == 2:
                    cancel.set()
                raise advisor.subprocess.TimeoutExpired("codex", timeout)

            def poll(self):
                return -9 if self.killed else None

            def kill(self):
                self.killed = True

        cancel = Event()
        started = []
        with patch.object(advisor, "codex_executable", return_value="codex"), \
                patch.object(advisor.subprocess, "Popen", side_effect=lambda *a, **k: started.append(Slow()) or started[-1]):
            with self.assertRaises(advisor.Cancelled):
                advisor.request("prompt", {}, web=False, cancel=cancel)
        self.assertTrue(started[0].killed)

    def test_the_largest_gap_is_named_in_words(self):
        bands = len(tone_space.BAND_NAMES)
        spectrum = [0.] * bands
        spectrum[3] = -3.2
        gap = desk_panels.describe_gap(tone_space.Fingerprint(tuple(spectrum), ()), "R", "B")
        self.assertTrue(gap.startswith("B sits 3.2 dB below R around "), gap)
        flat = desk_panels.describe_gap(tone_space.Fingerprint((.2,) * bands, ()), "A", "B")
        self.assertIn("within half a dB", flat)


if __name__ == "__main__":
    unittest.main()
