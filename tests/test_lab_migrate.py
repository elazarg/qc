"""The old app's history comes into the new Store: drafts and presets as sounds, rounds, recorded takes as phrases.

Every test works on a small synthetic copy of the old formats in a temporary folder.
"""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lab import catalog, migrate
from lab.chain import SCENES
from lab.sim import SimUnit
from lab.sounds import Store

MY_PRESETS = "/media/p4/Presets/My Presets"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def draft(draft_id, name, chain, **extra):
    return {"name": name, "target": f"{name} target", "explanation": f"Why {name}.", "chain": chain,
            "sources": [{"title": "A source", "url": "https://example.com"}], "id": draft_id,
            "created_utc": "2026-11-18T10:07:33.057296+00:00", "request": f"make {name}", "guitar": "Charvel",
            "parent": None, **extra}


PLEXI = [{"model_id": 5, "settings": [{"name": "OVERDRIVE", "value": 2.3}]},
         {"model_id": 1081, "settings": [{"name": "GAIN", "value": 6}, {"name": "BASS", "value": 3.2}]},
         {"model_id": 12006, "settings": []},
         {"model_id": 6004, "settings": [{"name": "MIX", "value": 24}, {"name": "DELAY TIME", "value": 375}]}]


def old_history(root):
    """A small tone_sessions folder in the old app's formats."""
    write(root / "drafts" / "aaaaaaaaaaaa.json", draft("aaaaaaaaaaaa", "Plexi Bloom", PLEXI))
    write(root / "drafts" / "aaaaaaaaaaaa.kept.json", {
        "label": "Plexi Bloom · Tone Lab 1A",
        "preset": {"name": "Plexi Bloom", "setlist": "Tone Lab",
                   "position": {"folder_key": "/media/p4/Presets/Tone Lab", "position": 0, "is_factory": False}},
        "scene": "A"})
    write(root / "drafts" / "bbbbbbbbbbbb.json", draft("bbbbbbbbbbbb", "Plexi Child", PLEXI[1:3], parent="aaaaaaaaaaaa"))
    write(root / "drafts" / "bbbbbbbbbbbb.kept.json", {"label": "Plexi Child · My Presets 9B"})
    two_paths = draft("cccccccccccc", "Two Amps", PLEXI,
                      parallel=[{"model_id": 1120, "settings": [{"name": "VOLUME", "value": 7}]},
                                {"model_id": 12013, "settings": []}],
                      blend={"level": -3, "spread": 40}, scenes=["Rhythm", "Lead"])
    two_paths["chain"] = [PLEXI[0], {**PLEXI[1], "scene_settings": [{"scene": 1, "name": "GAIN", "value": 8}]},
                          PLEXI[2], {**PLEXI[3], "off_in_scenes": [0]}]
    write(root / "drafts" / "cccccccccccc.json", two_paths)
    write(root / "drafts" / "dddddddddddd.json", "{ not json")
    write(root / "drafts" / "eeeeeeeeeeee.json", draft("eeeeeeeeeeee", "Unknown", [{"model_id": 987654, "settings": []}]))
    write(root / "drafts" / "ffffffffffff.json", draft("ffffffffffff", "With IR", [
        PLEXI[1], {"model_id": 29001, "settings": [],
                   "asset": {"kind": "ir", "key": "x", "name": "My IR", "source": "user"}}]))
    write(root / "rounds" / "111111111111.json", {
        "id": "111111111111", "created_utc": "2026-11-19T09:01:56+00:00", "request": "Time solo", "guitar": "Charvel",
        "aim": {"drive": "overdriven", "tone": "balanced", "space": "ambient", "note": "Singing."}, "parent": None,
        "candidates": [
            {"draft": "aaaaaaaaaaaa", "name": "Plexi Bloom", "differs": "The Plexi pushes.",
             "heard": {"squash": .8, "level_db": -39.5, "drive": "overdriven", "tone": "bright", "space": "ambient"},
             "shape": {"warmth": .8}, "tags": ["overdriven"], "user_tags": ["This is it"], "review": "Lovely bends",
             "played": True, "miss": 0},
            {"draft": "cccccccccccc", "name": "Two Amps", "differs": "Two amps.", "heard": None, "shape": {},
             "tags": [], "user_tags": [], "review": "", "played": False},
            {"draft": "dddddddddddd", "name": "Broken", "differs": "", "heard": None, "shape": {}, "tags": [],
             "user_tags": [], "review": "", "played": False}]})
    unit = SimUnit()
    preset = unit._preset("Vai for The Love God", [(1081, {}), (12006, {})]).SerializeToString()
    working = unit._preset("Tone Lab Style", [(1081, {})]).SerializeToString()
    write(root / "presets" / "p1" / "preset.pb", preset)
    write(root / "presets" / "p1" / "preset.json", {
        "name": "Vai for The Love God", "position": {"folder_key": MY_PRESETS, "position": 5, "is_factory": False},
        "firmware": "4.1.1", "captured_utc": "2026-11-18T00:40:00+00:00", "reason": "Before Tone Lab reamp"})
    write(root / "presets" / "p2" / "preset.pb", working)
    write(root / "presets" / "p2" / "preset.json", {
        "name": "Tone Lab Style", "position": {"folder_key": MY_PRESETS, "position": 254, "is_factory": False},
        "captured_utc": "2026-11-18T00:41:00+00:00", "reason": "Previous Tone Lab working copy"})
    write(root / "presets" / "p3" / "preset.pb", b"\xff\xff\xff not a preset")
    write(root / "presets" / "p3" / "preset.json", {"name": "Broken", "position": {"folder_key": MY_PRESETS,
                                                                                  "position": 9}})
    write(root / "device_snapshots" / "before-vai.pb", preset)       # the same preset, read again
    write(root / "device_snapshots" / "before-vai.json", {
        "preset": "Vai for The Love God", "position": {"folder_key": MY_PRESETS, "position": 5, "is_factory": False}})
    takes = {
        "clip-20261118-011223-aaaaaa": {"duration_seconds": 8.0},
        "clip-20261118-011805-bbbbbb": {"duration_seconds": 8.0, "guitar": "Charvel",
                                        "pickup_positions": ["neck", "up"], "passage": "Muted walk"},
        "clip-20261118-012159-cccccc": {"duration_seconds": 8.0, "guitar": "Charvel", "song": "For the Love of God",
                                        "pickup_context_clip": "clip-20261118-011805-bbbbbb"},
        "clip-20261118-133609-dddddd": {"duration_seconds": 8.0, "guitar": "Charvel"},
        "clip-20261118-133636-eeeeee": {"duration_seconds": 8.0, "guitar": "Charvel", "pickup": "Bridge · Series"},
    }
    for name, info in takes.items():
        write(root / "recordings" / name / "clip.json", info)
        write(root / "recordings" / name / "dry.wav", b"RIFF fake wave " + name.encode())
    write(root / "recordings" / "clip-20261118-140000-ffffff" / "clip.json", {"duration_seconds": 3.0})
    write(root / "guitar_learner" / "charvel-1" / "dk24_hh_toggle" / "r1" / "record.json", {
        "configuration": {"pickup": 4, "wiring": "Series", "volume": 10, "tone": 10}, "seconds": 7.9,
        "source": str(root / "recordings" / "clip-20261118-133609-dddddd" / "dry.wav")})
    write(root / "20261118-013144-draft-c6de54" / "session.json", {"kind": "draft", "draft_id": "aaaaaaaaaaaa"})
    return root


