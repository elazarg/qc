"""Panels the listening desk shows only when they are called, and the tray that holds them."""

from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Thread
import tkinter as tk
import webbrowser

import numpy as np

from . import audio, diagnostics, listening, lore, preset_design, session, theme, tone_space, usage
from .theme import DARK, DIM, INK, PAPER, RULE, SOFT, SUBTLE, TILE, FONT


def label(parent, value=None, *, variable=None, size=10, color=INK, bold=False, wrap=0):
    return tk.Label(parent, text=value, textvariable=variable, bg=parent["bg"], fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), justify="left", anchor="w", wraplength=wrap)


def small(parent, title, action):
    button = tk.Button(parent, text=title, command=action, bg=TILE, fg=INK, disabledforeground=DIM,
                       activebackground=RULE, activeforeground=INK, relief="flat", borderwidth=0,
                       padx=9, pady=4, font=(FONT, 9, "bold"), cursor="hand2", takefocus=True,
                       highlightthickness=1, highlightbackground=parent["bg"], highlightcolor=theme.ACCENT)
    return button


class Tray(tk.Frame):
    """A column beside the tones. One panel is open at a time unless the player pins more."""

    WIDTH = 446

    def __init__(self, parent, desk):
        super().__init__(parent, bg=PAPER, width=self.WIDTH, height=10)
        self.pack_propagate(False)
        self.desk = desk
        self.cards, self.bodies, self.pins, self.growing = {}, {}, {}, set()
        self.order = []
        self.pinned = set()

    def add(self, name, key, *, grow=False):
        """Create the card for a panel and return the frame its content goes in."""
        card = tk.Frame(self, bg=DARK, padx=12, pady=9)
        head = tk.Frame(card, bg=DARK)
        head.pack(fill="x", pady=(0, 6))
        label(head, name, size=11, bold=True).pack(side="left")
        label(head, key, size=8, color=DIM).pack(side="left", padx=(7, 0))
        close = tk.Label(head, text="✕", bg=DARK, fg=SUBTLE, font=(FONT, 10), cursor="hand2", padx=4)
        close.pack(side="right")
        close.bind("<ButtonRelease-1>", lambda _: self.close(name))
        pin = tk.Label(head, text="Pin", bg=DARK, fg=SUBTLE, font=(FONT, 9), cursor="hand2", padx=6)
        pin.pack(side="right")
        pin.bind("<ButtonRelease-1>", lambda _: self.pin(name))
        theme.Tooltip(pin, "Keep this panel open while others come and go")
        body = tk.Frame(card, bg=DARK)
        body.pack(fill="both", expand=True)
        self.cards[name], self.bodies[name], self.pins[name] = card, body, pin
        if grow:
            self.growing.add(name)
        return body

    def is_open(self, name):
        return name in self.order

    def situation(self):
        return {**self.desk.situation(), "open": list(self.order), "pinned": sorted(self.pinned)}

    def open(self, name, *, auto=False):
        """Show a panel. One that arrives by itself never pushes aside what the player opened."""
        if self.is_open(name):
            return True
        loose = [other for other in self.order if other not in self.pinned]
        if auto and loose:
            return False
        for other in loose:
            self.order.remove(other)
        self.order.append(name)
        self.layout()
        usage.record("open", name, auto=auto, **self.situation())
        return True

    def close(self, name):
        if self.is_open(name):
            self.order.remove(name)
            self.pinned.discard(name)
            self.layout()
            usage.record("close", name, **self.situation())

    def toggle(self, name):
        if self.is_open(name):
            self.close(name)
        else:
            self.open(name)

    def pin(self, name, value=None):
        value = name not in self.pinned if value is None else value
        if value:
            self.pinned.add(name)
            if not self.is_open(name):
                self.order.append(name)
        else:
            self.pinned.discard(name)
        self.layout()
        usage.record("pin" if value else "unpin", name, **self.situation())
        self.desk.remember()

    def put_away(self):
        """Clear the tray for a moment and return what was there."""
        state = (list(self.order), set(self.pinned))
        self.order, self.pinned = [], set()
        self.layout()
        return state

    def bring_back(self, state):
        if state:
            self.order, self.pinned = list(state[0]), set(state[1])
            self.layout()

    def restore(self, names):
        """Reopen the panels the player left pinned, without recording it as a new choice."""
        for name in names:
            if name in self.cards and name not in self.order:
                self.order.append(name)
                self.pinned.add(name)
        self.layout()

    def layout(self):
        for card in self.cards.values():
            card.pack_forget()
        for name in self.order:
            grow = name in self.growing
            self.cards[name].pack(fill="both" if grow else "x", expand=grow, pady=(0, 8))
            pinned = name in self.pinned
            self.pins[name].configure(text="Pinned" if pinned else "Pin", fg=INK if pinned else SUBTLE)
        if self.order:
            self.grid(row=0, column=1, sticky="ns", padx=(14, 0))
        else:
            self.grid_remove()
        self.desk.panels_changed()


