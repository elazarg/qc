"""Feedback attaches to a specific audition without modifying the recorded take."""

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from . import session


def comparisons(take_id):
    for path in sorted(session.SESSIONS.glob("*/session.json"), reverse=True):
        try:
            info = session.load_session(path.parent)
        except (OSError, ValueError):
            continue
        if info.get("source_clip") == take_id and info.get("state") == "ready" and info.get("auditions"):
            yield path.parent, info


def notes(folder):
    folder = Path(folder)
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((folder / "feedback").glob("*.json"))]


def save(folder, text, *, audition=None, audition_name=None):
    folder = Path(folder)
    description = session.load_session(folder)
    text = text.strip()
    if not text or not description.get("auditions"):
        raise ValueError("Enter feedback for a completed comparison.")
    now = datetime.now(timezone.utc)
    directory = folder / "feedback"
    directory.mkdir(exist_ok=True)
    path = directory / f"{now:%Y%m%d-%H%M%S}-{uuid4().hex[:6]}.json"
    heard = description["auditions"].get(audition, {})
    session.write_json(path, {"created_utc": now.isoformat(), "comparison": folder.name,
                             "source_clip": description.get("source_clip"), "text": text,
                             "audition": audition, "audition_name": audition_name, "heard_render": heard.get("render"),
                             "auditions": {label: row["render"] for label, row in description["auditions"].items()}})
    return path
