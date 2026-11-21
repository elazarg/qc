"""Listening to the dry guitar as it is played, for the Guitar Learner.

`run` is what the live worker process runs (see `lab.learner.worker`): it
holds the dry input open on the USB channel its settings name (the desk asked
the engine which one), follows the playing with `continuous.Tracker`, and
keeps a clip of the recent audio only when asked.

Every clip kept from one run is marked with that run and with where it starts
in it, so that two clips of the same playing are known to be one performance
(`lab.learner.dataset`).
"""

from datetime import datetime
from queue import Empty
from tempfile import TemporaryDirectory
from threading import Lock
import time
from pathlib import Path
from uuid import uuid4

import numpy as np

from lab.hearing import audio
from lab.learner import continuous, dataset as guitar_dataset, inputs, profiles
from lab.learner.folders import write_json

WINDOW_SECONDS = 3.
RECENT_SECONDS = 12.
ANALYSIS_INTERVAL = .26
REPORT_INTERVAL = .5
INPUT_TIMEOUT = 3.


class DryWindow:
    """A ring of the newest dry samples, filled by the audio callback and copied out in time order."""

    def __init__(self, size):
        self.samples = np.empty(size, dtype=np.float32)
        self.cursor = self.filled = self.generation = 0
        self.discontinuities = 0
        self.time = 0.0
        self.lock = Lock()

    def append(self, chunk, when, *, gap=False):
        with self.lock:
            if gap:
                self.filled = self.cursor = 0
                self.discontinuities += 1
            values = chunk[-len(self.samples):]
            first = min(len(values), len(self.samples) - self.cursor)
            self.samples[self.cursor:self.cursor + first] = values[:first]
            self.samples[:len(values) - first] = values[first:]
            self.cursor = (self.cursor + len(values)) % len(self.samples)
            self.filled = min(len(self.samples), self.filled + len(values))
            self.time = when
            self.generation += 1

    def latest(self, generation):
        snapshot = self.recent(generation=generation, minimum=len(self.samples))
        return snapshot[:3] if snapshot else None

    def recent(self, size=None, *, generation=None, minimum=1):
        """Copy the newest contiguous samples, in time order, outside the callback."""
        with self.lock:
            if self.filled < minimum or generation == self.generation:
                return None
            count = min(self.filled, size) if size is not None else self.filled
            start = (self.cursor - count) % len(self.samples)
            if start + count <= len(self.samples):
                values = self.samples[start:start + count].copy()
            else:
                values = np.concatenate((self.samples[start:], self.samples[:self.cursor]))
            return self.generation, self.time, values, self.discontinuities, self.filled


def save_recent(dry, guitar, physical_input, usb_input, *, boundary=None, run=None):
    if len(dry) < audio.RATE // 2 or not np.isfinite(dry).all():
        raise ValueError("Wait for at least half a second of dry audio before saving.")
    root = guitar_dataset.live_recordings()
    root.mkdir(parents=True, exist_ok=True)
    seconds = len(dry) / audio.RATE
    destination = root / f"clip-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
    with TemporaryDirectory(prefix=".learner-live-", dir=root) as temporary:
        stage = Path(temporary)
        audio.write_wave(stage / "dry.wav", dry)
        metadata = {"guitar": guitar, "physical_input": physical_input, "dry_channel": usb_input,
                    "sample_rate": audio.RATE, "duration_seconds": seconds,
                    "capture_mode": "learner-live", "passage": "Live dry take",
                    "dry_peak_dbfs": audio.db(np.max(np.abs(dry)))}
        if boundary is not None:
            metadata["change_seconds"] = boundary
        if run is not None:
            metadata["live_run"], metadata["run_start_seconds"] = run[0], round(run[1], 3)
        write_json(stage / "clip.json", metadata)
        stage.rename(destination)
    return destination, seconds


def _profile(profile):
    return profiles.validate(profile) if profile is not None else None


def _marked_change(snapshot, change):
    _, at, samples, _, _ = snapshot
    origin = at - len(samples) / audio.RATE
    start = max(0, round((change["start"] - origin) * audio.RATE))
    end = min(len(samples), round((change["end"] - origin) * audio.RATE))
    if end - start < audio.RATE // 2:
        return None
    boundary = min((end - start) / audio.RATE, max(0., change["at"] - origin - start / audio.RATE))
    return uuid4().hex[:12], samples[start:end].copy(), boundary, change["at"], origin + start / audio.RATE


def _after_change(window, at):
    snapshot = window.recent()
    if snapshot:
        _, latest, samples, _, _ = snapshot
        first = round((at - latest) * audio.RATE) + len(samples)
        if 0 <= first < len(samples):
            return samples[first:first + round(3 * audio.RATE)], latest - (len(samples) - first) / audio.RATE
    return np.empty(0, dtype=np.float32), at


