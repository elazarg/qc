"""Read the settings behind the Quad Cortex's own named device presets.

The unit holds Neural DSP's named settings for each model, such as "Tight
Modern" on a Rectifier or "Humbucker Tamer" on an equalizer. The index is one
read. The values come back only by loading a preset onto a block, so the
harvest uses the working slot, plays no audio, and restores the preset that
was selected. Run it with `python -m tone_lab.device_presets`.
"""

from datetime import datetime, timezone
from functools import lru_cache
import json
import sys
import time

from . import models, session

PATH = session.SESSIONS / "device-presets.json"
DEFAULT_ID = "SpecialFactoryModelPresetID"
CATEGORIES = ("Guitar Amplifier", "Guitar Overdrive", "Cabsim Guitar (M)", "Equalizer", "Compressor", "Utility",
              "Delay", "Reverb", "Modulation", "Wah", "Filter", "Pitch")


def _values(block, info):
    """Pair each catalog parameter with the stored value in display units."""
    values = []
    for index, (setting, param) in enumerate(zip(block.params, info["params"])):
        entry = setting.param_values[0] if setting.param_values else None
        kind = entry.WhichOneof("value") if entry is not None else None
        if kind == "float_value":
            value = entry.float_value
            if param["type"] in ("float", "fader"):
                try:
                    float(param["min"]), float(param["max"])
                    value = round(models.to_display(block.hash, index, value), 3)
                except (TypeError, ValueError):
                    value = round(value, 4)
            else:
                value = round(value, 4)
        elif kind:
            value = getattr(entry, kind)
        else:
            value = None
        values.append([param["name"], value])
    return values


def _block(qc):
    from . import device

    grid = device.read_preset(qc)
    return next((model for model in grid.chains[0].models if model.hash), None)


def _loaded(qc, preset_id, deadline):
    while time.monotonic() < deadline:
        # The pinned transport re-queues retained packets on every matched reply, so a
        # polled request must start from an empty queue or the queue doubles each time.
        with qc._io_lock:
            qc._pending.clear()
            try:
                slot = qc.select_model_slot(0, 0, timeout_ms=1200)
            except RuntimeError:
                slot = None
            finally:
                qc._pending.clear()
        if slot and slot["value"] == preset_id:
            return True
        time.sleep(.08)
    return False


def harvest(categories=CATEGORIES, *, only=None, report=print, stop=None, path=PATH):
    """Record every factory device preset of the chosen categories. Resumes a partial file."""
    from . import device, draft_render

    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {"models": {}}
    with device.connect(write=True) as qc:
        if record.get("firmware") not in (None, qc.firmware):
            record = {"models": {}}
        record["firmware"] = qc.firmware
        record["plugins"] = read_licences(qc)
        index = {}
        for row in qc.list_model_presets():
            info = models.lookup(row["model_hash"])
            if (info and row["is_factory"] and info["category"] in categories
                    and (only is None or row["model_hash"] in only)):
                index.setdefault(row["model_hash"], []).append(row)
        todo = [model for model in sorted(index) if str(model) not in record["models"]]
        report(f"{len(todo)} models to read, {len(index) - len(todo)} already recorded")
        with draft_render.working_grid(qc) as scratch:
            draft_render.build_grid(qc, scratch, [])
            for count, model_id in enumerate(todo, 1):
                if stop is not None and stop.is_set():
                    break
                if device.live_position(qc) != draft_render.WORK_SLOT:
                    raise RuntimeError("The selected preset changed on the Quad Cortex.")
                info = models.lookup(model_id)
                entry = {"name": info["name"], "category": info["category"], "available": False, "presets": []}
                qc.add_block(model_id, row=0, column=0)
                time.sleep(.3)
                block = _block(qc)
                if block is not None and block.hash == model_id and models.stored_as_listed(block, info):
                    entry["available"] = True
                    entry["presets"].append({"id": DEFAULT_ID, "name": "Default", "values": _values(block, info)})
                    for row in index[model_id]:
                        if row["is_default"] or row["id"] == DEFAULT_ID:
                            continue
                        qc.load_model_preset(0, 0, model_id, row["id"], is_factory=True, settle_s=.12)
                        confirmed = _loaded(qc, row["id"], time.monotonic() + 2.5)
                        block = _block(qc)
                        if confirmed and block is not None and block.hash == model_id:
                            entry["presets"].append({"id": row["id"], "name": row["name"],
                                                     "values": _values(block, info)})
                if block is not None:
                    qc.delete_block(0, 0)
                    time.sleep(.2)
                    if _block(qc) is not None:
                        raise RuntimeError("The working grid did not clear between models.")
                record["models"][str(model_id)] = entry
                record["harvested_utc"] = datetime.now(timezone.utc).isoformat()
                session.write_json(path, record)
                report(f"{count}/{len(todo)} {info['name']}: "
                       + (f"{len(entry['presets'])} settings" if entry["available"] else "not available on this unit"))
    load.cache_clear()
    return record


@lru_cache(maxsize=1)
def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))["models"]
    except (OSError, ValueError, KeyError):
        return {}


def _twin(model_id):
    """A model that shares this one's name and controls. Updated models keep the old id beside the new."""
    info = models.lookup(model_id)
    names = info and [param["name"] for param in info["params"]]
    for other_id, entry in load().items():
        other = models.lookup(int(other_id))
        if (info and other and int(other_id) != model_id and other["name"] == info["name"]
                and len(entry.get("presets", [])) > 1 and [param["name"] for param in other["params"]] == names):
            return entry
    return None


def settings(model_id):
    """The named factory settings of one model, as [{"name", "values": {parameter: display}}]."""
    entry = load().get(str(model_id))
    if not entry or len(entry.get("presets", [])) <= 1:
        entry = _twin(model_id) or entry
    if not entry or not entry.get("available"):
        return []
    return [{"name": preset["name"], "values": {name: value for name, value in preset["values"]
                                                 if isinstance(value, (int, float))}}
            for preset in entry["presets"]]


def read_licences(qc):
    """The plugin ids this unit is licensed for, or None when it does not say."""
    reply = qc.read_state("License", timeout_ms=4000)
    if reply is None:
        return None
    return sorted({item.plugin_id for item in reply.licenses if item.plugin_id})


def unavailable():
    """Models this unit cannot load.

    These are the models it refused during the harvest, every other model of a
    plugin it refused one from, and, when the unit reports its licences, the
    models of plugins it holds none for.
    """
    try:
        record = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    locked = {int(model_id) for model_id, entry in record.get("models", {}).items() if not entry.get("available")}
    refused = {models.plugin(model_id) for model_id in locked} - {0}
    owned = record.get("plugins")
    locked |= {model["id"] for model in models.all_models()
               if models.plugin(model["id"]) in refused
               or owned is not None and models.plugin(model["id"]) and models.plugin(model["id"]) not in owned}
    return locked


if __name__ == "__main__":
    chosen = {int(argument) for argument in sys.argv[1:]} or None
    harvest(only=chosen)