def snapshot(folder):
    return {str(path.relative_to(folder)): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in folder.rglob("*") if path.is_file()}


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.old = old_history(root / "tone_sessions")
        self.home = root / "home"
        self.before = snapshot(self.old)

    def tearDown(self):
        self.temporary.cleanup()

    def run_it(self, **options):
        return migrate.migrate(self.home, self.old, **options)

    def sound_from(self, kind, old_id):
        return Store(self.home).sound(migrate.new_id(kind, old_id))

    def test_drafts_become_designed_sounds_with_their_chain_parent_and_saved_slot(self):
        report = self.run_it()
        self.assertEqual(report["sounds"]["designed"], 3)
        first = self.sound_from("draft", "aaaaaaaaaaaa")
        self.assertEqual((first.name, first.origin["kind"], first.origin["migrated"]["from"],
                          first.origin["migrated"]["id"]), ("Plexi Bloom", "designed", "draft", "aaaaaaaaaaaa"))
        self.assertEqual(first.origin["request"], "make Plexi Bloom")
        self.assertEqual([block.model for _, _, block in first.chain.blocks], [5, 1081, 12006, 6004])
        amp = first.chain.block(0, 1)
        self.assertAlmostEqual(amp.display(catalog.model(1081).index("GAIN")), 6, places=2)
        self.assertEqual(first.chain.rows[0].route, (1, migrate.MULTI_OUT))
        self.assertEqual(first.saved, {"slot": {"folder_key": "/media/p4/Presets/Tone Lab", "position": 0,
                                                "is_factory": False}, "name": "Plexi Bloom"})
        self.assertEqual(first.sources, [{"title": "A source", "url": "https://example.com"}])
        child = self.sound_from("draft", "bbbbbbbbbbbb")
        self.assertEqual(child.parent, first.id)
        self.assertEqual(child.saved["slot"], {"folder_key": MY_PRESETS, "position": 65, "is_factory": False})

    def test_a_second_path_and_scenes_are_laid_out_as_the_old_app_built_them(self):
        self.run_it()
        sound = self.sound_from("draft", "cccccccccccc")
        first, second = sound.chain.rows[0], sound.chain.rows[1]
        self.assertEqual(first.split, (1, 3))
        self.assertEqual([(column, block.model) for column, block in first.blocks],
                         [(0, 5), (1, 1081), (2, 12006), (3, 6004)])
        self.assertEqual([(column, block.model) for column, block in second.blocks], [(1, 1120), (2, 12013)])
        self.assertTrue(first.mixer)
        self.assertEqual(sound.chain.labels[:3], ("Rhythm", "Lead", ""))
        gain = sound.chain.block(0, 1).values[catalog.model(1081).index("GAIN")]
        self.assertIsInstance(gain, tuple)
        self.assertEqual(len(gain), SCENES)
        self.assertAlmostEqual(catalog.to_display(1081, catalog.model(1081).index("GAIN"), gain[1]), 8, places=2)
        self.assertEqual(sound.chain.block(0, 3).bypass, (True,) + (False,) * (SCENES - 1))

    def test_what_cannot_be_read_is_skipped_and_counted(self):
        report = self.run_it()
        skipped = {item["what"]: item["why"] for item in report["skipped"]}
        self.assertEqual(set(skipped), {"draft dddddddddddd", "draft eeeeeeeeeeee", "draft ffffffffffff",
                                        "preset p3", "recording clip-20261118-140000-ffffff"})
        self.assertIn("catalog", skipped["draft eeeeeeeeeeee"])
        self.assertIn("impulse response", skipped["draft ffffffffffff"])

    def test_a_round_comes_across_with_the_players_marks_and_the_old_measurement_kept_aside(self):
        report = self.run_it()
        self.assertEqual(report["rounds"], 1)
        record = json.loads((self.home / "rounds" / f"{migrate.new_id('round', '111111111111')}.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(record["request"], "Time solo")
        self.assertEqual(record["aim"]["drive"], "overdriven")
        self.assertEqual([item["sound"] for item in record["candidates"]],
                         [migrate.new_id("draft", "aaaaaaaaaaaa"), migrate.new_id("draft", "cccccccccccc")])
        sound = self.sound_from("draft", "aaaaaaaaaaaa")
        self.assertEqual(sound.knobs, {"brightness": -.8})   # Warmth carried over: up was darker
        self.assertEqual(sound.marks, ["keeper"])
        self.assertEqual([note["text"] for note in sound.notes], ["Lovely bends"])
        self.assertTrue(sound.played)
        self.assertIsNone(sound.heard)       # measured by the old app another way: the new app listens again
        self.assertEqual(sound.origin["migrated"]["heard"]["level_db"], -39.5)
        self.assertIn("aim: overdriven, balanced, ambient", sound.tags)

    def test_presets_read_from_the_unit_become_sounds_but_working_copies_and_repeats_do_not(self):
        report = self.run_it()
        self.assertEqual(report["sounds"]["preset"], 1)
        self.assertEqual(report["left"]["copies of the old app's working slots, or repeats"], 2)
        sound = self.sound_from("preset", "p1")
        self.assertEqual((sound.name, sound.origin["kind"]), ("Vai for The Love God", "preset"))
        self.assertEqual(sound.origin["slot"], {"folder_key": MY_PRESETS, "position": 5, "is_factory": False})
        self.assertEqual(sound.origin["migrated"]["id"], "p1")
        self.assertEqual([block.model for _, _, block in sound.chain.blocks], [1081, 12006])
        self.assertEqual(report["left"]["comparison sessions (recordings and measurements, no chain)"], 1)

    def test_recorded_takes_become_phrases_with_their_pickup_setting_where_it_is_known(self):
        report = self.run_it()
        self.assertEqual(report["phrases"], {"known": 4, "unknown": 1})
        phrases = {}
        for path in (self.home / "phrases").glob("*/phrase.json"):
            item = json.loads(path.read_text(encoding="utf-8"))
            phrases[item["migrated"]["id"]] = item
            self.assertTrue((path.parent / "dry.wav").is_file())
        self.assertEqual({name: item["setting"] for name, item in phrases.items()}, {
            "clip-20261118-011223-aaaaaa": "unknown",
            "clip-20261118-011805-bbbbbb": "Neck · Parallel",          # from its own note of the switches
            "clip-20261118-012159-cccccc": "Neck · Parallel",          # "same as the previous take"
            "clip-20261118-133609-dddddd": "Inner coils · Series",     # labelled in the Guitar Learner
            "clip-20261118-133636-eeeeee": "Bridge · Series"})
        self.assertEqual(phrases["clip-20261118-012159-cccccc"]["song"], "For the Love of God")
        self.assertEqual(phrases["clip-20261118-011223-aaaaaa"]["instrument"], "unknown")
        dry = self.home / "phrases" / migrate.new_id("recording", "clip-20261118-011805-bbbbbb") / "dry.wav"
        self.assertEqual(dry.read_bytes(), b"RIFF fake wave clip-20261118-011805-bbbbbb")

    def test_running_twice_adds_nothing(self):
        first = self.run_it()
        files = snapshot(self.home)
        second = self.run_it()
        self.assertEqual(snapshot(self.home), files)
        self.assertEqual((second["sounds"], second["rounds"], second["phrases"]),
                         ({"designed": 0, "preset": 0}, 0, {"known": 0, "unknown": 0}))
        self.assertEqual(second["already"], {"sounds": first["sounds"]["designed"] + first["sounds"]["preset"],
                                             "rounds": first["rounds"],
                                             "phrases": sum(first["phrases"].values())})
        self.assertEqual(len(Store(self.home).sounds()), 4)

    def test_a_dry_run_counts_the_same_and_writes_nothing(self):
        dry = self.run_it(dry_run=True)
        self.assertFalse(self.home.exists())
        real = self.run_it()
        self.assertEqual(dry, real)

    def test_the_old_history_is_left_exactly_as_it_was(self):
        self.run_it()
        self.assertEqual(snapshot(self.old), self.before)

    def test_the_summary_says_it_in_a_few_lines(self):
        lines = migrate.summary(self.run_it())
        self.assertIn("3 designs and 1 presets as sounds, 1 rounds", lines[0])
        self.assertTrue(any(line.startswith("Skipped 5") for line in lines))

    def test_an_empty_or_missing_old_folder_brings_nothing(self):
        report = migrate.migrate(self.home, Path(self.temporary.name) / "nothing-here")
        self.assertEqual((report["sounds"], report["rounds"], report["skipped"]),
                         ({"designed": 0, "preset": 0}, 0, []))


if __name__ == "__main__":
    unittest.main()
