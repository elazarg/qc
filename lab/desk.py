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

from . import knobs as knob_table
from .audio import read_wave
from .knowledge import REPO, home
from .studio import Studio
from .theme import (ACCENT, DARK, DIM, FONT, INK, LIVE, PAPER, RULE, SIDE, SOFT, SUBTLE, TILE, Tooltip, mix)

PANELS = ("Shape", "Chain", "Sounds", "History", "Song", "Guitar")
PHRASE_SECONDS = 3.5


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


def loudest(path, seconds=PHRASE_SECONDS):
    import numpy as np

    dry = read_wave(path)[:, :1]
    length = round(seconds * 48000)
    if len(dry) <= length:
        return dry
    energy = np.concatenate(([0.], np.cumsum(np.square(dry[:, 0], dtype=np.float64))))
    start = int(np.argmax(energy[length:] - energy[:-length]))
    return dry[start:start + length].copy()


class Desk:
    def __init__(self, root, studio):
        self.root, self.studio = root, studio
        self.panel = self.learner = None
        root.title("Tone Lab")
        root.configure(bg=PAPER)
        root.geometry("1100x680")
        root.minsize(980, 620)
        self.build()
        studio.listeners.append(lambda state: self.draw())
        root.bind("<Key>", self.key)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.choose_phrase(phrases()[0] if phrases() else None)
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
            air = label(tile, size=8, color=LIVE, bold=True)
            air.pack(side="top", fill="x")
            caption.pack(side="bottom", fill="x")
            name.pack(side="bottom", fill="x")
            badge.pack(side="bottom", fill="x")
            for widget in (tile, badge, name, caption, air):
                widget.bind("<ButtonRelease-1>", lambda _, side=side: self.studio.select(side))
            self.tiles[side] = (tile, name, caption, air)

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
        self.request.bind("<Escape>", lambda _: self.root.focus_set())
        label(ask, "/", size=9, color=DIM).pack(side="right", padx=12)

    # ---- drawing ----

    def draw(self):
        state = self.studio.state
        self.working.configure(text=state.working)
        self.dot.itemconfigure(self.dot_mark, fill=LIVE if state.mode else (ACCENT if state.working else DIM))
        for side in ("a", "b"):
            tile, name, caption, air = self.tiles[side]
            sound = self.studio.sound(side)
            name.configure(text=sound.name if sound else "Empty")
            caption.configure(text=self.caption(sound))
            selected = state.side == side
            on_air = state.mode and selected
            tile.configure(highlightbackground=SIDE[side] if selected else DARK,
                           bg=mix(SIDE[side], DARK, .14) if on_air else DARK)
            for widget in (name, caption, air):
                widget.configure(bg=tile["bg"])
            air.configure(text=f"● {state.mode}" if on_air else "")
        self.line.configure(text=state.status)
        self.guitar.configure(bg=mix(LIVE, TILE, .4) if state.mode == "guitar" else TILE)
        self.loop.configure(bg=mix(LIVE, TILE, .4) if state.mode == "phrase" else TILE)
        self.draw_strip()
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
            parts.append("heard: " + ", ".join(sound.heard[key] for key in ("drive", "tone", "space")))
        if sound.knobs:
            parts.append("shaped")
        if sound.saved:
            parts.append(f"saved as {sound.saved['name']}")
        return " · ".join(parts)

    def draw_strip(self):
        for child in self.chips.winfo_children():
            child.destroy()
        found = self.studio.round()
        if not found:
            self.strip_note.configure(text="")
            return
        on_b = self.studio.state.b
        for place, candidate in enumerate(found["candidates"]):
            sound = self.studio.store.sound(candidate["sound"])
            chosen = candidate["sound"] == on_b
            chip = tk.Label(self.chips, text=f"{place + 1}  {sound.name}", padx=10, pady=5, cursor="hand2",
                            font=(FONT, 9, "bold" if chosen else "normal"),
                            bg=mix(SIDE["b"], DARK, .3) if chosen else TILE, fg=INK if chosen else SOFT)
            chip.pack(side="left", padx=(0, 5))
            chip.bind("<ButtonRelease-1>", lambda _, place=place: self.studio.step(place))
            if chosen:
                heard = (" · heard: " + ", ".join(sound.heard[key] for key in ("drive", "tone", "space"))
                         if sound.heard else " · not listened to yet")
                self.strip_note.configure(text=candidate["differs"] + heard)
        aim = found["aim"]
        label(self.chips, f"aiming for {aim['drive']}, {aim['tone']}, {aim['space']}", size=9, color=DIM).pack(
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
        for key in studio.available():
            title, low, _ = knob_table.KNOBS[key]
            row = tk.Frame(self.tray, bg=DARK)
            row.pack(fill="x")
            label(row, title, size=9, color=SOFT).pack(side="left")
            scale = tk.Scale(row, from_=low, to=1, resolution=.05, orient="horizontal", length=230, showvalue=False,
                             bg=DARK, fg=INK, troughcolor=TILE, highlightthickness=0, borderwidth=0)
            scale.set(values.get(key, 0.))
            scale.pack(side="right")
            scale.bind("<ButtonRelease-1>", lambda _, key=key, scale=scale: self.turned(key, scale.get()))

    def turned(self, key, value):
        sound = self.studio.sound(self.studio.state.side)
        self.studio.turn({**sound.knobs, key: value})

    def panel_chain(self):
        studio = self.studio
        sound = studio.sound(studio.state.side)
        if sound is None:
            label(self.tray, "Nothing here yet.", color=SUBTLE).pack(fill="x")
            return
        label(self.tray, f"{studio.state.side.upper()} · {sound.name}", size=12, bold=True, wrap=400).pack(fill="x")
        if sound.explanation:
            label(self.tray, sound.explanation, size=9, color=SOFT, wrap=400).pack(fill="x", pady=(2, 6))
        for line in studio.chain_text():
            label(self.tray, line, size=9, wrap=400).pack(fill="x", pady=2)
        for note in sound.notes[-4:]:
            label(self.tray, f"You: {note['text']}", size=9, color=SUBTLE, wrap=400).pack(fill="x")

    def panel_sounds(self):
        """Presets saved on the unit, from the last directory the app read. Choose one to work on."""
        listing = REPO / "tone_sessions" / "qc-library.json"
        try:
            catalog = json.loads(listing.read_text(encoding="utf-8"))["catalog"]
        except (OSError, ValueError, KeyError):
            label(self.tray, "The unit's presets have not been read yet.", color=SUBTLE).pack(fill="x")
            return
        box = tk.Listbox(self.tray, bg=DARK, fg=INK, selectbackground=TILE, relief="flat", highlightthickness=0,
                         font=(FONT, 9), height=24)
        box.pack(fill="both", expand=True)
        rows = []
        for folder in catalog.get("presets", []):
            if folder.get("is_factory"):
                continue
            for file in folder.get("files", []):
                if file.get("name"):
                    rows.append(({"folder_key": folder["key"], "position": file["index"], "is_factory": False},
                                 file["name"], folder.get("name") or folder["key"].rsplit("/", 1)[-1]))
        rows.sort(key=lambda row: (row[2] != "My Presets", row[2], row[0]["position"]))
        for slot, name, setlist in rows:
            box.insert("end", f"{name}  ·  {setlist}")
        button(self.tray, "Work on this", lambda: self.work_on(box, rows)).pack(fill="x", pady=(6, 0))

    def work_on(self, box, rows):
        chosen = box.curselection()
        if chosen:
            slot, name, _ = rows[chosen[0]]
            self.studio.work_on(slot, name)

    def panel_history(self):
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
                label(self.tray, f"{part['name']}: {sound.name}", size=10).pack(fill="x", pady=1)
            row = tk.Frame(self.tray, bg=DARK)
            row.pack(fill="x", pady=(8, 0))
            for part in ("Clean", "Rhythm", "Lead"):
                button(row, part, lambda part=part: studio.add_part(part)).pack(side="left", padx=(0, 5))

    # ---- actions ----

    def key(self, event):
        if self.root.focus_get() is self.request:
            return None
        name = event.keysym.lower()
        actions = {"a": lambda: self.studio.select("a"), "b": lambda: self.studio.select("b"),
                   "g": lambda: self.toggle_play("guitar"), "p": lambda: self.toggle_play("phrase"),
                   "space": self.studio.stop, "z": self.studio.undo, "slash": self.request.focus_set,
                   "1": lambda: self.studio.step(0), "2": lambda: self.studio.step(1), "3": lambda: self.studio.step(2),
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
        else:
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
        for item in self.studio.recorded_phrases()[:12]:
            menu.add_command(label=f"{item['instrument']} · {item['setting']} · {item['recorded'][5:16]}",
                             command=lambda item=item: self.choose_recorded(item))
        menu.add_separator()
        for take in phrases()[:12]:
            note = take[2].get("pickup_note") or ""
            menu.add_command(label=f"{take[0][5:]}  {note}"[:60], command=lambda take=take: self.choose_phrase(take))

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
        self.studio.phrase = (item["id"], read_wave(self.studio.folder / "phrases" / item["id"] / "dry.wav")[:, :1])
        self.phrase_menu.configure(text=f"Phrase · {item['setting']}")

    def panel_guitar(self):
        _, lines = self.studio.guitar_profile()
        label(self.tray, f"What the {self.studio.state.instrument} puts out", size=10, bold=True).pack(fill="x")
        for line in lines:
            label(self.tray, line, size=9, wrap=400).pack(fill="x", pady=2)
        button(self.tray, "Record a phrase", self.record).pack(fill="x", pady=(8, 0))

    def choose_phrase(self, take):
        if take is None:
            return
        self.studio.phrase = (take[0], loudest(take[1]))
        self.phrase_menu.configure(text=f"Phrase · {take[0][5:20]}")

    def calibrate(self):
        self.studio.set(working="Measuring the USB round trip…")
        self.studio.later(self.studio.host.call("measure_round_trip"),
                          lambda offset: self.studio.set(working="", status=f"Round trip: {offset} samples."))

    def open_learner(self):
        """The Guitar Learner, the window from the earlier app, seated on this desk."""
        from tone_lab.guitar_learner_ui import GuitarLearner

        if self.learner is None:
            self.learner = GuitarLearner(LearnerSeat(self))
        self.learner.show()

    def tick(self):
        try:
            self.studio.poll()
            self.seat_tick()
        finally:
            self.root.after(80, self.tick)

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
    from .host import Host

    simulated = "--simulated" in arguments
    folder = home() / ("simulated" if simulated else "")
    folder.mkdir(parents=True, exist_ok=True)
    host = Host(folder, simulated=simulated)
    root = tk.Tk()
    Desk(root, Studio(host, folder))
    root.mainloop()


if __name__ == "__main__":
    main(sys.argv[1:])
