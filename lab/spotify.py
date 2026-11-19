"""Songs to build collections for, from a Spotify account export.

Spotify's "Download your data" gives listening history as JSON files
(https://support.spotify.com/us/article/understanding-your-data/):

- the account data export: `StreamingHistory_music_0.json`, ... with
  `artistName`, `trackName`, `msPlayed`, `endTime`;
- the extended streaming history: `Streaming_History_Audio_2019-2021_0.json`,
  ... with `master_metadata_album_artist_name`, `master_metadata_track_name`,
  `ms_played`, `ts`. Podcast episodes and audiobooks have no track name.

Both come zipped. `history` reads files, folders or the zip itself. A play
under 30 seconds is not counted (Spotify's own threshold for a stream).
Versions of one song ("- Remastered 2011", "(Live)") are counted together.
"""

from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import zipfile

MINIMUM_MS = 30_000
VERSION = re.compile(r"\s*(\s-\s.*(remaster|live|version|edit|mix|mono|stereo|demo|acoustic|deluxe).*"
                     r"|[(\[][^)\]]*(remaster|live|version|edit|mix|mono|stereo|demo|deluxe)[^)\]]*[)\]])\s*$", re.I)
DASH = "–"


@dataclass(frozen=True)
class Track:
    artist: str
    title: str
    ms: int
    plays: int

    @property
    def minutes(self):
        return round(self.ms / 60_000, 1)

    @property
    def text(self):
        hours = self.ms / 3_600_000
        spent = f"{hours:.1f} h" if hours >= 1 else f"{self.minutes:g} min"
        return f"{self.artist} {DASH} {self.title} ({spent}, {self.plays} plays)"

    def to_record(self):
        return {"artist": self.artist, "title": self.title, "minutes": self.minutes, "plays": self.plays,
                "text": self.text}


def _entries(text, name):
    entries = json.loads(text)
    if not isinstance(entries, list):
        raise ValueError(f"{name} is not a Spotify listening history file.")
    return entries


def _wanted(name):
    base = Path(name).name
    return base.lower().endswith(".json") and base.startswith(("StreamingHistory", "Streaming_History_Audio",
                                                               "Streaming_History"))\
        and "Video" not in base and "podcast" not in base.lower()


def history(paths):
    """Every play in the given files, folders or zip archives, as (artist, title, ms) tuples."""
    plays = []
    for path in map(Path, paths):
        if path.is_dir():
            files = sorted(p for p in path.rglob("*.json") if _wanted(p.name))
            sources = [(p.name, p.read_text(encoding="utf-8-sig")) for p in files]
        elif zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                sources = [(name, archive.read(name).decode("utf-8-sig")) for name in sorted(archive.namelist())
                           if _wanted(name)]
        else:
            sources = [(path.name, path.read_text(encoding="utf-8-sig"))]
        for name, text in sources:
            for entry in _entries(text, name):
                if not isinstance(entry, dict):
                    continue
                artist = entry.get("master_metadata_album_artist_name") or entry.get("artistName")
                title = entry.get("master_metadata_track_name") or entry.get("trackName")
                ms = entry.get("ms_played", entry.get("msPlayed", 0))
                if artist and title and isinstance(ms, int) and not isinstance(ms, bool):
                    plays.append((str(artist).strip(), str(title).strip(), ms))
    return plays


def song_key(title):
    """A title without its version marks, folded, for counting versions together and matching songs."""
    base = VERSION.sub("", title or "")
    return re.sub(r"[^\w]+", " ", base.casefold()).strip()


def rank(plays, *, minimum_ms=MINIMUM_MS):
    """Tracks by time listened, most first. Each is named by the version listened to most."""
    totals, counts, names = defaultdict(int), defaultdict(int), defaultdict(lambda: defaultdict(int))
    for artist, title, ms in plays:
        if ms < minimum_ms:
            continue
        key = (artist.casefold(), song_key(title))
        totals[key] += ms
        counts[key] += 1
        names[key][(artist, VERSION.sub("", title).strip() or title)] += ms
    tracks = []
    for key, ms in totals.items():
        artist, title = max(names[key].items(), key=lambda item: item[1])[0]
        tracks.append(Track(artist, title, ms, counts[key]))
    return sorted(tracks, key=lambda track: (-track.ms, track.artist.casefold(), track.title.casefold()))


def _known(store):
    """Keys of the songs the store already has, by title and by "artist - title"."""
    known = set()
    for song in store.songs() if store is not None else ():
        name = str(song.get("name") or "")
        separator = next((s for s in (f" {DASH} ", " - ", " — ") if s in name), None)
        if song.get("artist"):
            known.add((str(song["artist"]).casefold(), song_key(name)))
        elif separator:
            artist, title = name.split(separator, 1)
            known.add((artist.strip().casefold(), song_key(title)))
            known.add(song_key(name))
        else:
            known.add(song_key(name))     # a song named by its title alone stands for any artist's
    return known


def proposals(paths, store=None, *, limit=20, minimum_ms=MINIMUM_MS):
    """The songs listened to most that the store has no song for, as Tracks, most listened first."""
    known = _known(store)
    found = []
    for track in rank(history(paths), minimum_ms=minimum_ms):
        if song_key(track.title) in known or (track.artist.casefold(), song_key(track.title)) in known:
            continue
        found.append(track)
        if len(found) >= limit:
            break
    return found
