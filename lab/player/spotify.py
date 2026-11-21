"""Songs to build collections for, from a Spotify account export.

Spotify's "Download your data" gives listening history as JSON files
(https://support.spotify.com/us/article/understanding-your-data/):

- the account data export: `StreamingHistory_music_0.json`, ... with
  `artistName`, `trackName`, `msPlayed`, `endTime`;
- the extended streaming history: `Streaming_History_Audio_2019-2021_0.json`,
  ... with `master_metadata_album_artist_name`, `master_metadata_track_name`,
  `ms_played`, `ts`. Podcast episodes and audiobooks have no track name.

Both come zipped. `entries` reads files, folders or the zip itself, one file
at a time, and `track_of` says what one entry is a play of. A play under 30
seconds is not counted (`MINIMUM_MS`, Spotify's own threshold for a stream),
and versions of one song ("- Remastered 2011", "(Live)") are counted together
(`song_key`). The ranking of an export's songs is `lab.player.listening`.
"""

import json
from pathlib import Path
import re
import zipfile

MINIMUM_MS = 30_000
# A version mark at the end of a title: " - 2012 Remaster", "(Live at ...)", "[Mono]". Whole words only, so that
# "Deliver Me" or "(Olive Branch)" are not taken for "live"; a remix is another recording and is not folded in.
_MARK = r"\b(remaster(ed)?|live|version|edit|mix|mono|stereo|demo|acoustic|deluxe)\b"
VERSION = re.compile(r"\s*(\s-\s.*" + _MARK + r".*|[(\[][^)\]]*" + _MARK + r"[^)\]]*[)\]])\s*$", re.I)


def _entries(text, name):
    entries = json.loads(text)
    if not isinstance(entries, list):
        raise ValueError(f"{name} is not a Spotify listening history file.")
    return entries


def _wanted(name):
    base = Path(name).name
    return base.lower().endswith(".json") and base.startswith(("StreamingHistory", "Streaming_History"))\
        and "Video" not in base and "podcast" not in base.lower()


def _sources(path):
    """(name, text) of each listening-history file in a file, folder or zip archive, one at a time: a
    multi-year export is over a hundred megabytes of text, and only one file of it is held at once."""
    if path.is_dir():
        for file in sorted(p for p in path.rglob("*.json") if _wanted(p.name)):
            yield file.name, file.read_text(encoding="utf-8-sig")
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if _wanted(name):
                    yield name, archive.read(name).decode("utf-8-sig")
    else:
        yield path.name, path.read_text(encoding="utf-8-sig")


def entries(paths):
    """Every entry of the listening-history files in the given files, folders or zip archives, as dicts.
    The one place an export is read; `history` here and `lab.player.listening.plays` both use it."""
    for path in map(Path, paths):
        for name, text in _sources(path):
            found = _entries(text, name)
            del text
            for entry in found:
                if isinstance(entry, dict):
                    yield entry


def track_of(entry):
    """(artist, title, milliseconds played) of an entry of either export, or None when it is not a music play
    (a podcast or audiobook has no track name) or its play time is not a number."""
    artist = entry.get("master_metadata_album_artist_name") or entry.get("artistName")
    title = entry.get("master_metadata_track_name") or entry.get("trackName")
    ms = entry.get("ms_played", entry.get("msPlayed", 0))
    if isinstance(ms, str):
        try:
            ms = float(ms)
        except ValueError:
            return None
    if not artist or not title or isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms != ms:
        return None
    return str(artist).strip(), str(title).strip(), int(ms)


def song_key(title):
    """A title without its version marks, folded, for counting versions together and matching songs."""
    base = VERSION.sub("", title or "")
    return re.sub(r"[^\w]+", " ", base.casefold()).strip()
