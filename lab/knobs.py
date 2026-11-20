"""The Shape knobs: bounded, directional moves of a chain's own values.

Each knob is a position from its lower limit to 1, and a set of positions
is an offset from the chain as it stands: `targets` works out every stored
value the positions would change, `apply` writes them into a new chain, and
`moves` says the same in the units the unit shows. Nothing here talks to
the unit.

Every knob is a quality the player hears (brightness, texture, body, echoes),
never one control or one device: each reaches for whichever controls this
chain has that do that job. A cab's microphone position is one of the ways
Brightness and Texture work, its low cut one of the ways Tightness and Body
work; none of them is a knob of its own.

Brightness (dark to bright) and Texture (round to rough) are the two axes tone
words collapse onto (docs/TONE_RESEARCH.md, findings 1 and 2); the desk shows them
together as one pad. "Warm" sits on the texture axis, not opposite "bright".

A knob's effect cannot be assumed from the table (amp controls interact:
docs/TONE_RESEARCH.md, finding 8), so `EXPECTED` says what each knob should do to
what listening measures, and `check` compares a sound heard with its knobs
against the same sound heard without them.

A knob's rules name a control by the kind of block and the control's name,
so one rule reaches every model that has such a control. `ROLES` gathers the
names that do one job, with a sign for the ones that work the other way
round (a Vox Tone Cut, a RAT Filter). docs/TONE_KNOWLEDGE.md, "What each control
does", records how each was checked.

Beyond the rule table:

- Drive is not a table. Up, it takes every gain control towards its top with
  a square-law taper; down, towards its floor, also turning a drive pedal's
  level back towards unity and, at the very bottom, bypassing the pedal. An
  amp's gain that rises has the level it was measured to add taken back at
  the amp's Output (`_hold_level`).
- An amp's tone controls are scaled by how strongly that amp's control was
  measured to act, so a turn means the same on every amp (mids excepted).
- Filter knobs move in octaves, and in each row only the filter doing the
  work (the lowest low-pass, the highest high-pass) takes the move.
- A knob for some quality of an effect that is parked dry (MIX at 0) brings
  it into the mix a little so the move can be heard, unless the player has
  turned that effect down.
- A value that differs by scene is moved only when a scene is named, and
  then only in that scene.
- Parameters under an expression pedal were skipped by the old code. A
  `Chain` does not record expression assignments yet, so nothing is skipped
  for that reason here; when it does, `_plan` is the one place to check it.
"""

from dataclasses import replace
import math
import re

from . import catalog, knowledge
from .chain import in_scene

AMP, PEDAL, CAPTURE = "Guitar Amplifier", "Guitar Overdrive", "Neural Capture"
GATE = 16003            # Simple Gate, the only Utility whose THRESHOLD the Gate knob moves

ROLES = {
    (AMP, "treble"): {"TREBLE": 1, "BRILLIANCE": 1, "TONE": 1, "HIGHS": 1},
    (AMP, "presence"): {"PRESENCE": 1, "TONE CUT": -1, "CUT": -1},
    (AMP, "bass"): {"BASS": 1, "LOWS": 1},
    (AMP, "thump"): {"DEPTH": 1, "RESONANCE": 1, "DEEP": 1, "TIGHT": -1},
    (AMP, "mid"): {"MID": 1, "MIDDLE": 1, "MIDS": 1},
    (PEDAL, "treble"): {"TONE": 1, "TREBLE": 1, "HIGH": 1, "SPECTRUM": 1, "PRESENCE": 1, "FILTER": -1, "CUT": -1},
    (PEDAL, "bass"): {"BASS": 1, "LOW": 1, "TIGHT": -1},
    (PEDAL, "gain"): {"GAIN": 1, "DRIVE": 1, "DISTORTION": 1, "OVERDRIVE": 1, "DIST": 1, "DRV": 1,
                      "FUZZ": 1, "SUSTAIN": 1},
}


def role(category, name, amount):
    return [(category, parameter, amount * sign) for parameter, sign in ROLES[category, name].items()]


CABS = ("Cabsim Guitar (M)", "Cabsim Guitar (ST)")
# Filter moves are in octaves.
LOW_CUT = [*((category, "HPF", 1.6) for category in CABS), ("IRLoaders", "HI PASS", 1.6),
           ("Equalizer", "HPF", 1.6), ("Equalizer", "HPF FREQ", 1.6)]
HIGH_CUT = [*((category, "LPF", -1.5) for category in CABS), ("IRLoaders", "LOW PASS", -1.5),
            ("Equalizer", "LPF", -1.5), ("Equalizer", "LPF FREQ", -1.5)]
FILTER_STAGE = {**{rule[:2]: "low" for rule in LOW_CUT}, **{rule[:2]: "high" for rule in HIGH_CUT}}
# A guitar cab passes little below 45 Hz or above 14 kHz, so a filter parked outside that span starts
# its travel from the edge of it.
AUDIBLE_SPAN = (45., 14000.)

