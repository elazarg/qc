"""A Nano Cortex sheet for a chain: what to capture, which cab to load, and what to set by hand.

The Nano Cortex runs one fixed path per preset, as Neural DSP's manual
describes it (https://neuraldsp.com/manual/nano-cortex, "Signal Path"):

    Input Gate > Pre 1 > Pre 2 > Neural Capture > IR Loader > Post 1 > Post 2 > Post 3

It holds one Neural Capture and no amp models, and it cannot load a Quad
Cortex preset. Captures move between the two units ("Neural Captures created
on Nano Cortex can be loaded on Quad Cortex, and vice versa", Nano Cortex FAQ,
https://support.neuraldsp.com/help/nano-cortex-r-faq). So a chain travels as:

- a capture of its amp-and-drive part: the first amp and the drives before it
  that are on in every scene, since a capture cannot switch a drive off;
- a cab for the IR Loader: one of the Nano's ten stock cabinets when it is the
  same cab or the nearest one, or the same impulse response uploaded as a user IR;
- the other effects in the fixed slots, each slot taking only the devices
  Neural DSP lists for it (https://neuraldsp.com/nano-cortex-device-list):
  drives, wahs, filters, mono compressors, utilities and EQ before the capture
  (pitch only in Pre 1); modulation and the doubler in Post 1, delays in Post 2,
  reverbs in Post 3, stereo compressors and EQ in any post slot.

The slot rules and device names are in `research/nano-cortex.json`. The Nano's
devices carry the names of Quad Cortex devices, so a value is given as the
Quad Cortex shows it, only for controls the same-named Quad Cortex device has.
The manual does not list each device's parameters; they are set in the Cortex
Cloud app.
"""

from dataclasses import asdict, dataclass, field
from functools import lru_cache
import json
from pathlib import Path
import re

from lab.sound import catalog
from lab import knowledge
from lab.sound.chain import Chain
from lab.sound.sounds import settings as stated

RESEARCH = Path(__file__).resolve().parents[2] / "research" / "nano-cortex.json"
PRE, POST = ("Pre 1", "Pre 2"), ("Post 1", "Post 2", "Post 3")
FIXED_POST = {"modulation": "Post 1", "doubler": "Post 1", "delay": "Post 2", "reverb": "Post 3"}
PRE_ORDER = ("drive", "bass_drive", "wah", "pitch", "compressor_mono", "filter", "utility", "eq")
ALIASES = {"mx vibe": "mx vibes"}


@lru_cache(maxsize=1)
def research():
    return json.loads(RESEARCH.read_text(encoding="utf-8"))


def _base(name):
    """A device name without its mono/stereo mark, for matching Quad Cortex and Nano names."""
    text = re.sub(r"\s*\((m|st|s/c)\)\s*$", "", (name or "").strip(), flags=re.I).casefold()
    text = text.replace("®", "").replace("™", "").strip()
    return ALIASES.get(text, text)


@lru_cache(maxsize=1)
def _devices():
    """{base name: [(kind, Nano device name, based on), ...]}."""
    found = {}
    for kind, devices in research()["devices"].items():
        for name, based_on in devices.items():
            found.setdefault(_base(name), []).append((kind, name, based_on))
    return found


def _kind(block):
    """What a Quad Cortex block is, in the Nano's terms."""
    info = block.info
    category = info.category if info else ""
    if category.endswith("Amplifier") or category == "Neural Capture":
        return "amp"
    if category.startswith("Cabsim"):
        return "cab"
    if category == "IRLoaders":
        return "ir"
    return {"Guitar Overdrive": "drive", "Bass Overdrive": "bass_drive", "Compressor": "compressor", "Wah": "wah",
            "Filter": "filter", "Pitch": "pitch", "Equalizer": "eq", "Modulation": "modulation", "Delay": "delay",
            "Reverb": "reverb"}.get(category) or ("doubler" if block.name == "Doubler" else
                                                   "gate" if "Gate" in block.name else
                                                   "utility" if category == "Utility" else "other")


