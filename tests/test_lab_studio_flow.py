"""The main flow in ordinary use, on the simulated unit with a scripted assistant: what a request is taken to be,
when the app listens by itself, what a failure leaves behind, and who has the audio input.

Each test here failed on the desk as the whole-codebase review found it (docs/WHOLE_CODEBASE_REVIEW.md)."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
from concurrent.futures import Future
import json
from pathlib import Path
import sys
import threading
import time
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lab.desk import studio as studio_module
from lab.desk.studio import Rounds, Studio, reads_as_change
from lab.hearing.audio import write_wave
from lab.qc import engine as engine_module
from lab.qc.host import Host
from lab.qc.unit import Stopped
from lab.sound import design
from lab.sound.chain import Block, Chain, Row
from lab.sound.sounds import Sound
from support import StudioCase
from test_lab_design import GREENBACK, PLEXI, V30, answer, block, reply

FACTORY = {"folder_key": "/opt/neuraldsp/Factory Library", "position": 0, "is_factory": True}
REVISION = {"name": "x", "target": "t", "explanation": "e", "differs": "", "scenes": [],
            "blend": {"level_db": 0, "spread": 0}, "sources": [],
            "rows": [{"row": 0, "split_at": -1, "join_at": -1,
                      "blocks": [{"column": 0, "model_id": PLEXI, "settings": [{"name": "GAIN", "value": 3}],
                                  "scene_settings": [], "off_in_scenes": []},
                                 {"column": 1, "model_id": V30, "settings": [],
                                  "scene_settings": [], "off_in_scenes": []}]}]}


def three(*names):
    return reply(*[answer(name, [block(PLEXI, 0, gain=gain), block(GREENBACK, 1)], differs=name)
                   for name, gain in zip(names, (3, 6, 9))])


def one_row(*models):
    return Chain((Row(tuple(Block(model) for model in models), inlet=1, outlet=19),))


def with_scenes(count=2):
    gain = Block(PLEXI).set(0, tuple(.2 + .3 * scene for scene in range(count)) + (.2,) * (8 - count))
    return Chain((Row((gain, Block(GREENBACK)), inlet=1, outlet=19),))


class FlowCase(StudioCase):
    idle_seconds = 0.        # silent listening starts at once, not after the unit was left alone

    def poll_for(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.studio.poll()
            time.sleep(.002)

    def a_round(self, *names, request="make a classic rock crunch"):
        self.script.answers.append(three(*(names or ("One", "Two", "Three"))))
        self.studio.ask(request)
        self.settle()

    def sent(self):
        """Note every command sent to the engine from now on, as (name, arguments)."""
        found, call = [], self.host.call

        def noting(name, *args, **options):
            found.append((name, args))
            return call(name, *args, **options)

        self.host.call = noting
        self.addCleanup(setattr, self.host, "call", call)
        return found

    def patch_engine(self, name, replacement):
        original = getattr(engine_module.Engine, name)
        setattr(engine_module.Engine, name, replacement)
        self.addCleanup(setattr, engine_module.Engine, name, original)
        return original

    def log(self):
        path = self.folder / "errors.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""


class RequestRoutingTest(FlowCase):
    def test_the_readmes_own_sentences_are_read_as_it_says(self):
        for text in ("Comfortably Numb, the second solo", "More Than a Feeling", "John Mayer", "Pink Floyd - Time",
                     "make a blues tone", "jazzy clean", "Turn the Page", "make the first request"):
            self.assertFalse(reads_as_change(text), text)
        for text in ("more sustain from the fuzz, without more mud", "a bit tighter", "muddy when I palm mute",
                     "make it heavier", "cleaner, it breaks up too early", "swap the amp for a Vox",
                     "the delay in dotted eighths", "too much bass", "crunchier, like AC/DC"):
            self.assertTrue(reads_as_change(text), text)

    def test_a_song_starts_a_round_and_a_change_revises_b_with_a_sound_on_b(self):
        self.a_round()
        first = self.studio.state.round
        song = three("Solo one", "Solo two", "Solo three")
        song["song"] = {"artist": "Pink Floyd", "title": "Comfortably Numb", "part": "second solo"}
        self.script.answers.append(song)
        self.studio.ask("Comfortably Numb, the second solo")
        self.settle()
        self.assertNotEqual(self.studio.state.round, first)                 # a round of its own, not a revision
        self.assertEqual(self.studio.sound("b").name, "Solo one Studio")
        self.assertEqual(self.studio.state.song, "Pink Floyd - Comfortably Numb")
        second = self.studio.state.round
        self.script.answers.append({**REVISION, **design.as_form(self.studio.sound("b").chain)})
        self.studio.ask("more sustain from the fuzz, without more mud")
        self.settle()
        revised = self.studio.sound("b")
        self.assertEqual((revised.origin["kind"], self.studio.state.round), ("revised", second))
        self.assertIn("current_chain", self.script.asked[-1][0])


class ListeningTest(FlowCase):
    def test_a_preset_brought_in_is_listened_to_by_itself_once_for_both_sides(self):
        sent = self.sent()
        self.studio.work_on(FACTORY, "Brit 2203")
        self.settle()
        a, b = self.studio.sound("a"), self.studio.sound("b")
        self.assertIn("probe", a.heard or {})
        self.assertIn("probe", b.heard or {})
        self.assertEqual([name for name, _ in sent].count("hear"), 1)        # one chain, heard once for both
        self.assertEqual(self.studio.difference(), ["Listening heard no clear difference between A and B."])
        self.assertEqual(self.studio.state.shown, "pair")                   # and A and B are ready again

    def test_what_arrived_while_the_guitar_was_played_is_listened_to_once_the_strings_are_quiet(self):
        self.a_round()
        self.host.call("lab.qc.sim.strum", True).result(10)
        self.until(lambda: self.studio.state.mode == "guitar")              # the guitar is live by itself
        self.a_round("Four", "Five", "Six", request="make a plexi lead")
        self.poll_for(.5)
        heard = [self.studio.store.sound(item["sound"]).heard for item in self.studio.round()["candidates"]]
        self.assertFalse(any(heard))                                        # never while the strings sound
        self.assertEqual(self.studio.state.mode, "guitar")
        self.host.call("lab.qc.sim.strum", False).result(10)
        self.until(lambda: all("probe" in (self.studio.store.sound(item["sound"]).heard or {})
                               for item in self.studio.round()["candidates"]), 20)
        self.until(lambda: self.studio.state.mode == "guitar")              # and the guitar is back after it

    def test_a_note_played_during_a_silent_listen_ends_it_and_the_guitar_is_heard_again(self):
        from lab.hearing.audio import Played

        original, played = engine_module.Engine.hear, []

        def hear(engine, chain, dry, scene=0, *, stop=None, interrupt=False):
            if interrupt and not played:
                played.append(chain.digest())
                raise Played("The guitar was played.")
            return original(engine, chain, dry, scene, stop=stop, interrupt=interrupt)

        self.patch_engine("hear", hear)
        self.studio.put("b", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.until(lambda: played and self.studio.sounding == ("b", "guitar"))
        self.assertEqual(self.studio.state.status_kind, "")                 # said nothing: the guitar plays
        self.assertEqual(self.log(), "")
        self.until(lambda: "probe" in (self.studio.sound("b").heard or {}), 20)     # listened to later

    def test_a_paused_guitar_stays_paused_and_listening_goes_on_meanwhile(self):
        self.studio.idle_seconds = 30.
        self.studio.put("b", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.until(lambda: self.studio.sounding == ("b", "guitar"))
        self.studio.pause()
        self.studio.idle_seconds = 0.
        self.until(lambda: "probe" in (self.studio.sound("b").heard or {}))
        self.settle()
        self.assertIsNone(self.studio.state.mode)
        self.studio.select("a")
        self.assertIsNone(self.studio.state.mode)                           # a click on a tile does not play
        self.studio.play("guitar")
        self.assertTrue(self.studio.live)

    def test_listening_waits_until_the_unit_has_been_left_alone_for_a_moment(self):
        self.studio.idle_seconds = 30.
        sent = self.sent()
        self.studio.put("b", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.settle()
        self.poll_for(.5)
        self.assertNotIn("hear", [name for name, _ in sent])
        self.studio.idle_seconds = 0.
        self.settle()
        self.assertIn("hear", [name for name, _ in sent])

    def test_a_new_part_of_the_phrase_is_listened_to_again(self):
        self.studio.put("b", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.settle()
        before = self.studio.sound("b").heard["phrase"]
        whole = self.studio.phrase[1]
        self.studio.use_phrase("take", whole, 0, 12000)
        self.settle()
        heard = self.studio.sound("b").heard
        self.assertNotEqual(heard["phrase"], before)
        self.assertIsNotNone(self.studio.evidence(self.studio.sound("b")))

    def test_a_sound_whose_scenes_differ_is_listened_to_at_the_scene_heard(self):
        self.studio.put("b", Sound("Switched", with_scenes(2), {"kind": "designed"}))
        self.settle()
        self.assertEqual(self.studio.sound("b").heard["scene"], 0)
        self.studio.scene(1)
        self.settle()
        sound = self.studio.sound("b")
        self.assertEqual(sound.heard["scene"], 1)
        self.assertIsNotNone(self.studio.evidence(sound))

    def test_a_scene_the_sounds_now_on_show_do_not_have_is_not_kept(self):
        self.studio.put("b", Sound("Scenes", with_scenes(3), {"kind": "designed"}))
        self.settle()
        self.studio.scene(2)
        self.settle()
        self.studio.put("b", Sound("Plain", one_row(PLEXI, V30), {"kind": "designed"}))
        self.settle()
        self.assertEqual(self.studio.state.scene, 0)
        self.studio.play("guitar")
        self.settle()
        self.assertEqual(self.host.call("lab.tools.probe.scene").result(10), 0)

    def test_the_unit_is_left_alone_after_the_player_chose_a_preset_of_their_own_on_it(self):
        self.studio.idle_seconds = 30.
        self.studio.put("b", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.settle()
        self.host.call("read", FACTORY).result(10)        # as if chosen by hand on the unit
        sent = self.sent()
        self.studio.idle_seconds = 0.
        self.settle()
        self.poll_for(.3)
        self.assertNotIn("hear", [name for name, _ in sent])
        self.assertIn("leaves it alone", self.studio.state.status)
        self.assertIsNone(self.studio.state.mode)        # the guitar does not come back by itself
        self.studio.select("b")                           # a click on a tile comes back to the desk's sounds
        self.until(lambda: self.studio.sounding == ("b", "guitar"))
        self.until(lambda: "probe" in (self.studio.sound("b").heard or {}), 20)


class UnheardTest(FlowCase):
    automatic = True

    def test_a_design_that_cannot_be_heard_is_noted_and_the_others_are_heard(self):
        calls, failing = [], []
        original = engine_module.Engine.hear

        def hear(engine, chain, dry, scene=0, *, stop=None, interrupt=False):
            calls.append(chain.digest())
            if not failing:
                failing.append(chain.digest())
            if chain.digest() == failing[0]:
                raise RuntimeError("Nothing came back from the Quad Cortex for the phrase.")
            return original(engine, chain, dry, scene, stop=stop)

        self.patch_engine("hear", hear)
        self.script.answers += [three("One", "Two", "Three"), three("Four", "Five", "Six")]
        self.studio.ask("make a classic rock crunch")
        self.settle(30)
        for place in (1, 2, 0):
            self.studio.step(place)
            self.settle()
        found = self.studio.round()
        sounds = [self.studio.store.sound(item["sound"]) for item in found["candidates"]]
        self.assertIn("Nothing came back", sounds[0].heard["failed"])
        self.assertTrue(all("probe" in sound.heard for sound in sounds[1:]))
        self.assertEqual(calls.count(failing[0]), 1)                         # not tried again at every step
        self.assertIn("Nothing came back", self.studio.unheard(sounds[0]))
        self.assertIsNone(self.studio.evidence(sounds[0]))
        self.assertTrue(found.get("next"))                                  # three more were prepared all the same
        self.studio.copy_to_a()
        self.settle()
        self.assertEqual(len(self.studio.difference()), 2)                  # A, the copy, and B: neither could be
        self.assertIn("A could not be listened to: Nothing came back", self.studio.difference()[0])

    def test_a_listen_the_unit_turned_away_says_nothing_about_the_sound(self):
        from lab.qc.slots import Unsaved

        def hear(engine, chain, dry, scene=0, *, stop=None, interrupt=False):
            raise Unsaved("The preset on the Quad Cortex has unsaved changes.")

        self.patch_engine("hear", hear)
        self.studio.put("b", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.settle()
        self.assertIsNone(self.studio.sound("b").heard)
        self.assertIn("unsaved changes", self.studio.state.status)
        self.assertEqual(self.log(), "")                                    # an outcome of ordinary use


class WorkingLineTest(FlowCase):
    def gated(self):
        gate, script = threading.Event(), self.script

        def run(prompt, schema, *, web, seconds, cancel):
            if not script.answers:
                gate.wait(20)
                return REVISION
            return script(prompt, schema, web=web, seconds=seconds, cancel=cancel)

        self.studio.assistant.run = run
        self.addCleanup(gate.set)
        return gate

    def test_the_working_line_and_cancel_stay_while_a_request_is_with_the_assistant(self):
        self.gated()
        self.a_round()
        self.studio.ask("more sustain from the amp, without more mud")
        self.assertIn("Revising", self.studio.state.working)
        self.studio.step(1)                               # other work on the unit begins and ends meanwhile
        self.until(lambda: not self.host.busy and len(self.studio.jobs) == 1)
        self.assertIn("Revising", self.studio.state.working)
        self.assertTrue(self.studio.cancellable())
        self.assertTrue(self.studio.cancel())
        self.assertEqual(self.studio.state.working, "")

    def test_guitar_plays_while_a_request_is_with_the_assistant_and_a_and_b_are_not_ready(self):
        self.gated()
        self.a_round()
        self.studio._set(shown=None)                      # as after another preset was chosen on the unit
        self.studio.ask("more sustain from the amp, without more mud")
        self.studio.play("guitar")
        self.until(lambda: self.studio.state.shown == "pair" and self.studio.sounding == ("b", "guitar"))
        self.assertEqual(self.studio.state.mode, "guitar")

    def test_a_callers_own_working_line_is_one_activity_among_the_others(self):
        self.studio.set(working="Rendering…")             # as the side tools do
        self.studio.begin("x", "Something of the desk's…")
        self.studio.end("x")
        self.assertEqual(self.studio.state.working, "Rendering…")
        self.studio.set(working="")
        self.assertEqual(self.studio.state.working, "")


class PlayTest(FlowCase):
    def test_a_play_that_fails_does_not_leave_the_desk_saying_it_plays(self):
        self.a_round()

        def refuse(engine, side, mode, phrase):
            raise RuntimeError("The audio driver would not start.")

        self.patch_engine("_play", refuse)
        sent = self.sent()
        self.studio.play("phrase")
        self.settle()
        self.assertIsNone(self.studio.state.mode)
        self.assertIn("would not start", self.studio.state.status)
        del sent[:]
        self.poll_for(2)
        self.assertNotIn("watch", [name for name, _ in sent])               # nothing plays: nothing to watch
        self.assertIn("_serve", self.log())                                 # the trace from where the unit is held

    def test_stop_says_no_fault_and_writes_none(self):
        self.a_round()
        self.studio.play("guitar")
        said = self.studio.state.status
        self.studio.pause()                               # with the play and its levels still waiting
        self.settle()
        self.assertIsNone(self.studio.state.mode)
        self.assertEqual(self.studio.state.status_kind, "")         # the buttons show it stopped: nothing is said
        self.assertIn(self.studio.state.status, (said, ""))
        self.assertIsNone(self.studio.state.mode)
        self.assertEqual(self.log(), "")

    def test_levels_that_have_not_changed_are_not_sent_again(self):
        self.a_round()
        self.studio.copy_to_a()
        self.settle()
        self.studio.play("guitar")
        self.settle()
        sent = self.sent()
        self.studio.select("a")
        self.studio.select("b")
        self.settle()
        self.assertEqual([name for name, _ in sent if name != "watch"], ["play", "play"])

    def test_guitar_pressed_during_a_silent_listen_does_not_wait_for_it(self):
        original = engine_module.Engine.hear
        listening = threading.Event()

        def slow(engine, chain, dry, scene=0, *, stop=None, interrupt=False):
            listening.set()
            for _ in range(160):                          # eight seconds, unless stopped
                if stop is not None and stop.is_set():
                    raise Stopped("Stopped.")
                time.sleep(.05)
            return original(engine, chain, dry, scene, stop=stop)

        self.studio.idle_seconds = 30.
        self.studio.put("b", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.settle()
        self.patch_engine("hear", slow)
        self.studio.idle_seconds = 0.
        self.until(listening.is_set)
        started = time.monotonic()
        self.studio.play("guitar")
        self.until(lambda: (self.studio.sounding or ("", ""))[1] == "guitar", 6)
        self.assertLess(time.monotonic() - started, 3.)
        self.assertEqual(self.log(), "")                                    # stopping it is no fault
        self.assertIsNone(self.studio.sound("b").heard)                     # and says nothing about the sound
        setattr(engine_module.Engine, "hear", original)
        self.until(lambda: "probe" in (self.studio.sound("b").heard or {}), 20)    # listened to once it is quiet


    def test_guitar_pressed_during_a_listen_plays_although_other_work_waited_behind_the_listen(self):
        original = engine_module.Engine.hear
        listening = threading.Event()

        def slow(engine, chain, dry, scene=0, *, stop=None, interrupt=False):
            listening.set()
            for _ in range(160):
                if stop is not None and stop.is_set():
                    raise Stopped("Stopped.")
                time.sleep(.05)
            return original(engine, chain, dry, scene, stop=stop)

        self.studio.idle_seconds = 30.
        self.a_round()
        self.patch_engine("hear", slow)
        self.studio.idle_seconds = 0.
        self.until(listening.is_set)
        self.studio.step(1)                               # A and B are to be made ready again, behind the listen
        self.studio.play("guitar")
        self.until(lambda: (self.studio.sounding or ("", ""))[1] == "guitar", 8)
        self.assertEqual((self.studio.state.mode, self.studio.state.shown), ("guitar", "pair"))
        self.assertEqual(self.studio.sound(self.studio.sounding[0]).name, "Two")
        self.assertNotIn("Getting ready", self.studio.state.status)    # said while it was, and not after


class ShapeTest(FlowCase):
    def test_a_remark_undone_and_said_again_tries_the_first_remedy_again(self):
        self.a_round()
        self.studio.ask("muddy")
        first = dict(self.studio.sound("b").knobs)
        self.studio.undo()
        self.assertEqual(self.studio.sound("b").knobs, {})
        self.studio.ask("muddy")
        self.assertEqual(self.studio.sound("b").knobs, first)
        self.studio.ask("muddy")                          # said again with the remedy still on: the next one
        self.assertNotEqual(self.studio.sound("b").knobs, first)

    def test_a_remark_said_again_after_the_knobs_were_turned_by_hand_starts_over(self):
        self.a_round()
        self.studio.ask("muddy")
        first = dict(self.studio.sound("b").knobs)
        key = next(iter(first))
        self.studio.turn({**first, key: 0.})
        self.studio.ask("muddy")
        self.assertEqual(self.studio.sound("b").knobs, first)

    def test_hearing_without_the_knobs_follows_a_turn(self):
        self.a_round()
        key = self.studio.available()[0]
        self.studio.turn({key: .5})
        self.settle()
        self.studio.settle_seconds = 999.
        sent = self.sent()
        self.studio.base()
        self.assertIn("the base", self.studio.state.status)
        self.assertEqual(sent[-1], ("turn", ("b", {})))
        self.studio.turn({key: .7})                       # the unit has the knobs on again
        self.studio.base()
        self.assertIn("the base", self.studio.state.status)                 # so this press takes them off again
        self.assertEqual(sent[-1], ("turn", ("b", {})))
        self.studio.base()
        self.assertIn("shaped", self.studio.state.status)
        self.assertEqual(sent[-1], ("turn", ("b", {key: .7})))


    def test_difference_says_which_side_is_not_measured_and_what_will_bring_it(self):
        self.studio.idle_seconds = 30.
        self.a_round()
        self.studio.copy_to_a()
        self.settle()
        lines = self.studio.difference()
        self.assertEqual(len(lines), 2)
        self.assertIn("A has not been listened to as it is set now", lines[0])
        self.studio.idle_seconds = 0.
        self.settle()
        self.studio.settle_seconds = 0.
        self.studio.ask("brighter")                       # on the simulated unit this measures as too little
        self.assertIn("B's Shape knobs have not been measured where they stand", self.studio.difference()[0])
        self.settle(30)
        self.assertIn("corrected", " ".join(self.studio.difference()))
        self.assertIn("turn one", self.studio.difference()[0])


class RoundsTest(FlowCase):
    def test_the_song_does_not_stick_to_a_later_round_that_is_not_for_a_song(self):
        song = three("One", "Two", "Three")
        song["song"] = {"artist": "Pink Floyd", "title": "Time", "part": "solo"}
        self.script.answers.append(song)
        self.studio.ask("make Pink Floyd - Time solo")
        self.settle()
        self.assertEqual(self.studio.state.song, "Pink Floyd - Time")
        self.assertEqual(self.studio.sound("b").origin["song"], "Pink Floyd - Time")
        self.a_round("Four", "Five", "Six", request="make a clean funk sound")
        self.assertIsNone(self.studio.state.song)
        self.assertNotIn("song", self.studio.sound("b").origin)

    def test_three_more_of_a_song_round_are_for_that_song(self):
        song = three("One", "Two", "Three")
        song["song"] = {"artist": "Pink Floyd", "title": "Time", "part": "solo"}
        self.script.answers += [song, three("Four", "Five", "Six")]
        self.studio.ask("make Pink Floyd - Time solo")
        self.settle()
        self.studio.more()
        self.settle()
        self.assertEqual(self.studio.sound("b").name, "Four")
        self.assertEqual(self.studio.sound("b").origin["song"], "Pink Floyd - Time")
        self.assertEqual(self.studio.state.song, "Pink Floyd - Time")

    def test_undo_after_a_new_round_brings_the_earlier_round_back_with_its_design(self):
        self.a_round()
        first, first_b = self.studio.state.round, self.studio.state.b
        self.a_round("Four", "Five", "Six", request="make a plexi lead")
        self.assertNotEqual(self.studio.state.round, first)
        self.studio.undo()
        self.settle()
        self.assertEqual((self.studio.state.round, self.studio.state.b), (first, first_b))
        self.assertIn(self.studio.state.b, [item["sound"] for item in self.studio.round()["candidates"]])

    def test_a_round_is_written_whole_and_found_by_its_sounds(self):
        rounds = Rounds(self.folder)
        rounds.save({"id": "r1", "request": "x", "aim": {}, "candidates": [{"sound": "s1", "differs": ""}]})
        rounds.save({"id": "r2", "request": "y", "aim": {}, "previous": "r1", "candidates": []})
        rounds.follow("r1", "r2")
        self.assertEqual(rounds.get("r1")["next"], "r2")
        self.assertEqual(rounds.of("s1")["id"], "r1")
        self.assertIsNone(rounds.of("nobody's"))
        self.assertEqual(sorted(path.name for path in (self.folder / "rounds").iterdir()), ["r1.json", "r2.json"])
        (self.folder / "rounds" / "r3.json").write_text("{ cut sh", encoding="utf-8")
        self.assertIsNone(rounds.get("r3"))


class BackgroundTest(FlowCase):
    automatic = True

    def scripted(self):
        """The first call answers at once; later ones wait, as Codex does, and end early only when cancelled."""
        calls, gate = [], threading.Event()

        def run(prompt, schema, *, web, seconds, cancel):
            calls.append(cancel)
            if len(calls) == 1:
                return three("One", "Two", "Three")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not gate.is_set():
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("withdrawn")
                time.sleep(.002)
            return three(f"Later {len(calls)}a", f"Later {len(calls)}b", f"Later {len(calls)}c")

        self.studio.assistant.run = run
        self.addCleanup(gate.set)
        return calls, gate

    def test_the_apps_own_request_ends_when_the_app_closes(self):
        calls, _ = self.scripted()
        self.studio.ask("make a classic rock crunch")
        self.until(lambda: len(calls) == 2, 30)
        self.assertIsNotNone(calls[1])                    # a cancel signal of its own
        self.assertFalse(self.studio.cancellable())       # which Cancel, the player's, does not touch
        started = time.monotonic()
        self.studio.close()
        self.studio.thinking.shutdown(wait=True)          # what the interpreter does before it can exit
        self.assertLess(time.monotonic() - started, 5.)
        self.assertTrue(calls[1].is_set())

    def test_a_new_request_does_not_start_the_preparation_a_second_time(self):
        calls, gate = self.scripted()
        self.studio.ask("make a classic rock crunch")
        self.until(lambda: len(calls) == 2, 30)
        first = self.studio.state.round
        self.studio.ask("make a plexi lead")              # the player's own, while three more are being prepared
        self.assertEqual(self.studio.preparing, first)
        self.studio.step(1)
        self.until(lambda: not self.host.busy)
        self.poll_for(.5)
        self.studio.assistant.automatic = False           # nothing further is to be prepared in this test
        gate.set()
        self.settle(30)
        self.assertEqual(len(calls), 3)
        rounds = [json.loads(path.read_text(encoding="utf-8")) for path in (self.folder / "rounds").glob("*.json")]
        self.assertEqual(sum(1 for item in rounds if item.get("previous") == first), 1)


class FaultLogTest(FlowCase):
    def test_a_cancelled_request_writes_no_fault(self):
        def run(prompt, schema, *, web, seconds, cancel):
            from lab.sound.assistant import Cancelled

            cancel.wait(10)
            raise Cancelled("The request was withdrawn.")

        self.studio.assistant.run = run
        self.studio.ask("make a classic rock crunch")
        self.studio.cancel()
        self.until(lambda: not self.studio.jobs)
        self.assertEqual(self.studio.state.status, "Request cancelled.")
        self.assertEqual(self.log(), "")

    def test_a_unit_that_will_not_open_yet_is_said_and_writes_no_fault(self):
        from lab.qc.host import Fault

        refused = Future()
        refused.set_exception(Fault("The preset on the Quad Cortex has unsaved changes.", "Unsaved", "a trace"))
        self.studio.later(refused, lambda result: None)
        self.studio.poll()
        self.assertIn("unsaved changes", self.studio.state.status)
        self.assertEqual(self.log(), "")

    def test_a_fault_where_the_unit_is_held_is_written_with_its_trace_from_there(self):
        from lab.qc.host import Fault

        broke = Future()
        broke.set_exception(Fault("It broke.", "KeyError", "Traceback\n  File engine.py, in show\nKeyError: 3"))
        self.studio.later(broke, lambda result: None)
        self.studio.poll()
        self.assertEqual(self.studio.state.status, "It broke.")
        self.assertIn("File engine.py, in show", self.log())

    def test_the_firmware_remark_is_made_for_the_firmware_the_unit_says_when_it_opens(self):
        from lab.qc import sim

        self.assertEqual(self.studio.firmware, sim.SimUnit.firmware)
        self.assertNotIn("another firmware", self.studio.state.status)       # the simulated unit's is the tables'
        self.addCleanup(setattr, sim.SimUnit, "firmware", sim.SimUnit.firmware)
        sim.SimUnit.firmware = "9.9.9"                    # as after a firmware update
        host = Host(self.folder / "updated", simulated=True, local=True)
        host.ready.result(10)
        again = Studio(host, self.folder, assistant=self.studio.assistant)
        self.addCleanup(again.close)
        again.open()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not again.opened:
            again.poll()
            time.sleep(.002)
        self.assertIn("another firmware than the unit's 9.9.9", again.state.status)


class SessionTest(FlowCase):
    def again(self):
        studio = Studio(self.host, self.folder, assistant=self.studio.assistant)
        return studio, studio.restore()

    def test_music_reference_and_listening_offset_persist_across_songs_and_restarts(self):
        reference = {"lufs": -16., "endpoint": "Quad Cortex"}
        self.studio.set_media_reference(reference)
        self.studio.set_listening({"loudness_db": 5, "muted": True})
        self.studio.set(song="Another song", instrument="Les Paul")
        self.studio.remember()
        studio, _ = self.again()
        self.assertEqual(studio.media_reference, reference)
        self.assertEqual(studio.listening["loudness_db"], 5)
        self.assertFalse(studio.listening["muted"])

    def test_a_sound_whose_file_cannot_be_read_is_not_put_back_and_the_rest_is(self):
        self.a_round()
        self.studio.copy_to_a()
        self.settle()
        a, b = self.studio.state.a, self.studio.state.b
        path = self.folder / "sounds" / f"{b}.json"
        path.write_text(path.read_text(encoding="utf-8")[:200], encoding="utf-8")     # cut short
        studio, record = self.again()
        self.assertIsNotNone(record)
        self.assertEqual((studio.state.a, studio.state.b, studio.state.round), (a, None, None))

    def test_a_session_that_is_not_a_record_is_set_aside_and_said(self):
        (self.folder / "session.json").write_text("[1, 2]", encoding="utf-8")
        studio, record = self.again()
        self.assertIsNone(record)
        self.assertEqual((studio.state.a, studio.state.b), (None, None))
        self.assertFalse((self.folder / "session.json").exists())
        self.assertEqual((self.folder / studio_module.SET_ASIDE).read_text(encoding="utf-8"), "[1, 2]")
        self.assertIn("could not be put back", studio.state.status)
        self.assertIn("session is not a record", self.log())

    def test_a_listener_that_fails_does_not_cut_the_action_short(self):
        def broken(state):
            raise KeyError("drawing")

        self.studio.listeners.append(broken)
        self.studio.put("b", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.settle()
        self.assertEqual(self.studio.state.shown, "pair")                   # the action went on to its end
        self.assertEqual(self.log().count("--- 20"), 1)                     # and the fault was logged, once
        self.assertIn("KeyError", self.log())

    def test_a_hand_edited_list_that_cannot_be_read_is_said_and_kept_aside(self):
        path = self.folder / "instruments.json"
        self.studio.store.instruments()                   # the list the app starts with is written
        path.write_text(path.read_text(encoding="utf-8").rstrip()[:-1] + ",]", encoding="utf-8")   # a slip
        self.assertTrue(self.studio.store.instruments())  # the app's own list is used meanwhile
        self.studio.poll()
        self.assertIn("instruments.json could not be read", self.studio.state.status)
        self.assertIn(",]", (self.folder / "instruments.json.bad").read_text(encoding="utf-8"))


class GuitarProfileTest(FlowCase):
    def take(self, name, setting, level=.05):
        folder = self.folder / "phrases" / name
        folder.mkdir(parents=True)
        tone = (np.sin(np.arange(48000) / 48000 * 2 * np.pi * 196) * level)[:, None].astype(np.float32)
        write_wave(folder / "dry.wav", tone)
        (folder / "phrase.json").write_text(json.dumps({
            "id": name, "instrument": "Charvel", "setting": setting, "recorded": f"2026-11-21T10:00:0{len(name)}+00:00",
            "seconds": 1}), encoding="utf-8")

    def watched(self):
        threads, original = [], self.studio.guitar.profile

        def profile(instrument, takes):
            threads.append(threading.current_thread().name)
            return original(instrument, takes)

        self.studio.guitar.profile = profile
        return threads

    def test_the_designer_gets_the_takes_recorded_this_session_worked_out_off_the_windows_thread(self):
        threads = self.watched()
        self.a_round()
        self.assertNotIn("guitar_profile", self.script.asked[-1][0])        # no take with its setting noted yet
        self.take("one", "Bridge · Series")
        self.a_round("Four", "Five", "Six", request="make a plexi lead")
        self.assertIn('"guitar_profile"', self.script.asked[-1][0])
        self.assertIn("Bridge", self.script.asked[-1][0])
        self.take("three", "Neck · Series")
        self.a_round("Seven", "Eight", "Nine", request="make a fuzz lead")
        self.assertIn("Neck", self.script.asked[-1][0])
        self.assertTrue(threads)
        self.assertNotIn(threading.current_thread().name, threads)

    def test_the_guitar_panels_lines_arrive_without_holding_the_window(self):
        threads = self.watched()
        self.take("one", "Bridge · Series")
        self.take("three", "Neck · Series", level=.02)
        version = self.studio.profile_version
        self.assertIn("Working out", self.studio.guitar_lines()[0])     # said at once; the takes are read elsewhere
        self.settle()
        self.assertGreater(self.studio.profile_version, version)
        self.assertTrue(threads)
        self.assertNotIn(threading.current_thread().name, threads)
        lines = self.studio.guitar_lines()
        self.assertNotIn("Working out", lines[0])
        self.assertEqual(lines[:len(self.studio.guitar_profile()[1])], self.studio.guitar_profile()[1])


class AudioHoldTest(FlowCase):
    def test_holding_the_input_stops_what_plays_and_says_when_the_unit_is_quiet(self):
        self.a_round()
        self.studio.play("guitar")
        self.settle()
        quiet = self.studio.hold_audio("learner")
        self.assertIsInstance(quiet, Future)
        self.assertEqual((self.studio.audio_held_by, self.studio.state.mode), ("learner", None))
        self.assertIs(self.studio.hold_audio("learner"), quiet)             # asked again: the same hold
        with self.assertRaisesRegex(RuntimeError, "Guitar Learner is using the input"):
            self.studio.hold_audio("gestures")
        quiet.result(10)                                                    # the unit is quiet: the driver is free
        self.studio.release_audio("gestures")                               # not theirs to give back
        self.assertEqual(self.studio.audio_held_by, "learner")
        self.studio.release_audio("learner")
        self.assertIsNone(self.studio.audio_held_by)

    def test_nothing_of_the_desk_opens_the_input_while_it_is_held(self):
        self.studio.idle_seconds = 30.
        self.a_round()
        self.studio.hold_audio("learner").result(10)
        self.settle()
        sent = self.sent()
        self.studio.play("guitar")
        self.assertEqual(self.studio.state.status, "The Guitar Learner is using the input.")
        self.assertIsNone(self.studio.state.mode)
        self.studio.play("phrase")
        self.studio.record("Neck", 1.)
        self.studio.measure_round_trip()
        self.studio.settle_seconds = self.studio.idle_seconds = 0.
        self.studio.turn({self.studio.available()[0]: .5})                  # a turn to check, and designs to hear
        self.settle()
        self.poll_for(.5)
        self.assertEqual([name for name, _ in sent if name != "turn"], [])
        self.studio.release_audio("learner")
        self.settle(30)
        self.assertIn("hear_shaped", [name for name, _ in sent])            # and when it is free again, it does


    def test_beside_gesture_listening_the_guitar_goes_on_and_the_phrase_does_not_start(self):
        self.a_round()
        self.studio.play("guitar")
        self.settle()
        self.studio.hold_audio("gestures").result(10)
        self.settle()
        self.assertEqual(self.studio.state.mode, "guitar")                  # it came back once the unit was quiet
        self.studio.stop()
        self.settle()
        self.studio.play("guitar")                                          # and can be started while the hold lasts
        self.settle()
        self.assertEqual(self.studio.state.mode, "guitar")
        self.studio.play("phrase")
        self.assertEqual(self.studio.state.status, "Gesture listening is using the input.")
        self.assertEqual(self.studio.state.mode, "guitar")


class LinesTest(FlowCase):
    def test_no_line_names_a_key_that_does_nothing(self):
        from unittest.mock import patch

        self.a_round()
        with patch("lab.knowledge.named_settings", return_value={"Hot": {"GAIN": 9.0}}):
            self.studio.named(0, 0, "Hot")
        self.assertIn("Undo takes it back", self.studio.state.status)
        self.settle()
        self.studio.copy_to_a()
        self.settle()
        self.studio.blind()
        self.assertIn("see which was which", self.studio.state.status)
        source = Path(studio_module.__file__).read_text(encoding="utf-8")
        for words in ("press N", "Z undoes", "press X", "the 0 key"):
            self.assertNotIn(words, source)


# ---- Faults the reviews of 2026-11-20 found (docs/CODEBASE_REVIEW.md), each reproduced as it was found ----

def held_back(gate, *, song=False):
    """An assistant whose answer to "the first request" waits for `gate`; with "fails" in the request it then
    fails. Its designs are called Late, the others' Soon."""
    def answering(prompt, schema, **options):
        first = "first request" in prompt
        if first:
            gate.wait(10)
            if "fails" in prompt:
                raise RuntimeError("the assistant failed")
        found = reply(*[answer(f"{'Late' if first else 'Soon'} {n}", [block(PLEXI, 0, gain=n * 3),
                                                                     block(GREENBACK, 1)], differs="d")
                        for n in (1, 2, 3)])
        if first and song:
            found["song"] = {"artist": "Old Band", "title": "Old Song", "part": "solo"}
        return found

    return answering