JAZZ = [*role(PEDAL, "gain", -4), *role(PEDAL, "treble", -2), (AMP, "GAIN", -3), *role(AMP, "treble", -2),
        (CAPTURE, "GAIN", -5), (CAPTURE, "TREBLE", -2.5), (CAPTURE, "MID", 1.2), ("Delay", "MIX", -18),
        ("Delay", "FEEDBACK", -12), ("Reverb", "MIX", -8)]
BRIGHTER = [*role(PEDAL, "treble", 2), *role(AMP, "treble", 2), *role(AMP, "presence", 2), (CAPTURE, "TREBLE", 3)]
# A cab microphone toward the speaker's edge (POSITION up, measured on the unit) is darker and fuller; nearer the
# speaker (DISTANCE down) has more low end. Those are means to Brightness, Texture and Body, not knobs.
MIC_TO_EDGE = [(category, "POSITION", 1.0) for category in CABS]
# Rough is more energy in the high harmonic orders (docs/TONE_RESEARCH.md, findings 2 and 7): more gain and a more open
# top in the drive pedal, more presence, the cab's low-pass opened and the microphones toward the centre. Round is
# the opposite, the microphones off centre.
ROUGHER = [*role(PEDAL, "gain", 2.5), *role(PEDAL, "treble", 1.5), *role(AMP, "presence", 1.5)]
MIC_CLOSER = [(category, "DISTANCE", -1.0) for category in CABS]


def scaled(rules, factor):
    return [(category, parameter, move * factor) for category, parameter, move in rules]

# key: (title, lower limit, [(block category, parameter, move at full turn)]). Amp and pedal moves are
# written for a 0-10 knob and scale to the control's own range; every other move is in display units.
KNOBS = {
    "jazz": ("Jazz", 0, JAZZ),
    "drive": ("Distortion", -1, []),  # Worked out by `_drive_delta`, not by rules.
    "brightness": ("Brightness", -1, [*BRIGHTER, *scaled(MIC_TO_EDGE, -2.5), *scaled(HIGH_CUT, -.8)]),
    "texture": ("Texture", -1, [*ROUGHER, *scaled(HIGH_CUT, -1.), *scaled(MIC_TO_EDGE, -1.5)]),
    "space": ("Space", -1, [("Delay", "MIX", 18.0), ("Delay", "FEEDBACK", 12.0),
                            ("Reverb", "MIX", 12.0), ("IRLoaders", "ROOM MIX", 10.0)]),
    "tightness": ("Tightness", -1, [*role(AMP, "bass", -2.0), *role(AMP, "thump", -2.5),
                                      (CAPTURE, "BASS", -3.0), *role(PEDAL, "bass", -1.8), *scaled(LOW_CUT, .65)]),
    "attack": ("Pick attack", -1, [("Compressor", "ATTACK", 18.0), ("Compressor", "MIX", -15.0)]),
    "sustain": ("Sustain", -1, [("Compressor", "THRESHOLD", -10.0), ("Compressor", "MIX", 20.0),
                                  (AMP, "GAIN", 1.2), (CAPTURE, "GAIN", 2.5)]),
    "gate": ("Quiet between notes", -1, [("Utility", "THRESHOLD", 15.0)]),
    "body": ("Body", -1, [*role(AMP, "bass", 2.0), *role(AMP, "thump", 1.5), (CAPTURE, "BASS", 3.0),
                          *scaled(LOW_CUT, -.5), *scaled(MIC_CLOSER, 2.0)]),
    "mids": ("Mids", -1, [*role(AMP, "mid", 2.0), (CAPTURE, "MID", 3.0)]),
    "presence": ("Presence", -1, [*role(AMP, "presence", 2.5), (CAPTURE, "TREBLE", 2.0),
                                    *role(PEDAL, "treble", 1.7)]),
    "delay": ("Echoes", -1, [("Delay", "MIX", 30.0)]),
    "delay_time": ("Echo timing", -1, [("Delay", "DELAY TIME", 150.0)]),
    "reverb": ("Room", -1, [("Reverb", "MIX", 25.0), ("IRLoaders", "ROOM MIX", 10.0)]),
    "modulation": ("Movement", -1, [("Modulation", "MIX", 25.0), ("Modulation", "DEPTH", 20.0)]),
    "width": ("Width", -1, [("Modulation", "WIDTH", 30.0), ("Delay", "WIDTH", 25.0),
                              ("Delay", "STEREO WIDTH", 25.0), ("Reverb", "WIDTH", 25.0)]),
    "compression": ("Evenness", -1, [("Compressor", "THRESHOLD", -9.0), ("Compressor", "MIX", 19.0),
                                      ("Compressor", "RATIO", 1.4)]),
    "punch": ("Pick bite", -1, [*role(AMP, "presence", 1.8), *role(AMP, "treble", .9), (CAPTURE, "MID", .8),
                                 (CAPTURE, "TREBLE", 1.3), ("Equalizer", "2kHz", 1.8)]),
    "tail": ("Tail length", -1, [("Reverb", "DECAY", .75), ("Delay", "FEEDBACK", 25.0)]),
    "motion": ("Movement speed", -1, [("Modulation", "RATE", 1.2), ("Modulation", "CHR RATE", .6),
                                    ("Modulation", "VIB RATE", 1.7)]),
    "bloom": ("Bloom", 0, [("Compressor", "THRESHOLD", -5.0), ("Compressor", "MIX", 12.0),
                          ("Compressor", "ATTACK", -5.0), ("Delay", "FEEDBACK", 8.0),
                          ("Reverb", "MIX", 9.0), (AMP, "GAIN", .7)]),
    "band_fit": ("Band fit", 0, [*role(AMP, "bass", -1.6), *role(AMP, "mid", 1.2),
                                (CAPTURE, "BASS", -1.8), (CAPTURE, "MID", 1.4),
                                ("Delay", "MIX", -6.0), ("Reverb", "MIX", -7.0),
                                ("Equalizer", "125Hz", -1.6), ("Equalizer", "1kHz", 1.4)]),
}

