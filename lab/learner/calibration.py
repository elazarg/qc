"""Guitar input calibration: teach one control's settings from dry takes, check what is heard, map a gesture.

    from lab.learner import calibration
    calibration.open(seat)      # the seat of lab.desk.LearnerSeat: the same protocol as the Guitar Learner

One control at a time (pickup selector, volume or tone) is taught from takes
recorded at two or more of its settings, with the others held fixed
(`state.calibrate`). A fresh take is then checked against what was taught,
with a timeline of the states heard and of any mapped return gesture.
"Show estimate while playing guitar" starts gesture listening
(`lab.learner.gestures`), which turns the mapped Shape knob on the desk's
sound when the gesture is armed.

The examples, profiles and mappings stay where the earlier app kept them
(`folders.calibration`).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
from pathlib import Path
from threading import Event
import tkinter as tk
from tkinter import filedialog, ttk

import numpy as np
from scipy.io import wavfile

from .. import audio, theme as palette
from ..knobs import KNOBS
from ..measure import measure
from ..sounds import INSTRUMENTS
from . import folders, gestures as guitar_gestures, state as guitar_state, takes
from .folders import LOG

GUITARS = tuple(instrument["name"] for instrument in INSTRUMENTS if instrument["kind"] == "electric")

# The desk's colours and widgets, as the earlier app drew this panel.
BG = palette.PAPER
PANEL = palette.DARK
RECESS = "#181a22"
LINE = palette.RULE
TEXT = palette.INK
MUTED = palette.SUBTLE
ACCENT = palette.ACCENT
INK = palette.ACCENT_INK
FONT = palette.FONT


def button(parent, label, command, *, accent=False, compact=False):
    return tk.Button(
        parent, text=label, command=command, font=(FONT, 10, "bold"), relief="flat",
        borderwidth=0, padx=10 if compact else 14, pady=6 if compact else 9,
        background=ACCENT if accent else palette.TILE, foreground=INK if accent else TEXT,
        activebackground="#ffd7b7" if accent else palette.RULE, activeforeground=INK if accent else TEXT,
        disabledforeground="#8a8b9a" if not accent else "#7a5a43", takefocus=True,
        highlightthickness=1, highlightcolor=ACCENT, highlightbackground=parent.cget("bg"),
    )


def label(parent, text=None, *, variable=None, size=10, strong=False, color=TEXT, wrap=0):
    options = {"textvariable": variable} if variable is not None else {"text": text}
    return tk.Label(parent, **options, font=(FONT, size, "bold" if strong else "normal"),
                    background=parent.cget("bg"), foreground=color, wraplength=wrap,
                    justify="left", anchor="w")


def card(parent, *, padding=16):
    return tk.Frame(parent, background=PANEL, padx=padding, pady=padding,
                    highlightbackground=LINE, highlightthickness=1)


def entry(parent, variable, *, width=24):
    return tk.Entry(parent, textvariable=variable, width=width, font=(FONT, 11),
                    background=RECESS, foreground=TEXT, insertbackground=TEXT, relief="flat",
                    highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                    selectbackground="#3e4c68", selectforeground=TEXT)


def add_scrollbar(parent, canvas):
    style = ttk.Style(parent)
    style.configure("ToneLab.Vertical.TScrollbar", background=LINE, troughcolor=BG, bordercolor=BG,
                    darkcolor=LINE, lightcolor=LINE, arrowcolor=MUTED, arrowsize=11)
    bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview, style="ToneLab.Vertical.TScrollbar")

    def show(first, last):
        bar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            bar.pack_forget()
        elif not bar.winfo_manager():
            bar.pack(side="right", fill="y", before=canvas)

    canvas.configure(yscrollcommand=show)


CONTROLS = {"Pickup selector": "pickup", "Volume knob": "volume", "Tone knob": "tone"}
SETTINGS = {
    "pickup": ("Neck", "Bridge", "Middle"),
    "tone": ("Tone 10", "Tone 0", "Tone 5"),
    "volume": ("Volume 10", "Volume 5", "Volume 2"),
}
CALIBRATION_HINTS = {
    "pickup": "Move the selector. Keep both knobs and your picking position fixed.",
    "volume": "Turn the volume knob. Keep pickup, tone, Quad Cortex input gain and picking strength fixed.",
    "tone": "Turn the tone knob. Keep pickup, volume and your picking position fixed.",
}
NO_RECORDING = object()


def profile_folder(instrument):
    return folders.calibration(instrument)


def example_counts(examples):
    counts = {}
    for example in examples:
        label = example["label"].strip()
        key = label.casefold()
        name, count = counts.get(key, (label, 0))
        counts[key] = (name, count + 1)
    return counts


def estimate_label(state, score):
    if not state:
        return "Uncertain"
    match = "strong match" if score >= .8 else "good match" if score >= .55 else "tentative match"
    return f"{state} · {match}"


class GuitarInputPanel:
    def __init__(self, review, parent, worker, *, live_observer=True):
        self.review, self.root, self.worker = review, review.root, worker
        self.future = None
        self.live_running = False
        self.profile = None
        self.mapping = None
        self.examples = []
        self.counts = {}
        self.latest_dry = None
        self.example_source = None
        self.recording_before = NO_RECORDING
        self.recording_kind = None
        self.recording_setting = None
        self.examples_changed = False
        self.result_audio = None
        self.result_intervals = []
        self.loaded_selection = None
        self.desk_guitar = getattr(review, "guitar", None)
        self.last_desk_guitar = self.desk_guitar.get() if self.desk_guitar else GUITARS[0]
        self.instrument = tk.StringVar(value=self.last_desk_guitar)
        self.control = tk.StringVar(value=next(iter(CONTROLS)))
        self.example_label = tk.StringVar()
        self.start_seconds, self.end_seconds = tk.StringVar(), tk.StringVar()
        self.profile_text = tk.StringVar()
        self.latest_text, self.source_text = tk.StringVar(), tk.StringVar()
        self.progress_text, self.next_step = tk.StringVar(), tk.StringVar()
        self.control_hint = tk.StringVar()
        self.status = tk.StringVar()
        self.estimate_text = tk.StringVar()
        self.live_text = tk.StringVar(value="Live estimate is off.")
        self.live_action = tk.StringVar()
        self.observe, self.arm = tk.BooleanVar(), tk.BooleanVar()
        self.home, self.excursion, self.knob = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.step = tk.StringVar(value="+10")
        self.knob_keys = {spec[0]: key for key, spec in KNOBS.items()}

        label(parent, variable=self.status, color=ACCENT, wrap=550).pack(side="bottom", fill="x", pady=(5, 0))
        self.scroller = tk.Canvas(parent, background=BG, highlightthickness=0)
        self.scroller.pack(side="left", fill="both", expand=True)
        add_scrollbar(parent, self.scroller)
        body = tk.Frame(self.scroller, background=BG)
        frame = self.scroller.create_window((0, 0), anchor="nw", window=body)
        body.bind("<Configure>", lambda _: self.scroller.configure(scrollregion=self.scroller.bbox("all")))
        self.scroller.bind("<Configure>", lambda event: self.scroller.itemconfigure(frame, width=event.width))

        header = card(body, padding=12)
        header.pack(fill="x", pady=(0, 8))
        choice = self.row(header)
        self.instrument_box = self.combo(choice, "Instrument", self.instrument, self.instruments(), width=18, editable=True)
        controls = self.field_cell(choice, "Control to learn")
        self.control_buttons = {}
        for title, key in CONTROLS.items():
            self.control_buttons[title] = button(controls, title, lambda value=key: self.choose_control(value), compact=True)
            self.control_buttons[title].pack(side="left", padx=(0, 4))
        self.instrument_box.bind("<<ComboboxSelected>>", lambda _: self.load_selection())
        self.instrument_box.bind("<Return>", lambda _: self.load_selection())
        self.instrument_box.bind("<FocusOut>", lambda _: self.load_selection())

        workspace = tk.Frame(body, background=BG)
        workspace.pack(fill="x")
        workspace.columnconfigure((0, 1), weight=1, uniform="guitar-calibration")
        training = self.section(workspace, "Teach two settings")
        training.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(0, 8))
        label(training, variable=self.control_hint, color=MUTED, wrap=375).pack(anchor="w", pady=(4, 0))
        label(training, "Repeat the same three sustained single notes at each setting.", color=MUTED,
              wrap=375).pack(anchor="w", pady=(2, 0))
        actions = self.row(training, 8)
        self.label_box = self.combo(actions, "Setting on guitar", self.example_label, (), width=18, editable=True)
        self.record = button(actions, "Record example", self.record_example, accent=True, compact=True)
        self.record.pack(side="left", anchor="s", padx=(0, 6))
        self.train = button(actions, "Learn settings", self.train_profile, compact=True)
        self.train.pack(side="left", anchor="s")
        label(training, variable=self.progress_text, strong=True, wrap=375).pack(anchor="w", pady=(9, 0))
        label(training, variable=self.next_step, color=MUTED, wrap=375).pack(anchor="w", pady=(3, 0))
        label(training, variable=self.source_text, color=ACCENT, wrap=375).pack(anchor="w", pady=(3, 0))
        label(training, variable=self.profile_text, color=ACCENT, wrap=375).pack(anchor="w", pady=(5, 0))

        example_tools = self.disclosure(training, "Example library and clip ranges")
        actions = self.row(example_tools, 4)
        self.use_latest = button(actions, "Use latest take", self.select_latest, compact=True)
        self.add_wav = button(actions, "Choose a dry WAV…", self.select_wav, compact=True)
        self.add_take = button(actions, "Save labeled example", self.add_example, compact=True)
        for widget in (self.use_latest, self.add_wav, self.add_take):
            widget.pack(side="left", padx=(0, 6))
        label(example_tools, variable=self.latest_text, color=MUTED, size=9, wrap=375).pack(anchor="w", pady=(4, 0))
        fields = self.row(example_tools, 4)
        self.field(fields, "Start (s, optional)", self.start_seconds, width=10)
        self.field(fields, "End (s, optional)", self.end_seconds, width=10)
        label(example_tools, "Leave the range empty to use the whole recording.", color=MUTED, size=9).pack(anchor="w", pady=(3, 0))
        self.example_list = self.listbox(example_tools, height=4)
        self.example_list.pack(fill="x", pady=(8, 4))
        self.example_list.bind("<<ListboxSelect>>", lambda _: self.set_buttons())
        self.remove = button(example_tools, "Remove selected example", self.remove_example, compact=True)
        self.remove.pack(anchor="e")

        analysis = self.section(workspace, "Check what Tone Lab hears")
        analysis.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=(0, 8))
        actions = self.row(analysis, 8)
        self.record_test = button(actions, "Record a fresh check", self.record_check, accent=True, compact=True)
        self.inspect_take = button(actions, "Check latest dry take", lambda: self.inspect(current=True), compact=True)
        for widget in (self.record_test, self.inspect_take):
            widget.pack(side="left", padx=(0, 5))
        label(analysis, variable=self.estimate_text, color=MUTED, wrap=375).pack(anchor="w", pady=(8, 4))
        label(analysis, "Estimates apply to the settings and notes you taught.", color=MUTED, size=9,
              wrap=375).pack(anchor="w", pady=(0, 4))
        self.observe_box = self.check(analysis, "Show estimate while playing guitar", self.observe, self.change_observe)
        if live_observer:
            label(analysis, variable=self.live_text, color=MUTED, wrap=375).pack(anchor="w", pady=(4, 0))
            label(analysis, variable=self.live_action, color=ACCENT, wrap=375).pack(anchor="w")
        else:
            self.observe_box.pack_forget()
        details = self.disclosure(analysis, "Dry audio and estimate timeline")
        actions = self.row(details, 5)
        self.inspect_wav = button(actions, "Check a dry WAV…", self.inspect, compact=True)
        self.audition = button(actions, "Hear selection", self.listen, compact=True)
        for widget in (self.inspect_wav, self.audition):
            widget.pack(side="left", padx=(0, 5))
        self.timeline = self.listbox(details, height=5)
        self.timeline.pack(fill="x")

        live = card(body)
        live.pack(fill="x", pady=(0, 10))
        gestures = self.disclosure(live, "Gesture mapping")
        label(gestures, "Map a return gesture", strong=True).pack(anchor="w", pady=(8, 0))
        label(gestures, "Pick a starting state and an excursion. A deliberate move away and back can nudge one tone control.",
              color=MUTED, wrap=550).pack(anchor="w", pady=(4, 8))
        mapping = self.row(gestures)
        self.home_box = self.combo(mapping, "Start and return", self.home, (), width=13)
        self.excursion_box = self.combo(mapping, "Move to", self.excursion, (), width=13)
        self.combo(mapping, "Tone control", self.knob, tuple(self.knob_keys), width=13)
        self.field(mapping, "Step (%)", self.step, width=7)
        self.map_save = button(gestures, "Save mapping", self.save_mapping, compact=True)
        self.map_save.pack(anchor="w", pady=(8, 4))
        self.arm_box = self.check(gestures, "Arm the mapped control when listening starts", self.arm)
        if live_observer:
            label(gestures, "Tick this before Show estimate. The control is armed for about 20 seconds from then, "
                            "and it turns the knob on the sound on show.",
                  color=MUTED, size=9, wrap=520).pack(anchor="w", pady=(1, 5))
        else:
            self.arm_box.pack_forget()
            label(gestures, "Check a recorded take to rehearse the mapped gesture.",
                  color=MUTED, size=9, wrap=520).pack(anchor="w", pady=(1, 5))
        self.scroller.bind("<MouseWheel>", self.scroll)
        for panel in (header, training, example_tools, live, gestures, analysis, details):
            for item in panel.winfo_children():
                if isinstance(item, (tk.Frame, tk.Label)):
                    item.bind("<MouseWheel>", self.scroll)
        self.example_label.trace_add("write", lambda *_: self.set_buttons())
        self.review.recorder.status.trace_add("write", lambda *_: self.capture_changed())
        self.load_selection()
        self.capture_changed()

    def scroll(self, event):
        self.scroller.yview_scroll(-int(event.delta / 120), "units")

    @staticmethod
    def instruments():
        names = list(GUITARS)
        for path in folders.calibrated_instruments().glob("*/instrument.json"):
            try:
                name = json.loads(path.read_text(encoding="utf-8"))["name"]
                if isinstance(name, str) and name.strip() and all(name.casefold() != old.casefold() for old in names):
                    names.append(name)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return names

    def section(self, body, title):
        panel = card(body, padding=12)
        label(panel, title, size=13, strong=True).pack(anchor="w")
        return panel

    @staticmethod
    def disclosure(parent, title):
        content = tk.Frame(parent, background=PANEL)

        def toggle():
            if content.winfo_manager():
                content.pack_forget()
                control.configure(text=f"▸  {title}")
            else:
                content.pack(fill="x", pady=(6, 0))
                control.configure(text=f"▾  {title}")

        control = button(parent, f"▸  {title}", toggle, compact=True)
        control.pack(anchor="w", pady=(5, 0))
        return content

    @staticmethod
    def row(parent, pady=0):
        row = tk.Frame(parent, background=PANEL)
        row.pack(fill="x", pady=(pady, 0))
        return row

    def field_cell(self, parent, title):
        cell = tk.Frame(parent, background=PANEL)
        cell.pack(side="left", padx=(0, 8))
        label(cell, title, color=MUTED, size=9).pack(anchor="w", pady=(0, 3))
        return cell

    def field(self, parent, title, variable, width):
        entry(self.field_cell(parent, title), variable, width=width).pack(ipady=4)

    def combo(self, parent, title, variable, values, *, width, editable=False):
        control = ttk.Combobox(self.field_cell(parent, title), textvariable=variable, values=values, width=width,
                               state="normal" if editable else "readonly", style="ToneLab.Guitar.TCombobox")
        style = ttk.Style(parent)
        style.configure("ToneLab.Guitar.TCombobox", fieldbackground=RECESS, foreground=TEXT, background=LINE,
                        arrowcolor=TEXT, padding=4)
        style.map("ToneLab.Guitar.TCombobox", fieldbackground=[("readonly", RECESS)],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", RECESS)],
                  selectforeground=[("readonly", TEXT)])
        control.pack()
        return control

    @staticmethod
    def check(parent, text, variable, command=None):
        widget = tk.Checkbutton(parent, text=text, variable=variable, command=command, font=(FONT, 10),
                                background=PANEL, foreground=TEXT, activebackground=PANEL, activeforeground=TEXT,
                                selectcolor=RECESS, relief="flat", highlightthickness=0, takefocus=True)
        widget.pack(anchor="w")
        return widget

    @staticmethod
    def listbox(parent, *, height):
        return tk.Listbox(parent, height=height, background=RECESS, foreground=TEXT, highlightthickness=1,
                          highlightbackground=LINE, highlightcolor=ACCENT, selectbackground="#3e4c68",
                          selectforeground=TEXT, exportselection=False, font=(FONT, 10), relief="flat")

    def selection(self):
        instrument, control = self.instrument.get().strip(), CONTROLS[self.control.get()]
        if not instrument:
            raise ValueError("Enter a name for this guitar.")
        return instrument, control, profile_folder(instrument)

    def follow_desk_guitar(self):
        if not self.desk_guitar or self.live_running or self.future is not None or self.review.recorder.stop is not None:
            return
        selected = self.desk_guitar.get()
        if not selected or selected == self.last_desk_guitar:
            return
        self.last_desk_guitar = selected
        self.instrument.set(selected)
        self.load_selection()

    def choose_control(self, control):
        if self.live_running or self.future is not None or self.review.recorder.stop is not None:
            return
        for title, key in CONTROLS.items():
            if control in (key, title):
                self.control.set(title)
                self.load_selection()
                return

    def load_selection(self):
        if self.live_running:
            return
        try:
            _, control, folder = self.selection()
        except ValueError as error:
            self.status.set(str(error))
            return
        selected = (folder, control)
        if self.loaded_selection == selected:
            return
        self.loaded_selection = selected
        self.observe.set(False)
        self.arm.set(False)
        self.live_text.set("Live estimate is off.")
        self.live_action.set("")
        self.status.set("")
        self.examples, self.mapping, self.profile = [], None, None
        self.examples_changed = False
        self.example_label.set("")
        self.set_source(None)
        try:
            examples = folder / f"{control}-examples.json"
            if examples.is_file():
                records = json.loads(examples.read_text(encoding="utf-8"))
                if not isinstance(records, list) or any(not self.valid_example(item) for item in records):
                    raise ValueError("Could not read the saved calibration examples for this control.")
                self.examples = records
            path = folder / f"{control}.json"
            if path.is_file():
                self.profile = guitar_state.load_profile(path)
            mapping = folder / f"{control}-gesture.json"
            if mapping.is_file():
                record = json.loads(mapping.read_text(encoding="utf-8"))
                if not isinstance(record, dict):
                    raise ValueError("Could not read the saved gesture mapping.")
                self.mapping = guitar_gestures.GestureMapping.from_dict(record)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.status.set(str(error))
        self.refresh_profile()
        self.update_latest()
        self.set_buttons()

    @staticmethod
    def valid_example(item):
        return (isinstance(item, dict) and all(isinstance(item.get(key), str) and item[key].strip()
                                              for key in ("label", "wav"))
                and all(type(item[key]) in (float, int) and math.isfinite(item[key]) and item[key] >= 0
                        for key in ("start", "end") if key in item))

    def refresh_profile(self):
        self.clear_inspection()
        states = list(self.profile["labels"]) if self.profile is not None else []
        if self.mapping and (self.mapping.home not in states or self.mapping.excursion not in states):
            self.mapping = None
        self.profile_text.set("Learned  ·  " + " / ".join(states) if states else "")
        self.home_box.configure(values=states)
        self.excursion_box.configure(values=states)
        self.home.set(self.mapping.home if self.mapping else states[0] if states else "")
        self.excursion.set(self.mapping.excursion if self.mapping else states[1] if len(states) > 1 else "")
        self.knob.set(KNOBS[self.mapping.control][0] if self.mapping else KNOBS["space"][0])
        self.step.set(f"{100 * self.mapping.step:+g}" if self.mapping else "+10")
        self.refresh_examples()

    def refresh_examples(self):
        _, control, _ = self.selection()
        self.counts = example_counts(self.examples)
        known = [label for label, _ in self.counts.values()]
        suggestions = [label for label in SETTINGS[control] if label.casefold() not in self.counts]
        self.label_box.configure(values=(*suggestions, *known))
        self.control_hint.set(CALIBRATION_HINTS[control])
        self.example_list.delete(0, "end")
        for example in self.examples:
            source = Path(example["wav"])
            span = ""
            if "start" in example or "end" in example:
                span = f"  ·  {example.get('start', 0):g}–{example.get('end', 'end')} s"
            self.example_list.insert("end", f"{example['label']}  ·  {source.parent.name}/{source.name}{span}")
        if self.examples:
            self.progress_text.set("  ·  ".join(f"{label} ×{count}" for label, count in self.counts.values()))
        else:
            self.progress_text.set("No examples yet")
        if not self.counts:
            next_step = f"Start with {SETTINGS[control][0]} or any setting you use."
        elif len(self.counts) == 1:
            next_step = f"Record a different setting, such as {suggestions[0]}. Repeat the same notes."
        elif self.profile is None or self.examples_changed:
            next_step = "Choose Learn settings, or record more examples first."
        else:
            next_step = "Record a fresh take and check which setting Tone Lab hears."
        self.next_step.set(next_step)
        if not self.example_label.get().strip():
            self.example_label.set(self.suggested_setting())
        self.set_buttons()

    def suggested_setting(self):
        settings = SETTINGS[CONTROLS[self.control.get()]]
        return next((setting for setting in settings if setting.casefold() not in self.counts), settings[0])

    def set_buttons(self):
        waiting = self.future is not None
        valid_profile = self.profile is not None
        recorder = self.review.recorder
        recording = recorder.stop is not None
        guided = self.recording_before is not NO_RECORDING and recording
        editable = not (self.live_running or recording or waiting)
        start_allowed = editable and not self.review.busy()
        stop_allowed = guided and not recorder.stop.is_set()
        setting = self.example_label.get().strip()
        caption = setting if len(setting) <= 18 else setting[:17] + "…"
        recording_example = guided and self.recording_kind == "example"
        recording_check = guided and self.recording_kind == "check"
        self.record.configure(text="Stop and save example" if recording_example else f"Record {caption}" if caption else "Record example",
                              state="normal" if (recording_example and stop_allowed or start_allowed and setting) else "disabled")
        self.record_test.configure(text="Stop and check" if recording_check else "Record a fresh check",
                                   state="normal" if (recording_check and stop_allowed or start_allowed and valid_profile) else "disabled")
        self.instrument_box.configure(state="normal" if editable else "disabled")
        for title, choice in self.control_buttons.items():
            selected = title == self.control.get()
            choice.configure(state="normal" if editable else "disabled", bg=ACCENT if selected else "#2c303c",
                             fg=INK if selected else TEXT)
        self.label_box.configure(state="normal" if editable else "disabled")
        for widget, ready in ((self.use_latest, editable and self.latest_dry is not None),
                              (self.add_take, editable and self.example_source is not None and self.example_label.get().strip()),
                              (self.add_wav, editable),
                              (self.train, editable and len(self.counts) >= 2),
                              (self.map_save, editable and valid_profile),
                              (self.inspect_wav, editable and valid_profile),
                              (self.inspect_take, editable and valid_profile and self.latest_dry is not None),
                              (self.audition, editable and self.result_audio is not None),
                              (self.remove, editable and bool(self.example_list.curselection()))):
            widget.configure(state="normal" if ready else "disabled")
        listening = self.live_running and self.observe.get()
        self.observe_box.configure(state="normal" if listening or editable and valid_profile and not self.live_running
                                   else "disabled")
        self.arm_box.configure(state="normal" if editable and self.mapping else "disabled")

    def current_take(self, info=None):
        if info is None:
            info = self.review.latest(pair=True)
        filename = info.get("dry")
        if not filename:
            raise ValueError("The latest take has no verified dry recording.")
        guitar = info.get("guitar")
        if guitar and guitar.casefold() != self.instrument.get().strip().casefold():
            raise ValueError(f"The latest dry take used {guitar}. Record an example on this guitar.")
        path = Path(filename).resolve()
        recorded_at = datetime.fromtimestamp(path.stat().st_mtime)
        details = [info.get("song") or info.get("passage") or f"Take at {recorded_at:%H:%M:%S}"]
        if "duration_seconds" in info:
            details.append(f"{info['duration_seconds']:.1f} s")
        return path, " · ".join(details)

    def update_latest(self):
        try:
            self.latest_dry = self.current_take()
        except (OSError, RuntimeError, ValueError) as error:
            self.latest_dry = None
            self.latest_text.set(str(error))
        else:
            self.latest_text.set(f"Latest dry take · {self.latest_dry[1]}")

    def capture_changed(self):
        recorder = self.review.recorder
        if recorder.stop is None:
            self.update_latest()
            if self.recording_before is not NO_RECORDING:
                before, self.recording_before = self.recording_before, NO_RECORDING
                kind, self.recording_kind = self.recording_kind, None
                setting, self.recording_setting = self.recording_setting, None
                completed_example = getattr(self.review, "completed_example", None)
                try:
                    captured = self.current_take(completed_example()) if completed_example else self.latest_dry
                except (OSError, RuntimeError, ValueError):
                    captured = None
                if captured and captured[0] != before:
                    if kind == "check":
                        self.estimate_text.set("Checking the dry recording…")
                        self.inspect_path(captured[0])
                    else:
                        self.set_source(*captured)
                        self.example_label.set(setting)
                        self.add_example()
                else:
                    status = self.estimate_text if kind == "check" else self.source_text
                    status.set("No new dry recording was saved. Check the recording status above.")
        elif self.recording_before is not NO_RECORDING:
            status = self.estimate_text if self.recording_kind == "check" else self.source_text
            if recorder.stop.is_set():
                status.set("Saving the dry recording…")
            elif recorder.ready.is_set():
                status.set("Recording. Play your notes, then press Stop.")
            else:
                status.set("Connecting to Quad Cortex. Wait for Recording before playing.")
        self.set_buttons()

    def record_example(self):
        self.record_dry("example")

    def record_check(self):
        self.record_dry("check")

    def record_dry(self, kind):
        recorder = self.review.recorder
        if recorder.stop is not None:
            if self.recording_kind == kind and not recorder.stop.is_set():
                recorder.press()
            return
        if self.review.busy() or self.future is not None:
            self.status.set("Finish the current audio task before recording a dry sample.")
            return
        self.load_selection()
        try:
            self.selection()
            setting = self.example_label.get().strip()
            if kind == "example" and not setting:
                raise ValueError("Enter the setting you are about to play.")
            if kind == "check" and self.profile is None:
                raise ValueError("Learn two settings before recording a check.")
        except ValueError as error:
            self.status.set(str(error))
            return
        self.update_latest()
        self.recording_before = self.latest_dry[0] if self.latest_dry else None
        self.recording_kind = kind
        self.recording_setting = setting
        if kind == "example":
            self.set_source(None)
        self.status.set("")
        recorder.press(guitar=self.instrument.get())

    def set_source(self, path, description=""):
        self.example_source = path
        self.start_seconds.set("")
        self.end_seconds.set("")
        self.source_text.set(f"Ready to label · {description}" if path else "")
        self.set_buttons()

    def select_latest(self):
        self.load_selection()
        self.update_latest()
        if self.latest_dry:
            self.set_source(*self.latest_dry)
            self.label_box.focus_set()
            self.status.set("")
        else:
            self.status.set(self.latest_text.get())
            self.set_buttons()

    def select_wav(self):
        self.load_selection()
        try:
            path = self.source(False)
        except (OSError, RuntimeError, ValueError) as error:
            self.status.set(str(error))
            return
        if path is not None:
            self.set_source(path, path.name)
            self.label_box.focus_set()
            self.status.set("")

    def source(self, current):
        if current:
            return self.current_take()[0]
        filename = filedialog.askopenfilename(parent=self.root, title="Choose a dry mono WAV",
                                              filetypes=(("WAV audio", "*.wav"),))
        return Path(filename).resolve() if filename else None

    def add_example(self):
        if self.future is not None or self.live_running or self.review.recorder.stop is not None:
            return
        self.load_selection()
        text = self.example_label.get().strip()
        try:
            _, control, folder = self.selection()
            if not text:
                raise ValueError(f"Name the setting you played, such as {SETTINGS[control][0]}.")
            path = self.example_source
            if path is None:
                raise ValueError("Record an example or choose a dry take first.")
            if not path.is_file():
                raise ValueError("That dry recording is unavailable. Choose another take.")
            fields = (("start", self.start_seconds.get().strip()), ("end", self.end_seconds.get().strip()))
            try:
                span = {key: float(value) for key, value in fields if value}
            except ValueError as error:
                raise ValueError("Enter start and end times in seconds, such as 1.5.") from error
            if (any(not math.isfinite(value) or value < 0 for value in span.values())
                    or ("end" in span and span["end"] <= span.get("start", 0))):
                raise ValueError("Choose a non-negative start and a later end.")
            for existing in self.examples:
                if (Path(existing["wav"]).resolve() == path
                        and max(span.get("start", 0), existing.get("start", 0))
                        < min(span.get("end", math.inf), existing.get("end", math.inf))):
                    raise ValueError(f"This part of the dry take is already labeled {existing['label']}. "
                                     "Use another take or a separate clip range.")
            text = self.counts.get(text.casefold(), (text, 0))[0]
            self.store_examples(folder, control, [*self.examples, {"label": text, "wav": str(path), **span}])
        except (OSError, RuntimeError, ValueError) as error:
            self.status.set(str(error))
        else:
            self.example_label.set(self.suggested_setting())
            self.set_source(None)
            self.status.set(f"Saved {text} as a calibration example.")

    def store_examples(self, folder, control, examples):
        folder.mkdir(parents=True, exist_ok=True)
        folders.write_json(folder / "instrument.json", {"name": self.instrument.get().strip()})
        folders.write_json(folder / f"{control}-examples.json", examples)
        self.instrument_box.configure(values=self.instruments())
        self.examples = examples
        self.examples_changed = True
        self.refresh_examples()

    def remove_example(self):
        self.load_selection()
        selected = self.example_list.curselection()
        if not selected:
            return
        try:
            _, control, folder = self.selection()
            self.store_examples(folder, control, [item for i, item in enumerate(self.examples) if i != selected[0]])
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def train_profile(self):
        self.load_selection()
        try:
            instrument, control, folder = self.selection()
        except ValueError as error:
            self.status.set(str(error))
            return
        examples = list(self.examples)

        def train():
            profile = guitar_state.calibrate(instrument, control, examples)
            folder.mkdir(parents=True, exist_ok=True)
            guitar_state.save_profile(folder / f"{control}.json", profile)
            return profile

        def finish(profile):
            if self.loaded_selection == (folder, control):
                self.profile = profile
                self.examples_changed = False
                self.refresh_profile()
                self.status.set("Settings learned. Record a fresh check at one of the settings you taught.")

        self.run("Learning these settings…", train, finish)

    def save_mapping(self):
        self.load_selection()
        try:
            if self.profile is None:
                raise ValueError("Train a profile with two or more states first.")
            _, control, folder = self.selection()
            mapping = guitar_gestures.GestureMapping(self.home.get(), self.excursion.get(),
                                                     self.knob_keys.get(self.knob.get(), ""),
                                                     float(self.step.get()) / 100)
            folder.mkdir(parents=True, exist_ok=True)
            folders.write_json(folder / f"{control}-gesture.json", mapping.to_dict())
        except (OSError, ValueError) as error:
            self.status.set(str(error))
        else:
            self.mapping = mapping
            self.arm.set(False)
            self.status.set("Mapping saved. Inspect a DI to rehearse it without turning a control.")
            self.set_buttons()

    def change_observe(self):
        """Start or end listening to the dry guitar. The desk can go on playing it meanwhile."""
        if not self.observe.get():
            guitar_gestures.stop()
            if not self.live_running:
                self.live_text.set("Live estimate is off.")
                self.live_action.set("")
            else:
                self.live_action.set("Closing the dry input…")
            self.set_buttons()
            return
        try:
            self.start_listening()
        except (OSError, RuntimeError, ValueError) as error:
            self.observe.set(False)
            self.live_running = False
            self.status.set(str(error))
        self.set_buttons()

    def start_listening(self):
        self.load_selection()
        if self.profile is None:
            raise ValueError("Learn two settings first.")
        if self.review.busy():
            raise RuntimeError("Wait for the recording to finish before listening.")
        instrument, control, _ = self.selection()
        armed = bool(self.mapping and self.arm.get())
        guitar_gestures.start(self.review.desk, instrument=instrument, control=control, arm=armed,
                              listener=self.live_event)
        self.live_running = True
        self.arm.set(False)
        self.live_text.set(f"Listening for {control} on {instrument}. Play a note you taught.")
        self.live_action.set("Waiting for a mapped return gesture." if armed else "Observing only")

    def live_event(self, event):
        kind = event.get("event")
        if kind == "guitar-state":
            state = event.get("label")
            result = estimate_label(state, event.get("confidence", 0))
            text = f"{result}  ·  {event['reason']}" if not state and event.get("reason") else result
            if text != self.live_text.get():
                self.live_text.set(text)
        elif kind == "guitar-gesture":
            action = "Moved" if event.get("applied") else "Observed return gesture for"
            title = KNOBS.get(event["control"], (event["control"],))[0]
            self.live_action.set(f"{action} {title} {event['step']:+.0%}")
        elif kind == "guitar-status":
            self.live_action.set(event["text"])
        elif kind == "guitar-stopped":
            self.live_running = False
            self.observe.set(False)
            self.live_text.set("Live estimate is off.")
            self.live_action.set(f"Stopped: {event['error']}" if event.get("error") else "")
            self.set_buttons()

    def clear_inspection(self):
        self.result_audio = None
        self.result_intervals = []
        if self.profile is None:
            self.estimate_text.set("Learn two settings to enable a check.")
        else:
            self.estimate_text.set("Play the same notes at a setting you taught for a fresh check.")
        self.timeline.delete(0, "end")

    def inspect(self, *, current=False):
        try:
            source = self.source(current)
        except (OSError, RuntimeError, ValueError) as error:
            self.status.set(str(error))
            return
        if source is not None:
            self.inspect_path(source)

    def inspect_path(self, source):
        if self.profile is None or self.future is not None:
            return
        if any(Path(example["wav"]).resolve() == source for example in self.examples):
            self.status.set("This dry take was used for calibration. Record a fresh check, or choose another dry take.")
            return
        profile, mapping = self.profile, self.mapping

        def estimate():
            samples = audio.read_wave(source)
            overall = guitar_state.estimate(profile, samples)
            observations = guitar_state.track(profile, samples)
            events = guitar_gestures.replay(mapping, observations) if mapping else []
            return samples, overall, observations, events

        def finish(result):
            if self.profile is profile:
                self.show_analysis(source, *result)
                self.status.set("Dry check complete.")

        self.run("Analyzing the dry signal…", estimate, finish)

    def show_analysis(self, source, samples, estimate, observations, events):
        self.result_audio = samples
        state = estimate.get("label")
        result = estimate_label(state, estimate.get("confidence", 0))
        reason = f"  ·  {estimate['reason']}" if not state and estimate.get("reason") else ""
        self.estimate_text.set(result + reason)
        self.timeline.delete(0, "end")
        self.result_intervals = []
        points = []
        for event in events:
            control = KNOBS.get(event.control, (event.control,))[0]
            points.append((event.at, f"Return {event.home} → {event.excursion} → {event.home}  ·  {control} {event.step:+.0%}", 0.95))
        last = object()
        for observation in observations:
            current = observation["label"]
            if current != last:
                when = observation["time"]
                score = observation.get("confidence", 0)
                text = estimate_label(current, score)
                points.append((when, text, .35))
                last = current
        for when, text, before in sorted(points):
            self.timeline.insert("end", f"{when:5.2f} s    {text}")
            start = max(0, round((when - before) * audio.RATE))
            self.result_intervals.append((start, start + audio.RATE * 2))
        self.set_buttons()

    def listen(self):
        if self.result_audio is None or self.review.busy():
            self.status.set("Finish recording or live control before playing audio.")
            return
        selected = self.timeline.curselection()
        interval = self.result_intervals[selected[0]] if selected else (0, 4 * audio.RATE)
        samples = self.result_audio[slice(*interval)]
        if len(samples) < audio.RATE // 2:
            self.status.set("There is too little audio in that selection.")
            return
        try:
            _, _, folder = self.selection()
            folder.mkdir(parents=True, exist_ok=True)
            found = measure(samples)
            if found.lufs is None:
                raise ValueError("There is no audible dry signal in that selection.")
            gain = 10 ** (min(-24 - found.lufs, -6 - found.peak_db) / 20)
            path = folder / "preview.wav"
            self.review.stop()
            # 16-bit, which the Windows player plays.
            wavfile.write(path, audio.RATE, np.rint(np.clip(samples * gain, -1, 1) * 32767).astype(np.int16))
            self.review.play_file(path)
        except (OSError, RuntimeError, ValueError) as error:
            self.status.set(str(error))

    def run(self, message, work, finish):
        if self.future is not None:
            return
        self.status.set(message)
        self.future = self.worker.submit(work)
        self.set_buttons()
        self.root.after(110, lambda: self.poll(finish))

    def poll(self, finish):
        if not self.future.done():
            self.root.after(110, lambda: self.poll(finish))
            return
        worker, self.future = self.future, None
        try:
            finish(worker.result())
        except Exception as error:
            LOG.exception("Guitar input experiment failed")
            self.status.set(str(error))
        finally:
            self.set_buttons()


# ---- on the new desk ----

def _studio(desk):
    return getattr(desk, "studio", None) or getattr(getattr(desk, "desk", None), "studio", None)


class Recorder:
    """Records a take on the desk's seat, as the Guitar Learner does, and says how it goes through `status`.

    `press` starts a recording, and stops it when one is running. When the
    take is saved, `stop` is None again and `result` is its folder.
    """

    def __init__(self, bench):
        self.bench = bench
        self.stop = self.result = None
        self.ready = Event()
        self.started = False
        self.guitar = GUITARS[0]
        self.status = tk.StringVar(master=bench.window)

    def press(self, *, guitar=None):
        if self.stop is not None:
            self.stop.set()
            self.status.set("Saving the take…")
            return
        desk = self.bench.desk
        if desk.leaving:
            return
        if desk.job is not None:
            self.status.set("Tone Lab is using the recorder. Wait for that action to finish.")
            return
        guitar_gestures.stop()
        self.stop, self.ready, self.started = Event(), Event(), False
        self.guitar = guitar or self.guitar
        self.status.set("Connecting to Quad Cortex…")
        self.poll()

    def poll(self):
        if self.stop is None:
            return
        desk = self.bench.desk
        if not self.started:
            if self.stop.is_set() or desk.leaving:
                self.stop = None
                self.status.set("Recording cancelled.")
                return
            if desk.job is None and not guitar_gestures.running():
                self.started = True
                desk.ready, desk.stop_signal, desk.live_is_ready = self.ready, self.stop, False
                desk.submit("record", "Opening the Quad Cortex inputs for guitar calibration…", self.capture,
                            desk.input_channel, self.guitar, self.stop, self.ready, done=self.recorded)
        elif self.ready.is_set() and not self.stop.is_set():
            self.status.set("Recording · dry + wet")
        self.bench.window.after(100, self.poll)

    @staticmethod
    def capture(channel, guitar, stop, ready):
        try:
            return takes.record_take(channel, guitar, stop, ready), None
        except Exception as error:  # noqa: BLE001
            LOG.exception("Guitar calibration recording failed")
            return None, str(error)

    def recorded(self, outcome):
        result, error = outcome
        self.result = result[0] if result else None
        self.stop = None
        desk = self.bench.desk
        desk.takes = takes.clip_paths()
        message = error or result[1]
        desk.set_status(message)
        self.status.set(message)


class Bench:
    """What the calibration panel asks of the screen around it, provided by the desk's seat."""

    def __init__(self, desk, window):
        self.desk, self.window, self.root = desk, window, window
        studio = _studio(desk)
        self.guitar = tk.StringVar(master=window, value=studio.state.instrument if studio is not None else GUITARS[0])
        self.recorder = Recorder(self)

    def follow(self):
        studio = _studio(self.desk)
        if studio is not None and studio.state.instrument != self.guitar.get():
            self.guitar.set(studio.state.instrument)

    @staticmethod
    def latest(*, pair=True):
        return takes.latest_clip()

    def completed_example(self):
        folder = self.recorder.result
        if folder is None:
            return {}
        for name in ("dry.wav", "inputs.wav"):
            if (Path(folder) / name).is_file():
                return takes.clip_info(Path(folder) / name)
        return {}

    def busy(self):
        return self.recorder.stop is not None or self.desk.job is not None or bool(self.desk.leaving)

    @staticmethod
    def stop():
        import winsound

        winsound.PlaySound(None, 0)

    def play_file(self, path):
        import winsound

        if self.busy():
            raise RuntimeError("Wait for the current recording first.")
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


