"""Gesture control while playing: a deliberate move of a guitar control away and back nudges one Shape knob.

A gesture is a stable A → B → A return between two settings of one control
(pickup, volume or tone) that the app was taught to tell apart
(`lab.learner.taught`). Its mapping names a Shape knob of `lab.sound.knobs`
and a step.

When gestures count. Listening is armed for `ARM_SECONDS` (20 s) from its
start, and every gesture recognised arms it for that long again. When that
time passes with no gesture, listening started from the desk ends and lets go
of the audio input (`state()` says so, and how long is left while it runs);
listening started to show the estimate goes on, observing only. `arm()` arms
a running session again.

Timing. An estimate is made from `state.WINDOW_SECONDS` of audio every
`state.HOP_SECONDS`, live and in a rehearsal on a recorded take
(`state.track`), so a rehearsal shows what listening live would do. While a
control is being moved the window holds both settings and no estimate is
close, for about a window's length; the detector waits that long
(`max_gap`, a window and two hops) before it gives up on a gesture. A
gesture therefore needs between about 0.6 and 1.2 seconds at the excursion
setting: shorter, and no window holds it alone; longer, and it is a change of
setting, not a gesture.

The listening runs in the learner's worker process (`lab.learner.worker`):
`run` opens the dry input on the channel the desk asked the engine for,
estimates the control's setting and proposes gestures; it never turns
anything. On the desk's side a `Session` takes the desk's audio hold, starts
that process, and for each proposal turns the knob on the sound on show with
`studio.turn`, provided that knob does something on its chain.

    from lab.learner import gestures
    gestures.start(seat)        # the seat of lab.learner.seat.LearnerSeat
    gestures.state()            # {"running", "armed", "seconds_left", "instrument", "control"}
    gestures.arm()
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
from lab.learner import folders, inputs, profiles, state as guitar_state
from lab.learner.folders import LOG
from lab.learner.live import DryWindow

CONTROLS = ("pickup", "volume", "tone")
HOLD = "gestures"
ARM_SECONDS = 20.0
STALE_SECONDS = .55
INPUT_TIMEOUT = 3.
BEAT_SECONDS = .8


# ---- the gesture ----

@dataclass(frozen=True)
class GestureMapping:
    """Return from an excursion to the original setting to move one Shape knob."""

    home: str
    excursion: str
    control: str
    step: float = 0.1

    def __post_init__(self):
        if not all(isinstance(label, str) and label.strip() for label in (self.home, self.excursion)):
            raise ValueError("Choose two taught settings.")
        if self.home.strip() == self.excursion.strip():
            raise ValueError("Choose different start and excursion settings.")
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
    """Recognize a stable A → B → A sequence within an armed window.

    Times are seconds on one monotonic clock. A reading counts only when its closeness to a taught setting is at
    least `closeness`; each setting must be held for `dwell`. A third setting, `max_gap` without a close reading,
    or a stay away longer than `excursion_timeout` cancels the pending gesture. A gesture arms the detector for
    another `arm_seconds`.
    """

    def __init__(self, mapping, *, closeness=0.8, dwell=0.2, excursion_timeout=1.5,
                 cooldown=1.0, max_gap=None, arm_seconds=ARM_SECONDS):
        if max_gap is None:
            # While the control moves, the window holds both settings: a window and two hops without a close reading.
            max_gap = guitar_state.WINDOW_SECONDS + 2 * guitar_state.HOP_SECONDS
        if not 0 < closeness <= 1:
            raise ValueError("Closeness must be above zero and at most one.")
        durations = (dwell, excursion_timeout, cooldown, max_gap, arm_seconds)
        if (not all(math.isfinite(value) and value > 0 for value in durations)
                or excursion_timeout <= dwell):
            raise ValueError("Use positive gesture timing and an excursion longer than the dwell.")
        self.mapping = mapping
        self.closeness = closeness
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

    def seconds_left(self, now):
        return max(0., self._armed_until - now) if self.is_armed(now) else 0.

    def observe(self, now, label, closeness):
        """Accept one estimate of the setting and return a proposal only after a full return."""
        now = self._time(now)
        if not self.is_armed(now):
            if self._armed_until is not None and now >= self._armed_until:
                self.disarm()
            return None
        if self._last_observed is not None and now <= self._last_observed:
            self._clear_path()
            return None
        self._last_observed = now

        if self._last_close is not None and now - self._last_close > self.max_gap + 1e-9:
            self._clear_path()
        if (label is None or closeness is None or not math.isfinite(closeness)
                or not self.closeness <= closeness <= 1):
            # A reading that is not close cannot contribute to the current dwell.
            self._candidate = None
            return None
        if label not in (self.mapping.home, self.mapping.excursion):
            self._clear_path()
            return None
        self._last_close = now
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
        self._armed_until = now + self.arm_seconds          # in use: armed for as long again
        return GestureEvent(**self.mapping.to_dict(), at=now)

    def _clear_path(self):
        self._last_close = None
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
    """Preview gestures in a track of estimates (`state.track`) without changing any controls."""
    observations = list(observations)
    if not observations:
        return []
    # An explicitly requested offline replay covers the entire supplied track.
    timing.setdefault("arm_seconds", max(1.0, observations[-1]["time"] - observations[0]["time"] + 1.0))
    detector = GestureDetector(mapping, **timing)
    detector.arm(observations[0]["time"])
    events = []
    for row in observations:
        event = detector.observe(row["time"], row["label"], row["closeness"])
        if event is not None:
            events.append(event)
    return events


# ---- what is saved ----

def saved(instrument, control):
    """(profile, mapping) saved for one taught control; either may be None."""
    folder = folders.taught(instrument)
    profile = mapping = None
    path = folder / f"{control}.json"
    if path.is_file():
        profile = profiles.load(path)
    gesture = folder / f"{control}-gesture.json"
    if gesture.is_file():
        record = json.loads(gesture.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("Could not read the saved gesture mapping.")
        mapping = GestureMapping.from_dict(record)
    if mapping and (profile is None or {mapping.home, mapping.excursion} - set(profile["labels"])):
        mapping = None
    return profile, mapping


def config(instrument, physical_input, *, control=None, arm=True, observe=False):
    """Settings for the worker: the control to listen for (a mapped one first), its profile and mapping.

    `observe` keeps listening when no gesture is armed, to show the estimate. Without it there must be a mapped
    gesture to arm: listening that could never turn anything is refused."""
    found = []
    for name in ([control] if control else CONTROLS):
        try:
            profile, mapping = saved(instrument, name)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if profile is not None:
            found.append((mapping is None, name, mapping))
    if not found:
        raise ValueError(f"Teach two settings of one of the {instrument}'s controls first (Teach a control).")
    _, name, mapping = min(found, key=lambda row: row[0])
    if not observe and not (arm and mapping):
        raise ValueError(f"No gesture is mapped for the {instrument} yet. Map one under Teach a control.")
    return {"mode": "gestures", "physical_input": physical_input, "instrument": instrument, "control": name,
            "profile": str((folders.taught(instrument) / f"{name}.json").resolve()),
            "gesture": mapping.to_dict() if mapping else None, "arm": bool(arm and mapping), "observe": bool(observe)}


# ---- in the worker process ----

class Observer:
    def __init__(self, config):
        self.profile = profiles.load(Path(config["profile"]))
        self.control = self.profile["control"]
        self.window = DryWindow(round(audio.RATE * guitar_state.WINDOW_SECONDS))
        self.results = deque(maxlen=4)
        self.stop = Event()
        self.failed = None
        self.detector = None
        self.allow_changes = config.get("arm") is True
        if config.get("gesture"):
            mapping = GestureMapping.from_dict(config["gesture"])
            self.detector = GestureDetector(mapping)

    def callback(self, data, frames, timing, status):
        self.window.append(data[:, 0], time.monotonic(), gap=bool(status.input_overflow))

    def read(self, generation=0):
        """Make one estimate from the newest full window, when there is audio newer than `generation`. Returns
        the generation read."""
        snapshot = self.window.latest(generation)
        if snapshot is None:
            return generation
        generation, when, dry = snapshot
        result = guitar_state.estimate(self.profile, dry)
        self.results.append((when, result.get("label"), float(result.get("closeness", 0)), result.get("reason")))
        return generation

    def analyze(self):
        """An estimate every `state.HOP_SECONDS`, on a steady clock whatever each estimate costs."""
        generation = 0
        due = time.monotonic() + guitar_state.HOP_SECONDS
        while not self.stop.wait(max(0., due - time.monotonic())):
            due = max(due + guitar_state.HOP_SECONDS, time.monotonic())
            try:
                generation = self.read(generation)
            except Exception as error:  # noqa: BLE001
                LOG.exception("The dry input could not be analysed; no gesture sent")
                self.failed = str(error) or type(error).__name__
                return

    def poll(self, now):
        """Return the newest estimate and a gesture proposal, if any. Raises when the analysis has stopped."""
        if self.failed is not None:
            raise RuntimeError(f"The dry input could not be analysed: {self.failed}")
        try:
            when, label, closeness, reason = self.results.popleft()
        except IndexError:
            return None, None
        if now - when > STALE_SECONDS:
            label, closeness, reason = None, 0.0, "Waiting for a new dry note."
        event = {"event": "guitar-state", "control": self.control, "label": label, "closeness": closeness,
                 "reason": reason}
        gesture = self.detector.observe(when, label, closeness) if self.detector else None
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
    """Listen for the control's setting and propose gestures until `stop` is set, or until the arm runs out when
    nothing else is being shown (`config["observe"]` false). Runs in the worker process."""
    physical, usb = config.get("physical_input"), config.get("dry_channel")
    if type(physical) is not int or physical not in (1, 2):
        raise ValueError("Choose Quad Cortex input 1 or 2.")
    if type(usb) is not int or not 1 <= usb <= 4:
        raise ValueError("The desk did not say which USB channel carries the dry guitar.")
    if stop.is_set():
        return {}
    observe = config.get("observe") is True
    with listen(config, usb) as observer:
        detector = observer.detector
        armed = observer.allow_changes and detector is not None
        now = time.monotonic()
        report({"event": "guitar-status", "armed_seconds": detector.seconds_left(now) if armed else 0.,
                "text": f"Listening to the dry guitar. The mapped return gesture is armed for {ARM_SECONDS:.0f} s."
                if armed else "Listening to the dry guitar. Observing only."})
        start = beat = now
        while not stop.wait(.05):
            now = time.monotonic()
            if now - (observer.window.time or start) > INPUT_TIMEOUT:
                raise RuntimeError("The Quad Cortex stopped sending dry audio. Close other audio sessions and try again.")
            try:
                asked = commands.get_nowait()
            except Empty:
                asked = None
            if asked and asked.get("event") == "guitar-arm" and observer.allow_changes and detector is not None:
                detector.arm(now)
                armed = True
                report({"event": "guitar-status", "armed_seconds": detector.seconds_left(now),
                        "text": f"Gestures armed for {ARM_SECONDS:.0f} s."})
            if armed and not detector.is_armed(now):
                armed = False
                if not observe:
                    return {"ended": "arm-expired"}
                report({"event": "guitar-status", "armed_seconds": 0.,
                        "text": "The gesture's arm has run out. Observing the dry guitar only."})
            observation, gesture = observer.poll(now)
            if observation is not None:
                report(observation)
            if gesture is not None:
                allowed = bool(observer.allow_changes and detector.is_armed(now))
                report({**gesture.to_dict(), "event": "guitar-gesture", "allowed": allowed,
                        "armed_seconds": detector.seconds_left(now) if allowed else 0.})
            if now - beat >= BEAT_SECONDS:
                report({"event": "heartbeat"})
                beat = now
    return {}


# ---- on the desk's side ----

class Session:
    """One run of gesture listening: it holds the desk's audio for gestures, is supervised on its own thread and
    handled on the desk's clock (`seat.watch`).

    The desk goes on playing the guitar through the unit while it listens; nothing of the desk's that opens the
    audio driver runs while the hold is taken.
    """

    def __init__(self, seat, config, *, listener=None, launch=True, after=None):
        self.seat, self.config, self.studio = seat, config, seat.studio
        self.listeners = [listener] if listener is not None else []
        self.events, self.commands, self.signal = Queue(), Queue(), Event()
        self.started = self.finished = False
        self.after = after                  # a session that must let go of the input first
        self.armed_until = None             # on this side's clock, from what the worker says
        self.thread = None
        if launch:
            seat.watch(self.step)

    # -- on the desk's clock --

    def step(self):
        if not self.started:
            if self.after is not None and not self.after.finished:
                return True
            self.after = None
            if self.signal.is_set():
                self.handle({"event": "guitar-stopped"})
                return False
            holder = self.studio.audio_held_by
            if holder not in (None, HOLD):
                self.handle({"event": "guitar-stopped", "error": f"the audio input is in use ({holder})"})
                return False
            self.started = True
            let_go = self.studio.hold_audio(HOLD)
            self.thread = Thread(target=self._supervise, args=(let_go,), name="guitar-gestures", daemon=True)
            self.thread.start()
        self.pump()
        return not self.finished

    def pump(self):
        """Handle what the worker has said so far."""
        if getattr(self.studio.state, "mode", None) == "phrase" and not self.signal.is_set():
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
        try:
            if "armed_seconds" in event:
                left = float(event["armed_seconds"])
                self.armed_until = time.monotonic() + left if left > 0 else None
            if kind == "guitar-gesture":
                event = {**event, **self.apply(event)}
                title = KNOBS.get(event["control"], (event["control"],))[0]
                self.say(f"{'Moved' if event['applied'] else 'Heard a return gesture for'} {title} {event['step']:+.0%}")
            elif kind == "guitar-status":
                self.say(event["text"])
        finally:
            if kind == "guitar-stopped":
                self._ended(event)
            for listener in list(self.listeners):
                try:
                    listener(event)
                except Exception:  # noqa: BLE001 - one listener's fault is not the session's
                    LOG.exception("A gesture listener failed")

    def _ended(self, event):
        global _session
        was_started, self.finished, self.armed_until = self.started, True, None
        if was_started:
            self.studio.release_audio(HOLD)
        if _session is self:
            _session = None
        if event.get("error"):
            self.say(f"Gesture listening stopped: {event['error']}")
        elif event.get("reason") == "arm-expired":
            self.say(f"Gestures off: no gesture came in {ARM_SECONDS:.0f} s. Press Gestures on to arm again.")

    def apply(self, event):
        """Turn the knob on the sound on show, if the gesture is armed and that knob does something there."""
        studio = self.studio
        sound = studio.sound(studio.state.side)
        values = dict(sound.knobs) if sound is not None else {}
        if not event.get("allowed") or sound is None or event["control"] not in studio.available():
            return {"applied": False, "value": values.get(event["control"], 0.)}
        proposed = GestureEvent.from_dict(event).apply(values)
        if proposed == values:
            return {"applied": False, "value": values.get(event["control"], 0.)}
        studio.turn(proposed)
        return {"applied": True, "value": proposed[event["control"]]}

    def say(self, text):
        self.studio.set(status=text)

    def arm(self):
        self.commands.put({"event": "guitar-arm"})

    def stop(self):
        self.signal.set()

    def seconds_left(self):
        return max(0., self.armed_until - time.monotonic()) if self.armed_until is not None else 0.

    # -- on its own thread --

    def _supervise(self, let_go):
        from lab.learner import worker
        from lab.learner.seat import wait_for

        try:
            wait_for(let_go)
            channels, _ = inputs.routing(self.seat.host, self.config["physical_input"])
            result = worker.run({**self.config, "dry_channel": channels[0]}, self.signal, self.commands,
                                self.events.put)
        except Exception as error:  # noqa: BLE001
            LOG.exception("Gesture listening failed")
            self.events.put({"event": "guitar-stopped", "error": str(error) or type(error).__name__})
        else:
            self.events.put({"event": "guitar-stopped", "reason": (result or {}).get("ended")})


_session = None


def running():
    return _session is not None and not _session.finished


def state():
    """What the desk can show of gesture listening: whether it runs, whether a gesture would count now, and for
    how many more seconds."""
    if not running():
        return {"running": False, "armed": False, "seconds_left": 0., "instrument": None, "control": None}
    left = _session.seconds_left()
    return {"running": True, "armed": left > 0, "seconds_left": left,
            "instrument": _session.config.get("instrument"), "control": _session.config.get("control")}


def start(seat, *, instrument=None, control=None, arm=True, listener=None, observe=False):
    """Listen for gestures on the desk's guitar input. Returns the running session.

    Asked again while a session runs: for the same instrument and control, the listener is added to it and, with
    `arm`, it is armed again; for another control, that session is ended and a new one starts once it has let go
    of the input."""
    global _session
    instrument = instrument or seat.guitar
    settings = config(instrument, seat.input_channel, control=control, arm=arm, observe=observe)
    current = _session if running() else None
    if current is not None and not current.signal.is_set() and all(
            current.config.get(key) == settings[key] for key in ("instrument", "control", "physical_input")) and (
            current.config.get("observe") or not observe) and (current.config.get("arm") or not settings["arm"]):
        if listener is not None and listener not in current.listeners:
            current.listeners.append(listener)
        if settings["arm"]:
            current.arm()
        return current
    if current is not None:
        current.stop()
    _session = Session(seat, settings, listener=listener, after=current)
    return _session


def arm():
    """Arm the running session's mapped gesture for another `ARM_SECONDS`."""
    if running():
        _session.arm()


def stop():
    """Ask the running session to end. It lets go of the input within a second or two."""
    if _session is not None:
        _session.stop()
