"""The main Tone Lab workspace and its take/reamp/feedback sidebar."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
import winsound

from . import diagnostics, feedback, styles
from .guitar_ui import GuitarInputPanel
from .library_ui import LibraryPanel
from .live_ui import LivePanel
from .soundcheck_ui import SoundCheck
from .widgets import ACCENT, BG, INK, LINE, MUTED, PANEL, TEXT, add_scrollbar, button, card, entry, label, theme


class Review:
    def __init__(self, root, recorder, latest):
        self.root, self.recorder, self.latest = root, recorder, latest
        theme(root)
        self.take = self.folder = self.pending = None
        self.take_info, self.comparison = {}, {}
        self.history = []
        self.can_render = False
        self.library_worker = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tone-library")
        self.take_text = tk.StringVar(value="No take yet")
        self.take_detail = tk.StringVar(value="Record dry and wet audio to start.")
        self.comparison_text = tk.StringVar(value="No comparison yet")
        self.status = tk.StringVar()
        self.text = tk.StringVar()

        shell = tk.Frame(root, bg=BG, padx=23, pady=18)
        shell.pack(fill="both", expand=True)
        header = tk.Frame(shell, bg=BG)
        header.pack(fill="x", pady=(0, 12))
        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left")
        label(brand, "Tone Lab", size=25, strong=True).pack(anchor="w")
        label(brand, "Quad Cortex", color=MUTED, size=9).pack(anchor="w")
        nav = tk.Frame(header, bg=BG)
        nav.pack(side="right")
        self.tabs = {}
        for key, name in (("live", "Live"), ("search", "Tone search"), ("match", "Tone match"),
                          ("soundcheck", "Sound check"), ("guitar", "Guitar input")):
            choice = button(nav, name, lambda view=key: self.show_view(view), compact=True)
            choice.pack(side="left", padx=(6, 0))
            self.tabs[key] = choice

        transport_bar = card(shell, padding=10)
        transport_bar.pack(fill="x", pady=(0, 13))
        transport = tk.Frame(transport_bar, bg=PANEL)
        transport.pack(side="left")
        self.recorder.button = button(transport, "Record clip", self.recorder.press, accent=True, compact=True)
        self.recorder.button.pack(side="left", padx=(0, 8))
        state = tk.Frame(transport_bar, bg=PANEL)
        state.pack(side="right", fill="x")
        label(state, variable=self.recorder.status, color=MUTED, wrap=315).pack(anchor="e")

        work = tk.Frame(shell, bg=BG)
        work.pack(fill="both", expand=True)
        sidebar = tk.Frame(work, bg=BG)
        sidebar.pack(side="right", fill="y", padx=(14, 0))
        views = tk.Frame(work, bg=BG)
        views.pack(side="left", fill="both", expand=True)
        self.views = {key: tk.Frame(views, bg=BG) for key in self.tabs}
        self.active_view = None
        self.live_panel = LivePanel(self, self.scrollable_live(), transport)
        self.live_status = label(state, variable=self.live_panel.status, color=ACCENT, size=9, wrap=315)
        self.live_status.pack(anchor="e")
        self.search = LibraryPanel(self, self.views["search"], self.library_worker)
        self.match = LibraryPanel(self, self.views["match"], self.library_worker, matching=True)
        self.soundcheck = SoundCheck(self, self.views["soundcheck"], self.library_worker)
        self.guitar_inputs = GuitarInputPanel(self, self.views["guitar"], self.library_worker)
        self.build_sidebar(sidebar)
        self.show_view("live")

        self.text.trace_add("write", lambda *_: self.update_buttons())
        self.refresh()

    def scrollable_live(self):
        host = self.views["live"]
        scroller = tk.Canvas(host, bg=BG, highlightthickness=0)
        scroller.pack(side="left", fill="both", expand=True)
        add_scrollbar(host, scroller)
        body = tk.Frame(scroller, bg=BG)
        token = scroller.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _: scroller.configure(scrollregion=scroller.bbox("all")))
        scroller.bind("<Configure>", lambda event: scroller.itemconfigure(token, width=event.width))
        scroller.bind("<MouseWheel>", lambda event: scroller.yview_scroll(-int(event.delta / 120), "units"))
        return body

    def build_sidebar(self, parent):
        take = card(parent, padding=13)
        take.pack(fill="x", pady=(0, 10))
        label(take, "Current take", size=13, strong=True).pack(anchor="w")
        label(take, variable=self.take_text, size=11, strong=True, wrap=295).pack(anchor="w", pady=(6, 0))
        label(take, variable=self.take_detail, color=MUTED, wrap=295).pack(anchor="w", pady=(4, 0))

        renders = card(parent, padding=13)
        renders.pack(fill="x", pady=(0, 10))
        label(renders, "Compare reamps", size=13, strong=True).pack(anchor="w")
        label(renders, variable=self.comparison_text, color=MUTED, wrap=295).pack(anchor="w", pady=(5, 7))
        player = tk.Frame(renders, bg=PANEL)
        player.pack(fill="x")
        self.play = {}
        for letter in "ABC":
            control = button(player, letter, lambda key=letter: self.listen(key), compact=True)
            control.pack(side="left", fill="x", expand=True, padx=(0, 5))
            self.play[letter] = control
        self.stop_button = button(player, "Stop", self.stop, compact=True)
        self.stop_button.pack(side="left", fill="x", expand=True)
        version_row = tk.Frame(renders, bg=PANEL)
        version_row.pack(fill="x", pady=(8, 0))
        self.previous_button = button(version_row, "Older", lambda: self.move(1), compact=True)
        self.previous_button.pack(side="left", padx=(0, 5))
        self.next_button = button(version_row, "Newer", lambda: self.move(-1), compact=True)
        self.next_button.pack(side="left")

        grading = card(parent, padding=13)
        grading.pack(fill="x")
        label(grading, "Feedback", size=13, strong=True).pack(anchor="w")
        label(grading, "What should change in this comparison?", color=MUTED, size=9).pack(anchor="w", pady=(3, 7))
        self.entry = entry(grading, self.text, width=31)
        self.entry.pack(fill="x", ipady=6)
        self.entry.bind("<Return>", lambda _: self.submit())
        self.send = button(grading, "Save feedback", self.submit, compact=True)
        self.send.pack(anchor="e", pady=(7, 0))
        label(grading, variable=self.status, color=MUTED, wrap=295).pack(anchor="w", pady=(5, 0))

    def show_view(self, key):
        if self.active_view:
            self.views[self.active_view].pack_forget()
        self.active_view = key
        self.views[key].pack(fill="both", expand=True)
        for name, tab in self.tabs.items():
            tab.configure(background=ACCENT if name == key else "#28332d",
                          foreground=INK if name == key else TEXT)

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
        self.take_info = info
        self.take = Path(path).parent if path else None
        self.can_render = all(info.get(field) for field in ("dry", "wet", "wet_preset", "wet_scene"))
        self.take_text.set(info.get("song") or info.get("passage") or "Latest recording")
        details = [info.get("guitar"), info.get("wet_preset")]
        if info.get("wet_scene"):
            details.append(f"Scene {info['wet_scene']}")
        if "duration_seconds" in info:
            details.append(f"{info['duration_seconds']:.1f} s")
        self.take_detail.set(" · ".join(value for value in details if value))
        self.history = list(feedback.comparisons(self.take.name)) if self.take else []
        if (not self.history or self.take != previous_take or previous_head != self.history[0][0]
                or not any(self.folder == folder for folder, _ in self.history)):
            self.folder, self.comparison = self.history[0] if self.history else (None, {})
        self.show_comparison()

    def show_comparison(self):
        options = self.comparison.get("auditions", {})
        if options:
            style = self.comparison.get("style")
            summary = style.capitalize() if style else "Level-matched"
            count = len(options)
            self.comparison_text.set(f"{summary} · {count} version" + ("s" if count != 1 else ""))
        else:
            self.comparison_text.set("No reamps for this take yet")
        self.update_buttons()

    def current_index(self):
        return next((i for i, (folder, _) in enumerate(self.history) if folder == self.folder), 0)

    def move(self, delta):
        target = self.current_index() + delta
        if 0 <= target < len(self.history):
            self.select_comparison(self.history[target][0])

    def select_comparison(self, path):
        if self.text.get().strip():
            self.status.set("Save your feedback before switching comparisons.")
            return
        if self.busy():
            return
        for folder, description in self.history:
            if folder == Path(path):
                self.stop()
                self.folder, self.comparison = folder, description
                try:
                    notes = feedback.notes(folder)
                except (OSError, ValueError):
                    notes = []
                self.status.set(notes[-1]["text"] if notes else "")
                self.show_comparison()
                return

    def busy(self):
        return self.pending is not None or self.recorder.stop is not None or self.live_active()

    def live_active(self):
        return self.live_panel.future is not None

    def update_buttons(self):
        style = styles.intent(self.text.get())
        busy = self.busy()
        for key, control in self.play.items():
            enabled = not busy and key in self.comparison.get("auditions", {})
            control.configure(state="normal" if enabled else "disabled")
        self.stop_button.configure(state="disabled" if busy else "normal")
        index = self.current_index()
        moving = not busy and not self.text.get().strip()
        self.next_button.configure(state="normal" if moving and index else "disabled")
        self.previous_button.configure(state="normal" if moving and index + 1 < len(self.history) else "disabled")
        render = style and self.can_render
        self.send.configure(text="Render change" if render else "Save feedback",
                            state="normal" if not busy and self.text.get().strip() and (self.folder or render) else "disabled")
        self.live_panel.update_buttons()

    def play_file(self, path):
        if self.busy():
            raise RuntimeError("Stop the live session or wait for the current recording first.")
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def listen(self, key):
        try:
            self.play_file(self.folder / self.comparison["auditions"][key]["file"])
        except RuntimeError as error:
            self.status.set(str(error))

    @staticmethod
    def stop():
        winsound.PlaySound(None, 0)

    def submit(self):
        note = self.text.get().strip()
        if not note or self.busy():
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

        self.render(iterate.render, note, take=self.take, arm=True)

    def render_knobs(self, take, values):
        from . import iterate

        self.render(iterate.render_knobs, take, values)

    def render(self, work, *args, **kwargs):
        if self.busy():
            return
        self.stop()
        self.recorder.button.configure(state="disabled")
        self.status.set("Rendering over USB…")
        self.pending = self.recorder.worker.submit(work, *args, **kwargs)
        self.update_buttons()
        self.root.after(150, self.finish)

    def finish(self):
        if not self.pending.done():
            self.root.after(150, self.finish)
            return
        try:
            self.pending.result()
        except Exception as error:
            diagnostics.LOG.exception("Comparison render failed")
            self.status.set(str(error))
        else:
            self.text.set("")
            self.status.set("Comparison ready")
        self.pending = None
        self.recorder.button.configure(state="normal")
        self.read_state()

    def shutdown(self):
        self.live_panel.shutdown()
        self.library_worker.shutdown(wait=False, cancel_futures=True)
