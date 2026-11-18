"""Supervise the live worker without letting the audio driver hold the UI."""

import json
from pathlib import Path
from queue import Empty, SimpleQueue
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time

from . import diagnostics

START_TIMEOUT = 18.0
HEARTBEAT_TIMEOUT = 6.0
STOP_TIMEOUT = 6.0
TONE_LOAD_TIMEOUT = 45.0


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
    return supervise(argv, stop, commands, report)


def run_monitor(config, stop, commands, report):
    with TemporaryDirectory(prefix="tone-monitor-") as folder:
        path = Path(folder) / "session.json"
        path.write_text(json.dumps(config, ensure_ascii=True), encoding="utf-8")
        return supervise(worker_command() + ["--monitor", str(path)], stop, commands, report, monitor=True)


def supervise(argv, stop, commands, report, *, monitor=False):
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
    loading_tone = False
    finished = False
    try:
        while True:
            if stop.is_set() and stop_deadline is None:
                outgoing.put({"event": "stop"})
                stop_deadline = time.monotonic() + (TONE_LOAD_TIMEOUT if loading_tone else STOP_TIMEOUT)
            if stop_deadline is None:
                updates = {}
                while True:
                    try:
                        item = commands.get_nowait()
                    except Empty:
                        break
                    event = item if monitor else {"event": "knobs", "values": item}
                    if event["event"] == "tone":
                        updates.pop(("knobs", event["config"].get("tone_id")), None)
                    key = (event["event"], event.get("tone_id")) if event["event"] == "knobs" else event["event"]
                    updates.pop(key, None)
                    updates[key] = event
                for event in updates.values():
                    outgoing.put(event)
            try:
                event = incoming.get(timeout=.05)
            except Empty:
                if time.monotonic() > (stop_deadline or deadline):
                    raise RuntimeError("The live audio worker stopped responding.")
                continue
            if event is None:
                raise RuntimeError("The live audio worker exited unexpectedly.")
            if event["event"] == "loading-tone":
                loading_tone = True
                deadline = time.monotonic() + TONE_LOAD_TIMEOUT
                if stop_deadline is not None:
                    stop_deadline = deadline
            elif event["event"] in ("status", "heartbeat", "source", "tone"):
                if event["event"] in ("source", "tone"):
                    loading_tone = False
                deadline = time.monotonic() + HEARTBEAT_TIMEOUT
            if event["event"] == "status":
                report(event["text"])
            elif event["event"] in ("controls", "source", "tone", "loading-tone", "shape",
                                     "guitar-state", "guitar-gesture", "guitar-status", "guitar-stopped"):
                report(event)
            elif monitor and event["event"] == "heartbeat":
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
