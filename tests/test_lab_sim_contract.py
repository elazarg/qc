"""What the simulated unit (`lab.qc.sim`) promises about the real one, checked as far as it can be without it.

Every test of a write to the unit runs on the simulated unit, so the app is only as safe as the simulated unit
is true. Five things are checked here:

- `ParityTest`: every call the simulated unit answers is a call of the device library with the same arguments,
  and every call the app makes on the device fits both. A change of the library (its commit is pinned in
  `requirements.txt`) that moves a call is noticed here.
- `RepliesTest`: what the simulated unit hands back where it does not use the library's own messages has only
  the fields those messages have.
- `RealPresetsTest`: presets read from a real unit are what the simulated unit takes a preset to be, and it
  holds them unchanged. The files are looked for in the folder `TONE_LAB_CONTRACT_PRESETS` names (any `*.pb`
  under it, each a `BinaryPreset` as the unit returns it); without it the test is skipped. It never looks in a
  folder of its own choosing: preset reads are the player's.
- `TraceTest`: a trace of what the real unit was asked and what it answered, replayed on the simulated unit.
  The app writes one when TONE_LAB_TRACE names a file (`lab.qc.trace.recording`, put around the device in
  `lab/qc/host.py`): every call is written to the file as it is made. The file is JSON, a line each:

      {"trace": 1, "of": "Quad Cortex device calls", "firmware": "4.1.1", "recorded": "2026-11-22 10:15"}
      {"call": "recall", "args": [], "options": {"folder_key": "...", "position": 255, "is_factory": false},
       "value": null, "seconds": 0.0004}
      {"call": "get_current_preset", "args": [], "options": {}, "preset": "<the BinaryPreset's bytes, base64>",
       "seconds": 0.31}
      {"call": "read_state", "args": ["PresetDirty"], "options": {}, "message": {"is_dirty": false}, ...}
      {"call": "get_setlist_position", ..., "value": {"folder_key": "...", "position": 255, "is_factory": false}}
      {"call": "read_state", "args": ["Scene"], "options": {}, "raised": "QCError: no response ...", ...}

  `replayed(lines)` makes every write of the trace on a simulated unit and compares every read with what it
  answers: the preset (within the tolerance for a rounded value), the position, the scene, the edit state and
  the names in the directory. What the simulated unit cannot know (the state the unit was in to begin with,
  and what the player's own presets hold) is taken over from the trace the first time it is read. A read that
  differs and is followed, with no write in between, by one that agrees is a change the unit showed late: it
  is counted apart. The trace is looked for in the file `TONE_LAB_CONTRACT_TRACE` names; without it the test
  is skipped. A trace holds the player's presets: it belongs in the data folder or in `personal/`, not in
  the repository.
- `UnsteadyTest`: the simulated unit answers at once, exactly, every time. A real unit rounds what it is
  given, may show a change a few reads late, and may leave a read unanswered. `Unsteady` is a simulated unit
  with those manners, and the tests say what the guarded unit (`lab.qc.unit`) does then. Where it does not do
  what it should, the test is marked as an expected failure with the fault in its reason. Whether the real
  unit ever shows a value late is not known: only a trace recorded from it can tell.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import ast  # noqa: E402
from copy import deepcopy  # noqa: E402
import inspect  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402
from tempfile import TemporaryDirectory  # noqa: E402
import unittest  # noqa: E402

from qc_mcp import directory as library_directory, preset as wire, protocol  # noqa: E402
from qc_mcp.transport import QCError, QuadCortex  # noqa: E402

from lab.qc.device import Device  # noqa: E402
from lab.qc.rig import Rig  # noqa: E402
from lab.qc.sim import MY_PRESETS, SimUnit  # noqa: E402
from lab.qc.slots import BUILD, Slots  # noqa: E402
from lab.qc.unit import POLL, Mismatch, Moved, Refused, Unit  # noqa: E402
from lab.sound import catalog  # noqa: E402
from lab.sound.chain import COLUMNS, ROWS, SCENES, TOLERANCE, Chain, differences, from_preset  # noqa: E402

from support import PLEXI, V30, fast, inputs, one_row  # noqa: E402
from test_lab_unit import lead_and_rhythm  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CONTRACT_PRESETS = "TONE_LAB_CONTRACT_PRESETS"
CONTRACT_TRACE = "TONE_LAB_CONTRACT_TRACE"

# What a test does to the simulated unit in the player's place: not calls of the library.
PLAYERS_HANDS = {"save_slot", "player_selects", "add_ir", "add_capture"}
# The simulated unit's names for arguments the app always passes by place.
OTHER_NAME = {"model_id": "model_hash", "index": "param_index", "name": "command"}
# Arguments that are a wait: the library's default is the time the unit needs, the simulated unit's is none.
WAITS = {"settle_s"}


def calls_of(kind):
    return {name: member for name, member in vars(kind).items()
            if callable(member) and not name.startswith("_") and name not in PLAYERS_HANDS}


def named(function):
    """The arguments a function names, without `self`, and whether it takes any others by keyword."""
    parameters = list(inspect.signature(function).parameters.values())[1:]
    return ([p for p in parameters if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)],
            any(p.kind is p.VAR_KEYWORD for p in parameters))


def real_call(name):
    """The concrete implementation of the device interface used by Unit."""
    return getattr(Device, name, None) or getattr(QuadCortex, name, None)


class ParityTest(unittest.TestCase):
    def test_every_call_the_simulated_unit_answers_is_one_the_library_has_with_the_same_arguments(self):
        simulated = calls_of(SimUnit)
        self.assertGreater(len(simulated), 20)
        for name, function in simulated.items():
            with self.subTest(call=name):
                self.assertTrue(callable(real_call(name)), "the device interface has no such call")
                ours, takes_more = named(function)
                theirs, _ = named(real_call(name))
                self.assertLessEqual(len(ours), len(theirs))
                for mine, real in zip(ours, theirs):
                    self.assertIn(real.name, (mine.name, OTHER_NAME.get(mine.name)))
                    if mine.name not in WAITS:
                        self.assertEqual(mine.default, real.default, mine.name)
                beyond = theirs[len(ours):]
                self.assertEqual([p.name for p in beyond if p.default is p.empty], [])
                if beyond:
                    self.assertTrue(takes_more, f"the library also takes {[p.name for p in beyond]}")

    def calls_on_the_device(self):
        """(file, line, call, how many arguments by place, the names of those by keyword) of every call the app
        makes on the device. A call is taken for one when it is made on something called `device` and the
        simulated unit answers a call of that name."""
        known = set(calls_of(SimUnit))
        for path in sorted((REPO / "lab").rglob("*.py")):
            if path.name == "sim.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in known):
                    continue
                on = ast.unparse(node.func.value)
                if on != "device" and not on.endswith(".device"):
                    continue
                if any(isinstance(argument, ast.Starred) for argument in node.args) \
                        or any(keyword.arg is None for keyword in node.keywords):
                    continue                    # arguments put together at run time cannot be read here
                yield (path.relative_to(REPO).as_posix(), node.lineno, node.func.attr, len(node.args),
                       [keyword.arg for keyword in node.keywords])

    def test_every_call_the_app_makes_on_the_device_fits_the_library_and_the_simulated_unit(self):
        calls = list(self.calls_on_the_device())
        self.assertGreater(len(calls), 25)
        self.assertGreater(len({call for _, _, call, _, _ in calls}), 12)
        for where, line, call, by_place, by_name in calls:
            for implementation in (real_call(call), getattr(SimUnit, call)):
                with self.subTest(call=f"{where}:{line} {call}", on=implementation.__qualname__):
                    signature = inspect.signature(implementation)
                    signature.bind(None, *[None] * by_place, **dict.fromkeys(by_name))


class RepliesTest(unittest.TestCase):
    def fields(self, message):
        return {field.name for field in protocol.message_class(message).DESCRIPTOR.fields}

    def test_the_preset_is_the_librarys_own_message(self):
        self.assertIs(type(SimUnit().get_current_preset()), type(wire.BinaryPreset()))

    def test_the_scene_and_the_position_have_only_fields_the_librarys_messages_have(self):
        device = SimUnit()
        for message in ("Scene", "SetlistPosition"):
            self.assertLessEqual(set(vars(device.read_state(message))), self.fields(message), message)
        self.assertLessEqual(set(device.get_setlist_position()), self.fields("SetlistPosition"))

    def test_the_edit_state_has_only_the_field_the_librarys_message_has(self):
        # Code that read a field the real message lacks would pass on the simulated unit and fail on the real one.
        self.assertLessEqual(set(vars(SimUnit().read_state("PresetDirty"))), self.fields("PresetDirty"))

    def test_the_usb_setting_is_where_the_library_has_it(self):
        settings = protocol.message_class("IOSettings").DESCRIPTOR.fields_by_name["settings"].message_type
        usb = settings.fields_by_name["usb_port"].message_type
        self.assertIn("dry_wet", usb.fields_by_name)
        reply = SimUnit().read_message("IOSettings")
        self.assertIn(reply.settings.usb_port.dry_wet, (0, 1))

    def test_the_processing_costs_have_only_fields_the_librarys_message_has(self):
        described = protocol.message_class("ModuleStats").DESCRIPTOR
        reply = SimUnit().read_message("ModuleStats")
        for part in ("stats", "core_stats"):
            real = set(described.fields_by_name[part].message_type.fields_by_name)
            self.assertLessEqual(set(vars(getattr(reply, part)[0])), real, part)

    def test_the_directory_is_shaped_as_the_library_shapes_it(self):
        message = protocol.message_class("File")()
        message.type = 0
        message.folder.key, message.folder.name = MY_PRESETS, "My Presets"
        message.folder.files.add().name = "One"
        real = library_directory.structure_directory([message])
        device = SimUnit()
        device.save_slot(MY_PRESETS, 2, "Mine", [(PLEXI, {})])
        ours = device.list_directory()
        self.assertEqual(set(ours), set(real))
        folder = next(row for row in ours["presets"] if row["key"] == MY_PRESETS)
        real_folder = real["presets"][0]
        self.assertLessEqual(set(folder), set(real_folder))
        self.assertEqual(len(folder["files"]), 256)             # a setlist is 256 slots, empty ones included
        held = next(row for row in folder["files"] if row.get("name"))
        self.assertLessEqual(set(held), set(real_folder["files"][0]))
        self.assertEqual([row["index"] for row in folder["files"][:3]], [0, 1, 2])


# ---- presets read from a real unit ----

def problems_with(blob):
    """What in a preset, as the unit returned it, is not what the simulated unit and the app take a preset to
    be. Empty when it is."""
    found = []
    message = wire.BinaryPreset()
    try:
        message.ParseFromString(blob)
    except Exception as error:  # noqa: BLE001
        return [f"it is not a preset: {error}"]
    if len(message.chains) != ROWS:
        found.append(f"{len(message.chains)} rows, not {ROWS}")
    if len(message.bypass) != ROWS:
        found.append(f"bypass for {len(message.bypass)} rows, not {ROWS}")
    unknown = set()
    for r, lane in enumerate(message.chains):
        if len(lane.models) != COLUMNS:
            found.append(f"row {r + 1} has {len(lane.models)} cells, not {COLUMNS}")
        if not len(lane.output_control) or len(lane.output_control[0].params) <= catalog.LANE_MUTE:
            found.append(f"row {r + 1} has no output control with a volume and a mute")
        if r < len(message.bypass) and len(message.bypass[r].colBypass) != COLUMNS:
            found.append(f"row {r + 1} has bypass for {len(message.bypass[r].colBypass)} cells, not {COLUMNS}")
        for column, cell in enumerate(lane.models):
            if not cell.hash:
                continue
            where = f"row {r + 1} column {column + 1}"
            try:
                listed = len(catalog.model(cell.hash).params)
            except Exception:  # noqa: BLE001
                unknown.add(cell.hash)
                found.append(f"{where} holds model {cell.hash}, which the catalog does not know")
                continue
            if len(cell.params) != listed + 1:
                found.append(f"{where} ({catalog.model(cell.hash).name}) holds {len(cell.params)} values; the "
                             f"catalog lists {listed}, and the unit is taken to hold one more")
            for index, param in enumerate(cell.params):
                if param.scene_mode and len(param.param_values) < SCENES:
                    found.append(f"{where} parameter {index} is held by scene with {len(param.param_values)} values")
    if unknown:
        return found                # a model the catalog does not know cannot be put on the simulated unit
    chain = from_preset(message)
    if Chain.from_record(chain.to_record()).digest() != chain.digest():
        found.append("written down and read again it is another chain")
    device = SimUnit()
    unit = fast(device)
    device.recall(**BUILD)
    unit.slot = BUILD
    try:
        held = unit.apply(chain, fresh=True)
    except Exception as error:  # noqa: BLE001
        return found + [f"the simulated unit could not be made to hold it: {type(error).__name__}: {error}"]
    return found + [f"on the simulated unit: {line}" for line in differences(held, chain)]


def checked(folder):
    """{file: problems} for every preset under a folder, and how many presets were read."""
    files = sorted(Path(folder).rglob("*.pb"))
    return {str(path.relative_to(folder)): found for path in files
            if (found := problems_with(path.read_bytes()))}, len(files)


class RealPresetsTest(unittest.TestCase):
    def test_a_preset_read_from_the_simulated_unit_passes_and_one_that_is_not_as_expected_does_not(self):
        device = SimUnit()
        unit = fast(device)
        device.recall(**BUILD)
        unit.apply(lead_and_rhythm())
        good = device.get_current_preset()
        short = deepcopy(good)
        del short.chains[0].models[1].params[-1]                # a block with one value fewer than expected
        fewer = deepcopy(good)
        del fewer.chains[3]
        with TemporaryDirectory() as folder:
            (Path(folder) / "deeper").mkdir()
            (Path(folder) / "good.pb").write_bytes(good.SerializeToString())
            (Path(folder) / "deeper" / "short.pb").write_bytes(short.SerializeToString())
            (Path(folder) / "fewer.pb").write_bytes(fewer.SerializeToString())
            found, read = checked(folder)
        self.assertEqual(read, 3)
        self.assertEqual(set(found), {str(Path("deeper") / "short.pb"), "fewer.pb"})
        self.assertIn("the unit is taken to hold one more", found[str(Path("deeper") / "short.pb")][0])
        self.assertIn("3 rows, not 4", found["fewer.pb"][0])

    @unittest.skipUnless(os.environ.get(CONTRACT_PRESETS), f"set {CONTRACT_PRESETS} to a folder of presets read "
                         "from a real unit (*.pb) to check the simulated unit against them")
    def test_presets_read_from_a_real_unit_are_what_the_simulated_unit_takes_a_preset_to_be(self):
        found, read = checked(os.environ[CONTRACT_PRESETS])
        self.assertGreater(read, 0, "no *.pb file under the folder")
        self.assertEqual(found, {}, f"{len(found)} of {read} presets")


# ---- a trace of the real unit, replayed on the simulated one ----

from lab.qc.trace import recording  # noqa: E402  (the app's own recorder: TONE_LAB_TRACE)


READS = ("get_current_preset", "get_setlist_position", "read_state", "read_message", "list_directory",
         "list_model_presets", "select_model_slot")
NOT_REPLAYED = ("open", "close")


def recorded_preset(entry):
    import base64

    message = wire.BinaryPreset()
    message.ParseFromString(base64.b64decode(entry["preset"]))
    return message


def furthest(shown, held):
    """How far apart the values of two readings of the same chain are at most (0 when they are the same)."""
    widest = 0.
    for one, other in zip(shown.rows, held.rows):
        for a, b in zip(one.cells, other.cells):
            if a is None or b is None or a.model != b.model:
                continue
            for index, value in a.params:
                theirs = b.values.get(index)
                pairs = zip(value, theirs) if isinstance(value, tuple) and isinstance(theirs, tuple) else [(value, theirs)]
                for x, y in pairs:
                    if isinstance(x, float) and isinstance(y, float):
                        widest = max(widest, abs(x - y))
    return widest


def replayed(lines):
    """Replay a trace on a simulated unit: every write is made on it, and every read is compared with what it
    answers. Returns what was found:

    - `compared`: reads compared; `adopted`: reads taken over because the simulated unit could not know the
      answer (the unit's own presets, and the state it was in when the trace began);
    - `problems`: where the simulated unit answered otherwise, as "line N: ...";
    - `late`: (line, reads waited) where the real unit showed a change only some reads after the write;
    - `rounding`: the furthest a value the unit showed was from the one the simulated unit holds.
    """
    import json

    entries = [json.loads(line) for line in lines if line.strip()]
    if not entries or entries[0].get("trace") != 1:
        raise ValueError("Not a trace of device calls: its first line does not say so.")
    sim = SimUnit()
    found = {"compared": 0, "adopted": 0, "problems": [], "late": [], "rounding": 0., "firmware": entries[0].get("firmware")}
    known = {"preset": False, "position": False, "dirty": False, "scene": False, "directory": False}
    saved = set()               # slots whose contents the simulated unit knows: saved or read during the replay
    behind = None               # (line, reads so far, what differed) of a read that does not show the last write yet

    def here():
        return (sim.position["folder_key"], int(sim.position["position"]))

    def adopt(name):
        known[name] = True
        found["adopted"] += 1

    for number, entry in enumerate(entries[1:], start=2):
        call, args, options = entry["call"], entry.get("args", []), entry.get("options", {})
        if call in NOT_REPLAYED or "raised" in entry:
            continue
        if call not in READS:
            if behind is not None:
                found["problems"].append(f"line {behind[0]}: {behind[2]}")
                behind = None
            getattr(sim, call)(*args, **options)
            if call == "recall":
                known["preset"] = here() in saved
                known["position"] = True
            elif call == "write_preset_file":
                saved.add(here())
            continue
        if call == "get_current_preset" and "preset" in entry:
            theirs = recorded_preset(entry)
            if not known["preset"]:
                sim.working = theirs
                if here() in sim.slots:         # a preset saved there: now the simulated unit knows what it holds
                    sim.slots[here()] = deepcopy(theirs)
                    saved.add(here())
                adopt("preset")
                continue
            shown, held = from_preset(theirs), from_preset(sim.get_current_preset())
            differing = differences(shown, held) + differences(held, shown)
            if theirs.name != sim.working.name:
                differing.append(f"it is called {theirs.name!r}, the simulated unit's {sim.working.name!r}")
            if differing:
                behind = (behind[0] if behind else number, (behind[1] if behind else 0) + 1,
                          "the preset read differs: " + "; ".join(differing[:3]))
                continue
            if behind is not None:
                found["late"].append((behind[0], behind[1]))
                behind = None
            found["compared"] += 1
            found["rounding"] = max(found["rounding"], furthest(shown, held))
        elif call == "get_setlist_position" and isinstance(entry.get("value"), dict):
            theirs = {key: entry["value"].get(key) for key in ("folder_key", "position", "is_factory")}
            if not known["position"]:
                sim.position = theirs
                adopt("position")
            else:
                found["compared"] += 1
                ours = sim.get_setlist_position()
                if (theirs["folder_key"], theirs["position"]) != (ours["folder_key"], ours["position"]):
                    found["problems"].append(f"line {number}: the unit is on {theirs}, the simulated unit on {ours}")
        elif call == "read_state" and args and args[0] in ("Scene", "PresetDirty") and "message" in entry:
            name, field = ("scene", "selected_scene") if args[0] == "Scene" else ("dirty", "is_dirty")
            theirs = entry["message"].get(field)
            if not known[name]:
                setattr(sim, name, int(theirs) if name == "scene" else bool(theirs))
                adopt(name)
            else:
                found["compared"] += 1
                ours = sim.scene if name == "scene" else sim.dirty
                if (int(theirs) if name == "scene" else bool(theirs)) != ours:
                    found["problems"].append(f"line {number}: the unit says {field} {theirs}, the simulated unit {ours}")
        elif call == "list_directory" and isinstance(entry.get("value"), dict):
            listed = {(folder["key"], row["index"]): row.get("name") or "" for folder in entry["value"].get("presets", [])
                      for row in folder.get("files", [])}
            if not known["directory"]:
                sim.folders = {folder["key"]: folder.get("name", "") for folder in entry["value"].get("presets", [])}
                for slot, name in listed.items():
                    if name and slot not in sim.slots:
                        sim.slots[slot] = sim._preset(name, [])
                for slot in [slot for slot in sim.slots if not listed.get(slot)]:
                    del sim.slots[slot]
                adopt("directory")
            else:
                found["compared"] += 1
                ours = {(folder["key"], row["index"]): row.get("name") or "" for folder in sim.list_directory()["presets"]
                        for row in folder["files"]}
                wrong = sorted(slot for slot in set(listed) | set(ours) if listed.get(slot, "") != ours.get(slot, ""))
                if wrong:
                    key, index = wrong[0]
                    found["problems"].append(f"line {number}: slot {index} of {key} holds {listed.get(wrong[0], '')!r} on "
                                             f"the unit and {ours.get(wrong[0], '')!r} on the simulated unit"
                                             + (f" (and {len(wrong) - 1} more)" if len(wrong) > 1 else ""))
    if behind is not None:
        found["problems"].append(f"line {behind[0]}: {behind[2]}")
    return found


class TraceTest(unittest.TestCase):
    """A trace is made here from the simulated unit itself, by what the app does to a unit: borrow the player's
    preset, build a chain with two paths and two scenes, cache it, save a sound with a knob, give the preset
    back."""

    def trace(self, device, folder):
        from lab.qc.engine import Engine
        from lab.qc.sim import SimAudio

        path = Path(folder) / "trace.jsonl"
        unit = fast(recording(device, path))
        engine = Engine(unit, Slots(unit, Path(folder) / "records"), SimAudio(device))
        engine.open()
        engine.show(one_row(PLEXI, V30), lead_and_rhythm())
        engine.play("b", "guitar")
        engine.turn("b", {"drive": .4})
        engine.save(one_row(PLEXI, V30), "Kept", {"drive": .3})
        engine.close()
        return path.read_text(encoding="utf-8").splitlines()

    def test_a_trace_of_the_simulated_unit_replays_without_a_difference(self):
        with TemporaryDirectory() as folder:
            lines = self.trace(SimUnit(), folder)
        found = replayed(lines)
        self.assertEqual((found["problems"], found["late"], found["rounding"]), ([], [], 0.))
        self.assertGreater(found["compared"], 40)
        self.assertLessEqual(found["adopted"], 8)               # the state it began in, and the empty build slot
        self.assertGreater(len(lines), 150)
        self.assertEqual(found["firmware"], "4.1.1")

    def test_the_app_writes_a_trace_of_a_session_when_asked_and_it_replays(self):
        # TONE_LAB_TRACE names the file: the engine, where the unit is held, writes every call there.
        from unittest import mock

        from lab.qc.host import Host

        with TemporaryDirectory() as folder:
            path = Path(folder) / "session.jsonl"
            with mock.patch.dict(os.environ, {"TONE_LAB_TRACE": str(path)}):
                host = Host(Path(folder) / "home", simulated=True, local=True)
                host.ready.result(10)
                host.call("open").result(10)
                host.call("show", one_row(PLEXI, V30), None).result(20)
                host.call("play", "a", "guitar").result(10)
                host.close()
            found = replayed(path.read_text(encoding="utf-8").splitlines())
        self.assertEqual((found["problems"], found["late"]), ([], []))
        self.assertGreater(found["compared"], 10)

    def test_a_unit_that_rounds_replays_within_what_counts_as_the_same_value(self):
        with TemporaryDirectory() as folder:
            found = replayed(self.trace(Unsteady(step=1 / 256), folder))
        self.assertEqual((found["problems"], found["late"]), ([], []))
        self.assertGreater(found["rounding"], 0.)
        self.assertLessEqual(found["rounding"], 1 / 512 + 1e-6)
        self.assertLess(found["rounding"], TOLERANCE)

    def test_a_unit_that_shows_its_blocks_late_is_found_out(self):
        with TemporaryDirectory() as folder:
            found = replayed(self.trace(Unsteady(late=2, late_calls={"add", "delete"}), folder))
        self.assertEqual(found["problems"], [])
        self.assertGreater(len(found["late"]), 0)
        self.assertEqual({reads for _, reads in found["late"]}, {2})

    def test_a_read_the_simulated_unit_would_not_give_is_named_with_its_line(self):
        import base64
        import json

        with TemporaryDirectory() as folder:
            lines = self.trace(SimUnit(), folder)
        entries = [json.loads(line) for line in lines]
        last = max(number for number, entry in enumerate(entries) if "preset" in entry)
        preset = recorded_preset(entries[last])
        preset.chains[0].models[0].params[0].param_values[0].float_value += .1          # the unit holds another gain
        entries[last]["preset"] = base64.b64encode(preset.SerializeToString()).decode("ascii")
        moved = max(number for number, entry in enumerate(entries) if entry.get("call") == "get_setlist_position")
        entries[moved]["value"]["position"] = 17                                        # and is somewhere else
        found = replayed([json.dumps(entry) for entry in entries])
        self.assertEqual(len(found["problems"]), 2)
        self.assertEqual(sorted(problem.split(":")[0] for problem in found["problems"]),
                         sorted([f"line {last + 1}", f"line {moved + 1}"]))
        self.assertIn("parameter 0", " ".join(found["problems"]))
        with self.assertRaisesRegex(ValueError, "Not a trace"):
            replayed(lines[1:])

    def test_the_units_own_messages_are_written_as_their_fields_and_a_failure_as_what_was_raised(self):
        import json

        class Device:
            firmware = "4.1.1"

            def read_state(self, name):
                if name == "Scene":
                    return protocol.message_class("Scene")(selected_scene=3)
                if name == "PresetDirty":
                    return protocol.message_class("PresetDirty")(is_dirty=True)
                raise QCError(f"no response to {name} within 2500ms")

        with TemporaryDirectory() as folder:
            path = Path(folder) / "trace.jsonl"
            device = recording(Device(), path)
            self.assertEqual(device.firmware, "4.1.1")
            self.assertEqual(device.read_state("Scene").selected_scene, 3)
            device.read_state("PresetDirty")
            with self.assertRaises(QCError):
                device.read_state("Tuner")
            head, scene, dirty, failed = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual((head["trace"], head["firmware"]), (1, "4.1.1"))
        self.assertEqual((scene["call"], scene["args"], scene["message"]["selected_scene"]), ("read_state", ["Scene"], 3))
        self.assertIs(dirty["message"]["is_dirty"], True)
        self.assertEqual(failed["raised"], "QCError: no response to Tuner within 2500ms")
        self.assertGreaterEqual(failed["seconds"], 0)

    @unittest.skipUnless(os.environ.get(CONTRACT_TRACE), f"set {CONTRACT_TRACE} to a trace recorded from a real "
                         "unit (see `recording`) to replay it on the simulated unit")
    def test_a_trace_of_the_real_unit_replays_on_the_simulated_unit(self):
        found = replayed(Path(os.environ[CONTRACT_TRACE]).read_text(encoding="utf-8").splitlines())
        summary = (f"{found['compared']} reads compared, {found['adopted']} taken over, furthest value "
                   f"{found['rounding']:.5f} apart, firmware {found['firmware']}")
        self.assertGreater(found["compared"], 0, summary)
        self.assertEqual(found["problems"], [], summary)
        # The simulated unit shows every write at the next read. A real unit that does not is not a difference in
        # what is held, but it is one in when: `Unsteady` and its tests are the place to make it part of the suite.
        self.assertEqual(found["late"], [], summary)
        self.assertLess(found["rounding"], TOLERANCE, summary)


# ---- a unit that rounds, shows changes late, and leaves reads unanswered ----

class Unsteady(SimUnit):
    """A simulated unit with a real unit's manners.

    - `step`: every value it is given is kept rounded to that step (the real unit: about 1/256).
    - `late`: what a write changes shows in the preset it returns only after that many reads; until then the
      preset reads as it was before. `late_calls` limits this to some kinds of write (as the simulated unit's
      log names them: "add", "delete", "param", "lane", "route", "recall", ...).
    - `unanswered`: {call: [outcomes]}. The next reads by that name (`get_current_preset`,
      `get_setlist_position`, or a state such as "PresetDirty") come back empty (None) or raise the outcome,
      one each, and then the unit answers again.
    """

    def __init__(self, *, step=None, late=0, late_calls=None, **options):
        super().__init__(**options)
        self.step, self.late, self.late_calls = step, late, late_calls
        self.behind, self.shown, self.reads = 0, None, 0
        self.unanswered = {}

    def _write(self, call, *args):
        if self.late and (self.late_calls is None or call in self.late_calls):
            if not self.behind:
                self.shown = deepcopy(self.working)
            self.behind = self.late
        super()._write(call, *args)

    def _silent(self, call):
        outcomes = self.unanswered.get(call)
        if not outcomes:
            return False
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return True

    def get_current_preset(self, **options):
        self.reads += 1
        if self._silent("get_current_preset"):
            return None
        if self.behind:
            self.behind -= 1
            return deepcopy(self.shown)
        return super().get_current_preset(**options)

    def get_setlist_position(self):
        return None if self._silent("get_setlist_position") else super().get_setlist_position()

    def read_state(self, name, **options):
        return None if self._silent(name) else super().read_state(name, **options)

    def _kept(self, value):
        return round(float(value) / self.step) * self.step if self.step else value

    def set_param(self, row, column, index, value):
        super().set_param(row, column, index, self._kept(value))

    def set_lane_param(self, row, field, index, value, column=0):
        super().set_lane_param(row, field, index, self._kept(value), column)


def on_build_slot(device, **options):
    """The guarded unit on a device, with the build slot selected and taken as the slot being worked on."""
    unit = Unit(device, **{"settle": .05, "sleep": lambda seconds: None, **options})
    device.recall(**BUILD)
    unit.slot = BUILD
    return unit


def writes(device, kind):
    return [args for call, args in device.log if call == kind]


class RoundingTest(unittest.TestCase):
    """The unit keeps every value rounded to 1/256."""

    def test_the_rounding_is_inside_what_the_app_takes_for_the_same_value(self):
        self.assertLess(1 / 256 / 2, TOLERANCE)

    def test_a_chain_is_confirmed_and_its_values_are_written_once(self):
        exact, rounding = SimUnit(), Unsteady(step=1 / 256)
        on_build_slot(exact).apply(lead_and_rhythm())
        held = on_build_slot(rounding).apply(lead_and_rhythm())
        self.assertEqual(differences(held, lead_and_rhythm()), [])
        self.assertGreater(len(writes(exact, "param")), 5)
        # The second look at the values (`plan_values`) finds nothing to write again.
        self.assertEqual(len(writes(rounding, "param")), len(writes(exact, "param")))
        gain = catalog.model(1018).index("GAIN")
        kept = rounding.working.chains[0].models[1].params[gain].param_values[1].float_value
        self.assertEqual(kept * 256, round(kept * 256))                     # it did round

    def test_a_chain_cached_on_such_a_unit_is_recalled_and_not_built_again(self):
        device = Unsteady(step=1 / 256)
        unit = fast(device)
        with TemporaryDirectory() as folder:
            slots = Slots(unit, Path(folder))
            rig = Rig(unit, slots)
            with slots.borrowed():
                slot, _ = rig.ready(lead_and_rhythm())
                device.log.clear()
                again, held = rig.ready(lead_and_rhythm())
        self.assertEqual(again, slot)
        self.assertEqual(writes(device, "add"), [])
        self.assertEqual(differences(held, lead_and_rhythm().with_routes([(0, 14), (0, 0), (0, 0), (0, 0)])), [])


class LateTest(unittest.TestCase):
    """The unit shows what a write changed only some reads later."""

    def test_blocks_that_show_a_few_reads_late_are_waited_for(self):
        device = Unsteady(late=3, late_calls={"add", "delete", "split"})
        held = on_build_slot(device).apply(lead_and_rhythm())
        self.assertEqual(differences(held, lead_and_rhythm()), [])
        self.assertGreater(device.reads, 3)

    def test_a_block_the_unit_never_shows_is_given_up_on_when_the_settle_time_is_over_and_not_before(self):
        now = [0.]

        def sleep(seconds):
            now[0] += seconds

        device = Unsteady(refuse=lambda grid, model_id: model_id == V30)
        unit = on_build_slot(device, settle=3., clock=lambda: now[0], sleep=sleep)
        with self.assertRaisesRegex(Refused, "no processing left for"):
            unit.apply(one_row(PLEXI, V30))
        self.assertGreater(now[0], 3.)
        self.assertLess(now[0], 3. + 3 * POLL)
        self.assertGreater(device.reads, 3. / POLL)             # it kept asking, a poll apart, for all that time

    def test_waiting_ends_as_soon_as_the_unit_shows_what_was_waited_for(self):
        now = [0.]

        def sleep(seconds):
            now[0] += seconds

        unit = Unit(SimUnit(), settle=3., clock=lambda: now[0], sleep=sleep)
        answers = iter([None, None, "there"])
        self.assertEqual(unit.until(lambda: next(answers), "it"), "there")
        self.assertAlmostEqual(now[0], 2 * POLL)
        with self.assertRaisesRegex(Mismatch, "did not show the block"):
            unit.until(lambda: None, "the block")

    def test_values_that_show_a_read_late_are_waited_for_and_written_once(self):
        # After writing values the unit is read until they show, within the settle time, as the blocks are: a
        # value shown a read or two late is neither written twice nor taken for refused.
        exact = SimUnit()
        on_build_slot(exact).apply(lead_and_rhythm())
        for late in (1, 2):
            device = Unsteady(late=late, late_calls={"param", "assign", "bypass"})
            held = on_build_slot(device).apply(lead_and_rhythm())
            self.assertEqual(differences(held, lead_and_rhythm()), [])
            self.assertEqual(len(writes(device, "param")), len(writes(exact, "param")), late)

    def test_levels_and_inputs_that_show_a_read_late_are_waited_for(self):
        device = Unsteady(late=1, late_calls={"lane", "route"})
        wanted = one_row(PLEXI, V30, output={catalog.LANE_VOLUME: .5})
        held = on_build_slot(device).apply(wanted)
        self.assertEqual(differences(held, wanted), [])

    def test_a_chain_refused_on_a_unit_that_never_shows_its_inputs_is_not_left_connected(self):
        # When the chain is not confirmed, the input is taken off every row the change was connecting by writing
        # so, without asking the unit first: asked, a unit that has not shown the connection would be taken to
        # have none, and the app would say "refused" with the input still connected.
        for calls in ({"route"}, {"lane", "route"}):
            device = Unsteady(late=10 ** 9, late_calls=calls)
            unit = on_build_slot(device)
            with self.assertRaises(Mismatch):
                unit.apply(lead_and_rhythm())
            self.assertEqual(inputs(device), [], calls)

    @unittest.expectedFailure
    def test_a_recalled_preset_that_shows_a_read_late_is_not_taken_for_the_one_before(self):
        # Expected to fail, and left so until a trace from the real unit says whether it can happen: `Unit.recall`
        # waits for the position to change and then trusts the first preset it reads. A unit that says where it
        # is before its preset has loaded would hand back the preset selected before as the one recalled.
        # `Rig.ready` would then take a cached chain for changed and build it again (slow, not wrong), and a
        # build would start from a stale picture of the slot (caught by the checks that follow). Waiting for
        # "another preset" needs the name the slot should hold, which `recall` is not given.
        device = Unsteady(late=1, late_calls={"recall"})
        device.save_slot(MY_PRESETS, 5, "Mine", [(PLEXI, {})])
        device.save_slot(MY_PRESETS, 6, "Other", [(V30, {})])
        unit = fast(device)
        unit.recall({"folder_key": MY_PRESETS, "position": 5})
        held = unit.recall({"folder_key": MY_PRESETS, "position": 6})
        self.assertEqual([block.model for _, _, block in held.blocks], [V30])


class UnansweredTest(unittest.TestCase):
    """The unit leaves a read unanswered: the library hands back nothing, or raises its `QCError`."""

    def silent_once_after_the_first_block(self, device, outcome):
        add_block = device.add_block
        done = []

        def add(model_id, **options):
            found = add_block(model_id, **options)
            if not done:
                done.append(model_id)
                device.unanswered["get_current_preset"] = [outcome]
            return found

        device.add_block = add

    def test_nothing_is_written_while_the_unit_does_not_say_which_preset_is_selected(self):
        for outcome, raised in ((None, Moved), (QCError("no response to SetlistPosition within 2500ms"), QCError)):
            with self.subTest(outcome=outcome):
                device = Unsteady()
                unit = on_build_slot(device)
                device.log.clear()
                device.unanswered["get_setlist_position"] = [outcome]
                with self.assertRaises(raised):
                    unit.apply(one_row(PLEXI, V30))
                self.assertEqual(device.log, [])

    def test_an_edit_state_that_comes_back_empty_is_asked_for_again(self):
        device = Unsteady()
        device.set_param(0, 0, 0, .9)
        device.unanswered["PresetDirty"] = [None]
        self.assertIs(fast(device).dirty(), True)
        device.unanswered["PresetDirty"] = [None, None]
        self.assertIsNone(fast(device).dirty())                 # not told twice: taken for edits by the callers

    def test_an_edit_state_the_unit_does_not_answer_is_asked_for_again(self):
        # The library raises QCError when the unit does not answer in time: that is asked again, as an empty
        # answer is, and after two the unit "did not tell".
        device = Unsteady()
        device.unanswered["PresetDirty"] = [QCError("no response to PresetDirty within 2500ms")]
        self.assertIs(fast(device).dirty(), False)

    def test_one_read_that_comes_back_empty_while_blocks_settle_does_not_fail_the_build(self):
        # A read left unanswered inside a wait counts as "not shown yet": one dropped read does not end an
        # eleven-second build.
        device = Unsteady()
        unit = on_build_slot(device)
        self.silent_once_after_the_first_block(device, None)
        held = unit.apply(one_row(PLEXI, V30))
        self.assertEqual(differences(held, one_row(PLEXI, V30)), [])

    def test_one_read_the_unit_does_not_answer_while_blocks_settle_does_not_fail_the_build(self):
        # The same when the library raises its QCError.
        device = Unsteady()
        unit = on_build_slot(device)
        self.silent_once_after_the_first_block(device, QCError("no response to RecallPreset within 6000ms"))
        held = unit.apply(one_row(PLEXI, V30))
        self.assertEqual(differences(held, one_row(PLEXI, V30)), [])

    def test_a_build_that_unanswered_reads_end_leaves_nothing_connected(self):
        device = Unsteady()
        unit = on_build_slot(device)
        add_block = device.add_block

        def add(model_id, **options):
            device.unanswered["get_current_preset"] = [None] * 10 ** 6      # from here on the unit says nothing
            return add_block(model_id, **options)

        device.add_block = add
        with self.assertRaises(RuntimeError):
            unit.apply(one_row(PLEXI, V30))
        self.assertEqual(inputs(device), [])

if __name__ == "__main__":
    unittest.main()
