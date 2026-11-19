"""The Quad Cortex model catalog as Tone Lab reads it.

The bundled ModelRepo lists only the two microphone selectors for a stock cab.
The device stores each of those blocks in the layout of its Default Cabsim:
two microphones, each with level, pan, distance and position, followed by the
block's high-pass, low-pass and output. Presets archived from the connected
unit (CorOS 4.1.1) show this layout for the mono guitar and bass cabs. The
stereo cabs follow the stereo default in the same catalog.
"""

from functools import lru_cache
import xml.etree.ElementTree as ET

from qc_mcp import catalog as _catalog

CAB_LAYOUTS = {"Cabsim Guitar (M)": 12000, "Cabsim Bass (M)": 12000,
               "Cabsim Guitar (ST)": 32000, "Cabsim Bass (ST)": 32000}
MIC_SELECTOR = "ir selector"


def _layout(model_id):
    info = _catalog.lookup(model_id)
    if info and [param["name"] for param in info["params"]] == [MIC_SELECTOR, MIC_SELECTOR]:
        return CAB_LAYOUTS.get(info["category"])
    return None


def lookup(model_id):
    info = _catalog.lookup(model_id)
    layout = _layout(model_id)
    return {**info, "params": _catalog.lookup(layout)["params"]} if layout else info


@lru_cache(maxsize=1)
def _plugins():
    """The Neural DSP plugin each model belongs to, from the catalog. Native models have none."""
    try:
        root = ET.parse(_catalog._XML).getroot()
    except (OSError, ET.ParseError):
        return {}
    return {int(model.get("id")): int(model.get("plugin_id")) for model in root.iter("Model")
            if (model.get("plugin_id") or "0").isdigit() and int(model.get("plugin_id") or 0)}


def plugin(model_id):
    """The id of the plugin whose licence a model needs, or 0 for a model every unit has."""
    return _plugins().get(model_id, 0)


def is_stock_cab(model_id):
    """A cab stored in the default layout, so its microphones and filters can be addressed."""
    return _layout(model_id) is not None


def all_models():
    return [lookup(model_id) for model_id in _catalog._catalog()]


def to_norm(model_id, index, display):
    return _catalog.to_norm(_layout(model_id) or model_id, index, display)


def to_display(model_id, index, value):
    return _catalog.to_display(_layout(model_id) or model_id, index, value)


def stored_as_listed(model, info):
    """Whether a cab read from the device has the layout assumed for it.

    Every block observed on CorOS 4.1.1 holds one value more than its catalog
    entry. A cab of another shape is left alone instead of addressed by index.
    """
    return not _layout(model.hash) or len(model.params) in (len(info["params"]), len(info["params"]) + 1)
