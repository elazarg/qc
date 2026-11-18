"""Relative whole-tone coordinates and measurements for the experimental workspace."""

from dataclasses import dataclass
import math

from . import audio, styles, tone_search


@dataclass(frozen=True)
class Dimension:
    low: str
    high: str
    moves: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class Plane:
    horizontal: Dimension
    vertical: Dimension
    description: str


PLANES = {
    "Gain & color": Plane(
        Dimension("Cleaner", "Saturated", (("drive", .9), ("sustain", .18))),
        Dimension("Rounded", "Cutting", (("warmth", -.72), ("presence", .63))),
        "Find the edge of breakup, then move the bite without moving the gain."),
    "Under the fingers": Plane(
        Dimension("Giving", "Immediate", (("attack", .78), ("sustain", -.48))),
        Dimension("Loose lows", "Tight lows", (("tightness", .9),)),
        "Play the same figure softly and firmly while you explore response and low-end control."),
    "Depth & width": Plane(
        Dimension("Close", "Distant", (("space", .72), ("warmth", .23))),
        Dimension("Focused", "Wide", (("width", .92), ("modulation", .12))),
        "Move the sound back into a room, or spread it to the sides."),
    "Inside a mix": Plane(
        Dimension("Tucked in", "Forward", (("mids", .7), ("presence", .41), ("space", -.34))),
        Dimension("Lean", "Weighty", (("body", .83), ("tightness", -.1))),
        "Listen for where the guitar sits against a backing part at a steady playback level."),
    "Note shape": Plane(
        Dimension("Brief", "Held", (("sustain", .77), ("repeats", .24), ("reverb", .15))),
        Dimension("Soft front", "Clear front", (("attack", .74), ("presence", .24))),
        "Compare how notes start and how much sound carries into the next note."),
    "Atmosphere": Plane(
        Dimension("Dry", "Bloom", (("reverb", .87), ("space", .18))),
        Dimension("Still", "In motion", (("modulation", .72), ("repeats", .32), ("delay", .22))),
        "Move from direct sound to wash, then add motion when the chain supports it."),
}


def bounded(value, minimum=-1.0, maximum=1.0):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Tone positions must be finite.")
    return min(maximum, max(minimum, value))


def moves(dimension, available=None):
    return {key: amount for key, amount in dimension.moves if available is None or key in available}


def position_values(anchor, plane, x, y, available=None):
    """Translate one point into bounded, absolute controls with an exact center."""
    changes = {}
    for dimension, position in ((plane.horizontal, bounded(x)), (plane.vertical, bounded(y))):
        for key, amount in moves(dimension, available).items():
            changes[key] = changes.get(key, 0.) + position * amount
    values = dict(anchor)
    for key, delta in changes.items():
        if delta:
            values[key] = bounded(values.get(key, 0.) + delta, styles.KNOBS[key][1])
    return values


def interpolate(start, end, fraction):
    """Travel between two real knob snapshots using a single bounded amount."""
    fraction = bounded(fraction, 0., 1.)
    if fraction == 0.:
        return dict(start)
    if fraction == 1.:
        return dict(end)
    values = {}
    for key in start.keys() | end.keys():
        before, after = start.get(key, 0.), end.get(key, 0.)
        values[key] = bounded(before + (after - before) * fraction, styles.KNOBS[key][1])
    return values


def equivalent(first, second):
    return all(abs(first.get(name, 0.) - second.get(name, 0.)) <= .005 for name in first.keys() | second.keys())


@dataclass(frozen=True)
class Fingerprint:
    spectrum_db: tuple[float, ...]
    perceptual_delta: tuple[tuple[str, float, str], ...]


def difference(before, after):
    """Describe the signals independently of their playback level."""
    a, b = tone_search.describe(before), tone_search.describe(after)
    return Fingerprint(
        tuple(right - left for left, right in zip(a.spectrum, b.spectrum)),
        (("Brightness", b.brightness - a.brightness, "oct"),
         ("Variation", b.dynamics - a.dynamics, "dB"),
         ("Stereo sides", (b.width - a.width) * 100, "pp"),
         ("Tail continuity", (b.tails - a.tails) * 100, "pp")),
    )


BAND_NAMES = tuple(f"{(low * high) ** .5 / 1000:.1f}k" if (low * high) ** .5 >= 1000
                   else str(round((low * high) ** .5)) for low, high in zip(audio.BANDS, audio.BANDS[1:]))
