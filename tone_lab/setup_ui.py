"""Show the existing instrument and level tools inside the listening desk."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
import tkinter as tk
import wave

from . import audio, capture, diagnostics, level_plan, listening, session
from .guitar_ui import GuitarInputPanel
from .soundcheck_ui import SoundCheck
from .widgets import ACCENT, BG, INK, MUTED, TEXT, button, label, theme


def measure_pair(pair, anchor):
    """Apply the existing level planner to the two rendered sounds on the desk."""
    if not all(pair) or not pair[0].paired_with(pair[1]):
        raise ValueError("Use A and B rendered from the same phrase to plan preset levels.")
    with TemporaryDirectory(prefix="tone-levels-") as saved:
        folder = Path(saved)
        candidates, auditions = [], {}
        for side, sound in zip("AB", pair):
            filename = f"{side}.wav"
            audio.write_wave(folder / filename, listening.samples(sound))
            candidates.append({"name": sound.name, "file": filename})
            auditions[side] = {"render": filename}
        session.write_json(folder / "session.json", {"candidates": candidates, "auditions": auditions,
                                                    "source_clip": pair[0].take_id})
        return level_plan.comparison_plan(folder, reference=anchor)


class DeskSoundCheck(SoundCheck):
    def __init__(self, review, parent, worker):
        super().__init__(review, parent, worker, anchors=("Quietest", "A", "B"))

    def check_trims(self):
        pair = self.review.pair
        if not all(pair) or not pair[0].paired_with(pair[1]):
            self.status.set("Use A and B rendered from the same phrase to plan preset levels.")
            return
        anchor = self.anchor.get()
        reference = None if anchor == "Quietest" else anchor

        def show(result):
            if self.review.pair == pair:
                self.show_trims(result)
            else:
                self.trim_text.set("A/B changed. Plan levels for the current pair again.")

        self.run("Measuring both sounds from the current phrase…", show, measure_pair, pair, reference)


class ExampleRecorder:
    """Use the desk's own capture queue without changing the selected phrase."""

    def __init__(self, desk):
        self.desk = desk
        self.stop = None
        self.ready = Event()
        self.status = tk.StringVar(master=desk.root)
        self.recorded_path = None

    def press(self, *, guitar=None):
        desk = self.desk
        if self.stop is not None:
            self.stop.set()
            self.status.set("Saving the example…")
        elif desk.job is None and desk.leaving is None:
            self.stop = Event()
            self.ready = desk.ready
            self.ready.clear()
            desk.stop_signal = self.stop
            desk.live_is_ready = False
            self.status.set("Connecting to Quad Cortex…")
            desk.submit("record", "Connecting to Quad Cortex for a guitar example…",
                        capture.record_take, desk.input_channel, guitar or desk.guitar.get(),
                        self.stop, self.ready, done=self.finished)
            desk.root.after(100, self.poll)

    def poll(self):
        self.sync()
        if self.stop is not None:
            self.desk.root.after(100, self.poll)

    def sync(self):
        if self.stop is not None and self.desk.job is None:
            # The desk handles capture errors and remains the authority for status.
            self.stop = None
            self.status.set(self.desk.status.get())
            return
        if self.stop is not None:
            value = "Saving the example…" if self.stop.is_set() else (
                "Recording the example…" if self.ready.is_set() else "Connecting to Quad Cortex…")
            if self.status.get() != value:
                self.status.set(value)

    def finished(self, result):
        folder, message = result
        self.stop = None
        self.desk.takes = listening.takes()
        dry = folder / "dry.wav"
        # A guided example belongs to this exact capture even if another take
        # was written elsewhere at roughly the same moment.
        self.recorded_path = dry if dry.is_file() else folder / "inputs.wav"
        try:
            self.status.set(message)
        finally:
            self.recorded_path = None
        self.desk.set_status(message)


class DeskReview:
    """Expose a small subset of the old review contract to the reused views."""

    def __init__(self, desk, play):
        self.desk, self.root = desk, desk.root
        self.guitar = desk.guitar
        self.recorder = ExampleRecorder(desk)
        self.play_file = play

    @property
    def take(self):
        return self.desk.take.folder if self.desk.take else None

    @property
    def take_info(self):
        return self.desk.take.info if self.desk.take else {}

    @property
    def pair(self):
        return self.desk.baseline, self.desk.alternative

    def latest(self, *, pair=False):
        return capture.latest_clip(pair=pair)

    def completed_example(self):
        path = self.recorder.recorded_path
        return capture.clip_info(path, pair=True) if path else {}

    def busy(self):
        return self.desk.job is not None or self.desk.leaving is not None

    def stop(self):
        self.desk.stop_audio()


