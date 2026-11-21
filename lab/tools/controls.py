"""The controls workbench: every Shape knob in its group, the versions of a sound, and what the unit holds.

Carried over from the old second workbench (`clip --workbench`: the knob panel `knob_panel`, the draft card
`draft_ui`, the live panel), onto the desk's studio:

- **Every knob.** The desk's Shape panel shows the knobs a sound can use; here they are all, by group
  (Main, Response, EQ, Effects, Character), each saying which controls of which blocks it moves at its
  present position. A knob the sound has no block for is shown and greyed. "Hear the base" plays the sound
  with the knobs at rest, and again with them; Reset puts them all at rest.
- **Versions.** A sound's line of revisions, oldest to newest, from the one it was first made as: step
  between them (each goes on the selected side), read why it was made that way and the pages it drew on,
  try it on the guitar, save it to the unit.
- **What the unit holds.** The chain on the Quad Cortex now, block by block, read back: what the knobs
  actually wrote, whatever the desk believes.

The old knob was a drawn dial turned by dragging or the arrow keys; here it is the desk's slider, the
same control the Shape panel uses.
"""

import tkinter as tk
import webbrowser

from lab.sound import knobs
from lab.sound.sounds import describe
from lab.desk.theme import ACCENT, ACCENT_INK, DARK, DIM, FONT, INK, PAPER, SUBTLE, TILE
from lab.tools.common import Window, button, label, section, stated

GROUPS = {"Main": knobs.PRIMARY, **knobs.GROUPS}


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


# ---- operations, run where the unit is held ----

def held(engine, *, stop=None):
    """The chain on the unit now, in words, and which slot it is in."""
    chain = engine.unit.read()
    return {"lines": describe(chain), "position": engine.unit.position(), "name": engine.unit.name()}


# ---- the window ----

