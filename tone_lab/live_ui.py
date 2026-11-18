"""Live controls and the tone advisor in the main Tone Lab window."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import math
from queue import Empty, Queue, SimpleQueue
from threading import Event
import tkinter as tk

from . import advisor, diagnostics, live_client, styles
from .widgets import ACCENT, FONT, INK, LINE, MUTED, PANEL, RECESS, TEXT, button, card, entry, label


class Knob(tk.Canvas):
    def __init__(self, parent, name, minimum, on_change):
        super().__init__(parent, width=112, height=92, background=RECESS, highlightthickness=1,
                         highlightbackground=RECESS, highlightcolor=ACCENT, takefocus=True)
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
        fraction = (self.value - self.minimum) / (1 - self.minimum)
        origin = (0 - self.minimum) / (1 - self.minimum)
        ring = (23, 10, 89, 76)
        self.create_arc(*ring, start=225, extent=-270, style="arc", outline="#34433c", width=3)
        if fraction != origin:
            self.create_arc(*ring, start=225 - 270 * origin, extent=-270 * (fraction - origin),
                            style="arc", outline=ACCENT, width=3)
        self.create_oval(32, 19, 80, 67, fill="#27332c", outline=LINE)
        angle = math.radians(225 - 270 * fraction)
        self.create_line(56 + 12 * math.cos(angle), 43 - 12 * math.sin(angle),
                         56 + 18 * math.cos(angle), 43 - 18 * math.sin(angle), fill=ACCENT, width=3)
        percent = round(self.value * 100)
        display = f"{percent:+d}%" if percent and self.minimum < 0 else f"{percent}%"
        self.create_text(56, 84, text=display, fill=TEXT if percent else MUTED,
                         font=(FONT, 9, "bold"))


class LivePanel:
    def __init__(self, review, parent, transport):
        self.review, self.root = review, review.root
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tone-advisor")
        self.future = self.ai_future = self.voice_future = None
        self.close_callback = None
        self.keep_request = None
        self.available_controls = None
        self.suggestion = {}
        self.history = deque(maxlen=4)
        self.status = tk.StringVar()
        self.hint = tk.StringVar(value="Drag or scroll a knob. Home resets it.")
        self.reply = tk.StringVar(value="Describe the sound you want. Use Win+H to dictate.")
        self.text = tk.StringVar()
        self.last_reply = ""

        self.guitar = button(transport, "Live guitar", lambda: self.start("guitar"), compact=True)
        self.reamp = button(transport, "Loop take", lambda: self.start("reamp"), compact=True)
        self.stop_button = button(transport, "Stop live", self.stop, compact=True)
        for control in (self.guitar, self.reamp, self.stop_button):
            control.pack(side="left", padx=(0, 6))

        controls = card(parent)
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

        advice = card(parent)
        advice.pack(fill="x")
        label(advice, "Tone advisor", size=15, strong=True).pack(anchor="w")
        request = tk.Frame(advice, bg=PANEL)
        request.pack(fill="x", pady=(10, 5))
        field = entry(request, self.text)
        field.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 7))
        field.bind("<Return>", lambda _: self.ask())
        self.ask_button = button(request, "Ask AI", self.ask, accent=True, compact=True)
        self.ask_button.pack(side="right")
        label(advice, variable=self.reply, color=MUTED, wrap=530).pack(anchor="w", pady=(3, 0))
        output = tk.Frame(advice, bg=PANEL)
        output.pack(fill="x", pady=(9, 0))
        self.apply_button = button(output, "Use suggested knobs", self.apply, compact=True)
        self.apply_button.pack(side="left", padx=(0, 6))
        self.speak_button = button(output, "Speak reply", self.speak, compact=True)
        self.speak_button.pack(side="left")
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
        self.keep_button.configure(state="normal" if self.review.take_info.get("wet") and self.review.take_info.get("dry")
                                   and self.review.pending is None
                                   and self.review.recorder.stop is None and self.keep_request is None
                                   and (not active or not self.stop_signal.is_set()) else "disabled")
        self.ask_button.configure(state="normal" if self.ai_future is None else "disabled")
        self.apply_button.configure(state="normal" if self.suggestion else "disabled")
        self.speak_button.configure(state="normal" if self.last_reply and self.voice_future is None else "disabled")

    def start(self, mode):
        if ((mode == "reamp" and not self.review.take_info.get("dry")) or self.future is not None or self.review.pending is not None
                or self.review.recorder.stop is not None):
            return
        self.review.stop()
        self.stop_signal = Event()
        self.commands = Queue()
        self.reports = SimpleQueue()
        self.commands.put(self.values())
        self.status.set("Loading the Quad Cortex test copy…")
        self.future = self.review.recorder.worker.submit(live_client.run, self.review.take, mode, self.stop_signal,
                                                         self.commands, self.reports.put)
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
                    self.available_controls = set(report["available"])
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
        self.available_controls = None
        keep, self.keep_request = self.keep_request, None
        self.review.recorder.button.configure(state="normal")
        self.review.update_buttons()
        if self.close_callback:
            callback, self.close_callback = self.close_callback, None
            callback()
        elif success and keep is not None:
            self.review.render_knobs(*keep)

    def ask(self):
        text = self.text.get().strip()
        if not text or self.ai_future is not None:
            return
        self.request = text
        self.text.set("")
        self.suggestion = {}
        self.reply.set("Asking the tone advisor…")
        self.ai_future = self.worker.submit(advisor.ask, text, self.review.take_info, self.values(), tuple(self.history))
        self.update_buttons()
        self.root.after(100, self.poll_reply)

    def poll_reply(self):
        if not self.ai_future.done():
            self.root.after(100, self.poll_reply)
            return
        try:
            self.last_reply, self.suggestion = self.ai_future.result()
        except Exception as error:
            diagnostics.LOG.exception("Tone advisor failed")
            self.reply.set(str(error))
            if not self.text.get():
                self.text.set(self.request)
        else:
            self.history.extend([{"user": self.request}, {"advisor": self.last_reply}])
            self.reply.set(self.last_reply)
        self.ai_future = None
        self.update_buttons()

    def apply(self):
        for name, value in self.suggestion.items():
            if name in self.knobs:
                self.knobs[name].set(value)
        self.suggestion = {}
        self.changed()
        self.update_buttons()

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
