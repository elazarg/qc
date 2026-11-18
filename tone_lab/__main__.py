"""Command line for automated and interactive Tone Lab sessions."""

import argparse
import json
from pathlib import Path
import sys

from . import audio, device, session, sources


def parser():
    command = argparse.ArgumentParser(prog="tone_lab", description=__doc__)
    sub = command.add_subparsers(dest="action", required=True)
    clip = sub.add_parser("clip", help="Open the listening desk")
    clip.add_argument("--input", type=int, choices=(1, 2), default=1)
    clip.add_argument("--workbench", action="store_true", help="Open the detailed controls")
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
    compensation = sub.add_parser("test-compensation", help="Test a bounded tone-pot EQ correction offline")
    compensation.add_argument("reference", type=Path, help="Open-tone dry WAV")
    compensation.add_argument("rolled", type=Path, help="Same notes played with the tone pot rolled back")
    compensation.add_argument("output", type=Path, help="New WAV for the result")
    levels = sub.add_parser("levels", help="Measure dry/wet levels or plan relative post-chain preset trims")
    levels.add_argument("--take", type=Path, help="Measure this dry/wet take. Defaults to the latest take.")
    levels.add_argument("--comparison", type=Path, help="Plan trims from this saved comparison's raw renders")
    levels.add_argument("--reference", help="Comparison letter or raw render filename to use as the anchor")
    levels.add_argument("--target-lufs", type=float, help="Optional target loudness for the anchor")
    levels.add_argument("--ceiling-dbtp", type=float, default=-3.0, help="Estimated peak ceiling for proposed trims")
    levels.add_argument("--offset", action="append", default=[], metavar="CANDIDATE=DB",
                        help="Intentional relative lift or cut, e.g. B=2")
    stage = sub.add_parser("stage-preview", help="Make offline, level-matched EQ listening candidates")
    stage.add_argument("wet", type=Path, help="Processed guitar WAV")
    stage.add_argument("output", type=Path, help="New directory for the preview")
    stage.add_argument("--context", choices=("frfr", "cab", "mix"), default="frfr")
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
            capture.launch(args.input, workbench=args.workbench)
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
        case "test-compensation":
            from . import guitar_compensation

            if args.output.suffix.lower() != ".wav" or args.output.exists():
                raise ValueError("Choose a new WAV path for the compensation result.")
            output, report = guitar_compensation.compare(audio.read_wave(args.reference), audio.read_wave(args.rolled))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            audio.write_wave(args.output, output, pcm16=True)
            return {**report, "output": str(args.output.resolve())}
        case "levels":
            from . import level_plan

            if not args.comparison:
                if args.reference or args.target_lufs is not None or args.offset:
                    raise ValueError("Supply --comparison to plan relative trims.")
                return level_plan.take_report(args.take)
            if args.take:
                raise ValueError("Measure one take or plan levels for one comparison at a time.")
            offsets = {}
            for item in args.offset:
                key, sep, value = item.partition("=")
                if not sep or not key:
                    raise ValueError("Enter each offset as CANDIDATE=DB, for example B=2.")
                offsets[key] = float(value)
            return level_plan.comparison_plan(args.comparison, reference=args.reference, target_lufs=args.target_lufs,
                                              ceiling_dbtp=args.ceiling_dbtp, offsets=offsets)
        case "stage-preview":
            from . import stage_check

            return stage_check.create_preview(args.wet, args.output, context=args.context)
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
