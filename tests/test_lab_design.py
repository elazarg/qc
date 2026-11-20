"""Asking for sounds: the cards, the checks on an answer, rounds and revisions, and the ledger of calls."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from lab import assets, catalog, design
from lab.assets import Asset
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
    return {"aim": {"distortion": "saturated", "brightness": "balanced", "tail": "ambient", "note": "A singing fuzz lead."},
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
        self.assertIn("crest_by_gain", found)
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
        self.assertEqual(aim["distortion"], "saturated")
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

    def test_the_guitar_profile_reaches_a_round_and_its_sounds_carry_the_input(self):
        from lab import profile

        summary = {"Bridge Series": {"takes": 1, "peak_db": -17., "lufs": -30., "centroid_hz": 1800,
                                     "bands": {"lows": -6.}},
                   "Neck Parallel": {"takes": 1, "peak_db": -33., "lufs": -45., "centroid_hz": 2600,
                                     "bands": {"lows": -10.}}}
        guitar = profile.facts(summary, "Neck Parallel")
        good = answer("Fuzz", [block(FUZZ, 0, fuzz=10), block(HIWATT, 1), block(V30, 2)])
        other = answer("Plexi", [block(PLEXI, 0, gain=8), block(GREENBACK, 1)])
        assistant, script = self.assistant(reply(good, other))
        _, designs = design.round_of_three(assistant, "Gilmour Time solo", CHARVEL, guitar=guitar)
        prompt = script.asked[0][0]
        self.assertIn(design.sections("input"), prompt)
        sent = json.loads(prompt[prompt.index("{\"request\""):])
        self.assertEqual(sent["guitar_profile"], guitar)
        self.assertIn("15 dB weaker", sent["guitar_profile"]["settings"]["Neck Parallel"]["implies"])
        for sound, _ in designs:
            self.assertEqual(sound.origin["input"], {"instrument": "Charvel", "setting": "Neck Parallel"})
            self.assertEqual(sound.origin["kind"], "designed")
        self.assertEqual(Sound.from_record(json.loads(json.dumps(designs[0][0].to_record()))).origin["input"],
                         {"instrument": "Charvel", "setting": "Neck Parallel"})

    def test_without_a_profile_nothing_about_it_is_said(self):
        good = answer("Fuzz", [block(FUZZ, 0, fuzz=10), block(HIWATT, 1), block(V30, 2)])
        other = answer("Plexi", [block(PLEXI, 0, gain=8), block(GREENBACK, 1)])
        assistant, script = self.assistant(reply(good, other))
        _, designs = design.round_of_three(assistant, "Gilmour Time solo", CHARVEL, setting="Bridge")
        self.assertNotIn("guitar_profile", script.asked[0][0])
        self.assertEqual(designs[0][0].origin["input"], {"instrument": "Charvel", "setting": "Bridge"})
        assistant, script = self.assistant(reply(good, other))
        _, designs = design.round_of_three(assistant, "Gilmour Time solo", None)
        self.assertNotIn("input", designs[0][0].origin)

    def test_a_revision_hears_of_the_guitar_and_carries_the_input(self):
        saved = Sound("Fuzz", Chain((Row((Block(FUZZ), Block(HIWATT), Block(V30)), inlet=1, outlet=16),)),
                      {"kind": "preset"})
        changed = {**design.as_form(saved.chain), "name": "x", "target": "t", "explanation": "e", "differs": "",
                   "blend": {"level_db": 0, "spread": 0}, "sources": []}
        guitar = {"recorded_on": "Bridge Series", "reference": "Bridge Series",
                  "settings": {"Bridge Series": {"peak_db": -30., "peak_against_typical_humbucker_db": -12.,
                                                 "implies": "about 12 dB weaker than presets assume."}}}
        assistant, script = self.assistant(changed)
        revised = design.revise(assistant, saved, "more gain", CHARVEL, guitar=guitar)
        prompt = script.asked[0][0]
        self.assertIn(design.sections("input"), prompt)
        self.assertIn("12 dB weaker", prompt)
        self.assertEqual(revised.origin["input"], {"instrument": "Charvel", "setting": "Bridge Series"})
        self.assertEqual(revised.origin["from"], saved.id)
        assistant, script = self.assistant(changed)
        design.revise(assistant, saved, "more gain", CHARVEL)
        self.assertNotIn(design.sections("input"), script.asked[0][0])

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


MIX04 = Asset("ir", "CIR_108babccfa301792b0e730ef0fbd889", "YA MES 212 V30 Mix 04")
SM57 = Asset("ir", "CIR_10e44cd333cf5cadfdc0692a2d7581", "Celestion G12M SM57 Cap")
KNOWN = [MIX04, SM57, Asset("ir", "CIR_99", "Factory Cab", "factory"),
         Asset("capture", "a" * 64, "Dark Jazz", "user", None)]


class PlayerIRTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_the_irs_a_request_names_are_offered_and_no_others(self):
        self.assertEqual(design.player_irs("Plexi crunch through YA MES 212 V30 Mix 04", known=KNOWN),
                         {design.ASSET_IDS: MIX04})
        self.assertEqual(design.player_irs("plexi with my SM57 G12M IR", known=KNOWN), {design.ASSET_IDS: SM57})
        self.assertEqual(design.player_irs("plexi with an SM57 on a G12M", known=KNOWN), {})  # no IR asked for
        self.assertEqual(design.player_irs("Factory Cab IR", known=KNOWN), {})                # only the player's
        held = Chain((Row((Block(PLEXI), assets.block_for(SM57)), inlet=1, outlet=19),))
        self.assertEqual(list(design.player_irs("YA MES 212 V30 Mix 04 please", held, known=KNOWN).values()),
                         [SM57, MIX04])                    # what the sound holds first, even if never read

    def test_a_round_may_put_a_named_ir_in_place_of_a_cab(self):
        script = Scripted(reply(answer("Mine", [block(PLEXI, 0, gain=7), block(design.ASSET_IDS, 1, low_pass=9000)]),
                                answer("Stock", [block(PLEXI, 0, gain=5), block(V30, 1)])))
        assistant = Assistant(self.folder, run=script)
        _, designs = design.round_of_three(assistant, "Plexi through my YA MES 212 V30 Mix 04", CHARVEL,
                                           assets_known=KNOWN)
        prompt, schema = script.asked[0][:2]
        self.assertIn("YA MES 212 V30 Mix 04 (the player's IR)", prompt)
        self.assertIn(design.ASSET_IDS, schema["properties"]["designs"]["items"]["properties"]["rows"]["items"]
                      ["properties"]["blocks"]["items"]["properties"]["model_id"]["enum"])
        loader = designs[0][0].chain.block(0, 1)
        self.assertEqual(assets.bound(loader), MIX04)
        self.assertAlmostEqual(loader.display(5), 9000, delta=50)        # the first IR's LOW PASS only
        self.assertNotIn(13, loader.values)

    def test_a_revision_keeps_the_ir_the_sound_holds_and_may_swap_it(self):
        loader = assets.block_for(SM57).set_display(3, -3)
        saved = Sound("Mine", Chain((Row((Block(PLEXI), loader), inlet=1, outlet=19),)), {"kind": "preset"})
        irs = design.player_irs("swap in the YA MES 212 V30 Mix 04 IR", saved.chain, known=KNOWN)
        form = design.as_form(saved.chain, irs)
        self.assertEqual(form["rows"][0]["blocks"][1]["model_id"], design.ASSET_IDS)
        self.assertEqual(form["rows"][0]["blocks"][1]["name"], SM57.name)
        models = design._with_irs(design.offered("swap", "electric", saved.chain), irs, saved.chain)
        self.assertNotIn(catalog.IR_LOADER, models)                       # never an empty loader
        answer_ = {**form, "name": "x", "target": "t", "explanation": "e", "differs": "",
                   "blend": {"level_db": 0, "spread": 0}, "sources": []}
        chain, problems = design.to_chain(answer_, offered_ids=set(models), base=saved.chain, irs=irs)
        self.assertEqual(problems, [])
        self.assertEqual(differences(chain, saved.chain), [])            # its level too
        answer_["rows"][0]["blocks"][1]["model_id"] = design.ASSET_IDS + 1
        chain, problems = design.to_chain(answer_, offered_ids=set(models), base=saved.chain, irs=irs)
        self.assertEqual((problems, assets.bound(chain.block(0, 1))), ([], MIX04))

    def test_an_empty_ir_loader_is_not_accepted(self):
        _, problems = design.to_chain(answer("Empty", [block(PLEXI, 0), block(catalog.IR_LOADER, 1)]),
                                      offered_ids={PLEXI, catalog.IR_LOADER})
        self.assertTrue(any("name the IR by its id" in problem for problem in problems))



class ListeningPriorTest(unittest.TestCase):
    def test_what_the_player_listens_to_reaches_the_assistant_with_its_rule(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        script = Scripted(reply(answer("Vox V30", [block(1011, 0), block(V30, 1)]),        # offered for any request
                                answer("Vox Greenback", [block(1011, 0), block(GREENBACK, 1)])))
        assistant = Assistant(Path(folder.name), run=script)
        prior = {"artists": [{"artist": "Band A", "guitarists": ["Y"], "tones": ["crunch"]}],
                 "tone_families": {"crunch": 1}}
        design.round_of_three(assistant, "a lead tone", CHARVEL, listening=prior)
        prompt = script.asked[-1][0]
        self.assertIn('"player_listening"', prompt)
        self.assertIn("names no artist, song, player or sound", prompt)


if __name__ == "__main__":
    unittest.main()
