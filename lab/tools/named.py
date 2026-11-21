"""Reading Neural DSP's named settings for each model from the unit.

The unit holds named settings for its models, such as "Tight Modern" on a
Rectifier or "Humbucker Tamer" on an equalizer ("device presets"). Their index
is one read. Their values come back only by loading one onto a block, so each
model is added in the build slot (My Presets 255, see `slots.py`) with every
input disconnected, so nothing is heard, each named setting is loaded and the
block read back, and the block is removed again.

What it writes is the table `knowledge.table("device")` reads
(`device-presets.json` in the data folder): for each model its name, category,
whether this unit would load it at all, and each named setting's values in
display units, plus the plugins the unit says it is licensed for. A partial
file is resumed: models already read on the same firmware are skipped, so a
stopped harvest carries on where it left off. A whole harvest takes about
half an hour.

The player's preset is given back afterwards. When the engine's session was
opened (`engine.open`, which notes the player's preset on disk) the session
gives it back when it closes, or after a crash when the app next starts; the
harvest only leaves the build slot. When it was not, the harvest borrows the
preset itself (`Slots.borrowed`) and gives it back when it ends. Either way a
preset with unsaved changes is refused first, since leaving it would lose
them.

Carried over from `tone_lab.device_presets.harvest`. It is an operation run
where the unit is held: `host.call("lab.tools.named.harvest")`, or
`python -m lab named-settings`.
"""

from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from lab.sound import catalog
from lab import knowledge
from lab.sound.chain import Chain
from lab.qc.rig import held_silent
from lab.qc.slots import BUILD, BUILD_NAME
from lab.qc.unit import Mismatch

DEFAULT_ID = "SpecialFactoryModelPresetID"
CATEGORIES = ("Guitar Amplifier", "Guitar Overdrive", "Cabsim Guitar (M)", "Equalizer", "Compressor", "Utility",
              "Delay", "Reverb", "Modulation", "Wah", "Filter", "Pitch")
LOADED_WITHIN = 2.5      # seconds for the unit to say a named setting is on the block


def default_path():
    """Where `knowledge` reads the named settings from, decided when asked (tests move it)."""
    return knowledge.device_path()


def stored_as_listed(cell, info):
    """Whether a block read from the unit has the layout assumed for it.

    Every block seen on CorOS 4.1.1 holds one value more than its catalog entry. A stock cab of another
    shape is left alone instead of read by index.
    """
    return not info.is_stock_cab or len(cell.params) in (len(info.params), len(info.params) + 1)


def values(cell, info):
    """Each catalog parameter with the value the block holds, in display units where it has a range."""
    found = []
    for param, setting in zip(info.params, cell.params):
        entry = setting.param_values[0] if setting.param_values else None
        kind = entry.WhichOneof("value") if entry is not None else None
        if kind == "float_value":
            value = entry.float_value
            if param.kind in ("float", "fader") and param.low is not None and param.high is not None:
                try:
                    value = round(catalog.to_display(cell.hash, param.index, value), 3) + 0.
                except (TypeError, ValueError, ZeroDivisionError):
                    value = round(value, 4)
            else:
                value = round(value, 4)
        elif kind:
            value = getattr(entry, kind)
        else:
            value = None
        found.append([param.name, value])
    return found


def licences(device):
    """The plugin ids this unit is licensed for, or None when it does not say."""
    reply = device.read_state("License", timeout_ms=4000)
    if reply is None:
        return None
    return sorted({item.plugin_id for item in getattr(reply, "licenses", ()) if item.plugin_id})


def _read(path):
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"models": {}}
    return record if isinstance(record, dict) and isinstance(record.get("models"), dict) else {"models": {}}


