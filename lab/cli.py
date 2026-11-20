"""Tone Lab from the command line, on the new core.

    python -m lab                       the desk, with the Quad Cortex
    python -m lab --simulated           the desk with a simulated unit
    python -m lab <command> ...         one of the commands below

On the computer alone:

    doctor [--unit]                     what the app needs, and with --unit the connection to the unit
    migrate [--dry-run]                 bring the earlier app's history into the data folder
    pod BACKUP [--user-slot 02B]        POD Go presets as Quad Cortex starting points, read locally
    history FILE ... [--missing]        songs by listening time from a Spotify export
    latest-clip                         the newest recorded phrase
    presets                             the presets kept as sounds (see `snapshot`)
    test-compensation REF ROLLED OUT    whether a small EQ offsets a rolled-back tone pot, offline
    shortcut [--desk new|old]           the Windows shortcut and launcher with Tone Lab's taskbar identity

With the unit (each takes --simulated to use the simulated unit instead):

    directory [--factory]               the presets saved on the unit, by setlist and slot
    inspect                             the selected preset: blocks, parameter indices, values
    snapshot                            keep the selected preset as a sound in History
    record [OUT.wav]                    record the dry guitar; without OUT, as a phrase for the desk
    calibrate                           measure the USB round trip
    reamp DRY.wav --slot 12B            play a phrase silently through a saved preset, scene by scene
    sweep DRY.wav --slot 12B ...        the same, with one parameter at several values
    budget                              read each model's processing cost from the unit
    named-settings [--only ID ...]      read Neural DSP's named settings for each model from the unit

A command that uses the unit runs the engine as the desk does (`host.Host`),
on a thread of this process, and closes it after, which gives back the
player's preset. It refuses while the desk is open: the desk holds the unit.
Ctrl+C stops a long command at its next safe point.

Results are printed as JSON, a file path, or lines of text. Errors go to
stderr with exit code 1.

This module also holds the few engine operations the commands need that no
other module has (`connection`, `inspect`, `renders`), run where the unit is
held as `host.call("lab.cli.<name>")`.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

SCENES = "ABCDEFGH"
COMMANDS = ("doctor", "migrate", "pod", "history", "latest-clip", "presets", "test-compensation", "shortcut",
            "directory", "inspect", "snapshot", "record", "calibrate", "reamp", "sweep", "budget", "named-settings")


class Refused(RuntimeError):
    """The command cannot run now, for a reason the player can put right."""


# ---- where things are ----

def data_folder(simulated=False):
    """The folder the desk keeps its records in: the same for the commands, so the cache and calibration are
    shared. The simulated unit has its own."""
    from lab.knowledge import home

    folder = home() / "simulated" if simulated else home()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def desk_open():
    """Whether a desk is open on this computer. Asking brings it forward, which is what the player wants."""
    import socket

    from lab.desk.window import PORT

    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=.5):
            return True
    except OSError:
        return False


# ---- the unit, through the engine ----

@contextmanager
def session(simulated=False, *, open_session=False, physical_input=1, host_class=None):
    """The engine, ready, for one command. `open_session` notes the player's preset first (`Engine.open`), for
    commands that load anything; closing the host gives it back."""
    if not simulated and desk_open():
        raise Refused("The desk is open and holds the Quad Cortex. Close it, or use the desk for this.")
    if host_class is None:
        from lab.qc.host import Host as host_class
    host = host_class(data_folder(simulated), simulated=simulated, local=True, physical_input=physical_input)
    try:
        host.ready.result(60)
        if open_session:
            wait(host, host.call("open"))
        yield host
    finally:
        host.close()


def wait(host, future, *, say=None):
    """The result of a command sent to the host. Ctrl+C stops it at its next safe point; twice gives up."""
    say = say or (lambda text: print(text, file=sys.stderr))
    asked = False
    while True:
        try:
            return future.result(timeout=.25)
        except TimeoutError:
            continue
        except KeyboardInterrupt:
            if asked:
                raise
            asked = True
            host.stop()
            say("Stopping at the next safe point… (Ctrl+C again to leave at once)")


def call(simulated, name, *args, open_session=False, physical_input=1, **options):
    with session(simulated, open_session=open_session, physical_input=physical_input) as host:
        return wait(host, host.call(name, *args, **options))


# ---- engine operations of the commands, run where the unit is held ----

def connection(engine, *, stop=None):
    """What the unit says about itself and the app's place on it. Reads only."""
    from lab.qc.device import recording_channels
    from lab.hearing.listen import calibration

    unit = engine.unit
    found = {"firmware": unit.firmware, "preset": unit.name(), "position": unit.position(), "scene": unit.scene(),
             "unsaved": unit.dirty(), "calibrated": calibration(engine.slots.folder, unit.firmware) is not None}
    try:
        engine.slots.refresh()
        found["tone_lab"] = True
    except RuntimeError as error:
        found["tone_lab"] = str(error)
    try:
        dry = recording_channels(unit.device, engine.physical_input)[0]
        found["dry_channel"] = dry
    except (RuntimeError, ValueError) as error:
        found["dry_channel"] = str(error)
    return found


