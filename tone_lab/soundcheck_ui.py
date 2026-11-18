"""Listen, measure, and plan level and EQ changes before touching the hardware."""

from pathlib import Path
import tkinter as tk
from tkinter import ttk
from uuid import uuid4

from . import diagnostics, session
from .widgets import ACCENT, BG, LINE, MUTED, PANEL, RECESS, TEXT, add_scrollbar, button, card, label

CONTEXTS = {"Full-range / PA": "frfr", "Guitar cabinet": "cab", "Backing mix": "mix"}


class SoundCheck:
    def __init__(self, review, parent, worker):
        self.review, self.worker = review, worker
        self.pending = self.preview_file = None
        self.take_text = tk.StringVar(value="Check the dry DI and processed signal from the current take.")
        self.trim_text = tk.StringVar(value="Use reamps of the same dry performance to compare preset levels.")
        self.stage_text = tk.StringVar(value="Audition small, level-matched changes on the take's wet audio.")
        self.status = tk.StringVar()
        self.context = tk.StringVar(value=next(iter(CONTEXTS)))
        self.anchor = tk.StringVar(value="Quietest")

        style = ttk.Style(parent)
        style.configure("ToneLab.SoundCheck.TCombobox", fieldbackground=RECESS, foreground=TEXT,
                        background=LINE, arrowcolor=TEXT, padding=4)
        style.map("ToneLab.SoundCheck.TCombobox", fieldbackground=[("readonly", RECESS)],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", RECESS)])

        scroller = tk.Canvas(parent, background=BG, highlightthickness=0)
        scroller.pack(side="left", fill="both", expand=True)
        add_scrollbar(parent, scroller)
        body = tk.Frame(scroller, background=BG)
        body_id = scroller.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _: scroller.configure(scrollregion=scroller.bbox("all")))
        scroller.bind("<Configure>", lambda event: scroller.itemconfigure(body_id, width=event.width))
        scroller.bind("<MouseWheel>", lambda event: scroller.yview_scroll(-int(event.delta / 120), "units"))

        take, row = self.section(body, "Input and output levels", self.take_text)
        self.check = button(row, "Check current take", self.check_take, accent=True, compact=True)
        self.check.pack(side="left")
        label(take, "Use a take with your hardest expected attack. These readings are from recorded USB audio.",
              color=MUTED, wrap=550).pack(anchor="w", pady=(7, 0))

        trim, row = self.section(body, "Relative preset levels", self.trim_text)
        ttk.Combobox(row, textvariable=self.anchor, values=("Quietest", "A", "B", "C"), state="readonly", width=9,
                     style="ToneLab.SoundCheck.TCombobox").pack(side="left", padx=(0, 7))
        self.trims = button(row, "Plan trims for this comparison", self.check_trims, compact=True)
        self.trims.pack(side="left")
        label(trim, "Apply level trims after the tone-producing chain to keep amp drive and compression consistent.",
              color=MUTED, wrap=550).pack(anchor="w", pady=(7, 0))

        stage, row = self.section(body, "Stage EQ preview", self.stage_text)
        ttk.Combobox(row, textvariable=self.context, values=tuple(CONTEXTS), state="readonly", width=17,
                     style="ToneLab.SoundCheck.TCombobox").pack(side="left", padx=(0, 7))
        self.build = button(row, "Make preview", self.stage, compact=True)
        self.build.pack(side="left", padx=(0, 7))
        self.play = button(row, "Play preview", self.play_stage, compact=True)
        self.play.pack(side="left")
        label(stage, "Judge the low end and top end against your backing track or band at soundcheck.",
              color=MUTED, wrap=550).pack(anchor="w", pady=(7, 0))
        label(body, variable=self.status, color=ACCENT, wrap=550).pack(anchor="w", pady=(0, 8))
        self.update_buttons()

    @staticmethod
    def section(parent, title, content):
        panel = card(parent)
        panel.pack(fill="x", pady=(0, 10))
        label(panel, title, size=15, strong=True).pack(anchor="w")
        label(panel, variable=content, color=MUTED, wrap=550).pack(anchor="w", pady=(5, 7))
        row = tk.Frame(panel, background=PANEL)
        row.pack(fill="x")
        return panel, row

    def update_buttons(self):
        idle = self.pending is None
        for control, valid in ((self.check, True), (self.trims, True), (self.build, True),
                               (self.play, self.preview_file is not None)):
            control.configure(state="normal" if idle and valid else "disabled")

    def run(self, status, on_success, fn, *args, **kwargs):
        if self.pending is not None:
            return
        self.status.set(status)
        self.pending = self.worker.submit(fn, *args, **kwargs)
        self.update_buttons()
        self.review.root.after(100, lambda: self.poll(on_success))

    def poll(self, on_success):
        if not self.pending.done():
            self.review.root.after(100, lambda: self.poll(on_success))
            return
        pending, self.pending = self.pending, None
        try:
            result = pending.result()
            on_success(result)
        except Exception as error:
            diagnostics.LOG.exception("Sound check operation failed")
            self.status.set(str(error))
        else:
            self.status.set("")
        self.update_buttons()

    def check_take(self):
        from . import level_plan

        if not self.review.take_info.get("dry") or not self.review.take_info.get("wet"):
            self.status.set("Record a paired dry/wet take first.")
            return
        self.run("Measuring the current take…", self.show_take, level_plan.take_report, self.review.take)

    def show_take(self, data):
        dry, wet = data["dry"], data["wet"]
        lufs = wet.get("lufs")
        loudness = f"{lufs:.1f} LUFS" if lufs is not None else "quiet"
        mono = wet.get("mono_fold_down_db")
        fold = f"  ·  Mono fold-down {mono:+.1f} dB" if mono is not None else ""
        self.take_text.set(f"Dry peak {dry['sample_peak_dbfs']:.1f} dBFS  ·  Wet {loudness}, "
                           f"{wet['true_peak_dbtp_estimate']:.1f} dBTP estimated\n"
                           f"Near-rail samples  {dry['near_rail_samples']} dry  ·  {wet['near_rail_samples']} wet{fold}")

    def check_trims(self):
        from . import level_plan

        if self.review.folder is None:
            self.status.set("Open a saved comparison with at least two raw renders first.")
            return
        anchor = None if self.anchor.get() == "Quietest" else self.anchor.get()
        self.run("Measuring the raw reamps…", self.show_trims, level_plan.comparison_plan, self.review.folder,
                 reference=anchor)

    def show_trims(self, data):
        lines = []
        for item in data["renders"]:
            gain = item.get("post_chain_trim_db")
            name = item.get("name") or item.get("file", "Render")
            label_text = f"{item.get('label') or '•'}  {name}"
            lines.append(f"{label_text}   {gain:+.1f} dB" if gain is not None else
                         f"{label_text}   {item.get('issue', 'No trim available')}")
        self.trim_text.set("\n".join(lines))

    def stage(self):
        from . import stage_check

        wet = self.review.take_info.get("wet")
        if not wet:
            self.status.set("Record a take with wet guitar audio first.")
            return
        folder = session.SESSIONS / "soundcheck" / f"{self.review.take.name}-{uuid4().hex[:6]}"
        self.run("Making level-matched EQ previews…", self.show_stage, stage_check.create_preview, Path(wet), folder,
                 context=CONTEXTS[self.context.get()], guitar=self.review.take_info.get("guitar", ""),
                 target=self.review.take_info.get("song", ""))

    def show_stage(self, data):
        self.preview_file = Path(data["file"])
        cue = "  ·  ".join(f"{key} at {seconds:.1f}s" for key, seconds in data["starts_seconds"].items())
        self.stage_text.set(f"{data['context']['name']}  ·  Original and two small EQ changes\n{cue}")

    def play_stage(self):
        if self.preview_file is not None:
            try:
                self.review.play_file(self.preview_file)
            except RuntimeError as error:
                self.status.set(str(error))
