"""Inventory real Quad Cortex assets and choose safe, persistent preset destinations."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from . import device, preset_library, session


SETLIST_NAME = "Tone Lab"
MY_PRESETS_KEY = "/media/p4/Presets/My Presets"
CAPTURE_KEY = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
IR_KEY = re.compile(r"CIR_[0-9a-f]+", re.IGNORECASE)


class SetlistUnavailable(RuntimeError):
    """The requested Quad Cortex setlist was absent or could not be verified."""


@dataclass(frozen=True)
class Setlist:
    name: str
    key: str
    files: tuple[dict, ...]

    @property
    def filled(self):
        return sum(bool(row.get("name") or row.get("key")) for row in self.files)


@dataclass(frozen=True)
class Asset:
    kind: str
    key: str
    name: str
    source: str
    folders: tuple[str, ...] = ()
    capture_version: int | None = None

    @property
    def model_id(self):
        if self.kind == "ir":
            return 29001
        return {1: 14000, 2: 14001}.get(self.capture_version)


@dataclass(frozen=True)
class Library:
    firmware: str
    scanned_utc: str
    setlists: tuple[Setlist, ...]
    presets: tuple[preset_library.SavedPreset, ...]
    irs: tuple[Asset, ...]
    captures: tuple[Asset, ...]
    download_captures: tuple[Asset, ...]

    def find_assets(self, query, kind=None):
        query = query.strip().casefold()
        rows = [asset for asset in (*self.irs, *self.captures) if not kind or asset.kind == kind]
        if not query:
            return rows
        return sorted((asset for asset in rows if query in asset.name.casefold() or query == asset.key.casefold()),
                      key=lambda asset: (asset.key.casefold() != query, asset.name.casefold() != query,
                                         asset.source != "user", asset.name.casefold()))


def user_setlists(catalog):
    for row in catalog.get("presets", []):
        key = row.get("key", "")
        if row.get("is_factory") or row.get("is_downloads"):
            continue
        if key.startswith("/media/p4/Presets/") or re.fullmatch(r"\d+_q", key):
            yield Setlist(row["name"], key, tuple(row.get("files", [])))


def _memberships(folders):
    labels = {}
    for folder in folders:
        name = folder.get("name", "")
        if not name:
            continue
        for row in folder.get("files", []):
            key = row.get("key", "")
            if key:
                labels.setdefault(key, set()).add(name)
    return labels


def _local_assets(folders, *, kind, root, key_pattern):
    aliases = _memberships(folders)
    factory = {row.get("key") for folder in folders if folder.get("is_factory")
               for row in folder.get("files", [])}
    local = next((folder for folder in folders if folder.get("key") == root), {})
    assets = {}
    for row in local.get("files", []):
        name, key = row.get("name", ""), row.get("key", "")
        if not name or not key_pattern.fullmatch(key):
            continue
        groups = aliases.get(key, ())
        versions = [version for version in (1, 2) if f"Factory Captures V{version}" in groups]
        assets[key] = Asset(kind, key, name, "factory" if key in factory else "user",
                            tuple(sorted(groups)), versions[0] if len(versions) == 1 else None)
    return tuple(sorted(assets.values(), key=lambda item: (item.source != "user", item.name.casefold(), item.key)))


def from_catalog(catalog, *, firmware="", scanned_utc=""):
    ir_folders, capture_folders = catalog.get("irs", []), catalog.get("captures", [])
    irs = _local_assets(ir_folders, kind="ir", root="local_ir_root", key_pattern=IR_KEY)
    captures = _local_assets(capture_folders, kind="capture", root="local_nc_root", key_pattern=CAPTURE_KEY)
    downloads = {row["key"]: Asset("capture", row["key"], row["name"], "downloads")
                 for folder in capture_folders if folder.get("is_downloads")
                 for row in folder.get("files", [])
                 if row.get("name") and CAPTURE_KEY.fullmatch(row.get("key", ""))}
    return Library(firmware, scanned_utc, tuple(user_setlists(catalog)),
                   tuple(preset_library.saved_presets(catalog)), irs, captures,
                   tuple(sorted(downloads.values(), key=lambda item: item.name.casefold())))


def _cache_path():
    return session.SESSIONS / "qc-library.json"


def cached():
    try:
        snapshot = json.loads(_cache_path().read_text(encoding="utf-8"))
        return from_catalog(snapshot["catalog"], firmware=snapshot.get("firmware", ""),
                            scanned_utc=snapshot.get("scanned_utc", ""))
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        return None


def refresh():
    """Read a fresh directory without switching or editing the selected preset."""
    with device.connect(write=True) as qc:
        catalog = qc.list_directory()
        firmware = qc.firmware
    if not catalog or not any(item.key == MY_PRESETS_KEY for item in user_setlists(catalog)):
        raise RuntimeError("The Quad Cortex did not return its user setlists. The previous inventory was kept.")
    scanned_utc = datetime.now(timezone.utc).isoformat()
    snapshot = {"catalog": catalog, "firmware": firmware, "scanned_utc": scanned_utc}
    library = from_catalog(catalog, firmware=firmware, scanned_utc=scanned_utc)
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    session.write_json(path, snapshot)
    return library


def destination(qc, name, *, allow_my_presets=False, setlist=SETLIST_NAME):
    """Plan an unused slot using the current directory under the caller's device lock."""
    if not name.strip() or len(name) > 24 or any(c in name for c in '\\/:*?"<>|\r\n'):
        raise ValueError("Use a preset name up to 24 characters without file path punctuation.")
    rows = tuple(user_setlists(qc.list_directory()))
    matched = [item for item in rows if item.name.casefold() == setlist.casefold()]
    if not matched and allow_my_presets:
        matched = [item for item in rows if item.key == MY_PRESETS_KEY]
    if len(matched) != 1:
        raise SetlistUnavailable(f'Create a dedicated setlist named "{setlist}" on the Quad Cortex to save there.')
    target = matched[0]
    if tuple(row.get("index") for row in target.files) != tuple(range(256)):
        raise SetlistUnavailable(f'The Quad Cortex did not return the complete "{target.name}" slot table.')
    # Leave the older Lab's two verified fixtures available in My Presets.
    eligible = target.files[:-2] if target.key == MY_PRESETS_KEY else target.files
    used = [row["index"] for row in eligible if row.get("name") or row.get("key") or row.get("is_readonly")]
    free = [row["index"] for row in eligible if row["index"] not in used]
    if not free:
        raise SetlistUnavailable(f'"{target.name}" has no empty preset slots.')
    slot = next((index for index in free if index > max(used, default=-1)), free[0])
    occupied = {row["name"].casefold() for row in target.files if row.get("name")}
    candidate = name
    for count in range(2, 258):
        if candidate.casefold() not in occupied:
            return preset_library.SavedPreset(candidate, target.name, target.key, slot)
        suffix = f" {count}"
        candidate = name[:24 - len(suffix)].rstrip() + suffix
    raise SetlistUnavailable("No unique preset name was available.")


