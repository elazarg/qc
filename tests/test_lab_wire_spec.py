import isolated  # noqa: F401

import unittest

from qc_mcp import protocol

from lab.qc.wire_spec import decode_rows, schema


class WireSpecTest(unittest.TestCase):
    def test_settings_schema_follows_nested_messages_by_their_wire_numbers(self):
        manifest = schema("4.1")
        command = manifest["commands"][protocol.NAME_TO_CMD["IOSettings"]]
        settings = next(field for field in manifest["messages"][command["message"]] if field["name"] == "settings")
        nested = manifest["messages"][settings["message"]]
        self.assertIn("usb_port", {field["name"] for field in nested})
        self.assertNotIn(protocol.NAME_TO_CMD["RemoteControl"], schema("4.0")["commands"])

    def test_wire_capture_keeps_simultaneous_fragment_streams_separate(self):
        out = protocol.encode_message("Scene", protocol.message_class("Scene", "4.1")(
            action=protocol.ACTION["UPDATE"], request_id=42, selected_scene=2).SerializeToString())
        incoming = protocol.encode_message("RecallPreset", b"\x00" * 180)
        report = protocol.message_to_reports(out)[0]
        chunks = protocol.message_to_reports(incoming, report_id=protocol.REPORT_QC_TO_HOST)
        events = list(decode_rows([[str(index), "10.5", "0x00", data.hex()]
                                   for index, data in enumerate((chunks[0], report, chunks[1]), 1)], "4.1"))
        self.assertEqual([event["direction"] for event in events], ["host-to-qc", "qc-to-host"])
        self.assertEqual(events[0]["request_id"], 42)
        self.assertNotIn("values", events[0])
        self.assertEqual((events[1]["frame"], events[1]["last_frame"]), (1, 3))

    def test_wire_capture_ignores_a_partial_report_at_the_start(self):
        data = protocol.message_to_reports(b"x" * 180)[1]
        self.assertEqual(list(decode_rows([["1", "10.5", "0x00", data.hex()]], "4.1")), [])