def inspect(engine, *, stop=None):
    """The selected preset as the unit holds it, unsaved changes included: its chain and where it is."""
    unit = engine.unit
    return {"name": unit.name(), "position": unit.position(), "scene": unit.scene(), "unsaved": unit.dirty(),
            "firmware": unit.firmware, "chain": unit.read()}


def find_slot(engine, place, setlist="My Presets"):
    """A saved preset's slot from its setlist name and its place on the footswitches ("12B")."""
    from lab.qc import library

    rows = library.directory(engine, folder=engine.slots.folder)
    wanted = place.strip().upper().lstrip("0")
    for row in rows:
        if row["setlist"].casefold() == setlist.casefold() and library.place(row["slot"]["position"]) == wanted:
            return row["slot"]
    raise RuntimeError(f"No saved preset at {setlist} {place}.")


def renders(engine, place, setlist, dry, variants, *, stop=None):
    """A saved preset's return for the phrase `dry`, once for each variant, played silently over USB.

    A variant is {"scene": 0..7} and/or {"edit": (row, column, index, display value)}, rows and columns from 0.
    """
    import numpy as np

    from lab.tools.common import render

    slot = find_slot(engine, place, setlist)
    chain, name = engine.read(slot)
    found = []
    for variant in variants:
        if stop is not None and stop.is_set():
            raise RuntimeError("Stopped.")
        changed = chain
        if variant.get("edit"):
            row, column, index, display = variant["edit"]
            block = chain.block(row, column)
            if block is None:
                raise RuntimeError(f"Row {row + 1}, column {column + 1} of {name} holds no block.")
            param = next((p for p in block.info.params if p.index == index), None) if block.info else None
            if param is None or param.kind not in ("float", "fader") or param.low is None or param.high is None:
                raise RuntimeError(f"{block.name} has no continuous parameter {index}. See `inspect`.")
            if not param.low <= display <= param.high:
                raise RuntimeError(f"{block.name} {param.name} goes from {param.low:g} to {param.high:g}.")
            changed = chain.with_block(row, column, block.set_display(index, display))
        found.append(np.asarray(render(engine, changed, dry, scene=variant.get("scene"), stop=stop)))
    return {"name": name, "slot": slot, "renders": found}


# ---- the commands ----

def doctor(args):
    from lab import setup

    findings = setup.checks(data_folder())
    if args.unit:
        try:
            found = call(args.simulated, "lab.cli.connection")
        except Exception as error:  # noqa: BLE001  every failure to reach the unit is a finding
            findings.append(setup.finding("unit", False, f"The Quad Cortex could not be reached: {error}"))
        else:
            where = found["position"]
            findings.append(setup.finding(
                "unit", True, f"The Quad Cortex answers: firmware {found['firmware']}, {found['preset']!r} selected "
                              f"({where.get('folder_key', '')} {where.get('position', '')}), scene "
                              f"{SCENES[found['scene']]}" + (", with unsaved changes" if found["unsaved"] else "")
                              + ". " + ("The USB round trip is measured." if found["calibrated"]
                                        else "The USB round trip is not measured yet: `python -m lab calibrate`.")))
            ok = found["tone_lab"] is True
            findings.append(setup.finding("setlist", ok, 'The "Tone Lab" setlist is there.' if ok
                                          else found["tone_lab"]))
            ok = isinstance(found["dry_channel"], int)
            findings.append(setup.finding("usb", ok, f"The dry guitar arrives on USB channel {found['dry_channel']}."
                                          if ok else found["dry_channel"]))
    args.failed = not all(item["ok"] for item in findings)
    return findings


