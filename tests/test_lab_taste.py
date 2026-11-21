"""What the player's decisions say, from synthetic sounds and synthetic choices only."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from lab.player import taste
from lab.sound.chain import Chain
from lab.sound.sounds import Sound, Store

TOP = ("compression", "brightness_db", "brightness_oct")


def record(**values):
    """A heard record shaped like lab.hearing.measure.Heard.to_record, with a probe record."""
    top = {key: values.pop(key) for key in list(values) if key in TOP}
    return {"compression": .3, "crest_db": 12., "brightness_oct": 0., "brightness_db": 0., "low_end_db": 0.,
            "tail_db": -50., "level_db": -20., **top,
            "probe": {"version": 2, "distortion_db": -30., "distortion_soft_db": -40., "low_order_db": -32.,
                      "high_order_db": -36., "late_energy_db": -40., **values},
            "distortion": "edge of breakup", "compressed": "light", "brightness": "balanced", "tail": "dry"}


def sound(index, heard):
    return Sound(f"Sound {index}", Chain(), {"kind": "designed"}, id=f"s{index:02d}", heard=heard)


def synthetic(count, seed):
    """Sounds whose measured quantities are independent random numbers."""
    rng = np.random.default_rng(seed)
    return [sound(i, record(distortion_db=float(rng.uniform(-60, -15)), low_order_db=float(rng.uniform(-50, -20)),
                            high_order_db=float(rng.uniform(-50, -20)), compression=float(rng.uniform(0, 1)),
                            brightness_db=float(rng.uniform(-8, 8)), late_energy_db=float(rng.uniform(-40, 0))))
            for i in range(count)]


def realistic(count, seed):
    """Sounds as amps make them: distortion, compression, the order balance and the brightness all follow one
    hidden gain, as they do on the ladder measured on the unit."""
    rng = np.random.default_rng(seed)
    sounds = []
    for i in range(count):
        gain = rng.uniform(0, 1)
        distortion = -60 + 45 * gain + rng.normal(0, 3)
        low = distortion - 2 + rng.normal(0, 1.5)
        sounds.append(sound(i, record(
            distortion_db=float(distortion), distortion_soft_db=float(distortion - 20 * (1 - gain)),
            low_order_db=float(low), high_order_db=float(low - 6 + 10 * gain + rng.normal(0, 1.5)),
            compression=float(np.clip(.9 * gain + rng.normal(0, .1), 0, 1)),
            brightness_db=float(6 * gain - 3 + rng.normal(0, 2)), late_energy_db=float(rng.normal(-25, 10)))))
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
    return {item.id: taste.features(item.heard) for item in sounds}


def less_distortion(item):
    return -item.heard["probe"]["distortion_db"]


class FeaturesTest(unittest.TestCase):
    def test_features_are_named_for_the_quantity_and_skip_what_was_not_measured(self):
        heard = record(distortion_db=-22., hnr_db=31., echo_ms=None, harmonics_db=[-20., -30.], gated=True,
                       compression=.4, brightness_db=None)
        found = taste.features(heard)
        self.assertEqual(found["distortion_db"], -22.)
        self.assertEqual(found["compression"], .4)
        self.assertEqual(found["late_energy_db"], -40.)
        for absent in ("level_db", "gated", "harmonics_db", "echo_ms", "distortion", "brightness_db", "hnr_db"):
            self.assertNotIn(absent, found)
        self.assertTrue(all(isinstance(value, float) for value in found.values()))

    def test_nothing_measured_gives_no_features(self):
        self.assertEqual(taste.features(None), {})
        self.assertEqual(taste.features({}), {})
        self.assertEqual(taste.features({"compression": float("nan"), "probe": None}), {})

    def test_a_quantity_found_in_the_other_place_still_counts(self):
        self.assertEqual(taste.features({"probe": {"compression": .5}})["compression"], .5)

    def test_five_axes_each_with_a_phrase_for_more_and_less(self):
        self.assertEqual(list(taste.AXES), ["distortion", "order balance", "compression", "brightness", "tail"])
        for name, (read, least, more, less) in taste.AXES.items():
            self.assertNotEqual(more, less, name)
            self.assertGreater(least, 0, name)
        found = taste.axes(taste.features(record(distortion_db=-20., low_order_db=-25., high_order_db=-21.,
                                                 compression=.6, brightness_db=2., late_energy_db=-90.)))
        self.assertEqual(found, {"distortion": -20., "order balance": 4., "compression": .6, "brightness": 2.,
                                 "tail": taste.TAIL_FLOOR_DB})          # clipped: no tail is no tail

    def test_a_record_made_before_the_new_measures_gives_the_axes_it_can(self):
        old = {"compression": .5, "brightness_oct": .3, "tail_db": -180., "level_db": -20.,
               "probe": {"distortion_db": -18., "distortion_soft_db": -39., "decay_s": 6.0, "even_odd_db": 116.4}}
        self.assertEqual(taste.axes(taste.features(old)), {"distortion": -18., "compression": .5})
        floor = record(low_order_db=-70., high_order_db=-70.)           # no harmonics: no balance to speak of
        self.assertNotIn("order balance", taste.axes(taste.features(floor)))


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_choices_are_appended_and_read_back(self):
        taste.record_choice(self.folder, "a", "b", "blind")
        taste.record_choice(self.folder, "c", "a", "a/b", round_id="r1", event="not this")
        with (self.folder / taste.CHOICES).open("a", encoding="utf-8") as file:
            file.write("not json\n")
        self.assertIsNone(taste.record_choice(self.folder, "a", "a", "blind"))
        with self.assertRaises(ValueError):
            taste.record_choice(self.folder, "a", "b", "guessed")
        with self.assertRaises(ValueError):
            taste.record_choice(self.folder, "a", "b", "a/b", event="liked")
        found = taste.choices(self.folder)
        self.assertEqual([(entry["kept"], entry["over"], entry["how"]) for entry in found],
                         [("a", "b", "blind"), ("c", "a", "a/b")])
        self.assertEqual((found[1]["round"], found[1]["event"]), ("r1", "not this"))

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
            store.save(Sound(name, Chain(), {"kind": "designed"}, id=name, heard=record(brightness_db=float(index)),
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

    def press(self, store, number, others=1):
        """What the desk does when Keep is pressed on A with B on show, in a round of 1 + `others` played designs:
        the mark is saved on the sound and the choice is appended with its audition."""
        names = [f"r{number}a"] + [f"r{number}{'bcdef'[i]}" for i in range(others)]
        for index, name in enumerate(names):
            store.save(Sound(name, Chain(), {"kind": "designed"}, id=name, played=True,
                             heard={**record(brightness_db=float(index)), "phrase": "p1"}))
        (self.folder / "rounds").mkdir(exist_ok=True)
        (self.folder / "rounds" / f"r{number}.json").write_text(json.dumps(
            {"id": f"r{number}", "request": f"request {number}", "candidates": [{"sound": name} for name in names]}),
            encoding="utf-8")
        kept = store.sound(names[0])
        kept.marks = ["keeper"]
        store.save(kept)
        taste.record_choice(self.folder, names[0], names[1], "a/b", round_id=f"r{number}", event="keeper",
                            context={"phrase": "p1", "scene": 0, "knobs": {names[0]: {}, names[1]: {}}})

    def test_one_press_of_keep_is_one_decision(self):
        # A1: the press recorded a choice, and the mark it set was read back from the round as the same pair
        # again, under another key: four presses made eight decisions, enough for the model to speak.
        store = Store(self.folder)
        for number in range(4):
            self.press(store, number, others=1 + number % 2)        # two or three designs played in the round
        pairs, measured = taste.evidence(self.folder)
        self.assertEqual(len({pair["decision"] for pair in pairs}), 4)
        self.assertEqual(sum(1 for pair in pairs if pair["over"].startswith(pair["kept"][:2] + "b")), 4)
        model = taste.fit(pairs, measured)
        self.assertEqual((model["enough"], model["n"]), (False, 4))
        self.assertTrue(taste.describe(model)[0].startswith("Not enough evidence yet: 4 usable decisions"))

    def test_a_not_this_and_what_it_implies_are_one_decision_too(self):
        store = Store(self.folder)
        for name in "abc":
            store.save(Sound(name, Chain(), {"kind": "designed"}, id=name, played=True, heard=record(),
                             marks=["not this"] if name == "b" else []))
        (self.folder / "rounds").mkdir()
        (self.folder / "rounds" / "r.json").write_text(json.dumps(
            {"id": "r", "request": "x", "candidates": [{"sound": name} for name in "abc"]}), encoding="utf-8")
        taste.record_choice(self.folder, "a", "b", "a/b", event="not this")
        pairs = taste.comparisons(self.folder)
        self.assertEqual(sorted((pair["kept"], pair["over"]) for pair in pairs), [("a", "b"), ("c", "b")])
        self.assertEqual(len({pair["decision"] for pair in pairs}), 1)

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

    def test_the_same_blind_pick_made_again_is_another_decision(self):
        # Picking the same sound again without knowing which it is, is new evidence; five picks collapsed to one.
        for _ in range(5):
            taste.record_choice(self.folder, "a", "b", "blind", context={"phrase": "p1", "scene": 0})
        pairs = taste.comparisons(self.folder, sounds=[])
        self.assertEqual(len(pairs), 5)
        self.assertEqual(len({pair["decision"] for pair in pairs}), 5)


class FitTest(unittest.TestCase):
    def test_the_sign_test_is_exact(self):
        self.assertAlmostEqual(taste.sign_test(8, 0), 2 / 256)
        self.assertAlmostEqual(taste.sign_test(7, 1), 18 / 256)
        self.assertAlmostEqual(taste.sign_test(8, 0, tests=5), 10 / 256)        # moderate: eight unanimous decisions
        self.assertLess(taste.sign_test(10, 0, tests=5), .01)                    # strong takes ten
        self.assertEqual(taste.sign_test(4, 4, tests=5), 1.)
        self.assertEqual(taste.sign_test(0, 0), 1.)

    def test_a_player_who_keeps_less_distortion_is_found_with_the_right_sign(self):
        sounds = synthetic(30, seed=1)
        model = taste.fit(player(sounds, 16, seed=1, prefers=less_distortion), rows(sounds))
        self.assertTrue(model["enough"])
        self.assertEqual(model["n"], 16)
        item = model["axes"]["distortion"]
        self.assertEqual((item["lean"], item["kept_higher"], item["strength"]), (-1, 0, "strong"))
        self.assertGreaterEqual(item["kept_lower"], 14)      # a pair under 2 dB apart is not counted as differing
        lines = taste.describe(model)
        self.assertEqual(lines[0], f"In {item['kept_lower']} of {item['n']} decisions where it differed, the sound "
                                   "you preferred had less harmonic distortion of a firmly played tone.")
        self.assertEqual(lines[-1], taste.DECISION)

    def test_sentences_speak_of_the_sound_preferred_since_a_not_this_keeps_nothing(self):
        # B4: "you kept the sound with ..." was also written when the press was Not this on the other tile.
        sounds = synthetic(30, seed=1)
        lines = taste.describe(taste.fit(player(sounds, 16, seed=1, prefers=less_distortion), rows(sounds)))
        self.assertFalse(any("you kept" in line for line in lines))
        self.assertIn("one Keep or Not this with two sounds on show", lines[-1])

    def test_an_inconsistent_player_is_still_found_with_the_count_behind_it(self):
        sounds = synthetic(40, seed=2)
        model = taste.fit(player(sounds, 60, seed=2, prefers=less_distortion, consistent=.8), rows(sounds))
        item = model["axes"]["distortion"]
        self.assertEqual(item["lean"], -1)
        self.assertIn(item["strength"], ("strong", "moderate"))
        self.assertGreater(item["kept_lower"], item["kept_higher"])
        self.assertIn("less harmonic distortion", taste.describe(model)[0])

    def test_random_choices_seldom_claim_anything_firm(self):
        # A2: with a bootstrap's z for a p-value, random choices between sounds whose measures move together gave
        # a "strong" quantity in 78 % of fits at 8 decisions, 55 % at 12 and 45 % at 20.
        for decisions in (8, 12, 20):
            strong = firm = 0
            for seed in range(200):
                sounds = realistic(30, seed=1000 * decisions + seed)
                model = taste.fit(player(sounds, decisions, seed=seed), rows(sounds))
                strengths = {item["strength"] for item in model["axes"].values()}
                strong += "strong" in strengths
                firm += bool(strengths & {"strong", "moderate"})
            self.assertLessEqual(strong / 200, .05, decisions)
            self.assertLessEqual(firm / 200, .08, decisions)

    def test_a_handful_of_independent_axes_claims_nothing_firm_either(self):
        firm = 0
        for seed in range(200):
            sounds = synthetic(30, seed=seed + 10)
            model = taste.fit(player(sounds, 30, seed=seed + 10), rows(sounds))
            firm += any(item["strength"] in ("strong", "moderate") for item in model["axes"].values())
        self.assertLessEqual(firm / 200, .08)

    def test_axes_that_went_together_are_said_to_have(self):
        # Keeping the less distorted sound is also keeping the less compressed one when both follow the gain: the
        # decisions cannot tell which, and the sentences say so instead of naming one.
        sounds = realistic(30, seed=7)
        model = taste.fit(player(sounds, 30, seed=7, prefers=less_distortion), rows(sounds))
        claimed = {name for name, item in model["axes"].items() if item["strength"] != "none"}
        self.assertTrue({"distortion", "compression"} <= claimed)
        self.assertEqual(model["axes"]["compression"]["lean"], -1)
        self.assertTrue(any(set(item["axes"]) == {"distortion", "compression"} for item in model["together"]))
        lines = taste.describe(model)
        self.assertTrue(any(line.startswith("Distortion and compression went together") and
                            "cannot tell which" in line for line in lines), lines)

    def test_below_the_minimum_nothing_is_claimed(self):
        sounds = synthetic(10, seed=3)
        model = taste.fit(player(sounds, 7, seed=3, prefers=less_distortion), rows(sounds))
        self.assertFalse(model["enough"])
        self.assertEqual(model["message"], taste.NOT_ENOUGH)
        self.assertEqual(model["axes"], {})
        self.assertTrue(taste.describe(model)[0].startswith("Not enough evidence yet: 7 usable decisions"))

    def test_eight_unanimous_decisions_are_moderate_evidence_and_no_more(self):
        sounds = synthetic(30, seed=1)
        model = taste.fit(player(sounds, 8, seed=5, prefers=less_distortion), rows(sounds))
        item = model["axes"]["distortion"]
        if item["n"] == 8:
            self.assertEqual(item["strength"], "moderate")
            self.assertIn("(moderate evidence)", taste.describe(model)[0])
        self.assertNotEqual(item["strength"], "strong")

    def test_comparisons_of_unmeasured_sounds_are_not_used(self):
        sounds = synthetic(20, seed=4)
        pairs = player(sounds, 12, seed=4, prefers=less_distortion)
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
        self.assertEqual(taste.describe(model), ["Of 10 decisions, nothing can be said yet: no measured quantity "
                                                 "differs between the sounds compared."])

    def test_sounds_measured_before_the_new_measures_are_still_compared_on_what_they_have(self):
        old = [Sound(f"S{i}", Chain(), {}, id=f"s{i}",
                     heard={"compression": .1 * i, "brightness_oct": .2 * i, "tail_db": -180. + i, "level_db": -20.,
                            "probe": {"distortion_db": -60. + 4 * i, "distortion_soft_db": -70., "decay_s": 6.0,
                                      "even_odd_db": 116.4, "echo_ms": None}}) for i in range(10)]
        pairs = [{"kept": f"s{i}", "over": f"s{i + 1}"} for i in range(9)]
        model = taste.fit(pairs, rows(old))
        self.assertTrue(model["enough"])
        self.assertEqual((model["axes"]["distortion"]["kept_lower"], model["axes"]["compression"]["kept_lower"]), (9, 9))
        self.assertEqual((model["axes"]["brightness"]["n"], model["axes"]["tail"]["n"]), (0, 0))
        json.dumps(model)
        self.assertTrue(taste.describe(model))


class KeepingTest(unittest.TestCase):
    def test_learn_reads_the_data_folder_and_keeps_the_model_there(self):
        folder = Path(tempfile.mkdtemp())
        store = Store(folder)
        sounds = synthetic(20, seed=6)
        for item in sounds:
            store.save(item)
        for pair in player(sounds, 12, seed=6, prefers=less_distortion):
            taste.record_choice(folder, pair["kept"], pair["over"], "blind")
        model = taste.learn(folder)
        self.assertTrue(model["enough"])
        self.assertTrue((folder / taste.MODEL).exists())
        self.assertEqual(taste.load(folder), json.loads(json.dumps(model)))
        self.assertEqual(taste.load(folder)["axes"]["distortion"]["lean"], -1)
        self.assertEqual([path.name for path in folder.glob("taste*")], [taste.MODEL])

    def test_a_model_kept_by_the_earlier_method_is_fitted_again_not_shown(self):
        folder = Path(tempfile.mkdtemp())
        Store(folder)
        earlier = {"version": 1, "enough": True, "n": 8, "features": {"brightness_oct": {"strength": "strong",
                   "weight": -1.2, "kept_higher": 0, "kept_lower": 8}}, "samples": [], "sample_names": []}
        (folder / taste.MODEL).write_text(json.dumps(earlier), encoding="utf-8")
        found = taste.load(folder)
        self.assertEqual((found["version"], found["enough"]), (taste.VERSION, False))
        self.assertTrue(taste.describe(found)[0].startswith("Not enough evidence yet: 0 usable decisions"))
        self.assertTrue(taste.describe(earlier)[0].startswith("Not enough evidence yet"))    # never read as it was

    def test_no_model_kept_reads_as_none(self):
        folder = Path(tempfile.mkdtemp())
        self.assertIsNone(taste.load(folder))
        (folder / taste.MODEL).write_text("{", encoding="utf-8")
        self.assertIsNone(taste.load(folder))


if __name__ == "__main__":
    unittest.main()