class ControlsWindow(Window):
    TITLE = "Controls workbench"
    SIZE = "640x720"

    def build(self):
        self.group, self.base = "Main", None
        self.scales = {}
        self.shown = None               # what the board was last drawn from
        knobs_card = section(self.body, "Every knob")
        self.name = label(knobs_card, size=10, bold=True)
        self.name.pack(fill="x")
        self.tabs = tk.Frame(knobs_card, bg=knobs_card["bg"])
        self.tabs.pack(fill="x", pady=(6, 4))
        self.board = tk.Frame(knobs_card, bg=knobs_card["bg"])
        self.board.pack(fill="x")
        self.hint = label(knobs_card, size=9, color=SUBTLE, wrap=560)
        self.hint.pack(fill="x", pady=(4, 0))
        row = tk.Frame(knobs_card, bg=knobs_card["bg"])
        row.pack(fill="x", pady=(6, 0))
        self.base_button = button(row, "Hear the base", self.toggle_base, quiet=True)
        self.base_button.pack(side="left")
        button(row, "Reset", self.reset, quiet=True).pack(side="left", padx=6)

        versions = section(self.body, "Versions")
        row = tk.Frame(versions, bg=versions["bg"])
        row.pack(fill="x")
        self.older = button(row, "Older", lambda: self.step(-1), quiet=True)
        self.older.pack(side="left")
        self.newer = button(row, "Newer", lambda: self.step(1), quiet=True)
        self.newer.pack(side="left", padx=4)
        button(row, "Try on the guitar", lambda: self.studio.play("guitar"), quiet=True).pack(side="left", padx=4)
        button(row, "Save to QC", self.save).pack(side="right")
        self.sources = tk.Menubutton(row, text="Why", bg=TILE, fg=INK, relief="flat", font=(FONT, 9), padx=8)
        self.sources_menu = tk.Menu(self.sources, tearoff=False)
        self.sources.configure(menu=self.sources_menu)
        self.sources.pack(side="right", padx=4)
        self.version = label(versions, size=9, wrap=560)
        self.version.pack(fill="x", pady=(6, 0))
        self.explanation = label(versions, size=9, color=SUBTLE, wrap=560)

        unit = section(self.body, "What the Quad Cortex holds")
        button(unit, "Read it back", self.read_back, quiet=True).pack(anchor="w")
        self.held = label(unit, size=9, wrap=560)
        self.held.pack(fill="x", pady=(4, 0))
        self.listener = lambda state: self.after(0, self.refresh) if self.alive() else None
        self.studio.listeners.append(self.listener)
        self.refresh()

    # the knobs

    def sound(self):
        return self.studio.sound(self.studio.state.side)

    def refresh(self, *, force=False):
        if not self.alive():
            return
        sound = self.sound()
        # The desk's state changes often (its working line); the board is rebuilt only when what it shows does.
        shown = (self.studio.state.side, sound.id if sound else None, tuple(sorted((sound.knobs if sound else {})
                                                                                   .items())), self.group, self.base)
        if shown == self.shown and not force:
            return
        self.shown = shown
        self.name.configure(text=f"{self.studio.state.side.upper()} · {sound.name}" if sound else
                            "Nothing on the selected side.")
        for child in self.tabs.winfo_children():
            child.destroy()
        for group in GROUPS:
            active = group == self.group
            tk.Button(self.tabs, text=group, command=lambda group=group: self.show(group), relief="flat",
                      borderwidth=0, padx=8, pady=3, font=(FONT, 9), bg=ACCENT if active else TILE,
                      fg=ACCENT_INK if active else INK).pack(side="left", padx=(0, 3))
        self.fill_board(sound)
        self.fill_versions(sound)

    def show(self, group):
        self.group = group
        self.refresh()

    def fill_board(self, sound):
        for child in self.board.winfo_children():
            child.destroy()
        self.scales = {}
        if sound is None:
            return
        available = set(knobs.available(sound.chain))
        values = sound.knobs if self.base is None else self.base
        for index, key in enumerate(GROUPS[self.group]):
            title, low, _ = knobs.KNOBS[key]
            usable = key in available
            cell = tk.Frame(self.board, bg=DARK)
            cell.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, 10), pady=1)
            label(cell, title, size=9, color=INK if usable else DIM).pack(side="left")
            scale = tk.Scale(cell, from_=low, to=1, resolution=.05, orient="horizontal", length=170, showvalue=False,
                             bg=DARK, fg=INK, troughcolor=TILE if usable else PAPER, highlightthickness=0,
                             borderwidth=0, state="normal" if usable else "disabled")
            scale.set(values.get(key, 0.))
            scale.pack(side="right")
            scale.bind("<ButtonRelease-1>", lambda _, key=key, scale=scale: self.turned(key, scale.get()))
            for widget in (cell, scale):
                widget.bind("<Enter>", lambda _, key=key, usable=usable: self.describe_knob(key, usable), add="+")
            self.scales[key] = scale

    def describe_knob(self, key, usable):
        sound = self.sound()
        if sound is None:
            return
        if not usable:
            self.hint.configure(text=f"{knobs.KNOBS[key][0]} needs a block this sound does not have.")
            return
        names = reach(sound.chain, key, sound.knobs.get(key, 0.))
        self.hint.configure(text=f"{knobs.KNOBS[key][0]} moves " + (", ".join(names[:6]) or "nothing here yet") + ".")

    def turned(self, key, value):
        sound = self.sound()
        if sound is None:
            return
        self.base = None
        self.base_button.configure(text="Hear the base")
        self.studio.turn({**sound.knobs, key: value})

    def toggle_base(self):
        sound = self.sound()
        if sound is None:
            return
        if self.base is None:
            if not sound.knobs:
                self.say("The knobs are already at rest.")
                return
            self.base = dict(sound.knobs)
            self.studio.turn({})
            self.base_button.configure(text="Hear the shape")
        else:
            values, self.base = self.base, None
            self.studio.turn(values)
            self.base_button.configure(text="Hear the base")

    def reset(self):
        self.base = None
        self.studio.turn({})

    # the versions

    def fill_versions(self, sound):
        self.sources_menu.delete(0, "end")
        if sound is None:
            self.version.configure(text="")
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
        self.explanation.configure(text=sound.explanation)
        if sound.explanation:
            self.sources_menu.add_command(label="Why this approach?", command=self.toggle_explanation)
        for source in sound.sources or []:
            title, url = (source.get("title", ""), source.get("url")) if isinstance(source, dict) else (str(source), None)
            self.sources_menu.add_command(label=title[:90] or str(url), command=lambda url=url: url and webbrowser.open(url))

    def toggle_explanation(self):
        if self.explanation.winfo_manager():
            self.explanation.pack_forget()
        else:
            self.explanation.pack(fill="x", pady=(4, 0))

    def step(self, direction):
        sound = self.sound()
        if sound is None:
            return
        line = lineage(self.studio.store, sound)
        place = next(index for index, item in enumerate(line) if item.id == sound.id) + direction
        if 0 <= place < len(line):
            self.studio.put(self.studio.state.side, line[place])

    def save(self):
        self.desk.save()                # the desk asks for the name in place, as its own Save to QC does

    # the unit

    def read_back(self):
        def shown(found):
            self.held.configure(text=f"{found['name']}\n" + ("\n".join(found["lines"]) or "No blocks."))

        self.call("lab.tools.controls.held", done=shown, working="Reading the Quad Cortex…")

    def close(self):
        if self.listener in self.studio.listeners:
            self.studio.listeners.remove(self.listener)
        super().close()


def open(desk):  # noqa: A001
    return ControlsWindow.open(desk)
