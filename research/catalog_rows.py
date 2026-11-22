"""The Quad Cortex catalog as plain rows, for the research scripts in this folder.

`lookup(model_id)` is the device library's own entry (a dict with "name", "category" and "params"), with a
stock cab given the parameters it really stores (see `lab/sound/catalog.py`). `all_models()` is every entry.

Two more things the measuring scripts ask of it: the text a stock cab's microphones are named with
(`microphone_prefix`), and which models the unit would not load when its named settings were read
(`unavailable`), so that a sweep does not spend its time on them.
"""

from qc_mcp import catalog as _library

from lab import knowledge
from lab.sound import catalog


def lookup(model_id):
    info = _library.lookup(model_id)
    found = catalog.model(model_id) if info else None
    if found is not None and found.is_stock_cab:
        return {**info, "params": _library.lookup(catalog.CAB_LAYOUTS[info["category"]])["params"]}
    return info


def all_models():
    return [lookup(model_id) for model_id in _library._catalog()]


def microphone_prefix(model_id):
    """What comes before a microphone's name in the text a stock cab's microphone selector holds, such as
    "ML_412 Brit 60B GB 71_" before "Dynamic 57": from the selector's default in the device library. "" for a
    model that is not a stock cab, or one whose default the library does not give."""
    info = _library.lookup(model_id)
    found = catalog.model(model_id) if info else None
    if found is None or not found.is_stock_cab:
        return ""
    cab, mark, _ = str(info["params"][0].get("default") or "").rpartition("_")
    return cab + mark


def unavailable():
    """The models the unit would not load when its named settings were read (`python -m lab named-settings`),
    the other models of their plugins, and, when the unit said which plugins it is licensed for, the models of
    every other plugin.

    Not `lab.knowledge.locked`, which also locks the amps that were measured to do nothing: a sweep must go on
    measuring those, or a table made from it would no longer say that they do nothing."""
    record = knowledge.table("device")
    refused = {int(model_id) for model_id, entry in record.get("models", {}).items() if not entry.get("available")}
    plugins = {catalog.model(model_id).plugin for model_id in refused if catalog.model(model_id)} - {0}
    owned = record.get("plugins")
    return refused | {model.id for model in catalog.models()
                      if model.plugin and (model.plugin in plugins or owned is not None and model.plugin not in owned)}
