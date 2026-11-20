"""The connection to the unit, against a stand-in for the device library. Never the real hardware."""

from threading import RLock
from types import SimpleNamespace
import unittest

from lab.qc import device
from lab.qc.sim import SimUnit
from lab.qc.unit import Unit


class Library:
    """Stands in for the library's `QuadCortex`: records how it was opened and closed."""

    def __init__(self, share, *, verified=True, fails=None, on_open=None):
        self.share, self.verified, self.fails, self.on_open = share, verified, fails, on_open
        self.opened = self.closed = False
        self.protocol_version_verified = False
        self.firmware = "4.1.1"
        self._io_lock, self._pending = RLock(), []
        self.seen = []

    def open(self):
        if self.on_open:
            self.on_open()
        if self.fails:
            raise self.fails
        self.opened = True
        self.protocol_version_verified = self.verified

    def close(self):
        self.closed = True

    def read_state(self, name, **_):
        self.seen.append(len(self._pending))
        self._pending.append(("retained", name))   # the library re-queues what it already had
        return SimpleNamespace(selected_scene=2)

    def get_setlist_position(self):
        self.seen.append(len(self._pending))
        self._pending.append(("retained", "position"))
        return {"folder_key": "/media/p4/Presets/My Presets", "position": 3, "is_factory": False}


class Opener:
    def __init__(self, **options):
        self.options, self.made = options, []

    def __call__(self, share):
        self.made.append(Library(share, **self.options))
        return self.made[-1]


class ConnectTest(unittest.TestCase):
    def test_a_writing_connection_takes_the_unit_and_closes_it_after(self):
        opener = Opener()
        with device.connect(write=True, opener=opener, editor=lambda: False) as qc:
            (made,) = opener.made
            self.assertTrue(made.opened)
            self.assertFalse(made.share)
            self.assertEqual(qc.firmware, "4.1.1")
            self.assertFalse(made.closed)
        self.assertTrue(made.closed)

    def test_a_reading_connection_shares_the_unit(self):
        opener = Opener()
        with device.connect(opener=opener, editor=lambda: False):
            pass
        self.assertTrue(opener.made[0].share)

    def test_cortex_control_has_priority_before_anything_is_opened(self):
        opener = Opener()
        with self.assertRaises(device.EditorActive):
            with device.connect(write=True, opener=opener, editor=lambda: True):
                self.fail("connected beside Cortex Control")
        self.assertEqual(opener.made, [])

    def test_cortex_control_starting_meanwhile_is_still_given_priority(self):
        calls = []

        def editor():
            calls.append(1)
            return len(calls) > 1      # absent at first, there by the time the device is opened

        opener = Opener()
        with self.assertRaises(device.EditorActive):
            with device.connect(write=True, opener=opener, editor=editor):
                self.fail("connected beside Cortex Control")
        self.assertFalse(opener.made[0].opened)
        self.assertTrue(opener.made[0].closed)

    def test_cortex_control_starting_during_the_open_is_noticed_after(self):
        running = []
        opener = Opener(on_open=lambda: running.append(True))
        with self.assertRaises(device.EditorActive):
            with device.connect(write=True, opener=opener, editor=lambda: bool(running)):
                self.fail("connected beside Cortex Control")
        self.assertTrue(opener.made[0].closed)

    def test_a_refused_device_is_blamed_on_cortex_control_only_when_it_runs(self):
        refusal = OSError("The device is held exclusively by another program")
        running = []
        opener = Opener(fails=refusal, on_open=lambda: running.append(True))
        with self.assertRaises(device.EditorActive):
            with device.connect(write=True, opener=opener, editor=lambda: bool(running)):
                pass
        with self.assertRaisesRegex(OSError, "exclusively"):
            with device.connect(write=True, opener=Opener(fails=refusal), editor=lambda: False):
                pass

    def test_unknown_firmware_is_refused_and_the_device_closed(self):
        opener = Opener(verified=False)
        with self.assertRaisesRegex(RuntimeError, "firmware"):
            with device.connect(write=True, opener=opener, editor=lambda: False):
                self.fail("used a unit whose firmware was not read")
        self.assertTrue(opener.made[0].closed)

    def test_the_device_closes_when_the_work_fails(self):
        opener = Opener()
        with self.assertRaisesRegex(ValueError, "work"):
            with device.connect(write=True, opener=opener, editor=lambda: False):
                raise ValueError("work")
        self.assertTrue(opener.made[0].closed)

    def test_the_editor_is_never_seen_off_windows(self):
        from unittest.mock import patch

        with patch.object(device.sys, "platform", "linux"):
            self.assertFalse(device.editor_running())


class DeviceTest(unittest.TestCase):
    def test_polled_requests_do_not_grow_the_queue(self):
        opener = Opener()
        with device.connect(write=True, opener=opener, editor=lambda: False) as qc:
            for _ in range(5):
                qc.read_state("Scene")
                qc.get_setlist_position()
            made = opener.made[0]
            self.assertEqual(made.seen, [0] * 10)
            self.assertEqual(made._pending, [])

    def test_the_unit_drives_the_connected_device(self):
        sim = SimUnit()
        sim._io_lock = RLock()      # the library's lock is re-entrant; `Unit.position` holds it around a request
        with device.connect(write=True, opener=lambda share: sim, editor=lambda: sim.editor_open) as qc:
            unit = Unit(qc, settle=.05, sleep=lambda seconds: None)
            self.assertEqual(unit.firmware, "4.1.1")
            self.assertEqual(unit.name(), "Brit 2203")
            self.assertEqual(unit.position()["position"], 0)
            unit.select_scene(3)
            self.assertEqual(sim.scene, 3)
            qc.anything_set = 1                     # attributes land on the library's object
            self.assertEqual(sim.anything_set, 1)

    def test_the_simulated_editor_is_respected(self):
        sim = SimUnit()
        sim.editor_open = True
        with self.assertRaises(device.EditorActive):
            with device.connect(write=True, opener=lambda share: sim, editor=lambda: sim.editor_open):
                pass

    def test_recording_channels_follow_the_dry_wet_setting(self):
        sim = SimUnit()
        self.assertEqual(device.recording_channels(sim, 2), (2, 3, 4))

        def wet(dry_wet):
            usb = SimpleNamespace(dry_wet=dry_wet)
            message = SimpleNamespace(HasField=lambda field: True,
                                      settings=SimpleNamespace(HasField=lambda field: True, usb_port=usb))
            return SimpleNamespace(read_message=lambda name: message)

        self.assertEqual(device.recording_channels(wet(1), 1), (3, 1, 2))
        with self.assertRaises(RuntimeError):
            device.recording_channels(wet(5), 1)
        with self.assertRaises(ValueError):
            device.recording_channels(sim, 3)


if __name__ == "__main__":
    unittest.main()