class ChainPanel(tk.Frame):
    """What the selected tone is made of, and why."""

    def __init__(self, parent, desk):
        super().__init__(parent, bg=parent["bg"])
        self.desk = desk
        self.shown = object()

    def sync(self):
        desk = self.desk
        tone, draft = desk.tone(), desk.tone_draft()
        key = (desk.side, tone, draft["id"] if draft else None)
        if key == self.shown:
            return
        self.shown = key
        for child in self.winfo_children():
            child.destroy()
        side = desk.side.upper()
        if tone is None:
            label(self, f"{side} is whatever the Quad Cortex has loaded. Tone Lab has not read its chain.",
                  color=SUBTLE, wrap=400).pack(fill="x")
            return
        label(self, f"{side} · {tone.name}", size=12, bold=True, wrap=400).pack(fill="x")
        if tone.preset:
            scene = "" if tone.scene is None else f" · scene {'ABCDEFGH'[tone.scene]}"
            label(self, f"On the Quad Cortex · {tone.preset.setlist} {tone.preset.slot}{scene}",
                  size=9, color=SUBTLE, wrap=400).pack(fill="x")
        if not draft:
            note = {"original": "This is the processing recorded with the phrase.",
                    "capture": "This is captured audio. It has no chain to edit."}.get(
                        tone.kind, "Open Cortex Control to see or edit this preset's blocks.")
            label(self, note, color=SUBTLE, wrap=400).pack(fill="x", pady=(8, 0))
            return
        label(self, draft["target"], color=SOFT, wrap=400).pack(fill="x", pady=(2, 8))
        scenes = draft.get("scenes", [])
        if scenes:
            # One preset, several sounds. The chosen scene is the one heard and shaped.
            chosen = tone.scene or 0
            bar = tk.Frame(self, bg=self["bg"])
            bar.pack(fill="x", pady=(0, 8))
            for index, name in enumerate(scenes):
                button = small(bar, name, lambda index=index: desk.choose_scene(index))
                if index == chosen:
                    button.configure(bg=theme.mix(theme.SIDE[desk.side], DARK, .3))
                button.pack(side="left", padx=(0, 5))
        parent = desk.drafts.get(draft.get("parent"))
        paths = (("chain", ""), ("parallel", "∥ "))
        before = {(path, self.role(block)): block for path, _ in paths
                  for block in parent.get(path) or []} if parent else None
        models = preset_design.choices()
        for path, mark, block in [(path, mark, block) for path, mark in paths for block in draft.get(path) or []]:
            if path == "parallel" and block is draft["parallel"][0]:
                blend = draft["blend"]
                level = "level with the main path" if not blend["level"] else f"{blend['level']:+g} dB against the main path"
                spread = f", panned {blend['spread']:g}% apart" if blend["spread"] else ""
                label(self, f"Second path, {level}{spread}", size=9, color=SUBTLE, wrap=400).pack(
                    fill="x", pady=(7, 1))
            role = self.role(block)
            asset = preset_design.asset_attachment(block)
            name = asset.name if asset else models[block["model_id"]]["name"]
            state = ""
            if block.get("reserved"):
                state = "neutral"
            elif before is not None and (path, role) not in before:
                state = "new"
            elif before is not None and any(before[path, role].get(key) != block.get(key) for key in (
                    "model_id", "settings", "asset", "scene_settings", "off_in_scenes")):
                state = "changed"
            row = tk.Frame(self, bg=self["bg"])
            row.pack(fill="x", pady=1)
            label(row, mark + role, size=9, color=DIM).pack(side="left")
            row.winfo_children()[0].configure(width=11)
            title = label(row, name, size=10, color=DIM if state == "neutral" else INK,
                          bold=state in ("new", "changed"))
            title.pack(side="left")
            # Resting on a block says what it is. Clicking one lists its named settings.
            known = lore.about_asset(asset.name) if asset else lore.about(block["model_id"])
            if known:
                theme.Tooltip(title, known, wrap=380)
            named = {} if asset else preset_design.named_settings(block["model_id"])
            if named and path == "chain":
                title.configure(cursor="hand2")
                title.bind("<ButtonRelease-1>", lambda event, role=role, named=named: self.voicings(event, role, named))
                label(row, "▾", size=8, color=DIM).pack(side="left", padx=(3, 0))
            if state:
                label(row, state, size=8, color=DIM if state == "neutral" else theme.SIDE[desk.side]).pack(
                    side="left", padx=(6, 0))
            settings = "  ".join(f"{item['name'].title() if item['name'].isupper() else item['name']} "
                                 f"{item['value']:g}" for item in block["settings"])
            if settings and state != "neutral":
                label(self, settings, size=8, color=SUBTLE, wrap=310).pack(fill="x", padx=(83, 0))
            for index, name in enumerate(scenes):
                varied = [f"{item['name'].title() if item['name'].isupper() else item['name']} {item['value']:g}"
                          for item in block.get("scene_settings", ()) if item["scene"] == index]
                if index in block.get("off_in_scenes", ()):
                    varied.insert(0, "off")
                if varied:
                    label(self, f"{name}: " + "  ".join(varied), size=8, wrap=310,
                          color=INK if index == (tone.scene or 0) else SUBTLE).pack(fill="x", padx=(83, 0))
        load = preset_design.weigh(draft)
        if load.percent >= 60 or load.memory >= 60:
            # Said only when the chain is getting heavy: there is little room left to add to it.
            heavy = f"Uses {load.percent}% of the processing one sound can have"
            heavy += f" and {load.memory}% of its delay memory." if load.memory >= 60 else "."
            label(self, heavy, size=8, color=SUBTLE, wrap=400).pack(fill="x", pady=(7, 0))
        if draft.get("explanation"):
            label(self, draft["explanation"], size=9, color=SOFT, wrap=400).pack(fill="x", pady=(9, 0))
        if draft.get("request"):
            label(self, f"Asked: “{draft['request']}”", size=9, color=DIM, wrap=400).pack(fill="x", pady=(5, 0))
        for source in draft.get("sources", [])[:4]:
            link = label(self, "↗ " + source["title"][:58], size=9, color=theme.TONE_A)
            link.configure(cursor="hand2")
            link.bind("<ButtonRelease-1>", lambda _, url=source["url"]: webbrowser.open(url))
            link.pack(fill="x", pady=(3, 0))

    def voicings(self, event, role, named):
        menu = tk.Menu(self, tearoff=False, bg=theme.WHITE, fg=theme.LIGHT, activebackground=RULE,
                       activeforeground=theme.LIGHT)
        for name, values in list(named.items())[:16]:
            menu.add_command(label=name, command=lambda name=name, values=values: self.desk.voice_block(
                role, name, values))
        menu.tk_popup(event.x_root, event.y_root)

    @staticmethod
    def role(block):
        asset = preset_design.asset_attachment(block)
        if asset:
            return "Amp" if asset.kind == "capture" else "Cab"
        return preset_design.choices()[block["model_id"]]["role"]


