"""The listening-history analysis, on a made-up export: what is kept of each play, and what it concludes."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from lab.player import listening


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
        self.assertIn("Songs chosen and come back to most:", lines)
        self.assertTrue(any("Band A - Song One: chosen 10 times, played on 10 days" in line for line in lines))

    def export(self, rows):
        path = Path(self.zip).with_name("Streaming_History_Audio_made_up.json")
        path.write_text(json.dumps(rows), encoding="utf-8")
        return [path]

    def test_a_click_is_a_choice_whatever_the_shuffle_switch_says(self):
        # B7: twenty clicks on a track with shuffle mode on read "chosen 0": the export's shuffle field is the
        # switch, not how the play was started.
        rows = [row("Band D", "Clicked With Shuffle On", shuffle=True, ts=f"2024-01-{day:02d}T20:00:00Z")
                for day in range(1, 21)]
        song = listening.summarize(listening.plays(self.export(rows)))["songs"][0]
        self.assertEqual((song["chosen"], song["days"]), (20, 20))

    def test_songs_are_ordered_by_how_often_they_were_chosen_then_by_days(self):
        # B7: by chosen + days, a track that only ever came up by itself on 25 days, and one skipped after 40 s
        # on 30 days, both ranked above one clicked on 12.
        rows = ([row("Band E", "Never Chosen", start="trackdone", ts=f"2024-02-{day:02d}T20:00:00Z")
                 for day in range(1, 26)]
                + [row("Band F", "Clicked Twelve Times", ts=f"2024-03-{day:02d}T20:00:00Z") for day in range(1, 13)]
                + [row("Band G", "Skipped After 40 s", ms=40_000, start="fwdbtn", end="fwdbtn",
                       ts=f"2024-04-{day:02d}T20:00:00Z") for day in range(1, 31)])
        songs = listening.summarize(listening.plays(self.export(rows)))["songs"]
        self.assertEqual([song["title"] for song in songs],
                         ["Clicked Twelve Times", "Skipped After 40 s", "Never Chosen"])
        self.assertEqual([song["chosen"] for song in songs], [12, 0, 0])
        self.assertNotIn("completion", songs[0])

    def test_the_short_export_does_not_record_how_a_play_started_and_says_so(self):
        # B7: every song of the short export read "chosen 0 times".
        rows = [{"endTime": f"2024-01-{day:02d} 21:05", "artistName": "Band H", "trackName": "Song", "msPlayed": 200000}
                for day in range(1, 6)]
        found = listening.plays(self.export(rows))
        self.assertIsNone(found[0].deliberate)
        summary = listening.summarize(found)
        self.assertEqual((summary["songs"][0]["chosen"], summary["songs"][0]["days"]), (None, 5))
        lines = listening.report(summary)
        self.assertTrue(any("does not record how a play was started" in line for line in lines))
        self.assertTrue(any("Band H - Song: chosen: not recorded, played on 5 days" in line for line in lines))
        self.assertFalse(any("chosen 0" in line or "None" in line for line in lines))
        json.dumps(summary, default=str)

    def test_odd_entries_do_not_stop_the_reading(self):
        rows = [{"ts": None, "ms_played": None, "master_metadata_track_name": "T", "master_metadata_album_artist_name": "A"},
                {"ts": "garbage", "ms_played": "61000", "master_metadata_track_name": "T2",
                 "master_metadata_album_artist_name": "A", "reason_start": "clickrow"},
                {"ts": "2024-05-01T00:00:00Z", "ms_played": "a minute", "master_metadata_track_name": "T3",
                 "master_metadata_album_artist_name": "A"}, "not an entry"]
        found = listening.plays(self.export(rows))
        self.assertEqual([(play.title, play.ms) for play in found], [("T2", 61000)])     # no play time: not a play
        self.assertEqual(listening.summarize(found)["plays"], 1)

    def test_the_designer_gets_only_guitar_led_artists_most_listened_first(self):
        summary = listening.summarize(listening.plays([self.zip]))
        record = {"summary": summary, "guitar_context": [
            {"artist": "Band B", "guitar_led": True, "guitarists": ["X"], "tones": ["clean"]},
            {"artist": "Band A", "guitar_led": True, "guitarists": ["Y"], "tones": ["crunch", "fuzz"]},
            {"artist": "Band C", "guitar_led": False, "guitarists": [], "tones": ["none"]}]}
        found = listening.prior(record)
        self.assertEqual([item["artist"] for item in found["artists"]], ["Band A", "Band B"])
        self.assertEqual(found["tone_families"], {"crunch": 1, "fuzz": 1, "clean": 1})
        self.assertIsNone(listening.prior({"summary": summary}))


if __name__ == "__main__":
    unittest.main()
