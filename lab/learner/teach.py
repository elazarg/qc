"""Teach a control: the window where the app is taught to tell one guitar control's settings apart by sound.

    from lab.learner import teach
    teach.open(seat)        # the seat of lab.learner.seat.LearnerSeat

One control at a time (pickup selector, volume or tone) is taught from takes
recorded at two or more of its settings, with the others held fixed. A fresh
take is then checked against what was taught, with a timeline of the settings
heard and of any mapped return gesture. "Show estimate while playing guitar"
starts gesture listening (`lab.learner.gestures`), which turns the mapped
Shape knob on the desk's sound while the gesture is armed.

Everything that is not a widget is in `lab.learner.taught`.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Event
import tkinter as tk
from tkinter import filedialog, ttk

from scipy.io import wavfile

from lab.hearing import audio
from lab.desk import theme as palette
from lab.sound.knobs import KNOBS
from lab.learner import folders, gestures as guitar_gestures, inputs, taught, takes
from lab.learner.taught import CONTROLS, HINTS

TITLE = "Teach a control"

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


class TeachPanel:
    def __init__(self, seat, parent, worker):
        self.seat, self.root, self.worker = seat, parent.winfo_toplevel(), worker
        self.working = False                # a fit or a check is running
        self.recording = None               # {"kind", "setting", "guitar", "stop", "ready", "started", "said"}
        self.live_running = False
        self.profile = self.mapping = None
        self.examples, self.counts = [], {}
        self.latest_dry = self.example_source = None
        self.examples_changed = False
        self.result_audio = None
        self.result_intervals = []
        self.loaded_selection = None
        self.last_desk_guitar = seat.guitar if seat.guitar in taught.instruments() else taught.GUITARS[0]
        self.instrument = tk.StringVar(master=self.root, value=self.last_desk_guitar)
        self.control = tk.StringVar(master=self.root, value=next(iter(CONTROLS)))
        self.example_label = tk.StringVar(master=self.root)
        self.start_seconds, self.end_seconds = tk.StringVar(master=self.root), tk.StringVar(master=self.root)
        self.profile_text = tk.StringVar(master=self.root)
        self.latest_text, self.source_text = tk.StringVar(master=self.root), tk.StringVar(master=self.root)
        self.progress_text, self.next_step = tk.StringVar(master=self.root), tk.StringVar(master=self.root)
        self.control_hint = tk.StringVar(master=self.root)
        self.status = tk.StringVar(master=self.root)
        self.estimate_text = tk.StringVar(master=self.root)
        self.live_text = tk.StringVar(master=self.root, value="Live estimate is off.")
        self.live_action = tk.StringVar(master=self.root)
        self.observe, self.arm = tk.BooleanVar(master=self.root), tk.BooleanVar(master=self.root)
        self.home, self.excursion = tk.StringVar(master=self.root), tk.StringVar(master=self.root)
        self.knob = tk.StringVar(master=self.root)
        self.step = tk.StringVar(master=self.root, value="+10")
        self.knob_keys = {spec[0]: key for key, spec in KNOBS.items()}
        self.build(parent)
        self.example_label.trace_add("write", lambda *_: self.set_buttons())
        self.load_selection()

    def build(self, parent):
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
        self.instrument_box = self.combo(choice, "Instrument", self.instrument, taught.instruments(), width=18, editable=True)
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
        workspace.columnconfigure((0, 1), weight=1, uniform="teach-a-control")
        training = self.section(workspace, "Teach two settings")
        training.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=(0, 8))
        label(training, variable=self.control_hint, color=MUTED, wrap=375).pack(anchor="w", pady=(4, 0))
        label(training, "Repeat the same three sustained single notes at each setting.", color=MUTED,
              wrap=375).pack(anchor="w", pady=(2, 0))
        actions = self.row(training, 8)
        self.label_box = self.combo(actions, "Setting on guitar", self.example_label, (), width=18, editable=True)
        self.record = button(actions, "Record example", lambda: self.record_dry("example"), accent=True, compact=True)
        self.record.pack(side="left", anchor="s", padx=(0, 6))
        self.train = button(actions, "Learn settings", self.fit_profile, compact=True)
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
        self.record_test = button(actions, "Record a fresh check", lambda: self.record_dry("check"), accent=True, compact=True)
        self.inspect_take = button(actions, "Check latest dry take", lambda: self.inspect(current=True), compact=True)
        for widget in (self.record_test, self.inspect_take):
            widget.pack(side="left", padx=(0, 5))
        label(analysis, variable=self.estimate_text, color=MUTED, wrap=375).pack(anchor="w", pady=(8, 4))
        label(analysis, "An estimate says which taught setting the notes are closest to, and how close: it applies "
                        "to the settings and notes you taught.", color=MUTED, size=9,
              wrap=375).pack(anchor="w", pady=(0, 4))
        self.observe_box = self.check(analysis, "Show estimate while playing guitar", self.observe, self.change_observe)
        label(analysis, variable=self.live_text, color=MUTED, wrap=375).pack(anchor="w", pady=(4, 0))
        label(analysis, variable=self.live_action, color=ACCENT, wrap=375).pack(anchor="w")
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
        mapped = self.disclosure(live, "Gesture mapping")
        label(mapped, "Map a return gesture", strong=True).pack(anchor="w", pady=(8, 0))
        label(mapped, "Pick a starting setting and an excursion. A deliberate move away and back, about a second at "
                      "the excursion, can nudge one tone control.",
              color=MUTED, wrap=550).pack(anchor="w", pady=(4, 8))
        mapping = self.row(mapped)
        self.home_box = self.combo(mapping, "Start and return", self.home, (), width=13)
        self.excursion_box = self.combo(mapping, "Move to", self.excursion, (), width=13)
        self.combo(mapping, "Tone control", self.knob, tuple(self.knob_keys), width=13)
        self.field(mapping, "Step (%)", self.step, width=7)
        self.map_save = button(mapped, "Save mapping", self.save_mapping, compact=True)
        self.map_save.pack(anchor="w", pady=(8, 4))
        self.arm_box = self.check(mapped, "Arm the mapped control when listening starts", self.arm)
        label(mapped, f"Tick this before Show estimate. The control is armed for {guitar_gestures.ARM_SECONDS:.0f} "
                      "seconds, and for as long again after each gesture; it turns the knob on the sound on show.",
              color=MUTED, size=9, wrap=520).pack(anchor="w", pady=(1, 5))
        self.scroller.bind("<MouseWheel>", self.scroll)
        for panel in (header, training, example_tools, live, mapped, analysis, details):
            for item in panel.winfo_children():
                if isinstance(item, (tk.Frame, tk.Label)):
                    item.bind("<MouseWheel>", self.scroll)

    def scroll(self, event):
        self.scroller.yview_scroll(-int(event.delta / 120), "units")

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

    # ---- what is chosen ----

    def selection(self):
        instrument, control = self.instrument.get().strip(), CONTROLS[self.control.get()]
        if not instrument:
            raise ValueError("Enter a name for this guitar.")
        return instrument, control

    def busy(self):
        """Something is using the audio input, or the desk is closing."""
        return self.recording is not None or self.seat.job is not None or bool(self.seat.leaving)

    def idle(self):
        return not (self.live_running or self.working or self.recording is not None)

    def follow_desk_guitar(self):
        """Take the instrument chosen on the desk when the window is shown, unless something is under way."""
        selected = self.seat.guitar
        if not self.idle() or not selected or selected == self.last_desk_guitar:
            return
        self.last_desk_guitar = selected
        if selected in taught.instruments():
            self.instrument.set(selected)
            self.load_selection()

    def choose_control(self, control):
        if not self.idle():
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
            selected = self.selection()
        except ValueError as error:
            self.status.set(str(error))
            return
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
            self.examples, self.profile, self.mapping = taught.load(*selected)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.status.set(str(error))
        self.refresh_profile()
        self.update_latest()
        self.set_buttons()

    def refresh_profile(self):
        self.clear_inspection()
        settings = list(self.profile["labels"]) if self.profile is not None else []
        if self.mapping and (self.mapping.home not in settings or self.mapping.excursion not in settings):
            self.mapping = None
        self.profile_text.set("Learned  ·  " + " / ".join(settings) if settings else "")
        self.home_box.configure(values=settings)
        self.excursion_box.configure(values=settings)
        self.home.set(self.mapping.home if self.mapping else settings[0] if settings else "")
        self.excursion.set(self.mapping.excursion if self.mapping else settings[1] if len(settings) > 1 else "")
        self.knob.set(KNOBS[self.mapping.control][0] if self.mapping else KNOBS["space"][0])
        self.step.set(f"{100 * self.mapping.step:+g}" if self.mapping else "+10")
        self.refresh_examples()

    def refresh_examples(self):
        instrument, control = self.selection()
        offered = taught.settings(instrument, control)
        self.counts = taught.example_counts(self.examples)
        known = [name for name, _ in self.counts.values()]
        suggestions = [name for name in offered if name.casefold() not in self.counts]
        self.label_box.configure(values=(*suggestions, *known))
        self.control_hint.set(HINTS[control])
        self.example_list.delete(0, "end")
        for example in self.examples:
            span = f"  ·  {example['source_start']:g}–{example['source_end']:g} s"
            self.example_list.insert("end", f"{example['label']}  ·  {example['source_label']}{span}")
        if self.examples:
            self.progress_text.set("  ·  ".join(f"{name} ×{count}" for name, count in self.counts.values()))
        else:
            self.progress_text.set("No examples yet")
        if not self.counts:
            next_step = f"Start with {offered[0]} or any setting you use."
        elif len(self.counts) == 1:
            other = suggestions[0] if suggestions else "another one"
            next_step = f"Record a different setting, such as {other}. Repeat the same notes."
        elif self.profile is None or self.examples_changed:
            next_step = "Choose Learn settings, or record more examples first."
        else:
            next_step = "Record a fresh take and check which setting Tone Lab hears."
        self.next_step.set(next_step)
        if not self.example_label.get().strip():
            self.example_label.set(self.suggested_setting())
        self.set_buttons()

    def suggested_setting(self):
        offered = taught.settings(*self.selection())
        return next((name for name in offered if name.casefold() not in self.counts), offered[0])

    def set_buttons(self):
        valid_profile = self.profile is not None
        recording = self.recording
        editable = self.idle()
        start_allowed = editable and not self.busy()
        stop_allowed = recording is not None and not recording["stop"].is_set()
        setting = self.example_label.get().strip()
        caption = setting if len(setting) <= 18 else setting[:17] + "…"
        recording_example = recording is not None and recording["kind"] == "example"
        recording_check = recording is not None and recording["kind"] == "check"
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
                              (self.add_take, editable and self.example_source is not None and setting),
                              (self.add_wav, editable),
                              (self.train, editable and len(self.counts) >= 2),
                              (self.map_save, editable and valid_profile),
                              (self.inspect_wav, editable and valid_profile),
                              (self.inspect_take, editable and valid_profile and self.latest_dry is not None),
                              (self.audition, editable and self.result_audio is not None),
                              (self.remove, editable and bool(self.example_list.curselection()))):
            widget.configure(state="normal" if ready else "disabled")
        listening = self.live_running and self.observe.get()
        self.observe_box.configure(state="normal" if listening or editable and valid_profile else "disabled")
        self.arm_box.configure(state="normal" if editable and self.mapping else "disabled")

    # ---- takes ----

    def current_take(self, info=None):
        """(dry WAV, how to describe it) of a take: the newest one, unless its record is given."""
        if info is None:
            info = takes.latest_clip()
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

    # ---- recording an example or a check ----

    def record_dry(self, kind):
        """Start recording an example or a check; pressed again, stop and save it."""
        recording = self.recording
        if recording is not None:
            if recording["kind"] == kind and not recording["stop"].is_set():
                recording["stop"].set()
                self.follow_recording()
            return
        if self.busy() or self.working:
            self.status.set("Finish the current audio task before recording a dry sample.")
            return
        self.load_selection()
        try:
            instrument, _ = self.selection()
            setting = self.example_label.get().strip()
            if kind == "example" and not setting:
                raise ValueError("Enter the setting you are about to play.")
            if kind == "check" and self.profile is None:
                raise ValueError("Learn two settings before recording a check.")
        except ValueError as error:
            self.status.set(str(error))
            return
        if kind == "example":
            self.set_source(None)
        self.status.set("")
        guitar_gestures.stop()              # it holds the input; the recording starts when it has let go
        self.recording = {"kind": kind, "setting": setting, "guitar": instrument, "stop": Event(), "ready": Event(),
                          "started": False, "said": None}
        self.seat.watch(self.follow_recording)
        self.follow_recording()

    def follow_recording(self):
        """Start the recording once the input is free, and say how it is going. On the desk's clock."""
        recording = self.recording
        if recording is None:
            return False
        said = self.estimate_text if recording["kind"] == "check" else self.source_text
        if not recording["started"]:
            if recording["stop"].is_set() or self.seat.leaving:
                self.recording = None
                said.set("Recording cancelled.")
                self.set_buttons()
                return False
            text = "Connecting to Quad Cortex. Wait for Recording before playing."
            if self.seat.job is None and not guitar_gestures.running():
                channel, host, guitar = self.seat.input_channel, self.seat.host, recording["guitar"]
                stop, ready = recording["stop"], recording["ready"]

                def record_take():
                    return takes.record_take(channel, guitar, stop, ready,
                                             routing=lambda: inputs.routing(host, channel))

                if not self.seat.submit(f"Opening the Quad Cortex inputs for {TITLE}…", record_take,
                                        done=self.recorded, stop=stop):
                    self.recording = None
                    said.set("The audio input is in use. Try again when it is free.")
                    self.set_buttons()
                    return False
                recording["started"] = True
        elif recording["stop"].is_set():
            text = "Saving the dry recording…"
        elif recording["ready"].is_set():
            text = "Recording. Play your notes, then press Stop."
        else:
            text = "Connecting to Quad Cortex. Wait for Recording before playing."
        if text != recording["said"]:
            recording["said"] = text
            said.set(text)
            self.set_buttons()
        return True

    def recorded(self, outcome):
        """The take is saved, or the recording failed: `(folder, what to say)` or the reason."""
        recording, self.recording = self.recording, None
        result, error = outcome
        said = self.estimate_text if recording and recording["kind"] == "check" else self.source_text
        self.update_latest()
        if error or recording is None:
            said.set(error or "")
            self.seat.set_status(error or "")
            self.set_buttons()
            return
        folder, note = result
        self.seat.set_status(note)
        try:
            captured = self.current_take(takes.clip_info(Path(folder) / "dry.wav"))
        except (OSError, RuntimeError, ValueError):
            said.set(f"No dry recording could be used. {note}")
            self.set_buttons()
            return
        if recording["kind"] == "check":
            self.estimate_text.set("Checking the dry recording…")
            self.inspect_path(captured[0])
        else:
            self.set_source(*captured)
            self.example_label.set(recording["setting"])
            self.add_example()
        self.set_buttons()

    # ---- examples ----

    def set_source(self, path, description=""):
        self.example_source = (path, description) if path else None
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
        path = self.choose_file()
        if path is not None:
            self.set_source(path, path.name)
            self.label_box.focus_set()
            self.status.set("")

    def choose_file(self):
        filename = filedialog.askopenfilename(parent=self.root, title="Choose a dry mono WAV",
                                              filetypes=(("WAV audio", "*.wav"),))
        return Path(filename).resolve() if filename else None

    def add_example(self):
        if not self.idle():
            return
        self.load_selection()
        text = self.example_label.get().strip()
        try:
            instrument, control = self.selection()
            if self.example_source is None:
                raise ValueError("Record an example or choose a dry take first.")
            path, description = self.example_source
            fields = (("start", self.start_seconds.get().strip()), ("end", self.end_seconds.get().strip()))
            try:
                span = {key: float(value) for key, value in fields if value}
            except ValueError as error:
                raise ValueError("Enter start and end times in seconds, such as 1.5.") from error
            row = taught.add_example(instrument, control, text, path, source_label=description or None, **span)
        except (OSError, RuntimeError, ValueError) as error:
            self.status.set(str(error))
            return
        self.examples = taught.examples(instrument, control)
        self.examples_changed = True
        self.instrument_box.configure(values=taught.instruments())
        self.refresh_examples()
        self.example_label.set(self.suggested_setting())
        self.set_source(None)
        self.status.set(f"Saved {row['label']} as an example.")

    def remove_example(self):
        self.load_selection()
        selected = self.example_list.curselection()
        if not selected:
            return
        try:
            instrument, control = self.selection()
            taught.remove_example(instrument, control, self.examples[selected[0]]["id"])
            self.examples = taught.examples(instrument, control)
        except (OSError, ValueError) as error:
            self.status.set(str(error))
            return
        self.examples_changed = True
        self.refresh_examples()

    # ---- the profile, the mapping, listening ----

    def fit_profile(self):
        self.load_selection()
        try:
            selected = self.selection()
        except ValueError as error:
            self.status.set(str(error))
            return

        def finish(profile):
            if self.loaded_selection == selected:
                self.profile = profile
                self.examples_changed = False
                self.refresh_profile()
                self.status.set("Settings learned. Record a fresh check at one of the settings you taught.")

        self.run("Learning these settings…", lambda: taught.fit(*selected), finish)

    def save_mapping(self):
        self.load_selection()
        try:
            if self.profile is None:
                raise ValueError("Learn two or more settings first.")
            mapping = taught.save_mapping(*self.selection(), self.home.get(), self.excursion.get(),
                                          self.knob_keys.get(self.knob.get(), ""), self.step.get())
        except (OSError, ValueError) as error:
            self.status.set(str(error))
        else:
            self.mapping = mapping
            self.arm.set(False)
            self.status.set("Mapping saved. Check a dry take to rehearse it without turning a control.")
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
        if self.busy():
            raise RuntimeError("Wait for the recording to finish before listening.")
        instrument, control = self.selection()
        armed = bool(self.mapping and self.arm.get())
        guitar_gestures.start(self.seat, instrument=instrument, control=control, arm=armed,
                              listener=self.live_event, observe=True)
        self.live_running = True
        self.arm.set(False)
        self.live_text.set(f"Listening for {control} on {instrument}. Play a note you taught.")
        self.live_action.set("Waiting for a mapped return gesture." if armed else "Observing only")

    def live_event(self, event):
        kind = event.get("event")
        if kind == "guitar-state":
            setting = event.get("label")
            result = taught.match_words(setting, event.get("closeness", 0), event.get("control"))
            text = f"{result}  ·  {event['reason']}" if not setting and event.get("reason") else result
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

    # ---- checking a take ----

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
            source = self.current_take()[0] if current else self.choose_file()
        except (OSError, RuntimeError, ValueError) as error:
            self.status.set(str(error))
            return
        if source is not None:
            self.inspect_path(source)

    def inspect_path(self, source):
        if self.profile is None or self.working:
            return
        profile, mapping, examples = self.profile, self.mapping, list(self.examples)

        def finish(result):
            if self.profile is profile:
                self.show_analysis(*result)
                self.status.set("Dry check complete.")

        self.run("Analyzing the dry signal…", lambda: taught.check(profile, mapping, examples, source), finish)

    def show_analysis(self, samples, estimate, track, heard):
        self.result_audio = samples
        control = self.profile["control"]
        setting = estimate.get("label")
        result = taught.match_words(setting, estimate.get("closeness", 0), control)
        self.estimate_text.set(result + (f"  ·  {estimate['reason']}" if not setting and estimate.get("reason") else ""))
        self.timeline.delete(0, "end")
        self.result_intervals = []
        for when, text, before in taught.timeline(control, track, heard):
            self.timeline.insert("end", f"{when:5.2f} s    {text}")
            start = max(0, round((when - before) * audio.RATE))
            self.result_intervals.append((start, start + audio.RATE * 2))
        self.set_buttons()

    def listen(self):
        """Play the selected stretch of the checked take on the computer's own output."""
        import winsound

        if self.result_audio is None or self.busy():
            self.status.set("Finish recording or live control before playing audio.")
            return
        selected = self.timeline.curselection()
        interval = self.result_intervals[selected[0]] if selected else (0, 4 * audio.RATE)
        try:
            folder = folders.taught(self.selection()[0])
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "preview.wav"
            winsound.PlaySound(None, 0)
            # 16-bit, which the Windows player plays.
            wavfile.write(path, audio.RATE, taught.preview(self.result_audio, interval))
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except (OSError, RuntimeError, ValueError) as error:
            self.status.set(str(error))

    # ---- work off the window's thread ----

    def run(self, message, work, finish):
        if self.working:
            return
        self.working = True
        self.status.set(message)
        self.set_buttons()

        def done(result):
            self.working = False
            try:
                finish(result)
            finally:
                self.set_buttons()

        def failed(error):
            self.working = False
            self.status.set(str(error))
            self.set_buttons()

        self.seat.later(self.worker.submit(work), done, failed)


