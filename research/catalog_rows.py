"""The Quad Cortex catalog as plain rows, for the research scripts in this folder.

`lookup(model_id)` is the device library's own entry (a dict with "name", "category" and "params"), with a
stock cab given the parameters it really stores (see `lab/sound/catalog.py`). `all_models()` is every entry.
"""

from qc_mcp import catalog as _library

from lab.sound import catalog


def lookup(model_id):
    info = _library.lookup(model_id)
    found = catalog.model(model_id) if info else None
    if found is not None and found.is_stock_cab:
        return {**info, "params": _library.lookup(catalog.CAB_LAYOUTS[info["category"]])["params"]}
    return info


def all_models():
    return [lookup(model_id) for model_id in _library._catalog()]