def _save_request(message, window, marked, guitar, physical, usb, report, run):
    event = {"event": "learner-snapshot", "request_id": message.get("request_id")}
    try:
        boundary = None
        if marker := message.get("change_id"):
            if not marked or marker != marked[0]:
                raise ValueError("That live change is no longer available. Save the recent dry audio instead.")
            _, samples, boundary, at, begins = marked
            part = message.get("part")
            if part in ("before", "after"):
                split = round(boundary * audio.RATE)
                if part == "after":
                    samples, begins = samples[split:], begins + split / audio.RATE
                    if len(samples) < audio.RATE // 2:
                        samples, begins = _after_change(window, at)
                else:
                    samples = samples[:split]
                boundary = None
            elif part is not None:
                raise ValueError("Choose the whole live change, before, or after.")
        else:
            seconds = float(message.get("seconds", 8))
            if not np.isfinite(seconds) or not .5 <= seconds <= RECENT_SECONDS:
                raise ValueError(f"Choose between half a second and {RECENT_SECONDS:g} seconds of recent audio.")
            snapshot = window.recent(round(seconds * audio.RATE), minimum=audio.RATE // 2)
            if not snapshot:
                raise ValueError("Wait for at least half a second of dry audio before saving.")
            samples = snapshot[2]
            begins = snapshot[1] - len(samples) / audio.RATE
        folder, duration = save_recent(samples, guitar, physical, usb, boundary=boundary,
                                       run=(run[0], begins - run[1]))
    except (OSError, TypeError, ValueError) as error:
        report({**event, "reason": str(error), "path": None})
    else:
        report({**event, "folder": str(folder), "path": str(folder / "dry.wav"), "seconds": duration,
                "boundary_seconds": boundary})


def run(config, stop, commands, report):
    profile = _profile(config.get("profile"))
    guitar = config.get("guitar", "Charvel").strip()
    physical = config.get("physical_input")
    if not guitar or type(physical) is not int or physical not in (1, 2):
        raise ValueError("Choose the guitar and Quad Cortex input 1 or 2.")
    if stop.is_set():
        return {}
    usb = config.get("dry_channel")
    if type(usb) is not int or not 1 <= usb <= 4:
        raise ValueError("The desk did not say which USB channel carries the dry guitar.")
    audio_device, index = inputs.asio_device()
    window = DryWindow(round(RECENT_SECONDS * audio.RATE))

    def receive(data, frames, timing, status):
        window.append(data[:, 0], time.monotonic(), gap=bool(status.input_overflow))

    stream = audio_device.InputStream(device=index, channels=1, dtype="float32", samplerate=audio.RATE,
                                      latency="high", extra_settings=audio_device.AsioSettings([usb - 1]),
                                      callback=receive)
    generation = gaps = 0
    ready = False
    marked = None
    last_state = None
    try:
        stream.start()
        tracker = continuous.Tracker(profile=profile)
        start = time.monotonic()
        run_of_listening = uuid4().hex, start
        checked = analyzed = reported = start
        while not stop.is_set():
            try:
                message = commands.get(timeout=.04)
            except Empty:
                message = None
            if stop.is_set():
                break
            if message and message["event"] == "learner-profile":
                tracker.set_profile(_profile(message.get("profile")))
                last_state = None
            elif message and message["event"] == "learner-snapshot":
                _save_request(message, window, marked, guitar, physical, usb, report, run_of_listening)
            now = time.monotonic()
            if not ready or now - checked >= .8:
                recent = window.recent(1)
                if not stream.active or now - (recent[1] if recent else start) > INPUT_TIMEOUT:
                    raise RuntimeError("The Quad Cortex stopped sending dry audio. Close other audio sessions and try again.")
                if not ready and recent:
                    ready = True
                    report({"event": "learner-ready", "dry_channel": usb, "sample_rate": audio.RATE})
                if ready and now - checked >= .8:
                    report({"event": "heartbeat", "audio": {"dropouts": recent[3]}})
                    checked = now
            if not ready or now - analyzed < ANALYSIS_INTERVAL:
                continue
            analyzed = now
            snapshot = window.recent(round(WINDOW_SECONDS * audio.RATE), generation=generation,
                                     minimum=audio.RATE // 4)
            if snapshot is None:
                continue
            generation, at, dry, dropouts, filled = snapshot
            if dropouts != gaps:
                tracker.reset()
                gaps = dropouts
            state = tracker.update(dry, at=at)
            change = state.get("change")
            if change and (clip := _marked_change(snapshot, change)):
                marked = clip
                state = {**state, "change": {**change, "id": marked[0]}}
            if change or last_state is None or now - reported >= REPORT_INTERVAL:
                last_state = state
                starts = np.linspace(0, len(dry), 64, endpoint=False, dtype=int)
                peaks = np.maximum.reduceat(np.abs(dry), starts).round(5).tolist()
                report({"event": "learner-state", **state, "at": at,
                        "peak_dbfs": round(audio.db(np.max(np.abs(dry[-audio.RATE // 4:]))), 1),
                        "buffered_seconds": round(filled / audio.RATE, 2), "dropouts": dropouts, "peaks": peaks})
                reported = now
    finally:
        stream.close()
    return {"dropouts": gaps}