def migrate(args):
    from lab import migrate as moving

    return moving.summary(moving.migrate(data_folder(), dry_run=args.dry_run))


def _pod_slot(text):
    text = text.strip().upper()
    if len(text) >= 2 and text[:-1].isdigit() and text[-1] in "ABCD":
        return f"{int(text[:-1]):02}{text[-1]}"
    raise ValueError(f"Name a POD Go slot as bank and letter, such as 02B, not {text!r}.")


def _piece(piece):
    from lab.sound import catalog

    found = {"position": piece.position, "name": piece.name, "category": piece.category, "based_on": piece.based_on,
             "relation": piece.relation, "why": piece.why, "on": list(piece.on), "params": piece.params}
    if piece.qc:
        found["quad_cortex"] = catalog.model(piece.qc).name if catalog.model(piece.qc) else piece.qc
    return found


def pod(args):
    from lab.gear import podgo
    from lab.sound.sounds import describe

    presets = podgo.import_backup(args.backup, args.setlist)
    if args.user_slot:
        slot = _pod_slot(args.user_slot)
        chosen = next((item for item in presets if item.slot == slot), None)
        if chosen is None:
            raise ValueError(f"{args.setlist} slot {slot} is empty or missing.")
        return {"setlist": chosen.setlist, "slot": chosen.slot, "name": chosen.name,
                "description": chosen.description, "problems": chosen.problems,
                "chain": describe(chosen.sound.chain) if chosen.sound else None,
                "pieces": [_piece(piece) for piece in chosen.pieces]}
    return {"setlists": sorted(podgo.read_backup(args.backup)["setlists"]), "setlist": args.setlist,
            "presets": [{"slot": item.slot, "name": item.name, "starting_point": item.confident,
                         "problems": item.problems} for item in presets]}


def history(args):
    from lab.player import spotify

    if args.missing:
        from lab.sound.sounds import Store

        tracks = spotify.proposals(args.files, Store(data_folder()), limit=args.limit)
    else:
        tracks = spotify.rank(spotify.history(args.files))[:args.limit]
    return [track.to_record() for track in tracks]


def latest_clip(args):
    """The newest phrase recorded on the desk, else the newest take the earlier app recorded."""
    found = []
    for path in (data_folder(args.simulated) / "phrases").glob("*/phrase.json"):
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (path.parent / "dry.wav").is_file():
            found.append((info.get("recorded", ""), path.stat().st_mtime_ns, info, path.parent / "dry.wav"))
    if found:
        _, _, info, dry = max(found, key=lambda item: (item[0], item[1]))
        return {**info, "dry": str(dry)}
    from lab.knowledge import REPO

    takes = sorted((take for take in (REPO / "tone_sessions" / "recordings").glob("clip-*")
                    if (take / "dry.wav").is_file()), reverse=True)
    if takes and not args.simulated:
        return {"id": takes[0].name, "dry": str(takes[0] / "dry.wav"), "from": "the earlier app"}
    raise RuntimeError("No phrase has been recorded yet.")


def presets(args):
    from lab.sound.sounds import Store

    return [{"id": sound.id, "name": sound.name, "created": sound.created, "slot": sound.origin.get("slot")}
            for sound in Store(data_folder(args.simulated)).sounds() if sound.origin.get("kind") == "preset"]


