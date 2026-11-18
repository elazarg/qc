"""Rotary style controls, direct monitoring, and the tone advisor."""

from concurrent.futures import ThreadPoolExecutor
import math
from queue import Empty, Queue, SimpleQueue
from threading import Event
import tkinter as tk

from . import advisor, diagnostics, live_client, styles

BG = "#151a1d"
TEXT = "#f0f1e8"
MUTED = "#b8c3bc"
ACCENT = "#d3ec9c"


class Knob(tk.Canvas):
    def __init__(self, parent, label, minimum, change):
        super().__init__(parent, width=100, height=112, background=BG, highlightthickness=1,
                         highlightbackground=BG, highlightcolor=ACCENT, takefocus=True)
        self.label, self.minimum, self.change = label, minimum, change
        self.value = 0.0
        self.bind("<Button-1>", self.begin)
        self.bind("<B1-Motion>", lambda event: self.turn(self.start_value + (self.start_y - event.y) / 70))
        self.bind("<MouseWheel>", lambda event: self.turn(self.value + (.05 if event.delta > 0 else -.05)))
        self.bind("<Up>", lambda _: self.turn(self.value + .05))
        self.bind("<Down>", lambda _: self.turn(self.value - .05))
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
            self.change()

    def paint(self):
        self.delete("all")
        fraction = (self.value - self.minimum) / (1 - self.minimum)
        self.create_arc(20, 12, 80, 72, start=225, extent=-270, style="arc", outline="#556463", width=3)
        if fraction:
            self.create_arc(20, 12, 80, 72, start=225, extent=-270 * fraction,
                            style="arc", outline=ACCENT, width=3)
        self.create_oval(27, 19, 73, 65, fill="#283130", outline="")
        angle = math.radians(225 - 270 * fraction)
        self.create_line(50, 42, 50 + 18 * math.cos(angle), 42 - 18 * math.sin(angle), fill=ACCENT, width=3)
        self.create_text(50, 84, text=self.label, fill=TEXT, font=("Segoe UI Semibold", 11))
        pct = round(self.value * 100)
        self.create_text(50, 103, text=f"{pct:+d}%" if pct and self.minimum < 0 else f"{pct}%",
                         fill=MUTED, font=("Segoe UI", 9))