PRIMARY = ("drive", "brightness", "texture", "punch", "space", "jazz")
PAD = ("brightness", "texture")    # shown together on the Shape panel as one plane: across, then up
GROUPS = {
    "Tone": ("body", "tightness", "mids", "presence"),
    "Response": ("attack", "sustain", "compression", "gate"),
    "Effects": ("delay", "delay_time", "reverb", "tail", "modulation", "motion", "width"),
    "Character": ("jazz", "bloom", "band_fit"),
}
# Knobs that were named for a mechanism or a word that turned out to lie on another axis, the quality that now
# does their job, and which way: Warmth turned up was darker (Brightness down), Smoothness turned up was rounder
# (Texture down). Positions stored on a sound under the old name are carried over, so a shaped sound keeps its shape.
RENAMED = {"low_cut": ("tightness", 1), "soft_top": ("texture", -1), "repeats": ("tail", 1),
           "mic": ("brightness", -1), "warmth": ("brightness", -1), "smoothness": ("texture", -1)}


def normalize(values):
    """Knob positions with any old knob carried over to the quality that now does its job, in its direction.
    Everything else is left as it is, for `targets` to check."""
    found = dict(values or {})
    for old, (new, sign) in RENAMED.items():
        if old in found:
            moved = found.pop(old)
            held = found.get(new, 0.)
            def position(value):
                return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

            if not (position(moved) and position(held)):
                found[new] = held if position(moved) else moved     # not a position: left for `targets` to refuse
                continue
            found[new] = round(max(KNOBS[new][1], min(1., held + sign * moved)), 3)
    return found

DRIVE_CATEGORIES = {"Guitar Overdrive", "Bass Overdrive", "Guitar Amplifier", "Bass Amplifier", "Neural Capture"}
PEDAL_CATEGORIES = {"Guitar Overdrive", "Bass Overdrive"}
AMP_CATEGORIES = {"Guitar Amplifier", "Bass Amplifier"}
SCALED_CATEGORIES = AMP_CATEGORIES | PEDAL_CATEGORIES
GAIN_NAMES = {"PREAMP", "PREAMP GAIN", *ROLES[PEDAL, "gain"]}
# Amp controls that follow a rule written for GAIN.
AMP_GAIN_ALIASES = {"GAIN 1", "GAIN 2", "GAIN I", "GAIN II", "INPUT GAIN", "GAIN NORMAL", "GAIN OD"}
AMP_INPUT_GAIN_NAMES = {"GAIN", *AMP_GAIN_ALIASES}
PEDAL_LEVEL_NAMES = {"LEVEL", "VOLUME", "OUTPUT"}
DRIVE_BYPASS = -0.98
# A knob for a quality of a parked effect brings it in at this much MIX (%) at full turn, unless the
# knob named here is turned down.
DRY_EFFECT_MIX = {"Compressor": ("compression", 25), "Delay": ("delay", 16),
                  "Reverb": ("reverb", 16), "Modulation": ("modulation", 16)}
DRY_EFFECT_DIRECTIONS = {"attack", "delay_time", "tail", "motion", "width"}


def _volume_is_the_drive():
    """Amps with a Volume and no gain control, such as a Vox, a blackface Fender or a Dumble.

    Turning those up is how they break up, so Volume takes the place of Gain.
    """
    found = set()
    for model in catalog.models():
        if model.category == AMP:
            names = {param.name for param in model.params if param.kind in ("float", "fader")}
            if "VOLUME" in names and not names & (AMP_INPUT_GAIN_NAMES | {"DRIVE", "VOLUME NORMAL", "VOLUME BRIGHT"}):
                found.add(model.id)
    return frozenset(found)


