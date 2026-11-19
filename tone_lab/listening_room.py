"""A session organized around a guitarist's next listening decision."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from time import monotonic
import json
import math
import random
import re
import tkinter as tk
from tkinter import filedialog
import webbrowser

from . import advisor, capture, desktop, device, diagnostics, draft_render, listening, live_client, lore, monitor, preset_design, preset_library, qc_library, session, styles, usage
from .audio import RATE
from .knob_panel import KnobPanel
from . import tone_space
from .tone_identity import Tone
from .tone_space_ui import ToneSpacePanel
from .volume_ui import VolumePanel, listening_command, saved_settings


from . import theme
from .desk_panels import ChainPanel, DifferencePanel, HistoryPanel, PhrasePanel, Tray
from .theme import ACCENT, ACCENT_INK, DARK, DIM, FONT, INK, LIGHT, PAPER, RULE, SOFT, SUBTLE, TILE, WHITE

# The panels a player can call beside the tones, with the bare key that opens each.
PANELS = (("Shape", "S"), ("Difference", "D"), ("Chain", "C"), ("History", "H"), ("Sounds", "F"))
QUIET_OUTPUT = ("", "Stopped", "Playback paused")
QUESTION = re.compile(r"(?i)^(?:why|what|how|which|can i|does|is)\b")


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


def popup(parent):
    return tk.Menu(parent, tearoff=False, bg=WHITE, fg=LIGHT, activebackground=RULE, activeforeground=LIGHT)


def measured(chosen, other, reference, knobs):
    """What the captured audio says about the selected tone, in a form the assistant can use."""
    if chosen is None or (other is None and reference is None):
        return None
    try:
        own = listening.samples(chosen)
        facts = {"captured_with_knobs": knobs, "bands_hz": list(tone_space.BAND_NAMES)}
        for label, sound in (("reference", reference), ("other_tone", other)):
            if sound is None or sound == chosen:
                continue
            gap = tone_space.difference(listening.samples(sound), own)
            facts[f"selected_minus_{label}"] = {
                "spectrum_db": [round(value, 1) for value in gap.spectrum_db],
                **{f"{name.lower().replace(' ', '_')}_{unit}": round(value, 2)
                   for name, value, unit in gap.perceptual_delta},
                "same_performance": chosen.paired_with(sound)}
        return facts if len(facts) > 2 else None
    except (OSError, ValueError, RuntimeError):
        diagnostics.LOG.info("The captured tone could not be measured for the assistant", exc_info=True)
        return None


def consult(text, take, shape, available, sounds, cancel, gear=()):
    """Runs off the main thread: measure what was captured, gather what is known about the chain, then ask."""
    return advisor.read(text, take, shape, available=available, heard=measured(*sounds), cancel=cancel,
                        knowledge=lore.brief(gear, text) or None)


def compose(text, guitar, take, prior, context, sounds, cancel):
    facts = measured(*sounds) if prior else None
    if facts:
        context += "\nMeasured on the player's own phrase (selected tone minus the other sound): " + json.dumps(facts)
    return preset_design.design(text, guitar, take, prior, context, cancel=cancel)


class ToneTile(tk.Frame):
    """One tone as a field of its own colour. A border means selected. A fill means it is sounding."""

    def __init__(self, parent, side, title, caption, command, *, color, compact=False):
        super().__init__(parent, bg=DARK, padx=16, pady=13, highlightthickness=2, highlightbackground=DARK,
                         cursor="hand2", takefocus=True)
        self.color, self.command, self.enabled = color, command, True
        self.fill = DARK
        self.configure(width=150 if compact else 190, height=150)
        self.pack_propagate(False)
        self.air = tk.Label(self, text="", bg=DARK, fg=theme.LIVE, font=(FONT, 8, "bold"), anchor="e")
        self.air.pack(side="top", fill="x")
        self.hint = tk.Label(self, textvariable=caption, bg=DARK, fg=SUBTLE, font=(FONT, 9), justify="left",
                             anchor="w", wraplength=114 if compact else 320)
        self.hint.pack(side="bottom", fill="x")
        self.name = tk.Label(self, textvariable=title, bg=DARK, fg=INK, font=(FONT, 11 if compact else 14, "bold"),
                             justify="left", anchor="w", wraplength=114 if compact else 320)
        self.name.pack(side="bottom", fill="x")
        self.letter, self.masked = side, False
        self.badge = tk.Label(self, text=side, bg=DARK, fg=color, font=(FONT, 22 if compact else 34, "bold"),
                              anchor="w")
        self.badge.pack(side="bottom", fill="x")
        self.parts = (self, self.air, self.hint, self.name, self.badge)
        for widget in self.parts:
            widget.configure(cursor="hand2")
            widget.bind("<ButtonRelease-1>", self.press)
        self.bind("<Return>", self.press)
        if not compact:
            self.bind("<Configure>", lambda event: [label.configure(wraplength=max(90, event.width - 40))
                                                    for label in (self.name, self.hint)])

    def press(self, _=None):
        if self.enabled:
            self.command()

    def on_menu(self, show):
        for widget in self.parts:
            widget.bind("<Button-3>", show)

    def sync(self, *, selected, enabled, on_air=False, note="", mask=False):
        self.enabled = enabled
        color = SOFT if mask else self.color  # A hidden tone gives nothing away, not even its colour.
        if mask != self.masked:
            self.masked = mask
            self.badge.configure(text="?" if mask else self.letter, fg=color)
        fill = theme.mix(color, DARK, .17) if on_air else DARK
        self.configure(highlightbackground=color if selected or on_air else DARK,
                       highlightcolor=color if selected or on_air else RULE)
        self.air.configure(text=note)
        if fill != self.fill:
            self.fill = fill
            for widget in self.parts:
                widget.configure(bg=fill)


class Desk:
    def __init__(self, root, input_channel, leave):
        self.root, self.input_channel, self.leave = root, input_channel, leave
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tone-audio")
        self.advisor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tone-advice")
        self.player = listening.Player()
        self.guitar_learner = None
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
        self.kept_live = None
        self.undo, self.journal, self.abandoned = [], [], []
        self.asked = self.followup = self.problem = self.level_problem = self.line_timer = None
        self.static_cache, self.chain_seen = {}, set()
        self.blind = self.blind_panels = self.cancel_signal = self.return_peak = None
        self.offered_layouts = set()
        self.live_is_ready = False
        self.task = None
        self.qc_name = self.qc_position = self.qc_scene = None
        self.state_file = session.SESSIONS / "desk.json"
        self.state = self.read_state(self.state_file)
        self.volume_settings = saved_settings(self.state.get("volume"))
        self.touched = [key for key in self.state.get("touched", ()) if key in styles.KNOBS][:8]
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
        self.r_name, self.r_context, self.air = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.build()
        selected = next((take for take in self.takes if take.folder.name == self.state.get("take")), None)
        self.select_take(selected or (self.takes[0] if self.takes else None), remember=False)
        self.restore_context()
        self.tray.restore(self.state.get("pinned", ()))
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
        root.minsize(980, 620)
        root.geometry(f"{min(1120, max(980, root.winfo_screenwidth() - 80))}x"
                      f"{min(720, max(620, root.winfo_screenheight() - 85))}")
        root.protocol("WM_DELETE_WINDOW", lambda: self.close(None))
        root.bind("<Escape>", lambda _: self.stop())
        root.bind("<Control-k>", lambda _: self.focus_request())
        root.bind("<Control-z>", lambda _: None if self.typing() else self.undo_last())
        root.bind("<Control-1>", lambda _: self.select_side("a"))
        root.bind("<Control-2>", lambda _: self.select_side("b"))
        root.bind("<Control-g>", lambda _: self.start_source("guitar"))
        root.bind("<Control-l>", lambda _: self.start_source("phrase"))
        root.bind("<Control-0>", lambda _: self.toggle_base())
        root.bind("<KeyPress>", self.key)

        page = tk.Frame(root, bg=PAPER, padx=22, pady=14)
        page.pack(fill="both", expand=True)

        # What is on air, and the way to everything that is not on the desk.
        top = tk.Frame(page, bg=PAPER)
        top.pack(fill="x")
        self.dot = tk.Canvas(top, width=16, height=16, bg=PAPER, highlightthickness=0)
        self.dot.pack(side="left")
        self.dot_halo = self.dot.create_oval(1, 1, 15, 15, fill=PAPER, outline="")
        self.dot_mark = self.dot.create_oval(4, 4, 12, 12, fill=DIM, outline="")
        theme.Tooltip(self.dot, lambda: self.hardware.get())
        self.air_label = text(top, variable=self.air, size=10, color=SOFT)
        self.air_label.pack(side="left", padx=(8, 0))
        self.air_link = theme.link(top, "", lambda: None)
        self.air_action = None
        self.more_button = theme.IconButton(top, "More", self.post_more,
                                            tip="Phrases, reference, levels, setup")
        self.more_button.pack(side="right")
        self.rail = {}
        for name, key in reversed(PANELS):
            self.rail[name] = theme.IconButton(top, name, lambda panel=name: self.toggle_panel(panel),
                                               tip=f"{name}  ·  {key}")
            self.rail[name].pack(side="right", padx=(0, 2))

        body = tk.Frame(page, bg=PAPER)
        body.pack(fill="both", expand=True, pady=(12, 0))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        main = tk.Frame(body, bg=PAPER)
        main.grid(row=0, column=0, sticky="nsew")
        self.tray = Tray(body, self)

        # Two tones. A third field appears only while there is a reference to hear.
        pair = tk.Frame(main, bg=PAPER)
        pair.pack(fill="both", expand=True)
        pair.columnconfigure((0, 1), weight=1, uniform="pair")
        pair.rowconfigure(0, weight=1)
        self.a_tile = ToneTile(pair, "A", self.a_name, self.a_context, lambda: self.pick("a"), color=theme.TONE_A)
        self.a_tile.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.b_tile = ToneTile(pair, "B", self.b_name, self.b_context, lambda: self.pick("b"), color=theme.TONE_B)
        self.b_tile.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.r_tile = ToneTile(pair, "R", self.r_name, self.r_context, lambda: self.listen("reference"),
                               color=theme.REFERENCE, compact=True)
        self.r_shown = False
        self.a_tile.on_menu(lambda event: self.tile_menu("a", event))
        self.b_tile.on_menu(lambda event: self.tile_menu("b", event))
        self.r_tile.on_menu(self.reference_menu)
        theme.Tooltip(self.a_tile.badge, "Select A  ·  A")
        theme.Tooltip(self.b_tile.badge, "Select B  ·  B")
        theme.Tooltip(self.r_tile.badge, "Hear the reference  ·  R")

        # The input. Guitar and Phrase are equally direct and never change either tone.
        row = tk.Frame(main, bg=PAPER)
        row.pack(fill="x", pady=(12, 0))
        self.live_button = control(row, "Guitar", lambda: self.toggle_source("guitar"), dark=True)
        self.live_button.pack(side="left")
        self.loop_button = control(row, "Phrase", lambda: self.toggle_source("phrase"), dark=True)
        self.loop_button.pack(side="left", padx=(2, 0))
        theme.Tooltip(self.live_button, "Play through the selected tone  ·  G\nPress again, or Space, to stop")
        theme.Tooltip(self.loop_button, "Loop the recorded phrase through the selected tone  ·  P\n"
                                        "Press again, or Space, to stop")
        self.take_menu = tk.Menubutton(row, textvariable=self.phrase, indicatoron=False, cursor="hand2",
                                       takefocus=True, font=(FONT, 9), bg=PAPER, fg=SUBTLE, padx=6, pady=6,
                                       activebackground=WHITE, activeforeground=INK)
        self.take_options = popup(self.take_menu)
        self.take_options.configure(postcommand=self.list_phrases)
        self.take_menu.configure(menu=self.take_options)
        self.take_menu.pack(side="right")
        self.save_button = control(row, "Save to QC", self.save)
        self.keep_button = control(row, "Keep", self.keep)
        self.record = control(row, "■  Stop recording", self.recording)
        self.record.configure(fg=theme.ALERT)
        theme.Tooltip(self.keep_button, "Remember that you preferred the sound you are hearing  ·  K")
        theme.Tooltip(self.save_button, "Write the selected tone to the Tone Lab setlist on the Quad Cortex")
        self.offered = ()
        self.playhead = tk.Canvas(row, background=TILE, height=3, highlightthickness=0)
        self.playhead.pack(side="left", fill="x", expand=True, padx=12)
        self.position = self.playhead.create_rectangle(0, 0, 0, 3, fill=LIGHT, width=0)

        # One line that speaks, then the one place to ask.
        self.line = theme.Line(main)
        self.line.pack(fill="x", pady=(8, 4))
        ask = tk.Frame(main, bg=DARK, highlightthickness=1, highlightbackground=RULE)
        ask.pack(fill="x")
        self.command = tk.Entry(ask, textvariable=self.request, font=(FONT, 12), bg=DARK, fg=INK, relief="flat",
                                borderwidth=0, highlightthickness=0, insertbackground=LIGHT)
        self.command.pack(side="left", fill="x", expand=True, ipady=9, padx=(12, 4))
        text(ask, "/", size=9, color=DIM).pack(side="right", padx=12)
        self.placeholder = tk.Label(self.command, text="Ask for a change, a sound or a song", bg=DARK, fg=DIM,
                                    font=(FONT, 12), cursor="xterm")
        self.placeholder.bind("<Button-1>", lambda _: self.command.focus_set())
        self.command.bind("<FocusIn>", lambda _: self.rest_request(ask, True))
        self.command.bind("<FocusOut>", lambda _: self.rest_request(ask, False))
        self.command.bind("<Return>", lambda _: self.ask())
        self.command.bind("<Escape>", lambda _: self.root.focus_set())
        self.request.trace_add("write", lambda *_: self.rest_request(ask, self.typing()))
        self.rest_request(ask, False)

        # Panels live in the tray and are built once.
        self.knobs = KnobPanel(self.tray.add("Shape", "S"), self)
        self.knobs.pack(fill="x")
        self.difference = DifferencePanel(self.tray.add("Difference", "D"), self)
        self.difference.pack(fill="x")
        self.chain = ChainPanel(self.tray.add("Chain", "C"), self)
        self.chain.pack(fill="x")
        self.history = HistoryPanel(self.tray.add("History", "H", grow=True), self)
        self.history.pack(fill="both", expand=True)
        from .qc_browser import Browser
        from .setup_ui import SetupPanel

        self.browser = Browser(self.tray.add("Sounds", "F", grow=True), self)
        self.browser.pack(fill="both", expand=True)
        self.volume = VolumePanel(self.tray.add("Levels", "L"), self.volume_settings, self.change_volume)
        self.volume.panel.pack(fill="x")
        self.phrase_panel = PhrasePanel(self.tray.add("Phrase", "T"), self)
        self.phrase_panel.pack(fill="x")

        # Setup and the tone field are rooms of their own, away from the desk.
        self.setup_window = self.window("Setup", "1040x700", lambda: self.setup.stop_preview())
        self.setup = SetupPanel(self.setup_window, self, live_observer=True)
        self.setup.pack(fill="both", expand=True, padx=18, pady=14)
        self.field_window = self.window("Tone field", "980x540", self.rest_field)
        self.space = ToneSpacePanel(self.field_window, read_values=lambda: self.values, apply_values=self.set_shape,
                                    audition=self.audition_shape,
                                    read_sounds=lambda: (self.baseline, self.alternative))
        self.space.pack(fill="both", expand=True, padx=8, pady=8)
        self.output.trace_add("write", lambda *_: self.sync_air())

    def window(self, title, size, on_hide=None):
        window = tk.Toplevel(self.root, bg=PAPER)
        window.withdraw()
        window.title(f"{title} · Tone Lab")
        window.geometry(size)

        def hide():
            if on_hide:
                on_hide()
            window.withdraw()
            usage.record("close", title, **self.situation())

        window.protocol("WM_DELETE_WINDOW", hide)
        window.bind("<Escape>", lambda _: self.stop())
        window.shown_as = title
        return window

    def show_window(self, window):
        window.deiconify()
        window.lift()
        try:
            desktop.dark_title(window)
        except (OSError, AttributeError):
            pass
        usage.record("open", window.shown_as, **self.situation())
        self.update()

    def rest_field(self):
        self.space.cancel_drag()
        self.space.stop_sweep()

    def show_setup(self):
        self.setup.guitar_input.follow_desk_guitar()
        self.show_window(self.setup_window)

    def show_guitar_calibration(self):
        self.show_setup()
        self.setup.show_calibration()

    def show_guitar_learner(self):
        from .guitar_learner_ui import GuitarLearner

        if self.guitar_learner is None:
            self.guitar_learner = GuitarLearner(self)
        self.guitar_learner.show()

    def typing(self):
        """True while the keyboard belongs to a text field."""
        try:
            focus = self.root.focus_get()
        except KeyError:
            return False
        return focus is not None and focus.winfo_class() in ("Entry", "TEntry", "Text", "TCombobox", "Spinbox")

    def rest_request(self, frame, focused):
        frame.configure(highlightbackground=ACCENT if focused else RULE)
        if focused or self.request.get():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=0, rely=.5, anchor="w")

    def focus_request(self):
        self.command.focus_set()
        return "break"

    def key(self, event):
        """Bare keys for the things done with a guitar in hand."""
        if event.state & 0x4 or event.state & 0x20000 or self.typing():
            return None
        try:
            focus = self.root.focus_get()
        except KeyError:
            focus = None
        kind = focus.winfo_class() if focus is not None else ""
        name = event.keysym.lower()
        if name == "space" and kind in ("Button", "Menubutton", "Checkbutton", "Scale", "Listbox"):
            return None
        actions = {
            "a": lambda: self.pick("a"), "b": lambda: self.pick("b"),
            "g": lambda: self.start_source("guitar"), "p": lambda: self.start_source("phrase"),
            "space": self.play_stop, "r": self.hear_reference, "z": self.undo_last, "k": self.keep,
            "0": self.toggle_base, "slash": self.focus_request, "f": self.find_sounds, "x": self.toggle_blind,
            "t": lambda: self.toggle_panel("Phrase"),
            "s": lambda: self.toggle_panel("Shape"), "d": lambda: self.toggle_panel("Difference"),
            "c": lambda: self.toggle_panel("Chain"), "h": lambda: self.toggle_panel("History"),
            "l": lambda: self.toggle_panel("Levels"),
        }
        if name not in actions:
            return None
        actions[name]()
        return "break"

    def toggle_panel(self, name):
        if self.blind:
            self.set_status("The tones are hidden. Press X to reveal them first.")
            return
        self.tray.toggle(name)

    def toggle_blind(self):
        """Hide and shuffle the two tones so the choice is made by ear alone."""
        if self.blind:
            self.reveal()
            return
        if not (self.selectable("a") and self.selectable("b")) or self.tone_b is None:
            self.set_status("A blind comparison needs two tones the Quad Cortex can play.")
            return
        if self.job is not None and not self.task_if_active("monitor"):
            return
        self.blind_panels = self.tray.put_away()
        order = random.sample(("a", "b"), 2)
        self.blind = {"a": order[0], "b": order[1]}
        self.set_status("Blind. The two tones are shuffled and hidden. A and B choose left and right, "
                        "K keeps the one you hear, X reveals.", kind="progress")
        self.update()

    def reveal(self, kept=None):
        if not self.blind:
            return
        hidden, self.blind = self.blind, None
        self.tray.bring_back(self.blind_panels)
        names = {side: (tone.name if (tone := self.tone(side)) else "Current Quad Cortex") for side in ("a", "b")}
        told = (f"Left was {hidden['a'].upper()} · {names[hidden['a']]}. "
                f"Right was {hidden['b'].upper()} · {names[hidden['b']]}.")
        if kept:
            told = f"You kept {kept.upper()} · {names[kept]}. " + told
        self.set_status(told, kind="reading")
        self.update()

    def find_sounds(self):
        self.tray.open("Sounds")
        self.browser.field.focus_set()

    def panels_changed(self):
        for name, icon in self.rail.items():
            icon.mark(open=self.tray.is_open(name), pinned=name in self.tray.pinned)
        if self.tray.is_open("Shape"):
            self.knobs.refresh()
        self.sync_panels()

    def sync_panels(self):
        """Only panels that are on screen do any work."""
        for name, panel in (("Chain", self.chain), ("Difference", self.difference), ("History", self.history),
                            ("Sounds", self.browser), ("Phrase", self.phrase_panel)):
            if self.tray.is_open(name):
                panel.sync()

    def offer_layout(self):
        """Suggest the panels this player usually has open in this situation. Never open them unasked."""
        if self.tray.order or self.blind:
            return
        where = "reference" if self.reference else "live" if self.on_air() else "idle"
        if where in self.offered_layouts:
            return
        self.offered_layouts.add(where)
        seen = [(count, views) for (situation, views), count in usage.companions().items() if situation == where]
        if not seen or max(seen)[0] < 3:
            return
        views = [name for name in max(seen)[1] if name in self.tray.cards]
        if len(views) > 1:
            self.set_status(f"You usually have {' and '.join(views)} open here.",
                            actions=(("Open them", lambda: [self.tray.pin(name, True) for name in views]),))

    def situation(self):
        return {"live": bool(self.on_air()), "source": self.active_source,
                "has_reference": self.reference is not None, "has_draft": self.tone_draft() is not None,
                "side": self.side}

    def on_air(self):
        """The side the Quad Cortex is sounding right now, if any."""
        live = self.task_if_active("monitor") and not self.stop_signal.is_set() and self.active_source
        return self.active_side if live else None

    def pick(self, side):
        """Select a tone. Captured audio, which the unit cannot shape, plays where it stands."""
        if self.blind:
            self.select_side(self.blind[side])
            if not self.task_if_active("monitor"):
                self.retire_line()
            return
        if self.selectable(side) or self.job is not None:
            self.select_side(side)
        elif (self.baseline if side == "a" else self.alternative) is not None:
            self.listen(side)
        else:
            self.select_side(side)

    def hear_reference(self):
        if self.reference is None:
            self.set_status("There is no reference yet. Choose one from the ⋯ menu.")
        else:
            self.listen("reference")

    def toggle_source(self, source):
        running = self.task_if_active("monitor") and not self.stop_signal.is_set()
        if running and self.active_source == source and self.requested_source == source:
            self.stop()
        else:
            self.start_source(source)

    def play_stop(self):
        if self.job is not None or self.playing or self.play_job:
            self.stop()
        else:
            self.start_source("phrase" if self.take else "guitar")

    def post_more(self):
        more = popup(self.root)
        phrases = popup(more)
        for take in self.takes[:14]:
            phrases.add_command(label=f"{take.name} · {take.info.get('guitar') or 'Guitar'} · {take.folder.name[14:20]}",
                                command=lambda chosen=take: self.select_take(chosen))
        if self.takes:
            phrases.add_separator()
        if self.take:
            phrases.add_command(label="Trim the loop", accelerator="T", command=lambda: self.tray.open("Phrase"))
        phrases.add_command(label="Record a new phrase", command=self.recording)
        more.add_cascade(label="Phrase", menu=phrases)
        guitars = popup(more)
        for name in session.GUITARS:
            guitars.add_radiobutton(label=name, variable=self.guitar, value=name,
                                    command=lambda value=name: self.select_guitar(value))
        more.add_cascade(label=f"Guitar · {self.guitar.get()}", menu=guitars)
        refs = popup(more)
        idle = "normal" if self.job is None else "disabled"
        refs.add_command(label="A saved Quad Cortex preset…", command=lambda: self.find_presets(""), state=idle)
        refs.add_command(label="An audio excerpt…", command=self.choose_wave, state=idle)
        self.reference_history = popup(refs)
        self.reference_history.configure(postcommand=self.list_references)
        refs.add_cascade(label="Previously captured presets", menu=self.reference_history)
        if self.reference:
            refs.add_command(label="Clear reference", command=self.clear_reference)
        more.add_cascade(label="Reference", menu=refs)
        more.add_separator()
        more.add_command(label=f"Levels · {self.volume.label}", accelerator="L",
                         command=lambda: self.tray.open("Levels"))
        more.add_command(label="Tone field", command=lambda: self.show_window(self.field_window))
        more.add_command(label="Setup and sound check", command=self.show_setup)
        more.add_command(label="Guitar Learner", command=self.show_guitar_learner)
        more.add_separator()
        more.add_command(label="Open Cortex Control", command=self.open_editor)
        more.add_command(label="Check the Quad Cortex", command=self.check_qc)
        more.tk_popup(self.more_button.winfo_rootx() - 150,
                      self.more_button.winfo_rooty() + self.more_button.winfo_height() + 2)

    def tile_menu(self, side, event):
        options = popup(self.root)
        steady = self.job is None or self.task_if_active("monitor")
        options.add_command(label="Shape this tone", command=lambda: (self.pick(side), self.tray.open("Shape")))
        options.add_command(label="Chain and reasons", command=lambda: (self.pick(side), self.tray.open("Chain")))
        if side == "b" and self.tone_b and steady:
            options.add_command(label=f"Copy {self.tone_b.name} to A", command=self.pin_baseline)
        if side == "a" and self.take and self.job is None and self.tone_a.kind != "original":
            options.add_command(label="Put the phrase's original tone on A", command=self.original_on_a)
        if self.take and self.selectable(side) and self.job is None:
            options.add_command(label=f"Capture {side.upper()} on this phrase",
                                command=lambda: self.capture_tone(side))
        options.tk_popup(event.x_root, event.y_root)

    def reference_menu(self, event):
        options = popup(self.root)
        idle = "normal" if self.job is None else "disabled"
        options.add_command(label="How far is it? Open Difference", command=lambda: self.tray.open("Difference"))
        options.add_command(label="Find the closest saved sound", command=self.match, state=idle)
        options.add_command(label="Put the reference on A", command=self.reference_on_a, state=idle)
        if self.reference_choice and self.take:
            scenes = popup(options)
            for index, scene in enumerate(device.SCENES):
                scenes.add_command(label=f"Scene {scene}", state=idle,
                                   command=lambda chosen=index: self.use_preset(self.reference_choice, chosen))
            options.add_cascade(label="Capture another scene", menu=scenes)
        options.add_separator()
        options.add_command(label="Clear reference", command=self.clear_reference)
        options.tk_popup(event.x_root, event.y_root)

    def set_status(self, value, *, kind="info", actions=(), hint=""):
        """Say one thing. Passing remarks retire by themselves. Readings and errors wait for the next action."""
        self.status.set(value)
        if self.line_timer is not None:
            self.root.after_cancel(self.line_timer)
            self.line_timer = None
        self.line.show(value, kind=kind, actions=actions, hint=hint)
        if value and kind in ("reading", "error"):
            self.journal.append(value if len(value) < 110 else value[:107] + "…")
            del self.journal[:-30]
        if value and kind == "info":
            self.line_timer = self.root.after(9000, self.retire_line)

    def retire_line(self):
        self.line_timer = None
        self.status.set("")
        self.line.clear()

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
                "volume": self.volume_settings, "touched": self.touched, "pinned": sorted(self.tray.pinned),
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

    def trim_take(self, start, end):
        """Change which part of the take loops. The recording itself is never altered."""
        take = self.take
        if take is None or self.job is not None and not self.task_if_active("monitor"):
            self.set_status("Wait for the Quad Cortex to finish, then trim the loop.")
            return
        duration = take.info["duration_seconds"]
        start, end = max(0., float(start)), min(duration, float(end))
        if end - start < .5:
            self.set_status("Keep at least half a second in the loop.")
            return
        if listening.same_passage((start, end), take.section):
            return
        record = take.folder / "clip.json"
        try:
            info = json.loads(record.read_text(encoding="utf-8"))
            if start <= .01 and end >= duration - .01:
                info.pop("selected_passage_seconds", None)
            else:
                info["selected_passage_seconds"] = [round(start, 3), round(end, 3)]
            session.write_json(record, info)
        except (OSError, ValueError) as error:
            self.set_status(f"The loop could not be saved. {error}", kind="error")
            return
        self.takes = listening.takes()
        fresh = next((item for item in self.takes if item.folder == take.folder), None)
        self.select_take(fresh)
        if self.task_if_active("monitor") and self.requested_source == "phrase" and fresh:
            self.start_source("phrase", reload=True)
        self.set_status(f"Loop is now {end - start:.1f} s.")

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
            self.set_status(f"{side.upper()} selected. Guitar or Phrase starts the input.")
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

    def set_shape(self, values, *, side=None, checkpoint=True, label="Shape"):
        side = side or self.side
        value_map = {}
        for key, value in values.items():
            if key in styles.KNOBS:
                floor = styles.KNOBS[key][1]
                value = round(max(floor, min(1., float(value))), 2)
                if value:
                    value_map[key] = value
        if checkpoint and value_map != self.shapes[side]:
            self.checkpoint(label, merge=("shape", side, label))
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
            self.set_status("Shape queued. Start Guitar or Phrase to hear these controls on the Quad Cortex.")
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
        self.player.set_volume(self.volume_settings)

    def change_volume(self, settings):
        settings = saved_settings(settings)
        if settings == self.volume_settings:
            return
        self.volume_settings = settings
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
        live = self.task_if_active("monitor")
        level = self.level_feedback.get(self.side, {}) if live else self.player.level_feedback if self.playing else {}
        status = level.get("status")
        if status in ("unavailable", "limited"):
            note = level.get("reason") or "The output reached its adjustment limit."
        elif not settings["match"]:
            note = "Manual output trim"
        elif status == "matched":
            note = "A/B matched from phrase" if live and self.active_source == "guitar" else "A/B matched"
        elif status in ("leveled", "measured"):
            note = "Level set from phrase" if live and self.active_source == "guitar" else "Loudness measured"
        elif live:
            if not self.take:
                note = "Choose a phrase to measure loudness."
            elif self.selectable("a") and self.selectable("b"):
                note = "Let Phrase loop on A and B to measure loudness."
            else:
                note = "Let Phrase loop to measure this tone."
        else:
            note = "Smart level across tones"
        if status in ("matched", "leveled", "measured", "manual") and level.get("limited"):
            note += " · Guitar output limit" if live and self.active_source == "guitar" else " · Headroom limit"
        peak = self.return_peak if live and self.active_source == "phrase" and not settings["muted"] else None
        if peak and peak > 1e-4:
            note += f" · return peak {20 * math.log10(peak):.0f} dBFS"
        self.volume.sync(settings, note)
        if peak and peak >= .99:
            self.level_problem = "The processed signal is clipping on its way back over USB"
        else:
            self.level_problem = note if live and status in ("unavailable", "limited") else None

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
            self.phrase.set(f"{take.name} · {length:.1f} s  ▾")
        else:
            self.phrase.set("No phrase yet  ▾")
        self.target.set(self.reference.name if self.reference else "")
        hidden = self.blind
        if hidden:
            for variable in (self.a_name, self.b_name):
                variable.set("Hidden")
            self.a_context.set("Left")
            self.b_context.set("Right")
        else:
            self.a_name.set(self.baseline.name if self.baseline and self.tone_a.kind == "original" else self.tone_a.name)
            self.b_name.set(self.tone_b.name if self.tone_b else "Current Quad Cortex")
            self.a_context.set(self.caption("a"))
            self.b_context.set(self.caption("b"))
        idle = self.job is None
        live_monitor = self.task_if_active("monitor") and not self.stop_signal.is_set()
        can_select = idle or live_monitor
        on_air = self.on_air()
        for place, tile in (("a", self.a_tile), ("b", self.b_tile)):
            side = hidden[place] if hidden else place
            sound = self.baseline if side == "a" else self.alternative
            captured = self.playing is not None and self.playing == sound and not hidden
            tile.sync(selected=self.side == side and self.selectable(side), enabled=can_select,
                      on_air=on_air == side or captured, mask=bool(hidden),
                      note="● on air" if on_air == side else "● captured, through Windows" if captured else "")
        if self.reference:
            self.r_name.set(self.reference.name)
            self.r_context.set("Reference")
            if not self.r_shown:
                self.r_tile.grid(row=0, column=2, sticky="nsew", padx=(10, 0))
                self.r_shown = True
            heard = self.playing is not None and self.playing == self.reference
            self.r_tile.sync(selected=False, enabled=idle, on_air=heard, note="● playing" if heard else "")
        elif self.r_shown:
            self.r_tile.grid_remove()
            self.r_shown = False
        for name, button in (("guitar", self.live_button), ("phrase", self.loop_button)):
            lit = self.active_source == name
            usable = can_select and (name == "guitar" or self.take)
            button.configure(bg=LIGHT if lit else TILE, fg=ACCENT_INK if lit else LIGHT,
                             state="normal" if usable else "disabled")
        self.take_menu.configure(state="normal" if can_select else "disabled")

        # Keep, Save and the recording stop exist only while they have something to act on.
        recording = self.task_if_active("record")
        tone = self.shape_tone()
        unsaved = bool(self.values) or bool(tone and tone.kind == "draft")
        offered = tuple(button for button, wanted in (
            (self.save_button, unsaved and can_select and not recording),
            (self.keep_button, (bool(on_air) or idle and self.selected_sound() is not None) and not recording),
            (self.record, recording)) if wanted)
        if offered != self.offered:
            for button in (self.save_button, self.keep_button, self.record):
                button.pack_forget()
            for button in offered:
                button.pack(side="right", padx=(6, 0), before=self.playhead)
            self.offered = offered
        self.keep_button.configure(text="Kept" if self.is_kept() else "Keep",
                                   state="disabled" if self.is_kept() else "normal")
        self.sync_volume()
        self.sync_air()
        selected_tone = self.shape_tone()
        recipe = self.tone_draft()
        expandable = (can_select and selected_tone and selected_tone.kind == "draft" and recipe
                      and preset_design.additional_shaping(recipe))
        available, moves = self.controls()
        self.knobs.sync("Hidden tone" if hidden else
                        f"{self.side.upper()} · {selected_tone.name if selected_tone else 'Current Quad Cortex'}",
                        self.values, available, expandable=bool(expandable), moves=moves)
        if self.field_window.winfo_viewable():
            self.space.sync(self.values, self.available)
        self.sync_panels()
        self.setup.sync()

    def caption(self, side):
        tone = self.tone(side)
        wave = self.baseline if side == "a" else self.alternative
        if tone is None:
            return "The preset loaded on the Quad Cortex"
        draft = self.drafts.get(tone.draft_id) if tone.draft_id else None
        if tone.preset:
            scene = f" · scene {device.SCENES[tone.scene]}" if tone.scene is not None else ""
            where = f"{tone.preset.setlist} {tone.preset.slot}{scene}"
        else:
            where = {"original": "Recorded with the phrase", "capture": "Captured audio",
                     "draft": (draft or {}).get("target") or "Draft"}[tone.kind]
            scenes = (draft or {}).get("scenes")
            if tone.kind == "draft" and scenes:
                where = f"{scenes[min(tone.scene or 0, len(scenes) - 1)]} · {where}"
        if tone.kind in ("original", "capture"):
            return where if wave else where + " · no clip for this phrase"
        return where + " · shaped" if self.shapes[side] else where

    def controls(self):
        """Which directions the selected chain can move, and through which parameters."""
        draft = self.tone_draft()
        moves = {}
        if draft:
            moves = self.static_cache.get(draft["id"])
            if moves is None:
                try:
                    moves = styles.draft_controls(draft)
                except (KeyError, TypeError, ValueError):
                    diagnostics.LOG.exception("Could not read the draft's controls")
                    moves = {}
                self.static_cache[draft["id"]] = moves
        if self.available is not None:
            return self.available, moves
        return (set(moves) if moves else None), moves

    def sync_air(self):
        """The dot is quiet while everything is fine and speaks when something needs the player."""
        live = self.task_if_active("monitor") and not self.stop_signal.is_set()
        output = self.output.get()
        action = None
        if self.task_if_active("record"):
            color = theme.ALERT
            words = "Recording · Space stops" if self.live_is_ready else "Connecting · wait before you play"
        elif live and self.active_source:
            if output.startswith(("Switching", "Loading")):
                color, words = SOFT, "Switching…" if self.blind else output
            elif self.level_problem:
                color, words = theme.WARN, self.level_problem
                action = ("Levels", lambda: self.tray.open("Levels"))
            else:
                color, words = theme.LIVE, "On air" + (" · blind" if self.blind else "") + (
                    " · base tone, shape bypassed" if self.base_bypassed else "")
        elif self.playing:
            color, words = theme.LIVE, output
        elif self.problem and self.job is None:
            color = theme.WARN
            words, action = self.problem
        elif self.job is not None or self.play_job is not None:
            color, words = SOFT, "" if output in QUIET_OUTPUT else output
        else:
            color, words = DIM, ""
        loud = color in (theme.LIVE, theme.WARN, theme.ALERT)
        self.dot.itemconfigure(self.dot_mark, fill=color)
        self.dot.itemconfigure(self.dot_halo, fill=theme.mix(color, PAPER, .24) if loud else PAPER)
        self.air.set(words)
        self.air_label.configure(fg=INK if color in (theme.WARN, theme.ALERT) else SOFT)
        if action != self.air_action:
            self.air_action = action
            self.air_link.pack_forget()
            if action:
                self.air_link.configure(text=action[0], command=action[1])
                self.air_link.pack(side="left", padx=(10, 0))

    def touch(self, key):
        """Remember which controls this player reaches for, most recent first."""
        self.touched = [key, *(name for name in self.touched if name != key)][:8]

    def checkpoint(self, label, *, merge=None):
        """Remember both tones before a change so one step brings them back."""
        now = monotonic()
        last = self.undo[-1] if self.undo else None
        if merge and last and last["merge"] == merge and now - last["at"] < 1.5:
            last["at"] = now
            return
        self.undo.append({"label": label, "merge": merge, "at": now, "side": self.side, "draft": self.draft,
                          "a": (self.tone_a, dict(self.shapes["a"])), "b": (self.tone_b, dict(self.shapes["b"]))})
        del self.undo[:-40]

    def undo_last(self):
        if not self.undo:
            self.set_status("Nothing to undo.")
            return "break"
        if self.job is not None and not self.task_if_active("monitor"):
            self.set_status("Wait for the Quad Cortex to finish, then undo.")
            return "break"
        step = self.undo.pop()
        self.draft = step["draft"]
        for side in ("a", "b"):
            tone, shape = step[side]
            if tone != self.tone(side):
                self.set_tone(tone, side=side, select=False, shape=shape, checkpoint=False)
            elif shape != self.shapes[side]:
                self.set_shape(shape, side=side, checkpoint=False)
        if step["side"] != self.side and self.selectable(step["side"]):
            self.select_side(step["side"])
        self.knobs.refresh()
        self.set_status(f"Undid: {step['label']}.")
        self.update()
        return "break"

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
            messages = {"record": "Saving the phrase…", "learner-listen": "Closing the dry input…"}
            self.set_status(messages.get(self.task, "Restoring your Quad Cortex sound…"))
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
        tracks, metrics = listening.playback_material(pair)
        if not cancel.is_set():
            return self.player.open(tracks, index, metrics=metrics)

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
            scene = (tone.scene or 0) if draft.get("scenes") else None
            self.submit("render", f"Rendering {draft['name']} on this phrase…",
                        draft_render.hear, draft, self.take.folder, values=values, scene=scene,
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
        self.set_status(f"Captured {side.upper()} is ready.", actions=(("Hear it", lambda: self.listen(side)),))

    def set_tone(self, tone, *, side="b", select=True, shape=None, checkpoint=True, label=None):
        if checkpoint:
            self.checkpoint(label or f"{tone.name if tone else 'tone'} on {side.upper()}")
        if self.blind:
            self.blind = None
            self.tray.bring_back(self.blind_panels)
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
        self.set_tone(Tone.from_draft(draft), shape=shape, label=f"{draft['name']} on B")

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
        self.set_status(f"{preset.name} is on {side.upper()}. Guitar or Phrase plays it.",
                        actions=(("Undo", self.undo_last),))

    def named_voicing(self, query):
        """A request that is exactly the name of one block's setting in the selected draft means that setting."""
        draft = self.tone_draft()
        matches = []
        for block in (draft or {}).get("chain", []):
            if block.get("asset") or block.get("reserved"):
                continue
            role = preset_design.choices()[block["model_id"]]["role"]
            matches += [(role, name, values) for name, values in preset_design.named_settings(block["model_id"]).items()
                        if name.casefold() == query.casefold()]
        return matches[0] if len(matches) == 1 else None

    def choose_scene(self, scene):
        """Hear another scene of the selected draft. The knobs stay where they are."""
        tone, draft = self.tone(), self.tone_draft()
        if (not tone or tone.kind != "draft" or not draft or scene >= len(draft.get("scenes", ()))
                or (tone.scene or 0) == scene):
            return
        if self.job is not None and not self.task_if_active("monitor"):
            self.set_status("Wait for the Quad Cortex to finish, then try again.")
            return
        name = draft["scenes"][scene]
        # Audio captured from another scene no longer describes this tone.
        self.set_tone(replace(tone, scene=scene, capture=None), side=self.side, shape=self.shapes[self.side],
                      label=f"Scene {name} on {self.side.upper()}")
        self.set_status(f"{self.side.upper()} is on scene {name}.", actions=(("Undo", self.undo_last),))

    def voice_block(self, role, name, values):
        """Give one block of the selected draft a named group of settings, as a new version."""
        current = self.tone_draft()
        if not current:
            return
        try:
            draft = preset_design.variant_with_settings(current, role, name, values)
        except (KeyError, TypeError, ValueError) as error:
            self.set_status(str(error), kind="error")
            return
        self.put_draft(draft, self.side)
        self.set_status(f"{role} set to “{name}” on {self.side.upper()}.", kind="reading",
                        actions=(("Undo", self.undo_last),))

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
        self.set_status(f"{asset.name} is in the new B tone.", actions=(("Undo", self.undo_last),))

    def choose_wave(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose a guitar reference excerpt",
                                          filetypes=(("WAV audio", "*.wav"),))
        if path:
            self.reference = listening.Sound(Path(path).stem, Path(path))
            self.reference_choice = None
            self.remember()
            self.set_status("This reference has its own performance. Press R to hear what you're aiming for.")
            self.tray.open("Difference", auto=True)
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
        self.set_status(f"{sound.name} is the reference. Press R to hear it.")
        self.tray.open("Difference", auto=True)
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
        self.tray.open("Difference", auto=True)

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

    def named_preset(self, query):
        """A request that is exactly the name of one preset on the unit means that preset."""
        library = self.browser.library
        matches = [row for row in (library.presets if library else ()) if row.name.casefold() == query.casefold()]
        return matches[0] if len(matches) == 1 else None

    def ask(self, text=None, *, force=None):
        typed = text is None
        query = (self.request.get() if typed else text).strip()
        if not query:
            return
        if self.thinking is not None:
            self.set_status("Still working on your last request.", kind="progress",
                            actions=(("Cancel it", self.cancel_request),))
            return
        current = self.tone_draft()
        moves = None
        if force:
            action, value = force, query
        else:
            preset = self.named_preset(query)
            if preset and (self.job is None or self.task_if_active("monitor")):
                self.request.set("")
                self.use_as_tone(preset)
                return
            moves, said, ways = self.instant_reading(query)
            voicing = None if moves else self.named_voicing(query)
            if voicing and (self.job is None or self.task_if_active("monitor")):
                self.request.set("")
                self.voice_block(*voicing)
                return
            action, value = ("adjust-live", query) if moves else listening.route(query, has_draft=current is not None)
        if action in ("reference", "match") and self.job is not None:
            self.set_status("Finish or stop the current hardware action, then try that request again.")
            return
        if typed:
            self.request.set("")
            self.root.focus_set()
        if action == "volume":
            self.change_volume(listening_command(self.volume_settings, value))
            self.set_status(f"Listening level · {self.volume.label}.")
            return
        if action == "stop":
            self.stop()
            return
        if action == "undo":
            self.undo_last()
            return
        if action == "side":
            self.pick(value)
            return
        if action == "save":
            self.save()
            return
        if action == "source":
            self.start_source(value)
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
        # The answer belongs to the tone that was there when the question was asked.
        side = "b" if action in ("create", "refine") else self.side
        tone = self.tone(side)
        name = tone.name if tone else "the current Quad Cortex sound"
        self.intent_side = side
        self.asked = {"text": query, "action": action, "side": side, "revision": self.tone_revisions[side],
                      "name": name}
        cancel = (("Cancel", self.cancel_request),)
        self.cancel_signal = Event()
        sounds = self.heard(self.side if action == "refine" else side)
        if moves:
            # A plain "tighter" or "too bright" needs no round trip. It is heard at once and can be undone.
            self.answered({"reply": said, "intent": "adjust", "instant": True, "ways": ways,
                           "knobs": self.stepped(side, moves)})
            self.update()
            return
        if action in ("create", "refine"):
            prior = current if action == "refine" else None
            prompt_context = f"Selected listening reference: {self.reference.name}" if self.reference else ""
            if self.kept_note and self.alternative and self.alternative.path == self.kept:
                prompt_context += f"\nPlayer's listening note on this version: {self.kept_note}"
            self.set_status(f"Revising {prior['name']} as a new version on B…" if prior
                            else "Building a new sound for B. Research can take a minute…",
                            kind="progress", actions=cancel)
            self.thinking = self.advisor.submit(compose, value, take["guitar"], take, prior, prompt_context,
                                                sounds, self.cancel_signal)
        else:
            if tone:
                take["active_tone"] = tone.name
            known = self.controls()[0]
            available = None if known is None else frozenset(known)
            self.set_status(f"Working out a change for {side.upper()} · {name}…", kind="progress", actions=cancel)
            chain = self.tone_draft(side)
            gear = tuple(block["model_id"] for block in chain["chain"]) if chain else ()
            self.thinking = self.advisor.submit(consult, value, take, dict(self.shapes[side]), available,
                                                sounds, self.cancel_signal, gear)
        self.update()

    def instant_reading(self, query):
        """A remark the selected chain can act on without asking anyone.

        Returns the knob steps, how the remark was read, and the other remedies
        players try for it, in order.
        """
        known = self.controls()[0]
        reading = lore.read(query)
        ways = lore.usable(reading, known) if reading else ()
        if ways:
            return ways[0].moves, f"{reading.said}: {ways[0].say}.", (reading.said, ways[1:], reading.tip)
        moves = styles.direction(query)
        if not moves or known is not None and any(key not in known for key in moves):
            return None, "", None
        return moves, "", None

    def instant_moves(self, query):
        return self.instant_reading(query)[0]

    def stepped(self, side, moves, shape=None):
        shape = self.shapes[side] if shape is None else shape
        return {key: round(max(styles.KNOBS[key][1], min(1, shape.get(key, 0) + step)), 2)
                for key, step in moves.items()}

    def another_way(self, side, before, said, ways, tip=""):
        """Swap the remedy just applied for the next one players reach for."""
        if self.tone_revisions[side] != before[0]:
            self.set_status(f"{side.upper()} has changed since then. Ask again for this tone.")
            return
        remedy, rest = ways[0], ways[1:]
        # Start again from the shape the remark was made about, so remedies do not pile up.
        knobs = {**{key: before[1].get(key, 0) for key in self.shapes[side]}, **before[1],
                 **self.stepped(side, remedy.moves, before[1])}
        more = (("Another way", lambda: self.another_way(side, before, said, rest, tip)),) if rest else ()
        self.apply_suggestion(side, knobs, reply=f"{said}: {remedy.say}.", more=more, moved=remedy.moves, hint=tip)

    def heard(self, side):
        """The captured sounds that say something about a tone, gathered for measuring off the main thread."""
        sounds = {"a": self.baseline, "b": self.alternative}
        chosen = sounds[side]
        return chosen, sounds["a" if side == "b" else "b"], self.reference, self.rendered_values(chosen)

    def cancel_request(self):
        """Let go of a request in flight. Its result, if one still arrives, is ignored."""
        if self.thinking is None:
            return
        if self.cancel_signal is not None:
            self.cancel_signal.set()
        self.abandoned.append(self.thinking)
        self.thinking = self.followup = None
        if not self.request.get():
            self.request.set(self.sent)
        self.set_status("Cancelled. Nothing was changed.")
        self.update()

    def answered(self, result):
        asked = self.asked
        side = asked["side"]
        current = self.tone_revisions[side] == asked["revision"]
        if asked["action"] in ("create", "refine"):
            self.drafts[result["id"]] = result
            if self.job is not None and not self.task_if_active("monitor"):
                self.draft = result
                self.set_status(f"{result['name']} is ready in History.", kind="reading",
                                actions=(("History", lambda: self.tray.open("History")),))
            elif not current:
                # B was replaced while this was being built. Offer the result. Never apply it unasked.
                self.set_status(f"{result['name']} is ready. B changed while it was being built, so it was left alone.",
                                kind="reading", actions=(("Put on B", lambda: self.put_draft(result, "b")),))
            else:
                self.install_draft(result)
                self.set_status(f"{result['name']} is on B · {result['target']}", kind="reading",
                                actions=(("Undo", self.undo_last), ("Why this sound", lambda: self.tray.open("Chain"))))
                if result["id"] not in self.chain_seen:
                    self.chain_seen.add(result["id"])
                    self.tray.open("Chain", auto=True)
        else:
            reply, knobs = result["reply"], result["knobs"]
            rebuild = () if QUESTION.match(asked["text"]) else (
                ("New sound instead", lambda request=asked["text"]: self.ask(request, force="create")),)
            if result.get("instant"):
                # The knob answers at once. The next remedy and a deeper change to the chain stay one step away.
                rebuild = (("Rework the chain", lambda request=asked["text"]: self.ask(request, force="refine")),
                           ) if self.tone_draft(side) else ()
                said, ways, tip = result.get("ways") or ("", (), "")
                if ways:
                    before = (self.tone_revisions[side], dict(self.shapes[side]))
                    rebuild = (("Another way", lambda: self.another_way(side, before, said, ways, tip)), *rebuild)
            if result.get("intent") == "new_sound" and not knobs:
                self.followup = asked["text"]
                self.set_status(reply, kind="reading")
            elif knobs and not current:
                self.set_status(f"{reply} That was for {asked['name']}, which is no longer on {side.upper()}.".strip(),
                                kind="reading",
                                actions=((f"Apply to {side.upper()} anyway",
                                          lambda: self.apply_suggestion(side, knobs)),))
            elif knobs:
                self.apply_suggestion(side, knobs, reply=reply, more=rebuild,
                                      hint=(result.get("ways") or ("", (), ""))[2])
            else:
                self.set_status(reply, kind="reading", actions=rebuild)
        self.remember()

    def apply_suggestion(self, side, knobs, *, reply="", more=(), moved=None, hint=""):
        self.set_shape({**self.shapes[side], **knobs}, side=side, label="Request")
        shown = knobs if moved is None else {key: knobs[key] for key in moved}
        for key in shown:
            self.touch(key)
        moved = ", ".join(f"{styles.KNOBS[key][0]} {round(value * 100):+d}%" for key, value in shown.items())
        # Shape opens by itself below, so a remark with other remedies to offer does not also offer to open it.
        shape = () if any(title == "Another way" for title, _ in more) else (
            ("What changed", lambda: self.tray.open("Shape")),)
        self.set_status(f"{reply} {moved} on {side.upper()}.".strip(), kind="reading", hint=hint,
                        actions=(("Undo", self.undo_last), *shape, *more))
        self.knobs.refresh()
        self.tray.open("Shape", auto=True)

    def put_draft(self, draft, side="b"):
        if self.job is not None and not self.task_if_active("monitor"):
            self.set_status("Wait for the Quad Cortex to finish, then try again.")
            return
        self.drafts[draft["id"]] = draft
        if side == "b":
            self.draft = draft
        self.set_tone(Tone.from_draft(draft), side=side, select=side == "b", label=f"{draft['name']} on {side.upper()}")
        self.set_status(f"{draft['name']} is on {side.upper()}.", actions=(("Undo", self.undo_last),))

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
            self.checkpoint("Original tone on A")
            self.stop_audio()
            self.tone_a = Tone.original()
            self.shapes["a"] = {}
            self.bind_recorded_tone()
            self.resolve_audio()
            self.remember()
            self.update()

    def is_kept(self):
        side = self.on_air()
        if side:
            return self.kept_live == (side, self.tone_revisions[side], tuple(sorted(self.shapes[side].items())))
        sound = self.selected_sound()
        return bool(sound and sound.path == self.kept)

    def keep(self, *, note=""):
        """Record a preference for the sound being heard. Saving to the unit is a separate act."""
        side = self.on_air()
        if side:
            tone = self.tone(side)
            record = tone.to_record() if tone else {"kind": "current", "name": self.qc_name or "Current Quad Cortex"}
            try:
                listening.keep_tone(record, self.shapes[side], self.take, source=self.active_source,
                                    reference=self.reference.name if self.reference else "", note=note)
            except OSError as error:
                self.set_status(str(error), kind="error")
                return
            self.kept_live = (side, self.tone_revisions[side], tuple(sorted(self.shapes[side].items())))
            self.kept_note = note
            if self.blind:
                self.reveal(kept=side)
                return
            self.set_status(f"Kept {side.upper()} · {record['name']} as you heard it. Save to QC writes it to the unit.",
                            kind="reading")
            self.update()
            return
        sound = self.selected_sound()
        if self.job is not None or not sound:
            self.set_status("Start Guitar or Phrase, then keep the sound you prefer.")
            return
        try:
            listening.keep(sound, self.take, self.target.get(), self.baseline, note=note)
        except OSError as error:
            self.set_status(str(error), kind="error")
            return
        self.kept = sound.path
        self.kept_note = note
        self.remember()
        self.set_status(f"Kept {sound.name} in this listening session.", kind="reading")
        self.update()

    def save(self):
        if self.job is not None:
            self.set_status("Stop the input first (Space), then save.")
            return
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
        else:
            self.set_status("There is nothing new to save on this tone.")

    def saved(self, location, draft, side):
        tone = Tone.from_draft(draft)
        if tone.preset:
            proof = self.baseline if side == "a" else self.alternative
            self.set_tone(tone.with_capture(proof), side=side, checkpoint=False)
        self.browser.changed()
        self.presets = None
        self.set_status(f"Saved · {location}")

    def saved_variant(self, result, side):
        preset, scene, fingerprint = result
        tone = Tone.from_preset(preset, scene, fingerprint=fingerprint)
        sound = self.baseline if side == "a" else self.alternative
        if sound and self.shapes[side] != self.rendered_values(sound):
            sound = None
        self.set_tone(tone.with_capture(sound), side=side, checkpoint=False)
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
            if config["draft"].get("scenes"):
                config["scene"] = tone.scene or 0
            proof = self.baseline if side == "a" else self.alternative
            if proof and proof.draft_id == tone.draft_id and proof.folder:
                config["gain"] = self.read_state(proof.folder / "session.json").get("output_gain_db")
        return config

    def start_source(self, source, *, reload=False):
        if source == "phrase" and not self.take:
            self.set_status("Choose or record a phrase first.")
            return
        if self.task_if_active("monitor") and not self.stop_signal.is_set():
            self.requested_source = source
            self.control_mail.put({"event": "source", "mode": source,
                                   "take": str(self.take.folder) if self.take else None,
                                   **({"reload": True} if reload else {})})
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
        self.set_status(message, kind="progress")
        self.job = self.worker.submit(work, *args, **kwargs)
        self.update()

    def tick(self):
        if self.check is not None and self.check.done():
            try:
                self.qc_name, self.qc_scene, self.qc_position = self.check.result()
                self.problem = None
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
                self.problem = ("Cortex Control is open · Quad Cortex controls are paused", None)
            except Exception:
                self.qc_name = self.qc_scene = self.qc_position = None
                self.hardware.set("Quad Cortex · connection unchecked")
                self.problem = ("The Quad Cortex is not answering", ("Check again", self.check_qc))
            self.check = None
            self.sync_air()
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
                self.set_status(str(error), kind="error")
                self.output.set("Playback unavailable")
            self.play_job = None
            self.update()
        if self.job is not None and self.job.done():
            past, self.job = self.job, None
            task = self.task
            self.stop_signal = None
            if task == "monitor":
                self.available = self.active_source = self.requested_source = self.active_side = None
                self.return_peak = None
                self.active_revision = None
                self.phrase_progress = None
                self.base_bypassed = False
                self.setup.live_event({"event": "guitar-stopped"})
                self.output.set("Stopped")
            try:
                self.outcome(past.result())
            except device.EditorActive as error:
                if task != "browse" or not self.preset_query or not self.use_available_reference(self.preset_query):
                    self.set_status(str(error), kind="error")
                self.qc_name = self.qc_scene = self.qc_position = None
                self.hardware.set("Cortex Control open · device controls paused")
                self.problem = ("Cortex Control is open · Quad Cortex controls are paused", None)
            except Exception as error:
                diagnostics.LOG.exception("Listening desk operation failed")
                self.set_status(str(error), kind="error")
                if task in ("monitor", "render", "reference", "save", "load"):
                    self.hardware.set("Quad Cortex · checking current sound")
                    self.check_qc()
            self.update()
            if self.editor_after and self.leaving is None:
                self.editor_after = False
                self.open_editor()
        elif self.task_if_active("record") and self.ready.is_set() and not self.stop_signal.is_set() and not self.live_is_ready:
            self.set_status("Recording your guitar · play the phrase, then press Space.", kind="progress")
            self.live_is_ready = True
            self.sync_air()
        try:
            while True:
                message = self.live_messages.get_nowait()
                if not self.task_if_active("monitor") or self.stop_signal.is_set():
                    continue
                self.monitor_event(message)
        except Empty:
            pass
        if self.thinking is not None and self.thinking.done():
            finished, self.thinking = self.thinking, None
            try:
                self.answered(finished.result())
            except Exception as error:
                diagnostics.LOG.exception("Listening advisor failed")
                self.set_status(str(error), kind="error",
                                actions=(("Try again", lambda request=self.sent: self.ask(request)),))
            if self.followup:
                request, self.followup = self.followup, None
                self.ask(request, force="create")
            self.update()
        self.abandoned = [future for future in self.abandoned if not future.done()]
        loop = self.player.loop
        if self.playing and (self.player.failures > 6 or self.player.stream and not self.player.stream.active):
            self.stop_audio()
            self.set_status("Windows playback stalled. Check the output device and start the comparison again.",
                            kind="error")
            self.update()
        width = self.playhead.winfo_width()
        if self.phrase_progress and self.active_source == "phrase" and self.stop_signal and not self.stop_signal.is_set():
            stamped, frame, length = self.phrase_progress
            along = ((frame + (monotonic() - stamped) * RATE) % length) / length
            cursor = width * along
        else:
            along = None
            cursor = width * loop.cursor / len(loop.tracks[0]) if loop and self.playing else 0
        if self.tray.is_open("Phrase"):
            self.phrase_panel.cursor(along)
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
                if not reason:
                    self.offer_layout()
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
            self.return_peak = audio.get("peak")
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
        if self.guitar_learner is not None:
            self.guitar_learner.shutdown()
        self.setup.shutdown()
        self.worker.shutdown(wait=True)
        self.advisor.shutdown(wait=False, cancel_futures=True)
        self.leave(destination)
