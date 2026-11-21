"""Songs to build collections for, from a Spotify account export."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import zipfile

from lab.player import spotify
from lab.sound.sounds import Store

MINUTE = 60_000


def standard(artist, track, ms):
    return {"endTime": "2024-01-01 10:00", "artistName": artist, "trackName": track, "msPlayed": ms}


def extended(artist, track, ms):
    return {"ts": "2024-01-01T10:00:00Z", "master_metadata_album_artist_name": artist,
            "master_metadata_track_name": track, "ms_played": ms}


class Listening(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.root = Path(self.folder.name)
        (self.root / "StreamingHistory_music_0.json").write_text(json.dumps([
            standard("Pink Floyd", "Time", 7 * MINUTE),
            standard("Pink Floyd", "Time - 2011 Remastered Version", 5 * MINUTE),
            standard("Steve Vai", "For the Love of God", 6 * MINUTE),
            standard("Steve Vai", "For the Love of God", 10_000),           # a skip, not counted
            standard("Dire Straits", "Money for Nothing", 4 * MINUTE),
        ]), encoding="utf-8")
        (self.root / "Streaming_History_Audio_2020-2024_0.json").write_text(json.dumps([
            extended("Dire Straits", "Money for Nothing (Live)", 9 * MINUTE),
            extended("Metallica", "Enter Sandman", 2 * MINUTE),
            {"ts": "2024-01-01T10:00:00Z", "episode_name": "A podcast", "ms_played": 50 * MINUTE},
        ]), encoding="utf-8")

    def tearDown(self):
        self.folder.cleanup()

    def files(self):
        return sorted(self.root.glob("*.json"))

    def test_ranks_by_time_with_versions_together_and_skips_dropped(self):
        ranked = spotify.rank(spotify.history(self.files()))
        self.assertEqual([(t.artist, t.title) for t in ranked],
                         [("Dire Straits", "Money for Nothing"), ("Pink Floyd", "Time"),
                          ("Steve Vai", "For the Love of God"), ("Metallica", "Enter Sandman")])
        self.assertEqual((ranked[0].minutes, ranked[0].plays), (13.0, 2))
        self.assertEqual(ranked[1].text, "Pink Floyd – Time (12 min, 2 plays)")

    def test_reads_a_folder_and_a_zip(self):
        archive = self.root / "my_spotify_data.zip"
        with zipfile.ZipFile(archive, "w") as packed:
            for path in self.files():
                packed.write(path, f"Spotify Extended Streaming History/{path.name}")
            packed.writestr("Spotify Extended Streaming History/Streaming_History_Video_2024.json", "[]")
        self.assertEqual(len(spotify.history([archive])), len(spotify.history([self.root])))
        self.assertEqual(len(spotify.history([archive])), 7)

    def test_proposals_leave_out_songs_already_in_the_store(self):
        store = Store(self.root / "store")
        store.save_song({"name": "Time", "instrument": None, "phrase": None, "parts": []})
        store.save_song({"name": "Metallica - Enter Sandman", "instrument": None, "phrase": None, "parts": []})
        found = spotify.proposals(self.files(), store, limit=10)
        self.assertEqual([t.title for t in found], ["Money for Nothing", "For the Love of God"])
        self.assertEqual(found[0].to_record()["minutes"], 13.0)

    def test_a_song_named_with_its_artist_does_not_hide_another_artists_song(self):
        store = Store(self.root / "store")
        store.save_song({"name": "Queen - Time", "instrument": None, "phrase": None, "parts": []})
        titles = [t.title for t in spotify.proposals(self.files(), store)]
        self.assertIn("Time", titles)

    def test_limit_and_no_store(self):
        self.assertEqual(len(spotify.proposals(self.files(), limit=2)), 2)

    def test_a_file_that_is_not_a_history_is_refused(self):
        wrong = self.root / "wrong.json"
        wrong.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            spotify.history([wrong])

    def test_a_play_time_that_is_not_a_number_is_left_out_and_one_written_as_text_is_read(self):
        # C5: the two readers disagreed; lab.player.listening.plays raised ValueError on "a minute".
        odd = self.root / "Streaming_History_Audio_odd.json"
        odd.write_text(json.dumps([extended("A", "Words", "a minute"), extended("A", "Text", "61000"),
                                   extended("A", "Float", 61000.0), extended("A", "Nothing", None),
                                   extended("A", "Flag", True), "not an entry"]), encoding="utf-8")
        self.assertEqual(spotify.history([odd]), [("A", "Text", 61000), ("A", "Float", 61000)])

    def test_an_export_is_read_one_file_at_a_time(self):
        # C5: every file's text was read before any was parsed: the whole export in memory at once.
        (self.root / "Streaming_History_Audio_2025_0.json").write_text("{", encoding="utf-8")   # broken, and last
        reading = spotify.entries([self.root])
        first = next(reading)                       # the earlier files are already giving entries
        self.assertEqual(first["master_metadata_album_artist_name"], "Dire Straits")
        with self.assertRaises(ValueError):
            list(reading)

    def test_version_marks_are_whole_words(self):
        folded = {"Black Dog - 2012 Remaster": "black dog", "Time - 2011 Remastered Version": "time",
                  "Sultans of Swing - Live At Hammersmith": "sultans of swing", "Layla - Acoustic": "layla",
                  "Layla (Acoustic)": "layla", "Angie - 2009 Mix": "angie", "Money (Mono)": "money"}
        kept = {"Hey Joe - Deliver Me": "hey joe deliver me", "Little Wing (Olive Branch)": "little wing olive branch",
                "Song (Demolition)": "song demolition", "Money (That's What I Want)": "money that s what i want",
                "One - Unmixed": "one unmixed"}
        for title, key in {**folded, **kept}.items():
            self.assertEqual(spotify.song_key(title), key, title)


if __name__ == "__main__":
    unittest.main()