class TeachWindow:
    def __init__(self, seat):
        self.seat = seat
        self.window = tk.Toplevel(seat.root, background=BG)
        self.window.withdraw()
        self.window.title(f"{TITLE} · Tone Lab")
        self.window.minsize(900, 620)
        self.window.geometry("1000x760")
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        self.window.bind("<Escape>", lambda _: self.hide())
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="teach-a-control")
        page = tk.Frame(self.window, background=BG, padx=20, pady=16)
        page.pack(fill="both", expand=True)
        header = tk.Frame(page, background=BG)
        header.pack(fill="x", pady=(0, 10))
        tk.Label(header, text="Teach a control's settings", font=("Georgia", 22), background=BG,
                 foreground=TEXT).pack(side="left")
        button(header, "Back to Tone Lab", self.hide, compact=True).pack(side="right")
        body = tk.Frame(page, background=BG)
        body.pack(fill="both", expand=True)
        self.panel = TeachPanel(seat, body, self.worker)

    def show(self):
        from lab.learner.window import dark_title

        self.window.deiconify()
        dark_title(self.window)
        self.window.lift()
        self.window.focus_set()
        self.panel.follow_desk_guitar()

    def hide(self):
        """Hide the window. A recording stops; gesture listening goes on, so the guitar can be played on the desk."""
        if self.panel.recording is not None:
            self.panel.recording["stop"].set()
        self.window.withdraw()
        self.seat.root.lift()


_windows = {}


def open(seat):  # noqa: A001 - the name the desk calls
    """Show the window for this seat, opening it the first time."""
    found = _windows.get(id(seat))
    if found is None or not found.window.winfo_exists():
        found = _windows[id(seat)] = TeachWindow(seat)
    found.show()
    return found
