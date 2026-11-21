"""The Guitar Learner: record or open a dry clip, see what the learner makes of it, tag it.

`GuitarLearner(seat)` is built on the seat the desk gives it (`lab.learner.seat.LearnerSeat`): the guitar and
input chosen on the desk, the one recording or listening at a time (`seat.submit`), work followed on the
desk's clock (`seat.later`, `seat.watch`) and the status line. What it says of an estimate and of its
checks is worded in `lab.learner.report`.

It is shown in the desk's window, in the place of the side panel (`show()`, `Desk.show`), and is laid out for
that width: the clip and what the learner makes of it first, then the guitar's controls to set as they were,
then the tagging. It is made once and kept, so it comes back as it was left. Going back to the desk ends a
recording or a live listening. Nothing is bound to a key, and nothing opens outside the window: a take or a
WAV is chosen from lists in place.

`offered_for(instrument)` says whether the learner has a control layout, with its photograph, for an
instrument; `show()` says so on the desk's line and stays closed when it has none.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from threading import Event
import tkinter as tk
from uuid import uuid4

import numpy as np

from lab.desk.theme import mix
from lab.learner import configuration as model, dataset as data, folders, gestures, inputs, takes as recorder, worker
from lab.learner.board import (ACCENT, ALERT, BG, LINE, MUTED, PANEL, WIDTH, GuitarBoard, LAYOUTS_SHOWN, button,
                               mark_button, word)
from lab.learner.folders import LOG
from lab.learner.report import (evaluate, guess_lines, prediction_value, purpose, report_score, setting,
                                teaching_hint, training_coverage)
from lab.tools.common import FileChoice, listbox, scrolled, usual_folders, wheel

GUIDE = ("Start with a short phrase of clear notes across your usual register. Repeat it after changing one control.",
         "Then add your normal playing in a few known setups. Keep the input gain steady and vary your picking "
         "across the examples.",
         "Check a different performance to see what carries over. Live listening uses the latest usable notes.")


def offered_for(instrument):
    """The control layouts the learner can show for an instrument: none means it is not offered for it."""
    return [layout for layout in data.layouts_for(instrument or "") if layout in LAYOUTS_SHOWN]


def dataset_line(record, values, layout, use):
    """One labelled recording, short enough for a list: what it is used for, its settings, then its name."""
    if not values:
        return f"untagged  ·  {record}"
    wiring = f" {values['wiring']}" if values.get("wiring") else ""
    return (f"{use}  ·  {prediction_value('pickup', values['pickup'], layout)}{wiring}, vol {setting(values['volume'])}, "
            f"tone {setting(values['tone'])}  ·  {record}")


class GuitarLearner:
    TITLE = "Guitar Learner"

    def __init__(self, seat):
        self.seat = seat
        self.guitar = None
        self.frame = seat.desk.host_frame()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="guitar-learner")
        self.job = None
        self.stop = self.ready = None
        self.record_started = False
        self.queued = []
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
        self.dataset_open = self.guide_open = self.takes_open = False
        self.dataset_rows = []
        self.take_sources = []
        self.examples, self.tests, self.checks = [], [], []
        self.profile = None
        self.build()
        self.use_guitar(seat.guitar)

    def use_guitar(self, guitar):
        """Work on the guitar chosen on the desk. False, with the reason on the desk's line, when the learner
        has no control layout for it."""
        layouts = offered_for(guitar)
        if not layouts:
            self.seat.set_status(f"The Guitar Learner has no control layout for the {guitar} yet. Choose a guitar "
                                 "it knows under More, Instrument.")
            return False
        if guitar == self.guitar:
            return True
        if self.job is not None or self.stop is not None or self.live_stop is not None:
            self.status.set(f"Still on the {self.guitar}: finish what is running, then open the learner again.")
            return True
        self.guitar, self.layout = guitar, None
        self.source = self.excerpt = self.full_excerpt = self.used_source = self.guess = None
        self.wave_peaks = ()
        saved = folders.read_json(folders.layout_choice()).get("layout")
        self.change_layout(saved if saved in layouts else layouts[0], remember=False)
        return True

    # ---- in the desk's window ----

    def show(self):
        """Show the learner in the desk's window, on the guitar chosen there; it stays closed for a guitar it has
        no layout for."""
        if self.use_guitar(self.seat.guitar):
            self.seat.desk.show(self)

    def showing(self):
        return self.seat.desk.hosted is self

    def shown(self):
        """The desk has put the learner on show."""

    def hidden(self):
        """The desk has gone back from the learner: a recording or a live listening under way is ended."""
        if self.stop:
            self.stop.set()
        if self.live_stop:
            self.live_stop.set()

    def hide(self):
        """Go back to the desk, if the learner is what is on show."""
        if self.showing():
            self.seat.desk.back()
        else:
            self.hidden()

    # ---- building ----

    def build(self):
        self.scroller, page = scrolled(self.frame)
        self.scroller.configure(background=PANEL)
        page.configure(background=PANEL)
        self.scroller.pack(side="left", fill="both", expand=True)
        header = tk.Frame(page, background=PANEL)
        header.pack(fill="x", pady=(0, 6))
        self.count = tk.StringVar(master=self.frame)
        word(header, variable=self.count, color=ACCENT, size=9, bold=True).pack(side="left")
        self.dataset_button = button(header, "Dataset", self.toggle_dataset, compact=True)
        self.dataset_button.pack(side="right")
        self.guide_button = button(header, "Guide", self.toggle_guide, compact=True)
        self.guide_button.pack(side="right", padx=(0, 4))
        self.guide = tk.Frame(page, background=PANEL)
        for line in GUIDE:
            word(self.guide, line, size=9, color=MUTED, wrap=WIDTH).pack(fill="x")

        panel = self.workspace = tk.Frame(page, background=PANEL)
        panel.pack(fill="x")
        live = tk.Frame(panel, background=PANEL)
        live.pack(fill="x")
        self.live_button = button(live, "Listen live", self.toggle_live, primary=True, compact=True)
        self.live_button.pack(side="left")
        self.follow_button = button(live, "Follow live", self.follow, compact=True)
        self.keep_button = button(live, "Keep recent clip", self.keep_live, compact=True)
        self.live_status = tk.StringVar(master=self.frame)
        self.live_status_line = word(panel, variable=self.live_status, size=9, color=ACCENT, wrap=WIDTH)
        self.live_status_line.pack(fill="x", pady=(4, 0))
        self.change_tools = tk.Frame(panel, background=PANEL)
        word(self.change_tools, "Last change", color=MUTED, size=9).pack(side="left")
        self.change_buttons = []
        for part in ("before", "after"):
            control = button(self.change_tools, f"Keep {part}", lambda side=part: self.keep_live(part=side), compact=True)
            control.pack(side="right", padx=(4, 0))
            self.change_buttons.append(control)

        self.title = tk.StringVar(master=self.frame)
        self.guidance = tk.StringVar(master=self.frame)
        word(panel, variable=self.title, size=11, bold=True).pack(fill="x", pady=(8, 2))
        word(panel, variable=self.guidance, color=MUTED, size=9, wrap=WIDTH).pack(fill="x", pady=(0, 6))

        self.source_title = tk.StringVar(master=self.frame, value="No recording selected")
        self.source_detail = tk.StringVar(master=self.frame, value="Dry input")
        self.clip = tk.Frame(panel, background=BG, padx=8, pady=6)
        self.clip.pack(fill="x")
        word(self.clip, variable=self.source_title, bold=True, size=9, wrap=WIDTH - 20).pack(fill="x")
        self.wave = tk.Canvas(self.clip, background=BG, width=WIDTH - 40, height=43, borderwidth=0,
                              highlightthickness=0)
        self.wave.pack(fill="x", pady=2)
        self.wave.bind("<Configure>", lambda _: self.draw_wave())
        self.wave.bind("<ButtonPress-1>", self.start_span)
        self.wave.bind("<B1-Motion>", self.move_span)
        self.wave.bind("<ButtonRelease-1>", self.end_span)
        self.wave.bind("<Double-Button-1>", self.reset_span)      # the same as "Whole clip" beside it
        foot = tk.Frame(self.clip, background=BG)
        foot.pack(fill="x")
        word(foot, variable=self.source_detail, color=MUTED, size=9).pack(side="left")
        self.whole_button = button(foot, "Whole clip", lambda: self.reset_span(None), compact=True)

        sources = tk.Frame(panel, background=PANEL)
        sources.pack(fill="x", pady=(8, 4))
        self.record_button = button(sources, "●  Record clip", self.record, compact=True)
        self.record_button.pack(side="left", padx=(0, 4))
        self.takes_button = button(sources, "Use a take or a WAV", self.toggle_takes, compact=True)
        self.takes_button.pack(side="left")
        # Choosing a take or a WAV, in place: shown when asked for.
        self.chooser = tk.Frame(panel, background=PANEL)
        word(self.chooser, "Takes recorded on this guitar", size=9, color=MUTED).pack(fill="x")
        self.takes = listbox(self.chooser, height=5)
        self.takes.pack(fill="x")
        self.take_button = button(self.chooser, "Open this take", self.open_take, compact=True)
        self.take_button.pack(anchor="w", pady=(4, 6))
        self.takes.bind("<Double-Button-1>", lambda _: self.open_take())      # the same as the button under it
        word(self.chooser, "Or a mono dry WAV from elsewhere", size=9, color=MUTED).pack(fill="x")
        self.wav_choice = FileChoice(self.chooser, folders=lambda: usual_folders("wav", self.seat.studio.folder),
                                     patterns=("*.wav",), choose=self.choose_wav, action="Open this WAV")
        self.wav_choice.pack(fill="x")

        self.status = tk.StringVar(master=self.frame)
        self.status_line = word(panel, variable=self.status, color=ALERT, wrap=WIDTH, size=9)
        self.status_line.pack(fill="x")

        self.report = tk.Frame(panel, background=PANEL)
        self.headline = tk.StringVar(master=self.frame)
        self.details = tk.StringVar(master=self.frame)
        self.note = tk.StringVar(master=self.frame)
        word(self.report, variable=self.headline, size=11, bold=True, color=ACCENT, wrap=WIDTH).pack(fill="x", pady=(1, 3))
        word(self.report, variable=self.details, bold=True, size=9, wrap=WIDTH).pack(fill="x")
        word(self.report, variable=self.note, color=MUTED, wrap=WIDTH, size=9).pack(fill="x", pady=(3, 4))

        word(panel, "The guitar as it was set", size=9, color=MUTED).pack(fill="x", pady=(8, 2))
        self.board = GuitarBoard(panel, self.change_layout, self.sync)
        self.board.pack(fill="x")

        self.tag_row = tk.Frame(panel, background=PANEL)
        self.tag_hint = tk.StringVar(master=self.frame)
        word(self.tag_row, variable=self.tag_hint, size=9, color=MUTED, wrap=WIDTH).pack(fill="x", pady=(2, 4))
        self.teach = button(self.tag_row, "Teach with this clip", lambda: self.add("example"), primary=True, compact=True)
        self.teach.pack(side="left", padx=(0, 4))
        self.keep_check = button(self.tag_row, "Keep as check", lambda: self.add("test"), compact=True)
        self.keep_check.pack(side="left")
        self.tag_row.pack(fill="x", pady=(6, 2))

        self.coverage = tk.StringVar(master=self.frame)
        self.coverage_line = word(panel, variable=self.coverage, color=ACCENT, size=9, wrap=WIDTH)
        self.coverage_line.pack(fill="x", pady=(6, 0))
        self.score = tk.StringVar(master=self.frame)
        word(panel, variable=self.score, color=MUTED, size=9, wrap=WIDTH).pack(fill="x", pady=(3, 0))
        self.bindings = (self.takes_button, self.take_button)
        self.build_dataset(page)
        wheel(self.scroller)

    def build_dataset(self, page):
        self.dataset = tk.Frame(page, background=PANEL)
        self.dataset_summary = tk.StringVar(master=self.frame)
        word(self.dataset, variable=self.dataset_summary, color=MUTED, size=9, wrap=WIDTH).pack(fill="x", pady=(0, 4))
        self.table = listbox(self.dataset, height=16)
        self.table.pack(fill="x")
        self.table.bind("<<ListboxSelect>>", lambda _: self.sync_dataset_buttons())
        self.table.bind("<Double-Button-1>", lambda _: self.open_dataset_row())       # the same as Open recording
        bar = tk.Frame(self.dataset, background=PANEL)
        bar.pack(fill="x", pady=(8, 0))
        self.dataset_select = button(bar, "Open recording", self.open_dataset_row, primary=True, compact=True)
        self.dataset_select.pack(side="left")
        self.dataset_remove = button(bar, "Remove tag", self.remove_dataset_tag, compact=True)
        self.dataset_remove.pack(side="left", padx=4)
        self.dataset_hint = tk.StringVar(master=self.frame)
        word(self.dataset, variable=self.dataset_hint, color=ALERT, size=9, wrap=WIDTH).pack(fill="x", pady=(4, 0))

    def toggle_guide(self):
        self.guide_open = not self.guide_open
        if self.guide_open:
            view = self.dataset if self.dataset_open else self.workspace
            self.guide.pack(before=view, fill="x", pady=(0, 8))
        else:
            self.guide.pack_forget()
        mark_button(self.guide_button, self.guide_open)

    def toggle_dataset(self):
        self.dataset_open = not self.dataset_open
        if self.dataset_open:
            self.workspace.pack_forget()
            self.dataset.pack(fill="x")
            self.dataset_button.configure(text="Back to the clip")
            self.refresh_dataset()
        else:
            self.dataset.pack_forget()
            self.workspace.pack(fill="x")
            self.dataset_button.configure(text="Dataset")
        mark_button(self.dataset_button, self.dataset_open)

    def toggle_takes(self):
        """Show the takes and WAVs to choose from, under the buttons; pressed again, put them away."""
        self.takes_open = not self.takes_open
        if self.takes_open:
            self.list_takes()
            self.wav_choice.look()
            self.chooser.pack(after=self.takes_button.master, fill="x", pady=(0, 6))
        else:
            self.chooser.pack_forget()
        mark_button(self.takes_button, self.takes_open)

    def refresh_dataset(self):
        if not self.dataset_open:
            return
        self.table.delete(0, "end")
        self.dataset_rows = []
        rows = data.dataset_sources(self.guitar, self.layout)
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
                self.table.insert("end", dataset_line(record, values, self.layout, purpose(label) if label else use))
                self.dataset_rows.append((source, label))
        self.dataset_summary.set(f"{tally['example']} examples  ·  {tally['test']} saved checks  ·  {tally['untagged']} untagged")
        self.sync_dataset_buttons()

    def selected_dataset_row(self):
        picked = self.table.curselection()
        return self.dataset_rows[picked[0]] if picked else None

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
            data.unlabel(self.guitar, self.layout, label["id"])
            return evaluate(self.guitar, self.layout)

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
                folders.layout_choice().parent.mkdir(parents=True, exist_ok=True)
                folders.write_json(folders.layout_choice(), {"layout": layout})
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
        self.work("Reading labeled recordings…", evaluate, lambda result: self.loaded(result, use_last=True),
                  self.guitar, layout)

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
            self.follow_button.pack(side="left", padx=4)
            self.keep_button.pack(side="left")
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
            self.guidance.set("Set the controls below as the guitar was set before tagging.")
        else:
            self.title.set("Check a recording")
            self.guidance.set(teaching_hint(self.layout, self.examples, self.board.values))
        available = self.excerpt is not None and not self.excerpt.clipped and idle and not following and not self.snapshot_request
        role = self.used_source["role"] if self.used_source else None
        if available:
            if not self.tag_row.winfo_manager():
                self.tag_row.pack(fill="x", pady=(6, 2), before=self.coverage_line)
            self.tag_hint.set("Use to teach takes this recording out of the checks."
                              if role == "test" else "Tag the actual settings shown on the guitar's controls.")
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
        """List the takes recorded on this guitar, newest first, each marked when it has no label yet."""
        self.takes.delete(0, tk.END)
        self.take_sources = data.take_sources(self.guitar)[:18]
        labeled = {folders.found(row["source"]).resolve() for row in data.records(self.guitar, self.layout)}
        for source in self.take_sources:
            self.takes.insert(tk.END, source.label if source.path in labeled else f"{source.label} · unlabelled")
        if not self.take_sources:
            self.status.set(f"No verified {self.guitar} dry takes yet: record a clip.")

    def open_take(self):
        picked = self.takes.curselection()
        if not picked:
            self.status.set("Choose a take in the list first.")
            return
        if self.takes_open:
            self.toggle_takes()
        self.select_source(self.take_sources[picked[0]])

    def review_label(self, row):
        source = data.DrySource(Path(row["wav"]), row["source_label"], guitar=self.guitar, kind="saved-excerpt", session=data.session_of(row))
        self.select_source(source, known_label=row)

    def choose_wav(self, path):
        """Open a dry WAV chosen in the list or by its path."""
        if self.job or self.stop:
            return
        try:
            source = data.source_from_wav(path)
        except (OSError, ValueError) as error:
            self.status.set(str(error))
            return
        if self.takes_open:
            self.toggle_takes()
        self.select_source(source)

    def select_source(self, source, *, known_label=None):
        if source.guitar and source.guitar.casefold() != self.guitar.casefold():
            self.status.set(f"This take used {source.guitar}. Choose a recording of the {self.guitar}.")
            return
        self.follow_live = False
        self.work("Reading the clip and checking its settings…", self.inspect_clip,
                  lambda result: self.source_ready(source, result), source, None, None, known_label)

    def inspect_clip(self, source, start=None, end=None, known_label=None):
        excerpt = data.inspect(source, start=start, end=end)
        label = known_label or data.find_label(self.guitar, self.layout, excerpt)
        guess, reason = None, ""
        if excerpt.clipped:
            reason = "This recording clips. Select a cleaner passage or use another dry take."
        elif label and label["role"] == "example":
            reason = "Teaching example. Adjust the controls and update its tag if needed."
        elif (label and not label.get("score_eligible", True)) or not data.never_taught(self.guitar, self.layout, excerpt):
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
        self.whole_button.pack_forget()
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
            detail = f"{excerpt.source_start:.1f}–{excerpt.source_end:.1f}s"
            self.whole_button.pack(side="right")
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
        self.wave.create_rectangle(left, 0, right, height, fill=mix(ACCENT, BG, .22), outline="")
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
                                  fill=ACCENT if left <= x <= right else LINE, width=2)

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
        """Back to the whole clip from a passage of it."""
        if (self.full_excerpt is not None and self.job is None and self.stop is None
                and not self.follow_live and self.source.kind != "saved-excerpt"):
            self.drag_span = None
            self.work("Checking the full passage…", self.inspect_clip, self.use_excerpt, self.source)

    def record(self):
        """Start a take; pressed again, stop and save it."""
        if self.stop:
            self.stop.set()
            self.status.set("Saving the dry recording…")
            self.sync()
            return
        if self.job or self.seat.leaving or self.live_stop:
            return
        if self.seat.job is not None:
            self.status.set("Tone Lab is using the recorder. Wait for that action to finish.")
            return
        self.stop, self.ready = Event(), Event()
        self.record_started = False
        if gestures.running():
            gestures.stop()             # it holds the input; the take starts when it has let go
            self.status.set("Ending gesture listening first…")
        self.seat.watch(self.watch_capture)
        self.watch_capture()

    def watch_capture(self):
        """Start the take once the input is free, and say how it is going. On the desk's clock."""
        if self.stop is None:
            return False
        if not self.record_started:
            if self.stop.is_set() or self.seat.leaving:
                self.stop = self.ready = None
                self.status.set("Recording cancelled.")
                self.sync()
                return False
            if self.seat.job is None and not gestures.running():
                channel, host, guitar, stop, ready = self.seat.input_channel, self.seat.host, self.guitar, self.stop, self.ready

                def record_take():
                    return recorder.record_take(channel, guitar, stop, ready,
                                                routing=lambda: inputs.routing(host, channel))

                if not self.seat.submit("Opening the Quad Cortex dry input for Guitar Learner…", record_take,
                                        done=self.recorded, stop=stop):
                    self.stop = self.ready = None
                    self.status.set("The audio input is in use. Try again when it is free.")
                    self.sync()
                    return False
                self.record_started = True
                self.status.set("Connecting… wait for Recording.")
        elif self.ready.is_set() and not self.stop.is_set():
            self.status.set("Recording… play the notes, then press Stop.")
        self.sync()
        return True

    def recorded(self, outcome):
        self.stop = self.ready = None
        result, error = outcome
        self.sync()
        if error:
            self.status.set(error)
            self.seat.set_status(error)
            return
        self.seat.set_status(result[1])
        try:
            self.select_source(data.source_from_result(self.guitar, result))
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def show_guess(self, guess, label=None, *, stale=False):
        headline, details, notes = guess_lines(guess, self.layout, self.examples, label, stale=stale)
        self.headline.set(headline)
        self.details.set(details)
        self.note.set("\n".join(notes))
        if not self.report.winfo_manager():
            self.report.pack(after=self.status_line, fill="x", pady=(6, 0))

    def add(self, role):
        if self.job or self.stop or self.source is None or self.follow_live or self.snapshot_request:
            return
        source, values, layout, excerpt, old = self.source, dict(self.board.values), self.layout, self.excerpt, self.used_source
        guitar = self.guitar

        def label():
            if role == "example" and not model.usable_notes(excerpt.samples):
                raise ValueError("No sustained single notes detected. Select a clearer passage or record again.")
            if old:
                row = data.retag(guitar, layout, old["id"], values, role=role, promote=role == "example")
            else:
                row = data.add(guitar, layout, source, values, role=role, start=excerpt.source_start, end=excerpt.source_end)
            return row, evaluate(guitar, layout)

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
        if self.stop or self.job or self.seat.leaving:
            return
        if self.seat.job is not None:
            self.status.set("Tone Lab is using the audio input. Wait for that action to finish.")
            return
        self.live_stop = Event()
        self.live_events, self.live_commands = Queue(), Queue()
        self.live_started = False
        self.live_state = self.live_change = self.last_live_guess = None
        if gestures.running():
            gestures.stop()             # it holds the input; listening starts when it has let go
            self.live_status.set("Ending gesture listening first…")
        self.follow()
        self.seat.watch(self.watch_listening)
        self.watch_listening()

    def follow(self):
        self.follow_live = True
        self.status.set("")
        self.draw_wave()
        self.sync()

    def watch_listening(self):
        """Start listening once the input is free, and take in what the worker says. On the desk's clock."""
        if self.live_stop is None:
            return False
        if not self.live_started:
            if self.live_stop.is_set() or self.seat.leaving:
                self.live_finished((None, None))
                return False
            if self.seat.job is None and not gestures.running():
                config = {"physical_input": self.seat.input_channel, "guitar": self.guitar, "profile": self.profile}
                host, stop, commands, said = self.seat.host, self.live_stop, self.live_commands, self.live_events.put

                def listen():
                    channels, _ = inputs.routing(host, config["physical_input"])
                    return worker.run({**config, "dry_channel": channels[0]}, stop, commands, said)

                if not self.seat.submit("Opening Guitar Learner dry input…", listen, done=self.live_finished,
                                        stop=stop):
                    self.live_finished((None, "The audio input is in use. Try again when it is free."))
                    return False
                self.live_started = True
                self.live_status.set("Opening the dry input…")
        try:
            while True:
                self.live_event(self.live_events.get_nowait())
        except Empty:
            pass
        if self.live_stop is None:
            return False
        self.sync()
        return True

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
            if estimate and estimate.get("match") != "none":
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
                    self.select_source(data.source_from_take(self.guitar, message["folder"]))
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
        self.seat.set_status(self.status.get())
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
        self.whole_button.pack_forget()
        self.source_title.set("Recent dry guitar")
        self.source_detail.set("Rolling audio · Keep a clip to save it")
        estimate = state.get("estimate")
        if estimate and estimate["match"] != "none":
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
        """Run `fn(*args)` off the window's thread and hand its result to `done` on the desk's clock. Asked while
        another piece of work runs, it waits its turn: nothing asked for is dropped."""
        if self.job is not None:
            self.queued.append((progress, fn, done, args))
            return
        self.job = self.worker.submit(fn, *args)
        self.status.set(progress)
        self.sync()
        self.seat.later(self.job, lambda result: self.worked(done, result), lambda error: self.worked(None, error))

    def worked(self, done, result):
        self.job = None
        try:
            if done is None:
                raise result
            done(result)
        except Exception as error:  # noqa: BLE001
            LOG.info("Guitar Learner action did not finish: %s", error)
            self.status.set(str(error))
        self.sync()
        if self.queued and self.job is None:
            progress, fn, then, args = self.queued.pop(0)
            self.work(progress, fn, then, *args)
