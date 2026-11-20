"""The player's taste, learned from synthetic sounds and synthetic choices only."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from lab.player import taste
from lab.sound.chain import Chain
from lab.sound.sounds import Sound, Store


def record(**values):
    """A heard record shaped like lab.hearing.measure.Heard.to_record, with a probe record."""
    top = {key: values.pop(key) for key in list(values) if taste.FEATURES.get(key, ("probe",))[0] == ""}
    return {"compression": .3, "crest_db": 12., "brightness_oct": 0., "low_end_db": 0., "tail_db": -50.,
            "level_db": -20., **top, "probe": {"distortion_db": -30., "distortion_soft_db": -40., **values},
            "distortion": "edge of breakup", "compressed": "light", "brightness": "balanced", "tail": "dry"}


def synthetic(count, seed):
    """Sounds whose measured quantities are independent random numbers."""
    rng = np.random.default_rng(seed)
    sounds = []
    for i in range(count):
        heard = record(tristimulus3=float(rng.uniform(.05, .6)), high_order_db=float(rng.uniform(-60, -20)),
                       compression=float(rng.uniform(0, 1)), brightness_oct=float(rng.uniform(-1, 1)),
                       attack_ms=float(rng.uniform(2, 40)))
        sounds.append(Sound(f"Sound {i}", Chain(), {"kind": "designed"}, id=f"s{i:02d}", heard=heard))
    return sounds


def player(sounds, count, seed, prefers=None, consistent=1.):
    """Comparisons by a simulated player: each pair is decided by `prefers` (a function of a sound), or at random."""
    rng = np.random.default_rng(seed + 1000)
    found = []
    for _ in range(count):
        a, b = rng.choice(len(sounds), 2, replace=False)
        first, second = sounds[a], sounds[b]
        if prefers is None:
            kept_first = rng.random() < .5
        else:
            kept_first = prefers(first) > prefers(second)
            if rng.random() > consistent:
                kept_first = not kept_first
        kept, over = (first, second) if kept_first else (second, first)
        found.append({"kept": kept.id, "over": over.id, "how": "blind", "weight": 1.})
    return found


def rows(sounds):
    return {sound.id: taste.features(sound.heard) for sound in sounds}


def lower_tristimulus3(sound):
    return -sound.heard["probe"]["tristimulus3"]


class FeaturesTest(unittest.TestCase):
    def test_features_are_named_for_the_quantity_and_skip_what_was_not_measured(self):
        heard = record(tristimulus3=.2, hnr_db=31., echo_ms=None, harmonics_db=[-20., -30.], gated=True,
                       attack_ms=7.)
        found = taste.features(heard)
        self.assertEqual(found["tristimulus3"], .2)
        self.assertEqual(found["hnr_db"], 31.)
        self.assertEqual(found["attack_ms"], 7.)
        self.assertEqual(found["distortion_db"], -30.)
        self.assertEqual(found["compression"], .3)
        for absent in ("level_db", "gated", "harmonics_db", "echo_ms", "distortion", "high_order_db"):
            self.assertNotIn(absent, found)
        self.assertTrue(all(isinstance(value, float) for value in found.values()))

    def test_nothing_measured_gives_no_features(self):
        self.assertEqual(taste.features(None), {})
        self.assertEqual(taste.features({}), {})
        self.assertEqual(taste.features({"compression": float("nan"), "probe": None}), {})

    def test_a_quantity_found_in_the_other_place_still_counts(self):
        self.assertEqual(taste.features({"probe": {"attack_ms": 5.}})["attack_ms"], 5.)

    def test_every_feature_has_a_phrase_for_higher_and_lower(self):
        for name, (where, more, less) in taste.FEATURES.items():
            self.assertIn(where, ("", "probe"))
            self.assertNotEqual(more, less, name)


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_choices_are_appended_and_read_back(self):
        taste.record_choice(self.folder, "a", "b", "blind")
        taste.record_choice(self.folder, "c", "a", "a/b", round_id="r1")
        with (self.folder / taste.CHOICES).open("a", encoding="utf-8") as file:
            file.write("not json\n")
        self.assertIsNone(taste.record_choice(self.folder, "a", "a", "blind"))
        with self.assertRaises(ValueError):
            taste.record_choice(self.folder, "a", "b", "guessed")
        found = taste.choices(self.folder)
        self.assertEqual([(entry["kept"], entry["over"], entry["how"]) for entry in found],
                         [("a", "b", "blind"), ("c", "a", "a/b")])
        self.assertEqual(found[1]["round"], "r1")

    def test_marks_saves_and_plays_within_a_request_become_comparisons(self):
        store = Store(self.folder)

        def sound(name, **changes):
            return store.save(Sound(name, Chain(), {"kind": "designed"}, id=name, heard=record(), **changes))

        sound("keeper", marks=["keeper"], played=True)
        sound("saved", saved={"slot": 1, "name": "x"}, played=True)
        sound("rejected", marks=["not this"], played=True)
        sound("passed", played=True)
        sound("unheard")
        sound("elsewhere", marks=["not this"], played=True)
        rounds = self.folder / "rounds"
        rounds.mkdir()
        (rounds / "r1.json").write_text(json.dumps({"id": "r1", "request": "Crunch", "candidates": [
            {"sound": "keeper"}, {"sound": "rejected"}, {"sound": "unheard"}]}), encoding="utf-8")
        (rounds / "r2.json").write_text(json.dumps({"id": "r2", "request": "crunch ", "previous": "r1", "candidates": [
            {"sound": "saved"}, {"sound": "passed"}, {"sound": "missing"}]}), encoding="utf-8")
        (rounds / "r3.json").write_text(json.dumps({"id": "r3", "request": "Clean", "candidates": [
            {"sound": "elsewhere"}]}), encoding="utf-8")
        taste.record_choice(self.folder, "keeper", "saved", "blind")
        found = {(pair["kept"], pair["over"]): (pair["how"], pair["weight"]) for pair in taste.comparisons(self.folder)}
        self.assertEqual(found, {
            ("keeper", "saved"): ("blind", 1.),
            # One keeper is one decision: its pairs share it (a not-this pair at twice a passed-over one).
            ("keeper", "rejected"): ("mark", .5), ("saved", "rejected"): ("mark", .5),
            ("keeper", "passed"): ("round", .25), ("saved", "passed"): ("round", .25),
            ("passed", "rejected"): ("round", .5)})

    def test_one_keeper_among_many_designs_counts_as_one_choice(self):
        store = Store(self.folder)
        rounds = self.folder / "rounds"
        rounds.mkdir()
        names = [f"s{index}" for index in range(9)]
        for index, name in enumerate(names):
            store.save(Sound(name, Chain(), {"kind": "designed"}, id=name, heard=record(brightness_oct=index / 10),
                             played=True, marks=["keeper"] if index == 0 else []))
        (rounds / "r1.json").write_text(json.dumps({"id": "r1", "request": "Crunch", "candidates": [
            {"sound": name} for name in names]}), encoding="utf-8")
        pairs = taste.comparisons(self.folder)
        self.assertEqual(len(pairs), 8)
        self.assertEqual(len({pair["decision"] for pair in pairs}), 1)
        self.assertAlmostEqual(sum(pair["weight"] for pair in pairs), .5)
        model = taste.fit(*taste.evidence(self.folder), minimum=2)
        self.assertFalse(model["enough"])
        self.assertEqual(model["n"], 1)

    def test_marks_outside_rounds_are_compared_only_between_related_sounds(self):
        store = Store(self.folder)
        for name, marks, parent in (("base", ["not this"], None), ("revision", ["keeper"], "base"),
                                    ("stranger", ["not this"], None)):
            store.save(Sound(name, Chain(), {"kind": "revised"}, id=name, marks=marks, parent=parent, heard=record()))
        pairs = [(pair["kept"], pair["over"]) for pair in taste.comparisons(self.folder)]
        self.assertEqual(pairs, [("revision", "base")])

    def test_a_pair_given_twice_counts_once_and_a_changed_mind_keeps_both(self):
        for kept, over in (("a", "b"), ("a", "b"), ("b", "a")):
            taste.record_choice(self.folder, kept, over, "a/b")
        pairs = sorted((pair["kept"], pair["over"]) for pair in taste.comparisons(self.folder, sounds=[]))
        self.assertEqual(pairs, [("a", "b"), ("b", "a")])


class FitTest(unittest.TestCase):
    def test_a_player_who_keeps_lower_tristimulus3_is_learned(self):
        sounds = synthetic(30, seed=1)
        model = taste.fit(player(sounds, 16, seed=1, prefers=lower_tristimulus3), rows(sounds))
        self.assertTrue(model["enough"])
        self.assertEqual(model["n"], 16)
        weights = {name: item["weight"] for name, item in model["features"].items()}
        self.assertLess(weights["tristimulus3"], 0)
        self.assertEqual(max(weights, key=lambda name: abs(weights[name])), "tristimulus3")
        self.assertEqual(model["features"]["tristimulus3"]["strength"], "strong")
        item = model["features"]["tristimulus3"]
        self.assertEqual(item["kept_higher"], 0)
        self.assertGreaterEqual(item["kept_lower"], 15)   # a pair within 5% of a standard deviation is not counted
        self.assertEqual(model["right"], 1.)
        lines = taste.describe(model)
        self.assertEqual(lines[0], f"Of {item['kept_lower']} choices where it differed, you kept the sound with a "
                                   "smaller share of harmonic energy from the 5th harmonic up (tristimulus 3) "
                                   f"{item['kept_lower']} times.")

    def test_an_inconsistent_player_is_still_learned_with_the_count_behind_it(self):
        sounds = synthetic(40, seed=2)
        model = taste.fit(player(sounds, 60, seed=2, prefers=lower_tristimulus3, consistent=.8), rows(sounds))
        item = model["features"]["tristimulus3"]
        self.assertLess(item["weight"], 0)
        self.assertIn(item["strength"], ("strong", "moderate"))
        self.assertGreater(item["kept_lower"], item["kept_higher"])
        self.assertIn("tristimulus 3", taste.describe(model)[0])

    def test_random_choices_claim_nothing_firm(self):
        learned = taste.fit(player(synthetic(30, 1), 30, 1, prefers=lower_tristimulus3), rows(synthetic(30, 1)))
        firm = abs(learned["features"]["tristimulus3"]["weight"])
        for seed in range(5):
            sounds = synthetic(30, seed=seed + 10)
            model = taste.fit(player(sounds, 30, seed=seed + 10), rows(sounds))
            for name, item in model["features"].items():
                self.assertNotIn(item["strength"], ("strong", "moderate"), (seed, name))
                self.assertLess(abs(item["weight"]), firm / 2, (seed, name))
            for line in taste.describe(model):
                self.assertTrue("weak evidence" in line or "none of the measured" in line, line)

    def test_below_the_minimum_nothing_is_claimed(self):
        sounds = synthetic(10, seed=3)
        model = taste.fit(player(sounds, 7, seed=3, prefers=lower_tristimulus3), rows(sounds))
        self.assertFalse(model["enough"])
        self.assertEqual(model["message"], taste.NOT_ENOUGH)
        self.assertEqual(model["features"], {})
        self.assertTrue(taste.describe(model)[0].startswith("Not enough evidence yet: 7 usable choices"))
        ranked = taste.rank(model, sounds[:3])
        self.assertEqual([item["candidate"] for item in ranked], sounds[:3])
        self.assertTrue(all(item["chance"] is None and item["top"] is None for item in ranked))

    def test_comparisons_of_unmeasured_sounds_are_not_used(self):
        sounds = synthetic(20, seed=4)
        pairs = player(sounds, 12, seed=4, prefers=lower_tristimulus3)
        measured = rows(sounds)
        for pair in pairs[:6]:
            measured.pop(pair["kept"], None)
        model = taste.fit(pairs, measured)
        self.assertLess(model["n"], 12)

    def test_identical_sounds_say_nothing(self):
        same = [Sound(f"S{i}", Chain(), {}, id=f"s{i}", heard=record()) for i in range(4)]
        pairs = [{"kept": f"s{i % 4}", "over": f"s{(i + 1) % 4}"} for i in range(10)]
        model = taste.fit(pairs, rows(same))
        self.assertFalse(model["enough"])
        self.assertEqual(taste.describe(model), ["Of 10 choices, nothing can be said yet: no measured quantity "
                                                 "differs between the sounds compared."])


class RankTest(unittest.TestCase):
    def test_candidates_are_ranked_by_the_learned_preference(self):
        sounds = synthetic(30, seed=5)
        model = taste.fit(player(sounds, 24, seed=5, prefers=lower_tristimulus3), rows(sounds))
        fresh = synthetic(5, seed=99)
        ranked = taste.rank(model, fresh)
        order = [item["candidate"] for item in ranked]
        self.assertEqual(order[0], min(fresh, key=lambda sound: sound.heard["probe"]["tristimulus3"]))
        chances = [item["chance"] for item in ranked]
        self.assertEqual(chances, sorted(chances, reverse=True))
        self.assertGreater(chances[0], .5)
        self.assertAlmostEqual(sum(item["top"] for item in ranked), 1., places=6)
        self.assertGreater(ranked[0]["top"], .5)
        records = taste.rank(model, [sound.heard for sound in fresh])
        self.assertEqual([item["candidate"] for item in records], [sound.heard for sound in order])


class KeepingTest(unittest.TestCase):
    def test_learn_reads_the_data_folder_and_keeps_the_model_there(self):
        folder = Path(tempfile.mkdtemp())
        store = Store(folder)
        sounds = synthetic(20, seed=6)
        for sound in sounds:
            store.save(sound)
        for pair in player(sounds, 12, seed=6, prefers=lower_tristimulus3):
            taste.record_choice(folder, pair["kept"], pair["over"], "blind")
        model = taste.learn(folder)
        self.assertTrue(model["enough"])
        self.assertTrue((folder / taste.MODEL).exists())
        self.assertEqual(taste.load(folder), json.loads(json.dumps(model)))
        self.assertLess(taste.load(folder)["features"]["tristimulus3"]["weight"], 0)

    def test_no_model_kept_reads_as_none(self):
        folder = Path(tempfile.mkdtemp())
        self.assertIsNone(taste.load(folder))
        (folder / taste.MODEL).write_text("{", encoding="utf-8")
        self.assertIsNone(taste.load(folder))


if __name__ == "__main__":
    unittest.main()
