"""A session organized around a guitarist's next listening decision."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from threading import Event
import json
import tkinter as tk
from tkinter import filedialog
import webbrowser

from . import advisor, capture, desktop, device, diagnostics, draft_render, iterate, listening, preset_design, preset_library, session, styles


PAPER = "#f1efea"
WHITE = "#ffffff"
INK = "#22232a"
SUBTLE = "#5a5b67"
RULE = "#dedbd5"
DARK = "#24262f"
TILE = "#353743"
LIGHT = "#f8f7f4"
SOFT = "#bcbcc9"
ACCENT = "#ffc18f"
FONT = "Segoe UI"


def text(parent, value=None, *, variable=None, size=11, color=INK, bold=False, wrap=0):
    return tk.Label(parent, text=value, textvariable=variable, bg=parent["bg"], fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), justify="left", anchor="w", wraplength=wrap)


def control(parent, title, action, *, primary=False, dark=False):
    return tk.Button(parent, text=title, command=action, relief="flat", borderwidth=0, takefocus=True,
                     padx=13, pady=6, cursor="hand2", font=(FONT, 10, "bold"),
                     bg=ACCENT if primary else TILE if dark else WHITE,
                     fg=LIGHT if dark and not primary else INK,
                     activebackground="#ffd7b7" if primary else "#dcd7d0", activeforeground=INK,
                     disabledforeground="#92909a", highlightthickness=1, highlightbackground=parent["bg"],
                     highlightcolor="#b2541d")


def menu(parent, title, *, dark=False, refresh=None):
    button = tk.Menubutton(parent, text=title, indicatoron=True, cursor="hand2", takefocus=True,
                          font=(FONT, 10, "bold"), bg=TILE if dark else WHITE,
                          fg=LIGHT if dark else INK, padx=13, pady=7)
    entries = tk.Menu(button, tearoff=False, postcommand=refresh)
    button.configure(menu=entries)
    return button, entries


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
        self.values = {}
        self.takes = listening.takes()
        self.drafts = {draft["id"]: draft for draft in preset_design.history()}
        self.presets = None
        self.reference = self.reference_choice = None
        self.adjustment = {}
        self.take = self.baseline = self.alternative = self.draft = None
        self.last_heard = self.kept = self.playing = self.play_group = None
        self.kept_note = ""
        self.qc_name = None
        self.state_file = session.SESSIONS / "desk.json"
        self.state = self.read_state(self.state_file)
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

    def build(self):
        root = self.root
        root.title("Tone Lab")
        root.configure(bg=PAPER)
        root.minsize(850, 545)
        root.geometry(f"{min(1060, max(850, root.winfo_screenwidth() - 70))}x"
                      f"{min(575, max(545, root.winfo_screenheight() - 110))}")
        root.protocol("WM_DELETE_WINDOW", lambda: self.close(None))
        root.bind("<Escape>", lambda _: self.stop())
        root.bind("<Control-k>", lambda _: self.command.focus_set())
        root.bind("<Control-1>", lambda _: self.listen("a"))
        root.bind("<Control-2>", lambda _: self.listen_or_render())

        page = tk.Frame(root, bg=PAPER, padx=25, pady=15)
        page.pack(fill="both", expand=True)
        header = tk.Frame(page, bg=PAPER)
        header.pack(fill="x", pady=(0, 12))
        text(header, "Tone Lab", size=19, bold=True).pack(side="left")
        self.tools, tools = menu(header, "Tools")
        tools.add_command(label="Open Cortex Control", command=self.open_editor)
        tools.add_command(label="Check Quad Cortex", command=self.check_qc)
        tools.add_command(label="Open workbench · calibration, levels and tone controls",
                          command=lambda: self.close("workbench"))
        self.tools.pack(side="right")
        guitar, guitars = menu(header, "")
        guitar.configure(textvariable=self.guitar)
        for name in session.GUITARS:
            guitars.add_command(label=name, command=lambda value=name: self.select_guitar(value))
        guitar.pack(side="right", padx=(6, 9))
        text(header, variable=self.hardware, size=10, color=SUBTLE).pack(side="right")

        ask = tk.Frame(page, bg=WHITE, padx=16, pady=12)
        ask.pack(fill="x")
        caption = tk.Frame(ask, bg=WHITE)
        caption.pack(fill="x")
        text(caption, "What should it sound like?", size=22, bold=True).pack(side="left")
        text(caption, "Win+H to dictate", size=9, color=SUBTLE).pack(side="right")
        entry = tk.Frame(ask, bg=WHITE)
        entry.pack(fill="x", pady=(9, 5))
        self.command = tk.Entry(entry, textvariable=self.request, font=(FONT, 13), bg=PAPER, fg=INK,
                                relief="flat", highlightthickness=1, highlightbackground=RULE,
                                highlightcolor="#b2541d", insertbackground=INK)
        self.command.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 7))
        self.command.bind("<Return>", lambda _: self.ask())
        self.ask_button = control(entry, "Ask Tone Lab", self.ask, primary=True)
        self.ask_button.pack(side="right")
        self.guidance = text(ask, variable=self.status, color=SUBTLE, size=10, wrap=790)
        self.guidance.pack(fill="x", pady=(2, 0))
        ask.bind("<Configure>", lambda event: self.guidance.configure(wraplength=max(200, event.width - 36)))

        sources = tk.Frame(page, bg=PAPER, pady=9)
        sources.pack(fill="x")
        phrase = tk.Frame(sources, bg=PAPER)
        phrase.pack(side="left", fill="x", expand=True)
        text(phrase, "YOUR PHRASE", bold=True, color=SUBTLE, size=9).pack(anchor="w")
        self.phrase_line = text(phrase, variable=self.phrase, bold=True, size=10)
        self.phrase_line.pack(anchor="w")
        self.take_menu, self.take_options = menu(phrase, "Choose", refresh=self.list_phrases)
        self.take_menu.pack(side="left", pady=(4, 0))
        self.record = control(phrase, "Record phrase", self.recording)
        self.record.pack(side="left", padx=(5, 0), pady=(4, 0))
        ref = tk.Frame(sources, bg=PAPER)
        ref.pack(side="left", fill="x", expand=True, padx=(12, 0))
        text(ref, "REFERENCE", bold=True, color=SUBTLE, size=9).pack(anchor="w")
        text(ref, variable=self.target, bold=True, size=10, wrap=350).pack(anchor="w")
        self.ref_menu, refs = menu(ref, "Choose")
        refs.add_command(label="A saved Quad Cortex preset", command=lambda: self.find_presets(""))
        refs.add_command(label="An audio excerpt", command=self.choose_wave)
        self.reference_history = tk.Menu(refs, tearoff=False, postcommand=self.list_references)
        refs.add_cascade(label="Previously captured presets", menu=self.reference_history)
        refs.add_command(label="Clear reference", command=self.clear_reference)
        self.ref_menu.pack(side="left", pady=(4, 0))
        self.hear_reference = control(ref, "Play reference", lambda: self.listen("reference"))
        self.hear_reference.pack(side="left", padx=(5, 0), pady=(4, 0))
        self.match_button = control(ref, "Find a match", self.match)
        self.match_button.pack(side="left", padx=(5, 0), pady=(4, 0))

        deck = tk.Frame(page, bg=DARK, padx=16, pady=13)
        deck.pack(fill="both", expand=True)
        line = tk.Frame(deck, bg=DARK)
        line.pack(fill="x")
        text(line, "Listen", size=15, color=LIGHT, bold=True).pack(side="left")
        text(line, variable=self.output, size=9, color=SOFT).pack(side="right")
        pair = tk.Frame(deck, bg=DARK)
        pair.pack(fill="x", pady=(8, 7))
        self.a_tile = self.tile(pair, "A  ·  STARTING SOUND", self.a_name, lambda: self.listen("a"))
        self.a_tile.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.b_tile = self.tile(pair, "B  ·  ALTERNATIVE", self.b_name, self.listen_or_render)
        self.b_tile.pack(side="left", fill="both", expand=True, padx=(4, 0))
        self.playhead = tk.Canvas(deck, background=TILE, height=4, highlightthickness=0)
        self.playhead.pack(fill="x")
        self.position = self.playhead.create_rectangle(0, 0, 0, 4, fill=ACCENT, width=0)
        transport = tk.Frame(deck, bg=DARK)
        transport.pack(fill="x", pady=(7, 0))
        self.pause = control(transport, "Stop", self.stop, dark=True)
        self.pause.pack(side="left")
        self.keep_button = control(transport, "Keep this sound", self.keep, primary=True)
        self.keep_button.pack(side="right")
        self.try_button = control(transport, "Try with guitar", self.try_guitar, dark=True)
        self.try_button.pack(side="right", padx=(0, 5))
        self.detail_menu, self.details = menu(transport, "Details", dark=True, refresh=self.list_details)
        self.detail_menu.pack(side="right", padx=(0, 5))
        text(transport, variable=self.step, color=SOFT, size=9, wrap=330).pack(side="left", padx=9)

    @staticmethod
    def tile(parent, heading, caption, action):
        tile = tk.Button(parent, relief="flat", anchor="w", justify="left", width=25, height=2, padx=12, pady=5,
                         takefocus=True, cursor="hand2", bg=TILE, fg=LIGHT, disabledforeground=SOFT,
                         activebackground="#ffd7b7", activeforeground=INK, font=(FONT, 12, "bold"),
                         highlightthickness=1, highlightbackground=TILE, highlightcolor=ACCENT, command=action)
        tile.heading = heading
        caption.trace_add("write", lambda *_: tile.configure(text=f"{tile.heading}\n{caption.get()}"))
        return tile

    def set_status(self, value):
        self.status.set(value)

    def selected_sound(self):
        return self.playing or self.last_heard

    def restore_context(self):
        self.reference = listening.Sound.from_record(self.state.get("reference"))
        if self.state.get("draft") in self.drafts:
            self.draft = self.drafts[self.state["draft"]]
        if self.take and self.state.get("take") == self.take.folder.name:
            stored_a = listening.Sound.from_record(self.state.get("baseline"))
            stored_b = listening.Sound.from_record(self.state.get("alternative"))
            if stored_a and stored_a.paired_with(self.take.original):
                self.baseline = stored_a
                self.alternative = stored_b if stored_b and stored_b.paired_with(stored_a) else None
        if self.state.get("kept"):
            self.kept = Path(self.state["kept"])
            self.kept_note = self.state.get("kept_note", "")
        if self.draft:
            self.set_status(self.draft["target"])
        self.update()

    def remember(self):
        data = {"guitar": self.guitar.get(), "take": self.take.folder.name if self.take else None,
                "kept": str(self.kept) if self.kept else None, "kept_note": self.kept_note,
                "draft": self.draft["id"] if self.draft else None,
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
        if self.job is not None:
            return
        self.stop_audio()
        self.last_heard = None
        self.take = take
        if self.draft and take:
            self.baseline, self.alternative = take.original, None
        else:
            self.baseline, self.alternative = listening.latest_comparison(take) if take else (None, None)
        if self.alternative and self.alternative.draft_id:
            self.draft = self.drafts.get(self.alternative.draft_id)
        self.adjustment = {}
        self.set_status("Try “Make me a Lenny Kravitz preset” or describe a change to your sound." if not self.draft
                        else f"{self.draft['target']}")
        if remember:
            self.remember()
        self.update()

    def update(self):
        take = self.take
        if take:
            length = take.section[1] - take.section[0]
            self.phrase.set(f"{take.name}  ·  {take.info.get('guitar') or 'Guitar unlabelled'}  ·  {length:.1f}s")
        else:
            self.phrase.set("Record a phrase or build a sound first")
        self.target.set(self.reference.name if self.reference else "No reference selected")
        self.a_tile.heading = "A  ·  REFERENCE" if self.baseline and self.baseline == self.reference else "A  ·  STARTING SOUND"
        self.b_tile.heading = "B  ·  REFERENCE" if self.alternative and self.alternative == self.reference else "B  ·  ALTERNATIVE"
        self.a_name.set(self.baseline.name if self.baseline else "Record a phrase")
        if self.alternative:
            self.b_name.set(self.alternative.name)
        elif self.draft or self.adjustment:
            self.b_name.set("Render the change" if self.take else "Needs a phrase")
        else:
            self.b_name.set("Ask for a change")
        renderable = (self.draft or self.adjustment) and self.take
        idle = self.job is None
        for button, enabled in (
                (self.a_tile, idle and self.baseline), (self.b_tile, idle and (self.alternative or renderable)),
                (self.ask_button, self.thinking is None and self.leaving is None),
                (self.take_menu, idle and self.takes), (self.ref_menu, idle),
                (self.hear_reference, idle and self.reference), (self.match_button, idle and self.take and self.reference),
                (self.keep_button, idle and self.selected_sound()),
                (self.try_button, idle and self.draft), (self.record, idle or self.task == "record"),
                (self.pause, not idle and self.stop_signal or self.playing or self.play_job)):
            button.configure(state="normal" if enabled else "disabled")
        for tile, sound in ((self.a_tile, self.baseline), (self.b_tile, self.alternative)):
            heard = sound and self.playing == sound
            tile.configure(bg=ACCENT if heard else TILE, fg=INK if heard else LIGHT)
        self.try_button.configure(text=f"Try {self.draft['name']} live" if self.draft else "Try with guitar")
        sound = self.selected_sound()
        if sound and sound.path == self.kept:
            self.keep_button.configure(text="Kept", state="disabled")
        else:
            self.keep_button.configure(text="Keep this sound")
        if self.baseline and self.alternative:
            self.step.set("Same phrase · level-matched" if self.baseline.paired_with(self.alternative)
                          else "Separate recordings")
        elif self.draft:
            self.step.set("Try it live or compare on a saved phrase")
        else:
            self.step.set("")

    def stop_audio(self):
        self.play_cancel.set()
        if self.playing or self.play_job or self.player.stream:
            self.worker.submit(self.player.close)
        self.play_group = self.playing = None
        self.output.set("Playback paused")

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
        tracks = listening.prepare(pair)
        if not cancel.is_set():
            return self.player.open(tracks, index)

    def started_playing(self, sound):
        self.last_heard = self.playing = sound
        side = "A" if sound == self.baseline else "B" if sound == self.alternative else "reference"
        self.output.set(f"Playing {side} · Windows audio")
        self.update()

    def listen_or_render(self):
        if self.alternative:
            self.listen("b")
        elif self.draft or self.adjustment:
            self.compare()

    def compare(self):
        if self.alternative:
            self.listen("b")
            return
        if not self.take:
            self.set_status("Record or choose a phrase to compare these sounds. You can try a draft live now.")
            return
        if self.draft:
            self.submit("render", f"Rendering {self.draft['name']} with your saved phrase…",
                        draft_render.hear, self.draft, self.take.folder, done=self.show_render)
        elif self.adjustment:
            self.submit("render", "Preparing the change on your saved phrase…",
                        iterate.render_knobs, self.take.folder, self.adjustment, done=self.show_render)
        else:
            self.set_status("Ask Tone Lab for a change or choose a preset to compare.")

    def restore_draft_comparison(self):
        if not self.draft or not self.take or self.job is not None:
            self.set_status("Choose a phrase and prepare a draft first.")
            return
        self.stop_audio()
        self.last_heard = None
        saved = listening.draft_comparison(self.take, self.draft["id"])
        self.baseline, self.alternative = saved or (self.take.original, None)
        self.remember()
        self.update()
        if saved:
            self.set_status(f"{self.draft['name']} is ready as B.")
        else:
            self.compare()

    def show_render(self, folder):
        pair = listening.comparison(folder, self.take)
        if not pair:
            self.set_status("The check did not produce two aligned sounds. Try the draft live or choose another phrase.")
            return
        baseline, alternative = pair
        if not self.baseline or not self.baseline.paired_with(alternative):
            self.baseline = baseline
        self.alternative = alternative
        self.last_heard = None
        self.remember()
        self.set_status("Ready to compare. Use A and B to change the sound without restarting the phrase.")

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
        if self.alternative and self.alternative == self.reference:
            self.alternative = None
        if self.baseline and self.baseline == self.reference:
            self.baseline = self.take.original if self.take else None
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
        self.baseline = (self.reference if self.reference.paired_with(candidate) else self.take.original)
        self.alternative = candidate
        self.last_heard = None
        self.remember()
        self.set_status("Closest saved sound is B. Listen to decide whether it fits.")

    def find_presets(self, query):
        self.preset_query = query
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
        if self.baseline and self.baseline.paired_with(sound):
            self.stop_audio()
            self.alternative = sound
            self.last_heard = None
            self.set_status("Previously captured reference ready as B.")
        else:
            self.set_status("Previously captured reference ready to play separately.")
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
        self.hardware.set(f"QC last seen · {choice.name}")
        self.set_status(f"{choice.name} is ready to play on the Quad Cortex.")

    def use_preset(self, choice, scene=None):
        self.reference_choice = choice
        if not self.take:
            self.set_status(f"Record a phrase to hear {choice.name} against your current sound.")
            return
        self.submit("reference", f"Replaying your phrase through {choice.name}…",
                    preset_library.render_reference, choice, self.take.folder, scene=scene, done=self.captured_reference)

    def captured_reference(self, reference):
        self.reference = listening.captured_sound(reference, self.take)
        self.alternative = self.reference
        self.last_heard = None
        self.remember()
        self.set_status("Reference ready as B. Your playing preset and scene were restored.")

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
        self.record.configure(text="Stop recording")

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
        action, value = listening.route(query, has_draft=bool(self.draft))
        if action in ("reference", "compare", "draft", "match") and self.job is not None:
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
            self.compare()
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
        if action in ("create", "refine"):
            prior = self.draft if action == "refine" else None
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
                self.thinking = self.advisor.submit(advisor.ask, value, take, self.values)
        self.update()

    def answered(self, result):
        if self.intent in ("create", "refine"):
            old_alternative = self.alternative
            self.stop_audio()
            self.last_heard = None
            self.draft = result
            self.drafts[result["id"]] = result
            if old_alternative and old_alternative.draft_id and old_alternative.draft_id == result.get("parent"):
                self.baseline = old_alternative
            elif self.take:
                self.baseline = self.take.original
            self.alternative = None
            self.adjustment = {}
            self.set_status(f"{result['target']} · {result['name']} is ready to try.")
        else:
            reply, knobs = result
            if knobs and self.task_if_active("live"):
                self.values.update(knobs)
                self.control_mail.put(dict(self.values))
            elif knobs:
                self.stop_audio()
                self.last_heard = None
                self.adjustment = knobs
                self.draft = self.alternative = None
                if self.take:
                    self.baseline = self.take.original
            self.set_status(reply)
        self.remember()

    def list_details(self):
        self.details.delete(0, tk.END)
        if self.draft:
            recipe = self.draft
            self.details.add_command(label=f"Compare {recipe['name']}", command=self.restore_draft_comparison)
            self.details.add_command(label="Why this sound?", command=lambda: self.set_status(recipe["explanation"]))
            self.details.add_command(label="Show signal chain", command=lambda: self.set_status(preset_design.chain_text(recipe)))
            for source in recipe["sources"]:
                self.details.add_command(label=source["title"][:70], command=lambda url=source["url"]: webbrowser.open(url))
            self.details.add_separator()
            saved = self.read_state(session.SESSIONS / "drafts" / f"{recipe['id']}.kept.json").get("label")
            if saved:
                self.details.add_command(label=f"Saved on QC · {saved}", state="disabled")
            else:
                self.details.add_command(label="Save a copy to My Presets", command=self.save)
        if self.reference_choice and self.take:
            for scene_index, scene in enumerate(device.SCENES):
                self.details.add_command(label=f"Capture reference scene {scene}",
                                         command=lambda index=scene_index: self.use_preset(self.reference_choice, index))
        if not self.draft and not self.reference_choice:
            self.details.add_command(label="Choose a sound to see its details", state="disabled")

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
        draft = self.draft
        proof = self.alternative.folder if self.alternative and self.alternative.draft_id == draft["id"] else None
        self.submit("save", "Saving a copy in an empty Quad Cortex slot…", draft_render.save, draft, proof,
                    physical_input=self.input_channel, done=self.saved)

    def saved(self, location):
        self.presets = None
        self.set_status(f"Saved · {location}")

    def try_guitar(self):
        if not self.draft or self.job is not None:
            return
        self.stop_signal = Event()
        self.control_mail = Queue()
        self.values = {}
        self.live_draft = self.draft
        proof = self.alternative.folder if self.alternative and self.alternative.draft_id == self.draft["id"] else None
        gain = self.read_state(proof / "session.json").get("output_gain_db") if proof else None
        self.submit("live", f"Loading {self.draft['name']} temporarily…", draft_render.try_guitar,
                    self.draft, self.input_channel, gain, self.stop_signal, self.control_mail, self.live_messages.put,
                    done=self.restored)

    def restored(self, _):
        self.set_status("Your previous Quad Cortex preset and scene are restored.")
        self.check_qc()

    @staticmethod
    def read_qc():
        with device.connect() as qc:
            return device.read_preset(qc).name, device.SCENES[device.active_scene(qc)]

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
            if self.task_if_active("live"):
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
                self.qc_name, scene = self.check.result()
                if not self.task_if_active("live"):
                    self.hardware.set(f"QC last seen · {self.qc_name} · {scene}")
            except device.EditorActive:
                self.qc_name = None
                self.hardware.set("Cortex Control open · device controls paused")
            except Exception:
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
            self.record.configure(text="Record phrase")
            try:
                self.outcome(past.result())
            except device.EditorActive as error:
                if task != "browse" or not self.preset_query or not self.use_available_reference(self.preset_query):
                    self.set_status(str(error))
                self.qc_name = None
                self.hardware.set("Cortex Control open · device controls paused")
            except Exception as error:
                diagnostics.LOG.exception("Listening desk operation failed")
                self.set_status(str(error))
                if task in ("live", "render", "reference", "save", "load"):
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
                if isinstance(message, str) and self.task_if_active("live") and not self.stop_signal.is_set():
                    self.hardware.set(f"Quad Cortex · previewing {self.live_draft['name']}")
                    self.set_status("Play your guitar through the draft. Press Esc to restore the previous sound.")
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
        cursor = width * loop.cursor / len(loop.tracks[0]) if loop and self.playing else 0
        self.playhead.coords(self.position, 0, 0, cursor, 4)
        if self.leaving is not None and self.job is None and self.play_job is None and self.check is None:
            self.finish_close(self.leaving)
            return
        self.root.after(65, self.tick)

    def close(self, destination):
        self.leaving = destination or "exit"
        self.stop()
        if self.job is None and self.play_job is None and self.check is None:
            self.finish_close(self.leaving)

    def finish_close(self, destination):
        self.worker.shutdown(wait=True)
        self.advisor.shutdown(wait=False, cancel_futures=True)
        self.leave(destination)
