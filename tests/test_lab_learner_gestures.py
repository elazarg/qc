from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lab import knobs
from lab.learner import folders, gestures, inputs
from lab.learner.gestures import GestureDetector, GestureEvent, GestureMapping, replay


def observations(labels, *, first=0.0, hop=0.12):
    return [{"time": first + step * hop, "label": label,
             "confidence": 0 if label is None else .95} for step, label in enumerate(labels)]


class GuitarGesturesTest(unittest.TestCase):
    def setUp(self):
        self.mapping = GestureMapping("neck", "bridge", "space", -.15)
        self.pattern = ["neck"] * 3 + ["bridge"] * 3 + ["neck"] * 3

    def test_opt_in_and_completed_return(self):
        detector = GestureDetector(self.mapping)
        for row in observations(self.pattern):
            self.assertIsNone(detector.observe(row["time"], row["label"], row["confidence"]))
        detector.arm(0)
        events = [detector.observe(row["time"], row["label"], row["confidence"])
                  for row in observations(self.pattern)]
        self.assertEqual(events[:-1], [None] * 8)
        self.assertIsNotNone(events[-1])
        self.assertAlmostEqual(events[-1].at, .96)
        knobs = {"space": .3, "drive": -.2}
        self.assertEqual(events[-1].apply(knobs), {"space": .15, "drive": -.2})
        self.assertEqual(knobs["space"], .3)

    def test_ignores_ordinary_switches_uncertainty_and_long_gaps(self):
        for labels in (
            ["bridge"] * 3 + ["neck"] * 3,
            ["neck"] * 3 + ["bridge"] * 16 + ["neck"] * 3,
            ["neck"] * 3 + ["bridge"] * 2 + [None, "bridge"] + ["neck"] * 3,
            ["neck"] * 3 + ["bridge"] * 3 + [None] * 4 + ["neck"] * 3,
            ["neck"] * 3 + ["bridge"] * 3 + ["middle"] + ["neck"] * 3,
        ):
            with self.subTest(labels=labels):
                self.assertEqual(replay(self.mapping, observations(labels)), [])

    def test_only_a_short_dropout_can_bridge_two_stable_states(self):
        pattern = ["neck"] * 3 + [None] + ["bridge"] * 3 + [None] + ["neck"] * 3
        self.assertEqual(len(replay(self.mapping, observations(pattern))), 1)

    def test_cooldown_and_arm_expiry(self):
        twice = self.pattern + ["bridge"] * 3 + ["neck"] * 3
        self.assertEqual(len(replay(self.mapping, observations(twice))), 1)
        detector = GestureDetector(self.mapping, arm_seconds=.85)
        detector.arm(0)
        for row in observations(self.pattern):
            self.assertIsNone(detector.observe(row["time"], row["label"], row["confidence"]))
        self.assertFalse(detector.is_armed(1))

    def test_out_of_order_observation_cancels_pending_return(self):
        track = observations(self.pattern)
        track.insert(6, {"time": track[4]["time"], "label": "bridge", "confidence": .99})
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


class Studio:
    def __init__(self, knob_values, available=("drive", "space")):
        self.state = SimpleNamespace(side="a", mode="guitar", instrument="Charvel")
        self.shown = SimpleNamespace(knobs=dict(knob_values))
        self.offered = list(available)
        self.turned, self.statuses = [], []

    def sound(self, side):
        return self.shown

    def available(self):
        return self.offered

    def turn(self, values):
        self.turned.append(values)
        self.shown.knobs = values

    def set(self, **changes):
        self.statuses.append(changes.get("status"))


def proposal(control="drive", step=.1, allowed=True):
    return {"event": "guitar-gesture", "home": "Neck", "excursion": "Bridge", "control": control, "step": step,
            "at": 10., "allowed": allowed}


