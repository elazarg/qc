"""Asking for sounds: the cards, the checks on an answer, rounds and revisions, and the ledger of calls."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest import mock

from lab.qc import assets
from lab.sound import catalog, design
from lab.qc.assets import Asset
from lab.sound.assistant import Assistant, Refused
from lab.sound.chain import Block, Chain, Row, differences
from lab.sound.sounds import Sound, describe

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
        # What was measured is the crest factor; the distortion word after it is labelled as what it suggests.
        self.assertEqual(found["crest_by_gain"],
                         "GAIN 2: 20 dB (suggests clean), 5: 20 dB (suggests clean), 8: 20 dB (suggests clean)")
        self.assertIn("crest factor", design.sections("craft"))
        self.assertIn("inference", design.sections("craft"))
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
        self.assertIn("The amp on the second path needs its own cab, or a cab on row 0 after the join.", problems)

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
        self.assertEqual(schema["properties"]["designs"]["items"]["properties"]["rows"]["items"]["properties"]
                         ["blocks"]["items"]["properties"]["model_id"], {"type": "integer"})
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
        from lab.hearing import profile

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

    def test_a_new_design_can_use_the_second_processor_pair(self):
        draft = answer("Four-row mix", [block(PLEXI, 0), block(V30, 1)])
        draft["rows"].extend([
            {"row": 2, "split_at": 0, "join_at": 3, "blocks": [block(DELAY, 1)]},
            {"row": 3, "split_at": -1, "join_at": -1, "blocks": [block(FUZZ, 1)]},
        ])
        chain, problems = design.to_chain(draft, offered_ids={PLEXI, V30, DELAY, FUZZ})
        self.assertEqual(problems, [])
        self.assertEqual(chain.rows[2].route, (1, 19))
        self.assertEqual(chain.rows[2].split, (0, 3))
        self.assertEqual(chain.block(3, 1).model, FUZZ)

    def test_a_new_design_can_use_a_model_outside_the_starting_points_and_a_loaded_capture(self):
        capture = Asset("capture", "f" * 64, "Bench preamp", "user", 2)
        indexed = set(design.inventory())
        stereo_cab = next(model.id for model in catalog.models()
                          if model.category == "Cabsim Guitar (ST)" and model.id in indexed)
        self.assertNotIn(stereo_cab, design.offered("clean electronic rig", "electric"))
        script = Scripted(reply(answer("Capture", [block(design.ASSET_IDS, 0), block(V30, 1)]),
                                answer("Stereo", [block(PLEXI, 0), block(stereo_cab, 1)])))
        _, rounds = design.round_of_three(Assistant(self.folder, run=script), "clean electronic rig", CHARVEL,
                                          assets_known=[capture])
        self.assertEqual(assets.bound(rounds[0][0].chain.block(0, 0)).key, capture.key)
        self.assertEqual(rounds[1][0].chain.block(0, 1).model, stereo_cab)
        self.assertNotIn(capture.key, script.asked[0][0])
        self.assertTrue(script.asked[0][2])

    def test_the_designer_recommends_the_actual_selected_guitars_controls(self):
        from lab.learner.dataset import suggested_setup

        guess = {"pickup": 5, "wiring": "Series",
                 "knobs": [{"name": "volume", "value": 10}, {"name": "tone", "value": 7}]}
        lead = {**answer("Smooth lead", [block(PLEXI, 0), block(V30, 1)]), "guitar_setup": guess}
        script = Scripted(reply(lead))
        _, rounds = design.round_of_three(Assistant(self.folder, run=script), "smooth lead", CHARVEL, assets_known=[])
        self.assertIn('"5": "Neck"', script.asked[0][0])
        suggested = rounds[0][0].origin["guitar_setup"]
        self.assertEqual(suggested_setup(suggested, "Charvel"), "Neck · Series · Vol 10 · Tone 7")
        self.assertEqual(suggested_setup(suggested, "Les Paul"), "")

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
        self.assertEqual(schema["properties"]["designs"]["items"]["properties"]["rows"]["items"]
                         ["properties"]["blocks"]["items"]["properties"]["model_id"], {"type": "integer"})
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


CAPTURE, COMP, EQ, ROOM = catalog.CAPTURE_MODELS[0], 5005, 4005, 8000
DARK_JAZZ = Asset("capture", "a" * 64, "Dark Jazz", "user", 1)


def given_back(chain, irs=None, **changed):
    """A chain as the assistant would return it unchanged: the form it was shown, completed as the schema asks."""
    form = json.loads(json.dumps(design.as_form(chain, irs)))
    return {"name": "x", "target": "t", "explanation": "e", "differs": "", "sources": [],
            "blend": {"level_db": 0, "spread": 0}, **form, **changed}


def shifted(reply, by=1, *, first=None):
    """The reply with row 0's blocks moved right to make room, and `first` put in front."""
    blocks = [dict(entry, column=entry["column"] + by) for entry in reply["rows"][0]["blocks"]]
    reply["rows"][0]["blocks"] = ([first] if first else []) + blocks
    return reply


