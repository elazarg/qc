"""The two workbenches: versions of a sound compared blind, and every knob with the versions of a sound.

The old tests of the workbenches (test_iterate, test_draft_forms) checked how the old app copied presets
into working slots and built drafts on them. On the new core that is `lab.qc.rig`, `lab.qc.slots` and
`lab.qc.unit`, tested in test_lab_*; what is ported here is what the workbenches did with it: a request at two
strengths next to the original, scenes, a sweep, a level-matched blind comparison, a kept preference."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from lab.sound import knobs
from lab.sound.chain import Chain, Row
from lab.hearing.measure import measure
from lab.sound.sounds import Sound, Store
from lab.tools import controls, workbench

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lab_tools_support import HIWATT, PLEXI, V30, chain, host, phrase  # noqa: E402


def scenes_chain():
    base = chain(PLEXI, V30)
    return Chain(base.rows, ("Rhythm", "Lead"))


class VersionsTest(unittest.TestCase):
    def test_a_request_is_rendered_at_two_strengths_next_to_the_original(self):
        sound = Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"})
        versions = workbench.strength_versions(sound, "darker")
        self.assertEqual([version["name"] for version in versions], ["As it is", "darker · 40%", "darker · 85%"])
        self.assertEqual(versions[0]["chain"], sound.chain)
        digests = {version["chain"].digest() for version in versions}
        self.assertEqual(len(digests), 3)
        softer, stronger = versions[1]["knobs"]["brightness"], versions[2]["knobs"]["brightness"]
        self.assertLess(abs(softer), abs(stronger))
        self.assertEqual(sound.knobs, {})      # the sound itself is left as it was
        with self.assertRaisesRegex(ValueError, "nothing that answers"):
            workbench.strength_versions(Sound("Empty", Chain(), {}), "darker")

    def test_scenes_and_a_sweep_make_one_version_each(self):
        sound = Sound("Two scenes", scenes_chain(), {"kind": "designed"})
        self.assertEqual([version["scene"] for version in workbench.scene_versions(sound)], [0, 1])
        names = [name for name, *_ in workbench.controls(sound.chain)]
        self.assertTrue(any(name.endswith("GAIN") or "GAIN" in name for name in names))
        label, row, column, index = next(item for item in workbench.controls(sound.chain) if "GAIN" in item[0])
        versions = workbench.sweep_versions(sound, row, column, index, [2., 8.])
        values = [version["chain"].block(row, column).display(index) for version in versions]
        self.assertAlmostEqual(values[0], 2., places=2)
        self.assertAlmostEqual(values[1], 8., places=2)
        with self.assertRaisesRegex(ValueError, "goes from"):
            workbench.sweep_versions(sound, row, column, index, [99.])
        with self.assertRaisesRegex(ValueError, "holds no block"):
            workbench.with_control(sound.chain, 3, 0, 0, 1.)
        with self.assertRaisesRegex(ValueError, "no control"):
            workbench.with_control(sound.chain, row, column, 999, 1.)

    def test_scene_a_kept_from_a_comparison_is_tagged_as_scene_a(self):
        # Regression: scene A is scene 0, which was taken for "no scene".
        sound = Sound("Two scenes", scenes_chain(), {"kind": "designed"})
        record = {"auditions": {"A": {"name": "Scene A", "scene": 0, "chain": sound.chain.to_record()}}}
        self.assertEqual(workbench.as_sound(record, "A", sound).tags, ["scene A"])

    def test_a_comparison_that_cannot_be_read_is_left_out_of_the_earlier_ones(self):
        # Regression: the list skipped it but the paths behind the list did not, so a row opened the wrong one.
        quiet, loud = phrase(level=.05), phrase(level=.4)
        with TemporaryDirectory() as folder:
            target, _ = workbench.comparison(folder, [{"name": "Quiet"}, {"name": "Loud"}], [quiet, loud], kind="WAV files")
            bad = Path(folder) / "comparisons" / "99999999-000000-bad"
            bad.mkdir()
            (bad / "comparison.json").write_text("{not json", encoding="utf-8")
            found = workbench.earlier(folder)
            self.assertEqual([path for path, _ in found], [target])
            self.assertEqual(set(workbench.auditions(*found[0])), {"A", "B"})

    def test_a_comparison_is_level_matched_blind_and_keeps_its_choice_and_notes(self):
        quiet, loud = phrase(level=.05), phrase(level=.4)
        sound = Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"})
        versions = [{"name": "Quiet", "chain": sound.chain, "scene": None},
                    {"name": "Loud", "chain": knobs.apply(sound.chain, {"drive": .5}), "scene": None,
                     "knobs": {"drive": .5}}]
        with TemporaryDirectory() as folder:
            store = Store(folder)
            store.save(sound)
            target, record = workbench.comparison(folder, versions, [quiet, loud], sound=sound, phrase_name="p",
                                                  kind="Strengths", seed=3)
            from lab.hearing.audio import read_wave

            loudness = [measure(read_wave(target / row["file"])).lufs for row in record["auditions"].values()]
            self.assertLess(max(loudness) - min(loudness), .2)
            letter = next(letter for letter, row in record["auditions"].items() if row["name"] == "Loud")
            with self.assertRaisesRegex(ValueError, "Play"):
                workbench.keep(target, "Z")
            kept = workbench.keep(target, letter, "more bite")
            self.assertEqual(workbench.load(target)["choice"], kept["choice"])
            note = workbench.feedback(target, "the loud one is harsh", audition=letter)
            self.assertEqual(json.loads(note.read_text(encoding="utf-8"))["audition_name"], "Loud")
            made = workbench.as_sound(kept, letter, sound)
            self.assertEqual(made.parent, sound.id)
            self.assertEqual(made.chain.digest(), versions[1]["chain"].digest())
            with self.assertRaisesRegex(ValueError, "at least two"):
                workbench.comparison(folder, versions[:1], [quiet])


class ControlsTest(unittest.TestCase):
    def test_the_versions_of_a_sound_run_from_the_first_to_the_newest(self):
        with TemporaryDirectory() as folder:
            store = Store(folder)
            first = store.save(Sound("First", chain(PLEXI, V30), {"kind": "designed"}, created="2026-01-01"))
            second = store.save(Sound("Second", chain(HIWATT, V30), {"kind": "revised"}, parent=first.id,
                                      created="2026-01-02"))
            third = store.save(Sound("Third", chain(HIWATT), {"kind": "revised"}, parent=second.id,
                                     created="2026-01-03"))
            self.assertEqual([sound.name for sound in controls.lineage(store, second)], ["First", "Second", "Third"])
            self.assertEqual([sound.name for sound in controls.lineage(store, third)], ["First", "Second", "Third"])

    def test_a_knob_says_which_controls_it_moves(self):
        names = controls.reach(chain(PLEXI, V30), "drive", 0.)
        self.assertTrue(names)
        self.assertTrue(all(isinstance(name, str) for name in names))


class OnTheUnitTest(unittest.TestCase):
    def test_versions_render_silently_and_the_unit_is_read_back(self):
        sound = Sound("Two scenes", scenes_chain(), {"kind": "designed"})
        versions = workbench.strength_versions(sound, "more drive") if workbench.request_moves(
            "more drive", sound.chain) else workbench.strength_versions(sound, "darker")
        with TemporaryDirectory() as folder:
            unit = host(folder)
            try:
                audios = unit.call("lab.tools.common.render_many",
                                   [(version["chain"], version["scene"]) for version in versions],
                                   phrase()).result(60)
                self.assertEqual(len(audios), 3)
                target, record = workbench.comparison(folder, versions, audios, sound=sound, kind="Strengths")
                self.assertEqual(len(record["auditions"]), 3)
                scenes = unit.call("lab.tools.common.render_many",
                                   [(version["chain"], version["scene"]) for version in workbench.scene_versions(sound)],
                                   phrase()).result(60)
                self.assertEqual(len(scenes), 2)
                held = unit.call("lab.tools.controls.held").result(10)
                self.assertTrue(any("Row 1" in line for line in held["lines"]))
                json.dumps(held["position"])
            finally:
                unit.close()


if __name__ == "__main__":
    unittest.main()
