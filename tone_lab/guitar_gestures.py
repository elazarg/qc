"""Opt-in gesture detection from timestamped dry guitar state observations."""

from dataclasses import asdict, dataclass
import math

from . import styles


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
        if not isinstance(self.control, str) or self.control not in styles.KNOBS:
            raise ValueError("Choose a supported Tone Lab control.")
        if type(self.step) not in (int, float) or not math.isfinite(self.step) or not 0 < abs(self.step) <= 0.25:
            raise ValueError("Choose a nonzero step between −0.25 and 0.25.")
        object.__setattr__(self, "home", self.home.strip())
        object.__setattr__(self, "excursion", self.excursion.strip())

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(data.get("home"), data.get("excursion"), data.get("control"), data.get("step", 0.1))


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
        minimum = styles.KNOBS[self.control][1]
        changed = round(min(1, max(minimum, position + self.step)), 6)
        return {**knobs, self.control: changed}

    def to_dict(self):
        return asdict(self)


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
