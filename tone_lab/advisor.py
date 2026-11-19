"""Ask the local Codex sign-in for bounded knob suggestions."""

from functools import lru_cache
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
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


class Cancelled(RuntimeError):
    """The player withdrew the request before the reply arrived."""


def ask(text, take, values, history=(), *, available=None):
    reply, suggestions, _ = _consult(text, take, values, history, available, classify=False)
    return reply, suggestions


def read(text, take, values, history=(), *, available=None, heard=None, cancel=None, knowledge=None):
    """Answer like `ask`, and also say whether the player wants a different sound altogether.

    `heard` carries measurements of the captured tone, when there are any.
    `knowledge` describes the loaded gear and how players treat the words used.
    """
    reply, suggestions, intent = _consult(text, take, values, history, available, classify=True,
                                          heard=heard, cancel=cancel, knowledge=knowledge)
    return {"reply": reply, "knobs": suggestions, "intent": intent}


def _consult(text, take, values, history, available, *, classify, heard=None, cancel=None, knowledge=None):
    supported = set(styles.KNOBS if available is None else available)
    schema = {"type": "object", "additionalProperties": False, "required": ["reply", "knobs"],
              "properties": {"reply": {"type": "string"}, "knobs": {"type": "object", "additionalProperties": False,
                              "required": list(styles.KNOBS), "properties": {
                                  key: {"type": ["number", "null"]} for key in styles.KNOBS}}}}
    if classify:
        schema["required"].append("intent")
        schema["properties"]["intent"] = {"type": "string", "enum": ["adjust", "answer", "new_sound"]}
    context = {key: take[key] for key in ("guitar", "pickup_note", "song", "wet_preset", "active_tone") if key in take}
    hints = getattr(styles, "KNOB_HINTS", {})
    directions = {name: {"label": label, "minimum": minimum, "maximum": 1, "positive": hints.get(name, "")}
                  for name, (label, minimum, _) in styles.KNOBS.items() if name in supported}
    prompt = (
        "You are the guitar-tone assistant in Tone Lab. Reply in at most two short sentences. "
        + ("You have not heard this guitar. Measurements of the captured tone are supplied as measurements: "
           "spectrum_db lists the selected tone minus the other sound in each band of bands_hz, on the same "
           "phrase when same_performance is true. Let them decide how far to move, and mention the one that "
           "mattered. They describe the tone as captured_with_knobs, which may differ from current_knobs. "
           if heard else "You receive text only and have not heard this guitar. ")
        + ("knowledge describes the gear in the selected chain, including controls that run backwards, and "
           "lists what players usually try for the words in the request, in the order they try it. Prefer the "
           "earliest of those that the listed directions can perform, and say in a few words why it works. "
           if knowledge else "")
        + "Use no tools and read no files. "
        "If the user asks for a tone change, suggest absolute values for the relevant knobs. "
        "Only propose knobs listed in directions. Each listed knob can change the selected chain. "
        "If the request needs a device that the chain lacks, explain the missing stage. "
        "Use null for each knob you would leave alone. Zero is the preset baseline. If asked a question, answer it and leave all knobs null. "
        "Jazz is 0 to 1 and reduces distortion, softens treble, adds mids and reduces ambience. "
        "Drive, warmth and space are -1 to 1, where zero means the original preset. "
        "Positive drive increases saturation; positive warmth reduces bright upper frequencies; "
        "positive space increases delay/reverb mix and delay feedback. The user decides whether to apply changes.\n"
        + ("Also classify the request as intent. Use adjust for a change to the sound described in the take, answer "
           "for a question, and new_sound only when the player names an artist, song, genre or rig, or otherwise asks "
           "for a different sound to be built. For new_sound leave all knobs null and say in one short sentence what "
           "you would build. An observation about the current sound is always adjust.\n" if classify else "")
        + json.dumps({"take": context, "directions": directions, "current_knobs": values, "previous_messages": list(history)[-4:],
                      **({"measurements": heard} if heard else {}),
                      **({"knowledge": knowledge} if knowledge else {}), "request": text}, ensure_ascii=False)
    )
    result = request(prompt, schema, web=False, **({"cancel": cancel} if cancel is not None else {}))
    suggestions = {}
    for key, (_, minimum, _) in styles.KNOBS.items():
        if key not in supported:
            continue
        value = result["knobs"].get(key)
        if (type(value) in (float, int) and math.isfinite(value) and minimum <= value <= 1):
            suggestions[key] = round(value, 2)
    intent = result.get("intent") if classify else None
    return result["reply"].strip(), suggestions, intent if intent in ("adjust", "answer", "new_sound") else "adjust"


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


def request(prompt, schema, *, web, cancel=None):
    """Run one Codex exchange. Setting `cancel` ends the process and raises Cancelled."""
    executable = codex_executable()
    search = "live" if web else "disabled"
    with TemporaryDirectory(prefix="tone-advisor-") as directory:
        root = Path(directory)
        schema_file = root / "schema.json"
        result_file = root / "reply.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        command = subprocess.Popen(
            [executable, "-a", "never", "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
             "--disable", "shell_tool", "-c", f'web_search="{search}"', "-c", 'model_reasoning_effort="low"',
             "--sandbox", "read-only", "--output-schema", str(schema_file), "-o", str(result_file), "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=root, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + (85 if web else 50)
        pending = prompt
        try:
            while True:
                try:
                    command.communicate(pending, timeout=.25)
                    break
                except subprocess.TimeoutExpired:
                    pending = None
                    if cancel is not None and cancel.is_set():
                        raise Cancelled("The request was cancelled.") from None
                    if time.monotonic() > deadline:
                        raise RuntimeError("The AI reply timed out. Try again in a moment.") from None
        finally:
            if command.poll() is None:
                command.kill()
                command.communicate()
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
