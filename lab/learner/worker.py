"""Live listening in a process of its own, so that a failure of the audio driver cannot hold the window.

`run` is called on the desk's worker thread. It starts
`python -m lab.learner.worker <config.json>`, sends it the window's commands
(a new profile, a request to keep a clip, stop) as JSON lines, and passes the
events it prints back to `report` until it finishes.
"""

import faulthandler
import json
import os
from pathlib import Path
from queue import Empty, Queue, SimpleQueue
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time

from ..knowledge import REPO
from . import folders
from .folders import LOG

START_TIMEOUT = 18.0
HEARTBEAT_TIMEOUT = 6.0
STOP_TIMEOUT = 6.0
COMMANDS = ("learner-profile", "learner-snapshot")


def worker_command():
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return [str(executable), "-m", "lab.learner.worker"]


def run(config, stop, commands, report):
    """Listen live with `config` until `stop` is set. Returns what the worker reports when it finishes."""
    with TemporaryDirectory(prefix="tone-learner-") as folder:
        path = Path(folder) / "session.json"
        path.write_text(json.dumps(config, ensure_ascii=True), encoding="utf-8")
        return supervise(worker_command() + [str(path)], stop, commands, report)


def supervise(argv, stop, commands, report):
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8", bufsize=1, cwd=str(REPO),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
                # Only the newest profile, and each requested clip once.
                updates = {}
                while True:
                    try:
                        event = commands.get_nowait()
                    except Empty:
                        break
                    key = (event["event"], event.get("request_id")) if event["event"] == "learner-snapshot" else event["event"]
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
            if event["event"] in ("status", "heartbeat", "learner-ready"):
                deadline = time.monotonic() + HEARTBEAT_TIMEOUT
            if event["event"] == "status":
                report(event["text"])
            elif event["event"].startswith("learner-"):
                report(event)
            elif event["event"] == "error":
                raise RuntimeError(event["text"])
            elif event["event"] == "done":
                finished = True
                return event["audio"]
    except Exception:
        LOG.exception("The learner's live worker failed")
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
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


# ---- in the worker process ----

def emit(event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


def read_commands(stop, commands):
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
                if kind in COMMANDS:
                    commands.put(event)
    except (OSError, ValueError):
        LOG.warning("Live worker command pipe ended", exc_info=True)
    finally:
        stop.set()


def main(arguments):
    from . import live

    if len(arguments) != 1:
        emit({"event": "error", "text": "Give the live listening settings."})
        return 2
    log = folders.crash_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as crash_log:
        faulthandler.enable(file=crash_log)
        try:
            LOG.info("Learner live worker started")
            stop, commands = Event(), Queue()
            Thread(target=read_commands, args=(stop, commands), daemon=True).start()

            def events(event):
                if event["event"] in ("heartbeat", "learner-ready"):
                    # A driver that hangs leaves its stack in the log.
                    faulthandler.dump_traceback_later(4.5, file=crash_log)
                emit(event)

            config = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
            result = live.run(config, stop, commands, events)
            LOG.info("Learner live worker stopped %s", result)
            emit({"event": "done", "audio": result})
            return 0
        except Exception as error:  # noqa: BLE001
            LOG.exception("Learner live worker failed")
            emit({"event": "error", "text": str(error)})
            return 1
        finally:
            faulthandler.cancel_dump_traceback_later()
            faulthandler.disable()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
