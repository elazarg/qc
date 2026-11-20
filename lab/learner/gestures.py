"""Gesture control while playing: a deliberate move of a guitar control away and back nudges one Shape knob.

A gesture is a stable A → B → A return between two states of one control
(pickup, volume or tone) that the guitar-input calibration has learned
(`lab.learner.calibration`). Its mapping names a Shape knob of `lab.sound.knobs`
and a step. It is recognised only within an armed window, about 20 seconds
from the start of listening.

The listening runs in the learner's worker process (`lab.learner.worker`):
`run` opens the dry input, estimates the control's state from it and
proposes gestures; it never turns anything. On the desk's side, `start(desk)`
supervises that process and, for each proposal, turns the knob on the sound
on show with `studio.turn`, provided that knob is available on its chain.
`stop()` ends it.

    from lab.learner import gestures
    gestures.start(seat)        # the seat of lab.desk.window.LearnerSeat, or the desk
    gestures.stop()
"""

from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
import time

from lab.hearing import audio
from lab.sound.knobs import KNOBS, RENAMED
from lab.learner import folders, inputs, state as guitar_state
from lab.learner.folders import LOG
from lab.learner.live import DryWindow

CONTROLS = ("pickup", "volume", "tone")
WINDOW_SECONDS = .48
POLL_SECONDS = .14
STALE_SECONDS = .55
INPUT_TIMEOUT = 3.
BEAT_SECONDS = .8


# ---- the gesture ----

@dataclass(frozen=True)
class GestureMapping:
    """Return from an excursion to the original state to move one virtual control."""

    home: str
    excursion: str
    control: str
    step: float = 0.1

    def __post_init__(self):
        if not all(isinstance(label, str) and label.strip() for label in (self.home, self.excursion)):
            raise ValueError("Choose two labeled guitar states.")
        if self.home.strip() == self.excursion.strip():
            raise ValueError("Choose different start and excursion states.")
        if not isinstance(self.control, str) or self.control not in KNOBS:
            raise ValueError("Choose a supported Tone Lab control.")
        if type(self.step) not in (int, float) or not math.isfinite(self.step) or not 0 < abs(self.step) <= 0.25:
            raise ValueError("Choose a nonzero step between −0.25 and 0.25.")
        object.__setattr__(self, "home", self.home.strip())
        object.__setattr__(self, "excursion", self.excursion.strip())

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        # A mapping saved for a knob since renamed for what it does (Warmth is now Brightness turned down) moves the
        # knob that does that job, with the step's sign carried over.
        control, step = data.get("control"), data.get("step", 0.1)
        if control in RENAMED:
            control, sign = RENAMED[control]
            step = step * sign if isinstance(step, (int, float)) else step
        return cls(data.get("home"), data.get("excursion"), control, step)


@dataclass(frozen=True)
class GestureEvent:
    home: str
    excursion: str
    control: str
    step: float
    at: float

    def apply(self, knobs):
        """Return proposed knob values. The caller decides whether to send them."""
        position = knobs.get(self.control, 0)
        if type(position) not in (int, float) or not math.isfinite(position):
            raise ValueError("The current control needs a finite value.")
        minimum = KNOBS[self.control][1]
        changed = round(min(1, max(minimum, position + self.step)), 6)
        return {**knobs, self.control: changed}

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(data["home"], data["excursion"], data["control"], data["step"], data["at"])


