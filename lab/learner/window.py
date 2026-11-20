"""A dedicated recording and evaluation desk for guitar configuration learning.

`GuitarLearner(desk)` opens on whatever desk seats it. The desk provides:
root, read_state(path), set_status(text), submit(task, message, work, *args,
done), job, task_if_active(name), stop(), stop_signal, ready, live_is_ready,
leaving, takes and input_channel (see `lab.desk.window.LearnerSeat`).
"""

from concurrent.futures import ThreadPoolExecutor
import ctypes
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Event
import tkinter as tk
from tkinter import filedialog, ttk
from uuid import uuid4

import numpy as np

from lab.learner import configuration as model, dataset as data, folders, gestures, takes as recorder, worker
from lab.learner.board import (ACCENT, ACTIVE_INK, ALERT, BG, FONT, INK, LINE, MUTED, PANEL, SURFACE,
                    GuitarBoard, LAYOUTS_SHOWN, button, mark_button, setting, word)
from lab.learner.folders import LOG


GUITAR = "Charvel"


def dark_title(window):
    """Ask Windows for a dark title bar, to match the window."""
    if sys.platform != "win32":
        return
    from ctypes import wintypes

    window.update_idletasks()
    user32 = ctypes.windll.user32
    ancestor = user32.GetAncestor
    ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
    ancestor.restype = wintypes.HWND
    handle = ancestor(window.winfo_id(), 2)  # Tk's native top-level wrapper.
    enabled = wintypes.BOOL(True)
    attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
    attribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    attribute(handle, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))


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
        return f"{value} · {data.LAYOUTS[layout]['pickups'][value - 1]}"
    if key == "wiring":
        return value
    low, high = setting(value["low"]), setting(value["high"])
    return low if low == high else f"{low}–{high}"


def teaching_hint(layout, examples, current):
    if not examples:
        return "Start with a short phrase in a setup you know. Tag the actual settings on the guitar board."
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
    return "Add another setup, or check a fresh performance in your usual playing style."


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


def setup_key(configuration):
    return configuration["pickup"], configuration.get("wiring")


def training_coverage(examples):
    if not examples:
        return ""
    settings = [row["configuration"] for row in examples]
    positions = ", ".join(map(str, sorted({row["pickup"] for row in settings})))
    knobs = []
    for key in ("tone", "volume"):
        values = sorted({row[key] for row in settings})
        knobs.append(f"{key.title()} {setting(values[0])}" if len(values) == 1 else
                     f"{key.title()} {setting(values[0])}–{setting(values[-1])}")
    return f"Taught blade positions {positions}  ·  {'  ·  '.join(knobs)}"