AMP_VOLUME_AS_DRIVE = _volume_is_the_drive()


# ---- What each amp's controls were measured to do (controls.json) ----

ROLE_BANDS = {"bass": (0,), "thump": (0,), "mid": (1, 2), "treble": (3,), "presence": (3, 4)}
SCALE = (.5, 2.)    # A knob move is never made smaller or larger than this to even out the amps.
IDLE = .05          # dB per point. A control weaker than this is not one a larger move would wake.
UNEVENED = ("mid",)  # A mid control shows up mostly as level, too faint to even the amps out by.


def _measured(model_id):
    return knowledge.table("controls").get("amps", {}).get(str(model_id), {})


def _effect(model_id, name):
    """What one knob or switch of an amp was measured to do, or None."""
    amp = _measured(model_id)
    return amp.get("knobs", {}).get(name) or amp.get("switches", {}).get(name)


def _scale(model_id, name, rising=True):
    """What to multiply a move of an amp's tone control by so that it does what the typical amp's does."""
    knob = _measured(model_id).get("knobs", {}).get(name)
    if not knob:
        return 1.
    found = knob.get("high" if rising else "low") or {}
    bands = [found["bands"][index] for index in ROLE_BANDS.get(knob.get("role"), ()) if "bands" in found]
    strength = None if not bands or None in bands else round(knob["sign"] * sum(bands) / len(bands), 3)
    typical = knowledge.table("controls").get("typical", {}).get(knob.get("role"))
    if not strength or not typical or strength < IDLE or knob.get("role") in UNEVENED:
        return 1.
    return min(SCALE[1], max(SCALE[0], typical / strength))


# ---- Working out the moves ----

def _rules(values):
    rules, previews = {}, {}
    for key, value in values.items():
        if key not in KNOBS:
            raise ValueError(f"There is no knob {key!r}.")
        _, lower, edits = KNOBS[key]
        value = float(value)
        if not math.isfinite(value) or not lower <= value <= 1:
            raise ValueError(f"Invalid amount for {key}.")
        for category, parameter, delta in edits:
            rules[category, parameter] = rules.get((category, parameter), 0.) + value * delta
            if key in DRY_EFFECT_DIRECTIONS and category in DRY_EFFECT_MIX:
                mix_knob, mix_percent = DRY_EFFECT_MIX[category]
                explicit_cut = values.get(mix_knob, 0) < 0 or (
                    category in {"Delay", "Reverb"} and values.get("space", 0) < 0)
                if value and parameter != "MIX" and not explicit_cut:
                    previews[category, parameter] = max(previews.get((category, parameter), 0), abs(value) * mix_percent)
    return rules, previews


def _number(block, index, scene):
    """A block's stored number at a parameter, None when unset or not a number, or when it differs by scene
    and no scene is named."""
    stored = block.values.get(index)
    if stored is None or isinstance(stored, tuple) and scene is None:
        return None
    value = in_scene(stored, scene or 0)
    return None if isinstance(value, str) else float(value)


def _written(block, index, raw, scene):
    """The new stored value: in the named scene only when the value differs by scene."""
    stored = block.values.get(index)
    if isinstance(stored, tuple):
        return tuple(raw if position == scene else old for position, old in enumerate(stored))
    return raw


def _sync_enabled(block, info, scene):
    for param in info.params:
        if param.name == "SYNC":
            stored = block.values.get(param.index)
            value = None if stored is None else in_scene(stored, scene or 0)
            return isinstance(value, float) and value >= .5
    return False


def _filter_owners(chain, scene):
    """In each row, the column whose filter is doing the work: the lowest low-pass and the highest high-pass."""
    owners = {}
    for row, found in enumerate(chain.rows):
        best = {}
        for column, block in found.blocks:
            info = block.info
            if not info or in_scene(block.bypass, scene or 0):
                continue
            for param in info.params:
                stage = FILTER_STAGE.get((info.category, param.name))
                stored = block.values.get(param.index)
                value = None if stored is None else in_scene(stored, scene or 0)
                if not stage or not isinstance(value, float):
                    continue
                shown = catalog.to_display(block.model, param.index, value)
                rank = shown if stage == "high" else -shown
                if stage not in best or rank < best[stage][0] - 1e-6:
                    best[stage] = (rank, column)
        owners.update({(row, stage): column for stage, (_, column) in best.items()})
    return owners