def describe_gap(result, first, second):
    """Name the largest spectral gap in words."""
    index = max(range(len(result.spectrum_db)), key=lambda band: abs(result.spectrum_db[band]))
    value = result.spectrum_db[index]
    band = tone_space.BAND_NAMES[index]
    band = f"{band[:-1]} kHz" if band.endswith("k") else f"{band} Hz"
    if abs(value) < .5:
        return f"{second} and {first} are within half a dB across the spectrum."
    return f"{second} sits {abs(value):.1f} dB {'above' if value > 0 else 'below'} {first} around {band}."


class DifferencePanel(tk.Frame):
    """Two captured sounds on the same scale, with the gap in words."""

    PAIRS = (("b", "a"), ("b", "reference"), ("a", "reference"))
    NAMES = {"a": "A", "b": "B", "reference": "R"}

    def __init__(self, parent, desk):
        super().__init__(parent, bg=parent["bg"])
        self.desk = desk
        self.pair = None
        self.measured = self.measuring = self.result = self.job = None
        self.sentence = tk.StringVar(master=self)
        self.detail = tk.StringVar(master=self)
        self.choices = tk.Frame(self, bg=self["bg"])
        self.choices.pack(fill="x", pady=(0, 6))
        self.pair_buttons = {}
        for pair in self.PAIRS:
            title = f"{self.NAMES[pair[0]]} against {self.NAMES[pair[1]]}"
            self.pair_buttons[pair] = small(self.choices, title, lambda choice=pair: self.choose(choice))
        self.chart = tk.Canvas(self, bg=theme.PAPER, height=108, highlightthickness=1, highlightbackground=RULE)
        self.chart.bind("<Configure>", lambda _: self.paint())
        note = label(self, variable=self.sentence, size=10, wrap=400)
        note.pack(fill="x")
        self.note = note
        fine = label(self, variable=self.detail, size=9, color=SUBTLE, wrap=400)
        fine.pack(fill="x", pady=(3, 0))
        self.bind("<Configure>", lambda event: [item.configure(wraplength=max(200, event.width - 8))
                                                for item in (note, fine)])
        self.actions = tk.Frame(self, bg=self["bg"])
        self.actions.pack(fill="x", pady=(8, 0))
        self.reference_row = tk.Frame(self, bg=self["bg"])
        self.bind("<Destroy>", self.stop)

    def sounds(self):
        desk = self.desk
        return {"a": desk.baseline, "b": desk.alternative, "reference": desk.reference}

    def choose(self, pair):
        self.pair = pair
        self.sync()

    def sync(self):
        desk = self.desk
        sounds = self.sounds()
        possible = [pair for pair in self.PAIRS if pair[1] != "reference" or sounds["reference"]]
        if self.pair not in possible:
            self.pair = next((pair for pair in possible if pair[1] == "reference" and pair[0] == desk.side),
                             possible[0])
        for pair, button in self.pair_buttons.items():
            if pair in possible and len(possible) > 1:
                button.pack(side="left", padx=(0, 4))
                button.configure(bg=theme.WHITE if pair == self.pair else TILE,
                                 fg=INK if pair == self.pair else SUBTLE)
            else:
                button.pack_forget()
        second, first = self.pair
        b, a = sounds[second], sounds[first]
        idle = desk.job is None
        for child in self.actions.winfo_children():
            child.destroy()
        for side in dict.fromkeys((first, second)):
            if sounds[side]:
                small(self.actions, f"▶ {self.NAMES[side]}", lambda choice=side: desk.listen(choice)).pack(
                    side="left", padx=(0, 4))
                self.actions.winfo_children()[-1].configure(state="normal" if idle else "disabled")
            elif side != "reference" and desk.take and desk.selectable(side):
                small(self.actions, f"Capture {self.NAMES[side]}", lambda choice=side: desk.capture_tone(choice)).pack(
                    side="left", padx=(0, 4))
                self.actions.winfo_children()[-1].configure(state="normal" if idle else "disabled")
        if sounds["reference"]:
            if not self.reference_row.winfo_manager():
                for child in self.reference_row.winfo_children():
                    child.destroy()
                small(self.reference_row, "Closest saved sound", desk.match).pack(side="left", padx=(0, 4))
                small(self.reference_row, "Reference on A", desk.reference_on_a).pack(side="left", padx=(0, 4))
                small(self.reference_row, "Clear reference", desk.clear_reference).pack(side="left")
                self.reference_row.pack(fill="x", pady=(5, 0))
        elif self.reference_row.winfo_manager():
            self.reference_row.pack_forget()
        if not a or not b:
            missing = [self.NAMES[side] for side in (first, second) if not sounds[side]]
            self.result = self.measured = None
            self.chart.pack_forget()
            if not desk.take:
                self.sentence.set("Record or choose a phrase first. A difference needs the same playing through both.")
            else:
                self.sentence.set(f"{' and '.join(missing)} {'has' if len(missing) == 1 else 'have'} "
                                  "not been captured on this phrase yet.")
            self.detail.set("Capturing replays the phrase through the Quad Cortex and records the result.")
            return
        if (a, b) != self.measured and self.measuring is None:
            self.measure(a, b)

    def measure(self, a, b):
        self.measured = (a, b)
        self.result = None
        self.sentence.set("Measuring…")
        self.detail.set("")
        queue = self.measuring = SimpleQueue()

        def work():
            try:
                queue.put((tone_space.difference(listening.samples(a), listening.samples(b)), None))
            except (OSError, ValueError, RuntimeError) as error:
                queue.put((None, str(error)))
            except Exception:
                diagnostics.LOG.exception("Difference panel analysis failed")
                queue.put((None, "Could not analyze these recordings."))

        Thread(target=work, daemon=True, name="tone-difference").start()
        self.job = self.after(80, self.poll)

    def poll(self):
        try:
            result, problem = self.measuring.get_nowait()
        except Empty:
            self.job = self.after(80, self.poll)
            return
        self.job = self.measuring = None
        a, b = self.measured
        second, first = self.pair
        if problem:
            self.sentence.set(problem)
            self.chart.pack_forget()
        else:
            self.result = result
            self.sentence.set(describe_gap(result, self.NAMES[first], self.NAMES[second]))
            deltas = "  ·  ".join(f"{name} {value:+.1f} {unit}" for name, value, unit in result.perceptual_delta)
            same = "Same playing" if a.paired_with(b) else "Different playing, so this includes the performance"
            self.detail.set(f"{same}\n{deltas}")
            self.chart.pack(fill="x", pady=(0, 6), before=self.note)
            self.paint()
        self.sync()

    def paint(self):
        chart = self.chart
        chart.delete("all")
        if not self.result:
            return
        width, height = max(180, chart.winfo_width()), max(80, chart.winfo_height())
        center = (height - 18) / 2
        chart.create_line(4, center, width - 4, center, fill=RULE)
        values = self.result.spectrum_db
        scale = max(6., max(abs(value) for value in values))
        step = (width - 10) / len(values)
        color = theme.SIDE[self.pair[0]]
        for index, (value, name) in enumerate(zip(values, tone_space.BAND_NAMES)):
            x = 5 + step * (index + .5)
            y = center - value / scale * (center - 5)
            chart.create_rectangle(x - step * .28, center, x + step * .28, y,
                                   fill=color if value > 0 else theme.mix(color, PAPER, .55), outline="")
            chart.create_text(x, height - 8, text=name, font=(FONT, 8), fill=SUBTLE)
        chart.create_text(width - 4, 3, anchor="ne", text=f"±{scale:.0f} dB", font=(FONT, 8), fill=SUBTLE)

    def stop(self, event=None):
        if (event is None or event.widget is self) and self.job is not None:
            self.after_cancel(self.job)
            self.job = None


