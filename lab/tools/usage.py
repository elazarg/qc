"""Usage and feedback: which tools are opened, what the assistant cost each day, and the player's notes.

- **Usage** is a local log of tool windows opened and closed (`ui-usage.jsonl` in the data folder), as the
  old desk kept for its panels.
- **The assistant's ledger** (`ledger.jsonl`, written by `lab.sound.assistant`) is read back day by day: how many
  calls, whose (the player's or the app's own), of which kind, how long they took, how much was sent, how
  they ended, against the daily allowance. A day is the player's own day (`assistant.local_day`), as the
  allowance counts it.
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

from lab.desk.theme import FONT, INK, PAPER, SOFT, SUBTLE
from lab.tools.common import WIDTH, Tool, button, disclosure, label, listbox, section

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


# ---- the assistant's ledger ----

def daily(folder, *, days=14, today=None):
    """The ledger by day, newest first, for the last `days` days that had calls.

    Each day: {"day", "calls", "player", "app", "web", "kinds": {kind: n}, "seconds", "characters",
    "ok", "cancelled", "failed", "refused"}. A call refused before it was made is not in the ledger.
    """
    from lab.sound.assistant import DAILY, local_day

    found = defaultdict(lambda: {"calls": 0, "player": 0, "app": 0, "web": 0, "kinds": Counter(), "seconds": 0.,
                                 "characters": 0, "ok": 0, "cancelled": 0, "failed": 0})
    for row in _rows(Path(folder) / "ledger.jsonl"):
        day = local_day(row.get("at"))      # the player's own day, as the daily allowance counts it
        if day is None:
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
    today = today or datetime.now().date().isoformat()
    result = []
    for day in sorted(found, reverse=True)[:days]:
        entry = found[day]
        result.append({"day": day, **entry, "kinds": dict(entry["kinds"]), "seconds": round(entry["seconds"], 1),
                       "limit": DAILY, "left": max(0, DAILY - entry["calls"]) if day == today else None})
    return result


def today_line(folder, today=None):
    from lab.sound.assistant import DAILY

    today = today or datetime.now().date().isoformat()
    entry = next((row for row in daily(folder, today=today) if row["day"] == today), None)
    used = entry["calls"] if entry else 0
    return f"Today: {used} of {DAILY} calls to the assistant" + (
        f" ({entry['player']} yours, {entry['app']} the app's own)" if entry else "")


# ---- feedback ----

def situation(studio):
    """What is on the desk, as a note about it should remember it."""
    state, found = studio.state, {}
    for side in ("a", "b"):
        sound = studio.sound(side)
        if sound is not None:
            found[side] = {"id": sound.id, "name": sound.name, "digest": sound.chain.digest(),
                           "knobs": dict(sound.knobs)}
    return {"sounds": found, "phrase": studio.phrase[0] if studio.phrase else None,
            "round": state.round, "song": state.song, "instrument": state.instrument}


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


def day_line(row):
    """One day of the ledger, short enough for the list."""
    problems = row["failed"] + row["cancelled"]
    return (f"{row['day']}  ·  {row['calls']} calls ({row['player']} yours, {row['app']} app)"
            + (f"  ·  {problems} not answered" if problems else ""))


def day_detail(row):
    """The rest of what the ledger says of a day: how long, how much, which kinds."""
    kinds = ", ".join(f"{kind} {count}" for kind, count in sorted(row["kinds"].items()))
    return f"{row['day']}: {row['seconds']:.0f} s with the assistant, {row['characters'] // 1000} k characters sent. {kinds}"


# ---- the tool ----

class UsageTool(Tool):
    TITLE = "Usage and feedback"

    def build(self):
        self.rows = []
        calls = section(self.body, "The assistant", "Calls by day, from the ledger every call is written to. "
                                                    "Choose a day for the rest.")
        self.today = label(calls, size=10, bold=True, wrap=WIDTH)
        self.today.pack(fill="x")
        self.days = listbox(calls, height=6)
        self.days.pack(fill="x", pady=(6, 0))
        self.days.bind("<<ListboxSelect>>", lambda _: self.describe_day())
        self.day = label(calls, size=9, color=SUBTLE, wrap=WIDTH)
        self.day.pack(fill="x", pady=(4, 0))

        notes = section(self.body, "Feedback", "A note about what is on A and B now. It is kept with them.")
        self.note = tk.Text(notes, height=3, width=20, bg=PAPER, fg=INK, insertbackground=INK, relief="flat",
                            font=(FONT, 10), wrap="word")
        self.note.pack(fill="x")
        button(notes, "Keep this note", self.keep).pack(anchor="w", pady=(6, 0))
        self.notes = listbox(notes, height=4)
        self.notes.pack(fill="x", pady=(6, 0))

        views = disclosure(self.body, "Tools opened, and what is kept on the unit")
        self.views = label(views, size=9, color=SOFT, wrap=WIDTH)
        self.views.pack(fill="x")
        button(views, "What the app keeps on the Quad Cortex", self.check_unit, quiet=True).pack(anchor="w",
                                                                                                pady=(6, 0))
        self.unit = label(views, size=9, color=SUBTLE, wrap=WIDTH)
        self.unit.pack(fill="x", pady=(4, 0))
        self.refresh()

    def shown(self):
        super().shown()
        self.refresh()

    def describe_day(self):
        picked = self.days.curselection()
        if picked:
            self.day.configure(text=day_detail(self.rows[picked[0]]))

    def refresh(self):
        self.today.configure(text=today_line(self.folder))
        self.days.delete(0, "end")
        self.rows = daily(self.folder)
        for row in self.rows:
            self.days.insert("end", day_line(row))
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

        self.call("lab.tools.probe.unit_state", done=shown, working="Asking the Quad Cortex…")

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
    return UsageTool.open(desk)
