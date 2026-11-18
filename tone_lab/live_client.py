"""Supervise the live worker without letting the audio driver hold the UI."""

import json
from pathlib import Path
from queue import Empty, SimpleQueue
import subprocess
import sys
from threading import Thread
import time

from . import diagnostics

START_TIMEOUT = 18.0
HEARTBEAT_TIMEOUT = 6.0
STOP_TIMEOUT = 6.0


def worker_command():
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return [str(executable), "-m", "tone_lab.live_worker"]


def run(take, mode, stop, commands, report, *, audible=True, guitar_config=None):
    argv = worker_command() + ["--mode", mode]
    if take is not None:
        argv += ["--take", str(take)]
    if not audible:
        argv.append("--muted")
    if mode == "guitar" and guitar_config:
        argv += ["--guitar-config", json.dumps(guitar_config, ensure_ascii=True)]
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8", bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
    incoming, outgoing = SimpleQueue(), SimpleQueue()

    def receive():
        try:
            for line in process.stdout:
                incoming.put(json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            incoming.put(None)

    def send():
        while (message := outgoing.get()) is not None:
            try:
                print(json.dumps(message), file=process.stdin, flush=True)
            except (OSError, ValueError):
                break

    Thread(target=receive, daemon=True).start()
    writer = Thread(target=send, daemon=True)
    writer.start()
    deadline = time.monotonic() + START_TIMEOUT
    stop_deadline = None
    finished = False
    try:
        while True:
            if stop.is_set() and stop_deadline is None:
                outgoing.put({"event": "stop"})
                stop_deadline = time.monotonic() + STOP_TIMEOUT
            if stop_deadline is None:
                values = None
                while True:
                    try:
                        values = commands.get_nowait()
                    except Empty:
                        break
                if values is not None:
                    outgoing.put({"event": "knobs", "values": values})
            try:
                event = incoming.get(timeout=.05)
            except Empty:
                if time.monotonic() > (stop_deadline or deadline):
                    raise RuntimeError("The live audio worker stopped responding.")
                continue
            if event is None:
                raise RuntimeError("The live audio worker exited unexpectedly.")
            if event["event"] in ("status", "heartbeat"):
                deadline = time.monotonic() + HEARTBEAT_TIMEOUT
            if event["event"] == "status":
                report(event["text"])
            elif event["event"] in ("controls", "guitar-state", "guitar-gesture", "guitar-status"):
                report(event)
            elif event["event"] == "error":
                raise RuntimeError(event["text"])
            elif event["event"] == "done":
                finished = True
                return event["audio"]
    except Exception as error:
        diagnostics.LOG.exception("Live worker supervising process caught a failure")
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        try:
            recovery = subprocess.run(worker_command() + ["--recover"], capture_output=True, encoding="utf-8",
                                      timeout=12, creationflags=subprocess.CREATE_NO_WINDOW)
            if recovery.returncode:
                raise RuntimeError("Could not verify preset restoration.")
        except (subprocess.TimeoutExpired, RuntimeError) as restore_error:
            raise RuntimeError(f"{error} Check the currently selected preset on the Quad Cortex.") from restore_error
        raise
    finally:
        outgoing.put(None)
        if finished:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        writer.join(timeout=.5)
        process.stdout.close()
        if not writer.is_alive():
            process.stdin.close()
