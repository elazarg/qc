"""Every call to the assistant goes through `ask`, which keeps a ledger and refuses calls nobody stands behind.

The assistant is the player's own Codex sign-in, run as `codex exec` with no
shell, a read-only sandbox, a working folder that holds only the schema and
the reply, and a JSON schema the reply must follow. Web search is on only when the call asks
for it.

Each call is written to `ledger.jsonl`: when (local time), what kind, whether
the player asked or the app did it on its own, whether the web was used, how
large the request was, how long it took and how it ended. A call must say who
stands behind it. Automatic calls are allowed only while `automatic` is on,
and no more than `DAILY` calls of any kind are made in one day of the
player's own clock.

`ask` is the only function that sends a request to Codex. Tests replace it,
or point `TONE_LAB_CODEX` at a stand-in program. (`lab.setup` also runs the
program, to ask whether it is signed in; that sends nothing.)
"""

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import threading
import time

DAILY = 80
# Calls begun and not yet in the ledger, per ledger: counted against the allowance from the moment they start, so two
# calls started together cannot both take the last place. `_UNWRITTEN` holds the lines a ledger could not take
# (the file was locked or the disk full): they still count, and are written with the next call.
_IN_FLIGHT, _UNWRITTEN, _LOCK = {}, {}, threading.Lock()
_FOUND = []                 # where the Codex program was last found, so that it is looked for once
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Cancelled(RuntimeError):
    """The request was withdrawn before the reply arrived."""


class Refused(RuntimeError):
    """The call was not made: nobody asked for it, or today's allowance is spent."""


def codex():
    """The Codex program. Looking for it can take a second (a desktop shortcut does not inherit the Codex
    terminal's PATH, so the installed package is asked for), so where it was found is remembered."""
    override = os.environ.get("TONE_LAB_CODEX")
    if override:
        if Path(override).is_file():
            return str(Path(override).resolve())
        raise RuntimeError("TONE_LAB_CODEX points to a file that does not exist.")
    if _FOUND and Path(_FOUND[0]).is_file():
        return _FOUND[0]
    found = shutil.which("codex.exe") or shutil.which("codex") or _packaged()
    if not found:
        raise RuntimeError("Could not find Codex. Install the Codex app or set TONE_LAB_CODEX to codex.exe.")
    _FOUND[:] = [found]
    return found


def _packaged():
    """Codex installed as a Windows package, or None."""
    shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-AppxPackage -Name OpenAI.Codex | "
              "Sort-Object Version -Descending | ForEach-Object { $_.InstallLocation }")
    try:
        found = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                               encoding="utf-8-sig", errors="replace", timeout=8, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for folder in (found.stdout.splitlines() if found.returncode == 0 else ()):
        path = Path(folder.strip()) / "app/resources/codex.exe"
        if path.is_file():
            return str(path)
    return None


def local_day(at):
    """The day, on the player's own clock, of a ledger entry's time ("2026-11-21"), or None when it is not a time.
    Entries written before the ledger kept local time are in UTC with their offset, and are read the same way."""
    try:
        moment = datetime.fromisoformat(str(at))
    except ValueError:
        return None
    return (moment.astimezone() if moment.tzinfo else moment).date().isoformat()


def _stop(command):
    """End the program and everything it started. Codex installed with npm is a script that starts the real
    program: ending only the script would leave the request running, and paid for, to its end."""
    if command.poll() is None:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(command.pid)], capture_output=True, timeout=10,
                               creationflags=NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if command.poll() is None:
            command.kill()
    try:
        command.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _send(pipe, request):
    """Write the request to the program and close its input. A program that ended first is not a fault here."""
    try:
        pipe.write(request)
    except OSError:
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _said(errors):
    """The line of the program's error output worth showing: the first that says "error", or else the last."""
    lines = [line.strip() for line in (errors or "").splitlines() if line.strip()]
    return next((line for line in lines if "error" in line.casefold()), lines[-1] if lines else "no reply")[:200]