def test_compensation(args):
    from lab.hearing.audio import read_wave, write_wave
    from lab.learner import compensation

    if args.output.suffix.lower() != ".wav" or args.output.exists():
        raise ValueError("Choose a new WAV path for the compensation result.")
    output, report = compensation.compare(read_wave(args.reference)[:, :1], read_wave(args.rolled)[:, :1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_wave(args.output, output)
    return {**report, "output": str(args.output.resolve())}


def directory(args):
    from lab.qc.library import place

    rows = call(args.simulated, "lab.qc.library.directory", factory=args.factory, folder=str(data_folder(args.simulated)))
    return [{"place": place(row["slot"]["position"]), **row} for row in rows]


def _chain_rows(chain):
    from lab.sound.sounds import settings

    rows = []
    for r, row in enumerate(chain.rows):
        if not (row.blocks or row.inlet or row.outlet):
            continue
        blocks = []
        for column, block in row.blocks:
            params = []
            for index, value in block.params:
                param = next((p for p in block.info.params if p.index == index), None) if block.info else None
                shown = value if isinstance(value, (str, tuple)) or param is None or param.low is None \
                    else round(block.display(index), 3)
                params.append([index, param.name if param else None, list(shown) if isinstance(shown, tuple)
                               else shown])
            blocks.append({"column": column + 1, "model": block.model, "name": block.name,
                           "bypass": list(block.bypass) if isinstance(block.bypass, tuple) else block.bypass,
                           "settings": settings(block), "params": params})
        rows.append({"row": r + 1, "input": row.inlet, "output": row.outlet,
                     "split": [row.split[0] + 1, row.split[1] + 1] if row.split else None, "blocks": blocks})
    return rows


def inspect_command(args):
    from lab.sound.sounds import describe

    found = call(args.simulated, "lab.cli.inspect")
    chain = found.pop("chain")
    return {**found, "summary": describe(chain), "scenes": [label for label in chain.labels],
            "rows": _chain_rows(chain)}


def snapshot(args):
    from lab.sound.sounds import Sound, Store

    found = call(args.simulated, "lab.cli.inspect")
    sound = Sound(name=(found["name"] or "Preset")[:32], chain=found["chain"],
                  origin={"kind": "preset", "slot": found["position"], "firmware": found["firmware"],
                          "snapshot": {"scene": found["scene"], "unsaved": found["unsaved"]}},
                  tags=["snapshot"])
    Store(data_folder(args.simulated)).save(sound)
    return {"sound": sound.id, "name": sound.name, "slot": found["position"], "unsaved": found["unsaved"]}


def record(args):
    import numpy as np

    from lab.hearing.audio import db, write_wave
    from lab.sound.sounds import new_id

    if args.output is not None and (args.output.suffix.lower() != ".wav" or args.output.exists()):
        raise ValueError("Choose a new .wav filename for the DI.")
    dry = call(args.simulated, "record", args.seconds, physical_input=args.input)
    if not len(dry):
        raise RuntimeError("Nothing was recorded.")
    described = {"seconds": round(len(dry) / 48000, 2), "peak_db": round(db(float(np.max(np.abs(dry)))), 1)}
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_wave(args.output, dry)
        return {"file": str(args.output.resolve()), **described}
    phrase_id = new_id()
    folder = data_folder(args.simulated) / "phrases" / phrase_id
    folder.mkdir(parents=True)
    write_wave(folder / "dry.wav", dry)
    info = {"id": phrase_id, "instrument": args.instrument, "setting": args.setting,
            "recorded": datetime.now(timezone.utc).isoformat(timespec="seconds"), "seconds": described["seconds"]}
    (folder / "phrase.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return {**info, "dry": str(folder / "dry.wav"), "peak_db": described["peak_db"]}


def calibrate(args):
    offset = call(args.simulated, "measure_round_trip", open_session=True)
    return {"offset_samples": offset, "ms": round(offset / 48, 2)}


def _write_renders(args, found, labels, extra):
    from lab.hearing.audio import write_wave
    from lab.hearing.measure import measure

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = args.out or data_folder(args.simulated) / "renders" / f"{stamp}-{args.action}"
    target.mkdir(parents=True, exist_ok=True)
    files = []
    for label, samples in zip(labels, found["renders"]):
        path = target / f"{label}.wav"
        write_wave(path, samples)
        files.append({"label": label, "file": str(path), **measure(samples).to_record()})
    record_ = {"preset": found["name"], "slot": found["slot"], "dry": str(Path(args.dry).resolve()), **extra,
               "renders": files}
    (target / "renders.json").write_text(json.dumps(record_, indent=1), encoding="utf-8")
    return {"folder": str(target), "preset": found["name"],
            "renders": [{key: item[key] for key in ("label", "file", "lufs", "true_peak_db", "centroid_hz")}
                        for item in files]}


def _dry(path):
    from lab.hearing.audio import read_wave

    dry = read_wave(path)[:, :1]
    if not len(dry):
        raise ValueError("The phrase is empty.")
    return dry


def reamp(args):
    scenes = args.scenes.upper()
    if not scenes or any(letter not in SCENES for letter in scenes):
        raise ValueError("Name the scenes as letters A to H, such as AB.")
    variants = [{"scene": SCENES.index(letter)} for letter in scenes]
    found = call(args.simulated, "lab.cli.renders", args.slot, args.setlist, _dry(args.dry), variants,
                 open_session=True)
    return _write_renders(args, found, [f"scene-{letter}" for letter in scenes], {"scenes": scenes})


def sweep(args):
    if not 2 <= len(args.values) <= 7:
        raise ValueError("Give two to seven values.")
    edit = (args.row - 1, args.column - 1, args.parameter)
    scene = SCENES.index(args.scene.upper()) if args.scene else None
    variants = [{"edit": (*edit, value), "scene": scene} for value in args.values]
    found = call(args.simulated, "lab.cli.renders", args.slot, args.setlist, _dry(args.dry), variants,
                 open_session=True)
    return _write_renders(args, found, [f"value-{value:g}" for value in args.values],
                          {"row": args.row, "column": args.column, "parameter": args.parameter,
                           "values": args.values, "scene": args.scene})


def budget(args):
    path = args.path or (data_folder(True) / "costs.json" if args.simulated else None)
    return call(args.simulated, "lab.tools.budget.measure", path=None if path is None else str(path))


def named_settings(args):
    path = args.path or (data_folder(True) / "device-presets.json" if args.simulated else None)
    report = (lambda text: print(text, file=sys.stderr, flush=True))
    return call(args.simulated, "lab.tools.named.harvest", only=args.only or None, report=report,
                path=None if path is None else str(path), open_session=True)


# ---- the Windows shortcut ----
#
# Carried over from `tone_lab.desktop.shortcut`. Nothing in it depends on the old desk: it builds
# `Tone Lab.exe` from `desktop_launcher/Program.cs`, which starts `desktop_launcher/start.pyw`, which opens
# whichever desk is chosen (`--new`, `--old`, TONE_LAB_DESK or launcher.json). `--desk` puts the choice in
# the shortcut itself.

APP_ID = "ToneLab.QuadCortex.Desktop"


def _root():
    from lab.knowledge import REPO

    return REPO


def build_launcher(root=None, *, run=subprocess.run):
    """Build `Tone Lab.exe` with the .NET Framework compiler that comes with Windows, unless it is current."""
    from tempfile import TemporaryDirectory

    root = Path(root or _root())
    source, icon, launcher = root / "desktop_launcher" / "Program.cs", root / "ToneLab.ico", root / "Tone Lab.exe"
    if launcher.is_file() and launcher.stat().st_mtime >= max(source.stat().st_mtime, icon.stat().st_mtime):
        return launcher
    framework = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Microsoft.NET"
    candidates = [framework / arch / "v4.0.30319" / "csc.exe" for arch in ("Framework64", "Framework")]
    compiler = next((path for path in candidates if path.is_file()), None)
    if compiler is None:
        raise RuntimeError("Windows could not find the .NET Framework compiler to build the Tone Lab launcher.")
    with TemporaryDirectory(dir=root) as temporary:
        staged = Path(temporary) / launcher.name
        result = run([str(compiler), "/nologo", "/target:winexe", "/optimize+", f"/win32icon:{icon}",
                      f"/out:{staged}", str(source)], capture_output=True, text=True, errors="replace")
        if result.returncode:
            raise RuntimeError((result.stdout + result.stderr).strip() or "Could not build the Tone Lab launcher.")
        staged.replace(launcher)
    return launcher


def _repair_taskbar_links(file, appdata):
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shell, shellcon

    pins = appdata / "Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar"
    for path in pins.glob("*.lnk"):
        try:
            store = propsys.SHGetPropertyStoreFromParsingName(str(path))
            app_id = store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue()
        except pythoncom.com_error:
            continue
        if app_id == APP_ID:
            file.Save(str(path), False)
            shell.SHChangeNotify(shellcon.SHCNE_UPDATEITEM, shellcon.SHCNF_PATHW, str(path), None)


def shortcut(desk=None):
    """`Tone Lab.lnk` in the program folder and the Start menu, and any pinned copy, for the launcher."""
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shell, shellcon

    root = _root()
    launcher = build_launcher(root)
    link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                     shell.IID_IShellLink)
    link.SetPath(str(launcher))
    link.SetArguments(f"--{desk}" if desk else "")
    link.SetWorkingDirectory(str(root))
    link.SetDescription("Tone Lab for Quad Cortex")
    link.SetIconLocation(str(root / "ToneLab.ico"), 0)
    store = link.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
    store.Commit()
    programs = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAMS, 0, 0))
    path = root / "Tone Lab.lnk"
    file = link.QueryInterface(pythoncom.IID_IPersistFile)
    file.Save(str(path), True)
    file.Save(str(programs / path.name), True)
    _repair_taskbar_links(file, Path(shell.SHGetFolderPath(0, shellcon.CSIDL_APPDATA, 0, 0)))
    return path