class OlderRequestTest(StudioCase):
    """Two requests on their way at once: the later one counts. (reviews of 2026-11-20)"""

    gate = threading.Event()        # holds the answer to the first request back until the test lets it go
    answers = staticmethod(held_back(gate, song=True))

    def setUp(self):
        self.gate.clear()
        self.addCleanup(self.gate.set)
        super().setUp()

    def later_answer(self):
        """Wait for the later request's answer, which comes while the earlier one's is still held back."""
        self.until(lambda: self.studio.sound("b") is not None, 30, "the later answer")
        self.assertTrue(self.studio.sound("b").name.startswith("Soon"), self.studio.sound("b").name)

    def test_cancel_drops_every_request_in_flight(self):
        first, second = self.studio._request(), self.studio._request()
        self.assertTrue(self.studio.cancel())
        self.assertTrue(first.is_set(), "the first request's signal")
        self.assertTrue(second.is_set(), "the second request's signal")

    def test_cancel_shows_at_once_and_an_older_answer_does_not_take_b(self):
        cancel_shown, said = [], []
        self.studio.listeners.append(lambda state: cancel_shown.append(bool(state.working)
                                                                       and self.studio.cancellable()))
        self.studio.listeners.append(lambda state: said.append(state.status))
        self.studio.ask("make the first request")
        self.assertEqual(cancel_shown[-1:], [True])
        self.studio.ask("make the second request")
        self.later_answer()
        self.gate.set()
        self.settle(30)
        self.assertTrue(self.studio.sound("b").name.startswith("Soon"), self.studio.sound("b").name)
        self.assertIn("earlier request", " | ".join(said))

    def test_an_older_answer_does_not_change_the_song(self):
        self.studio.ask("make the first request")
        self.studio.ask("make the second request")
        self.later_answer()
        song = self.studio.state.song
        self.assertNotIn("Old Song", song or "")
        self.gate.set()
        self.settle(30)
        self.assertEqual(self.studio.state.song, song)
        self.assertTrue(self.studio.sound("b").name.startswith("Soon"), self.studio.sound("b").name)

    def test_an_older_failure_keeps_cancel_while_the_latest_is_on_its_way(self):
        latest = threading.Event()
        self.addCleanup(latest.set)
        script = self.studio.assistant.run

        def second_waits(prompt, schema, **options):
            if "second request" in prompt:
                latest.wait(10)
            return script(prompt, schema, **options)

        self.studio.assistant.run = second_waits
        self.studio.ask("make the first request, which fails")
        self.studio.ask("make the second request")
        self.gate.set()
        self.until(lambda: "earlier request failed" in (self.studio.state.status or ""), 10,
                   "word of the earlier failure")
        self.assertNotEqual(self.studio.state.working, "")     # the latest request is still said to be on its way
        self.assertTrue(self.studio.cancellable())
        latest.set()
        self.settle(30)


if __name__ == "__main__":
    unittest.main()
