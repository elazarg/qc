"""Opt-in calibration, dry-signal inspection, and gesture mapping."""

from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import tkinter as tk
from tkinter import filedialog, ttk

from . import audio, diagnostics, session, styles
from .widgets import ACCENT, BG, FONT, LINE, MUTED, PANEL, RECESS, TEXT, add_scrollbar, button, card, entry, label


CONTROLS = {"Pickup selector": "pickup", "Tone knob": "tone", "Volume knob": "volume"}
SETTINGS = {
    "pickup": ("Neck", "Bridge", "Middle"),
    "tone": ("Tone 10", "Tone 0", "Tone 5"),
    "volume": ("Volume 10", "Volume 5", "Volume 2"),
}
NO_RECORDING = object()


def profile_folder(instrument):
    key = instrument.strip().casefold()
    prefix = re.sub(r"[^a-z0-9]+", "-", key).strip("-")[:35] or "guitar"
    return session.SESSIONS / "guitar_input" / f"{prefix}-{sha256(key.encode()).hexdigest()[:8]}"


def example_counts(examples):
    counts = {}
    for example in examples:
        label = example["label"].strip()
        key = label.casefold()
        name, count = counts.get(key, (label, 0))
        counts[key] = (name, count + 1)
    return counts


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
        self.examples_changed = False
        self.result_audio = None
        self.result_intervals = []
        self.loaded_selection = None
        self.instrument = tk.StringVar(value=session.GUITARS[0])
        self.control = tk.StringVar(value=next(iter(CONTROLS)))
        self.example_label = tk.StringVar()
        self.start_seconds, self.end_seconds = tk.StringVar(), tk.StringVar()
        self.profile_text = tk.StringVar()
        self.latest_text, self.source_text = tk.StringVar(), tk.StringVar()
        self.progress_text, self.next_step = tk.StringVar(), tk.StringVar()
        self.status = tk.StringVar()
        self.estimate_text = tk.StringVar()
        self.live_text = tk.StringVar(value="Live input is off.")
        self.live_action = tk.StringVar()
        self.observe, self.arm = tk.BooleanVar(), tk.BooleanVar()
        self.home, self.excursion, self.knob = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.step = tk.StringVar(value="+10")
        self.knob_keys = {spec[0]: key for key, spec in styles.KNOBS.items()}

        label(parent, variable=self.status, color=ACCENT, wrap=550).pack(side="bottom", fill="x", pady=(5, 0))
        self.scroller = tk.Canvas(parent, background=BG, highlightthickness=0)
        self.scroller.pack(side="left", fill="both", expand=True)
        add_scrollbar(parent, self.scroller)
        body = tk.Frame(self.scroller, background=BG)
        frame = self.scroller.create_window((0, 0), anchor="nw", window=body)
        body.bind("<Configure>", lambda _: self.scroller.configure(scrollregion=self.scroller.bbox("all")))
        self.scroller.bind("<Configure>", lambda event: self.scroller.itemconfigure(frame, width=event.width))

        header = card(body)
        header.pack(fill="x", pady=(0, 10))
        label(header, "Guitar input · experimental", size=16, strong=True).pack(anchor="w")
        label(header, "Teach Tone Lab how this guitar sounds at different settings.", color=MUTED,
              wrap=550).pack(anchor="w", pady=(4, 9))
        choice = self.row(header)
        self.instrument_box = self.combo(choice, "Instrument", self.instrument, self.instruments(), width=18, editable=True)
        self.control_box = self.combo(choice, "Control to teach", self.control, tuple(CONTROLS), width=16)
        self.instrument_box.bind("<<ComboboxSelected>>", lambda _: self.load_selection())
        self.instrument_box.bind("<Return>", lambda _: self.load_selection())
        self.instrument_box.bind("<FocusOut>", lambda _: self.load_selection())
        self.control_box.bind("<<ComboboxSelected>>", lambda _: self.load_selection())

        training = self.section(body, "Teach one setting at a time")
        label(training, "Set the guitar, then play three sustained single notes. Use the same strings, frets, and picking "
              "for every example and keep the other guitar controls fixed.", color=MUTED, wrap=530).pack(anchor="w", pady=(4, 8))
        actions = self.row(training)
        self.record = button(actions, "Record example", self.record_example, accent=True, compact=True)
        self.use_latest = button(actions, "Use latest take", self.select_latest, compact=True)
        for widget in (self.record, self.use_latest):
            widget.pack(side="left", padx=(0, 6))
        label(training, variable=self.latest_text, color=MUTED, size=9, wrap=530).pack(anchor="w", pady=(4, 0))
        label(training, variable=self.source_text, color=ACCENT, wrap=530).pack(anchor="w", pady=(9, 0))
        fields = self.row(training, 4)
        self.label_box = self.combo(fields, "Setting played", self.example_label, (), width=19, editable=True)
        self.label_box.bind("<Return>", lambda _: self.add_example())
        self.add_take = button(fields, "Save example", self.add_example, compact=True)
        self.add_take.pack(side="left", anchor="s", pady=1)
        label(training, variable=self.progress_text, strong=True, wrap=530).pack(anchor="w", pady=(12, 0))
        label(training, variable=self.next_step, color=MUTED, wrap=530).pack(anchor="w", pady=(3, 0))
        self.train = button(training, "Train profile", self.train_profile, accent=True, compact=True)
        self.train.pack(anchor="w", pady=(6, 0))
        label(training, variable=self.profile_text, color=ACCENT, wrap=530).pack(anchor="w", pady=(5, 0))

        example_tools = self.disclosure(training, "Example library and clip ranges")
        self.add_wav = button(example_tools, "Choose a dry WAV…", self.select_wav, compact=True)
        self.add_wav.pack(anchor="w", pady=(5, 2))
        fields = self.row(example_tools, 4)
        self.field(fields, "Start (s, optional)", self.start_seconds, width=10)
        self.field(fields, "End (s, optional)", self.end_seconds, width=10)
        label(example_tools, "Leave the range empty to use the whole recording.", color=MUTED, size=9).pack(anchor="w", pady=(3, 0))
        self.example_list = self.listbox(example_tools, height=4)
        self.example_list.pack(fill="x", pady=(8, 4))
        self.example_list.bind("<<ListboxSelect>>", lambda _: self.set_buttons())
        self.remove = button(example_tools, "Remove selected example", self.remove_example, compact=True)
        self.remove.pack(anchor="e")

        analysis = self.section(body, "Check a fresh take")
        actions = self.row(analysis, 8)
        self.inspect_take = button(actions, "Check latest dry take", lambda: self.inspect(current=True), compact=True)
        self.inspect_wav = button(actions, "Choose a dry WAV…", self.inspect, compact=True)
        self.audition = button(actions, "Hear selection", self.listen, compact=True)
        for widget in (self.inspect_take, self.inspect_wav, self.audition):
            widget.pack(side="left", padx=(0, 5))
        label(analysis, variable=self.estimate_text, color=MUTED, wrap=550).pack(anchor="w", pady=(8, 4))
        self.timeline = self.listbox(analysis, height=5)
        self.timeline.pack(fill="x")

        live = card(body)
        live.pack(fill="x", pady=(0, 10))
        gestures = self.disclosure(live, "Live observation and gesture mapping" if live_observer else "Gesture mapping")
        self.observe_box = self.check(gestures, "Observe during Live guitar", self.observe, self.change_observe)
        if live_observer:
            label(gestures, variable=self.live_text, color=MUTED, wrap=550).pack(anchor="w", pady=(4, 0))
            label(gestures, variable=self.live_action, color=ACCENT, wrap=550).pack(anchor="w")
        else:
            self.observe_box.pack_forget()
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
        self.arm_box = self.check(gestures, "Arm mapped control for the next run", self.arm)
        if live_observer:
            label(gestures, "The control is armed for about 20 seconds at the start of the run.",
                  color=MUTED, size=9, wrap=520).pack(anchor="w", pady=(1, 5))
        else:
            self.arm_box.pack_forget()
            label(gestures, "Check a recorded take to rehearse the mapped gesture.",
                  color=MUTED, size=9, wrap=520).pack(anchor="w", pady=(1, 5))
        self.scroller.bind("<MouseWheel>", self.scroll)
        for panel in (header, training, example_tools, live, gestures, analysis):
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
        names = list(session.GUITARS)
        for path in (session.SESSIONS / "guitar_input").glob("*/instrument.json"):
            try:
                name = json.loads(path.read_text(encoding="utf-8"))["name"]
                if isinstance(name, str) and name.strip() and all(name.casefold() != old.casefold() for old in names):
                    names.append(name)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return names

    def section(self, body, title):
        panel = card(body)
        panel.pack(fill="x", pady=(0, 10))
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
                          highlightbackground=LINE, highlightcolor=ACCENT, selectbackground="#3b5940",
                          selectforeground=TEXT, exportselection=False, font=(FONT, 10), relief="flat")

    def selection(self):
        instrument, control = self.instrument.get().strip(), CONTROLS[self.control.get()]
        if not instrument:
            raise ValueError("Enter a name for this guitar.")
        return instrument, control, profile_folder(instrument)

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
        self.live_text.set("Live input is off.")
        self.live_action.set("")
        self.status.set("")
        self.examples, self.mapping, self.profile = [], None, None
        self.examples_changed = False
        self.example_label.set("")
        self.set_source(None)
        from . import guitar_gestures, guitar_state

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
        self.profile_text.set("Trained settings  ·  " + " / ".join(states) if states else "")
        self.home_box.configure(values=states)
        self.excursion_box.configure(values=states)
        self.home.set(self.mapping.home if self.mapping else states[0] if states else "")
        self.excursion.set(self.mapping.excursion if self.mapping else states[1] if len(states) > 1 else "")
        self.knob.set(styles.KNOBS[self.mapping.control][0] if self.mapping else styles.KNOBS["space"][0])
        self.step.set(f"{100 * self.mapping.step:+g}" if self.mapping else "+10")
        self.refresh_examples()

    def refresh_examples(self):
        _, control, _ = self.selection()
        self.counts = example_counts(self.examples)
        known = [label for label, _ in self.counts.values()]
        suggestions = [label for label in SETTINGS[control] if label.casefold() not in self.counts]
        self.label_box.configure(values=(*known, *suggestions))
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
            next_step = "Train the profile now, or add more examples to teach additional settings."
        else:
            next_step = "Record a fresh take and check which setting Tone Lab hears."
        self.next_step.set(next_step)
        self.set_buttons()

    def set_buttons(self):
        waiting = self.future is not None
        valid_profile = self.profile is not None
        recorder = self.review.recorder
        recording = recorder.stop is not None
        guided = self.recording_before is not NO_RECORDING and recording
        editable = not (self.live_running or recording or waiting)
        self.record.configure(text="Stop and save example" if guided else "Record example",
                              state="normal" if ((guided and not recorder.stop.is_set())
                                                 or (editable and not self.review.busy())) else "disabled")
        self.instrument_box.configure(state="normal" if editable else "disabled")
        self.control_box.configure(state="readonly" if editable else "disabled")
        self.label_box.configure(state="normal" if editable else "disabled")
        for widget, ready in ((self.use_latest, editable and self.latest_dry is not None),
                              (self.add_take, editable and self.example_source is not None and self.example_label.get().strip()),
                              (self.add_wav, editable), (self.train, editable and len(self.counts) >= 2),
                              (self.map_save, editable and valid_profile),
                              (self.inspect_wav, editable and valid_profile),
                              (self.inspect_take, editable and valid_profile and self.latest_dry is not None),
                              (self.audition, editable and self.result_audio is not None),
                              (self.remove, editable and bool(self.example_list.curselection()))):
            widget.configure(state="normal" if ready else "disabled")
        self.observe_box.configure(state="normal" if editable and valid_profile else "disabled")
        self.arm_box.configure(state="normal" if editable and self.observe.get() and self.mapping else "disabled")

    def current_take(self):
        info = self.review.latest(pair=True)
        filename = info.get("dry")
        if not filename:
            raise ValueError("The latest take has no verified dry recording.")
        path = Path(filename).resolve()
        recorded_at = datetime.fromtimestamp(path.stat().st_mtime)
        details = [info.get("song") or info.get("passage") or f"Take at {recorded_at:%H:%M:%S}"]
        if "duration_seconds" in info:
            details.append(f"{info['duration_seconds']:.1f} s")
        return path, " · ".join(details)

    def update_latest(self):
        try:
            self.latest_dry = self.current_take()
        except (OSError, RuntimeError, ValueError):
            self.latest_dry = None
        self.latest_text.set(f"Latest dry take · {self.latest_dry[1]}" if self.latest_dry else "No dry take ready to use")

    def capture_changed(self):
        recorder = self.review.recorder
        if recorder.stop is None:
            self.update_latest()
            if self.recording_before is not NO_RECORDING:
                before, self.recording_before = self.recording_before, NO_RECORDING
                if self.latest_dry and self.latest_dry[0] != before:
                    self.set_source(*self.latest_dry)
                else:
                    self.source_text.set("No new dry example was saved. Check the recording status above.")
        elif self.recording_before is not NO_RECORDING:
            if recorder.stop.is_set():
                self.source_text.set("Saving the example…")
            elif recorder.ready.is_set():
                self.source_text.set("Recording. Play your notes, then stop and save the example.")
            else:
                self.source_text.set("Connecting to Quad Cortex. Wait for Recording before playing.")
        self.set_buttons()

    def record_example(self):
        recorder = self.review.recorder
        if recorder.stop is not None:
            if self.recording_before is not NO_RECORDING and not recorder.stop.is_set():
                recorder.press()
            return
        if self.review.busy() or self.future is not None:
            self.status.set("Finish the current audio task before recording an example.")
            return
        self.load_selection()
        try:
            self.selection()
        except ValueError as error:
            self.status.set(str(error))
            return
        self.update_latest()
        self.recording_before = self.latest_dry[0] if self.latest_dry else None
        self.set_source(None)
        self.status.set("")
        recorder.press(guitar=self.instrument.get())

    def set_source(self, path, description=""):
        self.example_source = path
        self.start_seconds.set("")
        self.end_seconds.set("")
        self.source_text.set(f"Ready to label · {description}" if path else "Record an example or choose a dry take.")
        self.set_buttons()

    def select_latest(self):
        self.load_selection()
        self.update_latest()
        if self.latest_dry:
            self.set_source(*self.latest_dry)
            self.label_box.focus_set()
            self.status.set("")
        else:
            self.status.set("The latest take has no verified dry recording.")
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
            self.example_label.set("")
            self.set_source(None)
            self.status.set(f"Saved {text} as a calibration example.")

    def store_examples(self, folder, control, examples):
        folder.mkdir(parents=True, exist_ok=True)
        session.write_json(folder / "instrument.json", {"name": self.instrument.get().strip()})
        session.write_json(folder / f"{control}-examples.json", examples)
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
        from . import guitar_state

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
                self.status.set("Profile saved. Try a new take with the same settings.")

        self.run("Training this profile…", train, finish)

    def save_mapping(self):
        from . import guitar_gestures

        self.load_selection()
        try:
            if self.profile is None:
                raise ValueError("Train a profile with two or more states first.")
            _, control, folder = self.selection()
            mapping = guitar_gestures.GestureMapping(self.home.get(), self.excursion.get(),
                                                     self.knob_keys.get(self.knob.get(), ""),
                                                     float(self.step.get()) / 100)
            folder.mkdir(parents=True, exist_ok=True)
            session.write_json(folder / f"{control}-gesture.json", mapping.to_dict())
        except (OSError, ValueError) as error:
            self.status.set(str(error))
        else:
            self.mapping = mapping
            self.arm.set(False)
            self.status.set("Mapping saved. Inspect a DI to rehearse it without turning a control.")
            self.set_buttons()

    def change_observe(self):
        if not self.observe.get():
            self.arm.set(False)
        self.live_text.set("Live input will observe this profile on the next run." if self.observe.get() else "Live input is off.")
        self.live_action.set("")
        self.set_buttons()

    def live_config(self):
        self.load_selection()
        if not self.observe.get() or self.profile is None:
            return None
        _, control, folder = self.selection()
        armed = bool(self.mapping and self.arm.get())
        self.live_running = True
        self.arm.set(False)
        self.live_text.set("Waiting for dry input. Use Stop live to end.")
        self.live_action.set("Waiting for a mapped return gesture." if armed else "Observing only")
        self.set_buttons()
        return {"profile": str((folder / f"{control}.json").resolve()),
                "gesture": self.mapping.to_dict() if self.mapping else None, "arm": armed}

    def live_event(self, event):
        kind = event.get("event")
        if kind == "guitar-state":
            state = event.get("label")
            text = f"{event['control'].capitalize()}  ·  {state} ({event['confidence']:.0%})" if state else "No reliable state yet"
            if text != self.live_text.get():
                self.live_text.set(text)
        elif kind == "guitar-gesture":
            action = "Moved" if event.get("applied") else "Observed return gesture for"
            title = styles.KNOBS.get(event["control"], (event["control"],))[0]
            self.live_action.set(f"{action} {title} {event['step']:+.0%}")
        elif kind == "guitar-status":
            self.live_action.set(event["text"])
        elif kind == "guitar-stopped":
            self.live_running = False
            self.change_observe()

    def clear_inspection(self):
        self.result_audio = None
        self.result_intervals = []
        if self.profile is None:
            self.estimate_text.set("Train a profile above to check a dry take.")
        else:
            self.estimate_text.set("Record a new take at a setting you taught, then check it here.")
        self.timeline.delete(0, "end")

    def inspect(self, *, current=False):
        from . import guitar_gestures, guitar_state

        try:
            source = self.source(current)
        except (OSError, RuntimeError, ValueError) as error:
            self.status.set(str(error))
            return
        if source is None or self.profile is None:
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
                self.status.set("Inspection finished. No hardware controls were moved.")

        self.run("Analyzing the dry signal…", estimate, finish)

    def show_analysis(self, source, samples, estimate, observations, events):
        self.result_audio = samples
        state = estimate.get("label")
        result = f"{state} · score {estimate.get('confidence', 0):.0%}" if state else "No reliable overall state"
        self.estimate_text.set(f"{source.name}  ·  {result}  ·  {len(events)} return gestures")
        self.timeline.delete(0, "end")
        self.result_intervals = []
        points = []
        for event in events:
            control = styles.KNOBS.get(event.control, (event.control,))[0]
            points.append((event.at, f"Return {event.home} → {event.excursion} → {event.home}  ·  {control} {event.step:+.0%}", 0.95))
        last = object()
        for observation in observations:
            current = observation["label"]
            if current != last:
                when = observation["time"]
                score = observation.get("confidence", 0)
                text = f"{current}  ·  score {score:.0%}" if current else "Uncertain or quiet"
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
            measures = audio.describe(samples)
            if measures["lufs"] is None:
                raise ValueError("There is no audible dry signal in that selection.")
            gain = 10 ** (min(-24 - measures["lufs"], -6 - measures["peak_dbfs"]) / 20)
            path = folder / "preview.wav"
            self.review.stop()
            audio.write_wave(path, samples * gain, pcm16=True)
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
            diagnostics.LOG.exception("Guitar input experiment failed")
            self.status.set(str(error))
        finally:
            self.set_buttons()
