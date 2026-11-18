"""A session organized around a guitarist's next listening decision."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from time import monotonic
import json
import tkinter as tk
from tkinter import filedialog
import webbrowser

from . import advisor, capture, desktop, device, diagnostics, draft_render, listening, live_client, monitor, preset_design, preset_library, qc_library, session, styles
from .audio import RATE
from .knob_panel import KnobPanel
from .tone_identity import Tone
from .tone_space_ui import ToneSpacePanel
from .volume_ui import VolumePanel, saved_settings


PAPER = "#14151a"
WHITE = "#252834"
INK = "#f0ede9"
SUBTLE = "#a5a6b5"
RULE = "#383c4a"
DARK = "#1e2028"
TILE = "#2c303c"
LIGHT = "#f5f1ee"
SOFT = "#bcbcc9"
ACCENT = "#f7b889"
ACCENT_INK = "#1a1718"
FONT = "Segoe UI"


def text(parent, value=None, *, variable=None, size=11, color=INK, bold=False, wrap=0):
    return tk.Label(parent, text=value, textvariable=variable, bg=parent["bg"], fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), justify="left", anchor="w", wraplength=wrap)


def control(parent, title, action, *, primary=False, dark=False):
    return tk.Button(parent, text=title, command=action, relief="flat", borderwidth=0, takefocus=True,
                     padx=13, pady=6, cursor="hand2", font=(FONT, 10, "bold"),
                     bg=ACCENT if primary else TILE if dark else WHITE,
                     fg=ACCENT_INK if primary else INK,
                     activebackground="#ffd7b7" if primary else RULE,
                     activeforeground=ACCENT_INK if primary else INK,
                     disabledforeground="#92909a", highlightthickness=1, highlightbackground=parent["bg"],
                     highlightcolor="#b2541d")


def menu(parent, title, *, dark=False, refresh=None):
    button = tk.Menubutton(parent, text=title, indicatoron=True, cursor="hand2", takefocus=True,
                          font=(FONT, 10, "bold"), bg=TILE if dark else WHITE,
                          fg=LIGHT if dark else INK, padx=13, pady=7)
    entries = tk.Menu(button, tearoff=False, postcommand=refresh, bg=WHITE, fg=LIGHT,
                      activebackground=RULE, activeforeground=LIGHT)
    button.configure(menu=entries)
    return button, entries


class ToneTile(tk.Frame):
    def __init__(self, parent, side, title, caption, command):
        super().__init__(parent, bg=TILE, padx=6, pady=3, highlightthickness=1, highlightbackground=TILE,
                         cursor="hand2")
        self.badge = control(self, side, command, dark=True)
        self.badge.pack(side="left", padx=(0, 8))
        body = tk.Frame(self, bg=TILE, cursor="hand2")
        body.pack(side="left", fill="both", expand=True)
        name = text(body, variable=title, bold=True, size=11)
        name.pack(fill="x")
        hint = text(body, variable=caption, color=SUBTLE, size=9)
        hint.pack(fill="x")
        for widget in (self, body, name, hint):
            widget.configure(cursor="hand2")
            widget.bind("<ButtonRelease-1>", lambda _: self.badge.invoke())

    def sync(self, *, selected, enabled):
        self.configure(highlightbackground=ACCENT if selected else TILE)
        self.badge.configure(bg=ACCENT if selected else TILE, fg=ACCENT_INK if selected else LIGHT,
                             state="normal" if enabled else "disabled")


class Desk:
    def __init__(self, root, input_channel, leave):
        self.root, self.input_channel, self.leave = root, input_channel, leave
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tone-audio")
        self.advisor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tone-advice")
        self.player = listening.Player()
        self.job = self.thinking = self.stop_signal = self.play_job = None
        self.outcome = self.leaving = self.check = None
        self.editor_after = False
        self.play_cancel = Event()
        self.ready = Event()
        self.control_mail = Queue()
        self.live_messages = Queue()
        self.side = "b"
        self.shapes = {"a": {}, "b": {}}
        self.tone_revisions = {"a": 0, "b": 1}
        self.tone_revision = 1
        self.confirmed_tones = {}
        self.tone_requests = {}
        self.active_side = None
        self.active_revision = None
        self.audition_values = {}
        self.available = None
        self.monitor_controls = {}
        self.active_source = None
        self.requested_source = None
        self.phrase_progress = None
        self.base_bypassed = False
        self.takes = listening.takes()
        self.drafts = {draft["id"]: draft for draft in preset_design.history()}
        self.presets = None
        self.reference = self.reference_choice = None
        self.take = self.baseline = self.alternative = self.draft = None
        self.tone_a, self.tone_b = Tone.original(), None
        self.last_heard = self.kept = self.playing = self.play_group = None
        self.kept_note = ""
        self.qc_name = self.qc_position = self.qc_scene = None
        self.state_file = session.SESSIONS / "desk.json"
        self.state = self.read_state(self.state_file)
        self.volume_settings = saved_settings(self.state.get("volume"))
        self.level_feedback = {}
        self.set_player_level()
        saved_shapes = self.state.get("tone_shapes") or {"b": self.state.get("shape", {})}
        for side in ("a", "b"):
            self.shapes[side] = draft_render.clean_values(saved_shapes.get(side))
        if self.state.get("active_tone") in ("a", "b"):
            self.side = self.state["active_tone"]
        guitar = self.state.get("guitar") or self.read_state(session.SESSIONS / "player.json").get("guitar")
        self.guitar = tk.StringVar(value=guitar if guitar in session.GUITARS else session.GUITARS[0])
        self.request = tk.StringVar()
        self.status = tk.StringVar()
        self.phrase = tk.StringVar()
        self.target = tk.StringVar()
        self.output = tk.StringVar(value="Playback paused")
        self.hardware = tk.StringVar(value="Quad Cortex · checking connection")
        self.step = tk.StringVar()
        self.a_name, self.b_name = tk.StringVar(), tk.StringVar()
        self.a_context, self.b_context = tk.StringVar(), tk.StringVar()
        self.build()
        selected = next((take for take in self.takes if take.folder.name == self.state.get("take")), None)
        self.select_take(selected or (self.takes[0] if self.takes else None), remember=False)
        self.restore_context()
        self.root.after(80, self.tick)
        self.root.after(160, self.check_qc)
        self.root.bind("<FocusIn>", lambda _: self.defer_read(), add="+")
        self.focus_timer = None

    @staticmethod
    def read_state(path):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    @property
    def values(self):
        return self.shapes[self.side]

    def build(self):
        root = self.root
        root.title("Tone Lab")
        root.configure(bg=PAPER)
        root.minsize(940, 700)
        root.geometry(f"{min(1180, max(940, root.winfo_screenwidth() - 80))}x"
                      f"{min(800, max(700, root.winfo_screenheight() - 85))}")
        root.protocol("WM_DELETE_WINDOW", lambda: self.close(None))
        root.bind("<Escape>", lambda _: self.stop())
        root.bind("<Control-k>", lambda _: self.command.focus_set())
        root.bind("<Control-1>", lambda _: self.select_side("a"))
        root.bind("<Control-2>", lambda _: self.select_side("b"))
        root.bind("<Control-g>", lambda _: self.start_source("guitar"))
        root.bind("<Control-l>", lambda _: self.start_source("phrase"))
        root.bind("<Control-0>", lambda _: self.toggle_base())

        page = tk.Frame(root, bg=PAPER, padx=22, pady=12)
        page.pack(fill="both", expand=True)
        header = tk.Frame(page, bg=PAPER)
        header.pack(fill="x", pady=(0, 9))
        text(header, "Tone Lab", size=19, bold=True).pack(side="left", padx=(0, 15))
        self.tools, tools = menu(header, "QC")
        tools.add_command(label="Open Cortex Control", command=self.open_editor)
        tools.add_command(label="Check current preset", command=self.check_qc)
        self.tools.pack(side="right")
        guitar, guitars = menu(header, "")
        guitar.configure(textvariable=self.guitar)
        for name in session.GUITARS:
            guitars.add_command(label=name, command=lambda value=name: self.select_guitar(value))
        guitars.add_separator()
        guitars.add_command(label="Learn guitar controls", command=self.show_guitar_calibration)
        guitar.pack(side="right", padx=5)
        text(header, variable=self.hardware, size=9, color=SUBTLE).pack(side="right", padx=(6, 0))

        ask = tk.Frame(page, bg=PAPER)
        ask.pack(fill="x", pady=(0, 6))
        self.command = tk.Entry(ask, textvariable=self.request, font=(FONT, 12), bg=DARK, fg=INK,
                                relief="flat", highlightthickness=1, highlightbackground=RULE,
                                highlightcolor=ACCENT, insertbackground=LIGHT)
        self.command.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 5))
        self.command.bind("<Return>", lambda _: self.ask())
        self.ask_button = control(ask, "Ask", self.ask, primary=True)
        self.ask_button.pack(side="left")
        text(ask, "Win+H", size=9, color=SUBTLE).pack(side="left", padx=7)
        status = text(page, variable=self.status, color=SUBTLE, size=10, wrap=1000)
        status.pack(fill="x", pady=(0, 6))
        page.bind("<Configure>", lambda event: status.configure(wraplength=max(300, event.width - 44)))

        deck = tk.Frame(page, bg=DARK, padx=13, pady=10)
        deck.pack(fill="x", pady=(0, 10))
        line = tk.Frame(deck, bg=DARK)
        line.pack(fill="x")
        self.live_button = control(line, "Guitar", lambda: self.start_source("guitar"), dark=True)
        self.live_button.pack(side="left", padx=(0, 4))
        self.loop_button = control(line, "▶  Phrase", lambda: self.start_source("phrase"), dark=True)
        self.loop_button.pack(side="left", padx=(0, 4))
        self.pause = control(line, "■", self.stop, dark=True)
        self.pause.pack(side="left")
        self.volume = VolumePanel(deck, line, self.volume_settings, self.change_volume)
        self.volume.button.pack(side="left", padx=(5, 0))
        self.save_button = control(line, "Save to QC", self.save, primary=True)
        self.save_button.pack(side="right")
        self.detail_menu, self.details = menu(line, "Details", dark=True, refresh=self.list_details)
        self.detail_menu.pack(side="right", padx=5)
        text(line, variable=self.output, size=10, color=SOFT).pack(side="left", padx=9)

        context = tk.Frame(deck, bg=DARK)
        context.pack(fill="x", pady=(8, 0))
        context.columnconfigure(0, weight=1, uniform="pair")
        context.columnconfigure(1, weight=1, uniform="pair")
        phrase = tk.Frame(context, bg=DARK)
        phrase.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        text(phrase, variable=self.phrase, size=10, color=SOFT).pack(side="left", fill="x", expand=True)
        self.take_menu, self.take_options = menu(phrase, "Takes", dark=True, refresh=self.list_phrases)
        self.take_menu.pack(side="right")
        self.record = control(phrase, "● Rec", self.recording, dark=True)
        self.record.pack(side="right", padx=(0, 4))
        ref = tk.Frame(context, bg=DARK)
        ref.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        text(ref, variable=self.target, size=10, color=SOFT).pack(side="left", fill="x", expand=True)
        self.ref_menu, refs = menu(ref, "Reference", dark=True)
        refs.add_command(label="A saved Quad Cortex preset", command=lambda: self.find_presets(""))
        refs.add_command(label="An audio excerpt", command=self.choose_wave)
        self.reference_history = tk.Menu(refs, tearoff=False, postcommand=self.list_references)
        refs.add_cascade(label="Previously captured presets", menu=self.reference_history)
        refs.add_command(label="Clear reference", command=self.clear_reference)
        self.ref_menu.pack(side="right")
        self.hear_reference = control(ref, "▶", lambda: self.listen("reference"), dark=True)
        self.hear_reference.pack(side="right", padx=(0, 4))
        self.match_button = control(ref, "Match", self.match, dark=True)
        self.match_button.pack(side="right", padx=(0, 4))

        pair = tk.Frame(deck, bg=DARK)
        pair.pack(fill="x", pady=(7, 4))
        pair.columnconfigure((0, 1), weight=1, uniform="pair")
        self.a_tile = ToneTile(pair, "A", self.a_name, self.a_context, lambda: self.select_side("a"))
        self.a_tile.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.b_tile = ToneTile(pair, "B", self.b_name, self.b_context, lambda: self.select_side("b"))
        self.b_tile.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.playhead = tk.Canvas(deck, background=TILE, height=3, highlightthickness=0)
        self.playhead.pack(fill="x")
        self.position = self.playhead.create_rectangle(0, 0, 0, 3, fill=ACCENT, width=0)
        status_row = tk.Frame(deck, bg=DARK)
        status_row.pack(fill="x", pady=(3, 0))
        text(status_row, variable=self.step, size=9, color=SOFT).pack(side="left")
        self.keep_button = control(status_row, "Keep choice", self.keep, dark=True)
        self.keep_button.pack(side="right")

        tabs = tk.Frame(page, bg=PAPER)
        tabs.pack(fill="x", pady=(0, 1))
        self.tabs = {}
        for name in ("Shape", "Sounds", "Lab", "Setup"):
            tab = control(tabs, name, lambda chosen=name: self.show_tab(chosen))
            tab.pack(side="left", padx=(0, 3))
            self.tabs[name] = tab
        tk.Frame(page, bg=RULE, height=1).pack(fill="x", pady=(0, 6))
        viewport = tk.Frame(page, bg=PAPER)
        viewport.pack(fill="both", expand=True)
        self.knobs = KnobPanel(viewport, self)
        self.space = ToneSpacePanel(viewport, read_values=lambda: self.values, apply_values=self.set_shape,
                                    audition=self.audition_shape,
                                    read_sounds=lambda: (self.baseline, self.alternative))
        from .qc_browser import Browser
        from .setup_ui import SetupPanel

        self.browser = Browser(viewport, self)
        self.setup = SetupPanel(viewport, self, live_observer=True)
        self.pages = {"Shape": self.knobs, "Sounds": self.browser, "Lab": self.space, "Setup": self.setup}
        self.show_tab("Shape")

    def show_tab(self, name):
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        for title, button in self.tabs.items():
            button.configure(bg=ACCENT if title == name else WHITE, fg=ACCENT_INK if title == name else INK)
        self.tab = name

    def show_guitar_calibration(self):
        self.show_tab("Setup")
        self.setup.show_calibration()

    def set_status(self, value):
        self.status.set(value)

    def selected_sound(self):
        return self.playing or self.last_heard

    def restore_context(self):
        self.reference = listening.Sound.from_record(self.state.get("reference"))
        if self.state.get("draft") in self.drafts:
            self.draft = self.drafts[self.state["draft"]]
        self.tone_a = (Tone.from_record(self.state.get("tone_a"))
                       or Tone.from_sound(listening.Sound.from_record(self.state.get("baseline"))) or Tone.original())
        self.tone_b = (Tone.from_record(self.state.get("tone_b"))
                       or Tone.from_sound(listening.Sound.from_record(self.state.get("alternative"))) or self.tone_b)
        if not self.tone_b and self.draft:
            self.tone_b = Tone.from_draft(self.draft)
        self.bind_recorded_tone()
        self.resolve_audio()
        if self.state.get("kept"):
            self.kept = Path(self.state["kept"])
            self.kept_note = self.state.get("kept_note", "")
        self.update()

    def remember(self):
        data = {"guitar": self.guitar.get(), "take": self.take.folder.name if self.take else None,
                "kept": str(self.kept) if self.kept else None, "kept_note": self.kept_note,
                "draft": self.draft["id"] if self.draft else None,
                "shape": self.shapes["b"], "tone_shapes": self.shapes, "active_tone": self.side,
                "volume": self.volume_settings,
                "tone_a": self.tone_a.to_record(),
                "tone_b": self.tone_b.to_record() if self.tone_b else None,
                "reference": self.reference.to_record() if self.reference else None,
                "baseline": self.baseline.to_record() if self.baseline else None,
                "alternative": self.alternative.to_record() if self.alternative else None}
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            session.write_json(self.state_file, data)
        except OSError:
            diagnostics.LOG.exception("Could not save the listening desk state")

    def select_guitar(self, name):
        self.guitar.set(name)
        self.remember()
        self.update()

    def list_phrases(self):
        self.take_options.delete(0, tk.END)
        for take in self.takes:
            self.take_options.add_command(label=f"{take.name} · {take.info.get('guitar') or 'Guitar'} · {take.folder.name[14:20]}",
                                          command=lambda chosen=take: self.select_take(chosen))
        if not self.takes:
            self.take_options.add_command(label="No paired phrases yet", state="disabled")

    def select_take(self, take, *, remember=True):
        live_monitor = self.task_if_active("monitor")
        if self.job is not None and not live_monitor:
            return
        self.stop_audio()
        self.last_heard = None
        if self.take != take:
            self.phrase_progress = None
        self.take = take
        if not remember and take:
            _, recent = listening.latest_comparison(take)
            self.tone_b = Tone.from_sound(recent)
        if remember:
            self.bind_recorded_tone()
        self.resolve_audio()
        if live_monitor:
            self.send_levels()
            if self.requested_source == "phrase":
                self.start_source("phrase")
        if remember:
            self.remember()
        self.update()

    def resolve_audio(self):
        self.baseline = self.tone_asset("a")
        self.alternative = self.tone_asset("b")
        self.audition_values = self.rendered_values(self.alternative)
        if (self.tone_b and self.tone_b.preset and self.alternative and self.alternative.draft_id
                and self.audition_values == dict(self.tone_b.values)):
            self.audition_values = {}

    def tone_asset(self, side):
        tone = self.tone(side)
        if tone and tone.kind == "draft":
            tone = tone.with_values(self.shapes[side])
        return tone.asset(self.take) if tone else None

    @staticmethod
    def rendered_values(sound):
        if sound and sound.folder:
            values = Desk.read_state(sound.folder / "session.json").get("knob_values", {})
            return {key: value for key, value in values.items() if value}
        return {}

    def tone(self, side=None):
        return self.tone_a if (side or self.side) == "a" else self.tone_b

    def shape_tone(self, side=None):
        tone = self.tone(side)
        return tone if tone and tone.kind in ("draft", "preset") else None

    def bind_recorded_tone(self):
        """Use an exact address for the recording's QC sound before offering it live."""
        if self.tone_a.kind != "original" or not self.take:
            return
        info = self.take.info
        name = info.get("wet_preset")
        if not name or info.get("wet_preset_unverified"):
            return
        scene = info.get("wet_scene")
        library = self.browser.library
        choices = [row for row in (library.presets if library else ()) if row.name == name]
        address = info.get("wet_position")
        if address is None and self.qc_name == name and self.qc_scene == scene:
            address = self.qc_position
        if address:
            choices = [row for row in choices if row.position == address]
        if len(choices) == 1:
            self.tone_a = Tone.from_preset(choices[0], scene).with_capture(self.take.original)

    def selectable(self, side):
        tone = self.tone(side)
        return bool(self.shape_tone(side) or (side == "b" and tone is None))

    def select_side(self, side):
        if self.job is not None and not self.task_if_active("monitor"):
            return
        if not self.selectable(side):
            self.set_status(f"{side.upper()} is captured audio. Choose a Quad Cortex preset in Sounds to shape it live.")
            return
        if self.side == side:
            return
        self.side = side
        self.available = None
        self.base_bypassed = False
        if not self.update_live_tone():
            self.set_status(f"Tone {side.upper()} selected. Guitar or ▶ Phrase starts the input.")
        self.remember()
        self.update()

    def update_live_tone(self):
        if not self.task_if_active("monitor") or self.stop_signal.is_set():
            return False
        self.output.set(f"Switching to tone {self.side.upper()}…")
        self.send_levels()
        config = self.monitor_config(self.requested_source)
        self.tone_requests[config["tone_revision"]] = self.tone_snapshot(self.side)
        if len(self.tone_requests) > 16:
            self.tone_requests.pop(next(iter(self.tone_requests)))
        self.control_mail.put({"event": "tone", "config": config})
        return True

    def tone_snapshot(self, side):
        return self.tone(side), dict(self.shapes[side]), self.tone_revisions[side]

    def audition_shape(self):
        if self.task_if_active("monitor"):
            self.set_status("The selected shape is already going through the Quad Cortex.")
        elif self.take:
            self.start_source("phrase")
        else:
            self.start_source("guitar")

    def shape_dirty(self):
        return self.shapes["b"] != self.audition_values

    def set_shape(self, values, *, side=None):
        side = side or self.side
        value_map = {}
        for key, value in values.items():
            if key in styles.KNOBS:
                floor = styles.KNOBS[key][1]
                value = round(max(floor, min(1., float(value))), 2)
                if value:
                    value_map[key] = value
        self.shapes[side] = value_map
        if side == self.side and self.task_if_active("monitor"):
            self.base_bypassed = False
            self.control_mail.put({"event": "knobs", "values": value_map, "tone_id": side})
            confirmed = self.confirmed_tones.get(side)
            if confirmed and confirmed[2] == self.tone_revisions[side]:
                self.confirmed_tones[side] = self.tone_snapshot(side)
            self.show_monitor_output()
        elif self.task_if_active("monitor"):
            self.send_levels()
        elif self.playing:
            self.set_status("Shape queued. Start Guitar or ▶ Phrase to hear these controls on the Quad Cortex.")
        self.schedule_state_save()
        self.update()

    def schedule_state_save(self):
        if getattr(self, "shape_writer", None) is not None:
            self.root.after_cancel(self.shape_writer)
        self.shape_writer = self.root.after(350, self.write_shape)

    def write_shape(self):
        self.shape_writer = None
        self.remember()

    def set_player_level(self):
        settings = self.volume_settings
        self.player.gain = 0. if settings["muted"] else 10 ** (settings["master_db"] / 20.)
        self.player.match = settings["match"]

    def change_volume(self, settings):
        self.volume_settings = saved_settings(settings)
        self.set_player_level()
        self.send_levels()
        self.schedule_state_save()
        self.sync_volume()

    def level_config(self, source):
        return {**self.volume_settings, "measurements": {}, "take": str(self.take.folder) if self.take else None,
                "contexts": {side: self.monitor_config(source, side=side)
                             for side in ("a", "b") if self.selectable(side)}}

    def send_levels(self):
        if self.task_if_active("monitor") and not self.stop_signal.is_set():
            self.control_mail.put({"event": "level", **self.level_config(self.requested_source)})

    def sync_volume(self):
        settings = self.volume_settings
        level = self.level_feedback.get(self.side, {})
        status = level.get("status") if self.task_if_active("monitor") else None
        text = "Plays at each tone’s own loudness."
        if settings["match"]:
            if status == "matched":
                text = f"Loudness matched · {self.side.upper()} trim {level['match_db']:+.1f} dB"
            elif status == "unavailable":
                text = level.get("reason") or "Loudness matching is unavailable for this tone."
            elif self.playing and self.play_group and len(self.play_group) == 2:
                text = "Captured A and B are loudness matched."
            elif self.active_source == "phrase":
                text = "Measuring · let the same phrase loop once on A and on B."
            else:
                text = "Match by playing the same recorded phrase through A and B."
        elif status == "unavailable":
            text = level.get("reason") or "Volume is unavailable for this tone."
        if level.get("limited") and self.task_if_active("monitor"):
            text += f"  ·  Output limit {level['applied_db']:+.1f} dB"
        self.volume.sync(settings, text)

    def toggle_base(self):
        if (self.task_if_active("monitor") and self.active_side == self.side and self.active_source
                and self.values and not self.stop_signal.is_set()):
            self.base_bypassed = not self.base_bypassed
            self.control_mail.put({"event": "knobs", "values": {} if self.base_bypassed else self.values, "tone_id": self.side})
            self.show_monitor_output()
            self.update()

    def show_monitor_output(self):
        if (self.active_source and self.active_side == self.side
                and self.active_revision in (None, self.tone_revisions[self.side])):
            shape = " · base" if self.base_bypassed else ""
            side = f"{self.active_side.upper()} · " if self.active_side else ""
            self.output.set(f"{self.active_source.title()} → {side}{self.active_monitor_name}{shape} · Quad Cortex")

    def update(self):
        take = self.take
        if take:
            length = take.section[1] - take.section[0]
            self.phrase.set(f"{take.name}  ·  {take.info.get('guitar') or 'Guitar unlabelled'}  ·  {length:.1f}s")
        else:
            self.phrase.set("Record a phrase or build a sound first")
        self.target.set(f"Reference · {self.reference.name}" if self.reference else "Choose a reference")
        self.a_name.set(self.baseline.name if self.baseline and self.tone_a.kind == "original" else self.tone_a.name)
        self.b_name.set(self.tone_b.name if self.tone_b else "Current Quad Cortex")
        self.a_context.set(self.tone_caption(self.tone_a, self.baseline, shaped=bool(self.shapes["a"])))
        self.b_context.set(self.tone_caption(self.tone_b, self.alternative, shaped=bool(self.shapes["b"])))
        idle = self.job is None
        live_monitor = self.task_if_active("monitor") and not self.stop_signal.is_set()
        can_select = idle or live_monitor
        for button, enabled in (
                (self.ask_button, self.thinking is None and self.leaving is None),
                (self.take_menu, (idle or live_monitor) and self.takes), (self.ref_menu, idle),
                (self.hear_reference, idle and self.reference), (self.match_button, idle and self.take and self.reference),
                (self.keep_button, idle and self.selected_sound()),
                (self.save_button, idle and (self.shape_tone() or self.values)),
                (self.live_button, can_select), (self.loop_button, can_select and self.take),
                (self.record, idle or self.task == "record"),
                (self.pause, not idle and self.stop_signal or self.playing or self.play_job)):
            button.configure(state="normal" if enabled else "disabled")
        for side, tile in (("a", self.a_tile), ("b", self.b_tile)):
            tile.sync(selected=self.side == side, enabled=can_select)
        for name, button in (("guitar", self.live_button), ("phrase", self.loop_button)):
            is_selected = self.active_source == name
            button.configure(bg=ACCENT if is_selected else TILE, fg=ACCENT_INK if is_selected else LIGHT)
        self.sync_volume()
        sound = self.selected_sound()
        if sound and sound.path == self.kept:
            self.keep_button.configure(text="Kept", state="disabled")
        else:
            self.keep_button.configure(text="Keep this sound")
        if live_monitor:
            self.step.set("A/B selects the tone. Guitar and Phrase select the input.")
        elif self.playing:
            self.step.set("Captured output · live shaping is available with Guitar or Phrase")
        else:
            self.step.set("Guitar or ▶ Phrase starts the selected tone.")
        selected_tone = self.shape_tone()
        recipe = self.tone_draft()
        expandable = (can_select and selected_tone and selected_tone.kind == "draft" and recipe
                      and preset_design.additional_shaping(recipe))
        self.knobs.sync(f"{self.side.upper()} · {selected_tone.name if selected_tone else 'Current Quad Cortex'}",
                        self.values, self.available, expandable=bool(expandable))
        if self.tab == "Lab":
            self.space.sync(self.values, self.available)
        self.browser.sync()
        self.setup.sync()

    @staticmethod
    def tone_caption(tone, wave, *, shaped=False):
        if tone is None:
            return "Use a saved tone or shape the current preset"
        if tone.preset:
            scene = f" · scene {device.SCENES[tone.scene]}" if tone.scene is not None else ""
            storage = f"QC · {tone.preset.setlist} {tone.preset.slot}{scene}"
        else:
            storage = {"original": "Recorded processing", "draft": "Draft", "capture": "Captured audio"}[tone.kind]
        if tone.kind in ("original", "capture"):
            return storage if wave else storage + " · no clip for this phrase"
        return storage + " · custom shape" if shaped else storage

    def stop_audio(self):
        self.setup.stop_preview()
        self.play_cancel.set()
        if self.playing or self.play_job or self.player.stream:
            self.worker.submit(self.player.close)
        self.play_group = self.playing = None
        if not self.task_if_active("monitor"):
            self.output.set("Stopped")

    def stop(self):
        self.stop_audio()
        if self.stop_signal is not None:
            self.stop_signal.set()
            self.set_status("Saving the phrase…" if self.task == "record" else "Restoring your Quad Cortex sound…")
        self.update()
        return "break"

    def listen(self, which):
        if self.job is not None or self.leaving is not None:
            return "break"
        sound = {"a": self.baseline, "b": self.alternative, "reference": self.reference}[which]
        if sound is None:
            return "break"
        pair = ((self.baseline, self.alternative) if self.baseline and self.alternative
                and self.baseline.paired_with(self.alternative) and sound in (self.baseline, self.alternative) else (sound,))
        index = pair.index(sound)
        if self.playing and pair == self.play_group and self.player.loop is not None:
            self.player.loop.selected = index
            self.started_playing(sound)
        elif self.play_job is None:
            self.stop_audio()
            self.play_cancel = Event()
            self.output.set("Preparing audio…")
            self.pending_sound, self.pending_pair = sound, pair
            self.play_job = self.worker.submit(self.prepare_playback, pair, index, self.play_cancel)
        self.update()
        return "break"

    def prepare_playback(self, pair, index, cancel):
        tracks, gains = listening.playback_material(pair)
        if not cancel.is_set():
            return self.player.open(tracks, index, gains=gains)

    def started_playing(self, sound):
        self.last_heard = self.playing = sound
        side = "A" if sound == self.baseline else "B" if sound == self.alternative else "reference"
        self.output.set(f"Captured {side} · Windows audio")
        self.update()

    def capture_tone(self, side):
        if self.job is not None:
            return
        if not self.take:
            self.set_status("Choose or record a phrase for this comparison. Guitar is available for live playing.")
            return
        tone = self.shape_tone(side)
        values = dict(self.shapes[side])
        if tone and tone.preset and not values:
            self.render_preset(tone, side)
        elif tone and tone.kind == "draft":
            draft = self.drafts[tone.draft_id]
            self.submit("render", f"Rendering {draft['name']} on this phrase…",
                        draft_render.hear, draft, self.take.folder, values=values,
                        done=lambda folder: self.show_render(folder, side, values))
        elif self.selectable(side):
            config = self.monitor_config("phrase", side=side)
            self.submit("render", "Rendering the current shape on this phrase…",
                        monitor.render_variant, self.take.folder, config,
                        done=lambda folder: self.show_render(folder, side, values))
        else:
            self.set_status("Ask Tone Lab for a change or choose a preset to compare.")

    def render_preset(self, tone, side):
        self.submit("render", f"Replaying the phrase through {tone.name}…", preset_library.render_reference,
                    tone.preset, self.take.folder, scene=tone.scene,
                    done=lambda result: self.rendered_preset(tone, side, result))

    def rendered_preset(self, tone, side, captured):
        sound = listening.captured_sound(captured, self.take)
        if side == "a":
            self.tone_a, self.baseline = tone.with_capture(sound), sound
        else:
            self.tone_b, self.alternative = tone.with_capture(sound), sound
            self.audition_values = {}
        self.last_heard = None
        self.remember()
        self.set_status(f"{sound.name} is ready on {side.upper()}.")

    def restore_draft_comparison(self):
        if not self.draft or self.job is not None and not self.task_if_active("monitor"):
            self.set_status("Prepare a draft first.")
            return
        self.set_tone(Tone.from_draft(self.draft))
        self.set_status(f"{self.draft['name']} is selected as B.")

    def show_render(self, folder, side, values):
        original, shaped = listening.comparison(folder, self.take, required=True)
        if side == "a":
            self.tone_a, self.baseline = self.tone_a.with_capture(shaped), shaped
        else:
            if self.tone_a.kind == "original" and not (self.baseline and self.baseline.paired_with(shaped)):
                self.baseline = original
            self.alternative = shaped
            self.audition_values = values
            if self.tone_b:
                self.tone_b = self.tone_b.with_capture(shaped)
        self.last_heard = None
        self.remember()
        self.set_status(f"Captured {side.upper()} is ready in Details.")

    def set_tone(self, tone, *, side="b", select=True, shape=None):
        self.stop_audio()
        self.last_heard = None
        if side == "a":
            self.tone_a = tone or Tone.original()
        else:
            self.tone_b = tone
        self.shapes[side] = dict(shape or {})
        self.tone_revision += 1
        self.tone_revisions[side] = self.tone_revision
        self.level_feedback.pop(side, None)
        if select and self.selectable(side):
            self.side = side
        self.resolve_audio()
        if side == self.side:
            self.available = None
            self.base_bypassed = False
            if self.selectable(side):
                self.update_live_tone()
        else:
            self.send_levels()
        self.remember()
        self.update()

    def tone_draft(self, side=None):
        tone = self.tone(side)
        return self.drafts.get(tone.draft_id) if tone else None

    def install_draft(self, draft, *, shape=None):
        self.draft = self.drafts[draft["id"]] = draft
        self.set_tone(Tone.from_draft(draft), shape=shape)

    def expand_tone(self):
        current = self.tone_draft()
        if current is None or self.job is not None and not self.task_if_active("monitor"):
            return
        try:
            expanded = preset_design.variant_with_shaping(current)
        except (ValueError, RuntimeError, OSError) as error:
            self.set_status(str(error))
            return
        self.install_draft(expanded, shape=self.values)
        self.set_status(f"{expanded['name']} is on B with more shaping controls.")

    def use_as_tone(self, preset, scene=None, *, side="b"):
        self.set_tone(Tone.from_preset(preset, scene), side=side)
        self.set_status(f"{preset.name} is selected as {side.upper()}. Choose Guitar or ▶ Phrase to hear it.")
        self.show_tab("Shape")

    def use_asset(self, asset, *, capture_has_cab=None, capture_version=None):
        base = (self.drafts[self.tone_b.draft_id] if self.tone_b and self.tone_b.draft_id in self.drafts else None)
        if base is None:
            base = {"id": None, "name": "Clean platform", "target": "An audition of your Quad Cortex asset",
                    "guitar": self.guitar.get(), "explanation": "A clean amp with minimal processing.",
                    "chain": [{"model_id": 1084, "settings": []}, {"model_id": 12022, "settings": []}], "sources": []}
        try:
            draft = preset_design.variant_with_asset(base, asset, capture_has_cab=capture_has_cab,
                                                     capture_version=capture_version)
        except (ValueError, RuntimeError, OSError) as error:
            self.set_status(str(error))
            return
        self.install_draft(draft)
        self.set_status(f"{asset.name} is in the new B tone.")
        self.show_tab("Shape")

    def choose_wave(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose a guitar reference excerpt",
                                          filetypes=(("WAV audio", "*.wav"),))
        if path:
            self.reference = listening.Sound(Path(path).stem, Path(path))
            self.reference_choice = None
            self.remember()
            self.set_status("This reference has its own performance. Play it separately to hear what you're aiming for.")
            self.update()

    def clear_reference(self):
        if self.playing == self.reference:
            self.stop_audio()
        self.last_heard = None
        self.reference = self.reference_choice = None
        self.remember()
        self.update()

    def match(self):
        if not self.reference or not self.take:
            self.set_status("Choose a reference and a recorded phrase first.")
            return
        self.submit("match", "Looking for a closer sound among your saved comparisons…",
                    listening.closest, self.reference, self.take, done=self.matched)

    def matched(self, candidate):
        self.tone_a = Tone.from_sound(self.reference) if self.reference.paired_with(candidate) else Tone.original()
        self.set_tone(Tone.from_sound(candidate))
        self.set_status("Closest saved sound is B. Listen to decide whether it fits.")

    def find_presets(self, query):
        self.preset_query = query
        if self.presets is None and self.browser.library:
            self.presets = self.browser.library.presets
        if query and self.qc_name is None and self.use_available_reference(query):
            return
        if self.presets is not None:
            self.choose_preset(query)
        else:
            self.submit("browse", "Reading your Quad Cortex presets…", preset_library.inventory,
                        done=lambda result: self.preset_inventory(result, query))

    def use_available_reference(self, query):
        matches = {}
        for sound, source in listening.reference_history():
            if source["name"].casefold() == query.casefold():
                address = source["setlist"], source["slot"]
                same_phrase = self.baseline and self.baseline.paired_with(sound)
                if address not in matches or same_phrase and not self.baseline.paired_with(matches[address]):
                    matches[address] = sound
        if len(matches) != 1:
            return False
        self.use_captured(next(iter(matches.values())))
        return True

    def list_references(self):
        choices = self.reference_history
        choices.delete(0, tk.END)
        count = 0
        for sound, source in listening.reference_history():
            choices.add_command(label=f"{sound.name} · {source['setlist']} {source['slot']}",
                                command=lambda ref=sound: self.use_captured(ref))
            count += 1
            if count == 12:
                break
        if not count:
            choices.add_command(label="No previously captured presets", state="disabled")

    def use_captured(self, sound):
        self.reference = sound
        self.reference_choice = None
        self.set_status(f"{sound.name} is ready on the reference player.")
        self.remember()
        self.update()

    def preset_inventory(self, rows, query):
        self.presets = rows
        self.choose_preset(query)

    def choose_preset(self, query):
        exact = [row for row in self.presets if row.name.casefold() == query.casefold()]
        if len(exact) == 1:
            self.use_preset(exact[0])
            return
        pick = tk.Toplevel(self.root, bg=PAPER, padx=18, pady=15)
        pick.title("Choose a Quad Cortex sound")
        pick.geometry("510x400")
        pick.transient(self.root)
        pick.grab_set()
        text(pick, "Choose a Quad Cortex sound", size=16, bold=True).pack(anchor="w", pady=(0, 8))
        search = tk.StringVar(value=query)
        field = tk.Entry(pick, textvariable=search, font=(FONT, 12))
        field.pack(fill="x", ipady=5)
        matches = tk.Listbox(pick, font=(FONT, 11), bg=WHITE, fg=INK, borderwidth=0,
                             selectbackground=ACCENT, selectforeground=INK, exportselection=False)
        matches.pack(fill="both", expand=True, pady=7)
        filtered = []

        def show(*_):
            terms = search.get().casefold().split()
            filtered[:] = [row for row in self.presets if all(
                term in f"{row.name} {row.setlist} {row.slot}".casefold() for term in terms)]
            matches.delete(0, tk.END)
            for row in filtered:
                matches.insert(tk.END, f"{row.name}   ·   {row.setlist} {row.slot}")
            if filtered:
                matches.selection_set(0)

        def select(reference):
            if not matches.curselection():
                return
            chosen = filtered[matches.curselection()[0]]
            pick.destroy()
            if reference:
                self.use_preset(chosen)
            else:
                self.submit("load", f"Loading {chosen.name} on your Quad Cortex…", preset_library.load_preset,
                            chosen, done=self.loaded_preset)

        show()
        search.trace_add("write", show)
        actions = tk.Frame(pick, bg=PAPER)
        actions.pack(fill="x")
        control(actions, "Use as reference", lambda: select(True), primary=True).pack(side="left")
        control(actions, "Play on Quad Cortex", lambda: select(False)).pack(side="left", padx=6)
        pick.bind("<Escape>", lambda _: pick.destroy())
        pick.bind("<Return>", lambda _: select(True))
        field.focus_set()

    def loaded_preset(self, choice):
        self.qc_name = choice.name
        self.qc_position, self.qc_scene = choice.position, None
        self.hardware.set(f"QC last seen · {choice.name}")
        self.set_status(f"{choice.name} is ready to play on the Quad Cortex.")
        self.check_qc()

    def use_preset(self, choice, scene=None):
        self.reference_choice = choice
        if not self.take:
            self.set_status(f"Record a phrase to hear {choice.name} against your current sound.")
            return
        self.submit("reference", f"Replaying your phrase through {choice.name}…",
                    preset_library.render_reference, choice, self.take.folder, scene=scene, done=self.captured_reference)

    def captured_reference(self, reference):
        self.reference = listening.captured_sound(reference, self.take)
        self.last_heard = None
        self.remember()
        self.set_status("Reference ready. Your playing preset and scene were restored.")

    def recording(self):
        if self.task_if_active("record"):
            self.stop()
            return
        if self.job is not None:
            return
        self.ready.clear()
        self.stop_signal = Event()
        self.live_is_ready = False
        self.submit("record", "Connecting… wait for Recording before playing.",
                    capture.record_take, self.input_channel, self.guitar.get(), self.stop_signal, self.ready,
                    done=self.recorded)
        self.record.configure(text="■ Rec")

    def task_if_active(self, name):
        return self.job is not None and self.task == name

    def recorded(self, result):
        folder, message = result
        self.takes = listening.takes()
        kept = next((take for take in self.takes if take.folder == folder), None)
        if kept:
            self.select_take(kept)
        self.set_status(message)
        if kept and self.reference_choice:
            self.set_status(f"{message}. Set the reference again to compare it on this phrase.")

    def ask(self):
        query = self.request.get().strip()
        if not query or self.thinking is not None:
            return
        current = self.tone_draft()
        action, value = listening.route(query, has_draft=current is not None)
        if styles.intent(query):
            action = "adjust-live"
        if action in ("reference", "match") and self.job is not None:
            self.set_status("Finish or stop the current hardware action, then try that request again.")
            return
        self.request.set("")
        if action == "stop":
            self.stop()
            return
        if action == "keep":
            self.keep(note=value)
            return
        if action == "reference":
            self.find_presets(value)
            return
        if action == "compare":
            if self.task_if_active("monitor"):
                self.select_side("a" if self.side == "b" else "b")
            else:
                self.audition_shape()
            return
        if action == "draft":
            self.restore_draft_comparison()
            return
        if action == "match":
            self.match()
            return
        take = dict(self.take.info) if self.take and self.take.info.get("guitar") == self.guitar.get() else {}
        take["guitar"] = self.guitar.get()
        if self.qc_name:
            take["wet_preset"] = self.qc_name
        self.sent = query
        self.intent = action
        self.intent_side = self.side
        if action in ("create", "refine"):
            prior = current if action == "refine" else None
            prompt_context = f"Selected listening reference: {self.reference.name}" if self.reference else ""
            if self.kept_note and self.alternative and self.alternative.path == self.kept:
                prompt_context += f"\nPlayer's listening note on this version: {self.kept_note}"
            self.set_status("Preparing a revision…" if prior else "Researching and preparing a new sound…")
            self.thinking = self.advisor.submit(preset_design.design, value, take["guitar"], take, prior, prompt_context)
        else:
            self.set_status("Working out how to change your current sound…")
            direction = styles.intent(value)
            if direction:
                key, delta = {"more jazz": ("jazz", .4), "darker": ("warmth", .35), "less delay": ("delay", -.3)}[direction]
                current = self.values.get(key, 0)
                next_value = max(styles.KNOBS[key][1], min(1, current + delta))
                self.thinking = self.advisor.submit(lambda: (f"Try {styles.KNOBS[key][0].lower()} at {next_value:.0%}.",
                                                             {key: next_value}))
            else:
                available = None if self.available is None else frozenset(self.available)
                self.thinking = self.advisor.submit(advisor.ask, value, take, self.values, available=available)
        self.update()

    def answered(self, result):
        if self.intent in ("create", "refine"):
            if self.job is not None and not self.task_if_active("monitor"):
                self.draft = self.drafts[result["id"]] = result
                self.set_status(f"{result['name']} is ready in Details.")
                self.remember()
                return
            self.install_draft(result)
            self.set_status(f"{result['name']} · {result['target']}")
        else:
            reply, knobs = result
            if knobs:
                self.set_shape({**self.shapes[self.intent_side], **knobs}, side=self.intent_side)
            self.set_status(reply)
        self.remember()

    def list_details(self):
        self.details.delete(0, tk.END)
        for side, audio in (("a", self.baseline), ("b", self.alternative)):
            if audio:
                self.details.add_command(label=f"▶  Captured {side.upper()} · {audio.name}",
                                         command=lambda choice=side: self.listen(choice),
                                         state="normal" if self.job is None else "disabled")
        for side in ("a", "b"):
            if self.take and self.selectable(side):
                self.details.add_command(label=f"Capture {side.upper()} for playback",
                                         command=lambda choice=side: self.capture_tone(choice),
                                         state="normal" if self.job is None else "disabled")
        self.details.add_separator()
        if self.tone_b:
            self.details.add_command(label=f"Pin {self.tone_b.name} as A", command=self.pin_baseline)
        if self.take:
            self.details.add_command(label="Restore the phrase's original tone on A", command=self.original_on_a)
        current = self.tone_draft()
        if self.draft and (not self.tone_b or self.tone_b.draft_id != self.draft["id"]):
            self.details.add_command(label=f"Use {self.draft['name']} as B", command=self.restore_draft_comparison)
        if current:
            recipe = current
            self.details.add_command(label="Why this sound?", command=lambda: self.set_status(recipe["explanation"]))
            self.details.add_command(label="Show signal chain", command=lambda: self.set_status(preset_design.chain_text(recipe)))
            for source in recipe["sources"]:
                self.details.add_command(label=source["title"][:70], command=lambda url=source["url"]: webbrowser.open(url))
            self.details.add_separator()
            saved = self.read_state(session.SESSIONS / "drafts" / f"{recipe['id']}.kept.json").get("label")
            if saved:
                self.details.add_command(label=f"Saved on QC · {saved}", state="disabled")
            else:
                self.details.add_command(label="Save selected tone to QC", command=self.save)
        if self.reference:
            self.details.add_command(label="Put reference on A", command=self.reference_on_a)
        if self.reference_choice and self.take:
            for scene_index, scene in enumerate(device.SCENES):
                self.details.add_command(label=f"Capture reference scene {scene}",
                                         command=lambda index=scene_index: self.use_preset(self.reference_choice, index))
        if not current and not self.reference_choice and not self.tone_b and not self.take:
            self.details.add_command(label="Choose a sound to see its details", state="disabled")

    def reference_on_a(self):
        if self.job is not None or not self.reference:
            return
        self.stop_audio()
        self.set_tone(Tone.from_sound(self.reference), side="a", select=False)

    def pin_baseline(self):
        if not self.tone_b or self.job is not None and not self.task_if_active("monitor"):
            return
        self.set_tone(self.tone_b, side="a", select=False, shape=self.shapes["b"])
        self.set_status(f"{self.tone_b.name} pinned as A.")

    def original_on_a(self):
        if self.job is None:
            self.stop_audio()
            self.tone_a = Tone.original()
            self.shapes["a"] = {}
            self.bind_recorded_tone()
            self.resolve_audio()
            self.remember()
            self.update()

    def keep(self, *, note=""):
        sound = self.selected_sound()
        if self.job is not None or not sound:
            self.set_status("Play the starting sound or the alternative first, then keep the one you want.")
            return
        try:
            listening.keep(sound, self.take, self.target.get(), self.baseline, note=note)
        except OSError as error:
            self.set_status(str(error))
            return
        self.kept = sound.path
        self.kept_note = note
        self.remember()
        self.set_status(f"Kept {sound.name} in this listening session. Press Esc to return to playing.")
        self.update()

    def save(self):
        tone = self.shape_tone()
        side = self.side
        if tone and tone.kind == "draft":
            draft = self.drafts[tone.draft_id]
            proof = self.alternative.folder if (side == "b" and self.alternative and self.alternative.draft_id == draft["id"]
                                                  and not self.shape_dirty()) else None
            self.submit("save", "Saving the tone in the Tone Lab setlist…", draft_render.save, draft, proof,
                        physical_input=self.input_channel, values=dict(self.values),
                        done=lambda location: self.saved(location, draft, side))
        elif self.values:
            self.submit("save", "Saving this shape in the Tone Lab setlist…", monitor.save_variant,
                        self.monitor_config("guitar"), done=lambda result: self.saved_variant(result, side))
        elif tone and tone.preset:
            self.set_status(f"Saved on the Quad Cortex · {tone.preset.setlist} {tone.preset.slot} · {tone.name}")

    def saved(self, location, draft, side):
        tone = Tone.from_draft(draft)
        if tone.preset:
            proof = self.baseline if side == "a" else self.alternative
            self.set_tone(tone.with_capture(proof), side=side)
        self.browser.changed()
        self.presets = None
        self.set_status(f"Saved · {location}")

    def saved_variant(self, result, side):
        preset, scene, fingerprint = result
        tone = Tone.from_preset(preset, scene, fingerprint=fingerprint)
        sound = self.baseline if side == "a" else self.alternative
        if sound and self.shapes[side] != self.rendered_values(sound):
            sound = None
        self.set_tone(tone.with_capture(sound), side=side)
        self.browser.changed()
        self.set_status(f"Saved · {preset.setlist} {preset.slot} · {preset.name}")

    def monitor_config(self, source, *, side=None):
        side = side or self.side
        tone = self.shape_tone(side)
        config = {"take": str(self.take.folder) if self.take else None, "mode": source,
                  "tone_id": side, "tone_revision": self.tone_revisions[side],
                  "physical_input": self.input_channel, "values": dict(self.shapes[side]),
                  "name": tone.name if tone else self.qc_name or "Current Quad Cortex"}
        if tone and tone.preset:
            config["preset"] = {"position": tone.preset.position, "name": tone.preset.name}
            config["scene"] = tone.scene
        elif tone and tone.kind == "draft":
            config["draft"] = self.drafts[tone.draft_id]
            proof = self.baseline if side == "a" else self.alternative
            if proof and proof.draft_id == tone.draft_id and proof.folder:
                config["gain"] = self.read_state(proof.folder / "session.json").get("output_gain_db")
        return config

    def start_source(self, source):
        if source == "phrase" and not self.take:
            self.set_status("Choose or record a phrase first.")
            return
        if self.task_if_active("monitor") and not self.stop_signal.is_set():
            self.requested_source = source
            self.control_mail.put({"event": "source", "mode": source,
                                   "take": str(self.take.folder) if self.take else None})
            self.output.set("Switching input…")
            return
        if self.job is not None:
            return
        if not self.selectable(self.side):
            self.set_status("Choose a Quad Cortex tone on A or B before starting the input.")
            return
        self.stop_signal = Event()
        self.control_mail = Queue()
        self.live_messages = Queue()
        self.monitor_controls.clear()
        self.level_feedback.clear()
        self.tone_requests.clear()
        self.confirmed_tones = {side: self.tone_snapshot(side) for side in ("a", "b")}
        self.requested_source = source
        config = self.monitor_config(source)
        config["guitar_config"] = self.setup.live_config()
        config["level"] = self.level_config(source)
        self.active_monitor_name = config["name"]
        self.submit("monitor", f"Preparing {config['name']} on the Quad Cortex…", live_client.run_monitor,
                    config, self.stop_signal, self.control_mail, self.live_messages.put,
                    done=self.restored)

    def restored(self, _):
        self.set_status("Quad Cortex returned to the playing preset.")
        self.check_qc()

    @staticmethod
    def read_qc():
        with device.connect() as qc:
            position = device.live_position(qc)
            name = device.read_preset(qc).name
            scene = device.SCENES[device.active_scene(qc)]
            if device.live_position(qc) != position:
                raise RuntimeError("The Quad Cortex changed while reading its current tone.")
            return name, scene, position

    def defer_read(self):
        if self.focus_timer is not None:
            self.root.after_cancel(self.focus_timer)
        self.focus_timer = self.root.after(650, self.check_qc)

    def check_qc(self):
        self.focus_timer = None
        if self.job is not None or self.leaving is not None or self.check is not None:
            return
        self.check = self.worker.submit(self.read_qc)

    def open_editor(self):
        if self.job is not None or self.check is not None:
            self.editor_after = True
            if self.task_if_active("monitor"):
                self.stop()
            self.set_status("Opening Cortex Control when the Quad Cortex handoff is complete…")
            return
        try:
            desktop.open_cortex_control()
        except (RuntimeError, OSError) as error:
            self.set_status(str(error))

    def submit(self, task, message, work, *args, done, **kwargs):
        if self.job is not None or self.leaving is not None:
            return
        self.stop_audio()
        self.task = task
        self.outcome = done
        self.set_status(message)
        self.job = self.worker.submit(work, *args, **kwargs)
        self.update()

    def tick(self):
        if self.check is not None and self.check.done():
            try:
                self.qc_name, self.qc_scene, self.qc_position = self.check.result()
                if not self.task_if_active("monitor"):
                    self.hardware.set(f"QC last seen · {self.qc_name} · {self.qc_scene}")
                    original = self.tone_a
                    self.bind_recorded_tone()
                    if self.tone_a is not original:
                        self.resolve_audio()
                        self.remember()
                        self.update()
            except device.EditorActive:
                self.qc_name = self.qc_scene = self.qc_position = None
                self.hardware.set("Cortex Control open · device controls paused")
            except Exception:
                self.qc_name = self.qc_scene = self.qc_position = None
                self.hardware.set("Quad Cortex · connection unchecked")
            self.check = None
            if self.editor_after and self.job is None and self.leaving is None:
                self.editor_after = False
                self.open_editor()
        if self.play_job is not None and self.play_job.done():
            try:
                if self.play_job.result() and not self.play_cancel.is_set():
                    self.play_group = self.pending_pair
                    self.started_playing(self.pending_sound)
            except Exception as error:
                diagnostics.LOG.exception("Listening playback failed")
                self.set_status(str(error))
                self.output.set("Playback unavailable")
            self.play_job = None
            self.update()
        if self.job is not None and self.job.done():
            past, self.job = self.job, None
            task = self.task
            self.stop_signal = None
            self.record.configure(text="● Rec")
            if task == "monitor":
                self.available = self.active_source = self.requested_source = self.active_side = None
                self.active_revision = None
                self.phrase_progress = None
                self.base_bypassed = False
                self.setup.live_event({"event": "guitar-stopped"})
                self.output.set("Stopped")
            try:
                self.outcome(past.result())
            except device.EditorActive as error:
                if task != "browse" or not self.preset_query or not self.use_available_reference(self.preset_query):
                    self.set_status(str(error))
                self.qc_name = self.qc_scene = self.qc_position = None
                self.hardware.set("Cortex Control open · device controls paused")
            except Exception as error:
                diagnostics.LOG.exception("Listening desk operation failed")
                self.set_status(str(error))
                if task in ("monitor", "render", "reference", "save", "load"):
                    self.hardware.set("Quad Cortex · checking current sound")
                    self.check_qc()
            self.update()
            if self.editor_after and self.leaving is None:
                self.editor_after = False
                self.open_editor()
        elif self.task_if_active("record") and self.ready.is_set() and not self.stop_signal.is_set() and not self.live_is_ready:
            self.set_status("Recording your guitar · play the phrase, then stop.")
            self.live_is_ready = True
        try:
            while True:
                message = self.live_messages.get_nowait()
                if not self.task_if_active("monitor") or self.stop_signal.is_set():
                    continue
                self.monitor_event(message)
        except Empty:
            pass
        if self.thinking is not None and self.thinking.done():
            try:
                self.answered(self.thinking.result())
            except Exception as error:
                diagnostics.LOG.exception("Listening advisor failed")
                self.set_status(str(error))
                if not self.request.get():
                    self.request.set(self.sent)
            self.thinking = None
            self.update()
        loop = self.player.loop
        if self.playing and (self.player.failures > 6 or self.player.stream and not self.player.stream.active):
            self.stop_audio()
            self.set_status("Windows playback stalled. Check the output device and start the comparison again.")
            self.update()
        width = self.playhead.winfo_width()
        if self.phrase_progress and self.active_source == "phrase" and self.stop_signal and not self.stop_signal.is_set():
            stamped, frame, length = self.phrase_progress
            cursor = width * ((frame + (monotonic() - stamped) * RATE) % length) / length
        else:
            cursor = width * loop.cursor / len(loop.tracks[0]) if loop and self.playing else 0
        self.playhead.coords(self.position, 0, 0, cursor, 4)
        if self.leaving is not None and self.job is None and self.play_job is None and self.check is None:
            self.finish_close(self.leaving)
            return
        self.root.after(65, self.tick)

    def monitor_event(self, message):
        if isinstance(message, str):
            self.set_status(message)
            return
        event = message["event"]
        side = message.get("tone_id")
        reason = message.get("reason")
        if event == "controls":
            self.monitor_controls[side] = set(message["available"])
        elif event == "level":
            self.level_feedback[side] = message
            self.sync_volume()
        elif event == "loading-tone" and side == self.side:
            self.output.set(f"Loading {message['name']} on the Quad Cortex…")
        elif event == "tone":
            self.active_side = side
            self.active_revision = message.get("tone_revision")
            self.active_monitor_name = message["name"]
            self.resolve_monitor_tone(message)
            if self.active_side == self.side and self.active_revision in (None, self.tone_revisions[self.side]):
                self.available = self.monitor_controls.get(side)
                self.show_monitor_output()
                self.set_status(reason or "")
            self.update()
        elif event == "source":
            if self.active_source != message["mode"]:
                self.phrase_progress = None
            self.active_source = message["mode"]
            if reason:
                self.requested_source = self.active_source
            self.show_monitor_output()
            if reason:
                self.set_status(reason)
            self.update()
        elif event == "heartbeat":
            audio = message["audio"]
            if self.active_source == "phrase" and audio.get("loop_frames"):
                self.phrase_progress = (monotonic(), audio["cursor"], audio["loop_frames"])
        elif event == "shape" and side == self.side:
            self.set_status(reason or "")
        elif event.startswith("guitar-"):
            self.setup.live_event(message)
            if event == "guitar-gesture" and message.get("applied"):
                side = side or self.active_side or self.side
                if side == self.side:
                    self.base_bypassed = False
                self.shapes[side][message["control"]] = message["value"]
                self.write_shape()
                self.show_monitor_output()
                self.update()

    def resolve_monitor_tone(self, message):
        side, revision = message["tone_id"], message.get("tone_revision")
        reason = message.get("reason")
        if not reason:
            snapshot = self.tone_requests.pop(revision, None)
            if revision == self.tone_revisions[side]:
                snapshot = self.tone_snapshot(side)
            if snapshot:
                self.confirmed_tones[side] = snapshot
            return
        attempted = message.get("requested_revision")
        self.tone_requests.pop(attempted, None)
        if attempted is not None and self.tone_revisions[self.side] != attempted:
            return
        if side == self.side and (previous := self.confirmed_tones.get(side)) and previous[2] == revision:
            tone, shape, version = previous
            if side == "a":
                self.tone_a = tone
            else:
                self.tone_b = tone
            self.shapes[side] = shape
            self.tone_revisions[side] = version
            self.resolve_audio()
            self.send_levels()
        self.side = side
        self.base_bypassed = False
        self.remember()

    def close(self, destination):
        self.leaving = destination or "exit"
        self.stop()
        if self.job is None and self.play_job is None and self.check is None:
            self.finish_close(self.leaving)

    def finish_close(self, destination):
        if getattr(self, "shape_writer", None) is not None:
            self.root.after_cancel(self.shape_writer)
            self.shape_writer = None
        self.remember()
        self.setup.shutdown()
        self.worker.shutdown(wait=True)
        self.advisor.shutdown(wait=False, cancel_futures=True)
        self.leave(destination)