class HistoryPanel(tk.Frame):
    """Every sound Tone Lab has built, and the way back to any of them."""

    def __init__(self, parent, desk):
        super().__init__(parent, bg=parent["bg"])
        self.desk = desk
        self.rows = []
        self.shown = None
        actions = tk.Frame(self, bg=self["bg"])
        actions.pack(fill="x", side="bottom", pady=(6, 0))
        self.to_b = small(actions, "Put on B", lambda: self.put("b"))
        self.to_b.pack(side="left", padx=(0, 4))
        self.to_a = small(actions, "Put on A", lambda: self.put("a"))
        self.to_a.pack(side="left")
        self.undo = small(actions, "Undo", desk.undo_last)
        self.undo.pack(side="right")
        self.recent = label(self, "", size=9, color=SUBTLE, wrap=400)
        self.recent.pack(fill="x", side="bottom", pady=(6, 0))
        self.list = tk.Listbox(self, bg=theme.PAPER, fg=INK, selectbackground="#3e4c68", selectforeground=INK,
                               font=(FONT, 10), borderwidth=0, highlightthickness=1, highlightcolor=RULE,
                               highlightbackground=RULE, exportselection=False, activestyle="none", height=6)
        self.list.pack(fill="both", expand=True)
        self.list.bind("<<ListboxSelect>>", lambda _: self.buttons())
        self.list.bind("<Double-Button-1>", lambda _: self.put("b"))
        self.bind("<Configure>", lambda event: self.recent.configure(wraplength=max(200, event.width - 8)))

    @staticmethod
    def saved(draft):
        try:
            return (session.SESSIONS / "drafts" / f"{draft['id']}.kept.json").is_file()
        except OSError:
            return False

    def sync(self):
        desk = self.desk
        drafts = sorted(desk.drafts.values(), key=lambda draft: draft.get("created_utc", ""), reverse=True)
        on = {side: tone.draft_id for side in ("a", "b") if (tone := desk.tone(side)) and tone.draft_id}
        journal = tuple(desk.journal[-4:])
        key = (tuple(draft["id"] for draft in drafts), tuple(sorted(on.items())), journal, len(desk.undo))
        if key != self.shown:
            self.shown = key
            chosen = self.selected()
            self.rows = drafts
            self.list.delete(0, tk.END)
            for draft in drafts:
                marks = " and ".join(side.upper() for side, draft_id in sorted(on.items()) if draft_id == draft["id"])
                try:
                    when = datetime.fromisoformat(draft["created_utc"]).astimezone().strftime("%d %b %H:%M")
                except (KeyError, ValueError):
                    when = ""
                saved = " ★" if self.saved(draft) else ""
                place = f"  ·  on {marks}" if marks else ""
                self.list.insert(tk.END, f" {draft['name']}{saved}{place}  ·  {when}")
            if chosen in drafts:
                self.list.selection_set(drafts.index(chosen))
            if not drafts:
                self.list.insert(tk.END, "   Sounds you ask for are kept here.")
            self.recent.configure(text="\n".join(journal))
        self.buttons()

    def selected(self):
        index = self.list.curselection()
        return self.rows[index[0]] if index and index[0] < len(self.rows) else None

    def buttons(self):
        ready = self.selected() is not None and (self.desk.job is None or self.desk.task_if_active("monitor"))
        for button in (self.to_a, self.to_b):
            button.configure(state="normal" if ready else "disabled")
        self.undo.configure(state="normal" if self.desk.undo else "disabled")

    def put(self, side):
        draft = self.selected()
        if draft and self.to_b.cget("state") == "normal":
            self.desk.put_draft(draft, side)


