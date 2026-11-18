"""The main Tone Lab workspace and its take/reamp/feedback sidebar."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import tkinter as tk
import winsound

from . import diagnostics, feedback, session
from .capture import clip_info, clip_paths
from .guitar_ui import GuitarInputPanel
from .library_ui import LibraryPanel
from .live_ui import Knob, LivePanel
from .preset_library_ui import PresetLibraryPanel
from .soundcheck_ui import SoundCheck
from .widgets import ACCENT, BG, FONT, INK, LINE, MUTED, PANEL, TEXT, add_scrollbar, button, card, entry, label, theme


class Review:
    def __init__(self, root, recorder, latest):
        self.root, self.recorder, self.latest = root, recorder, latest
        theme(root)
        self.take = self.folder = self.pending = None
        self.pinned_take = None
        self.heard = None
        self.take_info, self.comparison = {}, {}
        self.history = []
        self.can_render = False
        self.library_worker = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tone-library")
        self.take_text = tk.StringVar(value="No take yet")
        self.take_detail = tk.StringVar(value="Record dry and wet audio to start.")
        self.comparison_text = tk.StringVar(value="No comparison yet")
        self.status = tk.StringVar()
        self.text = tk.StringVar()
        self.reference_text = tk.StringVar()
        self.guitar_file = session.SESSIONS / "player.json"
        try:
            guitar = json.loads(self.guitar_file.read_text(encoding="utf-8")).get("guitar")
        except (OSError, ValueError, AttributeError):
            guitar = None
        self.guitar = tk.StringVar(value=guitar if guitar in session.GUITARS else session.GUITARS[0])

        shell = tk.Frame(root, bg=BG, padx=23, pady=15)
        shell.pack(fill="both", expand=True)
        header = tk.Frame(shell, bg=BG)
        header.pack(fill="x", pady=(0, 12))
        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left")
        label(brand, "Tone Lab", size=25, strong=True).pack(anchor="w")
        nav = tk.Frame(header, bg=BG)
        nav.pack(side="right")
        self.tabs = {}
        for key, name in (("live", "Live"), ("presets", "Presets"), ("search", "Tone search"), ("match", "Tone match"),
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
        self.presets = PresetLibraryPanel(self, self.views["presets"])
        self.soundcheck = SoundCheck(self, self.views["soundcheck"], self.library_worker)
        self.guitar_inputs = GuitarInputPanel(self, self.views["guitar"], self.library_worker)
        self.build_sidebar(sidebar)
        self.show_view("live")
        root.bind("<Control-k>", self.focus_intent)

        self.text.trace_add("write", lambda *_: self.update_buttons())
        self.refresh()
        self.live_panel.restore_draft()

    def scrollable_live(self):
        host = self.views["live"]
        scroller = tk.Canvas(host, bg=BG, highlightthickness=0)
        scroller.pack(side="left", fill="both", expand=True)
        add_scrollbar(host, scroller)
        body = tk.Frame(scroller, bg=BG)
        token = scroller.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _: scroller.configure(scrollregion=scroller.bbox("all")))
        scroller.bind("<Configure>", lambda event: scroller.itemconfigure(token, width=event.width))
        def wheel(event):
            if self.active_view == "live" and str(event.widget).startswith(str(host) + ".") and not isinstance(event.widget, Knob):
                scroller.yview_scroll(-int(event.delta / 120), "units")
        self.root.bind("<MouseWheel>", wheel, add="+")
        return body

    def focus_intent(self, _):
        self.show_view("live")
        self.live_panel.field.focus_set()
        return "break"

    def build_sidebar(self, parent):
        take = card(parent, padding=13)
        take.pack(fill="x", pady=(0, 10))
        player = tk.Frame(take, bg=PANEL)
        player.pack(fill="x", pady=(0, 7))
        label(player, "Playing", color=MUTED, size=9).pack(side="left")
        guitar = tk.Menubutton(player, textvariable=self.guitar, indicatoron=True, takefocus=True,
                               font=(FONT, 10, "bold"), background=PANEL, foreground=ACCENT, padx=7)
        choices = tk.Menu(guitar, tearoff=False)
        guitar.configure(menu=choices)
        for name in session.GUITARS:
            choices.add_command(label=name, command=lambda value=name: self.set_guitar(value))
        guitar.pack(side="left")
        heading = tk.Frame(take, bg=PANEL)
        heading.pack(fill="x")
        label(heading, "Current take", size=13, strong=True).pack(side="left")
        self.take_menu = tk.Menubutton(heading, text="Change", indicatoron=True, takefocus=True,
                                      font=(FONT, 9), background=PANEL, foreground=TEXT)
        self.takes = tk.Menu(self.take_menu, tearoff=False, postcommand=self.show_takes)
        self.take_menu.configure(menu=self.takes)
        self.take_menu.pack(side="right")
        label(take, variable=self.take_text, size=11, strong=True, wrap=295).pack(anchor="w", pady=(6, 0))
        label(take, variable=self.take_detail, color=MUTED, wrap=295).pack(anchor="w", pady=(4, 0))
        source = tk.Frame(take, bg=PANEL)
        source.pack(fill="x", pady=(7, 0))
        self.dry = button(source, "Dry", lambda: self.play_take("dry"), compact=True)
        self.dry.pack(side="left", padx=(0, 5))
        self.wet = button(source, "Original wet", lambda: self.play_take("wet"), compact=True)
        self.wet.pack(side="left")
        self.reference_label = label(take, variable=self.reference_text, color=ACCENT, size=9, wrap=295)

        renders = card(parent, padding=13)
        renders.pack(fill="x", pady=(0, 10))
        label(renders, "Compare versions", size=13, strong=True).pack(anchor="w")
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
        self.legend = tk.StringVar()
        self.legend_label = label(renders, variable=self.legend, size=9, color=MUTED, wrap=270)
        self.legend_label.pack(anchor="w", pady=(5, 0))
        version_row = tk.Frame(renders, bg=PANEL)
        version_row.pack(fill="x", pady=(8, 0))
        self.previous_button = button(version_row, "Older", lambda: self.move(1), compact=True)
        self.previous_button.pack(side="left", padx=(0, 5))
        self.next_button = button(version_row, "Newer", lambda: self.move(-1), compact=True)
        self.next_button.pack(side="left", padx=(0, 5))
        self.blind = False
        self.blind_button = button(version_row, "Blind", self.toggle_blind, compact=True)
        self.blind_button.pack(side="left")

        grading = self.grading = card(parent, padding=13)
        self.note_button = button(grading, "Add listening note", self.toggle_note, compact=True)
        self.note_button.pack(anchor="w")
        self.note_editor = tk.Frame(grading, bg=PANEL)
        self.note_context = tk.StringVar()
        label(self.note_editor, variable=self.note_context, color=MUTED, size=9, wrap=270).pack(anchor="w", pady=(5, 4))
        self.entry = entry(self.note_editor, self.text, width=29)
        self.entry.pack(fill="x", ipady=5)
        self.entry.bind("<Return>", lambda _: self.submit())
        self.send = button(self.note_editor, "Save note", self.submit, compact=True)
        self.send.pack(anchor="e", pady=(5, 0))
        label(grading, variable=self.status, color=MUTED, wrap=295).pack(anchor="w", pady=(5, 0))
        grading.pack(fill="x")

    def set_guitar(self, guitar):
        self.guitar.set(guitar)
        try:
            self.guitar_file.parent.mkdir(parents=True, exist_ok=True)
            session.write_json(self.guitar_file, {"guitar": guitar})
        except OSError:
            diagnostics.LOG.exception("Could not remember guitar selection")

    def show_takes(self):
        self.takes.delete(0, tk.END)
        self.takes.add_command(label="Follow newest recording", command=lambda: self.pin_take(None))
        for path in clip_paths()[:15]:
            try:
                info = clip_info(path)
                recorded = datetime.fromtimestamp(path.stat().st_mtime)
            except (OSError, ValueError):
                continue
            title = info.get("song") or info.get("passage") or info.get("wet_preset") or "Recording"
            self.takes.add_command(label=f"{title} · {recorded:%b %d %H:%M}",
                                   command=lambda value=path: self.pin_take(value))

    def pin_take(self, path):
        if self.busy():
            return
        self.stop()
        self.pinned_take = path
        self.read_state()

    def play_take(self, key):
        from . import tone_search

        path = self.take_info.get(key)
        if path:
            try:
                self.play_file(tone_search.audition({"file": path,
                                                     "selection": self.take_info.get("selected_passage_seconds")}))
            except (OSError, ValueError, RuntimeError) as error:
                self.status.set(str(error))

    def toggle_blind(self):
        self.blind = not self.blind
        self.blind_button.configure(text="Reveal" if self.blind else "Blind")
        self.show_comparison()

    def toggle_note(self):
        if self.note_editor.winfo_manager():
            self.note_editor.pack_forget()
        else:
            self.note_editor.pack(fill="x", after=self.note_button)
            self.entry.focus_set()

    def listening_note(self):
        if not self.folder:
            return ""
        try:
            notes = feedback.notes(self.folder)
        except (OSError, ValueError):
            return ""
        if not notes:
            return ""
        last = notes[-1]
        source = last.get("audition_name")
        return f"{source} · {last['text']}" if source else last["text"]

    def show_view(self, key):
        if self.active_view:
            self.views[self.active_view].pack_forget()
        self.active_view = key
        self.views[key].pack(fill="both", expand=True)
        for name, tab in self.tabs.items():
            tab.configure(background=ACCENT if name == key else "#28332d",
                          foreground=INK if name == key else TEXT)
        if key == "presets":
            self.presets.show()

    def refresh(self):
        if not self.text.get().strip() and self.pending is None:
            self.read_state()
        self.root.after(1500, self.refresh)

    def read_state(self):
        try:
            info = clip_info(self.pinned_take) if self.pinned_take else self.latest(pair=True)
        except (OSError, ValueError):
            self.update_buttons()
            return
        previous_take = self.take
        previous_head = self.history[0][0] if self.history else None
        path = info.get("dry") or info.get("raw")
        self.take_info = info
        self.take = Path(path).parent if path and Path(path).name in ("dry.wav", "inputs.wav") else None
        self.can_render = all(info.get(field) for field in ("dry", "wet", "wet_preset", "wet_scene"))
        self.take_text.set(info.get("song") or info.get("passage") or "Guitar recording")
        details = [info.get("guitar"), info.get("wet_preset")]
        if info.get("wet_scene"):
            details.append(f"Scene {info['wet_scene']}")
        excerpt = info.get("selected_passage_seconds")
        if excerpt:
            details.append(f"{excerpt[1] - excerpt[0]:.1f} s excerpt")
        elif "duration_seconds" in info:
            details.append(f"{info['duration_seconds']:.1f} s")
        self.take_detail.set(" · ".join(value for value in details if value))
        self.history = list(feedback.comparisons(self.take.name)) if self.take else []
        if (not self.history or self.take != previous_take or previous_head != self.history[0][0]
                or not any(self.folder == folder for folder, _ in self.history)):
            self.folder, self.comparison = self.history[0] if self.history else (None, {})
            self.heard = None
        self.show_comparison()

    def show_comparison(self):
        options = self.comparison.get("auditions", {})
        if options:
            style = self.comparison.get("style")
            summary = style.capitalize() if style else "Level-matched"
            count = len(options)
            display = f"{summary} · {count} version" + ("s" if count != 1 else "")
            if self.comparison.get("kind") == "match-audition":
                display = "Level-matched · " + ("Same performance" if self.comparison["same_candidate_take"]
                                                else "Separate performances")
            self.comparison_text.set(display)
        else:
            self.comparison_text.set("No reamps for this take yet")
        tags = [f"{key}  {self.audition_name(key)}" for key in options]
        self.legend.set("\n".join(tags) if tags and not self.blind else "")
        if self.legend.get():
            if not self.legend_label.winfo_manager():
                self.legend_label.pack(anchor="w", pady=(5, 0), after=self.play["A"].master)
        else:
            self.legend_label.pack_forget()
        self.note_context.set(
            f"About {self.heard} · {self.audition_name(self.heard)}" if self.heard and not self.blind
            else f"About {self.heard}" if self.heard else "About this comparison")
        self.update_buttons()

    def audition_name(self, key):
        path = self.comparison.get("auditions", {}).get(key, {}).get("render")
        for row in self.comparison.get("candidates", []):
            if path == row["file"]:
                name = row.get("name") or row.get("source") or f"Scene {row.get('scene', key)}"
                if type(row.get("amount")) in (int, float):
                    name = f"{name} · {row['amount']:.0%}"
                return name
        return "Processed version"

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
                self.heard = None
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
        busy = self.busy()
        self.take_menu.configure(state="disabled" if busy else "normal")
        for key, control in (("dry", self.dry), ("wet", self.wet)):
            control.configure(state="normal" if not busy and self.take_info.get(key) else "disabled")
        self.note_button.configure(state="normal" if self.folder else "disabled")
        self.blind_button.configure(state="normal" if self.folder else "disabled")
        for key, control in self.play.items():
            enabled = not busy and key in self.comparison.get("auditions", {})
            control.configure(state="normal" if enabled else "disabled",
                              background=ACCENT if enabled and key == self.heard else "#28332d",
                              foreground=INK if enabled and key == self.heard else TEXT)
        self.stop_button.configure(state="disabled" if busy else "normal")
        index = self.current_index()
        moving = not busy and not self.text.get().strip()
        self.next_button.configure(state="normal" if moving and index else "disabled")
        self.previous_button.configure(state="normal" if moving and index + 1 < len(self.history) else "disabled")
        self.send.configure(state="normal" if not busy and self.text.get().strip() and self.folder else "disabled")
        self.live_panel.update_buttons()
        self.presets.update_buttons()
        if self.match.reference:
            self.reference_text.set(f"Reference · {self.match.what.get()}")
            if not self.reference_label.winfo_manager():
                self.reference_label.pack(anchor="w", pady=(5, 0))
        else:
            self.reference_label.pack_forget()
        if hasattr(self, "guitar_inputs"):
            self.guitar_inputs.set_buttons()

    def play_file(self, path):
        if self.busy():
            raise RuntimeError("Stop the live session or wait for the current recording first.")
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def listen(self, key):
        try:
            self.play_file(self.folder / self.comparison["auditions"][key]["file"])
            self.heard = key
            self.show_comparison()
        except RuntimeError as error:
            self.status.set(str(error))

    def mark_audition(self, wav):
        if wav.parent != self.folder:
            self.select_comparison(wav.parent)
        if wav.parent == self.folder:
            for key, row in self.comparison.get("auditions", {}).items():
                if row["file"] == wav.name:
                    self.heard = key
                    self.show_comparison()
                    break

    @staticmethod
    def stop():
        winsound.PlaySound(None, 0)

    def submit(self):
        note = self.text.get().strip()
        if not note or self.busy() or not self.folder:
            return
        try:
            feedback.save(self.folder, note, audition=self.heard,
                          audition_name=self.audition_name(self.heard) if self.heard else None)
        except (ValueError, OSError) as error:
            self.status.set(str(error))
            return
        self.text.set("")
        self.note_editor.pack_forget()
        self.status.set("Listening note saved")

    def render_knobs(self, take, values):
        from . import iterate

        self.render(iterate.render_knobs, take, values)

    def render(self, work, *args, status="Rendering over USB…", on_success=None, on_error=None, **kwargs):
        if self.busy():
            return False
        self.stop()
        self.recorder.button.configure(state="disabled")
        self.status.set(status)
        self.render_callbacks = (on_success, on_error)
        self.pending = self.recorder.worker.submit(work, *args, **kwargs)
        self.update_buttons()
        self.root.after(150, self.finish)
        return True

    def finish(self):
        if not self.pending.done():
            self.root.after(150, self.finish)
            return
        future, self.pending = self.pending, None
        on_success, on_error = self.render_callbacks
        self.recorder.button.configure(state="normal")
        try:
            result = future.result()
        except Exception as error:
            diagnostics.LOG.exception("Quad Cortex operation failed")
            self.status.set(str(error))
            if on_error is not None:
                on_error(error)
        else:
            if on_success is None:
                self.text.set("")
                self.status.set("Comparison ready")
            else:
                self.status.set("")
                on_success(result)
        finally:
            self.read_state()

    def shutdown(self):
        self.live_panel.shutdown()
        self.library_worker.shutdown(wait=False, cancel_futures=True)
