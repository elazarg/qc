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
    with device.connect(write=True) as qc:
        if qc.get_setlist_position() == iterate.SCRATCH:
            iterate.recall(qc, target["position"], target["name"])
            if device.active_scene(qc) != target["scene"]:
                device.select_scene(qc, target["scene"])
            diagnostics.LOG.info("Recovered original preset after live worker failure: %s", target["name"])
    RECOVERY.unlink(missing_ok=True)


def read_commands(stop, commands):
    pending = b""
    try:
        # A raw pipe read avoids holding Python's buffered-stdin lock at shutdown.
        while data := os.read(sys.stdin.fileno(), 4096):
            *lines, pending = (pending + data).split(b"\n")
            for line in lines:
                event = json.loads(line)
                if event.get("event") == "stop":
                    return
                if event.get("event") == "knobs":
                    commands.put(event["values"])
    except (OSError, ValueError):
        diagnostics.LOG.warning("Live worker command pipe ended", exc_info=True)
    finally:
        stop.set()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--take", type=Path)
    parser.add_argument("--mode", choices=("guitar", "reamp"))
    parser.add_argument("--muted", action="store_true")
    parser.add_argument("--capture-return", action="store_true")
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    diagnostics.configure()
    with (session.SESSIONS / "live-driver.log").open("a", encoding="utf-8") as crash_log:
        faulthandler.enable(file=crash_log)
        try:
            if args.recover:
                recover()
                return 0
            if args.take is None or args.mode is None:
                raise ValueError("A take and live mode are required.")
            diagnostics.LOG.info("Live worker started mode=%s muted=%s", args.mode, args.muted)
            stop, commands = Event(), Queue()
            Thread(target=read_commands, args=(stop, commands), daemon=True).start()

            def events(event):
                if event["event"] == "restore":
                    session.write_json(RECOVERY, event)
                if event["event"] in ("restore", "heartbeat"):
                    faulthandler.dump_traceback_later(4.5, file=crash_log)
                emit(event)

            result = live.run(args.take, args.mode, stop, commands, lambda text: emit({"event": "status", "text": text}),
                              audible=not args.muted, capture_return=args.capture_return, events=events)
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
