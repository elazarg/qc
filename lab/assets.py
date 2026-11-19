"""The player's impulse responses and Neural Captures on the unit, and putting one into a sound.

`listing(engine)` reads them where the unit is held (a host command:
`host.call("lab.assets.listing")`) and remembers them in `assets.json` in the
app's home for `cached` to read without the unit.

What the unit's directory says about them (CorOS 4.1, the same `File` read
that lists the presets):

- Every IR on the unit is a file of the folder `local_ir_root`, with its
  name and a key `CIR_` and hex. Folders such as "My IRs" hold links to
  those files, by key, with blank names. The factory IRs are a folder of
  their own, flagged `is_factory`, whose rows have no key.
- Every capture on the unit is a file of `local_nc_root`, with its name and
  a key of 64 hex characters. A capture also in a factory folder is Neural
  DSP's; one in "Factory Captures V1" or "V2" runs on that version of the
  capture block. The version of the player's own captures is not in the
  directory.
- Cloud downloads that are not in `local_nc_root` are not on the unit.

How a block binds one (the device library's `set_ir` and `set_capture`):

- An IR loader (29001, or 29002 in stereo) holds the IR's key as its
  parameter 2 (IR PATH) and its name as parameter 22 (IR NAME). A readback
  of one holds the same strings at the same places.
- A capture block (14000 for V1, 14001 for V2) holds the capture's key
  followed by its name as parameter 5 (file_name). The key is what binds:
  the name after it is the unit's to fill in, so only the key is compared.
"""

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from . import catalog, knowledge
from .chain import Block

FILE = "assets.json"
IR_KEY = re.compile(r"CIR_[0-9a-f]+", re.IGNORECASE)
CAPTURE_KEY = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
IR_ROOT, CAPTURE_ROOT = "local_ir_root", "local_nc_root"
IR_STEREO = 29002
IR_MODELS = (catalog.IR_LOADER, IR_STEREO)
IR_PATH, IR_NAME = 2, 22
CAPTURE_FILE = 5
CAPTURE_VERSIONS = {1: catalog.CAPTURE_MODELS[0], 2: catalog.CAPTURE_MODELS[1]}
CAPTURE_MODELS_SET = frozenset(catalog.CAPTURE_MODELS)
STEREO_CABS = ("Cabsim Guitar (ST)", "Cabsim Bass (ST)")


@dataclass(frozen=True)
class Asset:
    kind: str                       # "ir" or "capture"
    key: str
    name: str
    source: str = "user"            # "user" or "factory"
    capture_version: int | None = None

    def to_record(self):
        return asdict(self)

    @classmethod
    def from_record(cls, record):
        return cls(record["kind"], record["key"], record.get("name") or "", record.get("source") or "user",
                   record.get("capture_version"))


# ---- Reading the directory ----

def _groups(folders):
    """For each key, the names of the folders that hold it."""
    found = {}
    for folder in folders:
        for row in folder.get("files", []):
            if row.get("key"):
                found.setdefault(row["key"], set()).add(folder.get("name") or "")
    return found


def _local(folders, kind, root, pattern):
    groups = _groups(folders)
    factory = {row.get("key") for folder in folders if folder.get("is_factory") for row in folder.get("files", [])}
    local = next((folder for folder in folders if folder.get("key") == root), {})
    found = {}
    for row in local.get("files", []):
        name, key = (row.get("name") or "").strip(), row.get("key") or ""
        if not name or not pattern.fullmatch(key):
            continue
        version = None
        if kind == "capture":
            versions = [number for number in (1, 2) if f"Factory Captures V{number}" in groups.get(key, ())]
            version = versions[0] if len(versions) == 1 else None
        found[key] = Asset(kind, key, name, "factory" if key in factory else "user", version)
    return list(found.values())


def from_listing(listing):
    """The IRs and captures on the unit, from a directory listing: the player's first, then by name."""
    listing = listing or {}
    rows = (_local(listing.get("irs", []), "ir", IR_ROOT, IR_KEY)
            + _local(listing.get("captures", []), "capture", CAPTURE_ROOT, CAPTURE_KEY))
    return sorted(rows, key=lambda asset: (asset.kind, asset.source != "user", asset.name.casefold(), asset.key))


def _path(folder):
    return Path(folder) / FILE if folder is not None else knowledge.home() / FILE


def listing(engine, *, stop=None, folder=None):
    """Read the unit's IRs and captures where the unit is held, remember them, and return them as records.

    A listing with neither the IR nor the capture library is a read that
    failed: it raises and what was read before is kept.
    """
    if stop is not None and stop.is_set():
        raise RuntimeError("Stopped.")
    device = engine.unit.device
    found = device.list_directory()
    roots = {folder_.get("key") for part in ("irs", "captures") for folder_ in (found or {}).get(part, [])}
    if not found or not roots & {IR_ROOT, CAPTURE_ROOT}:
        raise RuntimeError("The Quad Cortex did not list its IRs and captures. Those read before were kept.")
    records = [asset.to_record() for asset in from_listing(found)]
    path = _path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    spare = path.with_suffix(".tmp")
    spare.write_text(json.dumps({"read": datetime.now(timezone.utc).isoformat(),
                                 "firmware": getattr(device, "firmware", None), "assets": records}, indent=1),
                     encoding="utf-8")
    os.replace(spare, path)
    return records