def _drive_delta(category, name, drive, before, low, high, units, *, amp_volume):
    gain_control = name in GAIN_NAMES or amp_volume or (category in AMP_CATEGORIES and name in AMP_INPUT_GAIN_NAMES)
    if category in DRIVE_CATEGORIES and gain_control:
        if drive < 0:
            return (before - low) * drive
        # Full right is the top of the control, as full left is its floor. The square keeps small turns small:
        # a third of the way adds about a tenth of what is left. A fuzz does most of its work in the last point,
        # so a knob that stopped short of the top never reached it.
        return (high - before) * drive * drive
    if drive < 0 and category in PEDAL_CATEGORIES and name in PEDAL_LEVEL_NAMES:
        unity = 0 if units == "dB" and low <= 0 <= high else low + .55 * (high - low)
        return max(0, before - unity) * drive
    return 0.0


def _edit(row, column, block, param, before, after, raw, scene):
    return {"row": row, "column": column, "model": block.name, "index": param.index, "name": param.name,
            "from": before, "to": after, "stored": _written(block, param.index, raw, scene)}


def _hold_level(row, column, block, info, lift, scene):
    """Take back, at the amp's clean Output, the level its gain was measured to add.

    Drive asks for more distortion, not more volume. On an amp that stays clean, gain is mostly volume:
    a Hiwatt taken from 5 to 10 measured 8 dB louder. The figure is the amp's own, measured with nothing
    in front of it, so with a pedal driving it the result errs towards quieter.
    """
    for param in info.params:
        if param.name != "OUTPUT" or param.units != "dB":
            continue
        current = _number(block, param.index, scene)
        if current is None:
            continue
        before = catalog.to_display(block.model, param.index, current)
        after = max(param.low, before - lift)
        return _edit(row, column, block, param, before, after, catalog.to_norm(block.model, param.index, after), scene)
    return None


def _plan(chain, values, scene=None):
    """Every edit the knob positions make, in the order the old desk listed them."""
    values = normalize(values)
    rules, previews = _rules(values)
    drive = float(values.get("drive", 0.))
    preview_categories = {category for category, _ in previews}
    owners = _filter_owners(chain, scene)
    edits = []
    for row, column, block in chain.blocks:
        info = block.info
        if not info:
            continue
        category = info.category
        if in_scene(block.bypass, scene or 0):
            continue
        if drive <= DRIVE_BYPASS and category in PEDAL_CATEGORIES:
            if not isinstance(block.bypass, tuple):
                edits.append({"kind": "bypass", "row": row, "column": column, "model": block.name,
                              "name": "BYPASS", "from": False, "to": True, "stored": True})
            elif scene is not None:
                edits.append({"kind": "bypass", "row": row, "column": column, "model": block.name,
                              "name": "BYPASS", "from": False, "to": True,
                              "stored": tuple(True if position == scene else flag
                                              for position, flag in enumerate(block.bypass))})
        dry_mix, preview = None, 0.
        lift = 0.  # dB the amp gets louder from the gain the Drive knob adds.
        for param in info.params:
            name = param.name
            amp_volume = category == AMP and (
                name in {"VOLUME BRIGHT", "VOLUME NORMAL"} or name == "VOLUME" and block.model in AMP_VOLUME_AS_DRIVE)
            delta = rules.get((category, name), 0.)
            if amp_volume or (category == AMP and name in AMP_GAIN_ALIASES):
                delta += rules.get((category, "GAIN"), 0.)
            if category == "Utility" and name == "THRESHOLD" and block.model != GATE:
                continue
            if param.kind not in ("float", "fader"):
                continue
            current = _number(block, param.index, scene)
            if current is None:
                continue
            if not delta and not (drive and category in DRIVE_CATEGORIES) and not (
                    name == "MIX" and category in preview_categories):
                continue
            if category == "Delay" and name == "DELAY TIME" and _sync_enabled(block, info, scene):
                continue
            low, high = param.low, param.high
            if low is None or high is None:
                continue
            if category in SCALED_CATEGORIES:
                delta *= (high - low) / 10
            if category == AMP and delta:
                # Amps differ widely in how much a tone control does, so the same turn is made to mean the same.
                delta *= _scale(block.model, name, rising=delta > 0)
            if category == "Compressor" and name == "ATTACK" and param.units != "ms":
                delta *= (high - low) * .2 / 18
            if category == "Reverb" and name == "DECAY" and param.units != "s":
                delta *= (high - low) * .22 / .75
            before = catalog.to_display(block.model, param.index, current)
            stage = FILTER_STAGE.get((category, name))
            if stage and delta:
                if owners.get((row, stage)) != column:
                    continue
                start = (max(before, AUDIBLE_SPAN[0]) if stage == "low" and delta > 0
                         else min(before, AUDIBLE_SPAN[1]) if stage == "high" and delta < 0 else before)
                delta = start * 2 ** delta - before
            # The bottom of a conventional 0-10 preamp input knob can mute the amp. Keep low-drive moves
            # audible even when their offsets stack.
            amp_input_gain = category in AMP_CATEGORIES and name in AMP_INPUT_GAIN_NAMES and (low, high) == (0, 10)
            floor = min(before, low + .15 * (high - low)) if amp_volume or amp_input_gain else low
            pushed = _drive_delta(category, name, drive, before, floor, high, param.units, amp_volume=amp_volume)
            delta += pushed
            after = min(high, max(floor, before + delta))
            if pushed > 0 and category == AMP and after > before:
                louder = ((_effect(block.model, name) or {}).get("high") or {}).get("level") or 0.
                lift += max(0., louder) * min(pushed, after - before) / ((high - low) / 10)
            if name == "MIX" and param.units == "%" and low == 0 and abs(before) < 1e-6 and abs(after) < 1e-6:
                dry_mix = param, high
            if math.isclose(before, after, abs_tol=0.001):
                continue
            preview = max(preview, previews.get((category, name), 0.))
            edits.append(_edit(row, column, block, param, before, after,
                               catalog.to_norm(block.model, param.index, after), scene))
        if dry_mix and preview:
            param, high = dry_mix
            after = min(high, preview)
            edits.append(_edit(row, column, block, param, 0., after,
                               catalog.to_norm(block.model, param.index, after), scene))
        if lift > .05:
            held = _hold_level(row, column, block, info, lift, scene)
            if held:
                edits.append(held)
    return edits


