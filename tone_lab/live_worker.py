"""Process boundary around the native Quad Cortex drivers."""

import argparse
import faulthandler
import json
import os
from pathlib import Path
from queue import Queue
import sys
from threading import Event, Thread

from . import device, diagnostics, iterate, live, session

RECOVERY = session.SESSIONS / "live-recovery.json"


def emit(event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


def recover():
    if not RECOVERY.is_file():
        return
    target = json.loads(RECOVERY.read_text(encoding="utf-8"))
    fixtures = target.get("fixtures", [target.get("fixture", iterate.SCRATCH)])
    with device.connect(write=True) as qc:
        if qc.get_setlist_position() in fixtures:
            iterate.recall(qc, target["position"], target["name"])
            if device.active_scene(qc) != target["scene"]:
                device.select_scene(qc, target["scene"])
            diagnostics.LOG.info("Recovered original preset after live worker failure: %s", target["name"])
    RECOVERY.unlink(missing_ok=True)


def read_commands(stop, commands, monitor=False, learner=False):
    pending = b""
    try:
        # A raw pipe read avoids holding Python's buffered-stdin lock at shutdown.
        while data := os.read(sys.stdin.fileno(), 4096):
            *lines, pending = (pending + data).split(b"\n")
            for line in lines:
                event = json.loads(line)
                kind = event.get("event")
                if kind == "stop":
                    return
                if learner:
                    if kind in ("learner-profile", "learner-snapshot"):
                        commands.put(event)
                elif kind in ("knobs", "source", "tone", "level"):
                    if monitor:
                        commands.put(event)
                    elif event["event"] == "knobs":
                        commands.put(event["values"])
    except (OSError, ValueError):
        diagnostics.LOG.warning("Live worker command pipe ended", exc_info=True)
    finally:
        stop.set()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--take", type=Path)
    parser.add_argument("--mode", choices=("guitar", "reamp"))
    parser.add_argument("--monitor", type=Path)
    parser.add_argument("--learner", type=Path)
    parser.add_argument("--muted", action="store_true")
    parser.add_argument("--capture-return", action="store_true")
    parser.add_argument("--guitar-config", type=json.loads)
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    diagnostics.configure()
    with (session.SESSIONS / "live-driver.log").open("a", encoding="utf-8") as crash_log:
        faulthandler.enable(file=crash_log)
        try:
            if args.recover:
                recover()
                return 0
            if args.monitor is None and args.learner is None and (
                    args.mode is None or (args.mode == "reamp" and args.take is None)):
                raise ValueError("Choose a live mode and supply a dry take for reamping.")
            diagnostics.LOG.info("Live worker started mode=%s muted=%s", "learner" if args.learner else args.mode, args.muted)
            stop, commands = Event(), Queue()
            Thread(target=read_commands, args=(stop, commands, args.monitor is not None, args.learner is not None),
                   daemon=True).start()
            loading_tone = False

            def events(event):
                nonlocal loading_tone
                kind = event["event"]
                if kind == "restore":
                    session.write_json(RECOVERY, event)
                if kind == "loading-tone":
                    loading_tone = True
                elif kind in ("tone", "source"):
                    loading_tone = False
                if kind in ("restore", "heartbeat", "loading-tone", "tone", "source", "learner-ready"):
                    faulthandler.dump_traceback_later(30 if loading_tone else 4.5, file=crash_log)
                emit(event)

            if args.learner:
                from . import guitar_live

                config = json.loads(args.learner.read_text(encoding="utf-8"))
                result = guitar_live.run(config, stop, commands, events)
            elif args.monitor:
                from . import monitor

                config = json.loads(args.monitor.read_text(encoding="utf-8"))
                result = monitor.run(config, stop, commands, events)
            else:
                result = live.run(args.take, args.mode, stop, commands, lambda text: emit({"event": "status", "text": text}),
                                  audible=not args.muted, capture_return=args.capture_return,
                                  guitar_config=args.guitar_config, events=events)
            if not args.learner:
                RECOVERY.unlink(missing_ok=True)
            diagnostics.LOG.info("Live worker stopped %s", result)
            emit({"event": "done", "audio": result})
            return 0
        except Exception as error:
            diagnostics.LOG.exception("Live worker failed")
            emit({"event": "error", "text": str(error)})
            return 1
        finally:
            faulthandler.cancel_dump_traceback_later()
            faulthandler.disable()


if __name__ == "__main__":
    raise SystemExit(main())