def shortcut_command(args):
    return shortcut(args.desk)


# ---- the parser ----

def parser():
    command = argparse.ArgumentParser(prog="python -m lab", description="Tone Lab on the new core. Without a "
                                      "command, the desk opens (--simulated: with a simulated unit).")
    sub = command.add_subparsers(dest="action", required=True)

    def unit_command(name, help_text):
        found = sub.add_parser(name, help=help_text)
        found.add_argument("--simulated", action="store_true", help="Use the simulated unit, not the Quad Cortex")
        return found

    doctor_ = unit_command("doctor", "Check what the app needs; --unit also checks the connection to the unit")
    doctor_.add_argument("--unit", action="store_true", help="Also connect to the unit and read its state")
    migrate_ = sub.add_parser("migrate", help="Bring the earlier app's history into the data folder")
    migrate_.add_argument("--dry-run", action="store_true", help="Count what would be brought in; write nothing")
    pod_ = sub.add_parser("pod", help="Read a POD Go backup as Quad Cortex starting points, without any device")
    pod_.add_argument("backup", type=Path)
    pod_.add_argument("--user-slot", help="One slot in full, such as 02B")
    pod_.add_argument("--setlist", default="User", help="The backup's setlist (default User)")
    history_ = sub.add_parser("history", help="Rank songs by listening time from a Spotify data export")
    history_.add_argument("files", nargs="+", type=Path, help="JSON files, folders or the export's zip")
    history_.add_argument("--limit", type=int, default=50)
    history_.add_argument("--missing", action="store_true", help="Only songs the app has no song for yet")
    unit_command("latest-clip", "The newest recorded phrase (--simulated: of the simulated unit's data)")
    unit_command("presets", "Presets kept as sounds (--simulated: of the simulated unit's data)")
    compensation = sub.add_parser("test-compensation", help="Test a bounded tone-pot EQ correction offline")
    compensation.add_argument("reference", type=Path, help="Open-tone dry WAV")
    compensation.add_argument("rolled", type=Path, help="Same notes played with the tone pot rolled back")
    compensation.add_argument("output", type=Path, help="New WAV for the result")
    shortcut_ = sub.add_parser("shortcut", help="Build the launcher and the Windows shortcut")
    shortcut_.add_argument("--desk", choices=("new", "old"), help="Open this desk from the shortcut; without it, "
                           "the launcher's own choice (TONE_LAB_DESK or launcher.json)")

    directory_ = unit_command("directory", "List the presets saved on the unit")
    directory_.add_argument("--factory", action="store_true", help="Include the factory presets")
    unit_command("inspect", "Read the selected preset: blocks, parameter indices and values")
    unit_command("snapshot", "Keep the selected preset as a sound in History")
    record_ = unit_command("record", "Record the dry guitar")
    record_.add_argument("output", nargs="?", type=Path, help="A new WAV; without it the take becomes a phrase")
    record_.add_argument("--seconds", type=float, default=10.)
    record_.add_argument("--input", type=int, choices=(1, 2), default=1)
    record_.add_argument("--instrument", default="unknown", help="For a phrase: the instrument played")
    record_.add_argument("--setting", default="unknown", help="For a phrase: the pickup setting played")
    unit_command("calibrate", "Measure the USB round trip")
    for name, text in (("reamp", "Play a phrase silently through a saved preset, one render per scene"),
                       ("sweep", "Play a phrase through a saved preset with one parameter at several values")):
        task = unit_command(name, text)
        task.add_argument("dry", type=Path, help="The dry phrase, a WAV")
        task.add_argument("--slot", required=True, help="Its place on the unit, such as 12B")
        task.add_argument("--setlist", default="My Presets")
        task.add_argument("--out", type=Path, help="A folder for the renders (default: renders/ in the data folder)")
        if name == "reamp":
            task.add_argument("--scenes", default="A", help="Scene letters, such as AB")
        else:
            task.add_argument("--row", type=int, required=True, help="Row from inspect, from 1")
            task.add_argument("--column", type=int, required=True, help="Column from inspect, from 1")
            task.add_argument("--parameter", type=int, required=True, help="Parameter index from inspect")
            task.add_argument("--values", type=float, nargs="+", required=True, help="Values in display units")
            task.add_argument("--scene", help="Scene letter to hear (default: the preset's own)")
    budget_ = unit_command("budget", "Read each model's processing cost from the unit")
    budget_.add_argument("--path", type=Path, help="Write the table here instead of where the app reads it")
    named_ = unit_command("named-settings", "Read Neural DSP's named settings for each model (about half an hour)")
    named_.add_argument("--only", type=int, nargs="+", help="Only these model ids")
    named_.add_argument("--path", type=Path, help="Write the table here instead of where the app reads it")
    return command


