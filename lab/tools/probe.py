"""The smallest side-tool operations: what the unit says about itself. Also the example for writing one."""


def firmware(engine, *, stop=None):
    return engine.unit.firmware


def scene(engine, *, stop=None):
    """The scene the unit is on."""
    return engine.unit.scene()


def held(engine, *, stop=None):
    """The chain the unit holds now, as a record."""
    return engine.unit.read().to_record()


def unit_state(engine, *, stop=None):
    """What the unit says about itself and the app's place on it. Reads only; nothing is loaded or written.

    The one answer for the sound check, the Usage window and `python -m lab doctor --unit`:
    {"firmware", "preset", "position", "scene", "unsaved" (True, False, or None when the unit did not say),
    "calibrated" (the USB round trip is measured for this firmware), "cached" and "cache_slots" (built chains
    kept on the unit, of how many), "tone_lab" (the Tone Lab setlist is there) with "problem" (why not),
    "ours" (the selected preset is one of the app's own slots), "dry_channel" (the USB channel the dry guitar
    arrives on, or the reason it could not be told)}.
    """
    from lab.hearing.listen import calibration
    from lab.qc.device import recording_channels
    from lab.qc.slots import CACHE_SLOTS

    unit, slots = engine.unit, engine.slots
    position = unit.position()
    found = {"firmware": unit.firmware, "preset": unit.name(), "position": position, "scene": unit.scene(),
             "unsaved": unit.dirty(),
             "calibrated": engine.offset is not None or calibration(slots.folder, unit.firmware) is not None,
             "cached": len(slots.index()), "cache_slots": len(CACHE_SLOTS), "tone_lab": True, "problem": None}
    try:
        slots.refresh()
    except RuntimeError as error:
        found["tone_lab"], found["problem"] = False, str(error)
    found["ours"] = slots.ours(position)
    try:
        found["dry_channel"] = recording_channels(unit.device, engine.physical_input)[0]
    except (RuntimeError, ValueError) as error:
        found["dry_channel"] = str(error)
    return found