# ---- The interface ----

def targets(chain, values, *, scene=None):
    """Every stored value the knob positions would change, as {(row, column, index): new stored value}.

    A bypass change is keyed (row, column, None). A value that differs by scene comes back as the whole
    eight: with `scene`, only that scene's entry changed; without one, each scene's entry moved as the knob
    moves it in that scene, so a knob works on a scene-switched preset as a whole.
    """
    def keyed(scene):
        return {(edit["row"], edit["column"], None if edit.get("kind") == "bypass" else edit["index"]): edit["stored"]
                for edit in _plan(chain, values, scene)}

    found = keyed(scene)
    if scene is not None:
        return found
    per_scene = [keyed(each) for each in range(8)]
    for key in set().union(*per_scene):
        moved = [per_scene[each].get(key) for each in range(8)]
        if all(isinstance(value, tuple) for value in moved if value is not None) and key not in found:
            row, column, index = key
            block = chain.block(row, column)
            held = block.bypass if index is None else dict(block.params).get(index)   # all eight scenes
            if not isinstance(held, tuple):
                continue
            found[key] = tuple(moved[each][each] if moved[each] is not None else held[each] for each in range(8))
    return found


def apply(chain, values, *, scene=None):
    """A new chain with the knob positions' moves written into it."""
    for (row, column, index), value in targets(chain, values, scene=scene).items():
        block = chain.block(row, column)
        block = replace(block, bypass=value) if index is None else block.set(index, value)
        chain = chain.with_block(row, column, block)
    return chain


def moves(chain, values, *, scene=None):
    """The same edits as the desk shows them: [{"row", "column", "model", "name", "from", "to"}] in display
    units, rows and columns counted from 0 as in `Chain`. A bypass has "kind": "bypass" and from/to flags."""
    return [{key: value for key, value in edit.items() if key != "stored"} for edit in _plan(chain, values, scene)]


def _filled(chain):
    """The chain with every value it leaves unset taken from the catalog, as a design that is not built yet."""
    for row, column, block in chain.blocks:
        defaults = catalog.defaults(block.model)
        if set(defaults) - set(block.values):
            chain = chain.with_block(row, column, replace(block, params={**defaults, **block.values}))
    return chain


def controls(chain, *, scene=None):
    """Which controls each knob moves on this chain, as {knob: ((model, Control), ...)}.

    One answer for a chain read from the unit and for a design not yet built: values a design leaves to the
    unit are taken at the catalog's defaults. Each knob is tried at both ends, so this is exactly what
    `targets` would move there. This replaces the old pair `available_knobs` and `draft_controls`, which
    disagreed on bass amps: the estimate offered gain aliases on any amp, while the moves gave them only to
    guitar amps. Here the moves decide. A bass amp's tone controls answer to no knob, because the rules are
    written for guitar amps; Drive reaches its gain controls (GAIN, GAIN 1, GAIN 2), so a bass amp offers
    Drive and nothing else of its own.
    """
    chain = _filled(chain)
    found = {}
    for key, (_, lower, _) in KNOBS.items():
        ends = [1.] + ([float(lower)] if lower < 0 else [])
        named = [(edit["model"], edit["name"].title() if edit["name"].isupper() else edit["name"])
                 for end in ends for edit in _plan(chain, {key: end}, scene)]
        if named:
            found[key] = tuple(dict.fromkeys(named))
    return found


def available(chain, *, scene=None):
    """The knob keys that would change something on this chain, in the order of `KNOBS`."""
    return list(controls(chain, scene=scene))