class SetupPanel(tk.Frame):
    CALIBRATION = "Guitar calibration"

    def __init__(self, parent, desk, *, live_observer=False):
        super().__init__(parent, background=BG)
        self.desk = desk
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tone-setup")
        self.preview = self.preview_job = self.preview_timer = None
        self.preview_lock = Lock()
        self.review = DeskReview(desk, self.play_file)
        self.status = tk.StringVar(master=self)
        self.active = None
        theme(self)
        header = tk.Frame(self, background=BG)
        header.pack(fill="x", pady=(0, 8))
        self.tabs, self.views = {}, {}
        for name in (self.CALIBRATION, "Sound check"):
            self.tabs[name] = button(header, name, lambda target=name: self.show(target), compact=True)
            self.tabs[name].pack(side="left", padx=(0, 6))
            self.views[name] = tk.Frame(self, background=BG)
        self.stop_button = button(header, "Stop preview", self.stop_preview, compact=True)
        self.stop_button.pack(side="right")
        label(header, variable=self.status, color=MUTED, size=9).pack(side="right", padx=(6, 8))
        self.soundcheck = DeskSoundCheck(self.review, self.views["Sound check"], self.worker)
        self.guitar_input = GuitarInputPanel(self.review, self.views[self.CALIBRATION], self.worker,
                                            live_observer=live_observer)
        self.show_calibration()
        self.bind("<Map>", lambda event: self.guitar_input.follow_desk_guitar() if event.widget is self else None)
        self.bind("<Unmap>", self.unmapped)

    def show_calibration(self, control=None):
        self.show(self.CALIBRATION)
        if control:
            self.guitar_input.choose_control(control)

    def show(self, name):
        if name == self.CALIBRATION:
            self.guitar_input.follow_desk_guitar()
        if self.active == name:
            return
        if self.active:
            self.stop_preview()
            self.views[self.active].pack_forget()
        self.active = name
        self.views[name].pack(fill="both", expand=True)
        for title, tab in self.tabs.items():
            selected = title == name
            tab.configure(background=ACCENT if selected else "#2c303c", foreground=INK if selected else TEXT)
        self.sync()

    def sync(self):
        self.review.recorder.sync()
        self.guitar_input.set_buttons()
        self.stop_button.configure(state="normal" if self.preview is not None else "disabled")

    def live_config(self):
        self.guitar_input.follow_desk_guitar()
        return self.guitar_input.live_config()

    def live_event(self, event):
        self.guitar_input.live_event(event)

    def play_file(self, path):
        if self.review.busy():
            raise RuntimeError("Finish the current recording or live shaping before playing the setup preview.")
        self.desk.stop_audio()
        self.stop_preview()
        try:
            with wave.open(str(path), "rb") as wav:
                duration_ms = round(1000 * wav.getnframes() / wav.getframerate())
        except (OSError, wave.Error) as error:
            raise RuntimeError("The setup preview could not be opened.") from error
        cancel = self.preview = Event()
        self.status.set("Playing preview…")
        self.preview_job = self.desk.worker.submit(self.start_preview, path, cancel)
        self.preview_timer = self.after(60, lambda: self.preview_ready(duration_ms))
        self.sync()

    def start_preview(self, path, cancel):
        import winsound

        with self.preview_lock:
            if not cancel.is_set():
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def preview_ready(self, duration_ms):
        if not self.preview_job.done():
            self.preview_timer = self.after(60, lambda: self.preview_ready(duration_ms))
            return
        future, self.preview_job = self.preview_job, None
        self.preview_timer = None
        try:
            future.result()
        except (OSError, RuntimeError) as error:
            self.preview = None
            self.status.set(str(error))
            self.sync()
        else:
            self.preview_timer = self.after(duration_ms + 100, self.stop_preview)

    def stop_preview(self):
        if self.preview_timer is not None:
            self.after_cancel(self.preview_timer)
            self.preview_timer = None
        if self.preview is not None:
            import winsound

            self.preview.set()
            with self.preview_lock:
                winsound.PlaySound(None, 0)
            self.preview = self.preview_job = None
            self.status.set("")
            self.stop_button.configure(state="disabled")

    def unmapped(self, event):
        if event.widget == self:
            self.stop_preview()

    def shutdown(self):
        self.stop_preview()
        self.worker.shutdown(wait=False, cancel_futures=True)