def _write(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    spare = path.with_name(path.name + ".part")
    spare.write_text(json.dumps(record, indent=1), encoding="utf-8")
    os.replace(spare, path)


def _cell(device):
    """The block in row 1, column 1 as the unit stores it, or None when the cell is empty."""
    preset = device.get_current_preset()
    cell = preset.chains[0].models[0] if preset is not None and len(preset.chains) else None
    return cell if cell is not None and cell.hash else None


def _loaded(engine, preset_id):
    """Wait until the unit says the block holds `preset_id`. The request is polled with an empty queue."""
    device, unit = engine.unit.device, engine.unit
    deadline = unit.clock() + LOADED_WITHIN
    lock, pending = getattr(device, "_io_lock", None), getattr(device, "_pending", None)
    while True:
        try:
            if lock is not None and pending is not None:
                with lock:
                    pending.clear()
                    try:
                        slot = device.select_model_slot(0, 0, timeout_ms=1200)
                    finally:
                        pending.clear()
            else:
                slot = device.select_model_slot(0, 0, timeout_ms=1200)
        except RuntimeError:
            slot = None
        if slot and slot.get("value") == preset_id:
            return True
        if unit.clock() > deadline:
            return False
        unit.sleep(.08)


def _index(device, categories, only):
    index = {}
    for row in device.list_model_presets():
        info = catalog.model(row["model_hash"])
        if (info and row["is_factory"] and info.category in categories
                and (only is None or row["model_hash"] in only)):
            index.setdefault(row["model_hash"], []).append(row)
    return index


def _one(engine, model_id, rows):
    """Add one model, read its default and each named setting, and remove it. Returns its entry."""
    unit, device = engine.unit, engine.unit.device
    info = catalog.model(model_id)
    entry = {"name": info.name, "category": info.category, "available": False, "presets": []}
    unit.guard()
    device.add_block(model_id, row=0, column=0)

    def placed():
        found = _cell(device)
        return found if found is not None and found.hash == model_id else None

    try:
        cell = unit.until(placed, f"{info.name} on the build slot")
    except Mismatch:
        unit.guard()          # A block missing from another preset was not refused: the player moved.
        cell = _cell(device)
    if cell is not None and cell.hash == model_id and stored_as_listed(cell, info):
        entry["available"] = True
        entry["presets"].append({"id": DEFAULT_ID, "name": "Default", "values": values(cell, info)})
        for row in rows:
            if row["is_default"] or row["id"] == DEFAULT_ID:
                continue
            unit.guard()
            device.load_model_preset(0, 0, model_id, row["id"], is_factory=True, settle_s=.12)
            confirmed = _loaded(engine, row["id"])
            cell = _cell(device)
            if confirmed and cell is not None and cell.hash == model_id:
                entry["presets"].append({"id": row["id"], "name": row["name"], "values": values(cell, info)})
    if _cell(device) is not None:
        unit.guard()
        device.delete_block(0, 0)
        try:
            unit.until(lambda: _cell(device) is None, "the build slot cleared")
        except Mismatch:
            raise RuntimeError("The build slot did not clear between models.") from None
    return entry


def harvest(engine, *, stop=None, categories=CATEGORIES, only=None, report=None, path=None):
    """Record every factory named setting of the chosen categories (`only`: these model ids alone).

    Writes the table after each model, so a stopped or failed harvest keeps what it read. Returns a summary:
    where it was written, the firmware, how many models were read now and in all, which ones the unit would
    not load, and whether it was stopped. `report(text)` hears of each model as it is read.
    """
    say = report or (lambda text: None)
    target = Path(path) if path is not None else default_path()
    only = None if only is None else {int(model_id) for model_id in only}
    unit, slots, device = engine.unit, engine.slots, engine.unit.device
    engine.quiet()
    record = _read(target)
    if record.get("firmware") not in (None, unit.firmware):
        record = {"models": {}}           # read on another firmware: read again from the start
    record["firmware"] = unit.firmware
    record["plugins"] = licences(device)
    index = _index(device, categories, only)
    todo = [model_id for model_id in sorted(index) if str(model_id) not in record["models"]]
    say(f"{len(todo)} models to read, {len(index) - len(todo)} already recorded")
    read, stopped = [], False
    if todo:
        if engine.borrowed is not None and unit.dirty() and not slots.ours(unit.position()):
            raise RuntimeError("The preset on the Quad Cortex has unsaved changes. Save or discard them first.")
        # Without a session that gives the player's preset back, borrow it for the harvest alone.
        borrowing = slots.borrowed() if engine.borrowed is None else nullcontext()
        with borrowing:
            slots.claim(BUILD, BUILD_NAME)
            unit.slot = None
            unit.recall(BUILD)
            unit.slot = BUILD
            engine.shown = None
            try:
                unit.apply(held_silent(Chain()), stop=stop, fresh=True)   # empty, every input disconnected
                for count, model_id in enumerate(todo, 1):
                    if stop is not None and stop.is_set():
                        stopped = True
                        break
                    entry = _one(engine, model_id, index[model_id])
                    record["models"][str(model_id)] = entry
                    record["harvested_utc"] = datetime.now(timezone.utc).isoformat()
                    _write(target, record)
                    read.append(model_id)
                    say(f"{count}/{len(todo)} {entry['name']}: "
                        + (f"{len(entry['presets'])} settings" if entry["available"] else "not available on this unit"))
            finally:
                engine.shown = None
    else:
        _write(target, record)
    knowledge.table.cache_clear()
    knowledge.locked.cache_clear()
    refused = sorted(int(model_id) for model_id, entry in record["models"].items() if not entry.get("available"))
    return {"path": str(target), "firmware": unit.firmware, "read": len(read), "recorded": len(record["models"]),
            "left": len(todo) - len(read), "unavailable": refused, "plugins": record["plugins"], "stopped": stopped}
