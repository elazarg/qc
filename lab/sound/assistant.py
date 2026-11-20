"""Every call to the assistant goes through `ask`, which keeps a ledger and refuses calls nobody stands behind.

The assistant is the player's own Codex sign-in, run as `codex exec` with no
shell, a read-only sandbox, an empty working folder, and a JSON schema the
reply must follow. Web search is on only when the call asks for it.

Each call is written to `ledger.jsonl`: when, what kind, whether the player
asked or the app did it on its own, whether the web was used, how large the
request was, how long it took and how it ended. A call must say who stands
behind it. Automatic calls are allowed only while `automatic` is on, and no
more than `DAILY` calls of any kind are made in one day.

`ask` is the only function that runs Codex. Tests replace it.
"""

from datetime import datetime, timezone
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
# calls started together cannot both take the last place.
_IN_FLIGHT, _LOCK = {}, threading.Lock()


class Cancelled(RuntimeError):
    """The request was withdrawn before the reply arrived."""


class Refused(RuntimeError):
    """The call was not made: nobody asked for it, or today's allowance is spent."""


def codex():
    override = os.environ.get("TONE_LAB_CODEX")
    if override:
        if Path(override).is_file():
            return str(Path(override).resolve())
        raise RuntimeError("TONE_LAB_CODEX points to a file that does not exist.")
    found = shutil.which("codex.exe") or shutil.which("codex")
    if found:
        return found
    # A desktop shortcut does not inherit the Codex terminal's PATH.
    shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-AppxPackage -Name OpenAI.Codex | "
              "Sort-Object Version -Descending | ForEach-Object { $_.InstallLocation }")
    try:
        found = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                               encoding="utf-8-sig", errors="replace", timeout=8,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        found = None
    for folder in (found.stdout.splitlines() if found and found.returncode == 0 else ()):
        path = Path(folder.strip()) / "app/resources/codex.exe"
        if path.is_file():
            return str(path)
    raise RuntimeError("Could not find Codex. Install the Codex app or set TONE_LAB_CODEX to codex.exe.")


class Assistant:
    def __init__(self, folder, *, automatic=True, run=None):
        self.ledger = Path(folder) / "ledger.jsonl"
        self.automatic = automatic
        self.run = run or self._codex     # replaced in tests

    def spent_today(self):
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            lines = self.ledger.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0
        return sum(1 for line in lines if line.startswith('{"at": "' + today))

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
            with _LOCK:
                try:
                    self.ledger.parent.mkdir(parents=True, exist_ok=True)
                    with self.ledger.open("a", encoding="utf-8") as ledger:
                        ledger.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                 "kind": kind, "by": by, "web": web, "characters": len(prompt),
                                                 "seconds": round(time.monotonic() - started, 1),
                                                 "outcome": outcome}) + "\n")
                finally:
                    _IN_FLIGHT[key] -= 1

    @staticmethod
    def _codex(prompt, schema, *, web, seconds, cancel):
        executable = codex()
        with TemporaryDirectory(prefix="tone-assistant-") as directory:
            root = Path(directory)
            (root / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
            command = subprocess.Popen(
                [executable, "-a", "never", "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                 "--disable", "shell_tool", "-c", f'web_search="{"live" if web else "disabled"}"',
                 "-c", 'model_reasoning_effort="low"', "--sandbox", "read-only",
                 "--output-schema", str(root / "schema.json"), "-o", str(root / "reply.json"), "-"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=root, encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline, pending, errors = time.monotonic() + seconds, prompt, ""
            try:
                while True:
                    try:
                        _, errors = command.communicate(pending, timeout=.25)
                        break
                    except subprocess.TimeoutExpired:
                        pending = None
                        if cancel is not None and cancel.is_set():
                            raise Cancelled("The request was withdrawn.") from None
                        if time.monotonic() > deadline:
                            raise RuntimeError("The assistant did not answer in time.") from None
            finally:
                if command.poll() is None:
                    command.kill()
                    command.communicate()
            reply = root / "reply.json"
            if command.returncode or not reply.is_file():
                last = (errors or "").strip().splitlines()[-1:] or ["no reply"]
                raise RuntimeError(f"The assistant did not reply: {last[0][:200]}")
            return json.loads(reply.read_text(encoding="utf-8"))