class PhrasePanel(tk.Frame):
    """The recorded phrase as a waveform. The looped part is marked and can be dragged."""

    def __init__(self, parent, desk):
        super().__init__(parent, bg=parent["bg"])
        self.desk = desk
        self.loaded = self.peaks = self.dragging = None
        self.duration, self.span, self.at = 0., (0., 0.), None
        self.caption = tk.StringVar(master=self)
        self.canvas = tk.Canvas(self, bg=theme.PAPER, height=92, highlightthickness=1, highlightbackground=RULE,
                                cursor="sb_h_double_arrow")
        self.canvas.pack(fill="x")
        self.canvas.bind("<Configure>", lambda _: self.paint())
        self.canvas.bind("<Button-1>", self.grab)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        label(self, variable=self.caption, size=9, color=SUBTLE, wrap=400).pack(fill="x", pady=(5, 0))
        actions = tk.Frame(self, bg=self["bg"])
        actions.pack(fill="x", pady=(6, 0))
        self.whole = small(actions, "Whole take", lambda: desk.trim_take(0., self.duration))
        self.whole.pack(side="left", padx=(0, 4))
        small(actions, "Record a new phrase", desk.recording).pack(side="left")

    def sync(self):
        take = self.desk.take
        key = (take.folder, take.section) if take else None
        if key != self.loaded:
            self.loaded, self.peaks = key, None
            if take:
                try:
                    wave = np.abs(audio.read_wave(Path(take.info["dry"]))).max(axis=1)
                    columns = 360
                    usable = len(wave) // columns * columns
                    peaks = wave[:usable].reshape(columns, -1).max(axis=1)
                    self.peaks = peaks / max(float(peaks.max()), 1e-6)
                    self.duration = len(wave) / audio.RATE
                    self.span = tuple(float(value) for value in take.section)
                except (OSError, ValueError, KeyError):
                    diagnostics.LOG.info("The phrase could not be drawn", exc_info=True)
            self.paint()
        self.describe()
        steady = self.desk.job is None or self.desk.task_if_active("monitor")
        self.whole.configure(state="normal" if take and steady else "disabled")

    def describe(self):
        if self.peaks is None:
            self.caption.set("Record a phrase to loop it through either tone.")
            return
        start, end = self.span
        self.caption.set(f"Loop {start:.2f} – {end:.2f} s of {self.duration:.1f} s. Drag either edge. "
                         "Sounds captured on a different loop need capturing again.")

    def x_of(self, seconds):
        return 4 + (max(60, self.canvas.winfo_width()) - 8) * seconds / max(self.duration, 1e-6)

    def seconds_at(self, x):
        return min(self.duration, max(0., (x - 4) * self.duration / (max(60, self.canvas.winfo_width()) - 8)))

    def paint(self):
        canvas = self.canvas
        canvas.delete("all")
        if self.peaks is None:
            return
        width, height = max(60, canvas.winfo_width()), max(40, canvas.winfo_height())
        middle = height / 2
        left, right = self.x_of(self.span[0]), self.x_of(self.span[1])
        canvas.create_rectangle(left, 2, right, height - 2, fill=TILE, outline="")
        step = (width - 8) / len(self.peaks)
        for index, peak in enumerate(self.peaks):
            x = 4 + step * (index + .5)
            reach = max(1., peak ** .6 * (middle - 6))
            canvas.create_line(x, middle - reach, x, middle + reach, fill=INK if left <= x <= right else DIM)
        for edge in (left, right):
            canvas.create_rectangle(edge - 2, 2, edge + 2, height - 2, fill=theme.ACCENT, outline="")
        if self.at is not None:
            x = left + (right - left) * self.at
            canvas.create_line(x, 0, x, height, fill=theme.LIVE, width=2, tags="cursor")

    def cursor(self, fraction):
        """Follow the loop while it plays."""
        if fraction != self.at:
            self.at = fraction
            self.canvas.delete("cursor")
            if fraction is not None and self.peaks is not None:
                left, right = self.x_of(self.span[0]), self.x_of(self.span[1])
                x = left + (right - left) * fraction
                self.canvas.create_line(x, 0, x, max(40, self.canvas.winfo_height()), fill=theme.LIVE, width=2,
                                        tags="cursor")

    def grab(self, event):
        if self.peaks is None or self.whole.cget("state") != "normal":
            return
        left, right = self.x_of(self.span[0]), self.x_of(self.span[1])
        self.dragging = 0 if abs(event.x - left) <= abs(event.x - right) else 1
        self.drag(event)

    def drag(self, event):
        if self.dragging is None:
            return
        start, end = self.span
        moved = self.seconds_at(event.x)
        if self.dragging == 0:
            start = min(moved, end - .5)
        else:
            end = max(moved, start + .5)
        self.span = (round(max(0., start), 2), round(min(self.duration, end), 2))
        self.paint()
        self.describe()

    def release(self, _):
        if self.dragging is not None:
            self.dragging = None
            self.desk.trim_take(*self.span)
