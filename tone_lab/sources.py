"""Local inventory from a POD Go backup or a Spotify account export."""

from collections import Counter
import json
from pathlib import Path
import struct
import zlib


def pod_backup(path):
    raw = Path(path).read_bytes()
    if len(raw) < 48 or raw[:8] != b"AF6L\x01\0\0\0":
        raise ValueError("Choose a POD Go .pgb backup.")
    index = struct.unpack_from("<I", raw, 8)[0]
    if not 48 <= index < len(raw) or (len(raw) - index) % 36:
        raise ValueError("Invalid POD Go backup index.")
    setlists, irs = {}, []
    for reversed_tag, start, size, compressed, expanded in struct.iter_unpack("<4sQQIQ4x", raw[index:]):
        tag = reversed_tag[::-1].decode("ascii")
        if start + size > index or expanded > 20_000_000 or compressed not in (0, 1):
            raise ValueError(f"Invalid POD Go backup entry {tag}.")
        if tag.startswith("I"):
            irs.append(tag)
        if tag not in ("SL00", "SL01"):
            continue
        payload = raw[start:start + size]
        if compressed:
            inflater = zlib.decompressobj()
            payload = inflater.decompress(payload, expanded + 1)
            if not inflater.eof:
                raise ValueError(f"Incomplete compressed POD Go folder {tag}.")
        if len(payload) != expanded:
            raise ValueError(f"Wrong POD Go folder size for {tag}.")
        folder = json.loads(payload)
        presets = []
        for i, item in enumerate(folder["data"]["presets"]):
            if item:
                presets.append({"slot": f"{i // 4 + 1:02}{'ABCD'[i % 4]}",
                                "name": item["meta"]["name"], "tone": item["tone"]})
        setlists[folder["meta"]["name"]] = presets
    if not setlists:
        raise ValueError("No POD Go setlists found in that archive.")
    return {"setlists": setlists, "ir_entries": irs}


def listening_history(paths, limit=30):
    listening = Counter()
    for path in paths:
        entries = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(entries, list):
            raise ValueError(f"Choose a Spotify audio history JSON file: {Path(path).name}.")
        for entry in entries:
            artist = entry.get("master_metadata_album_artist_name") or entry.get("artistName")
            track = entry.get("master_metadata_track_name") or entry.get("trackName")
            milliseconds = entry.get("ms_played") or entry.get("msPlayed") or 0
            if artist and track and isinstance(milliseconds, int) and milliseconds >= 30_000:
                listening[artist, track] += milliseconds
    return [{"artist": artist, "track": track, "listened_minutes": round(ms / 60_000, 1)}
            for (artist, track), ms in listening.most_common(limit)]
