"""A dedicated recording and evaluation desk for guitar configuration learning."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import tkinter as tk
from tkinter import filedialog

import numpy as np

from . import capture, desktop, diagnostics, guitar_configuration as model, guitar_dataset as data, listening, session
from .guitar_board import (ACCENT, ACTIVE_INK, ALERT, BG, FONT, INK, LINE, MUTED, PANEL, SURFACE,
                           GuitarBoard, LAYOUTS_SHOWN, button, mark_button, setting, word)


GUITAR = "Charvel"


def summarize(layout, values):
    parts = [data.LAYOUTS[layout]["pickups"][values["pickup"] - 1]]
    if values.get("wiring"):
        parts.append(values["wiring"])
    parts.extend((f"Vol {setting(values['volume'])}", f"Tone {setting(values['tone'])}"))
    return "  ·  ".join(parts)


def prediction_value(key, value, layout):
    if value is None:
        return "Uncertain"
    if key == "pickup":
        return data.LAYOUTS[layout]["pickups"][value - 1]
    if key == "wiring":
        return value
    low, high = setting(value["low"]), setting(value["high"])
    return low if low == high else f"{low}–{high}"


def teaching_hint(layout, examples, current):
    if not examples:
        return "Match the guitar controls, then record three sustained single notes."
    setups = [row["configuration"] for row in examples]
    pickup_pairs = {(row["wiring"], row["pickup"]) for row in setups} if "wiring" in current else {
        (None, row["pickup"]) for row in setups}
    pickup_seen = any(wiring == other and pickup != position for wiring, pickup in pickup_pairs
                      for other, position in pickup_pairs)
    wiring_seen = any(wiring != other and pickup == position for wiring, pickup in pickup_pairs
                      for other, position in pickup_pairs)
    if not pickup_seen:
        return "To teach the blade, record the same notes at another pickup position with the other controls held fixed."
    if "wiring" in data.LAYOUTS[layout] and not wiring_seen:
        return "To teach the mini-toggle, repeat a recording with the same pickup and knobs, then flip only the toggle."
    positions = {(row["tone"], row["volume"]) for row in setups
                 if row["pickup"] == current["pickup"] and row.get("wiring") == current.get("wiring")}
    if len(positions) < 3 or np.linalg.matrix_rank(np.asarray(sorted(positions)) - min(positions), tol=.15) < 2:
        return "For knob estimates here, repeat the same notes while changing just tone or just volume. Keep input gain fixed."
    return "Add another setup, or use Blind check to try a fresh recording between taught settings."


def evaluate(layout):
    """Keep labeled checks independent and recalculate their score after teaching."""
    examples = data.records(GUITAR, layout, role="example")
    tests = data.records(GUITAR, layout, role="test")
    if len(examples) < 2:
        return examples, tests, None, [], ""
    try:
        profile = model.calibrate(GUITAR, examples)
    except ValueError as error:
        return examples, tests, None, [], str(error)
    checks = []
    for row in tests:
        try:
            prediction = model.estimate(profile, row["wav"], source_id=row["source_id"])
        except (OSError, ValueError):
            continue
        checks.append((row, prediction))
    return examples, tests, profile, checks, ""


def report_score(checks, layout):
    if not checks:
        return ""
    count = len(checks)
    parts = [f"{count} blind check{'s' if count != 1 else ''}"]
    controls = ("pickup", "wiring") if "wiring" in data.LAYOUTS[layout] else ("pickup",)
    for control in controls:
        correct = sum(guess.get(control) == row["configuration"][control] for row, guess in checks)
        parts.append(f"{control.title()} {correct}/{count}")
    for control in ("volume", "tone"):
        estimates = [(abs(guess[control]["value"] - row["configuration"][control]),
                      guess[control]["low"] <= row["configuration"][control] <= guess[control]["high"])
                     for row, guess in checks if guess.get(control) is not None]
        if estimates:
            covered = sum(inside for _, inside in estimates)
            error = float(np.mean([difference for difference, _ in estimates]))
            parts.append(f"{control.title()} error {error:.1f} · in range {covered}/{count}")
    return "  |  ".join(parts)


class GuitarLearner:
    def __init__(self, desk):
        self.desk = desk
        self.window = tk.Toplevel(desk.root, background=BG)
        self.window.withdraw()
        self.window.title("Guitar Learner · Tone Lab")
        self.window.minsize(1050, 680)
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        self.window.bind("<Escape>", lambda _: self.hide())
        self.window.bind("<Control-r>", lambda _: self.record())
        self.window.bind("<Control-o>", lambda _: self.choose_wav())
        self.window.geometry("1130x748")
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="guitar-learner")
        self.job = self.on_result = None
        self.stop = self.ready = None
        self.record_started = self.closed = False
        self.layout = None
        self.mode = "Teach"
        self.source = self.excerpt = self.used_source = self.guess = None
        self.full_excerpt = self.drag_span = None
        self.wave_peaks = ()
        self.examples, self.tests, self.checks = [], [], []
        self.profile = None
        self.prefs_path = session.SESSIONS / "guitar_learner" / "charvel-layout.json"
        self.build()
        saved = desk.read_state(self.prefs_path).get("layout")
        self.change_layout(saved if saved in LAYOUTS_SHOWN else LAYOUTS_SHOWN[0], remember=False)

    def show(self):
        self.window.deiconify()
        desktop.dark_title(self.window)
        self.window.lift()
        self.window.focus_set()

    def hide(self):
        if self.stop:
            self.stop.set()
        self.window.withdraw()
        self.desk.root.lift()

    def shutdown(self):
        self.closed = True
        if self.stop:
            self.stop.set()
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.window.destroy()

    def build(self):
        page = tk.Frame(self.window, background=BG, padx=23, pady=18)
        page.pack(fill="both", expand=True)
        header = tk.Frame(page, background=BG)
        header.pack(fill="x", pady=(0, 14))
        tk.Label(header, text="Guitar Learner", font=("Georgia", 27), background=BG, foreground=INK).pack(side="left")
        button(header, "Back to Tone Lab", self.hide, compact=True).pack(side="right", pady=7)
        self.count = tk.StringVar(master=self.window)
        word(header, variable=self.count, color=ACCENT, size=10, bold=True).pack(side="right", padx=15)

        work = tk.Frame(page, background=BG)
        work.pack(fill="both", expand=True)
        self.board = GuitarBoard(work, self.change_layout, self.sync)
        self.board.pack(side="left", fill="both", expand=True, padx=(0, 14))
        panel = tk.Frame(work, background=PANEL, padx=17, pady=14, highlightthickness=1, highlightbackground=LINE)
        panel.pack(side="right", fill="y")
        panel.configure(width=366)
        panel.pack_propagate(False)

        mode = tk.Frame(panel, background=PANEL)
        mode.pack(fill="x")
        self.modes = {}
        for title in ("Teach", "Blind check"):
            btn = button(mode, title, lambda picked=title: self.change_mode(picked), compact=True)
            btn.pack(side="left", padx=(0, 5))
            self.modes[title] = btn
        self.title = tk.StringVar(master=self.window)
        self.guidance = tk.StringVar(master=self.window)
        word(panel, variable=self.title, size=16, bold=True).pack(fill="x", pady=(13, 4))
        word(panel, variable=self.guidance, color=MUTED, wrap=322).pack(fill="x", pady=(0, 12))

        self.source_title = tk.StringVar(master=self.window, value="No recording selected")
        self.source_detail = tk.StringVar(master=self.window, value="Dry input")
        self.clip = tk.Frame(panel, background=BG, padx=10, pady=9, highlightthickness=1, highlightbackground=LINE)
        self.clip.pack(fill="x")
        word(self.clip, variable=self.source_title, bold=True, wrap=287).pack(fill="x")
        self.wave = tk.Canvas(self.clip, background=BG, height=43, borderwidth=0, highlightthickness=0)
        self.wave.pack(fill="x", pady=2)
        self.wave.bind("<Configure>", lambda _: self.draw_wave())
        self.wave.bind("<ButtonPress-1>", self.start_span)
        self.wave.bind("<B1-Motion>", self.move_span)
        self.wave.bind("<ButtonRelease-1>", self.end_span)
        self.wave.bind("<Double-Button-1>", self.reset_span)
        word(self.clip, variable=self.source_detail, color=MUTED, size=9).pack(fill="x")

        sources = tk.Frame(panel, background=PANEL)
        sources.pack(fill="x", pady=(9, 5))
        self.record_button = button(sources, "●  Record", self.record, compact=True)
        self.record_button.pack(side="left", padx=(0, 5))
        self.takes_button, self.takes = self.menu(sources, "Use a take", self.list_takes)
        self.takes_button.pack(side="left", padx=(0, 5))
        self.wav_button = button(sources, "Open WAV", self.choose_wav, compact=True)
        self.wav_button.pack(side="left")

        self.action = button(panel, "Add example", self.perform, primary=True)
        self.action.pack(fill="x", pady=(7, 5))
        self.status = tk.StringVar(master=self.window)
        word(panel, variable=self.status, color=ALERT, wrap=318, size=9).pack(fill="x")

        self.report = tk.Frame(panel, background=PANEL)
        self.headline = tk.StringVar(master=self.window)
        self.details = tk.StringVar(master=self.window)
        self.note = tk.StringVar(master=self.window)
        word(self.report, variable=self.headline, size=13, bold=True, color=ACCENT).pack(fill="x", pady=(1, 3))
        word(self.report, variable=self.details, bold=True, wrap=320).pack(fill="x")
        word(self.report, variable=self.note, color=MUTED, wrap=318, size=9).pack(fill="x", pady=(3, 4))
        self.actual = button(self.report, "Save actual settings from the guitar panel", lambda: self.add("test"),
                             compact=True)
        self.actual.pack(anchor="w", pady=(3, 0))

        lower = tk.Frame(panel, background=PANEL)
        lower.pack(side="bottom", fill="x", pady=(7, 0))
        self.labels_button, self.labels = self.menu(lower, "Recordings", self.list_labels)
        self.labels_button.pack(side="right")
        self.score = tk.StringVar(master=self.window)
        word(panel, variable=self.score, color=MUTED, size=9, wrap=319).pack(side="bottom", fill="x", pady=(3, 0))
        self.bindings = (self.takes_button, self.wav_button, self.labels_button)

    def menu(self, parent, title, refresh):
        widget = tk.Menubutton(parent, text=title, indicatoron=True, takefocus=True, cursor="hand2",
                               font=(FONT, 9, "bold"), bg=SURFACE, fg=INK, activebackground=LINE,
                               activeforeground=INK, highlightbackground=PANEL, padx=8, pady=4)
        rows = tk.Menu(widget, tearoff=False, postcommand=refresh, bg=SURFACE, fg=INK,
                       activebackground=ACCENT, activeforeground=ACTIVE_INK)
        widget.configure(menu=rows)
        return widget, rows

    def change_layout(self, layout, *, remember=True):
        if layout == self.layout or self.job is not None or self.stop is not None:
            return
        if remember:
            try:
                self.prefs_path.parent.mkdir(parents=True, exist_ok=True)
                session.write_json(self.prefs_path, {"layout": layout})
            except OSError:
                diagnostics.LOG.warning("Guitar Learner layout could not be saved", exc_info=True)
        self.layout = layout
        self.examples, self.tests, self.checks = [], [], []
        self.profile = self.guess = None
        self.used_source = None
        self.board.show(layout)
        self.work("Reading labeled recordings…", evaluate, lambda result: self.loaded(result, use_last=True), layout)

    def loaded(self, result, use_last=False):
        self.examples, self.tests, self.profile, self.checks, message = result
        if use_last and self.examples:
            self.board.show(self.layout, self.examples[0]["configuration"])
        self.status.set(message)
        self.sync()

    def change_mode(self, mode):
        self.mode = mode
        self.status.set("")
        self.sync()

    def sync(self):
        teaching = self.mode == "Teach"
        idle = self.job is None and self.stop is None
        self.board.set_enabled(idle)
        for name, widget in self.modes.items():
            mark_button(widget, self.mode == name)
            widget.configure(state="normal" if idle else "disabled")
        for widget in self.bindings:
            widget.configure(state="normal" if idle else "disabled")
        if self.stop:
            label = "Saving…" if self.stop.is_set() else "■  Stop"
            self.record_button.configure(text=label, state="disabled" if self.stop.is_set() else "normal")
        else:
            self.record_button.configure(text="●  Record", state="normal" if idle else "disabled")
        self.title.set("Teach a configuration" if teaching else "Estimate a recording")
        if teaching:
            instruction = teaching_hint(self.layout, self.examples, self.board.values)
        else:
            instruction = ("Use a fresh clip at a setting between your examples."
                           if self.profile else "Teach at least two recordings of the same notes to unlock a check.")
        self.guidance.set(instruction)
        available = self.excerpt is not None and not self.excerpt.clipped and idle
        available = available and (self.used_source is None and not self.excerpt.quiet if teaching else self.profile)
        self.action.configure(text="Add example" if teaching else "Estimate settings",
                              state="normal" if available else "disabled")
        count = len(self.examples)
        settings = len({tuple(sorted(row["configuration"].items())) for row in self.examples})
        self.count.set(f"{count} example{'s' if count != 1 else ''}  ·  {settings} setup{'s' if settings != 1 else ''}")
        self.score.set(report_score(self.checks, self.layout) if self.layout else "")
        if not teaching and self.guess:
            self.show_guess(idle)
        else:
            self.report.pack_forget()

    def list_takes(self):
        self.takes.delete(0, tk.END)
        sources = data.take_sources(GUITAR)
        if not sources:
            self.takes.add_command(label="No verified Charvel dry takes", state="disabled")
        for source in sources[:18]:
            self.takes.add_command(label=source.label, command=lambda picked=source: self.select_source(picked))

    def list_labels(self):
        self.labels.delete(0, tk.END)
        for submenu in self.labels.winfo_children():
            submenu.destroy()
        if not self.examples and not self.tests:
            self.labels.add_command(label="No labeled recordings yet", state="disabled")
        for caption, rows in (("Examples", self.examples), ("Independent checks", self.tests)):
            if not rows:
                continue
            group = tk.Menu(self.labels, tearoff=False)
            self.labels.add_cascade(label=f"{caption} ({len(rows)})", menu=group)
            for index, row in enumerate(rows, 1):
                label = summarize(self.layout, row["configuration"])
                item = tk.Menu(group, tearoff=False)
                group.add_cascade(label=f"{index}. {label}", menu=item)
                item.add_command(label="Set the board to this configuration", command=lambda saved=row:
                                 self.load_board(saved))
                item.add_command(label="Remove label", command=lambda saved=row: self.remove(saved))

    def load_board(self, row):
        self.board.show(self.layout, row["configuration"])
        self.sync()

    def remove(self, row):
        self.guess = None

        def remove_label():
            data.remove(GUITAR, self.layout, row["id"])
            return evaluate(self.layout)

        self.work("Removing the label…", remove_label, self.loaded)

    def choose_wav(self):
        if self.job or self.stop:
            return
        path = filedialog.askopenfilename(parent=self.window, title="Choose a mono dry guitar WAV",
                                          filetypes=(("WAV audio", "*.wav"),))
        if path:
            try:
                self.select_source(data.source_from_wav(path))
            except (OSError, ValueError) as error:
                self.status.set(str(error))

    def select_source(self, source):
        if source.guitar and source.guitar.casefold() != GUITAR.casefold():
            self.status.set(f"This take used {source.guitar}. Choose a recording of {GUITAR}.")
            return
        self.work("Reading dry audio…", data.inspect, lambda excerpt: self.source_ready(source, excerpt), source)

    def source_ready(self, source, excerpt):
        self.source, self.full_excerpt = source, excerpt
        self.wave_peaks = tuple(float(np.abs(chunk).max()) for chunk in np.array_split(excerpt.samples, 75))
        self.source_title.set(source.label)
        self.use_excerpt(excerpt)

    def use_excerpt(self, excerpt):
        self.excerpt = excerpt
        self.used_source = self.guess = None
        detail = f"{excerpt.source_end - excerpt.source_start:.1f}s · Drag to select a passage"
        if excerpt is not self.full_excerpt:
            detail = f"{excerpt.source_start:.1f}–{excerpt.source_end:.1f}s  ·  Double-click to reset"
        self.source_detail.set(detail)
        if excerpt.clipped:
            self.status.set("This recording clips. Choose a dry take with more headroom.")
        elif excerpt.quiet:
            self.status.set("Very quiet dry signal.")
        else:
            self.status.set("")
        self.draw_wave()
        self.sync()

    def draw_wave(self):
        width, height = self.wave.winfo_width(), self.wave.winfo_height()
        self.wave.delete("all")
        self.wave.create_line(0, height / 2, width, height / 2, fill=LINE)
        if self.full_excerpt is None or width < 4:
            return
        full = self.full_excerpt
        start, end = full.source_start, full.source_end
        span = end - start
        if self.drag_span is not None:
            left, right = sorted(self.drag_span)
        else:
            left = (self.excerpt.source_start - start) * width / span
            right = (self.excerpt.source_end - start) * width / span
        self.wave.create_rectangle(left, 0, right, height, fill="#243a34", outline="")
        normal = max(.002, max(self.wave_peaks))
        for index, peak in enumerate(self.wave_peaks):
            x = (index + .5) * width / len(self.wave_peaks)
            up = min(height / 2 - 2, (height / 2 - 2) * peak / normal)
            self.wave.create_line(x, height / 2 - up, x, height / 2 + up,
                                  fill=ACCENT if left <= x <= right else "#686e75", width=2)

    def start_span(self, event):
        if self.full_excerpt is not None and self.job is None and self.stop is None:
            self.drag_span = event.x, event.x

    def move_span(self, event):
        if self.drag_span is not None:
            width = self.wave.winfo_width()
            self.drag_span = self.drag_span[0], max(0, min(width, event.x))
            self.draw_wave()

    def end_span(self, event):
        if self.drag_span is None:
            return
        self.move_span(event)
        left, right = sorted(self.drag_span)
        self.drag_span = None
        if right - left < 5:
            self.draw_wave()
            return
        full = self.full_excerpt
        span = full.source_end - full.source_start
        start, end = (full.source_start + span * x / self.wave.winfo_width() for x in (left, right))
        if end - start < .5:
            self.status.set("Select at least half a second.")
            self.draw_wave()
            return
        self.work("Selecting the passage…", lambda: data.inspect(self.source, start=start, end=end), self.use_excerpt)

    def reset_span(self, _):
        if self.full_excerpt is not None and self.job is None and self.stop is None:
            self.drag_span = None
            self.use_excerpt(self.full_excerpt)

    def record(self):
        if self.stop:
            self.stop.set()
            self.status.set("Saving the dry recording…")
            self.sync()
            return
        if self.job or self.desk.leaving:
            return
        if self.desk.job is not None and not self.desk.task_if_active("monitor"):
            self.status.set("Tone Lab is using the recorder. Wait for that action to finish.")
            return
        self.stop, self.ready = Event(), Event()
        self.record_started = False
        if self.desk.task_if_active("monitor"):
            self.desk.stop()
            self.status.set("Releasing the Quad Cortex playback loop…")
        self.poll_capture()

    def poll_capture(self):
        if self.closed or self.stop is None:
            return
        if not self.record_started:
            if self.stop.is_set() or self.desk.leaving:
                self.stop = self.ready = None
                self.status.set("Recording cancelled.")
                self.sync()
                return
            if self.desk.job is None:
                self.record_started = True
                self.desk.ready = self.ready
                self.desk.stop_signal = self.stop
                self.desk.live_is_ready = False
                self.desk.submit("record", "Opening the Quad Cortex dry input for Guitar Learner…",
                                 self.capture, self.desk.input_channel, self.stop, self.ready, done=self.recorded)
                self.status.set("Connecting… wait for Recording.")
        elif self.ready.is_set() and not self.stop.is_set():
            self.status.set("Recording… play the notes, then press Stop.")
        self.sync()
        self.window.after(100, self.poll_capture)

    @staticmethod
    def capture(channel, stop, ready):
        try:
            return capture.record_take(channel, GUITAR, stop, ready), None
        except Exception as error:
            diagnostics.LOG.exception("Guitar Learner recording failed")
            return None, str(error)

    def recorded(self, outcome):
        self.stop = self.ready = None
        result, error = outcome
        self.sync()
        if error:
            self.status.set(error)
            self.desk.set_status(error)
            return
        self.desk.takes = listening.takes()
        self.desk.set_status(result[1])
        try:
            self.select_source(data.source_from_result(GUITAR, result))
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def perform(self):
        if self.mode == "Teach":
            self.add("example")
        elif self.profile and self.excerpt:
            self.work("Estimating the guitar settings…", self.estimate, self.guessed)

    def estimate(self):
        data.check_test(GUITAR, self.layout, self.excerpt)
        return model.estimate(self.profile, self.excerpt.samples, source_id=self.excerpt.source_id)

    def guessed(self, guess):
        self.guess = guess
        self.status.set("")
        self.sync()

    def show_guess(self, idle):
        guess = self.guess
        self.headline.set(f"{guess['confidence'].title()} estimate" if guess["confidence"] != "uncertain" else "Uncertain")
        keys = ["pickup", "volume", "tone"]
        if "wiring" in data.LAYOUTS[self.layout]:
            keys.insert(1, "wiring")
        present = [f"{key.title()}  {prediction_value(key, guess.get(key), self.layout)}" for key in keys]
        self.details.set("  ·  ".join(present))
        self.note.set(guess["reason"] if self.used_source is None else
                      f"Actual  {summarize(self.layout, self.used_source['configuration'])}")
        self.actual.configure(state="normal" if idle and self.used_source is None else "disabled")
        if not self.report.winfo_manager():
            self.report.pack(fill="x", pady=(6, 0))

    def add(self, role):
        if self.job or self.stop or self.source is None or self.used_source or (role == "test" and not self.guess):
            return
        source, values, layout, excerpt = self.source, dict(self.board.values), self.layout, self.excerpt

        def label():
            if role == "example" and not model.usable_notes(excerpt.samples):
                raise ValueError("No sustained single notes detected. Select a clearer passage or record again.")
            row = data.add(GUITAR, layout, source, values, role=role, start=excerpt.source_start, end=excerpt.source_end)
            return row, evaluate(layout)

        def saved(result):
            self.used_source, fit = result
            self.loaded(fit)
            if not self.status.get():
                self.status.set("Example added." if role == "example" else "Independent check scored.")
            self.sync()

        self.work("Adding the example…" if role == "example" else "Scoring the independent check…", label, saved)

    def work(self, progress, fn, done, *args):
        if self.closed or self.job is not None:
            return
        self.job = self.worker.submit(fn, *args)
        self.on_result = done
        self.status.set(progress)
        self.sync()
        self.window.after(60, self.poll_job)

    def poll_job(self):
        if self.closed or self.job is None:
            return
        if not self.job.done():
            self.window.after(60, self.poll_job)
            return
        job, self.job = self.job, None
        callback, self.on_result = self.on_result, None
        try:
            callback(job.result())
        except Exception as error:
            diagnostics.LOG.info("Guitar Learner action did not finish: %s", error)
            self.status.set(str(error))
        self.sync()