NEAREST = {
    "delay": (("tape", "Tape Delay (ST)"), ("analog", "Analog Delay (ST)"), ("reverse", "Dual Reverse Delay"),
              ("dual", "Dual Delay"), ("", "Digital Delay (ST)")),
    "reverb": (("shimmer", "Shimmer"), ("hall", "Hall"), ("plate", "Hall"), ("spring", "Room"), ("room", "Room"),
               ("ambien", "Ambience"), ("cave", "Cave"), ("", "Room")),
    "modulation": (("flang", "MX Flanger"), ("phase", "MX Phase 95"), ("vib", "MX Vibes"), ("trem", "Tremolo"),
                   ("dimension", "Chief DC2W (ST)"), ("chorus", "Chief CE2W (ST)")),
    "compressor": (("", "Opto Comp"),),
    "wah": (("bass", "Bass Wah"), ("", "Crying Wah")),
    "filter": (("", "Envelope Filter"),),
    "pitch": (("oct", "Poly Octaver"), ("wham", "Wham"), ("", "Transpose")),
    "eq": (("graphic", "Graphic-9"), ("cut", "Low-High Cut"), ("", "Parametric-3")),
}


def device_for(block, place):
    """The Nano device for a block in the pre or post section: (device, based on, "same" | "nearest") or None."""
    kind = _kind(block)
    wanted = {"pre": ("utility", "compressor_mono", "wah", "filter", "drive", "bass_drive", "eq", "pitch"),
              "post": ("modulation", "doubler", "compressor_stereo", "eq", "delay", "reverb")}[place]
    for found_kind, name, based_on in _devices().get(_base(block.name), ()):
        if found_kind in wanted:
            return name, based_on, "same"
    lowered = block.name.casefold()
    for word, name in NEAREST.get(kind, ()):
        if word in lowered:
            if kind == "compressor":
                name += " (M)" if place == "pre" else " (ST)"
            for found_kind, device, based_on in _devices().get(_base(name), ()):
                if found_kind in wanted:
                    return device, based_on, "nearest"
    return None


def _same_model(device):
    """The Quad Cortex model with the Nano device's name, whose controls the device is taken to share."""
    for model in catalog.models():
        if model.name == device:
            return model
    for model in catalog.models():
        if _base(model.name) == _base(device):
            return model
    return None


def _values(block, device):
    """The block's stated values that the same-named device has, in Quad Cortex display units."""
    shown = stated(block)
    model = _same_model(device)
    if model is None:
        return {}
    names = {param.name: param for param in model.params}
    kept = {}
    for name, value in shown.items():
        param = names.get(name)
        if param is None or not isinstance(value, (int, float)):
            continue
        if param.low is not None and param.high is not None and not param.low <= value <= param.high:
            continue
        kept[name] = f"{value:g}{(' ' + param.units) if param.units and param.units != '%' else param.units}"
    return kept


def _scenes_off(block):
    if block.bypass is True:
        return "all"
    if isinstance(block.bypass, tuple):
        return "some"
    return ""


def _path(chain):
    """The chain's blocks in the order the signal meets them, and a note if parts had to be set aside."""
    notes, blocks = [], []
    for r, row in enumerate(chain.rows):
        blocks += [block for _, block in row.blocks]
        if row.split:
            notes.append(f"Row {r + 1} splits into two paths; the Nano has one, so both are read as one line.")
    return blocks, notes


