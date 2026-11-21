"""Teaching one control: the examples kept, the profile, the check of a fresh take, and the teaching itself on a
seat, with its choices made in place. That it is shown in the real desk's window and fits it is in
test_lab_one_window.py."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import json
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import patch

import numpy as np

import support  # noqa: E402
from lab.hearing import audio
from lab.learner import calibration, dataset, folders, gestures, inputs, state, taught, teach

from test_lab_learner_gestures import note
from test_lab_learner_seat import seat_for, settle
from test_lab_learner_takes import four_inputs
from support import hosting


def phrase(kind, *, level=.08):
    return np.concatenate([note(110, kind, seconds=.65, level=level), np.zeros(4000, dtype=np.float32),
                           note(146.83, kind, seconds=.65, level=level)])


class TaughtTest(unittest.TestCase):
    def setUp(self):
        self.seat = seat_for(self)
        self.base = folders.root()

    def wave(self, name, samples):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        audio.write_wave(path, samples[:, None])
        return path

    def teach_two(self):
        for name in ("neck", "bridge"):
            taught.add_example("Charvel", "pickup", name.title(), self.wave(f"takes/{name}.wav", phrase(name)))

    def test_examples_are_kept_as_copies_so_a_take_that_goes_takes_nothing_with_it(self):
        # Before: an example was a path to the take; with the take moved or deleted, nothing could be learned again.
        self.teach_two()
        for path in (self.base / "takes").glob("*.wav"):
            path.unlink()
        rows = taught.examples("Charvel", "pickup")
        self.assertEqual([row["label"] for row in rows], ["Neck", "Bridge"])
        self.assertTrue(all(Path(row["wav"]).is_file() and folders.root() in Path(row["wav"]).parents for row in rows))
        profile = taught.fit("Charvel", "pickup")
        self.assertEqual(profile["labels"], ["Neck", "Bridge"])
        self.assertEqual(state.estimate(profile, note(110, "bridge", seconds=.5))["label"], "Bridge")
        examples, saved, mapping = taught.load("charvel", "pickup")
        self.assertEqual((len(examples), saved, mapping), (2, profile, None))

    def test_an_example_is_recorded_in_the_form_the_learners_labels_have(self):
        self.teach_two()
        row = taught.examples("Charvel", "pickup")[0]
        record = json.loads(Path(row["wav"]).with_name("record.json").read_text(encoding="utf-8"))
        learner_row = dataset.add("Charvel", "dk24_blade", self.wave("takes/other.wav", phrase("neck", level=.05)),
                                  {"pickup": 5, "volume": 10., "tone": 10.})
        shared = {"schema", "id", "instrument", "source_id", "excerpt_id", "source", "source_label", "source_start",
                  "source_end", "seconds", "peak_dbfs", "created_at"}
        self.assertLessEqual(shared, set(record))
        self.assertLessEqual(shared, set(learner_row))
        self.assertEqual(record["source"], "takes/neck.wav")              # relative to the learner's data folder
        self.assertEqual(Path(row["wav"]).parent.parent.name, "pickup-examples")

    def test_the_same_audio_is_not_an_example_twice_and_a_setting_keeps_its_first_spelling(self):
        take = self.wave("takes/neck.wav", phrase("neck"))
        taught.add_example("Charvel", "pickup", "Neck", take, start=0, end=.7)
        with self.assertRaisesRegex(ValueError, "already an example of Neck"):
            taught.add_example("Charvel", "pickup", "Bridge", take, start=.3, end=1.)
        again = taught.add_example("Charvel", "pickup", " NECK ", take, start=.7, end=1.3)
        self.assertEqual(again["label"], "Neck")
        self.assertEqual(taught.example_counts(taught.examples("Charvel", "pickup")), {"neck": ("Neck", 2)})
        taught.remove_example("Charvel", "pickup", again["id"])
        self.assertEqual(len(taught.examples("Charvel", "pickup")), 1)
        self.assertTrue(Path(again["wav"]).is_file())                     # taken out of use, not deleted
        with self.assertRaisesRegex(ValueError, "Name the setting"):
            taught.add_example("Charvel", "pickup", " ", take)

    def test_the_earlier_apps_list_of_paths_is_brought_into_the_store_once(self):
        kept = folders.recordings() / "clip-20261118-120000-abcdef"
        kept.mkdir(parents=True)
        audio.write_wave(kept / "dry.wav", phrase("neck")[:, None])
        folder = folders.taught("Charvel")
        folder.mkdir(parents=True)
        # As the earlier app wrote it: absolute paths into the program folder's tone_sessions, wherever it was.
        folders.write_json(folder / "pickup-examples.json", [
            {"label": "Neck", "wav": "C:/gone/qc/tone_sessions/recordings/clip-20261118-120000-abcdef/dry.wav", "end": .7},
            {"label": "Bridge", "wav": "C:/gone/qc/tone_sessions/recordings/clip-20261118-130000-abcdef/dry.wav"}])
        examples, _, _ = taught.load("Charvel", "pickup")
        self.assertEqual([(row["label"], row["source_end"]) for row in examples], [("Neck", .7)])     # what was found
        self.assertTrue((folder / "pickup-examples.json").is_file())                                  # left as it was
        self.assertEqual(len(taught.load("Charvel", "pickup")[0]), 1)                                 # not twice

    def test_a_pickup_selectors_settings_are_the_instruments_own(self):
        self.assertEqual(taught.settings("Charvel", "pickup"),
                         ("Neck", "Bridge", "Outer coils", "Both humbuckers", "Inner coils"))
        self.assertEqual(set(taught.settings("charvel", "pickup")), set(dataset.LAYOUTS["dk24_hh_toggle"]["pickups"]))
        self.assertEqual(taught.settings("A guitar of a friend", "pickup"), taught.SETTINGS["pickup"])
        self.assertEqual(taught.settings("Charvel", "tone")[0], "Tone 10")

    def test_a_check_is_made_at_the_cadence_of_live_listening_and_never_on_a_take_used_to_teach(self):
        self.teach_two()
        profile = taught.fit("Charvel", "pickup")
        mapping = taught.save_mapping("Charvel", "pickup", "Neck", "Bridge", "space", "+10")
        self.assertEqual(gestures.saved("Charvel", "pickup")[1], mapping)
        fresh = self.wave("takes/fresh.wav", np.concatenate(
            [note(110, kind, seconds=seconds) for kind, seconds in (("neck", .75),) * 2 + (("bridge", .8),)
             + (("neck", .7),) * 3]))
        examples = taught.examples("Charvel", "pickup")
        samples, overall, track, heard = taught.check(profile, mapping, examples, fresh)
        self.assertAlmostEqual(track[1]["time"] - track[0]["time"], state.HOP_SECONDS, places=3)
        self.assertEqual(track[0]["time"], state.WINDOW_SECONDS)
        self.assertEqual(len(heard), 1)
        lines = taught.timeline("pickup", track, heard)
        self.assertTrue(any("Return Neck → Bridge → Neck" in text for _, text, _ in lines))
        self.assertTrue(any(text == "Bridge · close match" for _, text, _ in lines))
        self.assertEqual(taught.preview(samples, (0, audio.RATE)).dtype, np.int16)
        with self.assertRaisesRegex(ValueError, "used to teach"):
            taught.check(profile, mapping, examples, self.base / "takes" / "neck.wav")
        with self.assertRaisesRegex(ValueError, "percentage"):
            taught.save_mapping("Charvel", "pickup", "Neck", "Bridge", "space", "a lot")

    def test_an_estimate_is_said_as_closeness_and_volume_as_read_from_the_level(self):
        self.assertEqual(taught.match_words("Neck", .9), "Neck · close match")
        self.assertEqual(taught.match_words("Neck", .6), "Neck · near match")
        self.assertEqual(taught.match_words("Neck", .2), "Neck · far match")
        self.assertEqual(taught.match_words(None, .9), "Uncertain")
        self.assertEqual(taught.match_words("Volume 5", .9, "volume"), "Volume 5 · close match · by dry level")
        self.assertIn("level of the dry signal", taught.HINTS["volume"])
        for words in ("strong", "confiden", "calibrat"):
            for text in (*taught.HINTS.values(), taught.match_words("Neck", .9), taught.match_words("V", .9, "volume")):
                self.assertNotIn(words, text.casefold())


class TeachWindowTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"No display: {error}")
        self.root.withdraw()
        self.seat = seat_for(self, self.root)
        hosting(self.seat.desk, self.root)
        self.window = teach.open(self.seat)
        self.panel = self.window.panel

    def tearDown(self):
        gestures.stop()
        teach._shown.clear()
        self.window.worker.shutdown(wait=True)
        self.root.destroy()
        self.window = self.panel = self.root = None
        support.free_now()

    def test_it_is_named_for_what_it_does_and_the_earlier_name_still_opens_it(self):
        self.assertEqual(self.window.TITLE, "Teach a control")
        self.assertEqual(self.window.frame.winfo_toplevel(), self.root)     # in the desk's window, not one of its own
        self.assertIs(self.seat.desk.hosted, self.window)
        self.assertIs(calibration.open, teach.open)
        self.assertIs(calibration.open(self.seat), self.window)
        self.assertEqual(self.panel.instrument.get(), "Charvel")
        self.assertEqual(self.panel.example_label.get(), "Neck")
        self.assertEqual(tuple(self.panel.setting_chips.chips)[:2], ("Neck", "Bridge"))

    def test_the_instrument_and_the_setting_are_chosen_from_chips_or_typed_in_place(self):
        # In place of the two drop-downs that also took typing: the choices as chips, and a line for a new one.
        self.assertIn("Charvel", self.panel.instrument_chips.chips)
        self.root.update()
        self.panel.setting_chips.chips["Bridge"].event_generate("<ButtonRelease-1>")
        self.root.update()
        self.assertEqual(self.panel.example_label.get(), "Bridge")
        self.panel.setting_entry.delete(0, "end")
        self.panel.setting_entry.insert(0, "Neck, tone rolled off")         # a setting of the player's own
        self.assertEqual(self.panel.example_label.get(), "Neck, tone rolled off")
        self.panel.new_instrument.set("A friend's Tele")
        self.panel.name_instrument()
        self.assertEqual(self.panel.instrument.get(), "A friend's Tele")
        self.assertIn("A friend's Tele", self.panel.instrument_chips.chips)
        self.assertEqual(self.panel.new_instrument.get(), "")
        self.assertEqual(tuple(self.panel.setting_chips.chips)[:3], taught.SETTINGS["pickup"][:3])

    def test_a_wav_to_label_or_to_check_is_chosen_in_place(self):
        # In place of the two file dialogs: the WAVs found where recordings are kept, or a path typed in.
        take = folders.recordings() / "clip-20261118-010000-abcdef" / "dry.wav"
        take.parent.mkdir(parents=True)
        audio.write_wave(take, phrase("neck")[:, None])
        self.panel.example_files.look()
        self.assertIn("dry.wav", self.panel.example_files.list.get(0))
        self.panel.example_files.path.insert(0, str(take.with_name("missing.wav")))
        self.panel.example_files.typed()
        self.assertIn("There is no file at", self.panel.example_files.said.cget("text"))
        self.assertIsNone(self.panel.example_source)
        self.panel.example_files.list.selection_set(0)
        self.panel.example_files.use()
        self.assertEqual(self.panel.example_source, (take.resolve(), "dry.wav"))
        self.assertEqual(self.panel.source_text.get(), "Ready to label · dry.wav")
        self.panel.check_files.look()
        self.panel.check_files.list.selection_set(0)
        self.panel.check_files.use()                                         # nothing is learned yet: said, not done
        self.assertEqual(self.panel.status.get(), "Learn two settings first.")

    def test_a_gesture_is_mapped_from_the_settings_learned_as_chips(self):
        folder = folders.taught("Charvel")
        folder.mkdir(parents=True)
        folders.write_json(folder / "pickup.json", {
            "schema": 1, "guitar": "Charvel", "control": "pickup", "labels": ["Neck", "Bridge"],
            "notes": {"45": {"centers": [[0.] * 12, [1.] * 12], "scale": [2.] * 12, "radii": [1., 1.]}}})
        self.panel.loaded_selection = None
        self.panel.load_selection()
        self.assertEqual(tuple(self.panel.home_chips.chips), ("Neck", "Bridge"))
        self.assertEqual(tuple(self.panel.excursion_chips.chips), ("Neck", "Bridge"))
        self.assertEqual((self.panel.home.get(), self.panel.excursion.get()), ("Neck", "Bridge"))

    def test_going_back_to_the_desk_ends_a_recording_and_leaves_gesture_listening_on(self):
        from threading import Event

        stop = Event()
        self.panel.recording = {"stop": stop}
        self.panel.live_running = True
        self.seat.desk.back()
        self.assertTrue(stop.is_set())
        self.assertTrue(self.panel.live_running)        # the guitar can be played on the desk while it listens
        self.assertFalse(self.window.showing())
        self.panel.recording, self.panel.live_running = None, False
        self.assertIs(teach.open(self.seat), self.window)       # the same one, as it was left
        self.assertTrue(self.window.showing())

    def record(self, played):
        """Press Record, let the stand-in driver deliver `played`, press Stop, and wait for the take to be used."""
        def deliver(seconds, channels, *, stop, ready):
            ready.set()
            stop.wait(10)
            return played, []

        with patch.object(inputs, "record_inputs", deliver):
            self.panel.record_dry("example")
            self.assertTrue(settle(self.seat, lambda: self.panel.recording and self.panel.recording["ready"].is_set(),
                                   root=self.root))
            self.seat.tick()
            self.assertEqual(self.panel.source_text.get(), "Recording. Play your notes, then press Stop.")
            self.assertEqual(self.seat.studio.audio_held_by, "learner")
            self.panel.record_dry("example")
            self.assertTrue(settle(self.seat, lambda: self.panel.recording is None and self.seat.job is None,
                                   root=self.root))

    def test_a_recorded_example_goes_through_the_seat_and_is_kept_under_the_setting_chosen(self):
        played = four_inputs(2, seconds=len(phrase("neck")) / audio.RATE)
        played[:, 1] = phrase("neck")
        self.record(played)
        self.assertEqual(self.seat.studio.host.calls, [("inputs", (2,))])       # the engine was asked; no unit opened
        self.assertIsNone(self.seat.studio.audio_held_by)
        self.assertEqual([row["label"] for row in self.panel.examples], ["Neck"])
        self.assertEqual(self.panel.status.get(), "Saved Neck as an example.")
        self.assertEqual(self.panel.example_label.get(), "Bridge")              # the next setting to teach
        self.assertIn("Neck ×1", self.panel.progress_text.get())
        self.assertIn("Clip saved", self.seat.studio.statuses[-1])
        take, = folders.recordings().glob("clip-*")
        self.assertEqual(json.loads((take / "clip.json").read_text(encoding="utf-8"))["guitar"], "Charvel")

    def test_a_take_with_no_dry_recording_is_said_and_nothing_is_added(self):
        self.seat.studio.host.error = RuntimeError("no unit")
        noisy = four_inputs(1)
        noisy[:, 1] = noisy[:, 0]
        self.record(noisy)
        self.assertEqual(self.panel.examples, [])
        self.assertIn("No dry recording could be used", self.panel.source_text.get())
        self.assertIn("routing needs checking", self.panel.source_text.get())

    def test_show_estimate_listens_for_the_chosen_control_and_says_closeness(self):
        folder = folders.taught("Charvel")
        folder.mkdir(parents=True)
        folders.write_json(folder / "pickup.json", {
            "schema": 1, "guitar": "Charvel", "control": "pickup", "labels": ["Neck", "Bridge"],
            "notes": {"45": {"centers": [[0.] * 12, [1.] * 12], "scale": [2.] * 12, "radii": [1., 1.]}}})
        self.panel.loaded_selection = None
        self.panel.load_selection()
        self.assertEqual(self.panel.profile["labels"], ["Neck", "Bridge"])
        with patch.object(teach.guitar_gestures, "start") as start:
            self.panel.observe.set(True)
            self.panel.change_observe()
        start.assert_called_once()
        asked = start.call_args.kwargs
        self.assertEqual((asked["instrument"], asked["control"], asked["arm"], asked["observe"]),
                         ("Charvel", "pickup", False, True))
        self.assertEqual(asked["listener"], self.panel.live_event)
        self.assertTrue(self.panel.live_running)
        self.panel.live_event({"event": "guitar-state", "control": "pickup", "label": "Neck", "closeness": .9, "reason": ""})
        self.assertEqual(self.panel.live_text.get(), "Neck · close match")
        self.panel.live_event({"event": "guitar-stopped"})
        self.assertFalse(self.panel.live_running)
        self.assertFalse(self.panel.observe.get())

    def test_the_instrument_follows_the_desk_when_the_teaching_is_shown(self):
        self.seat.desk.back()
        self.seat.studio.state.instrument = "Les Paul"
        self.window.show()
        self.assertEqual(self.panel.instrument.get(), "Les Paul")
        self.assertEqual(tuple(self.panel.setting_chips.chips)[:3], ("Neck", "Bridge", "Both"))


def tearDownModule():
    # Tk objects left as garbage here must be freed on the main thread: if a later test's worker thread runs
    # the collector, Tcl aborts the whole interpreter ("Tcl_AsyncDelete").
    support.free_now()


if __name__ == "__main__":
    unittest.main()
