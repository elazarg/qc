"""The desk: the window over `Studio`. It draws the state and turns clicks into actions.

Run it with `python -m lab` (the Quad Cortex) or `python -m lab --simulated`
(no hardware). Two sounds, the input, one line that speaks, one place to ask,
and panels that open when called. Nothing here decides anything; `studio.py`
does.

Everything in this window happens in it: no dialogs, no pop-up menus, no
keyboard shortcuts. Choices are buttons, names are typed in place, and the
panels say what they hold. (The Guitar Learner and the side tools, opened
from the Guitar and More panels, still have windows of their own.)

What is drawn again, and when: the tiles and the lines on every change of
state; the round's chips, the scene chips and the open panel only when what
they show has changed, and never while something in the panel is being
dragged.
"""

import json
from pathlib import Path
import sys
import tkinter as tk
import traceback

from lab.sound import knobs as knob_table
from lab.hearing.audio import read_wave
from lab.knowledge import REPO, home
from lab.hearing.measure import summary
from lab.desk.studio import RATE, Studio
from lab.learner.seat import LearnerSeat  # noqa: F401  (the desk makes one; tests import it from here)
from lab.desk.theme import ACCENT, DARK, DIM, FONT, INK, LIVE, PAPER, RULE, SIDE, SOFT, SUBTLE, TILE, mix

PANELS = ("Shape", "Chain", "Difference", "Phrase", "Sounds", "History", "Guitar", "More")
RECORD_SECONDS = 8.
SHORTEST_LOOP = RATE // 2       # a loop is at least half a second
PAD_SIZE = 200      # the Brightness x Texture pad, in pixels a side


def pad_point(across, up, size=PAD_SIZE):
    """Where knob positions (-1 to 1 each) sit on the pad: across from the left edge, up from the bottom."""
    return (across + 1) / 2 * size, (1 - up) / 2 * size


def pad_values(x, y, size=PAD_SIZE):
    """The knob positions at a point on the pad, kept on the pad and on steps of .05."""
    def position(fraction):
        return round(round(max(-1., min(1., fraction)) * 20) / 20, 2)

    return position(2 * x / size - 1), position(1 - 2 * y / size)


def label(parent, text="", *, size=10, color=INK, bold=False, wrap=0):
    return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=(FONT, size, "bold" if bold else "normal"),
                    justify="left", anchor="w", wraplength=wrap)


def button(parent, text, action, *, dark=True):
    return tk.Button(parent, text=text, command=action, relief="flat", borderwidth=0, padx=12, pady=6,
                     cursor="hand2", font=(FONT, 10, "bold"), bg=TILE if dark else "#252834", fg=INK,
                     activebackground=RULE, activeforeground=INK)


def link(parent, text, action, *, color=SOFT):
    """Words that are clicked."""
    found = tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=(FONT, 9), cursor="hand2", padx=8)
    found.bind("<ButtonRelease-1>", lambda _: action())
    return found


def history_rows(folder):
    """Every sound kept, newest first, as (id, name, where it came from, whether it is saved on the unit). Read
    from the files as they are, without building the chains: the list is long and is drawn often."""
    rows = []
    for path in (Path(folder) / "sounds").glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            rows.append((str(record.get("created", "")), record["id"], str(record["name"]),
                         str((record.get("origin") or {}).get("kind", "")), bool(record.get("saved"))))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return [row[1:] for row in sorted(rows, reverse=True)]


def loop_edges(edges, at, shortest=SHORTEST_LOOP):
    """The loop's edges after the nearer one is dragged to `at` (all in samples). An edge that would leave less
    than `shortest` between them stays where it is."""
    start, end = edges
    if abs(at - start) < abs(at - end):
        start = at
    else:
        end = at
    return (start, end) if end - start >= shortest else edges


