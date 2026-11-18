from contextlib import ExitStack, contextmanager
from queue import Queue
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from tone_lab import audio, guitar_gestures, monitor


class ShapeGuitarTest(unittest.TestCase):
    def test_switching_tones_keeps_the_phrase_stream_and_ignores_old_knobs(self):
        stop = Event()
        preset_a = {"position": {"position": 10}, "name": "Clean"}
        preset_b = {"position": {"position": 11}, "name": "Lead"}
        initial = {"tone_id": "a", "preset": preset_a, "name": "Clean", "mode": "phrase", "take": "take-1"}
        next_tone = {"tone_id": "b", "preset": preset_b, "name": "Lead", "values": {"drive": .5}}
        source = {"event": "source", "mode": "phrase", "take": "take-1"}
        messages = iter([
            {"tone": {"config": next_tone}, "source": source,
             ("knobs", "a"): {"values": {"drive": -.8}}},
            {("knobs", "b"): {"values": {"drive": .8}}},
            {"source": {"mode": "guitar"}},
            {"source": source},
        ])
        events, opened, stream_starts, controls = [], [], [], []
        position = monitor.iterate.SCRATCH
        physical = [(1, 19), (0, 0), (0, 0), (0, 0)]

        @contextmanager
        def connect(**kwargs):
            yield object()

        @contextmanager
        def grid(qc, config, report):
            opened.append(config["preset"]["name"])
            yield SimpleNamespace(name=config["preset"]["name"]), 0, position, physical, 1

        @contextmanager
        def unavailable(qc, report):
            def choose(config, mode):
                raise monitor.iterate.FixtureUnavailable("The optional slot is in use.")

            yield choose

        def switch(qc, pos, routes, inlet, message, stack, report):
            report({"event": "source", "mode": message["mode"]})
            if message["mode"] == "guitar":
                return None, {}
            cursor = message["transport"]
            stream_starts.append((cursor, cursor.frame))
            cursor.frame += 2048
            return SimpleNamespace(active=True), {"frames": cursor.frame}

        def drain(queue):
            message = next(messages, None)
            if message is None:
                stop.set()
            return message or {}

        def update(qc, base, scene, values, previous, *, position):
            controls.append((base.name, values))
            return values

        def route(qc, pos, routes):
            self.assertEqual(routes[0], (12, 19))
            self.assertTrue(stream_starts[0][0].muted)

        with (patch.object(monitor.device, "connect", connect),
              patch.object(monitor.device, "active_scene", return_value=0),
              patch.object(monitor.device, "live_position", return_value=position),
              patch.object(monitor, "grid", grid),
              patch.object(monitor, "resident_grids", unavailable),
              patch.object(monitor, "switch_source", switch),
              patch.object(monitor, "set_input_routes", route),
              patch.object(monitor, "drain", drain),
              patch.object(monitor.styles, "available_knobs", return_value=["drive"]),
              patch.object(monitor.live, "update", update)):
            monitor.run(initial, stop, Queue(), events.append)

        self.assertEqual(opened, ["Clean", "Lead"])
        self.assertEqual(len(stream_starts), 2)
        first, resumed = stream_starts
        self.assertIs(first[0], resumed[0])
        self.assertEqual((first[1], resumed[1]), (0, 2048))
        self.assertFalse(first[0].muted)
        self.assertEqual(controls, [("Clean", {}), ("Lead", {"drive": .5}), ("Lead", {"drive": .8})])
        self.assertEqual([event["tone_id"] for event in events if event["event"] == "tone"], ["a", "b"])

    def test_saved_sides_use_separate_native_working_slots_and_recall_on_repeat(self):
        iterate = monitor.iterate
        source_a, source_b = {"position": 1}, {"position": 2}
        state = {"position": source_a}
        physical = [(1, 19), (0, 0), (0, 0), (0, 0)]
        prepared, recalled, events = [], [], []
        source = {
            1: SimpleNamespace(name="Clean", default_scene=0, signature="clean"),
            2: SimpleNamespace(name="Lead", default_scene=0, signature="lead"),
        }

        def prepare(qc, pick, base, inlet, *, position, monitor_mode):
            prepared.append((pick, position, monitor_mode))
            fixture = SimpleNamespace(name=iterate.fixture_identity(position)[0], signature=base.signature)
            source[position["position"]] = fixture
            state["position"] = position
            return fixture

        def recall(qc, position, name):
            recalled.append(position)
            state["position"] = position
            return source[position["position"]]

        a = {"tone_id": "a", "preset": {"position": source_a, "name": "Clean"}}
        b = {"tone_id": "b", "preset": {"position": source_b, "name": "Lead"}}
        with (patch.object(monitor.device, "require_clean"),
              patch.object(monitor.device, "live_position", side_effect=lambda _: state["position"]),
              patch.object(monitor.device, "active_scene", return_value=0),
              patch.object(monitor.device, "read_preset", return_value=source[1]),
              patch.object(iterate, "routes", return_value=physical),
              patch.object(iterate, "sound_state", side_effect=lambda preset: preset.signature),
              patch.object(iterate, "prepare_fixture", prepare),
              patch.object(iterate, "recall", recall),
              monitor.resident_grids(object(), events.append) as choose):
            self.assertEqual(choose(a, "phrase")[2], iterate.SCRATCH)
            self.assertEqual(choose(b, "phrase")[2], iterate.LIVE_B)
            self.assertEqual(choose(a, "guitar")[0].signature, "clean")
            self.assertEqual(choose(b, "guitar")[0].signature, "lead")
        self.assertEqual(prepared, [(source_a, iterate.SCRATCH, "phrase"), (source_b, iterate.LIVE_B, "phrase")])
        self.assertEqual(recalled, [source_b, iterate.SCRATCH, iterate.LIVE_B, source_a])
        self.assertEqual(events[0]["fixtures"], [iterate.SCRATCH, iterate.LIVE_B])

    def test_callback_silences_a_recall_gap_but_keeps_the_phrase_at_the_current_frame(self):
        callbacks = []

        @contextmanager
        def output_stream(**options):
            callbacks.append(options["callback"])
            yield object()

        sd = SimpleNamespace(AsioSettings=Mock(), OutputStream=output_stream)
        dry = np.linspace(.01, .2, 4800, dtype=np.float32)[:, None]
        cursor = audio.PhraseCursor(frame=1000)
        output = np.zeros((20, 2), dtype=np.float32)
        with patch.object(audio, "asio_device", return_value=(sd, 0, {})):
            with audio.loop_feed(dry, transport=cursor):
                callbacks[-1](output, 20, None, None)
                np.testing.assert_array_equal(output[:, 0], dry[1000:1020, 0])
                cursor.muted = True
                callbacks[-1](output, 20, None, None)
                self.assertFalse(output.any())
            self.assertEqual(cursor.frame, 1040)
            cursor.muted = False
            with audio.loop_feed(dry, transport=cursor):
                callbacks[-1](output, 20, None, None)
                np.testing.assert_array_equal(output[:, 0], dry[1040:1060, 0])
                self.assertFalse(output[:, 1].any())

    def test_switching_sources_releases_previous_audio_owner(self):
        stop = Event()
        controls = Queue()
        controls.put({"event": "source", "mode": "phrase"})
        controls.put({"event": "source", "mode": "guitar"})
        seen, usage = [], []
        base = SimpleNamespace(name="Source")
        settings = {"arm": True, "profile": "guitar.json", "gesture": None}

        @contextmanager
        def stream(kind):
            self.assertFalse(usage)
            try:
                usage.append(kind)
                seen.append("open " + kind)
                yield SimpleNamespace(active=True)
            finally:
                self.assertEqual(usage.pop(), kind)
                seen.append("close " + kind)

        @contextmanager
        def observe(config, channel):
            self.assertEqual(channel, 3)
            with stream("dry"):
                yield SimpleNamespace(allow_changes=config["arm"], control="pickup",
                                      detector=SimpleNamespace(arm_seconds=20),
                                      poll=lambda now: (None, None))

        def sources(qc, position, routes, input_channel, message, pool, events):
            pool.close()
            if message["mode"] == "guitar":
                return None, {}
            return pool.enter_context(stream("loop")), {"frames": 2048}

        def drain(queue):
            if queue.empty():
                stop.set()
                return {}
            return {"source": queue.get_nowait()}

        @contextmanager
        def connect(**kwargs):
            yield object()

        @contextmanager
        def grid(qc, config, events):
            yield base, 0, {}, ((1, 19),), 1

        received = []
        with (patch.object(monitor.device, "connect", connect),
              patch.object(monitor.device, "recording_channels_from", return_value=(3, 1, 2)),
              patch.object(monitor, "grid", grid),
              patch.object(monitor, "switch_source", sources),
              patch.object(monitor, "drain", drain),
              patch.object(monitor.styles, "available_knobs", return_value=["drive"]),
              patch.object(monitor.live, "update", return_value={}),
              patch.object(monitor.guitar_runtime, "listen", observe)):
            monitor.run({"mode": "guitar", "guitar_config": settings}, stop, controls, received.append)
        self.assertEqual(seen, ["open dry", "close dry", "open loop", "close loop", "open dry", "close dry"])
        self.assertEqual(received[-1]["event"], "guitar-stopped")

    def test_arm_keeps_its_original_expiry_when_reopening_dry_input(self):
        config = {"arm": True, "profile": "guitar.json"}
        mocks = []

        @contextmanager
        def observe(settings, channel):
            mocks.append(settings["arm"])
            yield SimpleNamespace(detector=SimpleNamespace(arm_seconds=20))

        with (ExitStack() as pool,
              patch.object(monitor.device, "recording_channels_from", return_value=(1, 3, 4)),
              patch.object(monitor.guitar_runtime, "listen", observe),
              patch.object(monitor.time, "monotonic", side_effect=[10., 20., 31.])):
            _, deadline = monitor.attach_observer(None, 1, config, pool, Mock(), None)
            pool.close()
            _, deadline = monitor.attach_observer(None, 1, config, pool, Mock(), deadline)
            pool.close()
            _, deadline = monitor.attach_observer(None, 1, config, pool, Mock(), deadline)
        self.assertEqual(mocks, [True, True, False])
        self.assertEqual(deadline, 30.)

    def test_gesture_can_move_only_an_available_knob_within_arm_window(self):
        gesture = guitar_gestures.GestureEvent("Neck", "Bridge", "drive", .1, 10.)
        observer = SimpleNamespace(poll=lambda _: (None, gesture), allow_changes=True,
                                   detector=SimpleNamespace(is_armed=lambda _: True))
        settings = {"drive": .96}
        value, event = monitor.observe_guitar(observer, 10., {"drive"}, settings, 11., Mock())
        self.assertEqual(value["drive"], 1.)
        self.assertTrue(event["applied"])
        for controls, deadline in ((set(), 11.), ({"drive"}, 10.)):
            actual, event = monitor.observe_guitar(observer, 10., controls, settings, deadline, Mock())
            self.assertEqual(actual, settings)
            self.assertFalse(event["applied"])


if __name__ == "__main__":
    unittest.main()
