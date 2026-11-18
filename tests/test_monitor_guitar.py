from contextlib import ExitStack, contextmanager
from queue import Queue
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tone_lab import guitar_gestures, monitor


class ShapeGuitarTest(unittest.TestCase):
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
