"""The desk and what the other packages now say: the measurements Difference reads, sounds heard with the
earlier test signals, choices with the press they were, takes with their gesture, the tools' one way of keeping
a take, remedies that have run out, and a knob judged by another quality's measure."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lab.desk.difference import difference
from lab.desk.listening import _checked_words
from lab.desk.requests import reads_as_change
from lab.hearing import profile as profile_module
from lab.sound import words
from lab.qc.sim import SimAudio
from lab.sound import catalog
from lab.sound.chain import Block, Chain, Row
from lab.sound.sounds import Sound
from lab.tools import phrases
from test_lab_design import GREENBACK, HIWATT, PLEXI, V30
from support import StudioCase
from test_lab_studio_flow import FlowCase, one_row


def record(**values):
    """A measurement with the figures every record has, and `values` over them ("probe" is merged)."""
    probe = {"distortion_db": -40., "distortion_soft_db": -45., "version": 2, **values.pop("probe", {})}
    return {"level_db": -20., "compression": .3, "brightness_oct": 0., "low_end_db": 0., "tail_db": -40.,
            "distortion": "clean", "probe": probe, **values}


class DifferenceTest(unittest.TestCase):
    def test_brightness_is_the_highs_against_the_body_when_both_were_measured_so(self):
        lines = " ".join(difference(record(brightness_db=0., brightness_oct=.5),
                                    record(brightness_db=4., brightness_oct=-.5)))
        self.assertIn("B is 4 dB brighter (the energy above 1 kHz against 200 Hz to 1 kHz)", lines)
        self.assertNotIn("spectral centre", lines)        # which here points the other way, and is not what is meant

    def test_without_it_the_spectral_centre_is_called_what_it_is(self):
        lines = " ".join(difference(record(brightness_db=0., brightness_oct=0.), record(brightness_oct=-.4)))
        self.assertIn("B's spectral centre sits 0.4 octave darker (lower) than A's", lines)
        self.assertNotIn("dB darker", lines)

    def test_the_tail_is_the_clicks_late_energy_when_both_have_it(self):
        ringing = difference(record(probe={"late_energy_db": -30.}, tail_db=-10.),
                             record(probe={"late_energy_db": -12.}, tail_db=-40.))
        self.assertIn("After a click, B rings on 18 dB more (echo and reverb).", ringing)
        self.assertFalse(any("after you stop" in line for line in ringing))
        earlier = difference(record(tail_db=-40.), record(probe={"late_energy_db": -12.}, tail_db=-30.))
        self.assertIn("B rings on 10 dB more after you stop.", earlier)

    def test_what_could_not_be_measured_is_not_compared(self):
        unmeasured = {"intermod_db": None, "level_wobble": None, "pitch_wobble": None, "even_odd_db": None,
                      "decay_rt60_s": None, "late_energy_db": None}
        measured = {"intermod_db": -20., "level_wobble": 9., "pitch_wobble": 4., "even_odd_db": 12.,
                    "decay_rt60_s": 1.4, "late_energy_db": -20.}
        self.assertEqual(difference(record(probe=unmeasured), record(probe=measured)),
                         ["No clear difference measured."])
        both = " ".join(difference(record(probe={**measured, "intermod_db": -40., "decay_rt60_s": .4,
                                                 "level_wobble": 1., "even_odd_db": 0.}), record(probe=measured)))
        for said in ("Two notes together: B is 20 dB rougher", "lean 12 dB more to the even",
                     "takes 1.4 s to fall 60 dB on B, 0.4 s on A", "swings 9% on B, 1% on A"):
            self.assertIn(said, both)

    def test_a_chain_that_widens_the_drop_is_said_to(self):
        lines = " ".join(difference(record(compression=-.5), record(compression=.5)))
        self.assertIn("B is more compressed", lines)
        self.assertIn("A gives back 18 dB (it widens the drop) and B 6 dB", lines)
        self.assertIn("24 dB or more (it widens the drop)", " ".join(difference(record(compression=-1.), record())))

    def test_a_record_with_nothing_but_words_says_nothing_and_does_not_fail(self):
        self.assertEqual(difference({"distortion": "clean"}, {"distortion": "crunch", "probe": None}),
                         ["No clear difference measured."])


class ChecksTest(unittest.TestCase):
    def test_a_knob_judged_by_another_qualitys_measure_says_so(self):
        found = {"sustain": {"measure": "compression", "change": .12, "sign": 1, "verdict": "as intended",
                             "units": "", "proxy": True},
                 "drive": {"measure": "distortion_db", "change": 6., "sign": 1, "verdict": "as intended",
                           "units": "dB", "proxy": False}}
        line = _checked_words(found, {"sustain", "drive"}, None)
        self.assertIn("Sustain did what it says (compression +0.120; judged by compression)", line)
        self.assertEqual(line.count("judged by"), 1)


class HeardAgainTest(FlowCase):
    def test_a_sound_heard_with_the_earlier_test_signals_is_listened_to_again_after_the_ones_never_heard(self):
        self.studio.idle_seconds = 30.
        old = Sound("Old", one_row(PLEXI, V30), {"kind": "designed"})
        old.heard = {**record(probe={"version": 1}), "phrase": self.studio.phrase_key(), "digest": old.chain.digest(),
                     "scene": 0}
        del old.heard["probe"]["version"]                 # as the first layout wrote it
        new = Sound("New", one_row(HIWATT, GREENBACK), {"kind": "designed"})
        self.studio.put("a", old)
        self.studio.put("b", new)
        self.settle()
        sent = self.sent()
        self.studio.idle_seconds = 0.
        self.settle()
        heard = [args[0].digest() for name, args in sent if name == "hear"]
        self.assertEqual(heard, [new.chain.digest(), old.chain.digest()])
        self.assertEqual(self.studio.sound("a").heard["probe"]["version"], 2)
        self.settle()
        self.assertEqual(len([name for name, _ in sent if name == "hear"]), 2)       # and not again


class StoppedReplayTest(FlowCase):
    idle_seconds = 30.              # nothing is listened to until the test says so

    def test_a_listening_stopped_while_the_phrase_replays_is_not_a_failed_listening(self):
        # When Stop comes while a phrase is being replayed, the audio driver ends the replay with
        # `lab.hearing.audio.Cancelled`. That is a stop like one between two writes (`lab/qc/host.py`): the
        # sound is left to be listened to again later, with no failed measurement kept for it.
        passes = []
        reamp = SimAudio.reamp

        def stopped_after_the_first_pass(audio, dry, **options):
            returned = reamp(audio, dry, **options)
            if options.get("cancel") is None:
                return returned                         # measuring the round trip, which is not a listening
            passes.append(len(dry))
            if len(passes) == 1:
                self.host.stop_flag.set()               # Stop, as the player's own playing sends it
            return returned

        self.studio.put("b", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.settle()
        with mock.patch.object(SimAudio, "reamp", stopped_after_the_first_pass):
            self.studio.idle_seconds = 0.
            self.until(lambda: len(passes) >= 1 and not self.host.busy, 20, "the listening and its end")
            self.studio.idle_seconds = 30.
            self.settle()
        self.assertEqual(len(passes), 1)                # the replay did end there
        sound = self.studio.sound("b")
        self.assertNotIn("Could not listen", self.studio.state.status)
        self.assertNotIn("failed", sound.heard or {})


class ChoiceTest(FlowCase):
    def test_a_choice_says_which_press_it_was(self):
        self.studio.put("a", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.studio.put("b", Sound("Hiwatt", one_row(HIWATT, GREENBACK), {"kind": "designed"}))
        self.settle()
        a, b = self.studio.state.a, self.studio.state.b
        self.studio.mark("keeper", "b")
        self.studio.mark("not this", "b")
        choices = [json.loads(line) for line in (self.folder / "choices.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(item["kept"], item["over"], item["event"]) for item in choices],
                         [(b, a, "keeper"), (a, b, "not this")])


class TakesTest(FlowCase):
    def record(self, setting, gesture=None):
        self.studio.record(setting, 1., gesture=gesture)
        self.settle()
        return self.studio.recorded_phrases()[0]

    def test_a_take_is_kept_the_tools_way_and_carries_its_gesture(self):
        with mock.patch.object(phrases, "keep", wraps=phrases.keep) as keep:
            item = self.record("Bridge · Series", profile_module.GESTURES[0])
        self.assertEqual(keep.call_count, 1)
        self.assertEqual(keep.call_args.kwargs["setting"], "Bridge · Series")
        self.assertEqual(item["gesture"], profile_module.GESTURES[0])
        self.assertEqual(self.studio.phrase[0], item["id"])
        on_disk = json.loads((self.folder / "phrases" / item["id"] / "phrase.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["gesture"], profile_module.GESTURES[0])
        self.assertNotIn("gesture", self.record("Neck · Series"))              # none given: none written

    def test_a_take_that_is_not_kept_says_why(self):
        before = self.studio.phrase
        with mock.patch.object(phrases, "keep", side_effect=phrases.NotKept("Nothing came in on the guitar input.")):
            self.studio.record("Neck · Series", 1.)
            self.settle()
        self.assertEqual(self.studio.state.status, "Nothing came in on the guitar input.")
        self.assertIs(self.studio.phrase, before)

    def test_settings_are_compared_on_takes_of_the_same_gesture_and_said_not_to_be_otherwise(self):
        gesture = profile_module.GESTURES[0]
        self.record("Bridge · Series", gesture)
        self.record("Neck · Series")
        with mock.patch.object(profile_module, "profile", wraps=profile_module.profile) as made:
            _, lines = self.studio.guitar_profile()
        self.assertEqual(sorted((setting, tag) for setting, _, tag in made.call_args.args[0]),
                         [("Bridge · Series", gesture), ("Neck · Series", None)])
        self.assertTrue(any("not compared" in line for line in lines))
        self.record("Neck · Series", gesture)
        _, lines = self.studio.guitar_profile()
        self.assertFalse(any("not compared" in line for line in lines))
        # What the designer is told of the phrase in use is shown with it.
        self.studio.guitar_lines()
        self.settle()
        told = [line for line in self.studio.guitar_lines() if "the designer is told" in line]
        self.assertEqual(len(told), 1)
        self.assertIn("Neck · Series", told[0])
        self.assertIn("presets assume", told[0])


class WordsTest(FlowCase):
    def test_a_remark_whose_remedies_have_run_out_says_so_and_asks_nothing_of_the_assistant(self):
        self.a_round()
        asked = len(self.script.asked)
        said = []
        offered = []
        for _ in range(8):
            self.studio.ask("muddy")
            said.append(self.studio.state.status)
            offered.append(self.studio.next_remedy())
            if "Nothing more to try" in said[-1]:
                break
        knobs = dict(self.studio.sound("b").knobs)
        self.assertIn("Nothing more to try for “muddy”", said[-1])
        self.assertGreater(len(said), 1)                  # a remedy or more came first
        self.assertIsNone(offered[-2])                    # and the one before it offered no next one
        self.studio.ask("muddy")                          # said yet again: the same, and still no call
        self.assertIn("Nothing more to try", self.studio.state.status)
        self.assertEqual((len(self.script.asked), self.studio.cancellable(), self.studio.state.working),
                         (asked, False, ""))
        self.assertEqual(self.studio.sound("b").knobs, knobs)               # the last remedy stays on
        # The line says how to ask on purpose, and a request of that kind does go to the assistant.
        self.assertIn("say what to change and what to keep", self.studio.state.status)
        example = "less mud in the palm mutes, same gain"
        self.assertIsNone(words.read(example))
        self.assertTrue(reads_as_change(example))


class ListeningLevelTest(FlowCase):
    def test_the_levels_tools_offset_is_cleared_from_the_desk(self):
        self.studio.listening = {"master_db": 0, "loudness_db": 3, "muted": False, "match": True}
        sent = self.sent()
        self.studio.clear_listening()
        self.assertIsNone(self.studio.listening)
        self.assertEqual(sent[-1][0], "lab.tools.levels.clear")
        self.settle()
        self.assertEqual(self.studio.state.status, "Back at the desk's own level.")


# ---- Faults the reviews of 2026-11-20 found (docs/CODEBASE_REVIEW.md), each reproduced as it was found ----

def switched():
    """A sound whose second scene has more gain than the others."""
    gain = catalog.model(PLEXI).index("GAIN")
    return Chain((Row((Block(PLEXI).set(gain, (.3, .9) + (.3,) * 6), Block(V30)), inlet=1, outlet=19),))


class EvidenceTest(StudioCase):
    """What was measured holds only for what it was measured on. (review 2026-11-20)"""

    def test_a_measurement_belongs_to_the_part_of_the_phrase_it_was_made_on(self):
        self.put_two()
        b = self.studio.sound("b")
        b.heard = {"phrase": self.studio.phrase_key(), "digest": b.chain.digest(), "level_db": -20.,
                   "loudness_lufs": -20.}
        self.studio.store.save(b)
        self.assertIsNotNone(self.studio.evidence(self.studio.store.sound(b.id)))
        self.studio.phrase_whole = (self.studio.phrase[1], 0, 12000)
        self.assertIsNone(self.studio.evidence(self.studio.store.sound(b.id)))

    def test_a_measurement_of_the_first_scene_does_not_hold_for_another(self):
        sound = Sound("Switched", switched(), {"kind": "designed"})
        sound.heard = {"phrase": self.studio.phrase_key(), "digest": sound.chain.digest(), "scene": 0, "level_db": -20.}
        self.assertIsNotNone(self.studio.evidence(sound))
        self.studio.set(scene=1)
        self.assertIsNone(self.studio.evidence(sound))

    def test_difference_reads_the_sounds_as_they_are_set_now(self):
        key = self.studio.phrase_key()

        def heard(brightness, digest, shaped=None):
            record = {"phrase": key, "digest": digest, "scene": 0, "level_db": -20., "compression": .3,
                      "brightness_oct": brightness, "low_end_db": 0., "tail_db": -40., "distortion": "clean",
                      "probe": {"distortion_db": -40.}}
            if shaped is not None:
                record["shaped"] = {**record, "phrase": key, "knobs": {"brightness": -.5},
                                    "brightness_oct": shaped}
            return record

        a, b = one_row(PLEXI, V30), one_row(HIWATT, GREENBACK)
        self.studio.put("a", Sound("A", a, {"kind": "designed"}, heard=heard(.0, a.digest())))
        self.studio.put("b", Sound("B", b, {"kind": "designed"}, knobs={"brightness": -.5},
                                   heard=heard(.1, b.digest(), shaped=-.4)))
        self.settle()
        lines = " ".join(self.studio.difference())
        self.assertIn("0.4 octave darker", lines)


class ShapeCheckTest(StudioCase):
    """The check of a turned knob by listening. (review 2026-11-20)"""

    def test_a_phrase_changed_during_the_check_changes_no_knob(self):
        self.put_two()
        self.studio.settle_seconds = 0.
        call = self.host.call

        def change_take_meanwhile(name, *args, **options):
            if name == "hear_shaped":
                self.studio.phrase_whole = (self.studio.phrase[1], 0, 12000)
            return call(name, *args, **options)

        self.host.call = change_take_meanwhile
        self.studio.turn({"brightness": .3}, "b")
        self.settle(30)
        sound = self.studio.sound("b")
        self.assertEqual(sound.knobs, {"brightness": .3})
        self.assertNotIn("shaped", sound.heard or {})

    def test_the_shape_check_measures_the_scene_heard(self):
        self.studio.put("b", Sound("Switched", switched(), {"kind": "designed"}))
        self.settle()
        self.studio.scene(1)
        self.settle()
        self.studio.settle_seconds = 0.
        asked, call = [], self.host.call

        def noting(name, *args, **options):
            if name in ("hear", "hear_shaped"):
                asked.append((name, args[-1]))
            return call(name, *args, **options)

        self.host.call = noting
        self.studio.turn({"brightness": .3}, "b")
        self.settle(30)
        self.assertEqual(asked, [("hear", 1), ("hear_shaped", 1)])
        self.assertEqual(self.studio.sound("b").heard["scene"], 1)
        self.assertEqual(self.studio.sound("b").heard["shaped"]["scene"], 1)

    def test_a_check_is_current_only_for_its_own_sound(self):
        self.studio.put("a", Sound("Same", one_row(PLEXI, V30), {"kind": "designed"}))
        self.settle()
        elsewhere = Sound("Same elsewhere", one_row(PLEXI, V30), {"kind": "designed"})
        self.studio.store.save(elsewhere)
        inputs_ = (self.studio.phrase_key(), self.studio.phrase[1], self.studio.state.scene,
                   elsewhere.chain.digest(), elsewhere.id)
        self.assertFalse(self.studio._current(inputs_))


class RecordingTest(StudioCase):
    def test_recording_keeps_the_dry_and_wet_takes_and_says_when_to_play(self):
        """(review 2026-11-20)"""
        self.put_two()
        said = []
        self.studio.listeners.append(lambda state: said.append(state.working))
        self.studio.record("Neck", 1.)
        self.settle()
        self.assertIn("Recording · play", " | ".join(said))
        phrase_id = self.studio.phrase[0]
        folder = self.folder / "phrases" / phrase_id
        self.assertTrue((folder / "dry.wav").is_file(), "the dry take")
        self.assertTrue((folder / "wet.wav").is_file(), "the wet take")
        info = json.loads((folder / "phrase.json").read_text(encoding="utf-8"))
        self.assertEqual(info["wet"]["name"], "Hiwatt")
        # Recorded while the guitar played through B: cut from the open input, from a second before it was asked.
        self.assertEqual(len(self.studio.phrase[1]), 96000)
        self.assertEqual(self.studio.state.mode, "guitar")


if __name__ == "__main__":
    unittest.main()
