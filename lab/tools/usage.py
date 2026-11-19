"""Usage and feedback: which tools are opened, what the assistant cost each day, and the player's notes.

- **Usage** is a local log of tool windows opened and closed (`ui-usage.jsonl` in the data folder), as the
  old desk kept for its panels, with `companions` counting what was open together.
- **The assistant's ledger** (`ledger.jsonl`, written by `lab.assistant`) is read back day by day: how many
  calls, whose (the player's or the app's own), of which kind, how long they took, how much was sent, how
  they ended, against the daily allowance.
- **Feedback** is a note the player leaves about what is on the desk: it is kept with the sounds on A and
  B, the phrase and the round it was said about, so it can be read against them later. Notes about one
  blind comparison stay with that comparison (see `workbench.py`).
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import tkinter as tk
from uuid import uuid4

from ..theme import FONT, INK, PAPER, SOFT, SUBTLE
from .common import Window, button, label, listbox, section

LOG = "ui-usage.jsonl"


def _now():
    return datetime.now(timezone.utc)


# ---- usage ----

def record(folder, event, view, **situation):
    """Append one view event. Never fails the window that records it."""
    row = {"at": _now().isoformat(timespec="seconds"), "event": event, "view": view, **situation}
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        with (Path(folder) / LOG).open("a", encoding="utf-8") as log:
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _rows(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            found.append(row)
    return found


def opens(folder):
    """How often each view was opened."""
    return Counter(row.get("view") for row in _rows(Path(folder) / LOG) if row.get("event") == "open")


def companions(folder):
    """How often each set of views was open together, by situation, from rows that name what was open."""
    counts = Counter()
    for row in _rows(Path(folder) / LOG):
        if row.get("event") in ("open", "pin") and len(row.get("open", ())) > 1:
            situation = "reference" if row.get("has_reference") else "live" if row.get("live") else "idle"
            counts[(situation, tuple(sorted(row["open"])))] += 1
    return counts


# ---- the assistant's ledger ----

def daily(folder, *, days=14, today=None):
    """The ledger by day, newest first, for the last `days` days that had calls.

    Each day: {"day", "calls", "player", "app", "web", "kinds": {kind: n}, "seconds", "characters",
    "ok", "cancelled", "failed", "refused"}. A call refused before it was made is not in the ledger.
    """
    from ..assistant import DAILY

    found = defaultdict(lambda: {"calls": 0, "player": 0, "app": 0, "web": 0, "kinds": Counter(), "seconds": 0.,
                                 "characters": 0, "ok": 0, "cancelled": 0, "failed": 0})
    for row in _rows(Path(folder) / "ledger.jsonl"):
        day = str(row.get("at", ""))[:10]
        if len(day) != 10:
            continue
        entry = found[day]
        entry["calls"] += 1
        entry["player" if row.get("by") == "player" else "app"] += 1
        entry["web"] += bool(row.get("web"))
        entry["kinds"][row.get("kind", "?")] += 1
        entry["seconds"] += float(row.get("seconds") or 0)
        entry["characters"] += int(row.get("characters") or 0)
        outcome = str(row.get("outcome", ""))
        entry["ok" if outcome == "ok" else "cancelled" if outcome == "cancelled" else "failed"] += 1
    today = today or _now().date().isoformat()
    result = []
    for day in sorted(found, reverse=True)[:days]:
        entry = found[day]
        result.append({"day": day, **entry, "kinds": dict(entry["kinds"]), "seconds": round(entry["seconds"], 1),
                       "limit": DAILY, "left": max(0, DAILY - entry["calls"]) if day == today else None})
    return result


def today_line(folder, today=None):
    from ..assistant import DAILY

    today = today or _now().date().isoformat()
    entry = next((row for row in daily(folder, today=today) if row["day"] == today), None)
    used = entry["calls"] if entry else 0
    return f"Today: {used} of {DAILY} calls to the assistant" + (
        f" ({entry['player']} yours, {entry['app']} the app's own)" if entry else "")


# ---- feedback ----

def situation(studio):
    """What is on the desk, as a note about it should remember it."""
    state = getattr(studio, "state", None)
    found = {}
    for side in ("a", "b"):
        sound = studio.sound(side) if state is not None else None
        if sound is not None:
            found[side] = {"id": sound.id, "name": sound.name, "digest": sound.chain.digest(),
                           "knobs": dict(sound.knobs)}
    phrase = getattr(studio, "phrase", None)
    return {"sounds": found, "phrase": phrase[0] if phrase else None,
            "round": getattr(state, "round", None), "song": getattr(state, "song", None),
            "instrument": getattr(state, "instrument", None)}


def save_feedback(folder, text, about=None):
    text = (text or "").strip()
    if not text:
        raise ValueError("Write the note first.")
    now = _now()
    directory = Path(folder) / "feedback"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{now:%Y%m%d-%H%M%S}-{uuid4().hex[:6]}.json"
    path.write_text(json.dumps({"at": now.isoformat(timespec="seconds"), "text": text, **(about or {})},
                               indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def feedback_notes(folder):
    found = []
    for path in sorted((Path(folder) / "feedback").glob("*.json"), reverse=True):
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return found


# ---- the unit's share ----

def unit_state(engine, *, stop=None):
    """What the app keeps on the unit: firmware, whether the round trip is measured, chains in the cache."""
    from ..slots import CACHE_SLOTS

    return {"firmware": engine.unit.firmware, "calibrated": engine.offset is not None,
            "cached": len(engine.slots.index()), "cache_slots": len(CACHE_SLOTS)}


# ---- the window ----

class UsageWindow(Window):
    TITLE = "Usage and feedback"
    SIZE = "620x620"

    def build(self):
        calls = section(self.body, "The assistant", "Calls by day, from the ledger every call is written to.")
        self.today = label(calls, size=10, bold=True)
        self.today.pack(fill="x")
        self.days = listbox(calls, height=7)
        self.days.pack(fill="x", pady=(6, 0))

        views = section(self.body, "Tools opened")
        self.views = label(views, size=9, color=SOFT, wrap=540)
        self.views.pack(fill="x")
        self.unit = label(views, size=9, color=SUBTLE, wrap=540)
        self.unit.pack(fill="x", pady=(4, 0))
        button(views, "What the app keeps on the Quad Cortex", self.check_unit, quiet=True).pack(anchor="w",
                                                                                                pady=(6, 0))

        notes = section(self.body, "Feedback", "A note about what is on A and B now. It is kept with them.")
        self.note = tk.Text(notes, height=3, bg=PAPER, fg=INK, insertbackground=INK, relief="flat",
                            font=(FONT, 10), wrap="word")
        self.note.pack(fill="x")
        button(notes, "Keep this note", self.keep).pack(anchor="w", pady=(6, 0))
        self.notes = listbox(notes, height=5)
        self.notes.pack(fill="x", pady=(6, 0))
        self.refresh()

    def refresh(self):
        self.today.configure(text=today_line(self.folder))
        self.days.delete(0, "end")
        for row in daily(self.folder):
            kinds = ", ".join(f"{kind} {count}" for kind, count in sorted(row["kinds"].items()))
            problems = row["failed"] + row["cancelled"]
            self.days.insert("end", f"{row['day']}  ·  {row['calls']} calls ({row['player']} yours, {row['app']} app)"
                                    f"  ·  {row['seconds']:.0f} s  ·  {row['characters'] // 1000} k chars"
                                    + (f"  ·  {problems} not answered" if problems else "") + f"  ·  {kinds}")
        counted = opens(self.folder)
        self.views.configure(text=" · ".join(f"{view} {count}" for view, count in counted.most_common(12))
                             or "No tool has been opened yet.")
        self.notes.delete(0, "end")
        for note in feedback_notes(self.folder)[:30]:
            sounds = " / ".join(item["name"] for item in note.get("sounds", {}).values())
            self.notes.insert("end", f"{note['at'][:16]}  {note['text'][:70]}" + (f"  ·  {sounds}" if sounds else ""))

    def check_unit(self):
        def shown(state):
            self.unit.configure(text=f"CorOS {state['firmware']} · round trip "
                                     f"{'measured' if state['calibrated'] else 'not measured yet'} · "
                                     f"{state['cached']} of {state['cache_slots']} cache slots hold built chains")

        self.call("lab.tools.usage.unit_state", done=shown, working="Asking the Quad Cortex…")

    def keep(self):
        try:
            save_feedback(self.folder, self.note.get("1.0", "end"), situation(self.studio))
        except ValueError as error:
            self.say(str(error))
            return
        self.note.delete("1.0", "end")
        self.say("Kept.")
        self.refresh()


def open(desk):  # noqa: A001 - every tool opens with `open(desk)`
    return UsageWindow.open(desk)
