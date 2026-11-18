"""Ask the local Codex sign-in for bounded knob suggestions."""

import json
import math
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from .styles import KNOBS


def ask(text, take, values, history=()):
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("The Codex command is unavailable on this PC.")
    schema = {"type": "object", "additionalProperties": False, "required": ["reply", "knobs"],
              "properties": {"reply": {"type": "string"}, "knobs": {"type": "object", "additionalProperties": False,
                              "required": list(KNOBS), "properties": {
                                  key: {"type": ["number", "null"]} for key in KNOBS}}}}
    context = {key: take[key] for key in ("guitar", "song", "wet_preset") if key in take}
    prompt = (
        "You are the guitar-tone assistant in Tone Lab. Reply in at most two short sentences. "
        "You receive text only and have not heard this guitar. Use no tools and read no files. "
        "If the user asks for a tone change, suggest absolute values for the relevant knobs. "
        "Use null for each knob you would leave alone. If asked a question, answer it and leave all knobs null. "
        "Jazz is 0 to 1 and reduces distortion, softens treble, adds mids and reduces ambience. "
        "Drive, warmth and space are -1 to 1, where zero means the original preset. "
        "Positive drive increases saturation; positive warmth reduces bright upper frequencies; "
        "positive space increases delay/reverb mix and delay feedback. The user decides whether to apply changes.\n"
        + json.dumps({"take": context, "current_knobs": values, "previous_messages": list(history)[-4:],
                      "request": text}, ensure_ascii=False)
    )
    with TemporaryDirectory(prefix="tone-advisor-") as directory:
        root = Path(directory)
        schema_file = root / "schema.json"
        result_file = root / "reply.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        try:
            command = subprocess.run(
                [executable, "-a", "never", "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                 "--disable", "shell_tool", "-c", 'web_search="disabled"', "-c", 'model_reasoning_effort="low"',
                 "--sandbox", "read-only", "--output-schema", str(schema_file), "-o", str(result_file), "-"],
                input=prompt, cwd=root, capture_output=True, encoding="utf-8", timeout=50,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("The AI reply timed out. Try again in a moment.") from error
        if command.returncode or not result_file.is_file():
            raise RuntimeError("Codex did not return a reply. Check the existing Codex sign-in and try again.")
        result = json.loads(result_file.read_text(encoding="utf-8"))
    suggestions = {}
    for key, (_, minimum, _) in KNOBS.items():
        value = result["knobs"].get(key)
        if (type(value) in (float, int) and math.isfinite(value) and minimum <= value <= 1):
            suggestions[key] = round(value, 2)
    return result["reply"].strip(), suggestions


def speak(text):
    import pythoncom
    from win32com.client import Dispatch

    pythoncom.CoInitialize()
    try:
        Dispatch("SAPI.SpVoice").Speak(text)
    finally:
        pythoncom.CoUninitialize()