class Assistant:
    def __init__(self, folder, *, automatic=True, run=None):
        self.ledger = Path(folder) / "ledger.jsonl"
        self.automatic = automatic
        self.run = run or self._codex     # replaced in tests

    def spent_today(self):
        """Calls made today by the player's own clock. A line that cannot be read is not a call."""
        today = datetime.now().astimezone().date().isoformat()
        try:
            lines = self.ledger.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        spent = 0
        for line in lines + _UNWRITTEN.get(str(self.ledger), []):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            spent += isinstance(entry, dict) and local_day(entry.get("at")) == today
        return spent

    def ask(self, kind, prompt, schema, *, by, web=False, seconds=90, cancel=None):
        """One call. `by` is "player" when the player's own action asked for it, "app" when the app chose to."""
        if by not in ("player", "app"):
            raise ValueError("Say who stands behind the call.")
        if by == "app" and not self.automatic:
            raise Refused("Automatic calls to the assistant are off.")
        key = str(self.ledger)
        with _LOCK:
            if self.spent_today() + _IN_FLIGHT.get(key, 0) >= DAILY:
                raise Refused(f"Today's {DAILY} calls to the assistant are spent.")
            _IN_FLIGHT[key] = _IN_FLIGHT.get(key, 0) + 1
        started, outcome = time.monotonic(), "ok"
        try:
            return self.run(prompt, schema, web=web, seconds=seconds, cancel=cancel)
        except Cancelled:
            outcome = "cancelled"
            raise
        except Exception as error:
            outcome = f"failed: {error}"[:200]
            raise
        finally:
            line = json.dumps({"at": datetime.now().astimezone().isoformat(timespec="seconds"), "kind": kind,
                               "by": by, "web": web, "characters": len(prompt),
                               "seconds": round(time.monotonic() - started, 1), "outcome": outcome})
            with _LOCK:
                waiting = _UNWRITTEN.setdefault(key, [])
                waiting.append(line)
                try:
                    # A ledger that cannot be written must not cost the player the reply: the lines wait.
                    self.ledger.parent.mkdir(parents=True, exist_ok=True)
                    with self.ledger.open("a", encoding="utf-8") as ledger:
                        ledger.write("".join(each + "\n" for each in waiting))
                    waiting.clear()
                except OSError:
                    pass
                finally:
                    _IN_FLIGHT[key] -= 1

    @staticmethod
    def _codex(prompt, schema, *, web, seconds, cancel):
        executable = codex()
        with TemporaryDirectory(prefix="tone-assistant-", ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            (root / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
            # The error output goes to a file and the request is sent from a thread of its own, so that nothing
            # here waits on a pipe: a program that never reads, or one that leaves a child holding its output,
            # cannot hold the app past the time allowed or past Cancel. (A file cannot stand in for the request:
            # the launcher script of an npm install reads it away before the program sees it.)
            with (root / "errors.txt").open("wb") as errors:
                command = subprocess.Popen(
                    [executable, "-a", "never", "exec", "--skip-git-repo-check", "--ephemeral",
                     "--ignore-user-config", "--disable", "shell_tool",
                     "-c", f'web_search="{"live" if web else "disabled"}"',
                     "-c", 'model_reasoning_effort="low"', "--sandbox", "read-only",
                     "--output-schema", str(root / "schema.json"), "-o", str(root / "reply.json"), "-"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors, cwd=root,
                    creationflags=NO_WINDOW)
                threading.Thread(target=_send, args=(command.stdin, prompt.encode("utf-8")), daemon=True).start()
                deadline = time.monotonic() + seconds
                try:
                    while True:
                        try:
                            command.wait(timeout=.25)
                            break
                        except subprocess.TimeoutExpired:
                            if cancel is not None and cancel.is_set():
                                raise Cancelled("The request was withdrawn.") from None
                            if time.monotonic() > deadline:
                                raise RuntimeError("The assistant did not answer in time.") from None
                finally:
                    _stop(command)
            reply = root / "reply.json"
            if command.returncode or not reply.is_file():
                said = _said((root / "errors.txt").read_text(encoding="utf-8", errors="replace"))
                raise RuntimeError(f"The assistant did not reply: {said}")
            try:
                answer = json.loads(reply.read_text(encoding="utf-8", errors="replace"))
            except ValueError:
                raise RuntimeError("The assistant's reply was not JSON.") from None
            if not isinstance(answer, dict):
                raise RuntimeError("The assistant's reply was not a JSON object.")
            return answer