def report_score(checks, layout, examples):
    if not checks:
        return ""
    taught_setups = {setup_key(row["configuration"]) for row in examples}
    supported = [(row, guess) for row, guess in checks if setup_key(row["configuration"]) in taught_setups]
    count = len(supported)
    outside = len(checks) - count
    parts = []
    if count:
        parts.append(f"{count} check{'s' if count != 1 else ''} on taught setups")
    controls = ("pickup", "wiring") if "wiring" in data.LAYOUTS[layout] else ("pickup",)
    for control in controls:
        called = [(row, guess) for row, guess in supported if guess.get(control) is not None]
        if called:
            correct = sum(guess[control] == row["configuration"][control] for row, guess in called)
            detail = f"{correct}/{len(called)} correct"
            if len(called) != count:
                detail += f", {count - len(called)} uncertain"
            parts.append(f"{control.title()} {detail}")
        elif count:
            parts.append(f"{control.title()} uncertain")
    for control in ("volume", "tone"):
        estimates = [(abs(guess[control]["value"] - row["configuration"][control]),
                      guess[control]["low"] <= row["configuration"][control] <= guess[control]["high"])
                     for row, guess in supported if guess.get(control) is not None]
        if estimates:
            covered = sum(inside for _, inside in estimates)
            error = float(np.mean([difference for difference, _ in estimates]))
            parts.append(f"{control.title()} error {error:.1f} · in range {covered}/{count}")
    if outside:
        parts.append(f"{outside} outside taught setups")
    return "  ·  ".join(parts)


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
        self.source = self.excerpt = self.used_source = self.guess = None
        self.full_excerpt = self.drag_span = None
        self.wave_peaks = ()
        self.live_stop = self.live_state = self.snapshot_request = None
        self.live_started = self.follow_live = False
        self.live_events = Queue()
        self.live_commands = Queue()
        self.live_change = None
        self.last_live_guess = None
        self.dataset_open = self.guide_open = False
        self.dataset_rows = {}
        self.examples, self.tests, self.checks = [], [], []
        self.profile = None
        self.prefs_path = folders.layout_choice()
        self.build()
        saved = desk.read_state(self.prefs_path).get("layout")
        self.change_layout(saved if saved in LAYOUTS_SHOWN else LAYOUTS_SHOWN[0], remember=False)

    def show(self):
        self.window.deiconify()
        dark_title(self.window)
        self.window.lift()
        self.window.focus_set()

    def hide(self):
        if self.stop:
            self.stop.set()
        if self.live_stop:
            self.live_stop.set()
        self.window.withdraw()
        self.desk.root.lift()

    def shutdown(self):
        self.closed = True
        if self.stop:
            self.stop.set()
        if self.live_stop:
            self.live_stop.set()
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.window.destroy()

    def build(self):
        page = tk.Frame(self.window, background=BG, padx=23, pady=18)
        page.pack(fill="both", expand=True)
        header = tk.Frame(page, background=BG)
        header.pack(fill="x", pady=(0, 14))
        tk.Label(header, text="Guitar Learner", font=("Georgia", 27), background=BG, foreground=INK).pack(side="left")
        button(header, "Back to Tone Lab", self.hide, compact=True).pack(side="right", pady=7)
        self.dataset_button = button(header, "Dataset", self.toggle_dataset, compact=True)
        self.dataset_button.pack(side="right", padx=(0, 5), pady=7)
        self.guide_button = button(header, "Baseline guide", self.toggle_guide, compact=True)
        self.guide_button.pack(side="right", padx=(0, 5), pady=7)
        self.count = tk.StringVar(master=self.window)
        word(header, variable=self.count, color=ACCENT, size=10, bold=True).pack(side="right", padx=15)

        self.guide = tk.Frame(page, background=PANEL, padx=12, pady=9, highlightthickness=1, highlightbackground=LINE)
        for line in (
                "Start with a short phrase of clear notes across your usual register. Repeat it after changing one control.",
                "Then add your normal playing in a few known setups. Keep the input gain steady and vary your picking across the examples.",
                "Check a different performance to see what carries over. Live listening uses the latest usable notes."):
            word(self.guide, line, size=9, color=MUTED, wrap=920).pack(fill="x")
        work = self.workspace = tk.Frame(page, background=BG)
        work.pack(fill="both", expand=True)
        self.board = GuitarBoard(work, self.change_layout, self.sync)
        self.board.pack(side="left", fill="both", expand=True, padx=(0, 14))
        panel = tk.Frame(work, background=PANEL, padx=17, pady=14, highlightthickness=1, highlightbackground=LINE)
        panel.pack(side="right", fill="y")
        panel.configure(width=366)
        panel.pack_propagate(False)

        live = tk.Frame(panel, background=PANEL)
        live.pack(fill="x")
        self.live_button = button(live, "Listen live", self.toggle_live, primary=True, compact=True)
        self.live_button.pack(side="left")
        self.follow_button = button(live, "Follow live", self.follow, compact=True)
        self.follow_button.pack(side="left", padx=5)
        self.keep_button = button(live, "Keep recent clip", self.keep_live, compact=True)
        self.keep_button.pack(side="right")
        self.live_status = tk.StringVar(master=self.window)
        self.live_status_line = word(panel, variable=self.live_status, size=9, color=ACCENT, wrap=319)
        self.live_status_line.pack(fill="x", pady=(5, 0))
        self.change_tools = tk.Frame(panel, background=PANEL)
        word(self.change_tools, "Last change", color=MUTED, size=9).pack(side="left")
        self.change_buttons = []
        for part in ("before", "after"):
            control = button(self.change_tools, f"Keep {part}", lambda side=part: self.keep_live(part=side), compact=True)
            control.pack(side="right", padx=(5, 0))
            self.change_buttons.append(control)

        self.title = tk.StringVar(master=self.window)
        self.guidance = tk.StringVar(master=self.window)
        word(panel, variable=self.title, size=16, bold=True).pack(fill="x", pady=(10, 4))
        word(panel, variable=self.guidance, color=MUTED, wrap=322).pack(fill="x", pady=(0, 8))

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
        self.record_button = button(sources, "●  Record clip", self.record, compact=True)
        self.record_button.pack(side="left", padx=(0, 5))
        self.takes_button, self.takes = self.menu(sources, "Use a take", self.list_takes)
        self.takes_button.pack(side="left", padx=(0, 5))
        self.wav_button = button(sources, "Open WAV", self.choose_wav, compact=True)
        self.wav_button.pack(side="left")

        self.status = tk.StringVar(master=self.window)
        self.status_line = word(panel, variable=self.status, color=ALERT, wrap=318, size=9)
        self.status_line.pack(fill="x")

        self.report = tk.Frame(panel, background=PANEL)
        self.headline = tk.StringVar(master=self.window)
        self.details = tk.StringVar(master=self.window)
        self.note = tk.StringVar(master=self.window)
        word(self.report, variable=self.headline, size=13, bold=True, color=ACCENT).pack(fill="x", pady=(1, 3))
        word(self.report, variable=self.details, bold=True, wrap=320).pack(fill="x")
        word(self.report, variable=self.note, color=MUTED, wrap=318, size=9).pack(fill="x", pady=(3, 4))
        self.tag_row = tk.Frame(panel, background=PANEL)
        self.tag_hint = tk.StringVar(master=self.window)
        word(self.tag_row, variable=self.tag_hint, size=9, color=MUTED, wrap=319).pack(fill="x", pady=(2, 4))
        self.teach = button(self.tag_row, "Teach with this clip", lambda: self.add("example"), primary=True, compact=True)
        self.teach.pack(side="left", padx=(0, 5))
        self.keep_check = button(self.tag_row, "Keep as check", lambda: self.add("test"), compact=True)
        self.keep_check.pack(side="left")
        self.tag_row.pack(fill="x", pady=(7, 3))

        self.score = tk.StringVar(master=self.window)
        word(panel, variable=self.score, color=MUTED, size=9, wrap=319).pack(side="bottom", fill="x", pady=(3, 0))
        self.coverage = tk.StringVar(master=self.window)
        word(panel, variable=self.coverage, color=ACCENT, size=9, wrap=319).pack(side="bottom", fill="x", pady=(5, 0))
        self.bindings = (self.takes_button, self.wav_button)
        self.build_dataset(page)

    def menu(self, parent, title, refresh):
        widget = tk.Menubutton(parent, text=title, indicatoron=True, takefocus=True, cursor="hand2",
                               font=(FONT, 9, "bold"), bg=SURFACE, fg=INK, activebackground=LINE,
                               activeforeground=INK, highlightbackground=PANEL, padx=8, pady=4)
        rows = tk.Menu(widget, tearoff=False, postcommand=refresh, bg=SURFACE, fg=INK,
                       activebackground=ACCENT, activeforeground=ACTIVE_INK)
        widget.configure(menu=rows)
        return widget, rows

    def build_dataset(self, page):
        self.dataset = tk.Frame(page, background=PANEL, padx=18, pady=14,
                                highlightthickness=1, highlightbackground=LINE)
        header = tk.Frame(self.dataset, background=PANEL)
        header.pack(fill="x", pady=(0, 10))
        word(header, "Dataset", size=17, bold=True).pack(side="left")
        self.dataset_summary = tk.StringVar(master=self.window)
        word(header, variable=self.dataset_summary, color=MUTED, size=9).pack(side="right")
        style = ttk.Style(self.window)
        style.configure("Learner.Treeview", background=BG, fieldbackground=BG, foreground=INK, rowheight=30,
                        borderwidth=0, font=(FONT, 10))
        style.map("Learner.Treeview", background=[("selected", "#356052")], foreground=[("selected", INK)])
        style.configure("Learner.Treeview.Heading", font=(FONT, 10, "bold"))
        body = tk.Frame(self.dataset, background=PANEL)
        body.pack(fill="both", expand=True)
        columns = ("pickup", "wiring", "volume", "tone", "use")
        self.table = ttk.Treeview(body, columns=columns, selectmode="browse", style="Learner.Treeview", show="tree headings")
        self.table.heading("#0", text="Recording")
        self.table.column("#0", width=320, minwidth=210)
        for key, title, width in zip(columns, ("Pickup", "Mini-toggle", "Volume", "Tone", "Used for"),
                                     (138, 105, 80, 80, 157)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=60, anchor="w")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.table.tag_configure("example", foreground=ACCENT)
        self.table.tag_configure("untagged", foreground=MUTED)
        self.table.bind("<<TreeviewSelect>>", lambda _: self.sync_dataset_buttons())
        self.table.bind("<Double-1>", lambda _: self.open_dataset_row())
        self.table.bind("<Return>", lambda _: self.open_dataset_row())
        bar = tk.Frame(self.dataset, background=PANEL)
        bar.pack(fill="x", pady=(10, 0))
        self.dataset_remove = button(bar, "Remove tag", self.remove_dataset_tag, compact=True)
        self.dataset_remove.pack(side="right")
        self.dataset_select = button(bar, "Open recording", self.open_dataset_row, primary=True, compact=True)
        self.dataset_select.pack(side="left")
        self.dataset_hint = tk.StringVar(master=self.window)
        word(bar, variable=self.dataset_hint, color=ALERT, size=9).pack(side="left", padx=8)

    def toggle_guide(self):
        self.guide_open = not self.guide_open
        if self.guide_open:
            view = self.dataset if self.dataset_open else self.workspace
            self.guide.pack(before=view, fill="x", pady=(0, 10))
        else:
            self.guide.pack_forget()
        mark_button(self.guide_button, self.guide_open)

    def toggle_dataset(self):
        self.dataset_open = not self.dataset_open
        if self.dataset_open:
            self.workspace.pack_forget()
            self.dataset.pack(fill="both", expand=True)
            self.dataset_button.configure(text="Back to learner")
            self.refresh_dataset()
        else:
            self.dataset.pack_forget()
            self.workspace.pack(fill="both", expand=True)
            self.dataset_button.configure(text="Dataset")
        mark_button(self.dataset_button, self.dataset_open)

    def refresh_dataset(self):
        if not self.dataset_open:
            return
        for handle in self.table.get_children():
            self.table.delete(handle)
        self.dataset_rows = {}
        rows = data.dataset_sources(GUITAR, self.layout)
        if self.source and all(source.path != self.source.path for source, _ in rows) and self.used_source is None:
            rows.insert(0, (self.source, ()))
        tally = {"example": 0, "test": 0, "untagged": 0}
        for source, labels in rows:
            for label in labels or (None,):
                values = label["configuration"] if label else None
                use = label["role"] if label else "untagged"
                tally[use] += 1
                record = source.label
                if label and len(labels) > 1:
                    record += f" · {label['source_start']:.1f}–{label['source_end']:.1f}s"
                purpose = {"example": "Teaching example", "test": "Independent check", "untagged": "Untagged"}[use]
                if label and not label.get("score_eligible", True) and use == "test":
                    purpose = "Check · heard in training"
                settings = (prediction_value("pickup", values["pickup"], self.layout), values.get("wiring", "—"),
                            setting(values["volume"]), setting(values["tone"])) if values else ("—",) * 4
                row_id = self.table.insert("", "end", text=record, values=(*settings, purpose), tags=(use,))
                self.dataset_rows[row_id] = (source, label)
        self.dataset_summary.set(f"{tally['example']} examples  ·  {tally['test']} saved checks  ·  {tally['untagged']} untagged")
        self.sync_dataset_buttons()

    def selected_dataset_row(self):
        picked = self.table.selection()
        return self.dataset_rows.get(picked[0]) if picked else None

    def sync_dataset_buttons(self):
        row = self.selected_dataset_row()
        ready = self.job is None and self.stop is None and not self.snapshot_request
        self.dataset_select.configure(state="normal" if row and ready else "disabled")
        self.dataset_remove.configure(state="normal" if row and row[1] and ready else "disabled")

    def open_dataset_row(self):
        row = self.selected_dataset_row()
        if row and self.job is None and self.stop is None and not self.snapshot_request:
            source, label = row
            self.toggle_dataset()
            if label:
                self.review_label(label)
            else:
                self.select_source(source)

    def remove_dataset_tag(self):
        row = self.selected_dataset_row()
        if not row or not row[1]:
            return
        label = row[1]

        def unlabel():
            data.unlabel(GUITAR, self.layout, label["id"])
            return evaluate(self.layout)

        def removed(result):
            if self.used_source and self.used_source["id"] == label["id"]:
                self.used_source = self.guess = None
            self.loaded(result)
            self.dataset_hint.set("Tag removed. The recording is available for review.")

        self.work("Removing the tag…", unlabel, removed)

    def change_layout(self, layout, *, remember=True):
        if layout == self.layout or self.job is not None or self.stop is not None:
            return
        if remember:
            try:
                self.prefs_path.parent.mkdir(parents=True, exist_ok=True)
                folders.write_json(self.prefs_path, {"layout": layout})
            except OSError:
                LOG.warning("Guitar Learner layout could not be saved", exc_info=True)
        self.layout = layout
        self.examples, self.tests, self.checks = [], [], []
        self.profile = self.guess = None
        self.used_source = None
        if self.source is not None and self.source.kind == "saved-excerpt":
            self.source = self.excerpt = self.full_excerpt = None
            self.wave_peaks = ()
            self.source_title.set("No recording selected")
            self.source_detail.set("Dry input")
            self.draw_wave()
        self.board.show(layout)
        self.work("Reading labeled recordings…", evaluate, lambda result: self.loaded(result, use_last=True), layout)

    def loaded(self, result, use_last=False):
        self.examples, self.tests, self.profile, self.checks, message = result
        if use_last and self.examples:
            self.board.show(self.layout, self.examples[0]["configuration"])
        if self.live_started:
            self.live_commands.put({"event": "learner-profile", "profile": self.profile})
        self.status.set(message)
        self.refresh_dataset()
        self.sync()

    def sync(self):
        idle = self.job is None and self.stop is None
        listening_live = self.live_stop is not None
        live_ready = listening_live and self.live_started and not self.live_stop.is_set()
        self.board.set_enabled(idle and not listening_live)
        for widget in self.bindings:
            widget.configure(state="normal" if idle and not self.snapshot_request else "disabled")
        self.live_button.configure(text="Stop listening" if listening_live else "Listen live",
                                   state="normal" if listening_live and not self.live_stop.is_set()
                                   or idle and not listening_live else "disabled")
        if listening_live and not self.keep_button.winfo_manager():
            self.follow_button.pack(side="left", padx=5)
            self.keep_button.pack(side="right")
        elif not listening_live:
            self.follow_button.pack_forget()
            self.keep_button.pack_forget()
        self.follow_button.configure(state="normal" if live_ready and not self.follow_live else "disabled")
        can_keep = idle and live_ready and not self.snapshot_request and (self.live_state or {}).get("buffered_seconds", 0) >= .5
        self.keep_button.configure(state="normal" if can_keep else "disabled")
        if self.follow_live and self.live_change and self.live_change.get("id"):
            if not self.change_tools.winfo_manager():
                self.change_tools.pack(after=self.live_status_line, fill="x", pady=(3, 0))
        else:
            self.change_tools.pack_forget()
        for control in self.change_buttons:
            control.configure(state="normal" if can_keep else "disabled")
        if self.stop:
            label = "Saving…" if self.stop.is_set() else "■  Stop"
            self.record_button.configure(text=label, state="disabled" if self.stop.is_set() else "normal")
        else:
            self.record_button.configure(text="●  Record clip", state="normal" if idle and not listening_live else "disabled")
        following = listening_live and self.follow_live
        if following:
            self.title.set("Listening to your guitar")
            self.guidance.set("The estimate follows the most recent usable notes. Keep a clip to tag it.")
        elif self.source:
            self.title.set("Review this recording")
            self.guidance.set("Match the guitar board to the actual settings before tagging.")
        else:
            self.title.set("Check a recording")
            self.guidance.set(teaching_hint(self.layout, self.examples, self.board.values))
        available = self.excerpt is not None and not self.excerpt.clipped and idle and not following and not self.snapshot_request
        role = self.used_source["role"] if self.used_source else None
        if available:
            if not self.tag_row.winfo_manager():
                self.tag_row.pack(fill="x", pady=(7, 3))
            self.tag_hint.set("Use to teach moves this recording out of the independent score."
                              if role == "test" else "Tag the actual settings shown on the guitar board.")
        else:
            self.tag_row.pack_forget()
        self.teach.configure(text={"test": "Use to teach", "example": "Update teaching tag"}.get(role, "Teach with this clip"),
                             state="normal" if available and not self.excerpt.quiet else "disabled")
        self.keep_check.configure(text="Update check tag" if role == "test" else "Keep as check",
                                  state="normal" if available and role != "example" else "disabled")
        count = len(self.examples)
        settings = len({tuple(sorted(row["configuration"].items())) for row in self.examples})
        self.count.set(f"{count} example{'s' if count != 1 else ''}  ·  {settings} setup{'s' if settings != 1 else ''}")
        self.score.set(report_score(self.checks, self.layout, self.examples) if self.layout else "")
        self.coverage.set(training_coverage(self.examples))
        if following:
            self.show_live()
        else:
            self.display_source()
            if self.guess:
                self.show_guess(self.guess, self.used_source)
            else:
                self.report.pack_forget()
        if self.dataset_open:
            self.sync_dataset_buttons()

    def list_takes(self):
        self.takes.delete(0, tk.END)
        sources = data.take_sources(GUITAR)
        if not sources:
            self.takes.add_command(label="No verified Charvel dry takes", state="disabled")
        labeled = {Path(row["source"]) for row in data.records(GUITAR, self.layout)}
        for source in sources[:18]:
            title = source.label if source.path in labeled else f"{source.label} · unlabelled"
            self.takes.add_command(label=title, command=lambda picked=source: self.select_source(picked))

    def review_label(self, row):
        source = data.DrySource(Path(row["wav"]), row["source_label"], guitar=GUITAR, kind="saved-excerpt")
        self.select_source(source, known_label=row)

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

    def select_source(self, source, *, known_label=None):
        if source.guitar and source.guitar.casefold() != GUITAR.casefold():
            self.status.set(f"This take used {source.guitar}. Choose a recording of {GUITAR}.")
            return
        self.follow_live = False
        self.work("Reading the clip and checking its settings…", self.inspect_clip,
                  lambda result: self.source_ready(source, result), source, None, None, known_label)

    def inspect_clip(self, source, start=None, end=None, known_label=None):
        excerpt = data.inspect(source, start=start, end=end)
        label = known_label or data.find_label(GUITAR, self.layout, excerpt)
        guess, reason = None, ""
        if excerpt.clipped:
            reason = "This recording clips. Select a cleaner passage or use another dry take."
        elif label and label["role"] == "example":
            reason = "Teaching example. Adjust the board and update its tag if needed."
        elif (label and not label.get("score_eligible", True)) or not data.is_independent(GUITAR, self.layout, excerpt):
            reason = "The learner has heard this audio in training."
        elif self.profile:
            guess = model.estimate(self.profile, excerpt.samples, source_id=excerpt.source_id)
        elif not self.profile:
            reason = "Tag at least two distinct examples to start estimating."
        return excerpt, label, guess, reason

    def source_ready(self, source, result):
        excerpt = result[0]
        self.source, self.full_excerpt = source, excerpt
        self.wave_peaks = tuple(float(np.abs(chunk).max()) for chunk in np.array_split(excerpt.samples, 75))
        self.use_excerpt(result)

    def use_excerpt(self, result):
        excerpt, self.used_source, self.guess, reason = result
        self.excerpt = excerpt
        if self.used_source:
            self.board.show(self.layout, self.used_source["configuration"])
        self.status.set(reason or ("Very quiet dry signal." if excerpt.quiet else ""))
        self.draw_wave()
        self.refresh_dataset()
        self.sync()

    def display_source(self):
        if self.source is None:
            self.source_title.set("No recording selected")
            self.source_detail.set("Dry input")
            return
        self.source_title.set(self.source.label)
        excerpt = self.excerpt
        seconds = excerpt.source_end - excerpt.source_start
        detail = f"{seconds:.1f}s · Drag to select a passage"
        if self.source.kind == "saved-excerpt":
            detail = f"{seconds:.1f}s · Saved excerpt"
        elif excerpt is not self.full_excerpt:
            detail = f"{excerpt.source_start:.1f}–{excerpt.source_end:.1f}s  ·  Double-click to reset"
        self.source_detail.set(detail)

    def draw_wave(self):
        width, height = self.wave.winfo_width(), self.wave.winfo_height()
        self.wave.delete("all")
        self.wave.create_line(0, height / 2, width, height / 2, fill=LINE)
        if self.follow_live and self.live_stop is not None:
            peaks = (self.live_state or {}).get("peaks") or ()
            self.draw_peaks(peaks, width, height, 0, width, ceiling=.012)
            return
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
        self.draw_peaks(self.wave_peaks, width, height, left, right)

    def draw_peaks(self, peaks, width, height, left, right, *, ceiling=.002):
        if not peaks:
            return
        normal = max(ceiling, max(peaks))
        maximum = max(0, height / 2 - 2)
        for index, peak in enumerate(peaks):
            x = (index + .5) * width / len(peaks)
            up = min(maximum, maximum * peak / normal)
            self.wave.create_line(x, height / 2 - up, x, height / 2 + up,
                                  fill=ACCENT if left <= x <= right else "#686e75", width=2)

    def start_span(self, event):
        if (self.full_excerpt is not None and self.job is None and self.stop is None
                and not self.follow_live and self.source.kind != "saved-excerpt"):
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
        self.work("Checking the selected passage…", self.inspect_clip, self.use_excerpt, self.source, start, end)

    def reset_span(self, _):
        if (self.full_excerpt is not None and self.job is None and self.stop is None
                and not self.follow_live and self.source.kind != "saved-excerpt"):
            self.drag_span = None
            self.work("Checking the full passage…", self.inspect_clip, self.use_excerpt, self.source)

    def record(self):
        if self.stop:
            self.stop.set()
            self.status.set("Saving the dry recording…")
            self.sync()
            return
        if self.job or self.desk.leaving or self.live_stop:
            return
        if self.desk.job is not None and not self.desk.task_if_active("monitor"):
            self.status.set("Tone Lab is using the recorder. Wait for that action to finish.")
            return
        self.stop, self.ready = Event(), Event()
        self.record_started = False
        if self.desk.task_if_active("monitor"):
            self.desk.stop()
            self.status.set("Releasing the Quad Cortex playback loop…")
        if gestures.running():
            gestures.stop()
            self.status.set("Ending gesture listening first…")
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
            if self.desk.job is None and not gestures.running():
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
            return recorder.record_take(channel, GUITAR, stop, ready), None
        except Exception as error:
            LOG.exception("Guitar Learner recording failed")
            return None, str(error)

    def recorded(self, outcome):
        self.stop = self.ready = None
        result, error = outcome
        self.sync()
        if error:
            self.status.set(error)
            self.desk.set_status(error)
            return
        self.desk.takes = recorder.clip_paths()
        self.desk.set_status(result[1])
        try:
            self.select_source(data.source_from_result(GUITAR, result))
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def show_guess(self, guess, label=None, *, stale=False):
        keys = ["pickup", "volume", "tone"]
        if "wiring" in data.LAYOUTS[self.layout]:
            keys.insert(1, "wiring")
        predicted = [key for key in keys if guess.get(key) is not None]
        control_names = {"pickup": "Pickup", "wiring": "Mini-toggle", "tone": "Tone", "volume": "Volume"}
        held = {key for key in ("volume", "tone") if len({row["configuration"][key] for row in self.examples}) <= 1}
        present = [f"{control_names[key]}  {prediction_value(key, guess[key], self.layout)}" for key in predicted]
        unresolved = [control_names[key] for key in keys if key not in predicted and key not in held]
        if present and unresolved:
            present.append(f"{' and '.join(unresolved)} uncertain")
        confidence = guess["confidence"].title()
        if len(predicted) == 1:
            headline = f"{confidence} {control_names[predicted[0]].lower()} estimate"
        elif predicted:
            headline = f"{confidence} estimate"
        else:
            headline = "Insufficient evidence"
        if stale:
            headline = "Last estimate"
        self.headline.set(headline)
        self.details.set("  ·  ".join(present))
        notes = [guess["reason"]]
        if len(predicted) == 1 and (evidence := guess.get("evidence", {}).get(predicted[0])):
            count = len(evidence["notes_used"])
            notes.append(f"{count} comparable pitch{'es' if count != 1 else ''} supported this estimate.")
        if guess.get("nearest") and not predicted:
            nearest = guess["nearest"]
            reference = prediction_value("pickup", nearest["pickup"], self.layout)
            if nearest.get("wiring"):
                reference += f" · {nearest['wiring']}"
            notes.append(f"Closest taught setup · {reference}")
        if held:
            notes.append(f"{' and '.join(control_names[key] for key in keys if key in held)} need examples at different settings.")
        if label is not None:
            actual = label["configuration"]
            caption = f"Actual · {summarize(self.layout, actual)}"
            if setup_key(actual) not in {setup_key(row["configuration"]) for row in self.examples}:
                caption += " · outside taught setups"
            notes.insert(0, caption)
        self.note.set("\n".join(notes))
        if not self.report.winfo_manager():
            self.report.pack(after=self.status_line, fill="x", pady=(6, 0))

    def add(self, role):
        if self.job or self.stop or self.source is None or self.follow_live or self.snapshot_request:
            return
        source, values, layout, excerpt, old = self.source, dict(self.board.values), self.layout, self.excerpt, self.used_source

        def label():
            if role == "example" and not model.usable_notes(excerpt.samples):
                raise ValueError("No sustained single notes detected. Select a clearer passage or record again.")
            if old:
                row = data.retag(GUITAR, layout, old["id"], values, role=role, promote=role == "example")
            else:
                row = data.add(GUITAR, layout, source, values, role=role, start=excerpt.source_start, end=excerpt.source_end)
            return row, evaluate(layout)

        def saved(result):
            self.used_source, fit = result
            self.loaded(fit)
            if role == "example":
                self.guess = None
            else:
                self.guess = next((guess for row, guess in self.checks if row["id"] == self.used_source["id"]), None)
            if not self.status.get():
                self.status.set("Teaching tag saved." if role == "example" else "Check tag saved.")
            self.sync()

        self.work("Saving the tag…", label, saved)

    def toggle_live(self):
        if self.live_stop is not None:
            self.live_stop.set()
            self.live_status.set("Closing the dry input…")
            self.sync()
            return
        if self.stop or self.job or self.desk.leaving:
            return
        if self.desk.job is not None and not self.desk.task_if_active("monitor"):
            self.status.set("Tone Lab is using the audio input. Wait for that action to finish.")
            return
        self.live_stop = Event()
        self.live_events, self.live_commands = Queue(), Queue()
        self.live_started = False
        self.live_state = self.live_change = self.last_live_guess = None
        if self.desk.task_if_active("monitor"):
            self.desk.stop()
            self.live_status.set("Releasing the current Tone Lab playback loop…")
        if gestures.running():
            gestures.stop()
            self.live_status.set("Ending gesture listening first…")
        self.follow()
        self.poll_live()

    def follow(self):
        self.follow_live = True
        self.status.set("")
        self.draw_wave()
        self.sync()

    def poll_live(self):
        if self.closed or self.live_stop is None:
            return
        if not self.live_started:
            if self.live_stop.is_set() or self.desk.leaving:
                self.live_finished((None, None))
                return
            if self.desk.job is None and not gestures.running():
                self.live_started = True
                self.desk.stop_signal = self.live_stop
                config = {"physical_input": self.desk.input_channel, "guitar": GUITAR, "profile": self.profile}
                self.desk.submit("learner-listen", "Opening Guitar Learner dry input…", self.listen_worker,
                                 config, self.live_stop, self.live_commands, self.live_events.put, done=self.live_finished)
                self.live_status.set("Opening the dry input…")
        try:
            while True:
                self.live_event(self.live_events.get_nowait())
        except Empty:
            pass
        self.sync()
        self.window.after(100, self.poll_live)

    @staticmethod
    def listen_worker(config, stop, commands, report):
        try:
            return worker.run(config, stop, commands, report), None
        except Exception as error:
            LOG.exception("Guitar Learner live input failed")
            return None, str(error)

    def live_event(self, message):
        if isinstance(message, str):
            self.live_status.set(message)
            return
        kind = message["event"]
        if kind == "learner-ready":
            self.live_status.set(f"Listening · USB dry input {message['dry_channel']}")
        elif kind == "learner-state":
            self.live_state = message
            if message.get("change"):
                self.live_change = message["change"]
                self.last_live_guess = None
            estimate = message.get("estimate")
            if estimate and estimate.get("confidence") != "uncertain":
                self.last_live_guess = estimate
            if not self.live_stop.is_set():
                note = (f" · {message['reason']}" if message.get("mode") in ("silent", "listening") else "")
                self.live_status.set(f"Live · {message['buffered_seconds']:g}s buffered{note}")
            if self.follow_live:
                self.draw_wave()
        elif kind == "learner-snapshot" and message.get("request_id") == self.snapshot_request:
            self.snapshot_request = None
            if message.get("path"):
                try:
                    self.select_source(data.source_from_take(GUITAR, message["folder"]))
                except (OSError, ValueError) as error:
                    self.status.set(str(error))
            else:
                self.status.set(message.get("reason") or "The recent audio was unavailable.")

    def live_finished(self, outcome):
        _, error = outcome
        self.live_stop = self.live_state = self.live_change = self.snapshot_request = None
        self.live_started = self.follow_live = False
        self.live_status.set("")
        self.status.set(error or "Live listening stopped.")
        self.desk.set_status(self.status.get())
        self.draw_wave()
        self.sync()

    def keep_live(self, *, part=None):
        if self.snapshot_request or self.job or not self.live_stop or self.live_stop.is_set():
            return
        request = uuid4().hex
        self.snapshot_request = request
        message = {"event": "learner-snapshot", "request_id": request, "seconds": 8}
        if part and self.live_change and self.live_change.get("id"):
            message.update(change_id=self.live_change["id"], part=part)
        self.live_commands.put(message)
        self.status.set("Keeping a copy of the recent dry audio…")
        self.sync()

    def show_live(self):
        state = self.live_state or {}
        self.source_title.set("Recent dry guitar")
        self.source_detail.set("Rolling audio · Keep a clip to save it")
        estimate = state.get("estimate")
        if estimate and estimate["confidence"] != "uncertain":
            self.show_guess(estimate)
        elif self.last_live_guess and state.get("mode") == "silent":
            self.show_guess(self.last_live_guess, stale=True)
        elif estimate:
            self.show_guess(estimate)
        else:
            self.headline.set("Waiting for evidence")
            self.details.set("")
            self.note.set(state.get("reason") or "Play a few notes to start estimating.")
            if not self.report.winfo_manager():
                self.report.pack(after=self.status_line, fill="x", pady=(6, 0))
        if self.live_change and state.get("at", 0) - self.live_change["at"] < 10:
            change = self.live_change
            if change.get("confirmation") == "supported":
                changed = []
                for key in change["changed"]:
                    title = "Mini-toggle" if key == "wiring" else key.title()
                    before, after = (prediction_value(key, change[side][key], self.layout) for side in ("before", "after"))
                    changed.append(f"{title} {before} → {after}")
                caption = "  ·  ".join(changed)
            else:
                caption = "Possible audible change"
            self.note.set(f"{caption}\n{self.note.get()}")

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
            LOG.info("Guitar Learner action did not finish: %s", error)
            self.status.set(str(error))
        self.sync()