# ---- Checking a knob against what listening measures ----

# What each knob turned up should do to a measurement of `lab.measure.Heard`'s record ("probe.x" is in its test-tone
# record): (measurement, sign, the change a full turn must make at least, units). A knob with several measurements
# has alternative measures of one quality: it did as intended when any of them moved enough the intended way. Pick
# attack turned up lets the pick through (a slower compressor attack), so the onset gets quicker: attack_ms down.
EXPECTED = {
    "brightness": (("brightness_oct", 1, .15, "octave"),),
    # Texture moves the balance of high harmonic orders against low ones (measured on the unit, from -6 dB on a
    # Plexi at low gain to +4 dB on a Soldano lead), and, near hard clipping, their share.
    "texture": (("probe.order_balance_db", 1, 2., "dB"), ("probe.tristimulus3", 1, .03, "")),
    "drive": (("probe.distortion_db", 1, 3., "dB"), ("probe.distortion_soft_db", 1, 3., "dB")),
    "body": (("low_end_db", 1, 1.5, "dB"),),
    "tightness": (("low_end_db", -1, 1.5, "dB"),),
    "space": (("tail_db", 1, 3., "dB"),),
    "reverb": (("tail_db", 1, 3., "dB"),),
    "delay": (("tail_db", 1, 3., "dB"),),
    "tail": (("tail_db", 1, 3., "dB"),),
    "sustain": (("compression", 1, .05, ""),),
    "compression": (("compression", 1, .05, ""),),
    "attack": (("attack_ms", -1, 2., "ms"),),
}
# What each measurement is, in words for the status line.
MEASURES = {"brightness_oct": "spectral centre", "tristimulus3": "share of harmonics 5 and up",
            "order_balance_db": "high harmonic orders against low ones",
            "weighted_distortion_db": "order-weighted harmonics", "distortion_db": "harmonics played firmly",
            "distortion_soft_db": "harmonics 12 dB softer", "low_end_db": "low end", "tail_db": "tail",
            "compression": "compression", "attack_ms": "onset time"}
SMALLEST_TURN = .25    # a turn smaller than this is held to the change a quarter turn must make
VERDICTS = ("as intended", "too small", "opposite", "not measurable")


def _reading(record, path):
    if path == "probe.order_balance_db":       # derived: 5th-10th against 2nd-3rd
        high, low = _reading(record, "probe.high_order_db"), _reading(record, "probe.low_order_db")
        return None if high is None or low is None else high - low
    value = record or {}
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) \
        else None


def check(before, after, values):
    """Did each knob do what it is for? `before` is the sound heard without its knobs, `after` with them at
    `values`, both `Heard` records.

    Returns {knob: {"measure", "change", "sign", "verdict", "units"}} for every knob turned: the measured change
    (after minus before), the sign it should have, and "as intended", "too small" (the right way but less than
    the knob's share of a full turn's minimum, or the wrong way by less than that), "opposite" (the wrong way by
    at least that) or "not measurable" (no measurement for that knob, or one missing from either record)."""
    found = {}
    for key, value in (values or {}).items():
        if not isinstance(value, (int, float)) or not value:
            continue
        intended = 1 if value > 0 else -1
        readings = []
        for path, sign, full, units in EXPECTED.get(key, ()):
            first, second = _reading(before, path), _reading(after, path)
            if first is None or second is None:
                continue
            change = second - first
            least = full * max(abs(value), SMALLEST_TURN)
            towards = change * sign * intended
            verdict = "as intended" if towards >= least else "opposite" if towards <= -least else "too small"
            readings.append((towards / least, {"measure": path.removeprefix("probe."), "change": round(change, 3),
                                               "sign": sign * intended, "verdict": verdict, "units": units}))
        if not readings:
            expected = EXPECTED.get(key)
            found[key] = {"measure": None, "change": None, "sign": expected[0][1] * intended if expected else None,
                          "verdict": "not measurable", "units": ""}
            continue
        # The measure that moved furthest the intended way speaks for the knob.
        found[key] = max(readings, key=lambda pair: pair[0])[1]
    return found


# ---- Plain words as knob moves ----

AMOUNTS = {"a touch": .5, "a tad": .5, "a bit": .5, "a little bit": .5, "a little": .5, "slightly": .5,
           "just a bit": .5, "somewhat": .7, "a lot": 1.7, "much": 1.7, "way": 2., "far": 1.7, "really": 1.5,
           "very": 1.5, "so much": 1.7}
