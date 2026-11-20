"""The listening-history analysis, on a made-up export: what is kept of each play, and what it concludes."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from lab import listening


def row(artist, title, ms=200_000, ts="2026-05-01T20:00:00Z", start="clickrow", end="trackdone", shuffle=False,
        album="Album"):
    return {"ts": ts, "ms_played": ms, "master_metadata_track_name": title, "master_metadata_album_artist_name": artist,
            "master_metadata_album_album_name": album, "reason_start": start, "reason_end": end, "shuffle": shuffle,
            "skipped": end != "trackdone", "ip_addr": "203.0.113.9", "conn_country": "ZZ", "platform": "test"}


EXPORT = ([row("Band A", "Song One", ts=f"2026-0{m}-0{d}T20:00:00Z") for m in range(1, 4) for d in range(1, 4)]
          + [row("Band A", "Song One - Live", album="Live at Somewhere", ts="2025-03-01T10:00:00Z")]
          + [row("Band B", "Background", start="trackdone", shuffle=True, ts=f"2026-04-0{d}T10:00:00Z")
             for d in range(1, 10)]
          + [row("Band C", "Skipped", ms=5_000, end="fwdbtn")]
          + [{"ts": "2026-01-01T00:00:00Z", "ms_played": 900_000, "episode_name": "A podcast"}])


class ListeningTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.zip = Path(folder.name) / "export.zip"
        with zipfile.ZipFile(self.zip, "w") as archive:
            archive.writestr("Spotify Extended Streaming History/Streaming_History_Audio_2026.json", json.dumps(EXPORT))
            archive.writestr("Spotify Extended Streaming History/Streaming_History_Video_2026.json", json.dumps([]))

    def test_only_music_and_only_the_fields_needed_are_kept(self):
        found = listening.plays([self.zip])
        self.assertEqual(len(found), len(EXPORT) - 1)                     # the podcast is left out
        self.assertEqual(set(vars(found[0])), {"artist", "title", "album", "ms", "ts", "deliberate", "complete",
                                               "skipped"})                 # no address, country or device

    def test_chosen_and_returned_to_outranks_background_listening(self):
        summary = listening.summarize(listening.plays([self.zip]))
        top = summary["songs"][0]
        self.assertEqual((top["artist"], top["chosen"], top["days"]), ("Band A", 10, 10))
        self.assertTrue(top["both_versions"])                              # heard live and in the studio
        background = next(song for song in summary["songs"] if song["artist"] == "Band B")
        self.assertEqual(background["chosen"], 0)
        self.assertNotIn("Band C", [artist for artist, _ in summary["artists"]])   # under 30 s: not counted

    def test_songs_with_a_preset_are_found_by_name(self):
        summary = listening.summarize(listening.plays([self.zip]))
        marked = listening.with_presets(summary["songs"], ["Song One lead", "Something else"])
        self.assertEqual(marked[0]["preset"], "Song One lead")

    def test_the_report_says_only_what_was_summarized(self):
        summary = listening.summarize(listening.plays([self.zip]))
        lines = listening.report(summary, [{"artist": "Band A", "guitar_led": True, "tones": ["crunch"]},
                                           {"artist": "Band B", "guitar_led": False, "tones": ["none"]}])
        self.assertTrue(any("1 make guitar-led music" in line for line in lines))
        self.assertTrue(any("Band A - Song One" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
