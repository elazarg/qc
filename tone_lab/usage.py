"""A local record of which desk views are used together, so the layout can follow the player."""

from collections import Counter
from datetime import datetime, timezone
import json

from . import diagnostics, session

LOG = "ui-usage.jsonl"


def record(event, view, **situation):
    """Append one view event with the listening situation it happened in."""
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, "view": view,
           **situation}
    try:
        session.SESSIONS.mkdir(parents=True, exist_ok=True)
        with (session.SESSIONS / LOG).open("a", encoding="utf-8") as log:
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        diagnostics.LOG.warning("Could not record desk usage", exc_info=True)


def companions(path=None):
    """Count how often each set of views was open together, by situation."""
    counts = Counter()
    try:
        lines = (path or session.SESSIONS / LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return counts
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("event") in ("open", "pin") and len(row.get("open", ())) > 1:
            situation = "reference" if row.get("has_reference") else "live" if row.get("live") else "idle"
            counts[(situation, tuple(sorted(row["open"])))] += 1
    return counts