def _nano_cab(cab_block):
    """The Nano stock cab for a Quad Cortex stock cab: (name, "same" | "nearest", why) or None."""
    cabs = research()["cabs"]
    squash = lambda text: re.sub(r"[^a-z0-9]+", " ", re.sub(r"\((m|st)\)", "", text.casefold())).strip()
    target = squash(cab_block.name)
    for name in cabs:
        if squash(name) == target:
            return name, "same", "the same stock cab"
    entry = knowledge.gear(cab_block.model) or {}
    config = (entry.get("config") or cab_block.name.split()[0]).lower().replace("×", "x")
    if re.fullmatch(r"\d{3}", config):
        config = f"{config[0]}x{config[1:]}" if config[1:] != "10" or config[0] != "8" else "8x10"
    words = set(re.findall(r"[a-z0-9]+", " ".join([entry.get("real", ""), entry.get("speaker", ""),
                                                   cab_block.name]).lower()))
    best = None
    bass = cab_block.info.category.startswith("Cabsim Bass") if cab_block.info else False
    for name, about in cabs.items():
        if bool(about.get("bass")) != bass:
            continue
        theirs = set(re.findall(r"[a-z0-9]+", " ".join([about["based_on"], about.get("speaker") or "", name]).lower()))
        score = 3 * (about["config"] == config) + len((words & theirs) - {"with", "drivers", "celestion"})
        if best is None or score > best[0]:
            best = (score, name)
    if best is None or best[0] == 0:
        return None
    return best[1], "nearest", f"nearest of the Nano's ten cabs to {entry.get('real') or cab_block.name}"


@dataclass
class Sheet:
    capture: dict                     # {"blocks": [...], "settings": {name: {...}}, "note": str}
    ir: dict                          # {"cab": str | None, "match": ..., "note": str}
    slots: list = field(default_factory=list)  # [{"slot", "device", "based_on", "match", "from", "values", "off", "moved"}]
    input_gate: str = ""
    left_out: list = field(default_factory=list)

    def to_record(self):
        return asdict(self)

    def lines(self):
        """The sheet as short lines of text to read at a rehearsal."""
        out = []
        if self.capture["blocks"]:
            out.append("Capture: " + " + ".join(self.capture["blocks"]) + (f" ({self.capture['note']})"
                                                                         if self.capture.get("note") else ""))
            for name, values in self.capture["settings"].items():
                if values:
                    out.append(f"  {name}: " + ", ".join(f"{key} {value}" for key, value in values.items()))
        else:
            out.append("Capture: none. " + self.capture.get("note", ""))
        out.append("IR Loader: " + (self.ir["cab"] or "off") + (f" ({self.ir['note']})" if self.ir.get("note") else ""))
        if self.input_gate:
            out.append("Input Gate: " + self.input_gate)
        for slot in self.slots:
            text = f"{slot['slot']}: {slot['device']}"
            if slot["match"] == "nearest":
                text += f" (nearest to {slot['from']})"
            if slot.get("moved"):
                text += " (before the amp in the chain; the Nano has it only after the capture)"
            if slot["values"]:
                text += " - " + ", ".join(f"{key} {value}" for key, value in slot["values"].items())
            if slot["off"]:
                text += " [off]" if slot["off"] == "all" else " [switch by hand: off in some scenes]"
            out.append(text)
        for note in self.left_out:
            out.append("Not on the Nano: " + note)
        return out