class CalibrationWindow:
    def __init__(self, desk):
        self.desk = desk
        self.window = tk.Toplevel(desk.root, background=BG)
        self.window.withdraw()
        self.window.title("Guitar input calibration · Tone Lab")
        self.window.minsize(900, 620)
        self.window.geometry("1000x760")
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        self.window.bind("<Escape>", lambda _: self.hide())
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="guitar-calibration")
        page = tk.Frame(self.window, background=BG, padx=20, pady=16)
        page.pack(fill="both", expand=True)
        header = tk.Frame(page, background=BG)
        header.pack(fill="x", pady=(0, 10))
        tk.Label(header, text="Guitar input calibration", font=("Georgia", 22), background=BG,
                 foreground=TEXT).pack(side="left")
        button(header, "Back to Tone Lab", self.hide, compact=True).pack(side="right")
        body = tk.Frame(page, background=BG)
        body.pack(fill="both", expand=True)
        self.bench = Bench(desk, self.window)
        self.panel = GuitarInputPanel(self.bench, body, self.worker)

    def show(self):
        from .window import dark_title

        self.window.deiconify()
        dark_title(self.window)
        self.window.lift()
        self.window.focus_set()
        self.bench.follow()
        self.panel.follow_desk_guitar()

    def hide(self):
        """Hide the window. A recording stops; gesture listening goes on, so the guitar can be played on the desk."""
        if self.bench.recorder.stop is not None:
            self.bench.recorder.stop.set()
        self.window.withdraw()
        self.desk.root.lift()

    def shutdown(self):
        if self.bench.recorder.stop is not None:
            self.bench.recorder.stop.set()
        guitar_gestures.stop()
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.window.destroy()


_windows = {}


def open(desk):  # noqa: A001 - the name the desk calls
    """Show the calibration window for this desk, opening it the first time."""
    found = _windows.get(id(desk))
    if found is None or not found.window.winfo_exists():
        found = _windows[id(desk)] = CalibrationWindow(desk)
    found.show()
    return found
