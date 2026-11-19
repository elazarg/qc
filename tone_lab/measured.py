"""What each amp control was measured to do on the Quad Cortex.

The table comes from replaying one recorded phrase through every amp while
one control at a time is moved (research/measure_amps.py), and is rebuilt by
research/analyse_amps.py. It is one phrase through one cab, so it says how the
amps compare with each other more reliably than it says what any one will do
with another guitar.

Effects are dB per point as a control rises, where a point is a tenth of the
control's travel. The side below the default and the side above it are kept
apart, because a tone stack rarely does the same on both.
"""

from functools import lru_cache
import json
from pathlib import Path

PATH = Path(__file__).with_name("knowledge") / "controls.json"
POLARITY = PATH.with_name("polarity.json")
BANDS = ("below 200 Hz", "200–800 Hz", "800 Hz–3 kHz", "3–6 kHz", "above 6 kHz")
# The bands in which each role of styles.ROLES is judged.
ROLE_BANDS = {"bass": (0,), "thump": (0,), "mid": (1, 2), "treble": (3,), "presence": (3, 4)}
SCALE = (.5, 2.)  # A knob move is never made smaller or larger than this to even out the amps.
IDLE = .05  # dB per point. A control weaker than this is not one a larger move would wake.
# Mids are most of a guitar's spectrum, so a mid control shows up as level and hardly at all as a change against
# the rest. Its measured strength is too close to nothing to even out the amps by.
UNEVENED = ("mid",)


@lru_cache(maxsize=1)
def table():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"amps": {}, "typical": {}}


def effect(model_id, name):
    """What one knob or switch of an amp did, or None when it was not measured."""
    amp = table()["amps"].get(str(model_id), {})
    return amp.get("knobs", {}).get(name) or amp.get("switches", {}).get(name)


# How distorted a phrase sounds through an amp, from how far its peaks stand above its average. The dry
# guitar measured 17.6 dB, a clean amp near 20 and a saturated one 8 to 10.
BREAKUP = ((17., "clean"), (14., "edge of breakup"), (12., "crunch"), (10.5, "overdriven"), (0., "saturated"))


def breakup(crest):
    return next(word for floor, word in BREAKUP if crest >= floor)


def drive_curve(model_id, names):
    """How distorted an amp was at three settings of its gain control, as [(points from default, word)].

    `names` are the controls that can be the amp's gain, most likely first.
    Measured with one recorded phrase, whose pickup was not noted, and nothing in front of the amp, so an
    amp that stays clean here can still be driven hard by a pedal.
    """
    amp = table()["amps"].get(str(model_id))
    name = next((name for name in names if name in (amp or {}).get("knobs", {})), None)
    if not name:
        return None, []
    knob, start = amp["knobs"][name], amp["default"]["crest"]
    found = []
    for side in ("low", None, "high"):
        if side is None:
            found.append((0., breakup(start)))
        elif side in knob:
            moved = knob[side]["moved"]
            found.append((moved, "silent" if knob[side].get("silent") else breakup(start + moved * knob[side]["crest"])))
    return name, found


def inert():
    """The amps the unit loaded and passed the signal through untouched: no control of theirs changed anything."""
    return frozenset(table().get("inert", ()))


def idle_switch(model_id, name):
    """Whether a switch was measured and no position of it changed the sound."""
    positions = table()["amps"].get(str(model_id), {}).get("switches", {}).get(name)
    return bool(positions) and all(
        abs(found["level"]) < .01 and all(abs(band or 0) < .01 for band in found["bands"])
        for found in positions.values())


def side_strength(knob, side):
    """How far a knob moves its role's band on one side of its default, signed so that positive follows the role."""
    found = knob.get(side) or {}
    bands = [found["bands"][index] for index in ROLE_BANDS.get(knob.get("role"), ()) if "bands" in found]
    if not bands or None in bands:
        return None
    return round(knob["sign"] * sum(bands) / len(bands), 3)


def strength(model_id, name, rising=True):
    """dB per point that an amp's tone control moves its band, towards `rising` from the default.

    Positive means it works the way its role in styles.ROLES says. None means
    the control has no role or was not measured on that side.
    """
    knob = table()["amps"].get(str(model_id), {}).get("knobs", {}).get(name)
    return side_strength(knob, "high" if rising else "low") if knob else None


def scale(model_id, name, rising=True):
    """What to multiply a knob move by so this amp's control does what the typical amp's does.

    A control that was not measured, does nothing, or runs against its role is
    left alone: a larger move would not repair it.
    """
    found = strength(model_id, name, rising)
    knob = effect(model_id, name) or {}
    typical = table()["typical"].get(knob.get("role"))
    if not found or not typical or found < IDLE or knob.get("role") in UNEVENED:
        return 1.
    return min(SCALE[1], max(SCALE[0], typical / found))


@lru_cache(maxsize=1)
def polarity():
    """Per kind of model, (those measured, those that came out upside down against the rest of their kind).

    From research/measure_polarity.py. Amps were compared with amps and cabs
    with cabs, so nothing here says how an amp sits against a dry signal.
    """
    try:
        found = json.loads(POLARITY.read_text(encoding="utf-8"))
        return tuple((frozenset(found[kind]["measured"]), frozenset(found[kind]["inverted"]))
                     for kind in ("amps", "cabs"))
    except (OSError, ValueError, KeyError, TypeError):
        return ()


def opposed(first, second):
    """Whether two parallel paths, given as their model ids, come out upside down against each other.

    Blended as they are, such paths cancel where they overlap and the sound
    thins. The mixer's phase switch puts it right. Each kind is compared only
    when both paths hold as many measured models of it, since an amp beside a
    path without one was not measured. Anything else, such as a capture, is
    taken to leave the signal as it is.
    """
    turned = 0
    for measured, inverted in polarity():
        mine, theirs = ([model for model in path if model in measured] for path in (first, second))
        if len(mine) == len(theirs):
            turned += sum(model in inverted for model in mine + theirs)
    return turned % 2 == 1