class LivePanel:
    def __init__(self, review):
        self.review = review
        self.window = tk.Toplevel(review.root)
        self.window.title("Tone Lab Live")
        self.window.configure(background=BG, padx=20, pady=16)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tone-advisor")
        self.future = self.ai_future = self.voice_future = None
        self.close_after = False
        self.on_close = None
        self.suggestion = {}
        self.history = []
        self.status = tk.StringVar(value="Drag a knob up or down, or use the arrow keys.")
        self.reply = tk.StringVar(value="Dictate in the box with Win+H, or type a request.")
        self.text = tk.StringVar()

        dial_row = self.row()
        self.knobs = {name: Knob(dial_row, label, minimum, self.changed)
                      for name, (label, minimum, _) in styles.KNOBS.items()}
        for knob in self.knobs.values():
            knob.pack(side="left")
        controls = self.row(6)
        self.reamp = self.button(controls, "Loop recorded take", lambda: self.start("reamp"))
        self.guitar = self.button(controls, "Live guitar", lambda: self.start("guitar"))
        self.stop_button = self.button(controls, "Stop live", self.stop)
        self.button(controls, "Reset knobs", self.reset)
        self.label(self.status)

        tk.Label(self.window, text="Tone advisor", background=BG, foreground=TEXT,
                 font=("Segoe UI Semibold", 13)).pack(anchor="w", pady=(15, 4))
        request = self.row()
        entry = tk.Entry(request, textvariable=self.text, width=36, relief="flat", background="#26302f",
                         foreground=TEXT, insertbackground=TEXT, font=("Segoe UI", 11))
        entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        entry.bind("<Return>", lambda _: self.ask())
        self.ask_button = self.button(request, "Ask AI", self.ask)
        self.label(self.reply)
        output = self.row(6)
        self.apply_button = self.button(output, "Use suggested knobs", self.apply)
        self.speak_button = self.button(output, "Speak reply", self.speak)
        self.last_reply = ""
        self.update_buttons()

    def row(self, space=0):
        frame = tk.Frame(self.window, background=BG)
        frame.pack(fill="x", pady=(space, 0), anchor="w")
        return frame

    @staticmethod
    def button(parent, text, command):
        result = tk.Button(parent, text=text, command=command, relief="flat", background="#354342",
                           foreground=TEXT, activebackground="#495955", activeforeground=TEXT,
                           disabledforeground="#949e98", font=("Segoe UI", 10), padx=8, pady=5)
        result.pack(side="left", padx=(0, 5))
        return result

    def label(self, text):
        tk.Label(self.window, textvariable=text, background=BG, foreground=MUTED,
                 font=("Segoe UI", 10), wraplength=400, justify="left").pack(anchor="w", pady=(6, 0))

    def values(self):
        return {name: knob.value for name, knob in self.knobs.items()}

    def changed(self):
        if self.future is not None:
            self.commands.put(self.values())

    def reset(self):
        for knob in self.knobs.values():
            knob.set(0)
        self.changed()

    def update_buttons(self):
        active = self.future is not None
        available = self.review.can_render and not active and self.review.pending is None and self.review.recorder.stop is None
        self.reamp.configure(state="normal" if available else "disabled")
        self.guitar.configure(state="normal" if available else "disabled")
        self.stop_button.configure(state="normal" if active and not self.stop_signal.is_set() else "disabled")
        self.ask_button.configure(state="normal" if self.ai_future is None else "disabled")
        self.apply_button.configure(state="normal" if self.suggestion else "disabled")
        self.speak_button.configure(state="normal" if self.last_reply and self.voice_future is None else "disabled")

    def start(self, mode):
        if self.future is not None or self.review.pending is not None or self.review.recorder.stop is not None:
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
        self.update_buttons()
        self.window.after(100, self.poll)

    def stop(self):
        if self.future is not None:
            self.stop_signal.set()
            self.status.set("Stopping live control…")
            self.update_buttons()

    def poll(self):
        if not self.stop_signal.is_set():
            try:
                self.status.set(self.reports.get_nowait())
            except Empty:
                pass
        if not self.future.done():
            self.window.after(100, self.poll)
            return
        try:
            self.future.result()
        except Exception as error:
            diagnostics.LOG.exception("Live control failed")
            self.status.set(str(error))
        else:
            self.status.set("Stopped · original preset restored")
        self.future = None
        self.review.recorder.button.configure(state="normal")
        self.review.update_buttons()
        self.update_buttons()
        if self.close_after:
            self.close(self.on_close)

    def ask(self):
        text = self.text.get().strip()
        if not text or self.ai_future is not None:
            return
        try:
            info = self.review.latest(pair=True)
        except (OSError, ValueError):
            info = {}
        self.request = text
        self.text.set("")
        self.suggestion = {}
        self.reply.set("Asking the tone advisor…")
        self.ai_future = self.worker.submit(advisor.ask, text, info, self.values(), tuple(self.history))
        self.update_buttons()
        self.window.after(100, self.poll_reply)

    def poll_reply(self):
        if not self.ai_future.done():
            self.window.after(100, self.poll_reply)
            return
        try:
            self.last_reply, self.suggestion = self.ai_future.result()
        except Exception as error:
            diagnostics.LOG.exception("Tone advisor failed")
            self.reply.set(str(error))
        else:
            self.history.extend([{"user": self.request}, {"advisor": self.last_reply}])
            self.reply.set(self.last_reply)
        self.ai_future = None
        self.update_buttons()

    def apply(self):
        for name, value in self.suggestion.items():
            self.knobs[name].set(value)
        self.suggestion = {}
        self.changed()
        self.update_buttons()

    def speak(self):
        self.voice_future = self.worker.submit(advisor.speak, self.last_reply)
        self.update_buttons()
        self.window.after(100, self.poll_voice)

    def poll_voice(self):
        if not self.voice_future.done():
            self.window.after(100, self.poll_voice)
            return
        try:
            self.voice_future.result()
        except Exception:
            diagnostics.LOG.exception("Voice output failed")
        self.voice_future = None
        self.update_buttons()

    def close(self, callback=None):
        if self.future is not None:
            self.close_after, self.on_close = True, callback
            self.stop()
            return
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.review.live_panel = None
        self.window.destroy()
        if callback:
            callback()
