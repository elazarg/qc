"""The Quad Cortex's models and their parameters.

This is the one place that talks to the device library's catalog. It knows
two things the bundled catalog gets wrong or leaves out, both read from the
unit (CorOS 4.1.1):

- A stock cab is stored in the layout of the Default Cabsim: two microphones,
  each with bypass, selector, level, pan, distance and position, then the
  block's high-pass, low-pass and output. The catalog lists only the two
  microphone selectors.
- Every block holds one value more than its catalog entry. Its bypass is
  addressed as the parameter after that one, at the number of values the
  unit holds, when it is assigned to scenes (verified on the unit with a
  delay switched off in one scene).
"""

from dataclasses import dataclass
from functools import lru_cache
import xml.etree.ElementTree as ET

from qc_mcp import catalog as _catalog

CAB_LAYOUTS = {"Cabsim Guitar (M)": 12000, "Cabsim Bass (M)": 12000,
               "Cabsim Guitar (ST)": 32000, "Cabsim Bass (ST)": 32000}
MIC_SELECTOR = "ir selector"
CAB_IR_SLOTS = ((1, 19), (9, 20))   # (selector, derived display name) for each stock-cab mic

# Blocks and lane controls the app itself relies on.
GAIN_BLOCK = 16005      # a clean level trim, used as the app's own output control
MIXER = 11000           # the mixer that joins two paths: LEVEL A 0, PAN A 1, LEVEL B 2, PAN B 3, PHASE 4
IR_LOADER, IR_LOADER_STEREO = 29001, 29002
CAPTURE_MODELS = (14000, 14001)
LANE_VOLUME, LANE_MUTE = 0, 2   # parameters of a row's output control


@dataclass(frozen=True)
class Parameter:
    index: int
    name: str
    kind: str          # "float", "fader", "switch", "rotarySwitch", "comboBox", "int", ...
    low: float | None
    high: float | None
    default: float | None
    units: str


@dataclass(frozen=True)
class Model:
    id: int
    name: str
    category: str
    params: tuple
    plugin: int        # the Neural DSP plugin whose licence it needs, 0 for none

    def index(self, name, nth=0):
        """The index of a parameter by name. Some models repeat a name, such as a cab's two microphones."""
        found = [param.index for param in self.params if param.name == name]
        if len(found) <= nth:
            raise KeyError(f"{self.name} has no parameter {name}")
        return found[nth]

    def named(self, name):
        return [param for param in self.params if param.name == name]

    @property
    def bypass_index(self):
        return len(self.params) + 1

    @property
    def is_stock_cab(self):
        return _layout(self.id) is not None


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _layout(model_id):
    info = _catalog.lookup(model_id)
    if info and [param["name"] for param in info["params"]] == [MIC_SELECTOR, MIC_SELECTOR]:
        return CAB_LAYOUTS.get(info["category"])
    return None


@lru_cache(maxsize=1)
def _plugins():
    try:
        root = ET.parse(_catalog._XML).getroot()
    except (OSError, ET.ParseError):
        return {}
    return {int(model.get("id")): int(model.get("plugin_id")) for model in root.iter("Model")
            if (model.get("plugin_id") or "0").isdigit() and int(model.get("plugin_id") or 0)}


@lru_cache(maxsize=None)
def model(model_id):
    """The model with this id, or None for an empty cell or an id the catalog does not know."""
    info = _catalog.lookup(model_id) if model_id else None
    if not info:
        return None
    layout = _layout(model_id)
    params = _catalog.lookup(layout)["params"] if layout else info["params"]
    return Model(int(model_id), info["name"], info["category"],
                 tuple(Parameter(index, param["name"], param["type"], _number(param.get("min")),
                                 _number(param.get("max")), _number(param.get("default")), param.get("units") or "")
                       for index, param in enumerate(params)),
                 _plugins().get(int(model_id), 0))


def models():
    """Every model in the catalog."""
    return [found for found in (model(model_id) for model_id in _catalog._catalog()) if found]


def to_norm(model_id, index, display):
    """The value the unit stores for a display value."""
    return float(_catalog.to_norm(_layout(model_id) or model_id, index, display))


def to_display(model_id, index, value):
    """The display value for what the unit stores."""
    return float(_catalog.to_display(_layout(model_id) or model_id, index, value))


def defaults(model_id):
    """The stored value of every parameter of a model according to the catalog.

    The unit does not always agree: it sets a stock cab's microphone levels
    half way, where the catalog says 0, which silences the cab. So the app
    never writes these; a block it adds keeps the unit's own values for
    whatever the design leaves unset.
    """
    found = model(model_id)
    if not found:
        return {}
    values = {}
    for param in found.params:
        if param.default is None:
            continue
        try:
            values[param.index] = to_norm(model_id, param.index, param.default)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    return values