COMPARATIVES = {
    "brighter": ("brightness", .3), "darker": ("brightness", -.35), "duller": ("brightness", -.3),
    # Warm is round against rough, not dark against bright (docs/TONE_RESEARCH.md, finding 1).
    "warmer": ("texture", -.3), "rounder": ("texture", -.3), "smoother": ("texture", -.3),
    "rougher": ("texture", .3), "harsher": ("texture", .3),
    "tighter": ("tightness", .3), "looser": ("tightness", -.3),
    "dirtier": ("drive", .3), "heavier": ("drive", .3), "crunchier": ("drive", .25), "cleaner": ("drive", -.3),
    "drier": ("space", -.3), "dryer": ("space", -.3), "wetter": ("space", .3), "roomier": ("space", .3),
    "fuller": ("body", .3), "thicker": ("body", .3), "fatter": ("body", .3), "thinner": ("body", -.3),
    "punchier": ("punch", .3), "wider": ("width", .3), "narrower": ("width", -.3), "jazzier": ("jazz", .4),
}
# A quantity is a knob, or (knob, -1) for one whose "more" turns the knob down ("more warmth" is rounder).
QUANTITIES = {
    "gain": "drive", "drive": "drive", "distortion": "drive", "dirt": "drive", "saturation": "drive",
    "crunch": "drive", "reverb": "reverb", "delay": "delay", "echo": "delay", "echoes": "delay", "repeats": "tail",
    "fizz": "texture", "roughness": "texture", "texture": "texture", "brightness": "brightness",
    "smoothness": ("texture", -1), "warmth": ("texture", -1), "movement": "modulation",
    "sustain": "sustain", "mids": "mids", "midrange": "mids", "body": "body", "bass": "body", "low end": "body",
    "lows": "body", "bite": "punch", "punch": "punch", "attack": "attack", "presence": "presence",
    "treble": "presence", "highs": "presence", "top end": "presence", "space": "space", "room": "space",
    "ambience": "space", "compression": "compression", "modulation": "modulation", "tremolo": "modulation",
    "chorus": "modulation", "width": "width", "jazz": "jazz", "tightness": "tightness",
    "gate": "gate",
}
COMPLAINTS = {
    "bright": ("brightness", -.3), "shrill": ("brightness", -.3), "harsh": ("texture", -.3),
    "rough": ("texture", -.3), "fizzy": ("texture", -.35), "dark": ("brightness", .3), "dull": ("brightness", .3),
    "round": ("texture", .3), "muddy": ("tightness", .3), "boomy": ("tightness", .3),
    "thin": ("body", .3), "dry": ("space", .3), "wet": ("space", -.3), "washy": ("space", -.3),
    "dirty": ("drive", -.3), "distorted": ("drive", -.3), "clean": ("drive", .3), "noisy": ("gate", .3),
    "compressed": ("compression", -.3),
}
STEPS = {"jazz": .4, "delay": .3}
_AMOUNT = "|".join(sorted(map(re.escape, AMOUNTS), key=len, reverse=True))
_QUANTITY = "|".join(sorted(map(re.escape, QUANTITIES), key=len, reverse=True))
_LEAD = (r"(?:(?:can you |could you |please )?(?:make (?:it|this|that|the tone|the sound) "
         r"|it(?:'s| is| sounds| feels) |sounds |needs |i need |i want |give (?:me|it) )?)")


def _quantity(word):
    found = QUANTITIES[word]
    return found if isinstance(found, tuple) else (found, 1)


def direction(text):
    """Read a plain request for more or less of one quality as a bounded knob move, or None.

    Only whole, unambiguous phrases are recognized, such as "a bit tighter", "much less reverb" or
    "too bright". Anything else is left to the assistant.
    """
    words = " ".join(re.findall(r"[a-z']+", text.lower().replace("’", "'"))).replace("moar ", "more ")
    words = re.sub(r"\s*(?:please|thanks|thank you)$", "", words)
    simple = re.fullmatch(rf"{_LEAD}(?:({_AMOUNT}) )?(\w+)", words)
    if simple and simple[2] in COMPARATIVES:
        key, step = COMPARATIVES[simple[2]]
        return {key: round(step * AMOUNTS.get(simple[1], 1.), 2)}
    measured = re.fullmatch(rf"{_LEAD}(?:({_AMOUNT}) )?(more|less) ({_QUANTITY})", words)
    if measured:
        key, sign = _quantity(measured[3])
        step = STEPS.get(key, .3) * AMOUNTS.get(measured[1], 1.) * sign
        return {key: round(step if measured[2] == "more" else -step, 2)}
    lacking = re.fullmatch(rf"{_LEAD}(too much|not enough|too little) ({_QUANTITY})", words)
    if lacking:
        key, sign = _quantity(lacking[2])
        return {key: round(STEPS.get(key, .3) * sign * (-1 if lacking[1] == "too much" else 1), 2)}
    complaint = re.fullmatch(rf"{_LEAD}(?:({_AMOUNT}) )?too (\w+)", words)
    if complaint and complaint[2] in COMPLAINTS:
        key, step = COMPLAINTS[complaint[2]]
        return {key: round(step * AMOUNTS.get(complaint[1], 1.), 2)}
    return None
