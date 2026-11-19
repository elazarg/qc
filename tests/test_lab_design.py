"""Asking for sounds: the cards, the checks on an answer, rounds and revisions, and the ledger of calls."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from lab import catalog, design
from lab.assistant import Assistant, Refused
from lab.chain import Block, Chain, Row, differences
from lab.sounds import Sound, describe

RECTIFIER, PLEXI, V30, GREENBACK, FUZZ, HIWATT, DELAY = 1018, 1081, 12001, 12006, 22, 1063, 6011
CHARVEL = {"name": "Charvel", "kind": "electric", "input": 1, "about": "DK24"}


def block(model_id, column, **settings):
    return {"column": column, "model_id": model_id,
            "settings": [{"name": name.upper().replace("_", " "), "value": value} for name, value in settings.items()],
            "scene_settings": [], "off_in_scenes": []}


def answer(name, blocks, *, second=None, split=(-1, -1), scenes=(), differs="", blend=(0, 0)):
    rows = [{"row": 0, "split_at": split[0], "join_at": split[1], "blocks": blocks}]
    if second:
        rows.append({"row": 1, "split_at": -1, "join_at": -1, "blocks": second})
    return {"name": name, "target": "t", "explanation": "e", "differs": differs, "scenes": list(scenes), "rows": rows,
            "blend": {"level_db": blend[0], "spread": blend[1]}, "sources": []}


def reply(*designs):
    return {"aim": {"drive": "saturated", "tone": "balanced", "space": "ambient", "note": "A singing fuzz lead."},
            "designs": list(designs)}


class Scripted:
    """Stands in for the assistant: hands back prepared answers in order and keeps what it was asked."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def __call__(self, prompt, schema, *, web, seconds, cancel):
        self.asked.append((prompt, schema, web))
        return self.answers.pop(0)


class DesignTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.models = design.offered("Gilmour Time solo fuzz", "electric")

    def assistant(self, *answers):
        script = Scripted(*answers)
        return Assistant(self.folder, run=script), script

    def test_cards_say_what_a_model_is_what_it_costs_and_how_it_drives(self):
        found = design.card(HIWATT)
        self.assertEqual(found["controls"]["GAIN"], [0, 10])
        self.assertNotIn("OUTPUT", found["controls"])   # The amp's clean level is the app's.
        self.assertIn("measured_drive", found)
        self.assertIn("cpu", found)
        self.assertIn("about", found)
        self.assertEqual(design.card(FUZZ)["controls"]["FUZZ"], [0, 10])
        self.assertIn(FUZZ, self.models)
        self.assertNotIn(1180, design.offered("Petrucci lead", "electric"))  # A plugin the unit does not run.

    def test_an_answer_becomes_a_chain_that_states_only_what_it_sets(self):
        chain, problems = design.to_chain(answer("Fuzz", [block(FUZZ, 0, fuzz=9), block(HIWATT, 1, gain=7),
                                                         block(V30, 2, position=5)]), offered_ids=set(self.models))
        self.assertEqual(problems, [])
        self.assertEqual(chain.rows[0].route, (1, design.MULTI_OUT))
        cab = chain.block(0, 2)
        self.assertEqual(len([i for i, _ in cab.params]), 2)  # Both microphones' POSITION, nothing else.
        self.assertAlmostEqual(cab.display(cab.params[0][0]), 5., places=2)
        self.assertTrue(describe(chain)[0].startswith("Row 1: Facial Fuzz (FUZZ 9) → Watt D103 Normal (GAIN 7)"))

    def test_what_is_wrong_with_an_answer_is_said(self):
        bad = answer("Bad", [block(HIWATT, 0, gain=14), block(9999, 1), block(V30, 0), block(PLEXI, 3, sparkle=2)])
        _, problems = design.to_chain(bad, offered_ids=set(self.models))
        self.assertTrue(any("GAIN 14 is outside 0 to 10" in p for p in problems))
        self.assertTrue(any("9999 is not one of the available" in p for p in problems))
        self.assertTrue(any("two blocks in column 0" in p for p in problems))
        self.assertTrue(any("no control 'SPARKLE'" in p for p in problems))
        heavy = answer("Heavy", [block(RECTIFIER, 0), block(RECTIFIER, 1), block(RECTIFIER, 2), block(V30, 3)])
        _, problems = design.to_chain(heavy, offered_ids=set(self.models) | {RECTIFIER})
        self.assertTrue(any("does not fit" in p for p in problems))

    def test_two_paths_and_scenes(self):
        lead = block(RECTIFIER, 1, gain=4)
        lead["scene_settings"] = [{"scene": 1, "name": "GAIN", "value": 6.5}]
        delay = block(DELAY, 4, mix=20)
        delay["off_in_scenes"] = [0]
        chain, problems = design.to_chain(
            answer("Two", [lead, block(V30, 2), delay], second=[block(PLEXI, 1), block(GREENBACK, 2)], split=(1, 3),
                   scenes=("Rhythm", "Lead"), blend=(-3, 60)), offered_ids=set(self.models) | {RECTIFIER, PLEXI})
        self.assertEqual(problems, [])
        self.assertEqual(chain.rows[0].split, (1, 3))
        self.assertEqual(chain.labels[:2], ("Rhythm", "Lead"))
        self.assertAlmostEqual(catalog.to_display(RECTIFIER, 0, chain.block(0, 1).get(0, 1)), 6.5, places=1)
        self.assertEqual(chain.block(0, 4).bypass[:2], (True, False))
        mixer = dict(chain.rows[0].mixer)
        self.assertAlmostEqual(catalog.to_display(catalog.MIXER, 2, mixer[2]), -3., places=1)
        self.assertEqual(mixer[4], 1.)   # A Rectifier against a Plexi: measured to oppose, so the phase is turned.
        _, problems = design.to_chain(answer("No cab", [block(RECTIFIER, 1), block(V30, 2)],
                                             second=[block(PLEXI, 1)], split=(1, 3)),
                                      offered_ids=set(self.models) | {RECTIFIER, PLEXI})
        self.assertIn("The amp on the second path needs its own cab.", problems)

    def test_a_round_is_checked_and_a_bad_answer_is_repaired_once(self):
        good = answer("Fuzz", [block(FUZZ, 0, fuzz=10), block(HIWATT, 1), block(V30, 2)], differs="Documented")
        other = answer("Plexi", [block(PLEXI, 0, gain=8), block(GREENBACK, 1)], differs="Plexi")
        broken = answer("Bad", [block(HIWATT, 0, gain=99)])
        assistant, script = self.assistant(reply(broken, good, good), reply(good, other, broken))
        aim, designs = design.round_of_three(assistant, "Gilmour Time solo", CHARVEL)
        self.assertEqual(aim["drive"], "saturated")
        self.assertEqual([sound.name for sound, _ in designs], ["Fuzz", "Plexi"])
        self.assertEqual(len(script.asked), 2)
        self.assertIn("could not be used", script.asked[1][0])
        self.assertIn("GAIN 99 is outside", script.asked[1][0])
        self.assertIn("same chain as an earlier one", script.asked[1][0])
        schema = script.asked[0][1]
        self.assertIn(FUZZ, schema["properties"]["designs"]["items"]["properties"]["rows"]["items"]["properties"]
                      ["blocks"]["items"]["properties"]["model_id"]["enum"])
        ledger = [json.loads(line) for line in (self.folder / "ledger.jsonl").read_text().splitlines()]
        self.assertEqual([entry["kind"] for entry in ledger], ["round", "round repair"])
        self.assertEqual({entry["by"] for entry in ledger}, {"player"})

    def test_a_saved_preset_is_revised_with_everything_else_kept(self):
        rows = (Row((Block(FUZZ).set_display(0, 6), Block(HIWATT), Block(V30)), inlet=1, outlet=16),
                Row(), Row((Block(DELAY),), inlet=0, outlet=19), Row())
        saved = Sound("Pink Floyd Time", Chain(rows), {"kind": "preset"})
        form = design.as_form(saved.chain)
        changed = {**form, "name": "x", "target": "t", "explanation": "e", "differs": "", "blend": {"level_db": 0, "spread": 0},
                   "sources": []}
        changed["rows"][0]["blocks"][0]["settings"] = [{"name": "FUZZ", "value": 10}]
        assistant, script = self.assistant(changed)
        revised = design.revise(assistant, saved, "more fuzz", CHARVEL)
        self.assertIn("current_chain", script.asked[0][0])
        self.assertEqual(revised.parent, saved.id)
        self.assertEqual(revised.chain.rows[2], saved.chain.rows[2])     # Untouched rows are kept as they were.
        self.assertEqual(revised.chain.rows[0].route, (1, 16))           # So is the routing.
        self.assertAlmostEqual(revised.chain.block(0, 0).display(0), 10., places=1)
        self.assertEqual(differences(revised.chain.with_block(0, 0, saved.chain.block(0, 0)), saved.chain), [])

    def test_no_call_is_made_that_nobody_stands_behind(self):
        assistant, script = self.assistant()
        assistant.automatic = False
        with self.assertRaises(Refused):
            assistant.ask("round", "x", {}, by="app")
        with self.assertRaises(ValueError):
            assistant.ask("round", "x", {}, by="nobody")
        self.assertEqual(script.asked, [])


    def test_an_unchanged_answer_reproduces_a_saved_preset_exactly(self):
        # Values the assistant never sees (an amp's OUTPUT, microphone names, per-scene switching) are kept.
        hiwatt = Block(HIWATT).set_display(catalog.model(HIWATT).index("OUTPUT"), 5.4).set(2, "ML_mic")
        fuzz = Block(FUZZ, bypass=(True, True, False) + (True,) * 5)
        saved = Chain((Row((fuzz, hiwatt, Block(V30)), inlet=1, outlet=16, split=(5, 5)), Row((Block(HIWATT),))))
        form = design.as_form(saved)
        self.assertEqual(form["rows"][0]["blocks"][0]["off_in_scenes"], [0, 1, 3, 4, 5, 6, 7])
        answer_ = {**form, "name": "x", "target": "t", "explanation": "e", "differs": "",
                   "blend": {"level_db": 0, "spread": 0}, "sources": []}
        chain, problems = design.to_chain(answer_, offered_ids={FUZZ, HIWATT, V30}, base=saved)
        self.assertEqual(problems, [])
        self.assertEqual(differences(chain, saved), [])


if __name__ == "__main__":
    unittest.main()
