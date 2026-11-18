"""Ask the local Codex sign-in for bounded knob suggestions."""

from functools import lru_cache
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from . import styles


@lru_cache(maxsize=1)
def codex_executable():
    override = os.environ.get("TONE_LAB_CODEX")
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise RuntimeError("TONE_LAB_CODEX points to a file that does not exist.")
    executable = shutil.which("codex.exe") or shutil.which("codex")
    if executable:
        return executable
    # The desktop shortcut does not inherit the Codex terminal's PATH.
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
              "Get-AppxPackage -Name OpenAI.Codex | Sort-Object Version -Descending | "
              "ForEach-Object { $_.InstallLocation }")
    try:
        packages = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                                  capture_output=True, encoding="utf-8-sig", errors="replace", timeout=8,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        packages = None
    if packages and packages.returncode == 0:
        for folder in packages.stdout.splitlines():
            path = Path(folder.strip()) / "app/resources/codex.exe"
            if path.is_file():
                return str(path)
    raise RuntimeError("Could not find Codex. Install the Windows Codex app or set TONE_LAB_CODEX to codex.exe.")


def ask(text, take, values, history=()):
    schema = {"type": "object", "additionalProperties": False, "required": ["reply", "knobs"],
              "properties": {"reply": {"type": "string"}, "knobs": {"type": "object", "additionalProperties": False,
                              "required": list(styles.KNOBS), "properties": {
                                  key: {"type": ["number", "null"]} for key in styles.KNOBS}}}}
    context = {key: take[key] for key in ("guitar", "song", "wet_preset") if key in take}
    hints = getattr(styles, "KNOB_HINTS", {})
    directions = {name: {"label": label, "minimum": minimum, "maximum": 1, "positive": hints.get(name, "")}
                  for name, (label, minimum, _) in styles.KNOBS.items()}
    prompt = (
        "You are the guitar-tone assistant in Tone Lab. Reply in at most two short sentences. "
        "You receive text only and have not heard this guitar. Use no tools and read no files. "
        "If the user asks for a tone change, suggest absolute values for the relevant knobs. "
        "Use null for each knob you would leave alone. Zero is the preset baseline. If asked a question, answer it and leave all knobs null. "
        "Jazz is 0 to 1 and reduces distortion, softens treble, adds mids and reduces ambience. "
        "Drive, warmth and space are -1 to 1, where zero means the original preset. "
        "Positive drive increases saturation; positive warmth reduces bright upper frequencies; "
        "positive space increases delay/reverb mix and delay feedback. The user decides whether to apply changes.\n"
        + json.dumps({"take": context, "directions": directions, "current_knobs": values, "previous_messages": list(history)[-4:],
                      "request": text}, ensure_ascii=False)
    )
    result = request(prompt, schema, web=False)
    suggestions = {}
    for key, (_, minimum, _) in styles.KNOBS.items():
        value = result["knobs"].get(key)
        if (type(value) in (float, int) and math.isfinite(value) and minimum <= value <= 1):
            suggestions[key] = round(value, 2)
    return result["reply"].strip(), suggestions


def research(text, take):
    source = {"type": "object", "additionalProperties": False, "required": ["title", "url"],
              "properties": {"title": {"type": "string"}, "url": {"type": "string"}}}
    schema = {"type": "object", "additionalProperties": False, "required": ["reply", "sources"],
              "properties": {"reply": {"type": "string"}, "sources": {"type": "array", "items": source}}}
    context = {key: take[key] for key in ("guitar", "song", "wet_preset") if key in take}
    prompt = ("Research this guitarist's tone question using live web search. Prefer the artist, session participants, "
              "and official gear documentation. Distinguish the exact recorded passage, live rigs, and practical suggestions. "
              "Give a concise recipe or explanation. Cite only pages actually found, using canonical HTTPS URLs. "
              "If the exact gear is undocumented, say what is inferred. You have no audio. Do not access local files.\n"
              + json.dumps({"request": text, "take": context}, ensure_ascii=False))
    result = request(prompt, schema, web=True)
    return result["reply"].strip(), source_links(result["sources"])


def source_links(items):
    links = []
    for item in items[:6]:
        try:
            url = urlsplit(item["url"])
            title = item["title"].strip()
        except (KeyError, TypeError, AttributeError, ValueError):
            continue
        if url.scheme == "https" and url.hostname and not url.username and title:
            links.append({"title": title, "url": url.geturl()})
    return links


def request(prompt, schema, *, web):
    executable = codex_executable()
    search = "live" if web else "disabled"
    with TemporaryDirectory(prefix="tone-advisor-") as directory:
        root = Path(directory)
        schema_file = root / "schema.json"
        result_file = root / "reply.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        try:
            command = subprocess.run(
                [executable, "-a", "never", "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                 "--disable", "shell_tool", "-c", f'web_search="{search}"', "-c", 'model_reasoning_effort="low"',
                 "--sandbox", "read-only", "--output-schema", str(schema_file), "-o", str(result_file), "-"],
                input=prompt, cwd=root, capture_output=True, encoding="utf-8", timeout=85 if web else 50,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("The AI reply timed out. Try again in a moment.") from error
        if command.returncode or not result_file.is_file():
            raise RuntimeError("Codex did not return a reply. Check the existing Codex sign-in and try again.")
        return json.loads(result_file.read_text(encoding="utf-8"))


def speak(text):
    import pythoncom
    from win32com.client import Dispatch

    pythoncom.CoInitialize()
    try:
        Dispatch("SAPI.SpVoice").Speak(text)
    finally:
        pythoncom.CoUninitialize()
