"""Live controls and the tone advisor in the main Tone Lab window."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import math
from queue import Empty, Queue, SimpleQueue
import re
from threading import Event
import tkinter as tk

from . import advisor, diagnostics, draft_render, live_client, preset_design, styles
from .draft_ui import DraftCard
from .widgets import ACCENT, FONT, INK, LINE, MUTED, PANEL, RECESS, TEXT, button, card, entry, label


class Knob(tk.Canvas):
    def __init__(self, parent, name, minimum, on_change, *, colors=None):
        self.colors = colors or (RECESS, ACCENT, TEXT, MUTED, LINE, "#34433c", "#27332c")
        surface, accent, *_ = self.colors
        super().__init__(parent, width=112, height=92, background=surface, highlightthickness=1,
                         highlightbackground=surface, highlightcolor=accent, takefocus=True)
        self.name, self.minimum, self.on_change = name, minimum, on_change
        self.value = 0.0
        self.bind("<Button-1>", self.begin)
        self.bind("<B1-Motion>", lambda event: self.turn(self.start_value + (self.start_y - event.y) / 75))
        self.bind("<MouseWheel>", lambda event: self.turn(self.value + (.05 if event.delta > 0 else -.05)))
        self.bind("<Up>", lambda _: self.turn(self.value + .05))
        self.bind("<Right>", lambda _: self.turn(self.value + .05))
        self.bind("<Down>", lambda _: self.turn(self.value - .05))
        self.bind("<Left>", lambda _: self.turn(self.value - .05))
        self.bind("<Home>", lambda _: self.turn(0))
        self.paint()

    def begin(self, event):
        self.focus_set()
        self.start_y, self.start_value = event.y, self.value

    def set(self, value):
        self.value = round(min(1, max(self.minimum, value)), 2)
        self.paint()

    def turn(self, value):
        old = self.value
        self.set(value)
        if old != self.value:
            self.on_change(self.name)

    def paint(self):
        self.delete("all")
        _, accent, text, muted, line, track, dial = self.colors
        fraction = (self.value - self.minimum) / (1 - self.minimum)
        origin = (0 - self.minimum) / (1 - self.minimum)
        ring = (23, 10, 89, 76)
        self.create_arc(*ring, start=225, extent=-270, style="arc", outline=track, width=3)
        if fraction != origin:
            self.create_arc(*ring, start=225 - 270 * origin, extent=-270 * (fraction - origin),
                            style="arc", outline=accent, width=3)
        self.create_oval(32, 19, 80, 67, fill=dial, outline=line)
        angle = math.radians(225 - 270 * fraction)
        self.create_line(56 + 12 * math.cos(angle), 43 - 12 * math.sin(angle),
                         56 + 18 * math.cos(angle), 43 - 18 * math.sin(angle), fill=accent, width=3)
        percent = round(self.value * 100)
        display = f"{percent:+d}%" if percent and self.minimum < 0 else f"{percent}%"
        self.create_text(56, 84, text=display, fill=text if percent else muted,
                         font=(FONT, 9, "bold"))


class LivePanel:
    def __init__(self, review, parent, transport):
        self.review, self.root = review, review.root
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tone-advisor")
        self.future = self.ai_future = self.voice_future = None
        self.close_callback = None
        self.after_stop = None
        self.before_draft = None
        self.keep_request = None
        self.mode = None
        self.available_controls = None
        self.suggestion = {}
        self.target_draft = False
        self.designing = False
        self.history = deque(maxlen=4)
        self.status = tk.StringVar()
        self.hint = tk.StringVar(value="Drag or scroll a knob. Home resets it.")
        self.reply = tk.StringVar()
        self.text = tk.StringVar()
        self.last_reply = ""

        self.guitar = button(transport, "Live guitar", lambda: self.start("guitar"), compact=True)
        self.reamp = button(transport, "Loop take", lambda: self.start("reamp"), compact=True)
        self.stop_button = button(transport, "Stop live", self.stop, compact=True)
        for control in (self.guitar, self.reamp, self.stop_button):
            control.pack(side="left", padx=(0, 6))

        self.build_advisor(parent)
        controls = self.controls = card(parent)
        controls.pack(fill="x", pady=(0, 12))
        head = tk.Frame(controls, bg=PANEL)
        head.pack(fill="x")
        label(head, "Tone controls", size=16, strong=True).pack(side="left")
        button(head, "Reset", self.reset, compact=True).pack(side="right")
        self.keep_button = button(head, "Save A/B", self.keep, compact=True)
        self.keep_button.pack(side="right", padx=(0, 7))
        self.more = button(head, "More controls", self.toggle_groups, compact=True)
        self.more.pack(side="right", padx=(0, 7))

        self.groups = {"Essentials": getattr(styles, "PRIMARY_KNOBS", tuple(styles.KNOBS)),
                       **getattr(styles, "KNOB_GROUPS", {})}
        self.group_row = tk.Frame(controls, bg=PANEL)
        self.group_buttons = {}
        for group in self.groups:
            tab = button(self.group_row, group, lambda key=group: self.show_group(key), compact=True)
            tab.pack(side="left", padx=(0, 5))
            self.group_buttons[group] = tab
        if len(self.groups) == 1:
            self.more.pack_forget()

        self.knob_row = tk.Frame(controls, bg=PANEL)
        self.knob_row.pack(fill="x", pady=(13, 7))
        self.knobs, self.tiles = {}, {}
        for key, (title, minimum, _) in styles.KNOBS.items():
            tile = tk.Frame(self.knob_row, background=RECESS, highlightthickness=1,
                            highlightbackground=LINE, padx=4, pady=6)
            label(tile, title, size=11, strong=True).pack()
            knob = Knob(tile, key, minimum, self.changed)
            knob.pack()
            knob.bind("<Enter>", lambda _, name=key: self.focus_hint(name))
            knob.bind("<FocusIn>", lambda _, name=key: self.focus_hint(name))
            self.tiles[key], self.knobs[key] = tile, knob
        self.show_group("Essentials")
        label(controls, variable=self.hint, color=MUTED, wrap=530).pack(anchor="w")

        self.drafts = DraftCard(self, parent)
        self.update_buttons()

    def build_advisor(self, parent):
        advice = card(parent, padding=12)
        advice.pack(fill="x", pady=(0, 10))
        heading = tk.Frame(advice, bg=PANEL)
        heading.pack(fill="x")
        label(heading, "What do you want to play?", size=17, strong=True).pack(side="left")
        label(heading, "Win+H to dictate", size=9, color=MUTED).pack(side="right")
        request = tk.Frame(advice, bg=PANEL)
        request.pack(fill="x", pady=(9, 5))
        self.field = entry(request, self.text)
        self.field.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 7))
        self.field.bind("<Return>", lambda _: self.ask())
        self.ask_button = button(request, "Ask Tone Lab", self.ask, accent=True, compact=True)
        self.ask_button.pack(side="right")
        shortcuts = tk.Frame(advice, bg=PANEL)
        shortcuts.pack(fill="x", pady=(1, 0))
        self.target_button = tk.Menubutton(shortcuts, text="Adjust live tone", indicatoron=True,
                                           takefocus=True, font=(FONT, 10, "bold"),
                                           background="#28332d", foreground=TEXT, padx=8, pady=6)
        targets = tk.Menu(self.target_button, tearoff=False, font=(FONT, 10))
        self.target_button.configure(menu=targets)
        targets.add_command(label="Refine the draft", command=lambda: self.set_target(True))
        targets.add_command(label="Adjust the current QC tone", command=lambda: self.set_target(False))
        self.target_button.pack(side="right")
        button(shortcuts, "Choose a preset", lambda: self.review.show_view("presets"), compact=True).pack(side="left")
        self.example = button(shortcuts, "Try an artist request",
                              lambda: self.compose("Make me a Lenny Kravitz preset"), compact=True)
        self.example.pack(side="left", padx=(5, 0))
        self.reply_label = label(advice, variable=self.reply, color=MUTED, wrap=530)
        advice.bind("<Configure>", lambda event: self.reply_label.configure(wraplength=max(100, event.width - 34)))
        output = tk.Frame(advice, bg=PANEL)
        self.output = output
        self.apply_button = button(output, "Apply suggestions", self.apply, compact=True)
        self.speak_button = button(output, "Speak reply", self.speak, compact=True)
        self.reply.trace_add("write", lambda *_: self.show_reply())

    def show_reply(self):
        if self.reply.get():
            if not self.reply_label.winfo_manager():
                self.reply_label.pack(anchor="w", pady=(7, 0), before=self.output
                                      if self.output.winfo_manager() else None)
        else:
            self.reply_label.pack_forget()

    def compose(self, text):
        self.text.set(text)
        self.field.focus_set()
        self.field.icursor("end")

    def restore_draft(self):
        if self.drafts.versions:
            draft = self.drafts.versions[0]
            self.drafts.show(draft)
            self.last_reply = draft["explanation"]
            self.reply.set("")
            self.target_draft = True
            self.designing = True
            self.update_buttons()

    def set_target(self, draft):
        self.target_draft = draft
        self.designing = draft
        self.suggestion = {}
        self.last_reply = ""
        if self.target_draft:
            self.reply.set(f"Describe a change to {self.drafts.draft['name']}.")
        else:
            self.reply.set("Describe a change to your current Quad Cortex tone.")
        self.field.focus_set()
        self.update_buttons()

    def focus_hint(self, name):
        if self.available_controls is not None and name not in self.available_controls:
            self.hint.set("This preset has no active block for that control.")
            return
        description = getattr(styles, "KNOB_HINTS", {}).get(name)
        self.hint.set(description or "Drag or scroll a knob. Home resets it.")

    def toggle_groups(self):
        if self.group_row.winfo_manager():
            self.group_row.pack_forget()
            self.show_group("Essentials")
            self.more.configure(text="More controls")
        else:
            self.group_row.pack(fill="x", pady=(10, 0), before=self.knob_row)
            self.more.configure(text="Fewer controls")

    def show_group(self, group):
        for tile in self.tiles.values():
            tile.grid_remove()
        for index, name in enumerate(self.groups[group]):
            row, col = divmod(index, 4)
            self.knob_row.columnconfigure(col, weight=1, uniform="knobs")
            self.tiles[name].grid(row=row, column=col, sticky="ew", padx=(0, 6), pady=(0, 6))
        for name, tab in self.group_buttons.items():
            tab.configure(background=ACCENT if name == group else "#28332d",
                          foreground=INK if name == group else TEXT)
        self.hint.set("Drag or scroll a knob. Home resets it.")

    def values(self):
        return {name: knob.value for name, knob in self.knobs.items()}

    def changed(self, name=None):
        if name:
            self.focus_hint(name)
        if self.future is not None:
            self.commands.put(self.values())

    def reset(self):
        for knob in self.knobs.values():
            knob.set(0)
        self.changed()

    def update_buttons(self):
        active = self.future is not None
        available = not active and self.review.pending is None and self.review.recorder.stop is None
        self.reamp.configure(state="normal" if available and self.review.take_info.get("dry") else "disabled")
        self.guitar.configure(state="normal" if available else "disabled")
        self.stop_button.configure(state="normal" if active and not self.stop_signal.is_set() else "disabled")
        self.keep_button.configure(state="normal" if self.mode != "draft"
                                   and self.review.take_info.get("wet") and self.review.take_info.get("dry")
                                   and self.review.pending is None
                                   and self.review.recorder.stop is None and self.keep_request is None
                                   and (not active or not self.stop_signal.is_set()) else "disabled")
        self.ask_button.configure(state="normal" if self.ai_future is None else "disabled")
        if self.drafts.draft:
            self.example.pack_forget()
            self.target_button.configure(text="Refining draft" if self.target_draft else "Adjusting live tone",
                                         state="normal" if self.ai_future is None else "disabled")
            self.target_button.pack(side="right")
        else:
            self.target_button.pack_forget()
        if not self.designing and (self.suggestion or self.last_reply):
            self.output.pack(fill="x", pady=(7, 0))
        else:
            self.output.pack_forget()
        if self.suggestion:
            self.apply_button.pack(side="left", padx=(0, 5), before=self.speak_button
                                   if self.speak_button.winfo_manager() else None)
        else:
            self.apply_button.pack_forget()
        if self.last_reply:
            self.speak_button.pack(side="left")
        else:
            self.speak_button.pack_forget()
        self.apply_button.configure(text=("Apply live" if active else
                                         "Compare on this take" if self.review.can_render else "Set tone controls"),
                                    state="normal" if self.suggestion and (active or available) else "disabled")
        self.speak_button.configure(state="normal" if self.last_reply and self.voice_future is None else "disabled")
        self.drafts.update()

    def start(self, mode):
        if ((mode == "reamp" and not self.review.take_info.get("dry")) or self.future is not None or self.review.pending is not None
                or self.review.recorder.stop is not None):
            return
        try:
            guitar_config = self.review.guitar_inputs.live_config() if mode == "guitar" else None
        except (OSError, ValueError) as error:
            self.status.set(str(error))
            return
        self.prepare_live(mode, "Loading the Quad Cortex test copy…")
        self.future = self.review.recorder.worker.submit(live_client.run, self.review.take, mode, self.stop_signal,
                                                         self.commands, self.reports.put, guitar_config=guitar_config)
        self.started()

    def start_draft(self, draft, gain):
        if self.review.busy():
            return
        self.before_draft = self.values()
        self.reset()
        self.prepare_live("draft", "Loading the draft for live guitar…")
        self.future = self.review.recorder.worker.submit(
            draft_render.try_guitar, draft, self.review.recorder.input_channel, gain, self.stop_signal,
            self.commands, self.reports.put)
        self.started()

    def prepare_live(self, mode, status):
        self.mode = mode
        self.review.stop()
        self.stop_signal = Event()
        self.commands = Queue()
        self.reports = SimpleQueue()
        self.commands.put(self.values())
        self.status.set(status)

    def started(self):
        self.review.recorder.button.configure(state="disabled")
        self.review.update_buttons()
        self.root.after(100, self.poll)

    def stop(self):
        if self.future is not None:
            self.stop_signal.set()
            self.status.set("Restoring the original preset…")
            self.update_buttons()

    def keep(self):
        if (not self.review.take_info.get("dry") or not self.review.take_info.get("wet")
                or self.review.pending is not None or self.review.recorder.stop is not None):
            return
        request = (self.review.take, self.values())
        if self.future is not None:
            self.keep_request = request
            self.stop()
        else:
            self.review.render_knobs(*request)

    def poll(self):
        if not self.stop_signal.is_set():
            try:
                report = self.reports.get_nowait()
                if isinstance(report, dict):
                    if report["event"] == "controls":
                        self.available_controls = set(report["available"])
                    else:
                        self.review.guitar_inputs.live_event(report)
                        if report.get("applied") and report["control"] in self.knobs:
                            self.knobs[report["control"]].set(report["value"])
                else:
                    self.status.set(report)
            except Empty:
                pass
        if not self.future.done():
            self.root.after(100, self.poll)
            return
        success = False
        try:
            self.future.result()
        except Exception as error:
            diagnostics.LOG.exception("Live control failed")
            self.status.set(str(error))
        else:
            self.status.set("Original preset restored")
            success = True
        self.future = None
        self.mode = None
        if self.before_draft is not None:
            for key, value in self.before_draft.items():
                self.knobs[key].set(value)
            self.before_draft = None
        self.available_controls = None
        self.review.guitar_inputs.live_event({"event": "guitar-stopped"})
        keep, self.keep_request = self.keep_request, None
        self.review.recorder.button.configure(state="normal")
        after_stop, self.after_stop = self.after_stop, None
        self.review.update_buttons()
        if self.close_callback:
            callback, self.close_callback = self.close_callback, None
            callback()
        elif success and keep is not None:
            self.review.render_knobs(*keep)
        elif success and after_stop is not None:
            after_stop()

    def stop_for(self, action):
        if self.future is not None and not self.stop_signal.is_set():
            self.after_stop = action
            self.stop()
            return True
        return False

    def ask(self):
        text = self.text.get().strip()
        if not text or self.ai_future is not None:
            return
        self.request = text
        self.text.set("")
        self.suggestion = {}
        if self.target_draft:
            command = re.sub(r"[^a-z ]", "", text.casefold()).strip()
            action = {
                "hear this draft": self.drafts.listen,
                "compare this draft": self.drafts.hear,
                "play this draft": self.drafts.try_live,
                "save this preset": self.drafts.save,
            }.get(command)
            if action is not None:
                if action.cget("state") == "normal":
                    action.invoke()
                else:
                    self.reply.set("Check the draft card for the available actions.")
                self.update_buttons()
                return
        preset_query = re.fullmatch(r"(?i)(?:use|find|load|show)\s+(?:my\s+)?(?:preset\s+)?(.+?)"
                                    r"(?:\s+preset)?(?:\s+as\s+(?:a\s+)?reference)?[.!]?", text)
        if preset_query and re.search(r"(?i)\b(?:preset|reference)\b", text):
            action = ("reference" if re.search(r"(?i)\bas\s+(?:a\s+)?reference\b", text)
                      else "load" if re.match(r"(?i)^load\b", text) else None)
            self.review.presets.find_named(preset_query[1], action)
            self.reply.set("Choose a saved preset to load it or use it as your comparison reference.")
            self.update_buttons()
            return
        is_new = preset_design.is_request(text)
        prior = self.drafts.draft if self.target_draft and not is_new else None
        if prior and re.search(r"(?i)^(?:why|what|how|which|can\s+i|is)\b", text):
            prior = None
        self.designing = is_new or prior is not None
        context = {key: value for key, value in self.review.take_info.items()
                   if key != "pickup_note" or self.review.take_info.get("guitar") == self.review.guitar.get()}
        context["guitar"] = self.review.guitar.get()
        direction = styles.intent(text) if not self.designing else None
        if direction:
            key, step = {"more jazz": ("jazz", .4), "less delay": ("delay", -.3),
                         "darker": ("warmth", .35)}[direction]
            if self.available_controls is not None and key not in self.available_controls:
                self.reply.set(f"The active preset has no block for {styles.KNOBS[key][0].lower()}.")
            else:
                knob = self.knobs[key]
                value = round(max(knob.minimum, min(1.0, knob.value + step)), 2)
                if self.future is not None:
                    knob.turn(value)
                    self.reply.set(f"{styles.KNOBS[key][0]} set to {value:.0%} on the live preset.")
                else:
                    self.suggestion = {key: value}
                    self.reply.set(f"Set {styles.KNOBS[key][0].lower()} to {value:.0%} to hear the change.")
            self.update_buttons()
            return
        if self.designing:
            self.reply.set("Refining the draft…" if prior else "Researching and building a preset…")
            note = self.review.listening_note()
            self.ai_future = self.worker.submit(preset_design.design, text, context["guitar"], context, prior, note)
        else:
            self.reply.set("Thinking about your tone…")
            self.ai_future = self.worker.submit(advisor.ask, text, context, self.values(), tuple(self.history))
        self.update_buttons()
        self.root.after(100, self.poll_reply)

    def poll_reply(self):
        if not self.ai_future.done():
            self.root.after(100, self.poll_reply)
            return
        try:
            result = self.ai_future.result()
        except Exception as error:
            diagnostics.LOG.exception("Tone advisor failed")
            self.reply.set(str(error))
            if not self.text.get():
                self.text.set(self.request)
        else:
            if self.designing:
                self.drafts.show(result, add=True)
                self.target_draft = True
                self.last_reply = result["explanation"]
            else:
                self.last_reply, self.suggestion = result
            self.history.extend([{"user": self.request}, {"advisor": self.last_reply}])
            self.reply.set("" if self.designing else self.last_reply)
        self.ai_future = None
        self.update_buttons()

    def apply(self):
        for name, value in self.suggestion.items():
            if name in self.knobs:
                self.knobs[name].set(value)
        self.suggestion = {}
        self.changed()
        self.update_buttons()
        if not self.review.busy() and self.review.can_render:
            self.review.render_knobs(self.review.take, self.values())

    def speak(self):
        self.voice_future = self.worker.submit(advisor.speak, self.last_reply)
        self.update_buttons()
        self.root.after(100, self.poll_voice)

    def poll_voice(self):
        if not self.voice_future.done():
            self.root.after(100, self.poll_voice)
            return
        try:
            self.voice_future.result()
        except Exception:
            diagnostics.LOG.exception("Voice output failed")
            self.reply.set("Windows could not play the spoken reply. " + self.last_reply)
        self.voice_future = None
        self.update_buttons()

    def stop_and_close(self, callback):
        self.close_callback = callback
        self.stop()

    def shutdown(self):
        self.worker.shutdown(wait=False, cancel_futures=True)