def new_block(model_id, column, **settings):
    return {**block(model_id, column, **settings), "off": False}


class RevisionTest(unittest.TestCase):
    """A1, A2, A5, A6: what a revision keeps, and what it can now say."""

    def test_a_block_moved_to_make_room_keeps_what_the_assistant_never_sees(self):
        fuzz = Block(FUZZ, bypass=True).set_display(0, 5.)
        capture = assets.block_for(DARK_JAZZ).set_display(0, 3.)
        cab = Block(V30).set(1, "Dynamic 57").set(9, "Ribbon 121").set_display(5, 3.).set_display(13, 7.)
        saved = Chain((Row((fuzz, capture, cab), inlet=1, outlet=19),))
        reply = shifted(given_back(saved), first=new_block(COMP, 0))
        chain, problems = design.to_chain(reply, offered_ids={FUZZ, CAPTURE, V30, COMP}, base=saved)
        self.assertEqual(problems, [])
        self.assertEqual([found.model for _, found in chain.rows[0].blocks], [COMP, FUZZ, CAPTURE, V30])
        self.assertEqual(chain.block(0, 1), fuzz)                      # still off, as it was
        self.assertEqual(assets.bound(chain.block(0, 2)).key, DARK_JAZZ.key)     # the capture went with its block
        self.assertEqual(chain.block(0, 3), cab)                       # microphones, and each one's own position
        indexed = design.design_assets("add compression", saved, [])
        shown = design.as_form(saved, indexed)
        self.assertEqual(shown["rows"][0]["blocks"][1]["model_id"], design.ASSET_IDS)
        reply = shifted(shown, first=new_block(COMP, 0))
        chain, problems = design.to_chain(reply, offered_ids={FUZZ, V30, COMP, *indexed}, base=saved, irs=indexed)
        self.assertEqual((problems, chain.block(0, 2)), ([], capture))

    def test_blocks_of_one_model_are_told_apart_and_a_block_may_move_to_the_other_path(self):
        first, second = Block(DELAY).set_display(0, 20.), Block(DELAY).set_display(0, 50.)
        saved = Chain((Row((first, second, Block(PLEXI)), inlet=1, outlet=19),))
        reply = given_back(saved)
        del reply["rows"][0]["blocks"][0]                              # the first delay goes; the second stays put
        chain, problems = design.to_chain(reply, offered_ids={DELAY, PLEXI}, base=saved)
        self.assertEqual((problems, chain.block(0, 1), chain.block(0, 0)), ([], second, None))
        reply = given_back(saved)
        moved = reply["rows"][0]["blocks"].pop(1)
        reply["rows"].append({"row": 1, "split_at": -1, "join_at": -1, "blocks": [dict(moved, column=0)]})
        reply["rows"][0].update(split_at=0, join_at=1)
        chain, problems = design.to_chain(reply, offered_ids={DELAY, PLEXI}, base=saved)
        self.assertEqual(chain.block(1, 0), second)                    # onto the second path, with all it held

    def test_a_capture_block_that_ends_up_with_no_capture_is_a_problem(self):
        saved = Chain((Row((assets.block_for(DARK_JAZZ), Block(V30)), inlet=1, outlet=19),))
        reply = given_back(saved)
        reply["rows"][0]["blocks"].append(new_block(CAPTURE, 4))      # a second capture block, from nowhere
        _, problems = design.to_chain(reply, offered_ids={CAPTURE, V30}, base=saved)
        self.assertTrue(any("holds no capture" in problem for problem in problems), problems)
        _, problems = design.to_chain(answer("New", [block(CAPTURE, 0), block(V30, 1)]), offered_ids={CAPTURE, V30})
        self.assertTrue(any("holds no capture" in problem for problem in problems), problems)

    def test_a_second_path_added_by_a_revision_gets_its_mixer_blend_and_polarity(self):
        saved = Chain((Row((Block(FUZZ), Block(PLEXI).set_display(0, 5.), Block(V30)), inlet=1, outlet=19),))
        reply = given_back(saved, blend={"level_db": -6, "spread": 60})
        reply["rows"][0].update(split_at=1, join_at=3)
        reply["rows"].append({"row": 1, "split_at": -1, "join_at": -1,
                              "blocks": [new_block(RECTIFIER, 1, gain=4), new_block(GREENBACK, 2)]})
        offered = {FUZZ, PLEXI, V30, RECTIFIER, GREENBACK}
        chain, problems = design.to_chain(reply, offered_ids=offered, base=saved)
        mixer = dict(chain.rows[0].mixer)
        self.assertEqual(problems, [])
        self.assertAlmostEqual(catalog.to_display(catalog.MIXER, 2, mixer[2]), -6., places=1)
        self.assertAlmostEqual(catalog.to_display(catalog.MIXER, 3, mixer[3]) - catalog.to_display(catalog.MIXER, 1, mixer[1]),
                               6., places=1)
        self.assertEqual(mixer[4], 1.)                 # a Plexi against a Rectifier: measured to oppose
        fresh, _ = design.to_chain({**reply, "scenes": []}, offered_ids=offered)
        self.assertEqual(chain.rows[0].mixer, fresh.rows[0].mixer)     # as the same answer gets as a new design
        # The rules of a split hold for a path a revision adds.
        reply["rows"][1]["blocks"] = [new_block(RECTIFIER, 1, gain=4)]
        _, problems = design.to_chain(reply, offered_ids=offered, base=saved)
        self.assertTrue(any("needs its own cab" in problem for problem in problems))

    def test_a_revision_of_two_paths_keeps_the_join_until_the_amps_or_the_blend_change(self):
        odd = ((1, .31), (2, .5), (3, .62), (4, 0.), (0, .77))
        saved = Chain((Row((Block(FUZZ), Block(PLEXI), Block(V30)), inlet=1, outlet=19, split=(1, 3), mixer=odd),
                       Row((None, Block(HIWATT), Block(GREENBACK)))))
        offered = {FUZZ, PLEXI, V30, HIWATT, GREENBACK, RECTIFIER}
        form = design.as_form(saved)
        self.assertEqual(set(form["blend"]), {"level_db", "spread"})
        chain, problems = design.to_chain(given_back(saved), offered_ids=offered, base=saved)
        self.assertEqual((problems, chain.rows[0].mixer), ([], saved.rows[0].mixer))     # untouched, phase and all
        louder = given_back(saved, blend={"level_db": form["blend"]["level_db"] + 3, "spread": form["blend"]["spread"]})
        chain, _ = design.to_chain(louder, offered_ids=offered, base=saved)
        mixer = dict(chain.rows[0].mixer)
        self.assertAlmostEqual(catalog.to_display(catalog.MIXER, 2, mixer[2]) - catalog.to_display(catalog.MIXER, 2, .5),
                               3., places=1)
        self.assertEqual((mixer[4], mixer[0]), (0., .77))              # the amps are the same: the phase stays
        swapped = given_back(saved)
        swapped["rows"][1]["blocks"][0] = new_block(RECTIFIER, 1)      # another amp on the second path
        chain, problems = design.to_chain(swapped, offered_ids=offered, base=saved)
        self.assertEqual((problems, dict(chain.rows[0].mixer)[4]), ([], 1.))   # worked out again: now opposed
        gone = given_back(saved)
        gone["rows"][0].update(split_at=-1, join_at=-1)
        _, problems = design.to_chain(gone, offered_ids=offered, base=saved)
        self.assertTrue(any("nothing reaches them" in problem for problem in problems))

    def test_the_assistant_can_switch_a_block_on_and_off(self):
        saved = Chain((Row((Block(FUZZ, bypass=True), Block(DELAY, bypass=(False, True) + (False,) * 6), Block(PLEXI)),
                           inlet=1, outlet=19),), ("A", "B"))
        block_schema = design.design_schema()["properties"]["rows"]["items"]["properties"]["blocks"]["items"]
        self.assertEqual(block_schema["properties"]["off"], {"type": "boolean"})
        self.assertIn("off", block_schema["required"])
        self.assertIn("off is true", design.sections("chain"))
        reply = given_back(saved)
        self.assertEqual([entry["off"] for entry in reply["rows"][0]["blocks"]], [True, False, False])
        chain, problems = design.to_chain(reply, offered_ids={FUZZ, DELAY, PLEXI}, base=saved)
        self.assertEqual((problems, differences(chain, saved)), ([], []))
        reply["rows"][0]["blocks"][0]["off"] = False                   # "turn the fuzz on"
        reply["rows"][0]["blocks"][1]["off_in_scenes"] = []            # "the delay in every scene"
        reply["rows"][0]["blocks"][2]["off"] = True                    # "and the amp off"
        chain, problems = design.to_chain(reply, offered_ids={FUZZ, DELAY, PLEXI}, base=saved)
        self.assertEqual(problems, [])
        self.assertEqual([found.bypass for _, found in chain.rows[0].blocks], [False, False, True])
        reply["rows"][0]["blocks"][1]["off_in_scenes"] = [9]
        _, problems = design.to_chain(reply, offered_ids={FUZZ, DELAY, PLEXI}, base=saved)
        self.assertIn("A block is switched off in a scene the design does not have.", problems)

    def test_a_control_that_differs_by_scene_is_shown_with_its_first_scenes_value(self):
        gain = catalog.model(PLEXI).index("GAIN")
        clean, lead = catalog.to_norm(PLEXI, gain, 3.), catalog.to_norm(PLEXI, gain, 8.)
        amp = Block(PLEXI, ((gain, (clean, lead, clean, lead) + (clean,) * 4),))
        saved = Chain((Row((amp, Block(V30)), inlet=1, outlet=19),), ("Clean", "Lead"))
        shown = design.as_form(saved)["rows"][0]["blocks"][0]
        self.assertEqual(shown["settings"], [{"name": "GAIN", "value": 3.}])
        self.assertEqual(shown["scene_settings"], [{"scene": 1, "name": "GAIN", "value": 8.},
                                                   {"scene": 3, "name": "GAIN", "value": 8.}])    # past the names too
        chain, problems = design.to_chain(given_back(saved), offered_ids={PLEXI, V30}, base=saved)
        self.assertEqual((problems, chain), ([], saved))
        # A setting holds in every scene: with its scene settings left out, the control is the same everywhere.
        reply = given_back(saved)
        reply["rows"][0]["blocks"][0]["scene_settings"] = []
        chain, problems = design.to_chain(reply, offered_ids={PLEXI, V30}, base=saved)
        self.assertEqual((problems, chain.block(0, 0).values[gain]), ([], clean))
        # The first scene's value changed, the lead scene's kept.
        reply = given_back(saved)
        reply["rows"][0]["blocks"][0]["settings"] = [{"name": "GAIN", "value": 4}]
        chain, _ = design.to_chain(reply, offered_ids={PLEXI, V30}, base=saved)
        self.assertEqual([round(chain.block(0, 0).display(gain, scene), 2) for scene in range(4)], [4., 8., 4., 8.])
        self.assertEqual(chain.block(0, 0).values[gain][1], lead)      # to the last digit

    def test_no_chain_is_shown_in_a_form_that_would_be_refused(self):
        level = catalog.model(V30).index("OUTPUT VOLUME")
        sync = catalog.model(7040).index("SYNC NOTE")
        awkward = {
            "one named scene": Chain((Row((Block(PLEXI), Block(V30)), inlet=1, outlet=19),), ("Lead",)),
            "names with a gap": Chain((Row((Block(PLEXI), Block(V30)), inlet=1, outlet=19),), ("A", "", "C")),
            "a level past what a design may ask": Chain((Row((Block(PLEXI), Block(V30).set_display(level, -20.)),
                                                             inlet=1, outlet=19),)),
            "a switch between its steps": Chain((Row((Block(7040).set(sync, .55), Block(PLEXI)), inlet=1, outlet=19),)),
            "a model the catalog does not know": Chain((Row((Block(987654, ((0, .5),), bypass=True), Block(PLEXI)),
                                                            inlet=1, outlet=19),)),
            "more than the budget says fits": Chain((Row((Block(RECTIFIER),) * 3 + (Block(V30),), inlet=1, outlet=19),)),
            "an odd split": Chain((Row((Block(FUZZ), Block(HIWATT)), inlet=1, outlet=16, split=(5, 5)),
                                   Row((Block(HIWATT),)))),
            "an empty loader": Chain((Row((Block(PLEXI), Block(catalog.IR_LOADER)), inlet=1, outlet=19),)),
        }
        for what, saved in awkward.items():
            held = [found.model for _, _, found in saved.blocks]
            models = design.offered("x", "electric", saved)
            self.assertTrue(set(held) <= set(models), what)
            design.facts("x", CHARVEL, chain=saved, models=models)     # the cards and the form are made without fault
            chain, problems = design.to_chain(given_back(saved), offered_ids=set(models), base=saved)
            self.assertEqual((problems, chain), ([], saved), what)
        # What is out of range stays a problem once the answer changes it.
        reply = given_back(awkward["a level past what a design may ask"])
        reply["rows"][0]["blocks"][1]["settings"] = [{"name": "OUTPUT VOLUME", "value": -19}]
        _, problems = design.to_chain(reply, offered_ids={PLEXI, V30}, base=awkward["a level past what a design may ask"])
        self.assertTrue(any("OUTPUT VOLUME -19 is outside -6 to 6" in problem for problem in problems), problems)

    def test_a_revision_carries_the_assistants_account_of_what_it_changed(self):
        saved = Sound("Mine", Chain((Row((Block(PLEXI), Block(V30)), inlet=1, outlet=19),)), {"kind": "preset"})
        script = Scripted(given_back(saved.chain, explanation="Opened the cab's low-pass.",
                                     sources=[{"title": "t", "url": "https://example.org/x"}]))
        with TemporaryDirectory() as folder:
            revised = design.revise(Assistant(folder, run=script), saved, "brighter", CHARVEL)
        self.assertEqual((revised.name, revised.explanation), ("Mine", "Opened the cab's low-pass."))
        self.assertEqual(revised.sources, [{"title": "t", "url": "https://example.org/x"}])
        self.assertIn("what you changed", design.sections("revise"))


