"""The store: records that survive a later version, files never written over when unreadable, songs by name."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest import mock

from lab.sound import sounds
from lab.sound.chain import Block, Chain, Row
from lab.sound.sounds import INSTRUMENTS, Sound, Store, slug

CHAIN = Chain((Row((Block(22).set_display(0, 5.), Block(1081), Block(12001)), inlet=1, outlet=19),))


class StoreTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.store = Store(self.folder)


class SoundRecordTest(StoreTest):
    def test_a_record_with_a_field_this_version_does_not_know_is_still_a_sound(self):
        first = self.store.save(Sound("One", CHAIN, {"kind": "designed"}, knobs={"drive": .3}))
        path = self.folder / "sounds" / f"{first.id}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["rating"] = 5                                  # a later version's
        del record["played"]                                  # and one this version has that an earlier did not
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual([sound.name for sound in self.store.sounds()], ["One"])
        found = self.store.sound(first.id)
        self.assertEqual((found.knobs, found.played, found.chain), ({"drive": .3}, False, CHAIN))

    def test_a_file_that_is_not_a_sound_is_a_value_error_and_is_left_out_of_the_list(self):
        good = self.store.save(Sound("Good", CHAIN, {"kind": "designed"}))
        for name, text in (("cut", json.dumps(good.to_record())[:200]), ("list", "[1, 2]"), ("nochain", '{"name": "x"}')):
            (self.folder / "sounds" / f"{name}.json").write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                self.store.sound(name)
        self.assertEqual([sound.name for sound in self.store.sounds()], ["Good"])

    def test_a_save_waits_out_a_reader_and_two_saves_of_one_sound_do_not_collide(self):
        sound = self.store.save(Sound("One", CHAIN, {"kind": "designed"}))
        real, refused = sounds.os.replace, []

        def busy(source, target):
            if len(refused) < 2:
                refused.append(target)
                raise PermissionError(13, "Access is denied")
            return real(source, target)

        with mock.patch.object(sounds.os, "replace", side_effect=busy), mock.patch.object(sounds.time, "sleep"):
            sound.name = "Renamed"
            self.store.save(sound)
        self.assertEqual(self.store.sound(sound.id).name, "Renamed")
        faults = []

        def saver():
            for _ in range(60):
                try:
                    self.store.save(sound)
                except OSError as error:
                    faults.append(error)

        threads = [threading.Thread(target=saver) for _ in range(2)]
        [thread.start() for thread in threads]
        [thread.join() for thread in threads]
        self.assertEqual(faults, [])
        self.assertEqual([path.name for path in (self.folder / "sounds").iterdir()], [f"{sound.id}.json"])

    def test_a_save_that_cannot_finish_leaves_the_old_file_and_no_part(self):
        sound = self.store.save(Sound("One", CHAIN, {"kind": "designed"}))
        with mock.patch.object(sounds.os, "replace", side_effect=PermissionError(13, "Access is denied")), \
                mock.patch.object(sounds.time, "sleep"):
            sound.name = "Renamed"
            with self.assertRaises(PermissionError):
                self.store.save(sound)
        self.assertEqual(self.store.sound(sound.id).name, "One")
        self.assertEqual([path.name for path in (self.folder / "sounds").iterdir()], [f"{sound.id}.json"])


class InstrumentsTest(StoreTest):
    def test_the_starting_list_is_written_once_for_the_player_to_edit(self):
        self.assertEqual(self.store.instruments(), list(INSTRUMENTS))
        self.assertEqual(json.loads((self.folder / "instruments.json").read_text(encoding="utf-8")), list(INSTRUMENTS))

    def test_a_list_that_cannot_be_read_is_set_aside_and_never_written_over(self):
        mine = json.dumps([{"name": "Strat", "kind": "electric", "input": 2, "settings": [], "wiring": [], "about": ""}])
        path = self.folder / "instruments.json"
        path.write_text(mine, encoding="utf-8")
        self.assertEqual([item["name"] for item in self.store.instruments()], ["Strat"])
        path.write_text(mine[:40], encoding="utf-8")          # cut short while being edited
        self.assertEqual(self.store.instruments(), list(INSTRUMENTS))
        spare = self.folder / "instruments.json.bad"
        self.assertEqual(spare.read_text(encoding="utf-8"), mine[:40])
        self.assertEqual(self.store.aside, [spare])
        self.assertEqual(self.store.instruments(), list(INSTRUMENTS))      # asked again: still nothing written
        self.assertFalse(path.exists())
        self.assertEqual(spare.read_text(encoding="utf-8"), mine[:40])
        self.assertIsNone(self.store.instrument("Strat"))
        self.assertEqual(self.store.instrument("charvel")["name"], "Charvel")


class SongsTest(StoreTest):
    def test_names_in_any_script_keep_their_own_file(self):
        self.assertEqual(slug("AC/DC - Back in Black"), "ac-dc-back-in-black")
        self.assertEqual(slug("שיר אחד"), "שיר-אחד")
        first = {"name": "שיר אחד", "instrument": "Charvel", "phrase": None, "parts": [{"name": "Lead", "sound": "a"}]}
        self.store.save_song(first)
        self.assertEqual(self.store.song("שיר אחר")["parts"], [])            # another song, not the first's record
        self.store.save_song({"name": "שיר אחר", "instrument": None, "phrase": None,
                              "parts": [{"name": "Lead", "sound": "b"}]})
        self.assertEqual(self.store.song("שיר אחד"), first)
        self.assertEqual(sorted(song["name"] for song in self.store.songs()), ["שיר אחד", "שיר אחר"])

    def test_one_song_however_its_name_is_punctuated_and_different_names_never_share_a_file(self):
        part = lambda sound: [{"name": "Main", "sound": sound}]
        self.store.save_song({"name": "AC/DC - Back in Black", "instrument": None, "phrase": None, "parts": part("a")})
        self.assertEqual(self.store.song("ac dc  –  back in black")["parts"], part("a"))   # the same song, as typed later
        long = "Supper's Ready " * 12
        for name, sound in (("!!!", "c"), ("???", "d"), (long, "e"), (long + "part two", "f"), ("CON", "g"), ("AC DC", "h")):
            self.store.save_song({"name": name, "instrument": None, "phrase": None, "parts": part(sound)})
        for name, sound in (("AC/DC - Back in Black", "a"), ("!!!", "c"), ("???", "d"), (long, "e"),
                            (long + "part two", "f"), ("con", "g"), ("AC DC", "h")):
            self.assertEqual(self.store.song(name)["parts"], part(sound), name)
        self.assertEqual(len(self.store.songs()), 7)
        self.assertNotIn("con.json", [path.name.casefold() for path in (self.folder / "songs").iterdir()])
        self.store.save_song({"name": "ac dc back in black", "instrument": "Les Paul", "phrase": None, "parts": []})
        self.assertEqual(len(self.store.songs()), 7)                          # saved again into its own file
        self.assertEqual(self.store.song("AC/DC - Back in Black")["instrument"], "Les Paul")

    def test_a_song_named_as_the_old_shared_file_was_does_not_write_over_what_that_file_holds(self):
        old = {"name": "שיר אחד", "instrument": "Charvel", "phrase": None, "parts": [{"name": "Lead", "sound": "a"}]}
        (self.folder / "songs" / "untitled.json").write_text(json.dumps(old), encoding="utf-8")
        self.store.save_song({"name": "Untitled", "instrument": None, "phrase": None, "parts": []})
        self.assertEqual(self.store.song("שיר אחד"), old)                    # moved to its own place, not lost
        self.assertEqual(self.store.song("untitled")["parts"], [])
        self.assertEqual(sorted(song["name"] for song in self.store.songs()), ["Untitled", "שיר אחד"])

    def test_a_song_kept_by_an_earlier_version_is_still_found_and_moves_to_its_own_file(self):
        old = {"name": "שיר אחד", "instrument": "Charvel", "phrase": "p1", "parts": [{"name": "Lead", "sound": "a"}]}
        (self.folder / "songs" / "untitled.json").write_text(json.dumps(old), encoding="utf-8")
        latin = {"name": "Comfortably Numb", "instrument": None, "phrase": None, "parts": [{"name": "Solo", "sound": "s"}]}
        (self.folder / "songs" / "comfortably-numb.json").write_text(json.dumps(latin), encoding="utf-8")
        self.assertEqual(self.store.song("שיר אחד"), old)
        self.assertEqual(self.store.song("comfortably numb"), latin)
        self.assertEqual(self.store.song("שיר אחר")["parts"], [])            # the old shared file is not this song's
        found = self.store.song("שיר אחד")
        found["parts"].append({"name": "Rhythm", "sound": "b"})
        self.store.save_song(found)
        self.assertEqual(sorted(path.name for path in (self.folder / "songs").iterdir()),
                         ["comfortably-numb.json", "שיר-אחד.json"])
        self.assertEqual(len(self.store.song("שיר אחד")["parts"]), 2)

    def test_songs_are_written_whole_and_an_unreadable_one_is_set_aside_not_written_over(self):
        path = self.folder / "songs" / "my-song.json"
        path.write_text('{"name": "My song", "par', encoding="utf-8")
        self.assertEqual(self.store.song("My song")["parts"], [])
        self.assertEqual(self.store.songs(), [])
        with mock.patch.object(sounds.os, "replace", wraps=sounds.os.replace) as swapped:
            self.store.save_song({"name": "My song", "instrument": None, "phrase": None, "parts": []})
        self.assertTrue(any(str(call.args[1]).endswith("my-song.json") for call in swapped.call_args_list))
        self.assertEqual((self.folder / "songs" / "my-song.json.bad").read_text(encoding="utf-8"), '{"name": "My song", "par')
        self.assertEqual([song["name"] for song in self.store.songs()], ["My song"])


if __name__ == "__main__":
    unittest.main()