def preset_fingerprint(preset):
    return sha256(preset.SerializeToString(deterministic=True)).hexdigest()


def store_receipt(draft_id, saved, fingerprint=None, *, scene=None, values=None):
    label = f"{saved.name} · {saved.setlist} {saved.slot}"
    payload = {"label": label, "preset": {"name": saved.name, "setlist": saved.setlist,
                                         "position": saved.position, "fingerprint": fingerprint}}
    if scene is not None:
        if not isinstance(scene, str) or len(scene) != 1 or scene not in device.SCENES:
            raise ValueError("A saved Quad Cortex scene must be A through H.")
        payload["scene"] = scene
    if values:
        payload["knob_values"] = dict(values)
    folder = session.SESSIONS / "drafts"
    folder.mkdir(parents=True, exist_ok=True)
    session.write_json(folder / f"{draft_id}.kept.json", payload)
    return label


def lookup_receipt(draft_id):
    try:
        record = json.loads((session.SESSIONS / "drafts" / f"{draft_id}.kept.json").read_text(encoding="utf-8"))
        if "preset" in record:
            preset = record["preset"]
            address = preset["position"]
            if 0 <= address["position"] < 256 and address["folder_key"] and address["is_factory"] is False:
                return preset_library.SavedPreset(preset["name"], preset["setlist"],
                                                address["folder_key"], address["position"])
        # Previous builds recorded a human slot label. Rebind through the actual cached directory.
        library = cached()
        if library is not None:
            matches = [row for row in library.presets if f"{row.name} · {row.setlist} {row.slot}" == record.get("label")]
            if len(matches) == 1:
                return matches[0]
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        pass
    return None


def add_asset(qc, asset, row, column):
    """Bind a selected device asset to an empty working-grid slot."""
    if asset.kind == "ir" and IR_KEY.fullmatch(asset.key):
        return qc.set_ir(row, column, asset.key, asset.name)
    if asset.kind == "capture" and CAPTURE_KEY.fullmatch(asset.key) and asset.capture_version in (1, 2):
        return qc.set_capture(row, column, asset.key, asset.name, version=f"v{asset.capture_version}")
    raise ValueError("Choose an on-device IR or a capture with a known model version.")


def asset_matches(block, asset):
    """Confirm an asset binding from typed readback, whose parameter indices can be implicit."""
    if block.hash != asset.model_id:
        return False
    if asset.kind == "ir":
        return any(value.string_value == asset.key for param in block.params for value in param.param_values)
    return any(value.string_value[:64] == asset.key for param in block.params for value in param.param_values)


def preset_asset_keys(preset):
    """Return exact named asset bindings found in the existing preset's readback."""
    refs = set()
    for chain in preset.chains:
        for block in chain.models:
            for param in block.params:
                for value in param.param_values:
                    token = value.string_value
                    if IR_KEY.fullmatch(token):
                        refs.add(("ir", token, None))
                    elif block.hash in (14000, 14001) and CAPTURE_KEY.fullmatch(token[:64]):
                        refs.add(("capture", token[:64], 1 if block.hash == 14000 else 2))
    return tuple(sorted(refs))