class Desk:
    def __init__(self, root, studio):
        self.root, self.studio = root, studio
        self.panel = self.learner = None
        self.panel_key = None           # what the open panel was drawn from; it is redrawn only when that changes
        self.strip_key = self.scene_key = None      # the same for the round's chips and the scene chips
        self.dragging = False           # something in the panel is being dragged: the panel is left as it is
        self.expanded = set()           # blocks whose named settings are shown in the Chain panel
        self.show_nano = False
        self.sounds_kind = "Presets"    # which list the Sounds panel shows
        self.capture_version = 2        # what an unlabelled capture is taken to be
        self.record_setting = None
        self.save_target = None         # (sound id, knob positions) the name being typed is for
        self.phrase_shown = None        # the phrase the link at the right names
        self.seat = None                # the Guitar Learner's seat, once something of the learner was opened
        self.clock = self.door = None   # the `after` of `tick`; the socket a second launch knocks on
        self.drawing_fault = None       # the last fault while drawing, so that it is logged once
        self.play_when_free = None      # what to play once gesture listening has let go of the audio input
        self.closing = False
        root.report_callback_exception = self.failed
        root.title("Tone Lab")
        root.configure(bg=PAPER)
        try:
            root.iconbitmap(default=str(REPO / "ToneLab.ico"))   # this window and every one it opens
        except tk.TclError:
            pass
        root.geometry("1100x680")
        root.minsize(980, 620)
        self.build()
        studio.listeners.append(lambda state: self.draw())
        root.protocol("WM_DELETE_WINDOW", self.close)
        # The clock stops with the window, so nothing is called on a window that is gone.
        root.bind("<Destroy>", lambda event: event.widget is root and self.clock and root.after_cancel(self.clock),
                  add="+")
        try:
            self.come_back()
        except Exception as error:  # noqa: BLE001  whatever was left behind, the desk opens
            studio.set_aside(error)
            self.panel = None
            self.tray.grid_remove()
            self.draw()
        studio.open()
        self.tick()

    def come_back(self):
        """Put back what the last session left: A and B, the phrase and its loop, the open panel."""
        session = self.studio.restore() or {}
        recorded = self.studio.recorded_phrases()
        kept = session.get("phrase") if isinstance(session.get("phrase"), dict) else {}
        item = next((item for item in recorded if item["id"] == kept.get("id")), None)
        if item is not None:
            self.choose_recorded(item, kept.get("start"), kept.get("end"))
        elif recorded:
            self.choose_recorded(recorded[0])
        panel = self.studio.desk_session.get("panel")
        if panel in PANELS:
            self.toggle(panel)
        self.paint()

    # ---- building ----

    def build(self):
        page = tk.Frame(self.root, bg=PAPER, padx=20, pady=16)
        page.pack(fill="both", expand=True)
        top = tk.Frame(page, bg=PAPER)
        top.pack(fill="x")
        self.dot = tk.Canvas(top, width=12, height=12, bg=PAPER, highlightthickness=0)
        self.dot.pack(side="left")
        self.dot_mark = self.dot.create_oval(2, 2, 10, 10, fill=DIM, width=0)
        self.working = label(top, color=SOFT)
        self.working.pack(side="left", padx=8)
        self.cancel_link = link(top, "Cancel", lambda: self.studio.cancel(), color=ACCENT)
        for name in PANELS[::-1]:
            opener = tk.Label(top, text=name, bg=PAPER, fg=SUBTLE, font=(FONT, 10), cursor="hand2", padx=8)
            opener.pack(side="right")
            opener.bind("<ButtonRelease-1>", lambda _, name=name: self.toggle(name))

        body = tk.Frame(page, bg=PAPER)
        body.pack(fill="both", expand=True, pady=(12, 0))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        main = tk.Frame(body, bg=PAPER)
        main.grid(row=0, column=0, sticky="nsew")
        self.tray = tk.Frame(body, bg=DARK, width=430, padx=12, pady=10)
        self.tray.grid_propagate(False)
        self.tray.pack_propagate(False)        # one width for every panel

        pair = tk.Frame(main, bg=PAPER)
        pair.pack(fill="both", expand=True)
        pair.columnconfigure((0, 1), weight=1, uniform="pair")
        pair.rowconfigure(0, weight=1)
        self.tiles = {}
        for column, side in enumerate(("a", "b")):
            tile = tk.Frame(pair, bg=DARK, padx=16, pady=13, highlightthickness=2, highlightbackground=DARK,
                            cursor="hand2")
            tile.grid(row=0, column=column, sticky="nsew", padx=(0, 5) if side == "a" else (5, 0))
            badge = label(tile, side.upper(), size=34, color=SIDE[side], bold=True)
            name = label(tile, size=14, bold=True, wrap=320)
            caption = label(tile, size=9, color=SUBTLE, wrap=320)
            marks = tk.Frame(tile, bg=DARK)
            for word in ("keeper", "not this"):
                chip = tk.Label(marks, text="Keep" if word == "keeper" else "Not this", padx=8, pady=2,
                                font=(FONT, 9), cursor="hand2", bg=DARK, fg=SUBTLE)
                chip.pack(side="left", padx=(0, 6))
                chip.bind("<ButtonRelease-1>", lambda _, side=side, word=word: self.studio.mark(word, side))
            air = label(tile, size=8, color=LIVE, bold=True)
            air.pack(side="top", fill="x")
            marks.pack(side="bottom", fill="x", pady=(6, 0))
            caption.pack(side="bottom", fill="x")
            name.pack(side="bottom", fill="x")
            badge.pack(side="bottom", fill="x")
            for widget in (tile, badge, name, caption, air):
                widget.bind("<ButtonRelease-1>", lambda _, side=side: self.studio.select(side))
            self.tiles[side] = (tile, name, caption, air, marks)

        self.scene_row = tk.Frame(main, bg=PAPER)
        self.scene_row.pack(fill="x", pady=(8, 0))
        self.strip = tk.Frame(main, bg=PAPER)
        self.strip.pack(fill="x", pady=(10, 0))
        self.chips = tk.Frame(self.strip, bg=PAPER)
        self.chips.pack(fill="x")
        self.strip_note = label(self.strip, size=9, color=SUBTLE, wrap=600)
        self.strip_note.pack(fill="x", pady=(4, 0))

        row = tk.Frame(main, bg=PAPER)
        row.pack(fill="x", pady=(12, 0))
        self.guitar = button(row, "Guitar", lambda: self.toggle_play("guitar"))
        self.guitar.pack(side="left")
        self.loop = button(row, "Phrase", lambda: self.toggle_play("phrase"))
        self.loop.pack(side="left", padx=(2, 0))
        self.copy_link = link(row, "Copy B to A", lambda: self.studio.copy_to_a())
        self.copy_link.pack(side="left", padx=(10, 0))
        self.blind_link = link(row, "Compare blind", lambda: self.studio.blind())
        self.blind_link.pack(side="left")
        # Shown once there is something to take back.
        self.undo_link = link(row, "Undo", lambda: self.studio.undo())
        self.phrase_link = tk.Label(row, text="No phrase", bg=PAPER, fg=SUBTLE, font=(FONT, 9), cursor="hand2")
        self.phrase_link.pack(side="right")
        self.phrase_link.bind("<ButtonRelease-1>", lambda _: self.toggle("Phrase"))
        button(row, "Save to QC", self.save).pack(side="right", padx=8)
        # Naming a save, in place: shown when Save to QC is pressed.
        self.save_row = tk.Frame(main, bg=PAPER)
        label(self.save_row, "Save as", size=9, color=SOFT).pack(side="left")
        self.save_name = tk.Entry(self.save_row, font=(FONT, 11), bg=TILE, fg=INK, relief="flat", insertbackground=INK,
                                  width=26)
        self.save_name.pack(side="left", padx=8, ipady=4)
        self.save_name.bind("<Return>", lambda _: self.saved())
        button(self.save_row, "Save", self.saved).pack(side="left")
        link(self.save_row, "Cancel", self.save_row.pack_forget).pack(side="left")
        self.save_row_after = row

        self.line = label(main, size=10, color=INK, wrap=640)
        self.line.pack(fill="x", pady=(8, 4))
        ask = tk.Frame(main, bg=DARK, highlightthickness=1, highlightbackground=RULE)
        ask.pack(fill="x")
        self.request = tk.Entry(ask, font=(FONT, 12), bg=DARK, fg=INK, relief="flat", insertbackground=INK)
        self.request.pack(side="left", fill="x", expand=True, ipady=9, padx=12)
        self.request.bind("<Return>", lambda _: self.ask())

    # ---- drawing ----

    def draw(self):
        """Draw the state. A fault in drawing is logged once and said on the line; it never reaches the action
        that changed the state."""
        try:
            self.paint()
        except Exception as error:  # noqa: BLE001
            said = f"{type(error).__name__}: {error}"
            if said != self.drawing_fault:
                self.drawing_fault = said
                self.studio.log_error(error, traceback.format_exc())
            self.line.configure(text=f"Something went wrong drawing the desk: {error}. The details are in "
                                     "errors.log in the data folder.")

    def paint(self):
        studio, state = self.studio, self.studio.state
        sounds = {side: studio.sound(side) for side in ("a", "b")}
        self.working.configure(text=state.working)
        # Cancel is there for as long as a request is with the assistant, whatever else the working line says.
        if studio.cancellable():
            self.cancel_link.pack(side="left")
        else:
            self.cancel_link.pack_forget()
        self.dot.itemconfigure(self.dot_mark, fill=LIVE if state.mode else (ACCENT if state.working else DIM))
        for side in ("a", "b"):
            tile, name, caption, air, marks = self.tiles[side]
            sound = sounds[side]
            hidden = state.blind and sound is not None
            name.configure(text="?" if hidden else (sound.name if sound else "Empty"))
            caption.configure(text="Hidden while you compare" if hidden else self.caption(sound))
            # Keep and Not this stay available while comparing blind (choosing shows the names); a mark already
            # given is not shown then, since it would tell which sound is which.
            for chip, word in zip(marks.winfo_children(), ("keeper", "not this")):
                marked = sound is not None and not hidden and word in sound.marks
                chip.configure(bg=mix(SIDE[side], DARK, .35) if marked else tile["bg"], fg=INK if marked else SUBTLE)
            if sound is None:
                marks.pack_forget()
            elif not marks.winfo_ismapped():
                marks.pack(side="bottom", fill="x", pady=(6, 0), before=caption)
            selected = state.side == side
            on_air = state.mode and selected
            tile.configure(highlightbackground=SIDE[side] if selected else DARK,
                           bg=mix(SIDE[side], DARK, .14) if on_air else DARK)
            for widget in (name, caption, air, marks):
                widget.configure(bg=tile["bg"])
            air.configure(text=f"● {state.mode}" if on_air else "")
        self.line.configure(text=state.status)
        # While something else has the audio input, the two buttons are dimmed; pressing one says who has it.
        held = bool(studio.audio_held_by)
        for widget, mode in ((self.guitar, "guitar"), (self.loop, "phrase")):
            widget.configure(bg=mix(LIVE, TILE, .4) if state.mode == mode else TILE, fg=DIM if held else INK)
        self.blind_link.configure(text="Show which was which" if state.blind else "Compare blind")
        if studio.history and not self.undo_link.winfo_manager():
            self.undo_link.pack(side="left")
        elif not studio.history and self.undo_link.winfo_manager():
            self.undo_link.pack_forget()
        phrase_id = studio.phrase[0] if studio.phrase else None
        if phrase_id != self.phrase_shown:
            self.phrase_shown = phrase_id
            item = next((item for item in studio.recorded_phrases() if item["id"] == phrase_id), None)
            self.phrase_link.configure(text=f"Phrase · {item.get('instrument', '')} · {item.get('setting', '')}"
                                       if item else ("Phrase" if phrase_id else "No phrase"))
        self.draw_strip(sounds["b"])
        self.draw_scenes()
        if self.panel and not self.dragging and self.drawn_from(sounds) != self.panel_key:
            self.fill_panel()

    def drawn_from(self, sounds=None):
        """What the open panel shows depends on: the sounds on show, what is known of them, the round, the scene."""
        studio, state = self.studio, self.studio.state
        sides = []
        for side in ("a", "b"):
            sound = sounds[side] if sounds else studio.sound(side)
            sides.append(None if sound is None else (sound.id, json.dumps(sound.heard, sort_keys=True, default=str),
                                                     json.dumps(sound.knobs, sort_keys=True), tuple(sound.marks)))
        return (self.panel, state.side, state.round, state.blind, state.scene, state.song, state.instrument,
                tuple(sides), self.show_nano, tuple(sorted(self.expanded)), studio.phrase_key(),
                tuple(sorted(studio.hearing_base)), studio.profile_version if self.panel == "Guitar" else None)

    def caption(self, sound):
        if sound is None:
            return "Ask for a sound, or bring in a preset from Sounds."
        parts = []
        origin = sound.origin.get("kind")
        if origin == "preset":
            parts.append("Saved on the Quad Cortex")
        elif origin == "revised":
            parts.append("Revised: " + sound.origin["request"][:60] if sound.origin.get("request") else "A copy to work on")
        if self.studio.unheard(sound):
            parts.append("could not be listened to")
        elif summary(sound.heard):
            parts.append("measured: " + summary(sound.heard))
        if sound.knobs:
            parts.append("shaped")
        if sound.saved:
            parts.append(f"saved as {sound.saved['name']}")
        return " · ".join(parts)

    def draw_scenes(self):
        scenes = self.studio.scenes()
        key = (tuple(scenes), self.studio.state.scene, self.studio.state.side)
        if key == self.scene_key:
            return
        self.scene_key = None               # until they are drawn whole
        for child in self.scene_row.winfo_children():
            child.destroy()
        if scenes:
            label(self.scene_row, "Scene", size=9, color=DIM).pack(side="left", padx=(0, 6))
        for scene, name in scenes:
            chosen = scene == self.studio.state.scene
            chip = tk.Label(self.scene_row, text=f"{'ABCDEFGH'[scene]}{'  ' + name if name else ''}", padx=8, pady=3,
                            cursor="hand2", font=(FONT, 9, "bold" if chosen else "normal"),
                            bg=mix(SIDE[self.studio.state.side], DARK, .3) if chosen else TILE,
                            fg=INK if chosen else SOFT)
            chip.pack(side="left", padx=(0, 4))
            chip.bind("<ButtonRelease-1>", lambda _, scene=scene: self.studio.scene(scene))
        self.scene_key = key

    def draw_strip(self, on_b=None):
        """The round on show: its three designs as chips, the next and the earlier round, and what it aims for."""
        studio = self.studio
        found = None if studio.state.blind else studio.round()
        # The chips are made again only when what they show has changed: a chip that is replaced between the
        # press and the release of a click loses the click.
        key = found and (found["id"], found.get("next"), found.get("previous"), studio.state.b,
                         on_b and (summary(on_b.heard), studio.unheard(on_b)))
        if key == self.strip_key:
            return
        self.strip_key = None               # until they are drawn whole: a fault on the way is met again, not hidden
        for child in self.chips.winfo_children():
            child.destroy()
        self.strip_note.configure(text="")
        if not found:
            self.strip_key = key
            return
        if found.get("previous"):
            back = tk.Label(self.chips, text="‹", padx=6, pady=5, cursor="hand2", font=(FONT, 10), bg=PAPER, fg=SOFT)
            back.pack(side="left")
            back.bind("<ButtonRelease-1>", lambda _: studio.round_back())
        for place, candidate in enumerate(found["candidates"]):
            sound = studio.store.sound(candidate["sound"])
            chosen = candidate["sound"] == studio.state.b
            chip = tk.Label(self.chips, text=f"{place + 1}  {sound.name}", padx=10, pady=5, cursor="hand2",
                            font=(FONT, 9, "bold" if chosen else "normal"),
                            bg=mix(SIDE["b"], DARK, .3) if chosen else TILE, fg=INK if chosen else SOFT)
            chip.pack(side="left", padx=(0, 5))
            chip.bind("<ButtonRelease-1>", lambda _, place=place: studio.step(place))
            if chosen:
                heard = (" · could not be listened to" if studio.unheard(sound)
                         else " · measured: " + summary(sound.heard) if summary(sound.heard)
                         else " · not listened to yet")
                self.strip_note.configure(text=candidate["differs"] + heard)
        ready = bool(found.get("next"))
        more = tk.Label(self.chips, text="›  next three" if ready else "Three more", padx=10, pady=5, cursor="hand2",
                        font=(FONT, 9, "bold" if ready else "normal"), bg=PAPER, fg=ACCENT if ready else SOFT)
        more.pack(side="left", padx=(4, 0))
        more.bind("<ButtonRelease-1>", lambda _: studio.round_forward())
        aim = found["aim"]
        song = aim.get("song")
        part = f"{song.get('title')}{' · ' + song['part'] if song.get('part') else ''} · " if song else ""
        label(self.chips, f"{part}aiming for {summary(aim)}", size=9, color=DIM).pack(side="right")
        self.strip_key = key

    # ---- panels ----

    def toggle(self, name):
        if self.panel == name:
            self.panel = None
            self.studio.remember(panel=None)
            self.tray.grid_remove()
            return
        self.panel = name
        self.studio.remember(panel=name)
        self.tray.grid(row=0, column=1, sticky="ns", padx=(14, 0))
        self.fill_panel()

    def fill_panel(self):
        self.panel_key = self.drawn_from()
        self.dragging = False
        for child in self.tray.winfo_children():
            child.destroy()
        label(self.tray, self.panel, size=11, bold=True).pack(fill="x", pady=(0, 6))
        getattr(self, f"panel_{self.panel.lower()}")()

    def drag_ends(self):
        """A drag in the panel is over: the panel is drawn again if what it shows changed meanwhile."""
        self.dragging = False
        if self.panel and self.drawn_from() != self.panel_key:
            self.fill_panel()

    def panel_shape(self):
        studio = self.studio
        sound = studio.sound(studio.state.side)
        if sound is None:
            label(self.tray, "Nothing to shape yet.", color=SUBTLE).pack(fill="x")
            return
        values = dict(sound.knobs)
        available = studio.available()
        if set(knob_table.PAD) & set(available):
            self.shape_pad(values, [key for key in knob_table.PAD if key in available])
        for key in available:
            if key in knob_table.PAD:
                continue
            title, low, _ = knob_table.KNOBS[key]
            row = tk.Frame(self.tray, bg=DARK)
            row.pack(fill="x")
            label(row, title, size=9, color=SOFT).pack(side="left")
            scale = tk.Scale(row, from_=low, to=1, resolution=.05, orient="horizontal", length=230, showvalue=False,
                             bg=DARK, fg=INK, troughcolor=TILE, highlightthickness=0, borderwidth=0)
            scale.set(values.get(key, 0.))
            scale.pack(side="right")
            scale.bind("<Button-1>", lambda _: setattr(self, "dragging", True), add="+")
            scale.bind("<ButtonRelease-1>", lambda _, key=key, scale=scale: self.turned(key, scale.get()))
        actions = tk.Frame(self.tray, bg=DARK)
        actions.pack(fill="x", pady=(10, 0))
        button(actions, "Undo", studio.undo).pack(side="left")
        without = studio.state.side in studio.hearing_base
        button(actions, "Hear it with the knobs again" if without else "Hear it without the knobs",
               studio.base).pack(side="left", padx=6)

    def shape_pad(self, values, usable):
        """Brightness and Texture as one point on a square: dark to bright across, round to rough up. Drag the
        point; letting go turns both knobs. An axis the sound has nothing for stays at its centre, dimmed."""
        size = PAD_SIZE
        across, up = knob_table.PAD
        point = {key: float(values.get(key, 0.)) for key in knob_table.PAD}
        canvas = tk.Canvas(self.tray, width=size, height=size, bg=TILE, highlightthickness=0, cursor="crosshair")
        canvas.pack(pady=(2, 4))
        canvas.create_line(size / 2, 0, size / 2, size, fill=RULE)
        canvas.create_line(0, size / 2, size, size / 2, fill=RULE)
        for text, x, y, anchor, key in (("dark", 6, size / 2 - 4, "sw", across),
                                        ("bright", size - 6, size / 2 - 4, "se", across),
                                        ("rough", size / 2 + 6, 6, "nw", up),
                                        ("round", size / 2 + 6, size - 6, "sw", up)):
            canvas.create_text(x, y, text=text, anchor=anchor, fill=SUBTLE if key in usable else DIM, font=(FONT, 8))
        radius = 6
        x, y = pad_point(point[across], point[up], size)
        dot = canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=ACCENT, outline="")
        note = label(self.tray, size=9, color=SUBTLE)
        note.pack(fill="x", pady=(0, 8))

        def show():
            note.configure(text=f"{knob_table.KNOBS[across][0]} {point[across]:+.2f} · "
                                f"{knob_table.KNOBS[up][0]} {point[up]:+.2f}")

        def drag(event):
            self.dragging = True
            found = dict(zip(knob_table.PAD, pad_values(event.x, event.y, size)))
            point.update({key: found[key] for key in usable})
            x, y = pad_point(point[across], point[up], size)
            canvas.coords(dot, x - radius, y - radius, x + radius, y + radius)
            show()

        def done(event):
            drag(event)
            self.dragging = False
            sound = self.studio.sound(self.studio.state.side)
            if sound is not None:
                self.studio.turn({**sound.knobs, **{key: point[key] for key in usable}})

        canvas.bind("<Button-1>", drag)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", done)
        show()

    def turned(self, key, value):
        self.dragging = False
        sound = self.studio.sound(self.studio.state.side)
        if sound is not None:
            self.studio.turn({**sound.knobs, key: value})

    def panel_chain(self):
        studio = self.studio
        sound = studio.sound(studio.state.side)
        if studio.state.blind:
            label(self.tray, "Hidden while you compare blind.", color=SUBTLE).pack(fill="x")
            return
        if sound is None:
            label(self.tray, "Nothing here yet.", color=SUBTLE).pack(fill="x")
            return
        label(self.tray, f"{studio.state.side.upper()} · {sound.name}", size=12, bold=True, wrap=400).pack(fill="x")
        if sound.explanation:
            label(self.tray, sound.explanation, size=9, color=SOFT, wrap=400).pack(fill="x", pady=(2, 6))
        from lab.knowledge import named_settings
        from lab.sound.sounds import settings as stated

        for r, row in enumerate(sound.chain.rows):
            if not row.blocks:
                continue
            label(self.tray, f"Row {r + 1}" + (f" · split at {row.split[0] + 1}, joined at {row.split[1] + 1}"
                                                 if row.split else ""), size=8, color=DIM).pack(fill="x", pady=(4, 0))
            for column, block in row.blocks:
                line = tk.Frame(self.tray, bg=DARK)
                line.pack(fill="x")
                shown = stated(block)
                text = block.name + (" · " + ", ".join(f"{key} {value:g}" if isinstance(value, float) else
                                                       f"{key} {value}" for key, value in list(shown.items())[:6])
                                     if shown else "")
                label(line, text, size=9, wrap=330).pack(side="left", fill="x", expand=True)
                names = named_settings(block.model)
                if names:
                    place = (r, column)
                    opened = place in self.expanded
                    toggle = tk.Label(line, text="named settings ▴" if opened else "named settings ▾", bg=DARK,
                                      fg=ACCENT, font=(FONT, 8), cursor="hand2")
                    toggle.pack(side="right")
                    toggle.bind("<ButtonRelease-1>", lambda _, place=place: self.expand(place))
                    if opened:
                        choices = tk.Frame(self.tray, bg=DARK)
                        choices.pack(fill="x", padx=(12, 0), pady=(0, 4))
                        for name in names:
                            chip = tk.Label(choices, text=name, bg=TILE, fg=SOFT, font=(FONT, 8), padx=6, pady=2,
                                            cursor="hand2")
                            chip.pack(side="left", padx=(0, 4), pady=2)
                            chip.bind("<ButtonRelease-1>", lambda _, r=r, column=column, name=name:
                                      studio.named(r, column, name))
        from lab.hearing.measure import measured_lines

        label(self.tray, "Measured", size=8, color=DIM).pack(fill="x", pady=(10, 0))
        why = studio.unheard(sound)
        for line in ([f"It could not be listened to as it is set now: {why}"] if why else measured_lines(sound.heard)):
            label(self.tray, line, size=9, color=SOFT, wrap=400).pack(fill="x", pady=1)
        nano = tk.Label(self.tray, text="Nano Cortex sheet ▴" if self.show_nano else "Nano Cortex sheet ▾", bg=DARK,
                        fg=ACCENT, font=(FONT, 9), cursor="hand2")
        nano.pack(anchor="w", pady=(8, 2))
        nano.bind("<ButtonRelease-1>", lambda _: self.toggle_nano())
        if self.show_nano:
            from lab.gear.nano import sheet

            for line in sheet(sound.chain).lines():
                label(self.tray, line, size=9, color=SOFT, wrap=400).pack(fill="x")
        for note in sound.notes[-4:]:
            label(self.tray, f"You: {note['text']}", size=9, color=SUBTLE, wrap=400).pack(fill="x")

    def expand(self, place):
        self.expanded ^= {place}
        self.fill_panel()

    def toggle_nano(self):
        self.show_nano = not self.show_nano
        self.fill_panel()

    def panel_difference(self):
        for line in self.studio.difference():
            label(self.tray, line, size=10, wrap=400).pack(fill="x", pady=2)

    def panel_phrase(self):
        """The phrases recorded, recording a new one with its pickup setting, and the loop: drag its edges."""
        import numpy as np

        studio = self.studio
        current = studio.phrase[0] if studio.phrase else None
        for item in studio.recorded_phrases()[:8]:
            chosen = item["id"] == current
            row = tk.Label(self.tray, text=f"{item.get('instrument', '')} · {item.get('setting', '')} · "
                                           f"{str(item.get('recorded', ''))[5:16]}",
                           bg=TILE if chosen else DARK, fg=INK if chosen else SOFT, font=(FONT, 9), anchor="w",
                           padx=6, pady=2, cursor="hand2")
            row.pack(fill="x")
            row.bind("<ButtonRelease-1>", lambda _, item=item: (self.choose_recorded(item), self.fill_panel()))
        self.recording_controls()
        whole = studio.phrase_whole
        if whole is None:
            return
        audio, start, end = whole
        width, height = 400, 120
        canvas = tk.Canvas(self.tray, width=width, height=height, bg=TILE, highlightthickness=0, cursor="sb_h_double_arrow")
        canvas.pack(fill="x", pady=(10, 0))
        columns = np.array_split(np.abs(audio[:, 0]), width)
        peak = max(float(np.abs(audio).max()), 1e-6)
        for x, part in enumerate(columns):
            value = float(part.max()) / peak if len(part) else 0.
            canvas.create_line(x, height / 2 - value * height / 2, x, height / 2 + value * height / 2, fill=SOFT)
        shade = canvas.create_rectangle(0, 0, 0, height, fill=ACCENT, stipple="gray25", width=0)
        seconds = len(audio) / RATE
        note = label(self.tray, size=9, color=SUBTLE)
        note.pack(fill="x", pady=(4, 0))
        edges = {"now": (start, end)}

        def draw():
            first, last = edges["now"]
            canvas.coords(shade, first / len(audio) * width, 0, last / len(audio) * width, height)
            note.configure(text=f"Loop {first / RATE:.1f}–{last / RATE:.1f} s of {seconds:.1f} s. "
                                "Drag either edge; the recording itself is never changed.")

        def drag(event):
            self.dragging = True
            at = int(min(max(event.x, 0), width) / width * len(audio))
            edges["now"] = loop_edges(edges["now"], at, min(SHORTEST_LOOP, len(audio)))
            draw()

        def done(_):
            if edges["now"] != (start, end) and studio.phrase:
                self.dragging = False
                studio.use_phrase(studio.phrase[0], audio, *edges["now"])
            else:
                self.drag_ends()

        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", done)
        draw()

    def panel_sounds(self):
        """What is on the unit: presets to work on, and the player's IRs and captures to try in B."""
        kinds = tk.Frame(self.tray, bg=DARK)
        kinds.pack(fill="x", pady=(0, 6))
        for kind in ("Presets", "IRs", "Captures"):
            chosen = kind == self.sounds_kind
            tab = tk.Label(kinds, text=kind, padx=8, pady=3, cursor="hand2", font=(FONT, 9, "bold" if chosen else "normal"),
                           bg=TILE if chosen else DARK, fg=INK if chosen else SUBTLE)
            tab.pack(side="left", padx=(0, 4))
            tab.bind("<ButtonRelease-1>", lambda _, kind=kind: (setattr(self, "sounds_kind", kind), self.fill_panel()))
        if self.sounds_kind != "Presets":
            self.panel_assets("ir" if self.sounds_kind == "IRs" else "capture")
            return
        from lab.qc.library import cached, place

        entries = cached(self.studio.folder)
        top = tk.Frame(self.tray, bg=DARK)
        top.pack(fill="x", pady=(0, 6))
        button(top, "Read the unit again", self.read_directory).pack(side="right")
        if not entries:
            label(top, "Not read yet." if entries is None else "No presets of yours on the unit.",
                  color=SUBTLE).pack(side="left")
            return
        search = tk.Entry(top, font=(FONT, 10), bg=TILE, fg=INK, relief="flat", insertbackground=INK)
        search.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 8))
        box = tk.Listbox(self.tray, bg=DARK, fg=INK, selectbackground=TILE, relief="flat", highlightthickness=0,
                         font=(FONT, 9), height=24)
        box.pack(fill="both", expand=True)
        rows = []

        def fill(*_):
            wanted = search.get().strip().casefold()
            rows[:] = [(item["slot"], item["name"], item["setlist"]) for item in entries
                       if wanted in f"{item['name']} {item['setlist']}".casefold()]
            box.delete(0, "end")
            for slot, name, setlist in rows:
                box.insert("end", f"{name}  ·  {setlist} {place(slot['position'])}")

        fill()
        search.bind("<KeyRelease>", fill)
        box.bind("<Double-Button-1>", lambda _: self.work_on(box, rows))
        button(self.tray, "Work on this", lambda: self.work_on(box, rows)).pack(fill="x", pady=(6, 0))

    def panel_assets(self, kind):
        from lab.qc.assets import cached

        found = [item for item in cached(self.studio.folder) or [] if item.kind == kind]
        top = tk.Frame(self.tray, bg=DARK)
        top.pack(fill="x", pady=(0, 6))
        button(top, "Read the unit again", lambda: self.studio.read_assets(lambda _: self.refill("Sounds"))).pack(
            side="right")
        if not found:
            label(top, "Not read yet.", color=SUBTLE).pack(side="left")
            return
        search = tk.Entry(top, font=(FONT, 10), bg=TILE, fg=INK, relief="flat", insertbackground=INK)
        search.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 8))
        mine = tk.BooleanVar(value=True)
        box = tk.Listbox(self.tray, bg=DARK, fg=INK, selectbackground=TILE, relief="flat", highlightthickness=0,
                         font=(FONT, 9), height=22)
        rows = []

        def fill(*_):
            wanted = search.get().strip().casefold()
            rows[:] = [item for item in found if wanted in item.name.casefold()
                       and (item.source == "user" or not mine.get())]
            box.delete(0, "end")
            for item in rows[:400]:
                version = f"  · V{item.capture_version}" if item.capture_version else ""
                box.insert("end", item.name + version)

        tk.Checkbutton(self.tray, text="Only mine", variable=mine, command=fill, bg=DARK, fg=SOFT,
                       selectcolor=TILE, activebackground=DARK, font=(FONT, 9)).pack(anchor="w")
        box.pack(fill="both", expand=True)
        fill()
        search.bind("<KeyRelease>", fill)

        def chosen():
            picked = box.curselection()
            if not picked:
                self.studio.say("Choose one in the list first.")
            return rows[picked[0]] if picked else None

        def use(**options):
            asset = chosen()
            if asset is not None:
                self.studio.use_asset(asset, **options)

        actions = tk.Frame(self.tray, bg=DARK)
        actions.pack(fill="x", pady=(6, 0))
        if kind == "ir":
            button(actions, "Put in B's cab", use).pack(side="left")
        else:
            versions = tk.Frame(self.tray, bg=DARK)
            versions.pack(fill="x", pady=(6, 0))
            label(versions, "If the unit does not say, the capture is", size=9, color=SUBTLE).pack(side="left")
            for value in (1, 2):
                chosen_version = value == self.capture_version
                chip = tk.Label(versions, text=f"V{value}", bg=TILE if chosen_version else DARK,
                                fg=INK if chosen_version else SOFT, font=(FONT, 9), padx=6, cursor="hand2")
                chip.pack(side="left", padx=(6, 0))
                chip.bind("<ButtonRelease-1>", lambda _, value=value: (setattr(self, "capture_version", value),
                                                                       self.fill_panel()))
            button(actions, "Instead of B's amp", lambda: self.try_capture(chosen(), False)).pack(side="left")
            button(actions, "Instead of amp and cab", lambda: self.try_capture(chosen(), True)).pack(
                side="left", padx=6)

    def try_capture(self, asset, has_cab):
        if asset is None:
            return
        self.studio.use_asset(asset, has_cab=has_cab, version=asset.capture_version or self.capture_version)

    def refill(self, panel):
        """Draw a panel again, if it is the one open."""
        if self.panel == panel:
            self.fill_panel()

    def read_directory(self):
        self.studio.read_directory(lambda entries: self.refill("Sounds"))

    def work_on(self, box, rows):
        chosen = box.curselection()
        if not chosen:
            self.studio.say("Choose a preset in the list first.")
            return
        slot, name, _ = rows[chosen[0]]
        self.studio.work_on(slot, name)

    def panel_history(self):
        """Songs and their parts, collected from requests for songs; what the player's choices say; then every
        sound kept, newest first, to put on A or B."""
        studio = self.studio
        for song in studio.store.songs():
            parts = []
            for part in song.get("parts", []):
                try:
                    parts.append(f"{part['name']}: {studio.store.sound(part['sound']).name}")
                except Exception:  # noqa: BLE001  a part whose sound is gone is not listed
                    continue
            if parts:
                label(self.tray, f"{song['name']} · " + ", ".join(parts), size=9, wrap=400).pack(fill="x", pady=1)
        for line in studio.taste_lines():
            label(self.tray, line, size=9, color=SOFT, wrap=400).pack(fill="x", pady=1)
        rows = history_rows(studio.folder)
        box = tk.Listbox(self.tray, bg=DARK, fg=INK, selectbackground=TILE, relief="flat", highlightthickness=0,
                         font=(FONT, 9), height=24)
        box.pack(fill="both", expand=True)
        for _, name, kind, saved in rows:
            box.insert("end", f"{name}  ·  {kind}" + ("  ★" if saved else ""))

        def put(side):
            chosen = box.curselection()
            if not chosen:
                studio.say("Choose a sound in the list first.")
                return
            # As it is on disk now: what was listed may have been measured or marked since.
            studio.put(side, studio.store.sound(rows[chosen[0]][0]))

        row = tk.Frame(self.tray, bg=DARK)
        row.pack(fill="x", pady=(6, 0))
        for side in ("a", "b"):
            button(row, f"Put on {side.upper()}", lambda side=side: put(side)).pack(side="left", padx=(0, 6))

    # ---- actions ----

    def toggle_play(self, mode):
        """Guitar or Phrase was pressed: start it, or stop it when it is what plays. While something else has the
        audio input (`Studio.audio_held_by`), the studio says who instead of starting."""
        self.play_when_free = None
        if self.studio.state.mode == mode:
            self.studio.stop()
            return
        if mode == "phrase":
            # The phrase loop and gesture listening would both want the audio driver.
            from lab.learner import gestures

            if gestures.running():
                gestures.stop()
                self.studio.say("Gestures off while the phrase plays.")
            if self.studio.audio_held_by == "gestures":
                self.play_when_free = mode      # it lets go within a tick or two: the phrase starts then
                return
        self.studio.play(mode)

    def ask(self):
        text = self.request.get()
        self.request.delete(0, "end")
        self.root.focus_set()
        self.studio.ask(text)

    def save(self):
        """Show the name to save under, in place, filled in with the sound's own."""
        sound = self.studio.sound(self.studio.state.side)
        if sound is None:
            self.studio.say(f"Nothing is on {self.studio.state.side.upper()} to save.")
            return
        # The name is for this sound with these knobs, whatever is selected by the time it is confirmed.
        self.save_target = (sound.id, dict(sound.knobs))
        self.save_name.delete(0, "end")
        if not self.studio.state.blind:     # filled in with the name only when it is not hidden
            self.save_name.insert(0, sound.name[:24])
        self.save_row.pack(fill="x", pady=(8, 0), after=self.save_row_after)
        self.save_name.focus_set()

    def saved(self):
        name = self.save_name.get().strip()[:24]
        if not name:
            self.studio.say("Type a name to save under.")
            return
        self.save_row.pack_forget()
        target, self.save_target = self.save_target, None
        if target:
            self.studio.save(name, sound_id=target[0], knob_values=target[1])

    def recording_controls(self):
        """Record a phrase: the pickup setting it is played on, then Record (eight seconds, from the guitar)."""
        instrument = self.studio.store.instrument(self.studio.state.instrument) or {}
        settings = [f"{position} · {wiring}".rstrip(" ·") for position in instrument.get("settings") or ["Pickup"]
                    for wiring in (instrument.get("wiring") or [""])]
        label(self.tray, f"Record a phrase on the {self.studio.state.instrument}, played on:", size=9,
              color=SUBTLE).pack(fill="x", pady=(10, 2))
        grid = tk.Frame(self.tray, bg=DARK)
        grid.pack(fill="x")
        for index, setting in enumerate(settings):
            chosen = setting == self.record_setting
            chip = tk.Label(grid, text=setting, bg=TILE if chosen else DARK, fg=INK if chosen else SOFT, font=(FONT, 8),
                            padx=6, pady=2, cursor="hand2")
            chip.grid(row=index // 3, column=index % 3, sticky="w", padx=(0, 4), pady=1)
            chip.bind("<ButtonRelease-1>", lambda _, setting=setting: (setattr(self, "record_setting", setting),
                                                                       self.fill_panel()))
        go = button(self.tray, f"Record {RECORD_SECONDS:g} s" if self.record_setting else "Choose the setting first",
                    lambda: self.record_setting and self.studio.record(self.record_setting, RECORD_SECONDS))
        go.pack(anchor="w", pady=(4, 0))

    def choose_recorded(self, item, start=None, end=None):
        """Make a recorded take the phrase: the part of it kept from before, or its loudest few seconds."""
        whole = read_wave(self.studio.folder / "phrases" / item["id"] / "dry.wav")[:, :1]
        self.studio.use_phrase(item["id"], whole, start, end)

    def panel_guitar(self):
        row = tk.Frame(self.tray, bg=DARK)
        row.pack(fill="x", pady=(0, 8))
        button(row, "Guitar Learner", self.open_learner).pack(side="left")
        button(row, "Input calibration", self.open_calibration).pack(side="left", padx=6)
        from lab.learner import gestures

        button(row, "Gestures off" if gestures.running() else "Gestures on",
               lambda: (self.toggle_gestures(), self.refill("Guitar"))).pack(side="left")
        label(self.tray, f"What the {self.studio.state.instrument} puts out", size=10, bold=True).pack(fill="x")
        # Worked out away from the window (reading the takes takes a second or more); the panel is drawn again
        # when it arrives.
        for line in self.studio.guitar_lines():
            label(self.tray, line, size=9, wrap=400).pack(fill="x", pady=2)
        self.recording_controls()

    def panel_more(self):
        """The side tools, the instrument, and measuring the USB round trip."""
        from lab.tools.menu import TOOLS, opener

        label(self.tray, "Tools", size=9, color=SUBTLE).pack(fill="x")
        grid = tk.Frame(self.tray, bg=DARK)
        grid.pack(fill="x", pady=(2, 10))
        for index, (title, name) in enumerate(TOOLS):
            chip = tk.Label(grid, text=title, bg=TILE, fg=INK, font=(FONT, 9), padx=8, pady=4, cursor="hand2")
            chip.grid(row=index // 2, column=index % 2, sticky="we", padx=(0, 4), pady=2)
            chip.bind("<ButtonRelease-1>", lambda _, name=name: opener(name)(self))
        label(self.tray, "Instrument", size=9, color=SUBTLE).pack(fill="x")
        row = tk.Frame(self.tray, bg=DARK)
        row.pack(fill="x", pady=(2, 10))
        for item in self.studio.store.instruments():
            chosen = item["name"] == self.studio.state.instrument
            chip = tk.Label(row, text=item["name"], bg=TILE if chosen else DARK, fg=INK if chosen else SOFT,
                            font=(FONT, 9), padx=8, pady=3, cursor="hand2")
            chip.pack(side="left", padx=(0, 4))
            chip.bind("<ButtonRelease-1>", lambda _, name=item["name"]: self.studio.set(instrument=name))
        button(self.tray, "Measure the USB round trip", self.studio.measure_round_trip).pack(anchor="w")

    def learner_seat(self):
        return self.seat or LearnerSeat(self)

    def open_calibration(self):
        from lab.learner import calibration

        calibration.open(self.learner_seat())

    def toggle_gestures(self):
        from lab.learner import gestures

        if gestures.running():
            gestures.stop()
            self.studio.say("Gestures off.")
            return
        try:
            gestures.start(self.learner_seat())
        except ValueError as error:
            self.studio.say(f"Gestures: {error}")
            return
        self.studio.say("Gestures on: play, and the mapped control turns the knob on the sound on show.")

    def open_learner(self):
        """The Guitar Learner, in a window of its own, seated on this desk."""
        from lab.learner import GuitarLearner

        if self.learner is None:
            self.learner = GuitarLearner(self.learner_seat())
        self.learner.show()

    def failed(self, kind, error, trace):
        """A fault in a click: logged with its trace, and said on the line."""
        self.studio.log_error(error, "".join(traceback.format_exception(kind, error, trace)))
        self.studio.say(f"Something went wrong: {error}. The details are in errors.log in the data folder.")

    def tick(self):
        try:
            self.studio.poll()
            if self.seat is not None:
                self.seat.tick()
            if self.play_when_free and not self.studio.audio_held_by:
                mode, self.play_when_free = self.play_when_free, None
                self.studio.play(mode)
            self.come_forward()
        finally:
            self.clock = self.root.after(80, self.tick)

    def answer_second_launch(self):
        import socket

        try:
            self.door = socket.socket()
            self.door.bind(("127.0.0.1", PORT))
            self.door.listen(2)
            self.door.setblocking(False)
        except OSError:
            self.door = None

    def come_forward(self):
        """A second launch knocked: say that a desk is here (so that it leaves), and come forward."""
        import socket

        if self.door is None:
            return
        try:
            knock, _ = self.door.accept()
        except (BlockingIOError, OSError):
            return
        try:
            knock.settimeout(.3)
            knock.sendall(HERE)
            knock.shutdown(socket.SHUT_WR)
            while knock.recv(64):           # what the launch sent is read, so that closing does not cut the answer off
                pass
        except OSError:
            pass
        knock.close()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def close(self):
        if self.closing:
            return
        self.closing = True
        if self.seat is not None:
            self.seat.leaving = True
            if self.seat.stop_signal is not None:
                self.seat.stop_signal.set()
        self.studio.begin("close", "Giving the Quad Cortex back…")
        self.root.update()
        try:
            self.studio.close()
        finally:
            # The door closes with the window: a process that outlives it must not look like an open desk.
            if self.door is not None:
                self.door.close()
                self.door = None
            self.root.destroy()


def main(arguments):
    from lab.qc.host import Host

    simulated = "--simulated" in arguments
    if not simulated and not only_desk():
        return
    folder = home() / ("simulated" if simulated else "")
    folder.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    notice = first_run(folder) if not simulated else ""
    host = Host(folder, simulated=simulated)
    studio = Studio(host, folder)
    studio.notice = notice
    desk = Desk(root, studio)
    if not simulated:
        desk.answer_second_launch()
    root.mainloop()


PORT = 47231            # on this computer only: a second desk asks the first to come forward, and leaves
HERE = b"Tone Lab desk"  # what a desk answers a knock with: anything else on that port is not a desk
WAIT_FOR_CLOSING = 20.  # seconds a new launch waits for a desk that holds the door without answering


def only_desk():
    """True when no other desk is open. Otherwise ask that one to come forward and say no: two desks would fight
    over the unit. Only a desk's own answer counts: another program on that port, or a desk that is on its way
    out and no longer answers, does not keep this one from opening."""
    import socket
    import time

    # A desk answers within a tick. One that is closing (it is giving the unit back, which can take a while) still
    # holds the door without answering: wait for it to go rather than open beside it.
    deadline = time.monotonic() + WAIT_FOR_CLOSING
    while True:
        try:
            other = socket.create_connection(("127.0.0.1", PORT), timeout=.5)
        except OSError:
            return True                     # nobody there
        try:
            with other:
                other.settimeout(2.)          # the knock is the connection itself: a desk answers it and comes forward
                if other.recv(len(HERE)) == HERE:
                    return False
        except OSError:
            pass
        if time.monotonic() > deadline:
            return True                     # something else has that port
        time.sleep(.5)


def first_run(folder):
    """Once: check what the app needs, bring the earlier app's history in, and return what to say about it."""
    from lab import migrate, setup

    if not setup.first_run_needed(folder):
        return ""
    findings = setup.checks(folder)
    try:
        moved = migrate.summary(migrate.migrate(folder))[0]
    except Exception as error:  # noqa: BLE001
        moved = f"The earlier app's history could not be brought in: {error}"
    setup.record(folder, findings)
    missing = [item["say"] for item in findings if not item["ok"]]
    return " ".join(missing) + " " + moved if missing else moved


if __name__ == "__main__":
    main(sys.argv[1:])