class OfferedTest(unittest.TestCase):
    """A3 and A7: what the assistant is offered and told agrees with what an answer may hold."""

    def test_a_bass_is_offered_bass_amps_cabs_and_drives(self):
        for text in ("motown bass", "Ampeg SVT rock bass with grit", "a bass sound for Tool"):
            categories = {catalog.model(model_id).category for model_id in design.offered(text, "bass")}
            self.assertTrue({"Bass Amplifier", "Cabsim Bass (M)", "Bass Overdrive"} <= categories, text)
            self.assertFalse({"Guitar Amplifier", "Cabsim Guitar (M)"} & categories, text)
        every = [model.id for model in catalog.models() if model.category == "Bass Amplifier" and design.usable(model.id, "bass")]
        self.assertTrue(every and set(every) <= set(design.offered("x", "bass")))
        self.assertIn("bass amp", design.sections("bass"))
        amp, cab = every[0], next(model.id for model in catalog.models() if model.category == "Cabsim Bass (M)"
                                  and design.usable(model.id, "bass"))
        chain, problems = design.to_chain(answer("Bass", [block(amp, 0), block(cab, 1)]),
                                          offered_ids=set(design.offered("x", "bass")))
        self.assertEqual(problems, [])
        self.assertNotIn(amp, design.offered("x", "electric"))
        for kind in ("acoustic", "classical"):                         # no amp and no cab, as their sections say
            categories = {catalog.model(model_id).category for model_id in design.offered("warm fingerstyle", kind)}
            self.assertFalse({category for category in categories if "Amplifier" in category or "Cabsim" in category})
            self.assertIn("Start with direct EQ", design.sections(kind))

    def test_a_stock_cab_offers_its_microphones_filters_and_a_bounded_level_and_nothing_internal(self):
        found = design.card(V30)
        self.assertEqual(found["controls"], {"DISTANCE": [0., 10.], "POSITION": [0., 10.], "HPF": [20., 500.],
                                             "LPF": [500., 20000.], "OUTPUT VOLUME": [-6., 6.]})
        self.assertEqual(found["switches"], {})
        _, problems = design.to_chain(answer("Cab", [block(PLEXI, 0), {**block(V30, 1), "settings": [
            {"name": "bypass", "value": 1}, {"name": "OUTPUT VOLUME", "value": 12}]}]), offered_ids={PLEXI, V30})
        self.assertTrue(any("no control 'bypass'" in problem for problem in problems))
        self.assertTrue(any("OUTPUT VOLUME 12 is outside -6 to 6" in problem for problem in problems))
        self.assertEqual(design.card(EQ)["controls"]["OUTPUT"], [-6., 6.])
        self.assertEqual(design.ir_card(design.ASSET_IDS, MIX04)["controls"]["LEVEL"], [-3., 9.])

    def test_the_lead_scenes_level_is_the_control_the_instructions_name(self):
        said = design.sections("scenes")
        self.assertIn("OUTPUT VOLUME", said)
        self.assertNotIn("OUTPUT", design.card(PLEXI)["controls"])     # the amp's own is the app's, and is not offered
        self.assertIn("not among its controls", design.sections("chain"))
        lead = {**block(V30, 1), "scene_settings": [{"scene": 1, "name": "OUTPUT VOLUME", "value": 3}]}
        chain, problems = design.to_chain(answer("Lead", [block(PLEXI, 0, gain=6), lead], scenes=("Rhythm", "Lead")),
                                          offered_ids={PLEXI, V30})
        level = catalog.model(V30).index("OUTPUT VOLUME")
        self.assertEqual(problems, [])
        self.assertEqual([round(chain.block(0, 1).display(level, scene), 1) for scene in range(3)], [0., 3., 0.])

    def test_two_amps_may_share_one_cab_after_the_join_as_the_instructions_say(self):
        self.assertIn("one cab on row 0 after the join", design.sections("paths"))
        shared = answer("Shared", [block(PLEXI, 0, gain=5), block(V30, 1)], second=[block(RECTIFIER, 0, gain=4)],
                        split=(0, 1))
        chain, problems = design.to_chain(shared, offered_ids={PLEXI, V30, RECTIFIER})
        self.assertEqual(problems, [])
        self.assertEqual(dict(chain.rows[0].mixer)[4], 1.)             # and their polarity is still looked after

    def test_a_split_needs_a_block_on_the_second_path(self):
        self.assertIn("at least one block", design.sections("chain"))
        for second in (None, []):
            _, problems = design.to_chain(answer("Dry", [block(FUZZ, 0), block(HIWATT, 1), block(V30, 2)],
                                                 second=second, split=(1, 3)), offered_ids={FUZZ, HIWATT, V30})
            self.assertTrue(any("at least one block on the second path" in problem for problem in problems), second)

    def test_scene_settings_hold_a_switch_to_whole_steps_as_settings_do(self):
        for part in ("settings", "scene_settings"):
            entry = {"name": "PING PONG", "value": .5, **({"scene": 1} if part == "scene_settings" else {})}
            _, problems = design.to_chain(answer("Delay", [{**block(DELAY, 0), part: [entry]}], scenes=("a", "b")),
                                          offered_ids={DELAY})
            self.assertTrue(any("PING PONG 0.5 is outside 0 to 1 in whole steps." in problem for problem in problems), part)


