"""What a listening history says that can inform tone work. Generic: this module holds no one's data.

It reads a Spotify account export (the extended streaming history, or the
short one) and keeps, of each play, only the artist, title, album, how long
it played, when, and how it started and ended. Everything else in the export
(IP address, country, device, ...) is never read into memory past parsing.

From that it works out:

- how much each artist and song was listened to, over all and recently;
- which songs were chosen (the play was started by the listener's own click on
  the track, the play button or the back button, whatever the shuffle switch
  said) and on how many days they were come back to, which says more about
  liking than time alone. Only the extended export records how a play was
  started; for the short one "chosen" is not recorded (None), never 0;
- which songs were listened to in a live version as well as a studio one,
  since a recorded tone differs between them;
- which songs already have a preset or a sound of the player's, by name;
- with the assistant, for the most listened artists: whether the music is
  guitar-led, who plays guitar, the families of guitar tone used, and songs
  that would make good presets.

`report(...)` returns plain lines and a JSON-able record. Where they are kept
is the caller's business: Tone Lab writes them to the player's data folder
(`knowledge.home() / "personal"`), never into the project, because they are
personal.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re

from lab.player.spotify import MINIMUM_MS, entries, song_key, track_of

# Spotify's reason_start values for a play the listener started by hand: a click on the track, the play button,
# the back button (playing it again). The rest ("trackdone", "fwdbtn", "appload", "remote", ...) are the queue
# moving on or cannot be told. The export's own "shuffle" field is the state of the shuffle switch, not how this
# play was started, so it is not looked at.
DELIBERATE_STARTS = {"clickrow", "playbtn", "backbtn"}
COMPLETE_ENDS = {"trackdone"}
LIVE = re.compile(r"\blive\b|\(live|- live|unplugged|in concert", re.I)


@dataclass(frozen=True)
class Play:
    artist: str
    title: str
    album: str
    ms: int
    ts: str
    deliberate: bool | None   # started by the listener's own click; None when the export does not record it
    complete: bool        # played to the end
    skipped: bool


def plays(paths):
    """The music plays in an export: files, folders or the zip. Podcasts, audiobooks and video are left out."""
    found = []
    for row in entries(paths):
        track = track_of(row)
        if track is None:
            continue
        artist, title, ms = track
        started = row.get("reason_start")
        found.append(Play(artist=artist, title=title, album=str(row.get("master_metadata_album_album_name") or ""),
                          ms=ms, ts=str(row.get("ts") or row.get("endTime") or ""),
                          deliberate=None if not started else started in DELIBERATE_STARTS,
                          complete=row.get("reason_end") in COMPLETE_ENDS,
                          skipped=bool(row.get("skipped"))))
    return found


def _when(text):
    """A play's time, always with its time zone. The standard export writes "2024-03-01 21:05" (UTC, unmarked) and
    the extended one "2024-03-01T21:05:11Z"; both are read as UTC, so a mixed export compares."""
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def summarize(found, *, recent_days=365, now=None, limit=40):
    """Totals by year, top artists (by hours) over all and recently, and the songs chosen and come back to most:
    ordered by how often they were chosen, then by the days they were played on, then by hours. A song's "chosen"
    is None when none of its plays records how it was started (the short export)."""
    heard = [play for play in found if play.ms >= MINIMUM_MS]
    stamps = [_when(play.ts) for play in heard]
    latest = now or max((stamp for stamp in stamps if stamp), default=datetime.now(timezone.utc))
    cutoff = latest - timedelta(days=recent_days)
    years, artists, recent_artists = Counter(), Counter(), Counter()
    songs = defaultdict(lambda: {"ms": 0, "plays": 0, "chosen": None, "live": False, "studio": False, "days": set(),
                                 "artist": "", "title": ""})
    for play, stamp in zip(heard, stamps):
        hours = play.ms / 3.6e6
        if stamp:
            years[stamp.year] += hours
        artists[play.artist] += hours
        if stamp and stamp >= cutoff:
            recent_artists[play.artist] += hours
        key = (play.artist.casefold(), song_key(play.title))
        song = songs[key]
        song["artist"], song["title"] = song["artist"] or play.artist, song["title"] or play.title
        song["ms"] += play.ms
        song["plays"] += 1
        if play.deliberate is not None:
            song["chosen"] = (song["chosen"] or 0) + bool(play.deliberate)
        live = bool(LIVE.search(play.title) or LIVE.search(play.album))
        song["live" if live else "studio"] = True
        if stamp:
            song["days"].add(stamp.date())
    ranked = []
    for song in songs.values():
        ranked.append({"artist": song["artist"], "title": song["title"], "hours": round(song["ms"] / 3.6e6, 1),
                       "plays": song["plays"], "chosen": song["chosen"], "days": len(song["days"]),
                       "both_versions": song["live"] and song["studio"]})
    # Choosing a song, again and again, is the strongest sign of liking it; then the days it was come back to.
    ranked.sort(key=lambda song: (song["chosen"] or 0, song["days"], song["hours"]), reverse=True)
    return {"hours": round(sum(play.ms for play in heard) / 3.6e6), "plays": len(heard),
            "years": {year: round(hours) for year, hours in sorted(years.items())},
            "artists": [(artist, round(hours, 1)) for artist, hours in artists.most_common(limit)],
            "recent_artists": [(artist, round(hours, 1)) for artist, hours in recent_artists.most_common(limit)],
            "songs": ranked[:limit * 2]}


def with_presets(songs, names):
    """Which songs already have a preset or sound of the player's, matched on the song's name."""
    def words(text):
        return {word for word in re.findall(r"[^\W_]+", song_key(text).casefold()) if word not in FILLER}

    named = [(words(name), name) for name in names]
    out = []
    for song in songs:
        title = words(song["title"])
        # Whole words: every word of the shorter name appears in the longer ("Brothers" in "Brothers In Arms"),
        # never part of a word ("Gold" is not in "Goldtop").
        match = next((name for found, name in named if found and title
                      and (found <= title or title <= found) and max(map(len, found & title or {""})) >= 4), None)
        out.append({**song, "preset": match})
    return out


FILLER = {"the", "a", "an", "of", "in", "and", "to", "my", "solo", "lead", "rhythm", "clean", "tone", "preset",
          "remastered", "live", "version", "feat"}


GUITAR_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["artists"], "properties": {
    "artists": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["artist", "guitar_led", "guitarists", "tones", "songs_for_presets", "note"],
                "properties": {"artist": {"type": "string"}, "guitar_led": {"type": "boolean"},
                               "guitarists": {"type": "array", "items": {"type": "string"}},
                               "tones": {"type": "array", "items": {"type": "string", "enum": [
                                   "clean", "edge of breakup", "crunch", "overdriven", "saturated", "fuzz",
                                   "acoustic", "ambient", "modulated", "bass-led", "none"]}},
                               "songs_for_presets": {"type": "array", "items": {"type": "string"}},
                               "note": {"type": "string"}}}}}}


def guitar_context(assistant, artists, songs, *, by="player", cancel=None):
    """Ask the assistant, once, what kind of guitar music the most listened artists make. Facts it is unsure of
    it should leave out; the answer is a prior for designing, not a measurement."""
    prompt = ("For each artist below, say whether their music is guitar-led, who plays guitar, which families of "
              "guitar tone they mostly use (from the listed words only), and up to three of the listed songs "
              "that would make good presets to build for a guitarist who listens to them. Use only what is well "
              "documented; leave a field empty rather than guess. note: one short line on the guitar sound.\n\n"
              + json.dumps({"artists": [artist for artist, _ in artists],
                            "songs": [f"{song['artist']} - {song['title']}" for song in songs]}, ensure_ascii=False))
    return assistant.ask("listening", prompt, GUITAR_SCHEMA, by=by, web=False, seconds=180, cancel=cancel)["artists"]


def prior(record, *, limit=10):
    """What the designer may know of the player's listening: the guitar-led artists listened to most, with their
    guitarists and tone families, and how those families add up. None when the guitar context was never asked."""
    context = (record or {}).get("guitar_context") or []
    hours = {artist.casefold(): h for artist, h in ((record or {}).get("summary") or {}).get("artists", [])}
    led = [item for item in context if item.get("guitar_led")]
    led.sort(key=lambda item: hours.get(item["artist"].casefold(), 0), reverse=True)
    if not led:
        return None
    families = Counter(tone for item in led for tone in item.get("tones", []) if tone != "none")
    return {"artists": [{"artist": item["artist"], "guitarists": item.get("guitarists", [])[:3],
                         "tones": item.get("tones", [])} for item in led[:limit]],
            "tone_families": dict(families.most_common())}


def report(summary, context=None, presets=None):
    """Plain lines for the player, and nothing that is not already in the summary or the context."""
    lines = [f"{summary['hours']} hours of music over {len(summary['years'])} years "
             f"({', '.join(f'{year}: {hours} h' for year, hours in summary['years'].items())})."]
    lines.append("Most listened artists: " + ", ".join(f"{a} ({h} h)" for a, h in summary["artists"][:15]) + ".")
    lines.append("In the last year: " + ", ".join(f"{a} ({h} h)" for a, h in summary["recent_artists"][:10]) + ".")
    if context:
        led = [item for item in context if item.get("guitar_led")]
        tones = Counter(tone for item in led for tone in item.get("tones", []))
        lines.append(f"Of the top {len(context)} artists, {len(led)} make guitar-led music; their guitar tones lean "
                     f"to {', '.join(f'{tone} ({n})' for tone, n in tones.most_common(6))}.")
    songs = presets if presets is not None else summary["songs"]
    recorded = any(song.get("chosen") is not None for song in songs)
    lines.append("Songs chosen and come back to most:" if recorded else
                 "Songs come back to on the most days (this export does not record how a play was started, so "
                 "which were chosen is not known):")
    for song in songs[:25]:
        extra = f"; you have a preset: {song['preset']}" if song.get("preset") else ""
        both = "; heard live and studio" if song.get("both_versions") else ""
        chosen = "chosen: not recorded, played" if song.get("chosen") is None else f"chosen {song['chosen']} times, played"
        lines.append(f"  {song['artist']} - {song['title']}: {chosen} on {song['days']} days, {song['hours']} h{both}{extra}")
    return lines
