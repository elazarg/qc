"""Command line for automated and interactive Tone Lab sessions."""

import argparse
import json
from pathlib import Path
import sys

from . import audio, device, session, sources


def parser():
    command = argparse.ArgumentParser(prog="tone_lab", description=__doc__)
    sub = command.add_subparsers(dest="action", required=True)
    clip = sub.add_parser("clip", help="Open the one-button clip recorder")
    clip.add_argument("--input", type=int, choices=(1, 2), default=1)
    latest = sub.add_parser("latest-clip", help="Find the latest recorded clip")
    latest.add_argument("--pair", action="store_true", help="Include both dry and wet audio paths")
    sub.add_parser("ui", help="Open the optional workbench")
    sub.add_parser("shortcut", help="Create a Windows shortcut with Tone Lab's taskbar identity")
    sub.add_parser("doctor", help="Check the QC connection and open the ASIO stream without starting it")
    sub.add_parser("inspect", help="Read current preset, routing, scene and parameters")
    sub.add_parser("snapshot", help="Archive the current preset locally without writing to the Quad Cortex")
    sub.add_parser("presets", help="List local Quad Cortex preset snapshots")
    search = sub.add_parser("search", help="Find tones you have already rendered or recorded")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--reference", type=Path, help="Match a local reference WAV")
    search.add_argument("--take", help="Prefer results from this take ID")
    demo = sub.add_parser("demo", help="Generate a silent, offline example session")
    demo.add_argument("--guitar", choices=session.GUITARS, default="Charvel")
    record = sub.add_parser("record", help="Record a mono DI WAV over ASIO")
    record.add_argument("output", type=Path)
    record.add_argument("--seconds", type=float, default=10)
    record.add_argument("--input", type=int, default=1)
    for name, description in (("compare", "Compare two or more existing wet WAVs"),
                              ("reamp", "Compare scenes on a USB-routed Tone Lab preset"),
                              ("sweep", "Compare small changes to one shared continuous parameter")):
        task = sub.add_parser(name, help=description)
        task.add_argument("--guitar", choices=session.GUITARS, required=True)
        task.add_argument("--pickup", default="Unspecified")
        task.add_argument("--target", default="")
        task.add_argument("--reference", type=Path)
        if name == "compare":
            task.add_argument("renders", nargs="+", type=Path)
        else:
            task.add_argument("dry", type=Path)
            task.add_argument("--arm", action="store_true", help="Allow audio to be sent to the Quad Cortex")
            if name == "reamp":
                task.add_argument("--scenes", default="AB")
            else:
                task.add_argument("--column", type=int, required=True, help="Column from inspect, starting at 1")
                task.add_argument("--parameter", type=int, required=True, help="Parameter index from inspect")
                task.add_argument("--values", type=float, nargs="+", required=True, help="Values in display units")
    choose = sub.add_parser("choose", help="Save an audition as the preferred tone in the session")
    choose.add_argument("session", type=Path)
    choose.add_argument("label", choices="ABC")
    choose.add_argument("--notes", default="")
    apply = sub.add_parser("apply-winner", help="Reapply saved settings to the working Tone Lab grid")
    apply.add_argument("session", type=Path)
    apply.add_argument("--arm", action="store_true", help="Allow a working-grid edit")
    style = sub.add_parser("style", help="Render two strengths of a style change from the recorded tone")
    style.add_argument("request", help="For example: more jazz, less delay, or darker")
    style.add_argument("--take", type=Path)
    style.add_argument("--arm", action="store_true", help="Allow a temporary USB-only reamp")
    feedback = sub.add_parser("feedback", help="Read or save text tied to a specific comparison")
    feedback.add_argument("comparison", type=Path)
    feedback.add_argument("text", nargs="?")
    pod = sub.add_parser("pod", help="Read the local POD Go archive without any device connection")
    pod.add_argument("backup", type=Path)
    pod.add_argument("--user-slot", help="Show full settings of one User slot, e.g. 02B")
    history = sub.add_parser("history", help="Rank tracks from your local Spotify account-export JSON")
    history.add_argument("files", nargs="+", type=Path)
    return command


def run(args):
    match args.action:
        case "clip" | "latest-clip":
            from . import capture

            if args.action == "latest-clip":
                return capture.latest_clip(pair=args.pair)
            capture.launch(args.input)
        case "ui":
            from .ui import launch

            launch()
        case "shortcut":
            from . import desktop

            return desktop.shortcut()
        case "doctor" | "inspect":
            with device.connect() as qc:
                result = device.inspect(qc)
            if args.action == "doctor":
                result.pop("lanes")
                result["audio"] = audio.check_asio()
            return result
        case "snapshot" | "presets":
            from . import presets

            if args.action == "presets":
                return presets.history()
            with device.connect() as qc:
                return presets.archive(device.read_preset(qc), qc.get_setlist_position(), qc.firmware,
                                       reason="Manual snapshot")
        case "search":
            from . import tone_search

            return [{key: str(value) if isinstance(value, Path) else value for key, value in row.items()}
                    for row in tone_search.search(args.query, reference=args.reference, take_id=args.take)]
        case "demo":
            return session.demo(args.guitar)
        case "record":
            if args.output.suffix.lower() != ".wav" or args.output.exists():
                raise ValueError("Choose a new .wav filename for the DI.")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            samples = audio.record_di(args.seconds, args.input)
            audio.write_wave(args.output, samples)
            return {"file": str(args.output.resolve()), **audio.describe(samples)}
        case "compare":
            return session.imported(args.guitar, args.pickup, args.target, args.renders, args.reference)
        case "reamp" | "sweep":
            sweep = (args.column, args.parameter, args.values) if args.action == "sweep" else None
            return session.compare_qc(args.guitar, args.pickup, args.target, args.dry,
                                      getattr(args, "scenes", ""), args.reference, sweep,
                                      arm=args.arm, progress=lambda message: print(message, file=sys.stderr))
        case "choose":
            return session.choose(args.session, args.label, args.notes)
        case "apply-winner":
            return session.apply_winner(args.session, arm=args.arm)
        case "style":
            from . import iterate

            return iterate.render(args.request, take=args.take, arm=args.arm,
                                  progress=lambda message: print(message, file=sys.stderr))
        case "feedback":
            from . import feedback

            return feedback.save(args.comparison, args.text) if args.text else feedback.notes(args.comparison)
        case "pod":
            inventory = sources.pod_backup(args.backup)
            if args.user_slot:
                selected = next((item for item in inventory["setlists"].get("User", [])
                                 if item["slot"] == args.user_slot.upper()), None)
                if selected is None:
                    raise ValueError(f"User slot {args.user_slot} is empty or missing.")
                return selected
            for group in inventory["setlists"].values():
                for item in group:
                    item.pop("tone")
            return inventory
        case "history":
            return sources.listening_history(args.files)


def main():
    args = parser().parse_args()
    try:
        result = run(args)
        if isinstance(result, Path):
            print(result)
        elif result is not None:
            print(json.dumps(result, indent=2, allow_nan=False))
    except Exception as error:
        print(f"Tone Lab: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
