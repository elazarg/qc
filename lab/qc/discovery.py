"""Read-only Quad Cortex discovery and repeatable comparisons of observations."""

from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import distribution
from pathlib import Path
import ast
import json
import time

from google.protobuf.descriptor_pb2 import FieldDescriptorProto
from google.protobuf.message import DecodeError
from google.protobuf.json_format import MessageToDict
from google.protobuf.message_factory import GetMessageClass
from qc_mcp import protocol
from qc_mcp.transport import QCError, QuadCortex

from lab.qc import assets
from lab.qc import device as connection
from lab.qc.unit import Unit
from lab.sound import catalog
from lab.sound.chain import Chain, from_preset


def capabilities():
    """Inventory the installed SDK without importing its MCP server or opening USB."""
    package = distribution("qc-mcp")
    source = package.locate_file("qc_mcp/server.py").read_text(encoding="utf-8")
    direct = json.loads(package.read_text("direct_url.json") or "{}")
    sdk = [name for name, value in vars(QuadCortex).items() if not name.startswith("_") and callable(value)]
    tools = [node.name for node in ast.parse(source).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and any(ast.unparse(decorator).startswith("mcp.tool(") for decorator in node.decorator_list)]
    used = set()
    for path in Path(__file__).resolve().parents[1].rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                receiver = node.func.value
                if (isinstance(receiver, ast.Name) and receiver.id == "device"
                        or isinstance(receiver, ast.Attribute) and receiver.attr == "device"):
                    used.add(node.func.attr)
    families = []
    for command, name in protocol.COMMANDS.items():
        schemas = {}
        for version in protocol.PROTOCOL_VERSIONS:
            if command in protocol.commands(version):
                try:
                    fields = protocol.message_class(command, version).DESCRIPTOR.fields
                except KeyError:
                    continue
                schemas[version] = [
                    {"number": field.number, "name": field.name, "repeated": field.is_repeated,
                     "type": FieldDescriptorProto.Type.Name(field.type).removeprefix("TYPE_").lower(),
                     "message": field.message_type.full_name if field.message_type else None,
                     "enum": {item.name: item.number for item in field.enum_type.values} if field.enum_type else None}
                    for field in fields]
        families.append({"id": command, "name": name, "schemas": schemas,
                         "evidence": "decoded" if schemas else "named",
                         "observation": "state" if name in STATE_COMMANDS + SETTINGS else None})
    return {"adapter": {"version": package.version, "commit": direct.get("vcs_info", {}).get("commit_id")},
            "protocol": families, "sdk_methods": sdk, "mcp_tools": tools,
            "sdk_methods_called_by_tone_lab": sorted(used.intersection(sdk))}


def model_catalog():
    """Model metadata, in display units. The installed library does not prove entitlement on the unit."""
    return [asdict(model) for model in catalog.models()]


def dependencies(chain):
    """Locate IR Loader and Neural Capture references by the identifiers in the preset."""
    references = []
    for row, column, block in chain.blocks:
        for bound in assets.references(block):
            references.append({"row": row, "column": column, **bound.to_record()})
    return references


def preset_file(path, schema):
    """Decode an exported BinaryPreset protobuf without altering the copy."""
    if path.suffix.lower() != ".pb":
        raise ValueError("Choose an exported BinaryPreset .pb file.")
    raw = path.read_bytes()
    preset = protocol.message_class("RecallPreset", schema).DESCRIPTOR.fields_by_name["preset"].message_type
    try:
        parsed = GetMessageClass(preset).FromString(raw)
    except DecodeError:
        raise ValueError("The file could not be decoded as a BinaryPreset.") from None
    if not parsed.chains:
        raise ValueError("The file did not contain a preset grid.")
    chain = from_preset(parsed)
    return {"schema": schema, "name": parsed.name, "chain": chain.to_record(), "dependencies": dependencies(chain)}


def preview_parameter(state, row, column, index, value):
    """Describe a parameter edit in display units against a snapshot, without accessing the unit."""
    from lab.tools.workbench import with_control

    chain = Chain.from_record(state["chain"])
    if not 0 <= row < len(chain.rows) or not 0 <= column < len(chain.rows[row].cells):
        raise ValueError("Choose a row and column from the snapshot.")
    edited, block, parameter = with_control(chain, row, column, index, value)
    return {"revision": state["revision"], "block": block.name, "control": parameter.name, "units": parameter.units,
            "changes": diff({"chain": state["chain"]}, {"chain": edited.to_record()})}


STATE_COMMANDS = ("SetlistPosition", "Scene", "PresetDirty")
SETTINGS = ("GeneralSettings", "Mode", "GlobalTempo", "IOSettings", "GlobalEQ", "MasterVolume")
READ_ERRORS = (OSError, RuntimeError, QCError)


class Stale(RuntimeError):
    """The unit did not supply one coherent observation."""


def correlated_read(device, name, *, timeout_ms=1500):
    """Reject the SDK's fallback to an unrelated editor reply on a shared connection."""
    message = protocol.message_class(name)(action=protocol.ACTION["READ"], request_id=device.next_request_id())
    _, reply, _ = device.request(name, message, timeout_ms=timeout_ms)
    if reply is None or reply.request_id != message.request_id:
        raise Stale(f"No matching reply for {name}.")
    return reply


