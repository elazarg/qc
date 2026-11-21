"""Gestures: the detector, the one cadence from audio to a gesture, the arm, and the session on the desk's side."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
from contextlib import contextmanager
from concurrent.futures import Future
from queue import Queue
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from lab.hearing import audio
from lab.sound import knobs
from lab.learner import folders, gestures, state, worker
from lab.learner.gestures import GestureDetector, GestureEvent, GestureMapping, replay

from test_lab_learner_seat import seat_for, settle


def observations(labels, *, first=0.0, hop=0.12):
    return [{"time": first + step * hop, "label": label,
             "closeness": 0 if label is None else .95} for step, label in enumerate(labels)]


class GuitarGesturesTest(unittest.TestCase):
    def setUp(self):
        self.mapping = GestureMapping("neck", "bridge", "space", -.15)
        self.pattern = ["neck"] * 3 + ["bridge"] * 3 + ["neck"] * 3

    def test_opt_in_and_completed_return(self):
        detector = GestureDetector(self.mapping)
        for row in observations(self.pattern):
            self.assertIsNone(detector.observe(row["time"], row["label"], row["closeness"]))
        detector.arm(0)
        events = [detector.observe(row["time"], row["label"], row["closeness"])
                  for row in observations(self.pattern)]
        self.assertEqual(events[:-1], [None] * 8)
        self.assertIsNotNone(events[-1])
        self.assertAlmostEqual(events[-1].at, .96)
        knob_values = {"space": .3, "drive": -.2}
        self.assertEqual(events[-1].apply(knob_values), {"space": .15, "drive": -.2})
        self.assertEqual(knob_values["space"], .3)

    def test_ignores_ordinary_switches_uncertainty_and_long_gaps(self):
        for labels in (
            ["bridge"] * 3 + ["neck"] * 3,
            ["neck"] * 3 + ["bridge"] * 16 + ["neck"] * 3,
            ["neck"] * 3 + ["bridge"] * 2 + [None, "bridge"] + ["neck"] * 3,
            ["neck"] * 3 + ["bridge"] * 3 + [None] * 7 + ["neck"] * 3,
            ["neck"] * 3 + ["bridge"] * 3 + ["middle"] + ["neck"] * 3,
        ):
            with self.subTest(labels=labels):
                self.assertEqual(replay(self.mapping, observations(labels)), [])

    def test_the_time_a_control_takes_to_move_bridges_two_stable_settings(self):
        # While the control moves the window holds both settings: up to a window and two hops with nothing close.
        gap = int((state.WINDOW_SECONDS + 2 * state.HOP_SECONDS) / .12) - 1
        pattern = ["neck"] * 3 + [None] * gap + ["bridge"] * 3 + [None] * gap + ["neck"] * 3
        self.assertEqual(len(replay(self.mapping, observations(pattern))), 1)

    def test_cooldown_and_arm_expiry(self):
        twice = self.pattern + ["bridge"] * 3 + ["neck"] * 3
        self.assertEqual(len(replay(self.mapping, observations(twice))), 1)
        detector = GestureDetector(self.mapping, arm_seconds=.85)
        detector.arm(0)
        for row in observations(self.pattern):
            self.assertIsNone(detector.observe(row["time"], row["label"], row["closeness"]))
        self.assertFalse(detector.is_armed(1))

    def test_a_gesture_arms_the_detector_for_as_long_again(self):
        detector = GestureDetector(self.mapping, arm_seconds=3.)
        detector.arm(0)
        fired = [detector.observe(row["time"], row["label"], row["closeness"]) for row in observations(self.pattern)]
        self.assertIsNotNone(fired[-1])
        self.assertTrue(detector.is_armed(3.5))             # 3 s from the gesture at .96 s, not from the start
        self.assertAlmostEqual(detector.seconds_left(1.96), 2.)
        again = [detector.observe(row["time"], row["label"], row["closeness"])
                 for row in observations(self.pattern, first=2.1)]
        self.assertIsNotNone(again[-1])
        self.assertFalse(detector.is_armed(6.5))
        self.assertEqual(detector.seconds_left(6.5), 0.)

    def test_out_of_order_observation_cancels_pending_return(self):
        track = observations(self.pattern)
        track.insert(6, {"time": track[4]["time"], "label": "bridge", "closeness": .99})
        self.assertEqual(replay(self.mapping, track), [])

    def test_mapping_serializes_and_clamps_to_control_range(self):
        mapping = GestureMapping.from_dict(GestureMapping("neck", "bridge", "jazz", -.2).to_dict())
        event, = replay(mapping, observations(self.pattern))
        self.assertEqual(event.apply({"jazz": .1}), {"jazz": 0})
        with self.assertRaises(ValueError):
            GestureMapping("neck", "bridge", "not-a-control")
        with self.assertRaises(ValueError):
            GestureMapping("neck", "bridge", "space", 1)


class KnobTableTest(unittest.TestCase):
    def test_every_desk_knob_can_be_mapped_and_clamps_to_its_own_floor(self):
        for key, (_, low, _) in knobs.KNOBS.items():
            event = GestureEvent("neck", "bridge", key, -.25, 1.)
            self.assertEqual(event.apply({key: low})[key], low)
            self.assertEqual(GestureEvent("neck", "bridge", key, .25, 1.).apply({key: .9})[key], 1)


# ---- from audio to a gesture ----

def note(frequency, kind, *, seconds, level=.08):
    """A plucked note with the harmonics of one of two made-up pickup settings."""
    t = np.arange(round(seconds * audio.RATE)) / audio.RATE
    partials = np.arange(1, 13)
    gains = partials ** (-.6)
    if kind == "neck":
        gains = gains * np.exp(-.26 * partials)
        gains[3:5] *= .18
    else:
        gains[1:3] *= .28
    phases = partials[:, None] * 2 * np.pi * frequency * t
    return (np.sum(gains[:, None] * np.sin(phases), axis=0) * level * np.exp(-.8 * t)).astype(np.float32)


def played(lead, excursion, note_seconds=.5):
    """Picked notes: `lead` seconds at neck, `excursion` seconds at bridge, two seconds at neck again."""
    parts = []
    for kind, total in (("neck", lead), ("bridge", excursion), ("neck", 2.)):
        count = max(1, round(total / note_seconds))
        parts += [note(110, kind, seconds=total / count) for _ in range(count)]
    return np.concatenate(parts)


class AudioToGestureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        examples = [{"label": label, "wav": np.concatenate([note(110, label, seconds=.65), np.zeros(4000),
                                                            note(146.83, label, seconds=.65)])}
                    for label in ("neck", "bridge")]
        cls.profile = state.fit("Made up", "pickup", examples)
        cls.mapping = GestureMapping("neck", "bridge", "space", .1)

    def live(self, samples):
        """What listening live hears in `samples`: the audio arrives in blocks, an estimate is made from the
        observer's window every hop, and each goes through the observer's own detector."""
        with patch.object(gestures.profiles, "load", return_value=self.profile):
            observer = gestures.Observer({"profile": "profile.json", "gesture": self.mapping.to_dict(), "arm": True})
        observer.detector.arm(0.)
        hop = round(state.HOP_SECONDS * audio.RATE)
        fired, generation = [], 0
        for end in range(hop, len(samples) + 1, hop):
            now = end / audio.RATE
            with patch.object(gestures.time, "monotonic", return_value=now):
                observer.callback(samples[end - hop:end, None], hop, None, SimpleNamespace(input_overflow=False))
            generation = observer.read(generation)
            _, gesture = observer.poll(now)
            if gesture is not None:
                fired.append(gesture)
        return fired

    def test_a_clean_return_gesture_is_recognised_live_wherever_the_switch_falls(self):
        # Before: live estimates were .48 s windows .14 s apart and the detector gave up after .40 s without a
        # close reading, which the move itself takes; 20 of 32 clean gestures were missed.
        for excursion in (.7, 1.0):
            for offset in np.arange(0, state.HOP_SECONDS, .02):
                with self.subTest(excursion=excursion, offset=round(float(offset), 2)):
                    fired = self.live(played(1.5 + offset, excursion))
                    self.assertEqual(len(fired), 1)
                    self.assertEqual((fired[0].home, fired[0].excursion, fired[0].control), ("neck", "bridge", "space"))

    def test_the_rehearsal_on_a_recorded_take_says_what_listening_live_would(self):
        for samples in (played(1.5, .8), played(1.56, 1.0), played(1.5, 2.5),
                        np.concatenate([note(110, "neck", seconds=2), note(110, "bridge", seconds=2)])):
            rehearsed = replay(self.mapping, state.track(self.profile, samples))
            self.assertEqual(len(rehearsed), len(self.live(samples)))
        self.assertEqual(len(replay(self.mapping, state.track(self.profile, played(1.5, .8)))), 1)
        self.assertEqual(replay(self.mapping, state.track(self.profile, played(1.5, 2.5))), [])       # away too long
        detector = GestureDetector(self.mapping)
        self.assertGreaterEqual(detector.max_gap, state.WINDOW_SECONDS + state.HOP_SECONDS)

    def test_an_analysis_that_fails_ends_the_listening_with_its_reason(self):
        with patch.object(gestures.profiles, "load", return_value=self.profile):
            observer = gestures.Observer({"profile": "profile.json"})
        observer.window.append(np.zeros(round(state.WINDOW_SECONDS * audio.RATE)), 1.)
        with patch.object(gestures.guitar_state, "estimate", side_effect=RuntimeError("bad frame")):
            observer.stop.wait = Mock(side_effect=[False, True])
            observer.analyze()
        self.assertEqual(observer.failed, "bad frame")
        with self.assertRaisesRegex(RuntimeError, "could not be analysed: bad frame"):
            observer.poll(1.)


# ---- in the worker process ----

class WorkerSideTest(unittest.TestCase):
    def run_with(self, observer, config, commands=None, until=None):
        stop, reports = Event(), []

        def report(event):
            reports.append(event)
            if until is not None and until(reports):
                stop.set()

        @contextmanager
        def listen(settings, channel):
            self.channel = channel
            yield observer

        with patch.object(gestures, "listen", listen):
            result = gestures.run({"physical_input": 1, "dry_channel": 3, **config}, stop, commands or Queue(), report)
        return result, reports

    def observer(self, detector, results=()):
        results = iter(results)
        return SimpleNamespace(poll=lambda now: next(results, (None, None)), allow_changes=True,
                               window=SimpleNamespace(time=0.), detector=detector)

    def test_states_and_armed_proposals_are_reported_with_the_time_left(self):
        event = GestureEvent("Neck", "Bridge", "drive", .1, 5.)
        detector = SimpleNamespace(is_armed=lambda now: True, seconds_left=lambda now: 12.5, arm=Mock())
        observer = self.observer(detector, [({"event": "guitar-state", "label": "Neck", "closeness": .9}, None),
                                            (None, event)])
        _, reports = self.run_with(observer, {"observe": False}, until=lambda seen: seen[-1]["event"] == "guitar-gesture")
        self.assertEqual(self.channel, 3)                    # the channel the desk named: the unit is not opened
        self.assertEqual([report["event"] for report in reports], ["guitar-status", "guitar-state", "guitar-gesture"])
        self.assertEqual(reports[0]["armed_seconds"], 12.5)
        self.assertTrue(reports[2]["allowed"])
        self.assertEqual(reports[2]["armed_seconds"], 12.5)
        self.assertEqual(GestureEvent.from_dict(reports[2]), event)

    def test_when_the_arm_runs_out_listening_from_the_desk_ends_and_lets_go(self):
        armed = iter([True, True, False])
        detector = SimpleNamespace(is_armed=lambda now: next(armed, False), seconds_left=lambda now: 0., arm=Mock())
        result, reports = self.run_with(self.observer(detector), {"observe": False})
        self.assertEqual(result, {"ended": "arm-expired"})

    def test_listening_to_show_the_estimate_goes_on_observing_and_can_be_armed_again(self):
        state_of = {"armed": True}
        detector = SimpleNamespace(is_armed=lambda now: state_of["armed"], seconds_left=lambda now: 20. * state_of["armed"])
        detector.arm = lambda now: state_of.update(armed=True)
        commands = Queue()

        def until(seen):
            texts = [report.get("text", "") for report in seen]
            if any("run out" in text for text in texts) and not any("armed for" in text for text in texts[1:]):
                commands.put({"event": "guitar-arm"})
            return any("Gestures armed" in text for text in texts)

        observer = self.observer(detector)
        observer.window = SimpleNamespace(time=None)
        state_of["armed"] = True
        calls = {"count": 0}

        def is_armed(now):
            calls["count"] += 1
            if calls["count"] == 3:
                state_of["armed"] = False               # the arm runs out
            return state_of["armed"]

        detector.is_armed = is_armed
        result, reports = self.run_with(observer, {"observe": True}, commands, until)
        self.assertEqual(result, {})
        texts = [report["text"] for report in reports if report["event"] == "guitar-status"]
        self.assertIn("Observing the dry guitar only", texts[1])
        self.assertIn("Gestures armed", texts[2])
        self.assertEqual(reports[-1]["armed_seconds"], 20.)

    def test_needs_an_input_and_the_channel_from_the_desk(self):
        with self.assertRaisesRegex(ValueError, "input 1 or 2"):
            gestures.run({"physical_input": 3}, Event(), Queue(), Mock())
        with self.assertRaisesRegex(ValueError, "which USB channel"):
            gestures.run({"physical_input": 1}, Event(), Queue(), Mock())


# ---- what is saved ----

class SavedTest(unittest.TestCase):
    def setUp(self):
        self.seat = seat_for(self)
        self.folder = folders.taught("Charvel")
        self.folder.mkdir(parents=True)

    def profile(self, control, labels=("Neck", "Bridge")):
        width = 1 if control == "volume" else 12
        folders.write_json(self.folder / f"{control}.json", {
            "schema": 1, "guitar": "Charvel", "control": control, "labels": list(labels),
            "notes": {"45": {"centers": [[0.] * width, [1.] * width], "scale": [2.] * width, "radii": [1., 1.]}}})

    def test_a_mapped_control_is_chosen_and_armed(self):
        self.profile("pickup")
        self.profile("tone", ("Tone 10", "Tone 0"))
        folders.write_json(self.folder / "tone-gesture.json", GestureMapping("Tone 10", "Tone 0", "space").to_dict())
        found = gestures.config("charvel", 2)
        self.assertEqual((found["control"], found["physical_input"], found["arm"]), ("tone", 2, True))
        self.assertEqual(found["gesture"]["control"], "space")
        self.assertFalse(gestures.config("Charvel", 1, control="pickup", observe=True)["arm"])

    def test_listening_that_could_turn_nothing_is_refused_unless_it_is_to_show_the_estimate(self):
        self.profile("pickup")
        with self.assertRaisesRegex(ValueError, "No gesture is mapped"):
            gestures.config("Charvel", 1)
        self.assertTrue(gestures.config("Charvel", 1, observe=True)["observe"])

    def test_a_mapping_for_settings_no_longer_taught_is_dropped(self):
        self.profile("pickup")
        folders.write_json(self.folder / "pickup-gesture.json", GestureMapping("Middle", "Bridge", "space").to_dict())
        self.assertIsNone(gestures.saved("Charvel", "pickup")[1])

    def test_nothing_taught_is_explained(self):
        with self.assertRaisesRegex(ValueError, "Teach two settings"):
            gestures.config("Les Paul", 1)


# ---- on the desk's side ----

def proposal(control="drive", step=.1, allowed=True, **more):
    return {"event": "guitar-gesture", "home": "Neck", "excursion": "Bridge", "control": control, "step": step,
            "at": 10., "allowed": allowed, **more}


class DeskSideTest(unittest.TestCase):
    """A session on a real seat. The worker process is stood in for: it records its settings and waits for Stop."""

    def setUp(self):
        self.seat = seat_for(self)
        self.studio = self.seat.studio
        self.studio.state.mode = "guitar"
        self.studio.shown.knobs = {"drive": .96}
        self.runs, self.said = [], []
        self.release = Event()

        def run(config, stop, commands, report):
            self.runs.append((config, commands))
            self.said.append(report)
            stop.wait(10)
            return {"ended": "arm-expired"} if self.release.is_set() else {}

        guard = patch.object(worker, "run", run)
        guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.end)
        folder = folders.taught("Charvel")
        folder.mkdir(parents=True)
        for control, labels in (("pickup", ("Neck", "Bridge")), ("tone", ("Tone 10", "Tone 0"))):
            folders.write_json(folder / f"{control}.json", {
                "schema": 1, "guitar": "Charvel", "control": control, "labels": list(labels),
                "notes": {"45": {"centers": [[0.] * 12, [1.] * 12], "scale": [2.] * 12, "radii": [1., 1.]}}})
            folders.write_json(folder / f"{control}-gesture.json", GestureMapping(*labels, "drive").to_dict())

    def end(self):
        gestures.stop()
        settle(self.seat, lambda: not gestures.running(), seconds=5)
        gestures._session = None

    def started(self, **options):
        session = gestures.start(self.seat, **options)
        self.assertTrue(settle(self.seat, lambda: len(self.runs) >= 1 and session.started))
        return session

    def test_the_session_holds_the_audio_asks_the_engine_for_the_channel_and_gives_the_hold_back(self):
        self.studio.let_go = Future()                       # the engine has not let go of the driver yet
        session = gestures.start(self.seat, control="pickup")
        self.seat.tick()
        self.assertEqual(self.studio.audio_held_by, "gestures")
        time.sleep(.15)
        self.assertEqual(self.runs, [], "the worker was started before the engine let go of the driver")
        self.studio.let_go.set_result(None)
        self.assertTrue(settle(self.seat, lambda: self.runs))
        config, _ = self.runs[0]
        self.assertEqual(self.studio.host.calls, [("inputs", (2,))])
        self.assertEqual((config["dry_channel"], config["control"], config["arm"]), (2, "pickup", True))
        self.assertTrue(gestures.running())
        gestures.stop()
        self.assertTrue(settle(self.seat, lambda: session.finished))
        self.assertFalse(gestures.running())
        self.assertIsNone(self.studio.audio_held_by)
        self.assertEqual(self.seat.watchers, [])

    def test_it_does_not_start_while_the_learner_holds_the_audio(self):
        self.studio.audio_held_by = "learner"
        session = gestures.start(self.seat, control="pickup")
        self.assertTrue(settle(self.seat, lambda: session.finished))
        self.assertEqual(self.runs, [])
        self.assertEqual(self.studio.audio_held_by, "learner")
        self.assertIn("in use (learner)", self.studio.statuses[-1])

    def test_an_armed_gesture_turns_an_available_knob_and_the_desk_can_show_the_time_left(self):
        heard = []
        session = self.started(control="pickup", listener=heard.append)
        self.said[0]({"event": "guitar-status", "text": "Listening.", "armed_seconds": 20.})
        self.said[0](proposal(armed_seconds=20.))
        self.seat.tick()
        self.assertEqual(self.studio.turned, [{"drive": 1.}])
        self.assertTrue(heard[-1]["applied"])
        self.assertEqual(heard[-1]["value"], 1.)
        self.assertIn("Moved Distortion +10%", self.studio.statuses[-1])
        shown = gestures.state()
        self.assertEqual((shown["running"], shown["armed"], shown["instrument"], shown["control"]),
                         (True, True, "Charvel", "pickup"))
        self.assertTrue(19 < shown["seconds_left"] <= 20)
        gestures.arm()
        self.assertEqual(self.runs[0][1].get_nowait(), {"event": "guitar-arm"})
        self.assertIs(session, gestures._session)

    def test_unarmed_or_unavailable_gestures_are_only_reported(self):
        heard = []
        self.started(control="pickup", listener=heard.append)
        for event in (proposal(allowed=False), proposal(control="reverb")):
            self.said[0](event)
            self.seat.tick()
            self.assertEqual(self.studio.turned, [])
            self.assertFalse(heard[-1]["applied"])

    def test_when_the_arm_runs_out_the_session_ends_and_says_so(self):
        session = self.started(control="pickup")
        self.release.set()
        session.signal.set()                # the stand-in worker returns, as the real one does when the arm runs out
        self.assertTrue(settle(self.seat, lambda: session.finished))
        self.assertEqual(gestures.state(), {"running": False, "armed": False, "seconds_left": 0., "instrument": None,
                                            "control": None})
        self.assertIn("Press Gestures on to arm again", self.studio.statuses[-1])
        self.assertIsNone(self.studio.audio_held_by)

    def test_one_fault_in_a_handler_does_not_stop_the_sessions_clock(self):
        # Before: `_poll` rescheduled itself only after the handler returned, so one fault left `running()` true
        # for good and everything that waits for gestures to end waited for ever.
        session = self.started(control="pickup")
        self.studio.turn = Mock(side_effect=RuntimeError("the engine has stopped"))
        self.said[0](proposal())
        self.seat.tick()
        self.assertIn("the engine has stopped", self.studio.statuses[-1])
        self.assertIn(session.step, self.seat.watchers)
        gestures.stop()
        self.assertTrue(settle(self.seat, lambda: not gestures.running()))
        self.assertTrue(session.finished)
        self.assertIsNone(self.studio.audio_held_by)

    def test_a_listener_that_fails_does_not_keep_the_session_from_ending(self):
        session = self.started(control="pickup", listener=Mock(side_effect=RuntimeError("the panel is gone")))
        gestures.stop()
        self.assertTrue(settle(self.seat, lambda: session.finished))
        self.assertFalse(gestures.running())

    def test_started_again_for_the_same_control_the_listener_is_added_and_the_gesture_armed_again(self):
        # Before: the second caller got the first session back and its listener was dropped.
        first, heard = self.started(control="pickup"), []
        second = gestures.start(self.seat, control="pickup", listener=heard.append)
        self.assertIs(second, first)
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(self.runs[0][1].get_nowait(), {"event": "guitar-arm"})
        self.said[0]({"event": "guitar-state", "control": "pickup", "label": "Neck", "closeness": .9, "reason": ""})
        gestures.stop()
        self.assertTrue(settle(self.seat, lambda: first.finished))
        self.assertEqual([event["event"] for event in heard], ["guitar-state", "guitar-stopped"])

    def test_started_for_another_control_the_first_session_ends_and_the_new_one_follows_it(self):
        first, heard = self.started(control="pickup"), []
        second = gestures.start(self.seat, control="tone", listener=heard.append, observe=True)
        self.assertIsNot(second, first)
        self.assertTrue(first.signal.is_set())
        self.assertTrue(settle(self.seat, lambda: len(self.runs) == 2))
        self.assertTrue(first.finished)
        self.assertEqual((self.runs[1][0]["control"], self.runs[1][0]["observe"]), ("tone", True))
        self.assertTrue(gestures.running())
        self.assertEqual(self.studio.audio_held_by, "gestures")
        self.said[1]({"event": "guitar-state", "control": "tone", "label": "Tone 10", "closeness": .9, "reason": ""})
        self.seat.tick()
        self.assertEqual(heard[-1]["label"], "Tone 10")

    def test_listening_does_not_watch_what_the_desk_plays(self):
        # The desk ends gesture listening itself before it starts the phrase (`Desk.toggle_play`), and holds the
        # phrase back until the input is free. The session's own check for a playing phrase could never be true.
        session = self.started(control="pickup")
        self.studio.state.mode = "phrase"
        self.seat.tick()
        self.assertFalse(session.signal.is_set())
        self.assertFalse(session.finished)

    def test_it_is_refused_on_a_desk_whose_unit_is_simulated(self):
        # Listening opens the Quad Cortex's real audio driver, which a simulated unit has nothing to do with.
        self.seat.real_audio = False
        with self.assertRaisesRegex(ValueError, "Not with a simulated unit"):
            gestures.start(self.seat, control="pickup")
        self.assertFalse(gestures.running())
        self.assertIsNone(self.studio.audio_held_by)

    def test_a_failure_to_start_is_said_and_written_down(self):
        self.studio.host.error = RuntimeError("the engine stopped")
        session = gestures.start(self.seat, control="pickup")
        self.assertTrue(settle(self.seat, lambda: session.finished))
        self.assertIn("Gesture listening stopped: the engine stopped", self.studio.statuses[-1])
        self.assertIsNone(self.studio.audio_held_by)
        self.assertIn("Gesture listening failed", (self.studio.folder / "errors.log").read_text(encoding="utf-8"))


class RenamedKnobTest(unittest.TestCase):
    def test_a_mapping_saved_for_warmth_moves_brightness_the_other_way(self):
        mapping = GestureMapping.from_dict({"home": "Neck", "excursion": "Bridge", "control": "warmth", "step": .1})
        self.assertEqual((mapping.control, mapping.step), ("brightness", -.1))


if __name__ == "__main__":
    unittest.main()