class GestureDetector:
    """Recognize a stable A → B → A sequence within an explicitly armed window.

    Times are seconds on one monotonic clock. Observations must have enough
    confidence to establish each dwell. A third state, long gap, or long stay
    away from the starting state cancels the pending gesture.
    """

    def __init__(self, mapping, *, confidence=0.8, dwell=0.2, excursion_timeout=1.5,
                 cooldown=1.0, max_gap=0.4, arm_seconds=20.0):
        if not 0 < confidence <= 1:
            raise ValueError("Confidence must be above zero and at most one.")
        durations = (dwell, excursion_timeout, cooldown, max_gap, arm_seconds)
        if (not all(math.isfinite(value) and value > 0 for value in durations)
                or excursion_timeout <= dwell):
            raise ValueError("Use positive gesture timing and an excursion longer than the dwell.")
        self.mapping = mapping
        self.confidence = confidence
        self.dwell = dwell
        self.excursion_timeout = excursion_timeout
        self.cooldown = cooldown
        self.max_gap = max_gap
        self.arm_seconds = arm_seconds
        self.disarm()

    def arm(self, now):
        now = self._time(now)
        self._armed_at = now
        self._armed_until = now + self.arm_seconds
        self.reset()

    def disarm(self):
        self._armed_at = None
        self._armed_until = None
        self.reset()

    def reset(self):
        """Clear partial observations and cooldown while keeping the arm window."""
        self._last_observed = None
        self._last_event = None
        self._clear_path()

    def is_armed(self, now):
        return self._armed_until is not None and self._armed_at <= now < self._armed_until

    def observe(self, now, label, confidence):
        """Accept one state estimate and return a proposal only after a full return."""
        now = self._time(now)
        if not self.is_armed(now):
            if self._armed_until is not None and now >= self._armed_until:
                self.disarm()
            return None
        if self._last_observed is not None and now <= self._last_observed:
            self._clear_path()
            return None
        self._last_observed = now

        if self._last_confident is not None and now - self._last_confident > self.max_gap + 1e-9:
            self._clear_path()
        if (label is None or confidence is None or not math.isfinite(confidence)
                or not self.confidence <= confidence <= 1):
            # An uncertain frame cannot contribute to the current dwell.
            self._candidate = None
            return None
        if label not in (self.mapping.home, self.mapping.excursion):
            self._clear_path()
            return None
        self._last_confident = now
        if self._candidate != label:
            self._candidate, self._candidate_since = label, now
        if label == self._stable or now - self._candidate_since + 1e-9 < self.dwell:
            return None
        prior, self._stable = self._stable, label
        if label == self.mapping.excursion:
            self._departed = self._candidate_since if prior == self.mapping.home else None
            return None

        departed, self._departed = self._departed, None
        if departed is None or self._candidate_since - departed > self.excursion_timeout + 1e-9:
            return None
        if self._last_event is not None and now - self._last_event < self.cooldown:
            return None
        self._last_event = now
        return GestureEvent(**self.mapping.to_dict(), at=now)

    def _clear_path(self):
        self._last_confident = None
        self._candidate = None
        self._candidate_since = None
        self._stable = None
        self._departed = None

    @staticmethod
    def _time(now):
        if not math.isfinite(now):
            raise ValueError("Use finite observation times in seconds.")
        return now


def replay(mapping, observations, **timing):
    """Preview gestures in a state track without changing any controls."""
    observations = list(observations)
    if not observations:
        return []
    # An explicitly requested offline replay covers the entire supplied track.
    timing.setdefault("arm_seconds", max(1.0, observations[-1]["time"] - observations[0]["time"] + 1.0))
    detector = GestureDetector(mapping, **timing)
    detector.arm(observations[0]["time"])
    events = []
    for row in observations:
        event = detector.observe(row["time"], row["label"], row["confidence"])
        if event is not None:
            events.append(event)
    return events


# ---- what is saved ----

