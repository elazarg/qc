"""Optional in-memory analysis of a dry guitar input during Live guitar."""

from contextlib import contextmanager
from collections import deque
from pathlib import Path
from threading import Event, Lock, Thread
import time

import numpy as np

from . import audio, diagnostics, guitar_gestures, guitar_state

WINDOW_SECONDS = .48
POLL_SECONDS = .14
STALE_SECONDS = .55


class DryWindow:
    def __init__(self, size):
        self.samples = np.empty(size, dtype=np.float32)
        self.cursor = self.filled = self.generation = 0
        self.time = 0.0
        self.lock = Lock()

    def append(self, chunk, when, *, gap=False):
        with self.lock:
            if gap:
                self.filled = self.cursor = 0
            values = chunk[-len(self.samples):]
            first = min(len(values), len(self.samples) - self.cursor)
            self.samples[self.cursor:self.cursor + first] = values[:first]
            self.samples[:len(values) - first] = values[first:]
            self.cursor = (self.cursor + len(values)) % len(self.samples)
            self.filled = min(len(self.samples), self.filled + len(values))
            self.time = when
            self.generation += 1

    def latest(self, generation):
        with self.lock:
            if self.filled != len(self.samples) or generation == self.generation:
                return None
            values = np.concatenate((self.samples[self.cursor:], self.samples[:self.cursor]))
            return self.generation, self.time, values


class Observer:
    def __init__(self, config):
        self.profile = guitar_state.load_profile(Path(config["profile"]))
        self.control = self.profile["control"]
        self.window = DryWindow(round(audio.RATE * WINDOW_SECONDS))
        self.results = deque(maxlen=4)
        self.stop = Event()
        self.detector = None
        self.allow_changes = config.get("arm") is True
        if config.get("gesture"):
            mapping = guitar_gestures.GestureMapping.from_dict(config["gesture"])
            self.detector = guitar_gestures.GestureDetector(mapping)

    def callback(self, data, frames, timing, status):
        self.window.append(data[:, 0], time.monotonic(), gap=bool(status.input_overflow))

    def analyze(self):
        generation = 0
        while not self.stop.wait(POLL_SECONDS):
            snapshot = self.window.latest(generation)
            if snapshot is None:
                continue
            generation, when, dry = snapshot
            try:
                result = guitar_state.estimate(self.profile, dry)
            except Exception:
                diagnostics.LOG.exception("Guitar input analysis failed; no gesture sent")
                self.results.append((when, None, 0.0, "Dry input analysis stopped."))
                return
            self.results.append((when, result.get("label"), float(result.get("confidence", 0)), result.get("reason")))

    def poll(self, now):
        """Return the newest state and a gesture proposal, if any."""
        try:
            when, label, confidence, reason = self.results.popleft()
        except IndexError:
            return None, None
        if now - when > STALE_SECONDS:
            label, confidence, reason = None, 0.0, "Waiting for a new dry note."
        event = {"event": "guitar-state", "control": self.control, "label": label, "confidence": confidence,
                 "reason": reason}
        gesture = self.detector.observe(when, label, confidence) if self.detector else None
        return event, gesture


@contextmanager
def listen(config, usb_channel):
    observer = Observer(config)
    sd, index, _ = audio.asio_device()
    worker = Thread(target=observer.analyze, name="guitar-state", daemon=True)
    try:
        with sd.InputStream(device=index, channels=1, samplerate=audio.RATE, dtype="float32", latency="high",
                            extra_settings=sd.AsioSettings([usb_channel - 1]), callback=observer.callback):
            if observer.detector:
                # Observe-only also recognizes the pattern, without routing it to QC controls.
                observer.detector.arm(time.monotonic())
            worker.start()
            yield observer
    finally:
        observer.stop.set()
        if worker.ident is not None:
            worker.join(timeout=.8)