def cached(folder=None):
    """The assets from the last read, or None when none has been read. `folder` defaults to the app's home."""
    try:
        record = json.loads(_path(folder).read_text(encoding="utf-8"))
        return [Asset.from_record(item) for item in record["assets"]]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


# ---- Blocks ----

def _check(asset, version=None):
    if asset.kind == "ir":
        if not IR_KEY.fullmatch(asset.key or ""):
            raise ValueError(f"{asset.name or 'This IR'} has no key on the unit, so it cannot be loaded.")
        return None
    if asset.kind == "capture":
        if not CAPTURE_KEY.fullmatch(asset.key or ""):
            raise ValueError(f"{asset.name or 'This capture'} has no key on the unit, so it cannot be loaded.")
        version = version if version is not None else asset.capture_version
        if version not in CAPTURE_VERSIONS:
            raise ValueError(f"Say whether {asset.name or 'the capture'} is a V1 or a V2 capture: "
                             "the unit does not say for the player's own captures.")
        return version
    raise ValueError("Choose an IR or a capture.")


def block_for(asset, *, stereo=False, version=None):
    """The block that loads an asset: an IR loader holding the IR, or the capture block of its version."""
    found = _check(asset, version)
    if asset.kind == "ir":
        return Block(IR_STEREO if stereo else catalog.IR_LOADER, ((IR_PATH, asset.key), (IR_NAME, asset.name)))
    return Block(CAPTURE_VERSIONS[found], ((CAPTURE_FILE, asset.key + asset.name),))


def bound(block, known=()):
    """The asset a block holds, or None. `known` are assets read from the unit, for their names and sources."""
    if block is None:
        return None
    by_key = {asset.key.casefold(): asset for asset in known}
    if block.model in IR_MODELS:
        key = block.get(IR_PATH)
        if isinstance(key, str) and IR_KEY.fullmatch(key):
            name = block.get(IR_NAME)
            return by_key.get(key.casefold()) or Asset("ir", key, name if isinstance(name, str) else "")
    if block.model in CAPTURE_MODELS_SET:
        text = block.get(CAPTURE_FILE)
        if isinstance(text, str) and CAPTURE_KEY.fullmatch(text[:64]):
            version = 1 if block.model == CAPTURE_VERSIONS[1] else 2
            found = by_key.get(text[:64].casefold())
            return replace(found, capture_version=version) if found else Asset("capture", text[:64], text[64:],
                                                                               "user", version)
    return None


# ---- Putting one into a sound ----

def is_cab(block):
    info = block.info if block is not None else None
    return bool(info) and (info.category.startswith("Cabsim") or block.model in IR_MODELS)


def is_amp(block):
    info = block.info if block is not None else None
    return bool(info) and (info.category.endswith("Amplifier") or block.model in CAPTURE_MODELS_SET)


def with_ir(chain, asset):
    """The chain with each cab (on both paths when each has one) replaced by an IR loader holding `asset`,
    in the same place. Returns (chain, what changed in words). Raises ValueError with the reason when it cannot."""
    if asset.kind != "ir":
        raise ValueError("Choose an IR to put in place of the cab.")
    _check(asset)
    cabs = [(r, column, block) for r, column, block in chain.blocks if is_cab(block)]
    if not cabs:
        raise ValueError("This sound has no cab to replace. A capture that includes its cab needs no IR.")
    replaced = []
    for r, column, block in cabs:
        stereo = block.model == IR_STEREO or block.info.category in STEREO_CABS
        chain = chain.with_block(r, column, replace(block_for(asset, stereo=stereo), bypass=block.bypass))
        replaced.append(block.name if block.model not in IR_MODELS else (bound(block) or asset).name or block.name)
    if len(replaced) == 1:
        said = f"Replaced the cab ({replaced[0]}) with the IR {asset.name}."
    else:
        said = f"Replaced both cabs ({' and '.join(replaced)}) with the IR {asset.name}."
    return chain, said


def with_capture(chain, asset, *, has_cab, version=None):
    """The chain with its amp replaced by a capture block holding `asset`, in the same place, and its cab
    removed too when the capture includes one. Returns (chain, what changed in words). Raises ValueError with
    the reason when it cannot."""
    if asset.kind != "capture":
        raise ValueError("Choose a capture to put in place of the amp.")
    if type(has_cab) is not bool:
        raise ValueError("Say whether the capture includes its cab.")
    found = _check(asset, version)
    amps = [(r, column, block) for r, column, block in chain.blocks if is_amp(block)]
    if not amps:
        raise ValueError("This sound has no amp to replace.")
    if len(amps) > 1:
        raise ValueError("This sound blends two amps. A capture takes the place of one; start from a sound with one amp.")
    r, column, amp = amps[0]
    chain = chain.with_block(r, column, replace(block_for(asset, version=found), bypass=amp.bypass))
    cabs = [(row, col, block) for row, col, block in chain.blocks if is_cab(block)]
    if has_cab:
        for row, col, _ in cabs:
            chain = chain.with_block(row, col, None)
        said = f"Replaced the amp ({amp.name}) " + (f"and the cab with the capture {asset.name}." if cabs else
                                                    f"with the capture {asset.name}, which includes its cab.")
    else:
        if not cabs:
            raise ValueError(f"{asset.name} needs a cab after it, and this sound has none. "
                             "Use a capture that includes its cab, or start from a sound with a cab.")
        said = f"Replaced the amp ({amp.name}) with the capture {asset.name}."
    return chain, said
