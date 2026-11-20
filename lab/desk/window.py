"""The desk: the window over `Studio`. It draws the state and turns clicks and keys into actions.

Run it with `python -m lab` (the Quad Cortex) or `python -m lab --simulated`
(no hardware). It is the same quiet desk as before: two sounds, the input,
one line that speaks, one place to ask, and panels that open when called.
Nothing here decides anything; `studio.py` does.
"""

import json
from pathlib import Path
import sys
import tkinter as tk
from tkinter import simpledialog

from lab.sound import knobs as knob_table
from lab.hearing.audio import read_wave
from lab.knowledge import REPO, home
from lab.hearing.measure import summary
from lab.desk.studio import Studio
from lab.desk.theme import (ACCENT, DARK, DIM, FONT, INK, LIVE, PAPER, RULE, SIDE, SOFT, SUBTLE, TILE, Tooltip, mix)

PANELS = ("Shape", "Chain", "Difference", "Phrase", "Sounds", "History", "Song", "Guitar")
PHRASE_SECONDS = 3.5
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


def phrases():
    """The recorded takes, newest first, as (name, dry wav path, what is known of how it was played)."""
    found = []
    for take in sorted((REPO / "tone_sessions" / "recordings").glob("clip-*"), reverse=True):
        if (take / "dry.wav").is_file():
            try:
                info = json.loads((take / "clip.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            found.append((take.name, take / "dry.wav", info))
    return found


def loudest_at(dry, seconds=PHRASE_SECONDS):
    """Where the loudest `seconds` of a take start, and how long they are."""
    import numpy as np

    length = round(seconds * 48000)
    if len(dry) <= length:
        return 0, len(dry)
    energy = np.concatenate(([0.], np.cumsum(np.square(dry[:, 0], dtype=np.float64))))
    return int(np.argmax(energy[length:] - energy[:-length])), length


def loudest(path, seconds=PHRASE_SECONDS):
    dry = read_wave(path)[:, :1]
    start, length = loudest_at(dry, seconds)
    return dry[start:start + length].copy()


class Desk:
    def __init__(self, root, studio):
        self.root, self.studio = root, studio
        self.panel = self.learner = None
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
        root.bind("<Key>", self.key)
        root.protocol("WM_DELETE_WINDOW", self.close)
        recorded = studio.recorded_phrases()
        if recorded:
            self.choose_recorded(recorded[0])
        studio.open()
        self.tick()

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
        more = tk.Menubutton(top, text="⋯", bg=PAPER, fg=SUBTLE, relief="flat", font=(FONT, 14), cursor="hand2")
        menu = tk.Menu(more, tearoff=False, bg="#252834", fg=INK)
        menu.add_command(label="Guitar Learner", command=self.open_learner)
        menu.add_command(label="Guitar input calibration", command=self.open_calibration)
        menu.add_command(label="Gestures on / off", command=self.toggle_gestures)
        menu.add_separator()
        from lab.tools.menu import TOOLS, opener

        for title, name in TOOLS:
            menu.add_command(label=title, command=lambda name=name: opener(name)(self))
        menu.add_separator()
        menu.add_command(label="Measure the USB round trip", command=self.calibrate)
        instruments = tk.Menu(menu, tearoff=False, bg="#252834", fg=INK)
        for item in self.studio.store.instruments():
            instruments.add_command(label=item["name"], command=lambda name=item["name"]: self.studio.set(instrument=name))
        menu.add_cascade(label="Instrument", menu=instruments)
        more.configure(menu=menu)
        more.pack(side="right")
        for name in PANELS[::-1]:
            link = tk.Label(top, text=name, bg=PAPER, fg=SUBTLE, font=(FONT, 10), cursor="hand2", padx=8)
            link.pack(side="right")
            link.bind("<ButtonRelease-1>", lambda _, name=name: self.toggle(name))

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
            marks = label(tile, size=9, color=ACCENT, wrap=320)
            air = label(tile, size=8, color=LIVE, bold=True)
            air.pack(side="top", fill="x")
            marks.pack(side="bottom", fill="x", pady=(4, 0))
            caption.pack(side="bottom", fill="x")
            name.pack(side="bottom", fill="x")
            badge.pack(side="bottom", fill="x")
            for widget in (tile, badge, name, caption, air, marks):
                widget.bind("<ButtonRelease-1>", lambda _, side=side: self.studio.select(side))
                widget.bind("<Button-3>", lambda event, side=side: self.tile_menu(event, side))
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
        Tooltip(self.guitar, "Play through the selected sound  ·  G")
        Tooltip(self.loop, "Loop the recorded phrase through it  ·  P")
        self.phrase_menu = tk.Menubutton(row, text="No phrase", bg=PAPER, fg=SUBTLE, relief="flat", font=(FONT, 9),
                                         cursor="hand2")
        options = tk.Menu(self.phrase_menu, tearoff=False, bg="#252834", fg=INK,
                          postcommand=lambda: self.list_phrases(options))
        self.phrase_menu.configure(menu=options)
        self.phrase_menu.pack(side="right")
        button(row, "Save to QC", self.save).pack(side="right", padx=8)

        self.line = label(main, size=10, color=INK, wrap=640)
        self.line.pack(fill="x", pady=(8, 4))
        ask = tk.Frame(main, bg=DARK, highlightthickness=1, highlightbackground=RULE)
        ask.pack(fill="x")
        self.request = tk.Entry(ask, font=(FONT, 12), bg=DARK, fg=INK, relief="flat", insertbackground=INK)
        self.request.pack(side="left", fill="x", expand=True, ipady=9, padx=12)
        self.request.bind("<Return>", lambda _: self.ask())
        self.request.bind("<Escape>", lambda _: self.studio.cancel() or self.root.focus_set())
        label(ask, "/", size=9, color=DIM).pack(side="right", padx=12)

    # ---- drawing ----

    def draw(self):
        state = self.studio.state
        self.working.configure(text=state.working)
        self.dot.itemconfigure(self.dot_mark, fill=LIVE if state.mode else (ACCENT if state.working else DIM))
        for side in ("a", "b"):
            tile, name, caption, air, marks = self.tiles[side]
            sound = self.studio.sound(side)
            hidden = state.blind and sound is not None
            name.configure(text="?" if hidden else (sound.name if sound else "Empty"))
            caption.configure(text="Hidden while you compare · X shows them" if hidden else self.caption(sound))
            marks.configure(text="" if hidden or sound is None else "  ".join(f"● {mark}" for mark in sound.marks))
            selected = state.side == side
            on_air = state.mode and selected
            tile.configure(highlightbackground=SIDE[side] if selected else DARK,
                           bg=mix(SIDE[side], DARK, .14) if on_air else DARK)
            for widget in (name, caption, air, marks):
                widget.configure(bg=tile["bg"])
            air.configure(text=f"● {state.mode}" if on_air else "")
        self.line.configure(text=state.status)
        self.guitar.configure(bg=mix(LIVE, TILE, .4) if state.mode == "guitar" else TILE)
        self.loop.configure(bg=mix(LIVE, TILE, .4) if state.mode == "phrase" else TILE)
        self.draw_strip()
        self.draw_scenes()
        if self.panel:
            self.fill_panel()

    def caption(self, sound):
        if sound is None:
            return "Ask for a sound, or bring in a preset from Sounds."
        parts = []
        origin = sound.origin.get("kind")
        if origin == "preset":
            parts.append("Saved on the Quad Cortex")
        elif origin == "revised":
            parts.append("Revised: " + sound.origin["request"][:60] if sound.origin.get("request") else "A copy to work on")
        if sound.heard:
            parts.append("measured: " + summary(sound.heard))
        if sound.knobs:
            parts.append("shaped")
        if sound.saved:
            parts.append(f"saved as {sound.saved['name']}")
        return " · ".join(parts)

    def draw_scenes(self):
        for child in self.scene_row.winfo_children():
            child.destroy()
        scenes = self.studio.scenes()
        if not scenes:
            return
        label(self.scene_row, "Scene", size=9, color=DIM).pack(side="left", padx=(0, 6))
        for scene, name in scenes:
            chosen = scene == self.studio.state.scene
            chip = tk.Label(self.scene_row, text=f"{'ABCDEFGH'[scene]}{'  ' + name if name else ''}", padx=8, pady=3,
                            cursor="hand2", font=(FONT, 9, "bold" if chosen else "normal"),
                            bg=mix(SIDE[self.studio.state.side], DARK, .3) if chosen else TILE,
                            fg=INK if chosen else SOFT)
            chip.pack(side="left", padx=(0, 4))
            chip.bind("<ButtonRelease-1>", lambda _, scene=scene: self.studio.scene(scene))

    def draw_strip(self):
        for child in self.chips.winfo_children():
            child.destroy()
        found = self.studio.round()
        if not found:
            self.strip_note.configure(text="")
            return
        if self.studio.state.blind:
            self.strip_note.configure(text="")
            return
        on_b = self.studio.state.b
        if found.get("previous"):
            back = tk.Label(self.chips, text="‹", padx=6, pady=5, cursor="hand2", font=(FONT, 10), bg=PAPER, fg=SOFT)
            back.pack(side="left")
            back.bind("<ButtonRelease-1>", lambda _: self.studio.round_back())
            Tooltip(back, "The round before  ·  [")
        for place, candidate in enumerate(found["candidates"]):
            sound = self.studio.store.sound(candidate["sound"])
            chosen = candidate["sound"] == on_b
            chip = tk.Label(self.chips, text=f"{place + 1}  {sound.name}", padx=10, pady=5, cursor="hand2",
                            font=(FONT, 9, "bold" if chosen else "normal"),
                            bg=mix(SIDE["b"], DARK, .3) if chosen else TILE, fg=INK if chosen else SOFT)
            chip.pack(side="left", padx=(0, 5))
            chip.bind("<ButtonRelease-1>", lambda _, place=place: self.studio.step(place))
            if chosen:
                heard = (" · measured: " + summary(sound.heard)
                         if sound.heard else " · not listened to yet")
                self.strip_note.configure(text=candidate["differs"] + heard)
        ready = bool(found.get("next"))
        more = tk.Label(self.chips, text="›  next three" if ready else "Three more", padx=10, pady=5, cursor="hand2",
                        font=(FONT, 9, "bold" if ready else "normal"), bg=PAPER, fg=ACCENT if ready else SOFT)
        more.pack(side="left", padx=(4, 0))
        more.bind("<ButtonRelease-1>", lambda _: self.studio.round_forward())
        Tooltip(more, "Three more, from what was heard and what you marked  ·  N")
        aim = found["aim"]
        label(self.chips, f"aiming for {summary(aim)}", size=9, color=DIM).pack(
            side="right")

    # ---- panels ----

    def toggle(self, name):
        if self.panel == name:
            self.panel = None
            self.tray.grid_remove()
            return
        self.panel = name
        self.tray.grid(row=0, column=1, sticky="ns", padx=(14, 0))
        self.fill_panel()

    def fill_panel(self):
        for child in self.tray.winfo_children():
            child.destroy()
        label(self.tray, self.panel, size=11, bold=True).pack(fill="x", pady=(0, 6))
        getattr(self, f"panel_{self.panel.lower()}")()

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
            scale.bind("<ButtonRelease-1>", lambda _, key=key, scale=scale: self.turned(key, scale.get()))

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
            found = dict(zip(knob_table.PAD, pad_values(event.x, event.y, size)))
            point.update({key: found[key] for key in usable})
            x, y = pad_point(point[across], point[up], size)
            canvas.coords(dot, x - radius, y - radius, x + radius, y + radius)
            show()

        def done(event):
            drag(event)
            sound = self.studio.sound(self.studio.state.side)
            if sound is not None:
                self.studio.turn({**sound.knobs, **{key: point[key] for key in usable}})

        canvas.bind("<Button-1>", drag)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", done)
        show()

    def part_scene(self):
        chosen = getattr(self, "scene_choice", None)
        value = chosen.get() if chosen else ""
        return "ABCDEFGH".index(value[-1]) if value.startswith("scene ") else None

    def turned(self, key, value):
        sound = self.studio.sound(self.studio.state.side)
        self.studio.turn({**sound.knobs, key: value})

    def panel_chain(self):
        studio = self.studio
        sound = studio.sound(studio.state.side)
        if studio.state.blind:
            label(self.tray, "Hidden while you compare blind. X shows them.", color=SUBTLE).pack(fill="x")
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
                    choose = tk.Menubutton(line, text="▾", bg=DARK, fg=ACCENT, relief="flat", cursor="hand2",
                                           font=(FONT, 10))
                    options = tk.Menu(choose, tearoff=False, bg="#252834", fg=INK)
                    for name in names:
                        options.add_command(label=name, command=lambda r=r, column=column, name=name:
                                            studio.named(r, column, name))
                    choose.configure(menu=options)
                    choose.pack(side="right")
                    Tooltip(choose, "Its named settings, Neural DSP's own")
        from lab.hearing.measure import measured_lines

        label(self.tray, "Measured", size=8, color=DIM).pack(fill="x", pady=(10, 0))
        for line in measured_lines(sound.heard):
            label(self.tray, line, size=9, color=SOFT, wrap=400).pack(fill="x", pady=1)
        button(self.tray, "Nano Cortex sheet", lambda: self.nano_sheet(sound)).pack(anchor="w", pady=(8, 4))
        for note in sound.notes[-4:]:
            label(self.tray, f"You: {note['text']}", size=9, color=SUBTLE, wrap=400).pack(fill="x")

    def nano_sheet(self, sound):
        """How to set this sound up on the Nano Cortex: what to capture, the IR, and the effects by hand."""
        from lab.gear.nano import sheet

        top = tk.Toplevel(self.root, bg=PAPER)
        top.title(f"Nano Cortex · {sound.name}")
        text = tk.Text(top, bg=DARK, fg=INK, font=(FONT, 10), relief="flat", wrap="word", padx=14, pady=12,
                       width=70, height=30)
        text.insert("1.0", "\n".join(sheet(sound.chain).lines()))
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)

    def panel_difference(self):
        for line in self.studio.difference():
            label(self.tray, line, size=10, wrap=400).pack(fill="x", pady=2)

    def panel_phrase(self):
        """The phrase as a waveform; drag the edges to choose the part that loops."""
        import numpy as np

        whole = getattr(self.studio, "phrase_whole", None)
        if whole is None:
            label(self.tray, "Choose or record a phrase first.", color=SUBTLE).pack(fill="x")
            return
        audio, start, end = whole
        width, height = 400, 120
        canvas = tk.Canvas(self.tray, width=width, height=height, bg=TILE, highlightthickness=0, cursor="sb_h_double_arrow")
        canvas.pack(fill="x")
        columns = np.array_split(np.abs(audio[:, 0]), width)
        peak = max(float(audio.max()), 1e-6)
        for x, part in enumerate(columns):
            value = float(part.max()) / peak if len(part) else 0.
            canvas.create_line(x, height / 2 - value * height / 2, x, height / 2 + value * height / 2, fill=SOFT)
        shade = canvas.create_rectangle(0, 0, 0, height, fill=ACCENT, stipple="gray25", width=0)
        seconds = len(audio) / 48000
        note = label(self.tray, size=9, color=SUBTLE)
        note.pack(fill="x", pady=(4, 0))
        edges = {"start": start, "end": end}

        def draw():
            canvas.coords(shade, edges["start"] / len(audio) * width, 0, edges["end"] / len(audio) * width, height)
            note.configure(text=f"Loop {edges['start'] / 48000:.1f}–{edges['end'] / 48000:.1f} s of {seconds:.1f} s. "
                                "Drag either edge; the recording itself is never changed.")

        def drag(event):
            at = int(min(max(event.x, 0), width) / width * len(audio))
            nearer = "start" if abs(at - edges["start"]) < abs(at - edges["end"]) else "end"
            edges[nearer] = at
            if edges["end"] - edges["start"] < 24000:
                return
            draw()

        def done(_):
            self.studio.phrase = (self.studio.phrase[0], audio[edges["start"]:edges["end"]].copy())
            self.studio.phrase_whole = (audio, edges["start"], edges["end"])
            if self.studio.state.mode == "phrase":
                self.studio.play("phrase")

        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", done)
        draw()

    def panel_sounds(self):
        """What is on the unit: presets to work on, and the player's IRs and captures to try in B."""
        kinds = tk.Frame(self.tray, bg=DARK)
        kinds.pack(fill="x", pady=(0, 6))
        self.sounds_kind = getattr(self, "sounds_kind", "Presets")
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
            label(top, "Not read yet.", color=SUBTLE).pack(side="left")
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
        button(top, "Read the unit again", lambda: self.studio.read_assets(lambda _: self.fill_panel())).pack(
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
            return rows[picked[0]] if picked else None

        actions = tk.Frame(self.tray, bg=DARK)
        actions.pack(fill="x", pady=(6, 0))
        if kind == "ir":
            button(actions, "Put in B's cab", lambda: chosen() and self.studio.use_asset(chosen())).pack(side="left")
        else:
            button(actions, "Instead of B's amp", lambda: self.try_capture(chosen(), False)).pack(side="left")
            button(actions, "Instead of amp and cab", lambda: self.try_capture(chosen(), True)).pack(
                side="left", padx=6)

    def try_capture(self, asset, has_cab):
        if asset is None:
            return
        version = asset.capture_version
        if version is None:
            from tkinter import messagebox

            answer = messagebox.askyesnocancel("Capture version", f"Is {asset.name} a V2 capture? "
                                               "(No means V1. Cortex Control shows it.)", parent=self.root)
            if answer is None:
                return
            version = 2 if answer else 1
        self.studio.use_asset(asset, has_cab=has_cab, version=version)

    def read_directory(self):
        self.studio.set(working="Reading the presets on the Quad Cortex…")

        def read(entries):
            self.studio.set(working="", status=f"{len(entries)} presets on the unit.")
            if self.panel == "Sounds":
                self.fill_panel()

        self.studio.later(self.studio.host.call("lab.qc.library.directory", folder=str(self.studio.folder)), read)

    def work_on(self, box, rows):
        chosen = box.curselection()
        if chosen:
            slot, name, _ = rows[chosen[0]]
            self.studio.work_on(slot, name)

    def panel_history(self):
        # What the player's marks and choices say about what they keep, in measured quantities.
        for line in self.studio.taste_lines():
            label(self.tray, line, size=9, color=SOFT, wrap=400).pack(fill="x", pady=1)
        sounds = self.studio.store.sounds()[::-1][:60]
        box = tk.Listbox(self.tray, bg=DARK, fg=INK, selectbackground=TILE, relief="flat", highlightthickness=0,
                         font=(FONT, 9), height=24)
        box.pack(fill="both", expand=True)
        for sound in sounds:
            box.insert("end", f"{sound.name}  ·  {sound.origin.get('kind', '')}" + ("  ★" if sound.saved else ""))
        row = tk.Frame(self.tray, bg=DARK)
        row.pack(fill="x", pady=(6, 0))
        for side in ("a", "b"):
            button(row, f"Put on {side.upper()}",
                   lambda side=side: box.curselection() and self.studio.put(side, sounds[box.curselection()[0]])
                   ).pack(side="left", padx=(0, 6))

    def panel_song(self):
        studio = self.studio
        name = tk.Entry(self.tray, font=(FONT, 11), bg=TILE, fg=INK, relief="flat", insertbackground=INK)
        name.insert(0, studio.state.song or "")
        name.pack(fill="x", ipady=6)
        name.bind("<Return>", lambda _: studio.set(song=name.get().strip() or None))
        label(self.tray, "The song this sound is for. Enter to set.", size=8, color=DIM).pack(fill="x")
        if studio.state.song:
            song = studio.store.song(studio.state.song)
            for part in song["parts"]:
                sound = studio.store.sound(part["sound"])
                where = f", scene {'ABCDEFGH'[part['scene']]}" if part.get("scene") is not None else ""
                label(self.tray, f"{part['name']}: {sound.name}{where}", size=10).pack(fill="x", pady=1)
            row = tk.Frame(self.tray, bg=DARK)
            row.pack(fill="x", pady=(8, 0))
            label(row, "Make what is on show the", size=9, color=SUBTLE).pack(side="left", padx=(0, 6))
            for part in ("Clean", "Rhythm", "Lead"):
                button(row, part, lambda part=part: studio.add_part(part, self.part_scene())).pack(side="left",
                                                                                                    padx=(0, 5))
            scenes = tk.Frame(self.tray, bg=DARK)
            scenes.pack(fill="x", pady=(6, 0))
            label(scenes, "of", size=9, color=SUBTLE).pack(side="left", padx=(0, 6))
            self.scene_choice = tk.StringVar(value="the whole sound")
            tk.OptionMenu(scenes, self.scene_choice, "the whole sound",
                          *[f"scene {letter}" for letter in "ABCDEFGH"]).pack(side="left")

    # ---- actions ----

    def tile_menu(self, event, side):
        studio = self.studio
        sound = studio.sound(side)
        menu = tk.Menu(self.root, tearoff=False, bg="#252834", fg=INK)
        if sound is not None and not studio.state.blind:
            for word, key in (("keeper", "K"), ("not this", "D")):
                menu.add_checkbutton(label=f"{word.capitalize()}  ·  {key}", onvalue=True, offvalue=False,
                                     variable=tk.BooleanVar(value=word in sound.marks),
                                     command=lambda word=word: studio.mark(word, side))
            menu.add_command(label="Mark with a word…", command=lambda: self.mark_with_word(side))
            menu.add_separator()
        menu.add_command(label="Copy B to A", command=studio.copy_to_a)
        menu.add_command(label=("Show which was which" if studio.state.blind else "Compare blind") + "  ·  X",
                         command=studio.blind)
        menu.tk_popup(event.x_root, event.y_root)

    def mark_with_word(self, side=None):
        word = simpledialog.askstring("Mark", "A word for this sound (it is yours, not the app's):", parent=self.root)
        if word:
            self.studio.mark(word, side)

    def key(self, event):
        if self.root.focus_get() is self.request:
            return None
        name = event.keysym.lower()
        actions = {"a": lambda: self.studio.select("a"), "b": lambda: self.studio.select("b"),
                   "g": lambda: self.toggle_play("guitar"), "p": lambda: self.toggle_play("phrase"),
                   "space": self.studio.stop, "z": self.studio.undo, "slash": self.request.focus_set,
                   "1": lambda: self.studio.step(0), "2": lambda: self.studio.step(1), "3": lambda: self.studio.step(2),
                   "escape": self.studio.cancel, "0": self.studio.base,
                   "k": lambda: self.studio.mark("keeper"), "d": lambda: self.studio.mark("not this"),
                   "t": self.mark_with_word, "x": self.studio.blind, "n": self.studio.round_forward,
                   "bracketright": self.studio.round_forward, "bracketleft": self.studio.round_back,
                   "s": lambda: self.toggle("Shape"), "c": lambda: self.toggle("Chain"), "h": lambda: self.toggle("History")}
        if name in actions:
            actions[name]()
            return "break"
        return None

    def toggle_play(self, mode):
        seat = getattr(self, "seat", None)
        if seat is not None and seat.job is not None:
            self.studio.set(status="The Guitar Learner is using the guitar input. Stop it there first.")
            return
        if self.studio.state.mode == mode:
            self.studio.stop()
            return
        if mode == "phrase":
            # The phrase loop and gesture listening would both want the audio driver.
            from lab.learner import gestures

            if gestures.running():
                gestures.stop()
                self.studio.say("Gestures off while the phrase plays.")
        self.studio.play(mode)

    def ask(self):
        text = self.request.get()
        self.request.delete(0, "end")
        self.root.focus_set()
        self.studio.ask(text)

    def save(self):
        sound = self.studio.sound(self.studio.state.side)
        if sound is None:
            return
        name = simpledialog.askstring("Save to the Quad Cortex", "Name (up to 24 letters):", initialvalue=sound.name,
                                      parent=self.root)
        if name:
            self.studio.save(name.strip())

    def list_phrases(self, menu):
        menu.delete(0, "end")
        menu.add_command(label="Record a phrase…", command=self.record)
        menu.add_separator()
        for item in self.studio.recorded_phrases()[:20]:
            menu.add_command(label=f"{item['instrument']} · {item['setting']} · {item['recorded'][5:16]}",
                             command=lambda item=item: self.choose_recorded(item))

    def record(self):
        instrument = self.studio.store.instrument(self.studio.state.instrument) or {}
        settings = [f"{position} · {wiring}" for position in instrument.get("settings") or ["Pickup"]
                    for wiring in (instrument.get("wiring") or [""])]
        settings = [setting.rstrip(" ·") for setting in settings]
        choice = simpledialog.askstring(
            "Record a phrase", f"Pickup setting on the {self.studio.state.instrument}?\n" + "\n".join(settings),
            initialvalue=settings[0], parent=self.root)
        if choice:
            self.studio.record(choice.strip())

    def choose_recorded(self, item):
        path = self.studio.folder / "phrases" / item["id"] / "dry.wav"
        whole = read_wave(path)[:, :1]
        start, length = loudest_at(whole)
        self.studio.phrase = (item["id"], whole[start:start + length].copy())
        self.studio.phrase_whole = (whole, start, start + length)
        self.phrase_menu.configure(text=f"Phrase · {item.get('instrument', '')} · {item['setting']}")

    def panel_guitar(self):
        _, lines = self.studio.guitar_profile()
        label(self.tray, f"What the {self.studio.state.instrument} puts out", size=10, bold=True).pack(fill="x")
        for line in lines:
            label(self.tray, line, size=9, wrap=400).pack(fill="x", pady=2)
        button(self.tray, "Record a phrase", self.record).pack(fill="x", pady=(8, 0))

    def calibrate(self):
        self.studio.set(working="Measuring the USB round trip…")
        self.studio.later(self.studio.host.call("measure_round_trip"),
                          lambda offset: self.studio.set(working="", status=f"Round trip: {offset} samples."))

    def learner_seat(self):
        return getattr(self, "seat", None) or LearnerSeat(self)

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

    def tick(self):
        try:
            self.studio.poll()
            self.seat_tick()
            self.come_forward()
        finally:
            self.root.after(80, self.tick)

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
        door = getattr(self, "door", None)
        if door is None:
            return
        try:
            knock, _ = door.accept()
        except (BlockingIOError, OSError):
            return
        knock.close()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def seat_tick(self):
        seat = getattr(self, "seat", None)
        if seat is not None:
            seat.tick()

    def close(self):
        if getattr(self, "seat", None) is not None:
            self.seat.leaving = True
            if self.seat.stop_signal is not None:
                self.seat.stop_signal.set()
        self.studio.set(working="Giving the Quad Cortex back…")
        self.root.update()
        try:
            self.studio.close()
        finally:
            self.root.destroy()


class LearnerSeat:
    """What the Guitar Learner asks of the desk that opens it: a worker for its recording and live listening,
    the status line, and whether something else is using the audio input.

    The learner records the guitar's dry input itself, so the seat stops the phrase or the guitar the desk is
    playing before it starts, and the desk does not start playing while it works.
    """

    def __init__(self, desk):
        from concurrent.futures import ThreadPoolExecutor

        self.desk, self.root = desk, desk.root
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="learner")
        self.job = self.task = self.done = None
        self.leaving = None
        self.ready = self.stop_signal = None
        self.live_is_ready = False
        self.takes = []
        instrument = desk.studio.store.instrument(desk.studio.state.instrument) or {}
        self.input_channel = instrument.get("input", 1)
        desk.seat = self

    @staticmethod
    def read_state(path):
        try:
            found = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def set_status(self, value, **_):
        self.desk.studio.set(status=value)

    def task_if_active(self, name):
        return self.job is not None and self.task == name

    def stop(self):
        if self.stop_signal is not None:
            self.stop_signal.set()

    def submit(self, task, message, work, *args, done, **options):
        if self.job is not None or self.leaving:
            return
        self.desk.studio.stop()
        self.task, self.done = task, done
        self.set_status(message)
        self.job = self.worker.submit(work, *args, **options)

    def tick(self):
        if self.job is None or not self.job.done():
            return
        job, done = self.job, self.done
        self.job = self.task = self.done = None
        try:
            done(job.result())
        except Exception as error:  # noqa: BLE001
            self.set_status(f"Guitar Learner: {error}")


def main(arguments):
    from lab.qc.host import Host

    simulated = "--simulated" in arguments
    if not simulated and not only_desk():
        return
    folder = home() / ("simulated" if simulated else "")
    folder.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    if not simulated:
        first_run(root, folder)
    host = Host(folder, simulated=simulated)
    desk = Desk(root, Studio(host, folder))
    if not simulated:
        desk.answer_second_launch()
    root.mainloop()


PORT = 47231            # on this computer only: a second desk asks the first to come forward, and leaves


def only_desk():
    """True when no other desk is open. Otherwise ask that one to come forward and say no: two desks would fight
    over the unit."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=.5) as other:
            other.sendall(b"raise")
        return False
    except OSError:
        return True


def first_run(root, folder):
    """Once: check what the app needs, bring the earlier app's history in, and say what is missing."""
    from tkinter import messagebox

    from lab import migrate, setup

    if not setup.first_run_needed(folder):
        return
    findings = setup.checks(folder)
    try:
        moved = migrate.summary(migrate.migrate(folder))[0]
    except Exception as error:  # noqa: BLE001
        moved = f"The earlier app's history could not be brought in: {error}"
    setup.record(folder, findings)
    missing = [item["say"] for item in findings if not item["ok"]]
    text = ("\n\n".join(missing) if missing else "Everything the app needs is here.") + "\n\n" + moved
    root.withdraw()
    (messagebox.showwarning if missing else messagebox.showinfo)("Tone Lab · first run", text, parent=root)
    root.deiconify()


if __name__ == "__main__":
    main(sys.argv[1:])
