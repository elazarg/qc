"""Take and comparison review with a single text input for feedback."""

from pathlib import Path
import tkinter as tk
import winsound

from . import diagnostics, feedback, styles

PANEL = "#22292a"
LIGHT = "#f0f1e8"
MUTED = "#b8c3bc"


class Review:
    def __init__(self, root, recorder, latest):
        self.root, self.recorder, self.latest = root, recorder, latest
        self.take = None
        self.folder = None
        self.comparison = {}
        self.history = []
        self.can_render = False
        self.pending = None
        self.live_panel = None
        self.take_text = tk.StringVar(value="No take yet")
        self.comparison_text = tk.StringVar(value="No comparison yet")
        self.status = tk.StringVar()
        self.text = tk.StringVar()

        take = self.section("Take")
        self.label(take, self.take_text).pack(anchor="w", pady=(3, 0))
        renders = self.section("Reamps")
        version_row = tk.Frame(renders, background=PANEL)
        version_row.pack(fill="x", pady=(3, 8))
        self.label(version_row, self.comparison_text).pack(side="left")
        self.next_button = self.button(version_row, "Newer", lambda: self.move(-1))
        self.next_button.pack(side="right")
        self.previous_button = self.button(version_row, "Older", lambda: self.move(1))
        self.previous_button.pack(side="right", padx=(0, 5))
        player = tk.Frame(renders, background=PANEL)
        player.pack(fill="x")
        self.play = {}
        for letter in "ABC":
            self.play[letter] = self.button(player, f"Play {letter}", lambda key=letter: self.listen(key))
            self.play[letter].pack(side="left", padx=(0, 6))
        self.button(player, "Stop", self.stop).pack(side="left")
        self.button(player, "Live controls", self.open_live).pack(side="right")

        grading = self.section("Feedback")
        entry_row = tk.Frame(grading, background=PANEL)
        entry_row.pack(fill="x", pady=(7, 0))
        self.entry = tk.Entry(entry_row, textvariable=self.text, background="#141819", foreground=LIGHT,
                              insertbackground=LIGHT, relief="flat", font=("Segoe UI", 11), width=34)
        self.entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self.entry.bind("<Return>", lambda _: self.submit())
        self.send = self.button(entry_row, "Save feedback", self.submit)
        self.send.pack(side="right")
        self.label(grading, self.status).pack(anchor="w", pady=(7, 0))
        self.text.trace_add("write", lambda *_: self.update_buttons())
        self.refresh()

    def section(self, heading):
        frame = tk.Frame(self.root, background=PANEL, padx=14, pady=11)
        frame.pack(fill="x", pady=(12, 0))
        tk.Label(frame, text=heading, font=("Segoe UI Semibold", 12), foreground=LIGHT,
                 background=PANEL).pack(anchor="w")
        return frame

    @staticmethod
    def label(parent, text):
        return tk.Label(parent, textvariable=text, foreground=MUTED, background=PANEL,
                        anchor="w", justify="left", wraplength=460, font=("Segoe UI", 10))

    @staticmethod
    def button(parent, title, command):
        return tk.Button(parent, text=title, command=command, foreground=LIGHT,
                         activeforeground=LIGHT, background="#384344", activebackground="#49595a",
                         disabledforeground="#929b97", font=("Segoe UI", 10), relief="flat", padx=9, pady=4)

    def refresh(self):
        if not self.text.get().strip() and self.pending is None:
            self.read_state()
        self.root.after(1500, self.refresh)

    def read_state(self):
        try:
            info = self.latest(pair=True)
        except (OSError, ValueError):
            self.update_buttons()
            return
        previous_take = self.take
        previous_head = self.history[0][0] if self.history else None
        path = info.get("dry") or info.get("raw")
        self.take = Path(path).parent if path else None
        self.can_render = all(info.get(field) for field in ("dry", "wet", "wet_preset", "wet_scene"))
        name = info.get("song") or info.get("passage") or "Latest take"
        detail = " · ".join(x for x in (info.get("guitar"), name, info.get("wet_preset")) if x)
        if "duration_seconds" in info:
            detail += f" · {info['duration_seconds']:.1f}s"
        self.take_text.set(detail)
        self.history = list(feedback.comparisons(self.take.name)) if self.take else []
        if (not self.history or self.take != previous_take or previous_head != self.history[0][0]
                or not any(self.folder == folder for folder, _ in self.history)):
            self.folder, self.comparison = self.history[0] if self.history else (None, {})
        self.show_comparison()

    def show_comparison(self):
        options = self.comparison.get("auditions", {})
        if options:
            style = self.comparison.get("style")
            self.comparison_text.set(f"{' / '.join(options)} · {style.capitalize() + ' comparison' if style else 'Level-matched comparison'}")
        else:
            self.comparison_text.set("No renders for this take yet")
        self.update_buttons()

    def move(self, delta):
        index = next((i for i, (folder, _) in enumerate(self.history) if folder == self.folder), 0)
        target = index + delta
        if not self.text.get().strip() and self.pending is None and 0 <= target < len(self.history):
            self.stop()
            self.folder, self.comparison = self.history[target]
            prior = feedback.notes(self.folder)
            self.status.set(prior[-1]["text"] if prior else "")
            self.show_comparison()

    def update_buttons(self):
        style = styles.intent(self.text.get())
        busy = self.pending is not None or self.recorder.stop is not None or self.live_active()
        for label, control in self.play.items():
            control.configure(state="normal" if not busy and label in self.comparison.get("auditions", {}) else "disabled")
        index = next((i for i, (folder, _) in enumerate(self.history) if folder == self.folder), 0)
        moving = not busy and not self.text.get().strip()
        self.next_button.configure(state="normal" if moving and index else "disabled")
        self.previous_button.configure(state="normal" if moving and index + 1 < len(self.history) else "disabled")
        also_render = style and self.can_render
        self.send.configure(text="Render change" if also_render else "Save feedback",
                            state="normal" if not busy and self.text.get().strip() and (self.folder or also_render) else "disabled")
        if self.live_panel is not None:
            self.live_panel.update_buttons()

    def live_active(self):
        return self.live_panel is not None and self.live_panel.future is not None

    def open_live(self):
        if self.live_panel is not None:
            self.live_panel.window.lift()
            return
        from .live_ui import LivePanel

        self.live_panel = LivePanel(self)

    def listen(self, label):
        path = self.folder / self.comparison["auditions"][label]["file"]
        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except RuntimeError as error:
            self.status.set(str(error))

    @staticmethod
    def stop():
        winsound.PlaySound(None, 0)

    def submit(self):
        note = self.text.get().strip()
        if not note or self.pending is not None or self.recorder.stop is not None or self.live_active():
            return
        style = styles.intent(note) if self.can_render else None
        if not self.folder and not (style and self.take):
            return
        if self.folder:
            try:
                feedback.save(self.folder, note)
            except (ValueError, OSError) as error:
                self.status.set(str(error))
                return
        if not style:
            self.text.set("")
            self.status.set("Feedback saved with this comparison")
            return
        from . import iterate

        self.stop()
        self.recorder.button.configure(state="disabled")
        self.status.set("Rendering over USB…")
        self.pending = self.recorder.worker.submit(iterate.render, note, take=self.take, arm=True)
        self.update_buttons()
        self.root.after(150, self.finish)

    def finish(self):
        if not self.pending.done():
            self.root.after(150, self.finish)
            return
        try:
            self.pending.result()
        except Exception as error:
            diagnostics.LOG.exception("Style render failed")
            self.status.set(str(error))
        else:
            self.text.set("")
            self.status.set("Comparison ready")
        self.pending = None
        self.recorder.button.configure(state="normal")
        self.read_state()