def sheet(chain: Chain) -> Sheet:
    """What it takes to play `chain` on a Nano Cortex."""
    blocks, notes = _path(chain)
    left_out = list(notes)
    amp_at = next((i for i, block in enumerate(blocks) if _kind(block) == "amp"), None)
    before = blocks[:amp_at] if amp_at is not None else [b for b in blocks if _kind(b) not in ("cab", "ir")]
    after = blocks[amp_at + 1:] if amp_at is not None else [b for b in blocks if _kind(b) in ("cab", "ir")]

    # The capture: the amp, and the drives before it that are on in every scene.
    captured, capture_note = [], ""
    if amp_at is not None:
        amp = blocks[amp_at]
        drives = [block for block in before if _kind(block) in ("drive", "bass_drive") and block.bypass is False]
        captured = drives + [amp]
        before = [block for block in before if block not in drives]
        if _scenes_off(amp):
            capture_note = "the amp is off in some scenes; a capture is always on"
        if drives:
            capture_note = capture_note or "drives that stay on are captured with the amp, cab off"
        for extra in after:
            if _kind(extra) == "amp":
                left_out.append(f"{extra.name}: a second amp; the Nano holds one capture")
        after = [block for block in after if _kind(block) != "amp"]
        capture = {"blocks": [block.name for block in captured],
                   "settings": {block.name: dict(stated(block)) for block in captured},
                   "note": capture_note or "capture the amp alone, cab off"}
    else:
        capture = {"blocks": [], "settings": {},
                   "note": "the chain has no amp; leave the capture slot empty or capture a drive pedal"}

    # The cab.
    ir = {"cab": None, "match": None, "note": "the chain has no cab"}
    cabs = [block for block in after + before if _kind(block) in ("cab", "ir")]
    if cabs:
        first = cabs[0]
        if _kind(first) == "ir":
            path = next((value for _, value in first.params if isinstance(value, str) and value), "")
            ir = {"cab": f"user IR {Path(path).name}" if path else "user IR", "match": "same",
                  "note": "upload the same impulse response file to the Nano (it stores 256 user IRs)"}
        else:
            found = _nano_cab(first)
            if found:
                ir = {"cab": found[0], "match": found[1], "note": found[2]}
            else:
                ir = {"cab": None, "match": None,
                      "note": f"no Nano stock cab is near {first.name}; upload an IR of that cab"}
        for extra in cabs[1:]:
            left_out.append(f"{extra.name}: a second cab; the IR Loader holds one")
    before = [block for block in before if _kind(block) not in ("cab", "ir")]
    after = [block for block in after if _kind(block) not in ("cab", "ir")]

    slots, gate = [], ""

    # Before the capture.
    candidates, moved = [], []
    for block in before:
        kind = _kind(block)
        if kind == "gate" and not gate and block.name not in ("Adaptive Gate", "Utility Gate"):
            gate = f"on, in place of {block.name} (set its noise reduction by ear)"
            continue
        found = device_for(block, "pre")
        if found is None and kind in ("modulation", "delay", "reverb") and device_for(block, "post"):
            moved.append(block)             # the Nano has these only after the capture
            continue
        if found is None:
            left_out.append(f"{block.name}: no Nano device of its kind before the capture")
            continue
        device, based_on, match = found
        device_kind = next(k for k, name, _ in _devices()[_base(device)] if name == device)
        candidates.append((block, device, based_on, match, device_kind))
    pitch = [item for item in candidates if item[4] == "pitch"]
    for extra in pitch[1:]:
        left_out.append(f"{extra[0].name}: pitch can go only in Pre 1")
    rest = sorted((item for item in candidates if item[4] != "pitch"),
                  key=lambda item: (PRE_ORDER.index(item[4]) if item[4] in PRE_ORDER else 99, before.index(item[0])))
    chosen = pitch[:1] + rest
    for item in chosen[len(PRE):]:
        left_out.append(f"{item[0].name}: both pre slots are taken")
    # The slots keep the order the signal meets the blocks in, except that pitch must be first.
    kept = sorted(chosen[:len(PRE)], key=lambda item: (item[4] != "pitch", before.index(item[0])))
    for slot, (block, device, based_on, match, _) in zip(PRE, kept):
        slots.append({"slot": slot, "device": device, "based_on": based_on, "match": match, "from": block.name,
                      "values": _values(block, device), "off": _scenes_off(block), "moved": False})

    # After the capture.
    taken = {}
    flexible = []
    for block in moved + after:
        found = device_for(block, "post")
        if found is None:
            left_out.append(f"{block.name}: no Nano device of its kind after the capture")
            continue
        device, based_on, match = found
        device_kind = next(k for k, name, _ in _devices()[_base(device)] if name == device)
        entry = {"device": device, "based_on": based_on, "match": match, "from": block.name,
                 "values": _values(block, device), "off": _scenes_off(block),
                 "moved": block in moved}
        slot = FIXED_POST.get(device_kind)
        if slot is None:
            flexible.append(entry)
        elif slot in taken:
            left_out.append(f"{block.name}: {slot} already holds {taken[slot]['device']}")
        else:
            taken[slot] = entry
    for entry in flexible:
        free = next((slot for slot in POST if slot not in taken), None)
        if free is None:
            left_out.append(f"{entry['from']}: every post slot is taken")
            continue
        taken[free] = entry
    slots += [{"slot": slot, **taken[slot]} for slot in POST if slot in taken]
    return Sheet(capture, ir, slots, gate, left_out)
