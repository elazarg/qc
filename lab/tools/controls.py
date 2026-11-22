"""Versions and the unit: the line of revisions a sound belongs to, and what the Quad Cortex holds now.

What is left of the old second workbench once the desk's Shape panel does the knobs (turning them, hearing
the sound without them, Undo) and the desk saves and plays:

- **Versions.** A sound's line of revisions, oldest to newest, from the one it was first made as: step
  between them (each goes on the selected side), and read, in place, why it was made that way and the pages
  it drew on (their addresses are shown as text; nothing is opened outside the window).
- **What each knob moves here.** For the selected sound, which controls of which blocks each Shape knob
  moves. The Shape panel turns the knobs; this says what turning one does to this chain.
- **What the unit holds.** The chain on the Quad Cortex now, block by block, read back: what the knobs
  actually wrote, whatever the desk believes.
"""

import tkinter as tk

from lab.sound import knobs
from lab.sound.sounds import describe
from lab.desk.theme import SUBTLE
from lab.tools.common import WIDTH, Tool, button, disclosure, label, said, section, stated


def lineage(store, sound):
    """The sound's versions, oldest first: its parents, itself, and the sounds revised from it, newest last."""
    chain, seen = [sound], {sound.id}
    while chain[0].parent and chain[0].parent not in seen:
        try:
            parent = store.sound(chain[0].parent)
        except (OSError, ValueError):
            break
        seen.add(parent.id)
        chain.insert(0, parent)
    children = {}
    for other in store.sounds():
        if other.parent and other.id not in seen:
            children.setdefault(other.parent, []).append(other)
    current = sound
    while children.get(current.id):
        current = sorted(children[current.id], key=lambda item: item.created)[-1]
        chain.append(current)
        seen.add(current.id)
    return chain


def reach(chain, key, value):
    """What one knob at `value` moves, as "block control" names."""
    if not value:
        value = 1. if key != "drive" else .5
    names = []
    for edit in knobs.moves(stated(chain), {key: value}):
        name = f"{edit['model']} {edit.get('name', 'on/off')}"
        if name not in names:
            names.append(name)
    return names


def reach_lines(sound):
    """For each Shape knob this sound can use, the controls it moves: "Drive: Brit Plexi GAIN, …"."""
    lines = []
    for key in knobs.available(sound.chain):
        names = reach(sound.chain, key, sound.knobs.get(key, 0.))
        lines.append(f"{knobs.KNOBS[key][0]}: " + (", ".join(names[:6]) or "nothing here yet"))
    return lines


def why_lines(sound):
    """Why a sound was made as it was, and the pages it drew on, as text: each page's title and its address."""
    lines = [sound.explanation] if sound.explanation else []
    for source in sound.sources or []:
        title, url = (source.get("title", ""), source.get("url")) if isinstance(source, dict) else (str(source), None)
        lines.append(" · ".join(part for part in (title[:90], url) if part))
    return lines


# ---- operations, run where the unit is held ----

def held(engine, *, stop=None):
    """The chain on the unit now, in words, and which slot it is in."""
    chain = engine.unit.read()
    return {"lines": describe(chain), "position": engine.unit.position(), "name": engine.unit.name()}


# ---- the tool ----

class ControlsTool(Tool):
    TITLE = "Versions and the unit"

    def build(self):
        self.drawn = None               # what the tool was last drawn from
        versions = section(self.body, "Versions", "The sounds the selected one was made from and the ones made "
                                                  "from it, oldest first. Older and Newer put that version on the "
                                                  "selected side.")
        self.name = label(versions, size=10, bold=True, wrap=WIDTH)
        self.name.pack(fill="x")
        row = tk.Frame(versions, bg=versions["bg"])
        row.pack(fill="x", pady=(4, 0))
        self.older = button(row, "Older", lambda: self.step(-1), quiet=True)
        self.older.pack(side="left")
        self.newer = button(row, "Newer", lambda: self.step(1), quiet=True)
        self.newer.pack(side="left", padx=4)
        self.version = said(versions, pady=(6, 0))
        self.why = disclosure(versions, "Why it was made this way")
        self.why_text = said(self.why, color=SUBTLE, pady=0)
        self.moves = disclosure(versions, "What each knob moves here")
        self.moves_text = said(self.moves, color=SUBTLE, pady=0)

        unit = section(self.body, "What the Quad Cortex holds",
                       "The chain on the unit now, read back from it: what plays, whatever the desk shows.")
        button(unit, "Read it back", self.read_back, quiet=True).pack(anchor="w")
        self.held = said(unit, pady=(4, 0))
        self.listener = lambda state: self.after(0, self.refresh) if self.showing() else None
        self.studio.listeners.append(self.listener)
        self.refresh()

    def shown(self):
        super().shown()
        self.refresh()

    def sound(self):
        return self.studio.sound(self.studio.state.side)

    def refresh(self, *, force=False):
        if not self.alive():
            return
        sound = self.sound()
        # The desk's state changes often (its working line); the tool is drawn again only when what it shows does.
        drawn = (self.studio.state.side, sound.id if sound else None,
                 tuple(sorted((sound.knobs if sound else {}).items())))
        if drawn == self.drawn and not force:
            return
        self.drawn = drawn
        self.name.configure(text=f"{self.studio.state.side.upper()} · {sound.name}" if sound else
                            "Nothing on the selected side.")
        if sound is None:
            for control in (self.older, self.newer):
                control.configure(state="disabled")
            for text in (self.version, self.why_text, self.moves_text):
                text.configure(text="")
            return
        line = lineage(self.studio.store, sound)
        place = next(index for index, item in enumerate(line) if item.id == sound.id)
        self.older.configure(state="normal" if place > 0 else "disabled")
        self.newer.configure(state="normal" if place < len(line) - 1 else "disabled")
        origin = sound.origin or {}
        made = {"designed": "Designed", "preset": "Saved on the Quad Cortex", "revised": "Revised",
                "imported": "Imported"}.get(origin.get("kind"), "")
        request = origin.get("request")
        self.version.configure(text=f"Version {place + 1} of {len(line)} · {made}" + (f": {request}" if request else "")
                               + ("\n" + "\n".join(describe(sound.chain)) if sound.chain else ""))
        self.why_text.configure(text="\n".join(why_lines(sound)) or "Nothing was noted for this one.")
        self.moves_text.configure(text="\n".join(reach_lines(sound)) or "This sound has nothing the knobs move.")

    def step(self, direction):
        sound = self.sound()
        if sound is None:
            return
        line = lineage(self.studio.store, sound)
        place = next(index for index, item in enumerate(line) if item.id == sound.id) + direction
        if 0 <= place < len(line):
            self.studio.put(self.studio.state.side, line[place])
            self.refresh()

    # the unit

    def read_back(self):
        def shown(found):
            self.held.configure(text=f"{found['name']}\n" + ("\n".join(found["lines"]) or "No blocks."))

        self.call("lab.tools.controls.held", done=shown, working="Reading the Quad Cortex…")


def open(desk):  # noqa: A001
    return ControlsTool.open(desk)
