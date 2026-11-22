"""Offline schema and USB trace tools for the Quad Cortex HID protocol."""

import csv
from io import StringIO
import subprocess

from google.protobuf.descriptor_pb2 import FieldDescriptorProto
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import DecodeError
from qc_mcp import protocol


FIELDS = ("frame.number", "frame.time_epoch", "usb.endpoint_address", "usb.capdata", "usb.data_fragment",
          "usbhid.data", "usbhid.data.vendor", "usbhid.data.unknown")


def schema(version):
    """Describe all named messages reachable from the installed command descriptors."""
    messages, enums, commands = {}, {}, {}

    def visit_enum(item):
        enums[item.full_name] = {value.name: value.number for value in item.values}
        return item.full_name

    def visit_message(item):
        if item.full_name not in messages:
            fields = messages[item.full_name] = []
            for field in item.fields:
                entry = {"id": field.number, "name": field.name,
                         "type": FieldDescriptorProto.Type.Name(field.type).removeprefix("TYPE_").lower(),
                         "repeated": field.is_repeated, "presence": field.has_presence}
                if field.message_type is not None:
                    entry["message"] = visit_message(field.message_type)
                if field.enum_type is not None:
                    entry["enum"] = visit_enum(field.enum_type)
                if field.containing_oneof is not None:
                    entry["oneof"] = field.containing_oneof.name
                fields.append(entry)
        return item.full_name

    for number, name in protocol.commands(version).items():
        try:
            root = protocol.message_class(number, version).DESCRIPTOR
        except KeyError:
            root = None
        commands[number] = {"name": name, "message": visit_message(root) if root else None}
    return {"schema": version, "commands": commands, "messages": messages, "enums": enums,
            "transport": {"host_report_id": protocol.REPORT_HOST_TO_QC, "device_report_id": protocol.REPORT_QC_TO_HOST,
                          "bytes_per_report": protocol.REPORT_SIZE + 1, "max_chunk_bytes": protocol.CHUNK_MAX,
                          "chunk_flags": {"first": protocol.FLAG_FIRST, "middle": protocol.FLAG_MIDDLE,
                                          "last": protocol.FLAG_LAST, "single": protocol.FLAG_SINGLE},
                          "actions": protocol.ACTION}}


def capture_fields(tshark, path, device):
    """Read a saved capture with TShark. Filter to one USB address before extracting report data."""
    usb_filter = (f"usb.device_address == {device} && usb.data_len > 0 "
                  "&& (usb.endpoint_address == 0x00 || usb.endpoint_address == 0x81)")
    command = [str(tshark), "-n", "-r", str(path), "-Y",
               usb_filter, "-T", "fields", "-E", "occurrence=f"]
    for field in FIELDS:
        command.extend(("-e", field))
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "TShark could not read the USB capture.")
    return csv.reader(StringIO(result.stdout), delimiter="\t")


def report_from(columns):
    """Find a full QC HID report among the payload fields for one URB."""
    for column in columns:
        try:
            packet = bytes.fromhex(column.replace(":", ""))
        except ValueError:
            continue
        if (len(packet) == protocol.REPORT_SIZE + 1
                and packet[0] in (protocol.REPORT_HOST_TO_QC, protocol.REPORT_QC_TO_HOST)
                and 0 < packet[1] <= protocol.CHUNK_MAX
                and packet[2] in (protocol.FLAG_FIRST, protocol.FLAG_MIDDLE, protocol.FLAG_LAST,
                                  protocol.FLAG_SINGLE)):
            return packet
    return None


def decode_rows(rows, version, *, values=False):
    """Reassemble each direction separately and label decoded commands with frame/time provenance."""
    buffers = {protocol.REPORT_HOST_TO_QC: protocol.Reassembler(),
               protocol.REPORT_QC_TO_HOST: protocol.Reassembler()}
    started = {}
    for frame, at, endpoint, *payload in rows:
        report = report_from(payload)
        if report is None:
            continue
        report_id, flags = report[0], report[2]
        if flags & protocol.FLAG_FIRST:
            started[report_id] = (int(frame), float(at))
        if report_id not in started:
            continue
        for envelope in buffers[report_id].feed(report, has_report_id=True):
            first_frame, begin = started.pop(report_id)
            command, body, _ = protocol.decode_message(envelope)
            if command is None:
                continue
            direction = "host-to-qc" if report_id == protocol.REPORT_HOST_TO_QC else "qc-to-host"
            event = {"frame": first_frame, "last_frame": int(frame), "at": begin, "endpoint": endpoint,
                     "direction": direction, "command_id": command,
                     "command": protocol.COMMANDS.get(command), "payload_bytes": len(body)}
            try:
                message = protocol.message_class(command, version).FromString(body)
            except (KeyError, DecodeError):
                event["decoded"] = False
            else:
                event["decoded"] = True
                event["fields"] = [field.name for field, _ in message.ListFields()]
                if "action" in event["fields"]:
                    event["action"] = message.action
                if "request_id" in event["fields"]:
                    event["request_id"] = message.request_id
                if values:
                    event["values"] = MessageToDict(message, preserving_proto_field_name=True)
            yield event