def saved(instrument, control):
    """(profile, mapping) saved by the calibration for one control; either may be None."""
    folder = folders.calibration(instrument)
    profile = mapping = None
    path = folder / f"{control}.json"
    if path.is_file():
        profile = guitar_state.load_profile(path)
    gesture = folder / f"{control}-gesture.json"
    if gesture.is_file():
        record = json.loads(gesture.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("Could not read the saved gesture mapping.")
        mapping = GestureMapping.from_dict(record)
    if mapping and (profile is None or {mapping.home, mapping.excursion} - set(profile["labels"])):
        mapping = None
    return profile, mapping


def config(instrument, physical_input, *, control=None, arm=True):
    """Settings for the worker: the control to listen for (a mapped one first), its profile and mapping."""
    found = []
    for name in ([control] if control else CONTROLS):
        try:
            profile, mapping = saved(instrument, name)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if profile is not None:
            found.append((mapping is None, name, mapping))
    if not found:
        raise ValueError(f"Teach two settings of the {instrument} in Guitar input calibration first.")
    _, name, mapping = min(found, key=lambda row: row[0])
    return {"mode": "gestures", "physical_input": physical_input, "instrument": instrument, "control": name,
            "profile": str((folders.calibration(instrument) / f"{name}.json").resolve()),
            "gesture": mapping.to_dict() if mapping else None, "arm": bool(arm and mapping)}


# ---- in the worker process ----

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
            mapping = GestureMapping.from_dict(config["gesture"])
            self.detector = GestureDetector(mapping)

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
                LOG.exception("Guitar input analysis failed; no gesture sent")
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
    sd, index = inputs.asio_device()
    worker = Thread(target=observer.analyze, name="guitar-state", daemon=True)
    try:
        with sd.InputStream(device=index, channels=1, samplerate=audio.RATE, dtype="float32", latency="high",
                            extra_settings=sd.AsioSettings([usb_channel - 1]), callback=observer.callback):
            if observer.detector:
                # Observe-only also recognizes the pattern, without routing it to a knob.
                observer.detector.arm(time.monotonic())
            worker.start()
            yield observer
    finally:
        observer.stop.set()
        if worker.ident is not None:
            worker.join(timeout=.8)


def run(config, stop, commands, report):
    """Listen for the control's state and propose gestures until `stop` is set. Runs in the worker process."""
    physical = config.get("physical_input")
    if type(physical) is not int or physical not in (1, 2):
        raise ValueError("Choose Quad Cortex input 1 or 2.")
    if stop.is_set():
        return {}
    usb = inputs.recording_channels(physical)[0]
    if stop.is_set():
        return {}
    with listen(config, usb) as observer:
        armed = observer.allow_changes and observer.detector is not None
        report({"event": "guitar-status", "text": "Listening to the dry guitar. A mapped return gesture is briefly armed."
                if armed else "Listening to the dry guitar. Observing only."})
        start = beat = time.monotonic()
        while not stop.wait(.05):
            now = time.monotonic()
            if now - (observer.window.time or start) > INPUT_TIMEOUT:
                raise RuntimeError("The Quad Cortex stopped sending dry audio. Close other audio sessions and try again.")
            if armed and not observer.detector.is_armed(now):
                armed = False
                report({"event": "guitar-status", "text": "Gesture arm expired. Observing the dry guitar only."})
            observation, gesture = observer.poll(now)
            if observation is not None:
                report(observation)
            if gesture is not None:
                report({**gesture.to_dict(), "event": "guitar-gesture",
                        "allowed": bool(observer.allow_changes and observer.detector.is_armed(now))})
            if now - beat >= BEAT_SECONDS:
                report({"event": "heartbeat"})
                beat = now
    return {}


# ---- on the desk's side ----

def _studio(desk):
    return getattr(desk, "studio", None) or getattr(getattr(desk, "desk", None), "studio", None)


class Session:
    """One run of gesture listening, supervised on its own thread and handled on the desk's Tk clock.

    It does not take the desk's job: the desk keeps playing the guitar while it listens.
    """

    def __init__(self, desk, config, *, listener=None, launch=True):
        self.desk, self.config, self.listener = desk, config, listener
        self.studio = _studio(desk)
        self.events, self.signal = Queue(), Event()
        self.finished = False
        self.thread = None
        if launch:
            self.thread = Thread(target=self._supervise, name="guitar-gestures", daemon=True)
            self.thread.start()
            self._schedule()

    def _supervise(self):
        from lab.learner import worker

        try:
            worker.run(self.config, self.signal, Queue(), self.events.put)
        except Exception as error:  # noqa: BLE001
            self.events.put({"event": "guitar-stopped", "error": str(error)})
        else:
            self.events.put({"event": "guitar-stopped"})

    def _schedule(self):
        root = getattr(self.desk, "root", None)
        if root is not None:
            root.after(100, self._poll)

    def _poll(self):
        self.pump()
        if not self.finished:
            self._schedule()

    def pump(self):
        """Handle what the worker has said so far."""
        studio = self.studio
        if studio is not None and getattr(studio.state, "mode", None) == "phrase" and not self.signal.is_set():
            # The phrase loop needs the unit's audio driver; the dry input lets go of it.
            self.stop()
            self.say("Gesture listening stopped while the phrase plays.")
        while True:
            try:
                event = self.events.get_nowait()
            except Empty:
                return
            self.handle(event)

    def handle(self, event):
        kind = event.get("event")
        if kind == "guitar-gesture":
            event = {**event, **self.apply(event)}
            title = KNOBS.get(event["control"], (event["control"],))[0]
            self.say(f"{'Moved' if event['applied'] else 'Heard a return gesture for'} {title} {event['step']:+.0%}")
        elif kind == "guitar-status":
            self.say(event["text"])
        elif kind == "guitar-stopped":
            self.finished = True
            global _session
            if _session is self:
                _session = None
            if event.get("error"):
                self.say(f"Gesture listening stopped: {event['error']}")
        if self.listener is not None:
            self.listener(event)

    def apply(self, event):
        """Turn the knob on the sound on show, if the gesture is armed and that knob does something there."""
        studio = self.studio
        sound = studio.sound(studio.state.side) if studio is not None else None
        values = dict(sound.knobs) if sound is not None else {}
        if not event.get("allowed") or sound is None or event["control"] not in studio.available():
            return {"applied": False, "value": values.get(event["control"], 0.)}
        proposed = GestureEvent.from_dict(event).apply(values)
        if proposed == values:
            return {"applied": False, "value": values.get(event["control"], 0.)}
        studio.turn(proposed)
        return {"applied": True, "value": proposed[event["control"]]}

    def say(self, text):
        if self.studio is not None:
            self.studio.set(status=text)
        elif hasattr(self.desk, "set_status"):
            self.desk.set_status(text)

    def stop(self):
        self.signal.set()


_session = None


def running():
    return _session is not None and not _session.finished


def start(desk, *, instrument=None, control=None, arm=True, listener=None):
    """Listen for gestures on the desk's guitar input. Returns the running session."""
    global _session
    if running():
        return _session
    studio = _studio(desk)
    instrument = instrument or (studio.state.instrument if studio is not None else "Charvel")
    settings = config(instrument, getattr(desk, "input_channel", 1), control=control, arm=arm)
    _session = Session(desk, settings, listener=listener)
    return _session


def stop():
    """Ask the running session to end. It lets go of the input within a second or two."""
    if _session is not None:
        _session.stop()