class AnswersTest(unittest.TestCase):
    """B4: answers out of form, too many designs, and keeping what can be used."""

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_an_answer_out_of_form_is_a_problem_and_not_a_fault(self):
        good = [block(PLEXI, 0)]
        cases = {
            "blend of text": {**answer("x", [block(PLEXI, 0), block(V30, 1)], second=[block(RECTIFIER, 0), block(GREENBACK, 1)],
                                       split=(0, 2)), "blend": {"level_db": "loud", "spread": 0}},
            "rows of text": {**answer("x", good), "rows": ["x"]},
            "rows not a list": {**answer("x", good), "rows": {"row": 0}},
            "blocks not a list": {**answer("x", good), "rows": [{"row": 0, "split_at": -1, "join_at": -1, "blocks": 3}]},
            "a block of text": answer("x", ["plexi"]),
            "a column of text": answer("x", [{"column": "0", "model_id": PLEXI}]),
            "a model as a list": answer("x", [{"column": 0, "model_id": [1]}]),
            "a model as a name": answer("x", [{"column": 0, "model_id": "Plexi"}]),
            "scenes as a number": {**answer("x", good), "scenes": 3},
            "scenes of numbers": {**answer("x", good), "scenes": [1, 2]},
            "a value that is true": answer("x", [block(PLEXI, 0, gain=True)]),
            "a value that is not a number": answer("x", [block(PLEXI, 0, gain=float("nan"))]),
            "a setting of text": answer("x", [{**block(PLEXI, 0), "settings": ["GAIN 5"]}]),
            "settings not a list": answer("x", [{**block(PLEXI, 0), "settings": {"GAIN": 5}}]),
            "a split of text": {**answer("x", good), "rows": [{"row": 0, "split_at": "1", "join_at": "3", "blocks": good}]},
            "the same row twice": {**answer("x", good), "rows": [
                {"row": 0, "split_at": -1, "join_at": -1, "blocks": good},
                {"row": 0, "split_at": -1, "join_at": -1, "blocks": [block(RECTIFIER, 0)]}]},
            "off as text": answer("x", [{**block(PLEXI, 0), "off": "yes"}]),
            "not a design at all": ["x"],
        }
        for what, reply_ in cases.items():
            _, problems = design.to_chain(reply_, offered_ids={PLEXI, V30, RECTIFIER, GREENBACK})
            self.assertTrue(problems, what)
        _, problems = design.to_chain(cases["the same row twice"], offered_ids={PLEXI, RECTIFIER})
        self.assertIn("Row 0 is given twice.", problems)

    def test_a_reply_that_is_not_a_round_at_all_is_repaired_like_any_other(self):
        good = answer("One", [block(PLEXI, 0, gain=5), block(V30, 1)])
        other = answer("Two", [block(RECTIFIER, 0, gain=5), block(V30, 1)])
        for broken in ({"aim": "loud", "designs": []}, {"designs": [good]}, ["x"], {**reply(good, other), "designs": 7},
                       {**reply(good), "aim": {"distortion": "extreme", "brightness": "balanced", "tail": "dry"}}):
            script = Scripted(broken, reply(good, other))
            _, designs = design.round_of_three(Assistant(self.folder, run=script), "plexi crunch", CHARVEL)
            self.assertEqual((len(designs), len(script.asked)), (2, 2), broken)

    def test_a_round_is_three_designs_at_most(self):
        many = [answer(f"D{gain}", [block(PLEXI, 0, gain=gain), block(V30, 1)]) for gain in range(1, 7)]
        script = Scripted(reply(*many))
        _, designs = design.round_of_three(Assistant(self.folder, run=script), "plexi crunch", CHARVEL)
        self.assertEqual([sound.name for sound, _ in designs], ["D1", "D2", "D3"])
        self.assertIn("no more", design.sections("round"))

    def test_the_one_design_that_can_be_used_is_kept(self):
        good = answer("Good", [block(PLEXI, 0, gain=5), block(V30, 1)])
        broken = answer("Bad", [block(HIWATT, 0, gain=99)])
        script = Scripted(reply(good, broken, broken), reply(broken, broken, good))
        _, designs = design.round_of_three(Assistant(self.folder, run=script), "plexi crunch", CHARVEL)
        self.assertEqual([sound.name for sound, _ in designs], ["Good"])           # after the repair: one is a round
        script = Scripted(reply(good, broken), reply(broken, broken))
        _, designs = design.round_of_three(Assistant(self.folder, run=script), "plexi crunch", CHARVEL)
        self.assertEqual([sound.name for sound, _ in designs], ["Good"])           # the first answer's, not thrown away

        def once(prompt, schema, **options):
            if once.asked:
                raise RuntimeError("The assistant did not answer in time.")
            once.asked.append(prompt)
            return reply(good, broken)

        once.asked = []
        _, designs = design.round_of_three(Assistant(self.folder, run=once), "plexi crunch", CHARVEL)
        self.assertEqual([sound.name for sound, _ in designs], ["Good"])           # no repair to be had: still kept
        script = Scripted(reply(broken), reply(broken))
        with self.assertRaisesRegex(ValueError, "could not be used"):
            design.round_of_three(Assistant(self.folder, run=script), "plexi crunch", CHARVEL)


