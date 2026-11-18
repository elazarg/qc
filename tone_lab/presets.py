"""Local, content-addressed snapshots of Quad Cortex working presets."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from tempfile import TemporaryDirectory
from pathlib import Path

from . import session


def archive(preset, position, firmware, *, reason):
    blob = preset.SerializeToString()
    digest = sha256(blob).hexdigest()
    root = session.SESSIONS / "presets"
    root.mkdir(parents=True, exist_ok=True)
    target = root / digest
    if target.is_dir():
        return target
    with TemporaryDirectory(prefix=".snapshot-", dir=root) as temporary:
        stage = Path(temporary)
        (stage / "preset.pb").write_bytes(blob)
        session.write_json(stage / "preset.json", {
            "name": preset.name, "position": position, "firmware": firmware, "sha256": digest,
            "captured_utc": datetime.now(timezone.utc).isoformat(), "reason": reason,
        })
        try:
            stage.rename(target)
        except FileExistsError:
            if not target.is_dir():
                raise
    return target


def history():
    rows = [json.loads(path.read_text(encoding="utf-8"))
            for path in (session.SESSIONS / "presets").glob("*/preset.json")]
    return sorted(rows, key=lambda row: row["captured_utc"], reverse=True)