RUN = {"doctor": doctor, "migrate": migrate, "pod": pod, "history": history, "latest-clip": latest_clip,
       "presets": presets, "test-compensation": test_compensation, "shortcut": shortcut_command,
       "directory": directory, "inspect": inspect_command, "snapshot": snapshot, "record": record,
       "calibrate": calibrate, "reamp": reamp, "sweep": sweep, "budget": budget, "named-settings": named_settings}


def run(args):
    return RUN[args.action](args)


def show(result, out=None):
    out = out or sys.stdout
    if result is None:
        return
    if isinstance(result, Path):
        print(result, file=out)
    elif isinstance(result, list) and all(isinstance(line, str) for line in result) and result:
        for line in result:
            print(line, file=out)
    else:
        print(json.dumps(result, indent=2, allow_nan=False, default=str), file=out)


def wants_desk(arguments):
    """No command: the desk opens. Its own flags (`--simulated`) are all it takes."""
    return not arguments or all(argument == "--simulated" for argument in arguments)


def main(arguments=None, *, desk=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if wants_desk(arguments):
        if desk is None:
            from lab.desk.window import main as desk
        desk(arguments)
        return 0
    args = parser().parse_args(arguments)
    args.failed = False
    try:
        result = run(args)
        show(result)
    except KeyboardInterrupt:
        print("Tone Lab: left.", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001  one line for the player, not a traceback
        print(f"Tone Lab: {error}", file=sys.stderr)
        return 1
    return 1 if args.failed else 0