class PointedAtTest(unittest.TestCase):
    """Which models a request points at, and what a model's card tells the designer. (The earlier app's tests
    of this, carried over.)"""

    def named(self, text):
        return {catalog.model(model_id).name for model_id in design.named_in(text, "electric")}

    def test_a_request_points_at_gear_by_maker_model_artist_or_genre(self):
        self.assertLessEqual({"CA Duo Ch3 Modern", "Green 808", "412 CA Stand OS S V30 90s (M)"},
                             self.named("rectifier wall of sound"))
        self.assertLessEqual({"Plini Clean", "Plini Lead"}, self.named("plini"))
        self.assertIn("US DLX Vibrato", self.named("blues with a deluxe reverb"))

    def test_a_name_made_of_common_words_is_matched_whole(self):
        self.assertIn("UK C30 TopBoost", self.named("something like The Edge"))
        self.assertIn("UK C30 TopBoost", self.named("a U2 sound"))
        import re

        pattern = dict(design.NAMES)["the edge"]
        self.assertIsNone(re.search(pattern, "on the edge of breakup"))     # a phrase, not the guitarist

    def test_a_remark_about_the_sound_points_at_nothing(self):
        for remark in ("a bit tighter", "too bright on the high strings", "make it sound like a clean guitar amp"):
            self.assertEqual(design.named_in(remark, "electric"), [], remark)

    def test_the_amps_come_first_and_bring_the_boosts_and_cabs_they_are_paired_with(self):
        found = design.named_in("rectifier wall of sound", "electric")
        kinds = [catalog.model(model_id).category for model_id in found]
        self.assertEqual(kinds[0], "Guitar Amplifier")
        self.assertLess(kinds.index("Guitar Amplifier"), kinds.index("Guitar Overdrive"))
        self.assertIn("Cabsim Guitar (M)", kinds[kinds.index("Guitar Overdrive"):])
        self.assertEqual(len(found), len(set(found)))

    def test_a_model_the_unit_will_not_run_is_never_pointed_at(self):
        usable = design.usable
        with mock.patch.object(design, "usable", lambda model_id, kind: model_id != RECTIFIER and usable(model_id, kind)):
            self.assertNotIn(RECTIFIER, design.named_in("rectifier wall of sound", "electric"))
            self.assertNotIn(RECTIFIER, design.offered("rectifier wall of sound", "electric"))

    def test_a_request_widens_the_models_offered_beyond_the_everyday_ones(self):
        everyday = {model_id for model_id in design.EVERYDAY if design.usable(model_id, "electric")}
        self.assertEqual(set(design.offered("a bit tighter", "electric")), everyday)
        offered = set(design.offered("rectifier wall of sound", "electric"))
        self.assertLess(everyday, offered)
        self.assertLessEqual({1018, 1019, 1020}, offered)       # the three channel modes of the Dual Rectifier
        held = Chain((Row((Block(1176),), inlet=1, outlet=19),))
        self.assertNotIn(1176, everyday)
        self.assertIn(1176, design.offered("warmer", "electric", held))     # what the sound holds stays offered

    def test_a_card_says_what_the_model_is_what_to_watch_out_for_and_what_its_controls_do(self):
        card = design.card(5)                                   # Green 808
        self.assertIn("TS808", card["about"])
        self.assertLessEqual(len(card["about"]), 300)
        self.assertIn("honk", card["watch_out"])
        self.assertEqual(set(card["controls"]), {"OVERDRIVE", "TONE", "LEVEL"})
        self.assertLessEqual(set(card["control_notes"]), set(card["controls"]))
        self.assertIn("gain", card["control_notes"]["OVERDRIVE"])
        bare = design.card(16005)                               # a model the research says nothing about
        self.assertFalse({"about", "watch_out", "control_notes"} & set(bare))

    def test_a_switch_is_offered_apart_from_the_knobs_and_only_in_whole_positions(self):
        card = design.card(1011)                                # UK C30 TopBoost
        self.assertEqual(set(card["controls"]), {"VOLUME", "BASS", "TREBLE", "TONE CUT"})
        self.assertEqual(card["switches"], {"BOOST": [0, 1]})
        _, problems = design.to_chain(answer("Half", [block(1011, 0, boost=.5), block(V30, 1)]),
                                      offered_ids={1011, V30})
        self.assertEqual(len([problem for problem in problems if "BOOST" in problem]), 1, problems)
        chain, problems = design.to_chain(answer("On", [block(1011, 0, boost=1), block(V30, 1)]),
                                          offered_ids={1011, V30})
        self.assertEqual(problems, [])

    def test_a_card_carries_the_units_named_settings_for_the_models_own_controls_only(self):
        from lab import knowledge

        named = {"Tight boost": {"OVERDRIVE": 0, "TONE": 5.5, "LEVEL": 9, "NOT A CONTROL": 1}, "Nothing of its own": {"X": 1}}
        with mock.patch.object(knowledge, "named_settings", return_value=named):
            self.assertEqual(design.card(5)["named_settings"], {"Tight boost": {"OVERDRIVE": 0, "TONE": 5.5, "LEVEL": 9}})
        with mock.patch.object(knowledge, "named_settings", return_value={}):
            self.assertNotIn("named_settings", design.card(5))


if __name__ == "__main__":
    unittest.main()