class DeskSideTest(unittest.TestCase):
    def session(self, studio, **options):
        desk = SimpleNamespace(root=None, studio=studio)
        return gestures.Session(desk, {"mode": "gestures"}, launch=False, **options)

    def test_an_armed_gesture_turns_an_available_knob_on_the_sound_on_show(self):
        studio = Studio({"drive": .96})
        heard = []
        session = self.session(studio, listener=heard.append)
        session.events.put(proposal())
        session.pump()
        self.assertEqual(studio.turned, [{"drive": 1.}])
        self.assertTrue(heard[-1]["applied"])
        self.assertEqual(heard[-1]["value"], 1.)
        self.assertIn("Moved Drive +10%", studio.statuses[-1])

    def test_unarmed_or_unavailable_gestures_are_only_reported(self):
        for event in (proposal(allowed=False), proposal(control="reverb")):
            with self.subTest(event=event):
                studio = Studio({"drive": .5})
                heard = []
                session = self.session(studio, listener=heard.append)
                session.events.put(event)
                session.pump()
                self.assertEqual(studio.turned, [])
                self.assertFalse(heard[-1]["applied"])

    def test_the_phrase_loop_ends_listening_and_a_stop_is_passed_on(self):
        studio = Studio({})
        studio.state.mode = "phrase"
        heard = []
        session = self.session(studio, listener=heard.append)
        session.pump()
        self.assertTrue(session.signal.is_set())
        session.events.put({"event": "guitar-stopped", "error": "no dry audio"})
        session.pump()
        self.assertTrue(session.finished)
        self.assertEqual(heard[-1]["event"], "guitar-stopped")
        self.assertIn("no dry audio", studio.statuses[-1])


class SavedTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        guard = patch.object(folders, "SESSIONS", Path(directory.name))
        guard.start()
        self.addCleanup(guard.stop)
        self.folder = folders.calibration("Charvel")
        self.folder.mkdir(parents=True)

    def profile(self, control, labels=("Neck", "Bridge")):
        folders.write_json(self.folder / f"{control}.json", {
            "schema": 1, "guitar": "Charvel", "control": control, "labels": list(labels),
            "notes": {"45": {"centers": [[0.] * 12, [1.] * 12], "scale": [2.] * 12, "radii": [1., 1.]}}})

    def test_a_mapped_control_is_chosen_and_armed(self):
        self.profile("pickup")
        self.profile("tone", ("Tone 10", "Tone 0"))
        folders.write_json(self.folder / "tone-gesture.json", GestureMapping("Tone 10", "Tone 0", "space").to_dict())
        found = gestures.config("charvel", 2)
        self.assertEqual((found["control"], found["physical_input"], found["arm"]), ("tone", 2, True))
        self.assertEqual(found["gesture"]["control"], "space")
        self.assertFalse(gestures.config("Charvel", 1, control="pickup")["arm"])

    def test_a_mapping_for_states_no_longer_taught_is_dropped(self):
        self.profile("pickup")
        folders.write_json(self.folder / "pickup-gesture.json", GestureMapping("Middle", "Bridge", "space").to_dict())
        self.assertIsNone(gestures.saved("Charvel", "pickup")[1])

    def test_nothing_taught_is_explained(self):
        with self.assertRaisesRegex(ValueError, "Teach two settings"):
            gestures.config("Les Paul", 1)


class WorkerSideTest(unittest.TestCase):
    def test_states_and_armed_proposals_are_reported_until_stop(self):
        stop = Event()
        event = GestureEvent("Neck", "Bridge", "drive", .1, 5.)
        results = iter([({"event": "guitar-state", "label": "Neck", "confidence": .9}, None),
                        (None, event)])

        def poll(now):
            try:
                return next(results)
            except StopIteration:
                stop.set()
                return None, None

        observer = SimpleNamespace(poll=poll, allow_changes=True, window=SimpleNamespace(time=0.),
                                   detector=SimpleNamespace(is_armed=lambda now: True))

        @contextmanager
        def listen(config, channel):
            self.assertEqual(channel, 3)
            yield observer

        reports = []
        with (patch.object(inputs, "recording_channels", return_value=(3, 1, 2)),
              patch.object(gestures, "listen", listen)):
            gestures.run({"physical_input": 1, "profile": "p.json"}, stop, Queue(), reports.append)
        kinds = [report["event"] for report in reports]
        self.assertEqual(kinds[:3], ["guitar-status", "guitar-state", "guitar-gesture"])
        self.assertTrue(reports[2]["allowed"])
        self.assertEqual(GestureEvent.from_dict(reports[2]), event)

    def test_only_input_one_or_two(self):
        with self.assertRaisesRegex(ValueError, "input 1 or 2"):
            gestures.run({"physical_input": 3}, Event(), Queue(), Mock())


if __name__ == "__main__":
    unittest.main()
