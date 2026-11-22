import isolated  # noqa: F401

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lab.hearing import audio
from lab.qc import discovery
from lab.qc.assets import Asset, block_for
from lab.qc.device import observe_editor
from lab.qc.mcp import server
from lab.qc.sim import SimUnit
from lab.sound.catalog import GAIN_BLOCK
from lab.sound.chain import Chain


class DiscoveryTest(unittest.TestCase):
    def test_inventory_distinguishes_schema_from_observation(self):
        families = {row["name"]: row for row in discovery.capabilities()["protocol"]}
        self.assertEqual(families["Scene"]["evidence"], "decoded")
        self.assertIsNone(families["RemoteControl"]["observation"])
        self.assertEqual(set(families["ModelPreset"]["schemas"]), {"4.1"})

    def test_snapshot_detects_a_scene_change_and_ignores_revision_in_diff(self):
        device = SimUnit()
        before = discovery.snapshot(device).record()
        device.set_scene(1)
        after = discovery.snapshot(device).record()
        self.assertNotEqual(before["revision"], after["revision"])
        self.assertEqual(discovery.diff(before, after), [{"path": ["scene"], "before": 0, "after": 1}])

    def test_snapshot_refuses_a_preset_that_changes_while_being_read(self):
        device = SimUnit()
        read = device.get_current_preset

        def changing(**options):
            device.set_scene(1)
            return read(**options)

        with patch.object(device, "get_current_preset", changing), self.assertRaises(discovery.Stale):
            discovery.snapshot(device)

    def test_shared_read_refuses_a_reply_belonging_to_the_editor(self):
        class OtherReply:
            def next_request_id(self):
                return 47

            def request(self, name, message, **options):
                return name, type(message)(request_id=5, selected_scene=2), b""

        with self.assertRaises(discovery.Stale):
            discovery.correlated_read(OtherReply(), "Scene")

    def test_watch_reports_stale_then_reopens_and_starts_a_new_baseline(self):
        device = SimUnit()
        outcomes = iter((OSError("offline"), device))

        @contextmanager
        def observer():
            outcome = next(outcomes)
            if isinstance(outcome, OSError):
                raise outcome
            yield outcome, False

        with patch.object(discovery.time, "sleep"):
            events = list(discovery.watch(observer, 2, 1.))
        self.assertEqual([event["freshness"] for event in events], ["stale", "fresh"])
        self.assertIn("snapshot", events[1])

    def test_shared_observer_does_not_start_a_device_session(self):
        calls = []

        class Handle:
            def open(self):
                calls.append("opened")

            def close(self):
                calls.append("closed")

        def opener(share):
            calls.append(("shared", share))
            return SimpleNamespace(io=Handle())

        with observe_editor(opener=opener, editor=lambda: True):
            pass
        self.assertEqual(calls, [("shared", True), "opened", "closed"])

    def test_editor_watch_reports_traffic_without_claiming_a_snapshot(self):
        @contextmanager
        def open_editor():
            yield SimpleNamespace(passive_commands=lambda seconds: [1, 1, 5])

        event = next(discovery.watch_editor(1, 1, open_editor=open_editor))
        self.assertEqual(event["events"], {"Grid": 2, "IOMeter": 1})
        self.assertNotIn("snapshot", event)

    def test_preview_uses_display_units_and_does_not_write_to_the_unit(self):
        device = SimUnit()
        device.add_block(GAIN_BLOCK)
        before = discovery.snapshot(device).record()
        result = discovery.preview_parameter(before, 0, 0, 0, -9.)
        self.assertEqual(result["units"], "dB")
        self.assertEqual(result["revision"], before["revision"])
        self.assertTrue(result["changes"])
        self.assertEqual(before, discovery.snapshot(device).record())

    def test_decoding_exported_preset_does_not_rewrite_it(self):
        preset = SimUnit().get_current_preset()
        data = preset.SerializeToString()
        with TemporaryDirectory() as folder:
            path = Path(folder) / "fake.pb"
            path.write_bytes(data)
            self.assertEqual(discovery.preset_file(path, "4.1")["name"], preset.name)
            self.assertEqual(path.read_bytes(), data)

    def test_dependencies_use_the_asset_key_in_the_chain(self):
        path = "CIR_1234abcd"
        block = block_for(Asset("ir", path, "Example cabinet"))
        chain = Chain.from_record(discovery.snapshot(SimUnit()).chain).with_block(0, 0, block)
        self.assertEqual(discovery.dependencies(chain)[0]["key"], path)

    def test_constructing_audio_for_a_control_session_does_not_open_asio(self):
        class Driver:
            @staticmethod
            def query_devices():
                raise AssertionError("The control session tried to load ASIO.")

        with patch.object(audio, "os", SimpleNamespace(environ={})), patch.dict("sys.modules", sounddevice=Driver()):
            audio.Audio()


class DiscoveryMCPTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_agent_has_discovery_and_previews_without_device_write_tools(self):
        app = server(simulated=True, desk_open=lambda: True)
        tools = await app.list_tools()
        self.assertEqual({tool.name for tool in tools}, {"get_snapshot", "get_settings", "preview_parameter"})
        response = await app.call_tool("get_snapshot", {})
        self.assertEqual(response.structured_content["scene"], 0)