def read_settings(device, *, correlated):
    """Read the tested settings families. Unsupported or unanswered fields stay unavailable."""
    results = {}
    for name in SETTINGS:
        try:
            reply = correlated_read(device, name) if correlated else device.read_state(name)
            payload = MessageToDict(reply, preserving_proto_field_name=True) if reply is not None else {}
            for field in ("action", "request_id"):
                payload.pop(field, None)
            results[name] = {"value": payload} if payload else {"unavailable": "No settings returned"}
        except READ_ERRORS as error:
            results[name] = {"unavailable": str(error)}
    return results


@contextmanager
def observer(*, simulated=False):
    if simulated:
        from lab.qc.sim import SimUnit

        yield SimUnit(), False
    else:
        with connection.connect(exclusive=True) as device:
            yield device, True


def _pointer(unit, correlated):
    if not correlated:
        values = (unit.position(), unit.scene(), unit.dirty())
    else:
        device = unit.device
        position, scene, dirty = (correlated_read(device, name) for name in STATE_COMMANDS)
        if not position.folder_key or not scene.HasField("selected_scene"):
            raise Stale("The unit did not return its position and scene.")
        values = ({"folder_key": position.folder_key, "position": position.position,
                   "is_factory": position.is_factory}, scene.selected_scene, dirty.is_dirty)
    if not values[0] or not 0 <= values[1] <= 7 or values[2] is None:
        raise Stale("The unit did not return its position, scene and edit state.")
    return values


@dataclass(frozen=True)
class Snapshot:
    firmware: str | None
    schema: str | None
    position: dict
    scene: int
    unsaved: bool
    name: str
    chain: dict
    dependencies: list

    def record(self):
        state = asdict(self)
        revision = sha256(json.dumps(state, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
        return {**state, "revision": revision}


def snapshot(device, *, correlated=False):
    """Observe the live graph, including edits. Reject a pointer change during the read."""
    unit = Unit(device)
    before = _pointer(unit, correlated)
    preset = device.get_current_preset(timeout_ms=2500)
    after = _pointer(unit, correlated)
    if preset is None or before != after:
        raise Stale("The preset changed or did not finish reading. Read it again.")
    chain = from_preset(preset)
    return Snapshot(unit.firmware, getattr(device, "protocol_version", None), *after, preset.name, chain.to_record(),
                    dependencies(chain))


def diff(before, after):
    """Compare JSON snapshots. Report leafy changes and preserve lists added or removed."""
    changes = []

    def compare(path, old, new):
        if old == new:
            return
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(old.keys() | new.keys()):
                if key in old and key in new:
                    compare([*path, key], old[key], new[key])
                else:
                    changes.append({"path": [*path, key], "before": old.get(key), "after": new.get(key),
                                    "change": "added" if key in new else "removed"})
        elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
            for index, (left, right) in enumerate(zip(old, new)):
                compare([*path, index], left, right)
        else:
            changes.append({"path": path, "before": old, "after": new})

    compare([], {key: value for key, value in before.items() if key != "revision"},
            {key: value for key, value in after.items() if key != "revision"})
    return changes


def watch(open_observer, count, interval):
    """Yield a baseline, changes, and explicit stale intervals. Try a fresh session after a failed read."""
    if count < 1 or interval < 1:
        raise ValueError("Read at least once, with at least one second between reads.")
    previous, attempts = None, 0
    while attempts < count:
        entered = False
        try:
            with open_observer() as (device, correlated):
                entered = True
                while attempts < count:
                    attempts += 1
                    now = datetime.now(timezone.utc).isoformat()
                    current = snapshot(device, correlated=correlated).record()
                    changes = diff(previous, current) if previous else []
                    yield {"at": now, "freshness": "fresh", "revision": current["revision"],
                           **({"changes": changes} if previous else {"snapshot": current})}
                    previous = current
                    if attempts < count:
                        time.sleep(interval)
        except READ_ERRORS as error:
            if not entered:
                attempts += 1
            previous = None
            yield {"at": datetime.now(timezone.utc).isoformat(), "freshness": "stale", "reason": str(error)}
            if attempts < count:
                time.sleep(interval)


def watch_editor(count, interval, *, open_editor=connection.observe_editor):
    """Report the editor's inbound traffic only. Receiving traffic does not prove current preset state."""
    if count < 1 or interval < 1:
        raise ValueError("Read at least once, with at least one second per listening window.")
    try:
        with open_editor() as device:
            for _ in range(count):
                commands = device.passive_commands(interval)
                counts = Counter(protocol.COMMANDS.get(command, str(command)) for command in commands)
                yield {"freshness": "fresh" if counts else "stale", "source": "passive-hid",
                       "events": dict(counts), "at": datetime.now(timezone.utc).isoformat()}
    except READ_ERRORS as error:
        yield {"freshness": "stale", "source": "passive-hid", "reason": str(error),
               "at": datetime.now(timezone.utc).isoformat()}


def inventory(device, *, details=False):
    """Read the on-device library, with private names and keys omitted unless requested."""
    listing = device.list_directory()
    if not listing.get("presets"):
        raise Stale("The unit did not return its preset directory.")
    if details:
        return {**listing, "local_assets": [asset.to_record() for asset in assets.from_listing(listing)]}
    result = Counter()
    for category in ("presets", "irs", "captures"):
        folders = listing.get(category, [])
        result[f"{category}_folders"] = len(folders)
        result[f"{category}_entries"] = sum(bool(entry.get("key") or entry.get("name"))
                                            for folder in folders for entry in folder.get("files", []))
    return dict(result)
