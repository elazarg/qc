import unittest
from unittest import mock

from lab import catalog, knobs
from lab.chain import Block, Chain, Row


def block(model, chosen=None, *, every=True):
    """A block with the named controls at display values and, when `every`, the rest at the catalog default."""
    chosen = chosen or {}
    found = Block(model)
    for param in catalog.model(model).params:
        if param.name in chosen:
            found = found.set_display(param.index, chosen[param.name])
        elif every and param.default is not None and param.kind not in ("string",):
            try:
                found = found.set_display(param.index, param.default)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
    return found


def chain(*blocks, rows=None):
    """One row from blocks, or several rows from `rows`, each a list of blocks."""
    return Chain(tuple(Row(tuple(cells)) for cells in (rows or [blocks])))


def moves(source, values, **options):
    return {(edit["model"], edit["name"]): round(edit["to"] - edit["from"], 2)
            for edit in knobs.moves(source, values, **options)}


def index(model, name, nth=0):
    return catalog.model(model).index(name, nth)


class EvenAmps(unittest.TestCase):
    """Every amp's tone controls taken as equally strong, and no amp's gain known to add level."""

    def setUp(self):
        for name, value in (("_scale", 1.), ("_effect", None)):
            patch = mock.patch.object(knobs, name, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)


class ControlRolesTest(EvenAmps):
    def test_brightness_turns_backward_controls_the_other_way(self):
        edits = moves(chain(block(4), block(1011)), {"brightness": -.5})
        self.assertEqual(edits, {("Rodent Drive", "FILTER"): 1., ("UK C30 TopBoost", "TREBLE"): -1.,
                                 ("UK C30 TopBoost", "TONE CUT"): 1.})
        self.assertEqual(moves(chain(block(1011)), {"presence": .4}), {("UK C30 TopBoost", "TONE CUT"): -1.})

    def test_tightness_reaches_the_low_end_resonance_and_a_tight_control(self):
        edits = moves(chain(block(8), block(1017)), {"tightness": .4})
        self.assertEqual(edits, {("Freeman BOD", "BASS"): -.72, ("Freeman BOD", "TIGHT"): .72,
                                 ("EV101IIIS Red EL34 100W", "BASS"): -.8,
                                 ("EV101IIIS Red EL34 100W", "RESONANCE"): -1.})

    def test_drive_moves_whatever_a_model_calls_its_gain(self):
        edits = moves(chain(block(5), block(1176)), {"drive": .5})
        self.assertEqual(set(edits), {("Green 808", "OVERDRIVE"), ("Dumbbell ODS", "VOLUME")})
        self.assertTrue(all(change > 0 for change in edits.values()))
        self.assertEqual(set(moves(chain(block(1121)), {"drive": .5})), {("Brit UBL Lead", "INPUT GAIN")})

    def test_volume_is_the_drive_only_on_amps_without_a_gain_control(self):
        self.assertTrue({1008, 1011, 1095, 1136, 1176} <= knobs.AMP_VOLUME_AS_DRIVE)
        self.assertFalse({1137, 1018, 1093} & knobs.AMP_VOLUME_AS_DRIVE)

    def test_mids_follow_either_spelling(self):
        self.assertEqual(moves(chain(block(1180)), {"mids": .5}), {("Petrucci Lead", "MIDDLE"): 1.})
        self.assertEqual(moves(chain(block(1093)), {"mids": .5}), {("Brit Plexi 100 Normal", "MID"): 1.})

    def test_a_move_scales_to_the_range_of_the_control(self):
        self.assertEqual(moves(chain(block(42, {"CUT": 20.})), {"brightness": -.5}), {("81 Creations Drive", "CUT"): 10.})

    def test_the_gate_knob_moves_only_the_simple_gate(self):
        self.assertEqual(moves(chain(block(16003, {"THRESHOLD": -60.})), {"gate": .4}),
                         {("Simple Gate", "THRESHOLD"): 6.})
        others = [model.id for model in catalog.models()
                  if model.category == "Utility" and model.id != 16003 and model.named("THRESHOLD")]
        for model in others[:3]:
            self.assertEqual(knobs.moves(chain(block(model)), {"gate": .4}), [], model)


class StackedMovesTest(EvenAmps):
    def test_stacked_clean_controls_leave_the_amp_input_open(self):
        source = chain(block(1045, {"GAIN": 5.4}, every=False))
        gain = next(edit for edit in knobs.moves(source, {"drive": -1, "jazz": 1, "sustain": -1, "bloom": 1})
                    if edit["name"] == "GAIN")
        self.assertAlmostEqual(gain["from"], 5.4, places=4)
        self.assertAlmostEqual(gain["to"], 1.5)
        self.assertAlmostEqual(source.block(0, 0).display(0), 5.4, places=4)

    def test_clean_controls_keep_an_already_quiet_amp_and_full_capture_gain_range(self):
        source = chain(block(1045, {"GAIN": .7}, every=False), block(14000, {"GAIN": 0.}, every=False))
        edits = knobs.moves(source, {"drive": -1, "jazz": 1})
        self.assertEqual([(edit["column"], edit["name"], edit["to"]) for edit in edits], [(1, "GAIN", -24.)])

    def test_stock_graphic_eq_faders_are_available_to_mix_and_attack_controls(self):
        edits = {edit["name"]: edit["to"] for edit in knobs.moves(chain(block(4005)), {"band_fit": 1., "punch": .5})}
        self.assertEqual(edits, {"125Hz": -1.6, "1kHz": 1.4, "2kHz": .9})

    def test_positions_outside_a_knobs_travel_are_refused(self):
        for values in ({"jazz": -.5}, {"drive": 1.5}, {"warmth": float("nan")}, {"nonsense": .2}):
            with self.assertRaises(ValueError):
                knobs.targets(chain(block(1045)), values)


class ScenesTest(EvenAmps):
    def setUp(self):
        super().setUp()
        model = 1084
        low, high = catalog.to_norm(model, 0, 3.), catalog.to_norm(model, 0, 7.)
        self.split = (low, high) + (low,) * 6
        self.source = chain(Block(model, {0: self.split, 2: catalog.to_norm(model, 2, 5.)}))

    def test_moves_shape_only_the_named_scene(self):
        edits = knobs.moves(self.source, {"drive": -.5}, scene=1)
        self.assertEqual(len(edits), 1)
        self.assertAlmostEqual(edits[0]["from"], 7., places=4)
        stored = knobs.targets(self.source, {"drive": -.5}, scene=1)[0, 0, 0]
        self.assertEqual(stored[0], self.split[0])
        self.assertLess(stored[1], self.split[1])
        self.assertEqual(stored[2:], self.split[2:])

    def test_without_a_scene_each_scene_moves_its_own_value(self):
        result = knobs.apply(self.source, {"drive": -.5, "body": .5})
        moved = result.block(0, 0).values[0]
        once = knobs.targets(self.source, {"drive": -.5}, scene=1)[0, 0, 0]
        self.assertEqual(moved[1], once[1])                  # as if turned in that scene alone
        self.assertLess(moved[0], self.split[0])
        self.assertLess(moved[1], self.split[1])
        # The shared Bass still moves.
        self.assertAlmostEqual(result.block(0, 0).display(2), 6., places=1)
        # The original chain is never changed.
        self.assertAlmostEqual(self.source.block(0, 0).display(2), 5., places=3)

    def test_apply_writes_the_named_scene(self):
        result = knobs.apply(self.source, {"drive": -.5}, scene=1)
        self.assertLess(result.block(0, 0).display(0, 1), 7.)
        self.assertAlmostEqual(result.block(0, 0).display(0, 0), 3., places=4)


class FiltersTest(EvenAmps):
    def test_owned_ir_filters_participate_in_musical_directions(self):
        model, at = 29001, index(29001, "LOW PASS")
        source = chain(Block(model, {**{i: .25 for i in range(at)}, at: catalog.to_norm(model, at, 8000.)}))
        edits = knobs.moves(source, {"texture": -.5})
        self.assertTrue(any(edit["index"] == at and edit["to"] < edit["from"] for edit in edits))

    def test_each_row_moves_only_the_filter_doing_the_work(self):
        cab, eq = 12006, 4005
        # Row 1: the equalizer's high-pass is set and the cab's is parked; the cab's low-pass is the lower.
        # Row 2: the other way round.
        first = [block(cab, {"HPF": 20., "LPF": 9000.}), block(eq, {"HPF": 85., "LPF": 20001.})]
        second = [block(cab, {"HPF": 120., "LPF": 20000.}), block(eq, {"HPF": 19., "LPF": 6000.})]
        source = chain(rows=[first, second])
        # Tightness reaches the low cut, Texture the low-pass (it also moves the cab microphones).
        low = {(edit["row"], edit["column"]) for edit in knobs.moves(source, {"tightness": .5})
               if edit["name"] in ("HPF", "HPF FREQ")}
        self.assertEqual(low, {(0, 1), (1, 0)})
        high = {(edit["row"], edit["column"]) for edit in knobs.moves(source, {"texture": -.5})
                if edit["name"] in ("LPF", "LPF FREQ")}
        self.assertEqual(high, {(0, 0), (1, 1)})

    def test_a_filter_parked_outside_the_audible_span_starts_from_its_edge(self):
        edits = knobs.moves(chain(block(4005, {"LPF": 20001.})), {"texture": -1.})
        self.assertAlmostEqual(edits[0]["to"], 14000 * 2 ** -1.5, places=2)

    def test_a_bypassed_filter_owns_nothing(self):
        source = chain(block(12006, {"HPF": 20.}), replace_bypass(block(4005, {"HPF": 85.})))
        self.assertEqual({edit["column"] for edit in knobs.moves(source, {"tightness": .5})
                          if edit["name"] in ("HPF", "HPF FREQ")}, {0})


def replace_bypass(found, flag=True):
    from dataclasses import replace
    return replace(found, bypass=flag)


class EffectsTest(EvenAmps):
    def test_secondary_delay_knob_auditions_a_dry_delay_and_respects_an_explicit_cut(self):
        source = chain(block(6011, {"MIX": 0.}))
        self.assertEqual(knobs.moves(source, {"tail": 0}), [])
        edits = {edit["name"]: edit["to"] for edit in knobs.moves(source, {"tail": .5})}
        self.assertAlmostEqual(edits["MIX"], 8.)
        self.assertAlmostEqual(edits["FEEDBACK"], 37.5)
        cut = knobs.moves(source, {"tail": .5, "delay": -1.})
        self.assertEqual([edit["name"] for edit in cut], ["FEEDBACK"])
        self.assertEqual(source.block(0, 0).get(0), 0.)

    def test_a_synced_delay_keeps_its_time(self):
        synced = chain(block(6011, {"SYNC": 1.}))
        self.assertEqual(knobs.moves(synced, {"delay_time": .5}), [])
        free = chain(block(6011, {"SYNC": 0.}))
        self.assertEqual([edit["name"] for edit in knobs.moves(free, {"delay_time": .5})], ["DELAY TIME"])

    def test_a_bypassed_block_is_left_alone(self):
        self.assertEqual(knobs.moves(chain(replace_bypass(block(6011))), {"delay": .5}), [])


class DriveTravelTest(unittest.TestCase):
    FUZZ, HIWATT = 22, 1063

    def setUp(self):
        for name, value in (("_scale", 1.), ("_effect", {"high": {"level": 1.5}})):
            patch = mock.patch.object(knobs, name, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_full_right_is_the_top_of_every_gain_control_and_a_small_turn_stays_small(self):
        source = chain(block(self.FUZZ, {"FUZZ": 7.6, "VOLUME": 5.3}), block(self.HIWATT, {"GAIN": 5.4}))
        full = moves(source, {"drive": 1.})
        self.assertEqual((full["Facial Fuzz", "FUZZ"], full["Watt D103 Normal", "GAIN"]), (2.4, 4.6))
        small = moves(source, {"drive": .3})
        self.assertEqual((small["Facial Fuzz", "FUZZ"], small["Watt D103 Normal", "GAIN"]), (.22, .41))
        self.assertLess(moves(source, {"drive": .5})["Facial Fuzz", "FUZZ"], 1.)
        self.assertNotIn(("Facial Fuzz", "VOLUME"), full)

    def test_the_level_an_amps_gain_adds_is_taken_back_at_its_output(self):
        source = chain(block(self.HIWATT, {"GAIN": 5.4}))
        self.assertEqual(moves(source, {"drive": 1.})["Watt D103 Normal", "OUTPUT"], -6.9)
        self.assertEqual(moves(source, {"drive": .3})["Watt D103 Normal", "OUTPUT"], -.62)
        self.assertNotIn(("Watt D103 Normal", "OUTPUT"), moves(source, {"drive": -.5}))
        with mock.patch.object(knobs, "_effect", return_value=None):
            self.assertNotIn(("Watt D103 Normal", "OUTPUT"), moves(source, {"drive": 1.}))

    def test_a_control_already_at_its_top_has_nowhere_to_go(self):
        self.assertEqual(moves(chain(block(self.FUZZ, {"FUZZ": 10.})), {"drive": 1.}), {})

    def test_less_drive_brings_a_pedal_level_back_to_unity_and_the_bottom_bypasses_it(self):
        source = chain(block(5, {"OVERDRIVE": 6., "LEVEL": 8.}))
        half = moves(source, {"drive": -.5})
        self.assertEqual(half["Green 808", "LEVEL"], -1.25)
        self.assertEqual(half["Green 808", "OVERDRIVE"], -3.)
        self.assertNotIn((0, 0, None), knobs.targets(source, {"drive": -.5}))
        bottom = knobs.targets(source, {"drive": -1.})
        self.assertIs(bottom[0, 0, None], True)
        self.assertIs(knobs.apply(source, {"drive": -1.}).block(0, 0).bypass, True)

    def test_a_pedal_bypassed_by_scene_is_bypassed_only_in_the_named_scene(self):
        source = chain(replace_bypass(block(5), (False, True) + (False,) * 6))
        # Without a scene the knob is turned on the whole sound: the pedal goes off in every scene.
        self.assertEqual(knobs.targets(source, {"drive": -1.})[0, 0, None], (True,) * 8)
        flags = knobs.targets(source, {"drive": -1.}, scene=2)[0, 0, None]
        self.assertEqual(flags, (False, True, True) + (False,) * 5)


class MeasuredStrengthTest(unittest.TestCase):
    def test_a_weak_tone_control_is_turned_further(self):
        source = chain(block(1011))
        with mock.patch.object(knobs, "_effect", return_value=None):
            with mock.patch.object(knobs, "_scale", return_value=1.):
                plain = moves(source, {"brightness": -.5})["UK C30 TopBoost", "TREBLE"]
            asked = []

            def scale(model_id, name, rising=True):
                asked.append((model_id, name, rising))
                return 2. if name == "TREBLE" else 1.

            with mock.patch.object(knobs, "_scale", scale):
                scaled = moves(source, {"brightness": -.5})
        self.assertEqual(scaled["UK C30 TopBoost", "TREBLE"], 2 * plain)
        self.assertIn((1011, "TREBLE", False), asked)

    def test_the_scale_comes_from_the_measured_table_and_leaves_mids_alone(self):
        table = {"typical": {"treble": .6, "mid": .2},
                 "amps": {"1": {"knobs": {"TREBLE": {"role": "treble", "sign": 1,
                                                     "high": {"bands": [0, 0, 0, .3, 0]},
                                                     "low": {"bands": [0, 0, 0, .01, 0]}},
                                          "MID": {"role": "mid", "sign": 1, "high": {"bands": [0, .1, .1, 0, 0]}}}}}}
        with mock.patch.object(knobs.knowledge, "table", return_value=table):
            self.assertEqual(knobs._scale(1, "TREBLE"), 2.)
            self.assertEqual(knobs._scale(1, "TREBLE", rising=False), 1.)  # too weak to wake
            self.assertEqual(knobs._scale(1, "MID"), 1.)
            self.assertEqual(knobs._scale(2, "TREBLE"), 1.)


class AvailableTest(unittest.TestCase):
    def test_a_design_reports_the_controls_its_chain_can_move_before_it_is_built(self):
        # Stone Pulse, as a design leaves it: only what it means to change is stated.
        source = chain(block(5005, {"MIX": 0.}, every=False), block(1081, {"GAIN": 2.3}, every=False),
                       Block(12006), block(4005, {"HPF": 85.}, every=False),
                       block(7004, {"RATE": 3.6}, every=False), block(8000, {"MIX": 9.}, every=False))
        found = knobs.controls(source)
        self.assertIn(("Brit Plexi 50 Normal", "Gain"), found["drive"])
        self.assertIn(("Graphic-9", "Hpf"), found["tightness"])
        self.assertIn(("412 Brit 60B GB 71 (M)", "Lpf"), found["texture"])
        self.assertIn(("412 Brit 60B GB 71 (M)", "Position"), found["brightness"])
        self.assertIn(("Tremolo", "Rate"), found["motion"])
        self.assertNotIn("gate", found)
        self.assertNotIn("delay", found)
        self.assertEqual(knobs.available(chain(Block(16005))), [])

    def test_a_guitar_amp_offers_its_tone_knobs(self):
        found = knobs.available(chain(block(1093)))
        self.assertTrue({"drive", "brightness", "texture", "body", "mids", "presence", "tightness", "sustain"} <= set(found))
        self.assertFalse({"delay", "reverb", "gate"} & set(found))

    def test_a_bass_amp_offers_drive_through_whatever_it_calls_its_gain(self):
        found = knobs.controls(chain(block(2011)))
        self.assertEqual(list(found), ["drive"])
        self.assertEqual({name for _, name in found["drive"]}, {"Gain 1", "Gain 2"})
        self.assertEqual(knobs.available(chain(block(2010))), ["drive"])

    def test_a_control_at_both_ends_is_not_offered(self):
        self.assertEqual(knobs.available(chain(block(16003, {"THRESHOLD": 0.}))), ["gate"])
        self.assertEqual(knobs.controls(chain(block(16003, {"THRESHOLD": -90.})))["gate"],
                         (("Simple Gate", "Threshold"),))


class DirectionTest(unittest.TestCase):
    def test_plain_directions_become_bounded_knob_moves(self):
        for words, move in (("tighter", {"tightness": .3}), ("A bit tighter.", {"tightness": .15}),
                            ("much brighter", {"brightness": .51}), ("more jazz", {"jazz": .4}),
                            ("less delay", {"delay": -.3}), ("darker", {"brightness": -.35}),
                            ("a little less reverb", {"reverb": -.15}), ("too bright", {"brightness": -.3}),
                            ("it's too harsh", {"texture": -.3}), ("too much gain", {"drive": -.3}),
                            ("not enough mids", {"mids": .3}), ("make it warmer", {"texture": -.3}),
                            ("needs more sustain", {"sustain": .3}), ("brighter please", {"brightness": .3})):
            self.assertEqual(knobs.direction(words), move, words)
        for words in ("Less bite, keep the attack.", "the low end gets muddy when I palm mute", "tighter and brighter",
                      "more Andy Timmons", "more", "too", "John Mayer Gravity", "undo"):
            self.assertIsNone(knobs.direction(words), words)

    def test_every_knob_is_a_quality_heard_not_a_control_or_a_device(self):
        mechanisms = {"mic", "microphone", "position", "low cut", "high cut", "low-pass", "high-pass", "filter",
                      "mix", "gate", "compressor", "attack time", "rate", "time", "feedback", "drive", "repeats",
                      "modulation", "reverb", "delay", "soft top"}
        for key, (title, _, _) in knobs.KNOBS.items():
            self.assertNotIn(title.lower(), mechanisms, key)
        shown = set(knobs.PRIMARY) | {key for group in knobs.GROUPS.values() for key in group}
        self.assertEqual(shown, set(knobs.KNOBS))           # every knob has a place on the Shape panel

    def test_a_sound_shaped_with_an_old_knob_keeps_its_shape(self):
        self.assertEqual(knobs.normalize({"low_cut": .3, "tightness": .2, "soft_top": .4, "repeats": .5}),
                         {"tightness": .5, "texture": -.4, "tail": .5})
        with self.assertRaises(ValueError):
            knobs.targets(chain(block(1045)), knobs.normalize({"nonsense": .2}))

    def test_warmth_and_smoothness_carry_over_to_the_pad_the_other_way_round(self):
        # Warmth up was darker, Smoothness up was rounder: both are the new knobs turned down.
        self.assertEqual(knobs.normalize({"warmth": .3, "smoothness": .4, "drive": .2}),
                         {"brightness": -.3, "texture": -.4, "drive": .2})
        self.assertEqual(knobs.normalize({"mic": .2, "warmth": .3, "brightness": .1}), {"brightness": -.4})
        self.assertEqual(knobs.normalize({"warmth": -1., "brightness": .5}), {"brightness": 1.})
        source = chain(block(4), block(1011))
        self.assertEqual(knobs.targets(source, {"warmth": .5}), knobs.targets(source, {"brightness": -.5}))
        with self.assertRaises(ValueError):
            knobs.targets(source, {"warmth": float("nan")})

    def test_brightness_and_texture_reach_their_own_controls(self):
        source = chain(block(5), block(1093), block(12006, {"LPF": 9000.}))
        bright = {(edit["model"], edit["name"]): edit["to"] - edit["from"]
                  for edit in knobs.moves(source, {"brightness": .5})}
        rough = {(edit["model"], edit["name"]): edit["to"] - edit["from"]
                 for edit in knobs.moves(source, {"texture": .5})}
        cab = catalog.model(12006).name
        self.assertGreater(bright["Brit Plexi 100 Normal", "TREBLE"], 0)
        self.assertGreater(bright[cab, "LPF"], 0)
        self.assertLess(bright[cab, "POSITION"], 0)             # microphones toward the centre
        self.assertGreater(rough["Green 808", "OVERDRIVE"], 0)  # more high-order harmonics from the pedal
        self.assertGreater(rough["Brit Plexi 100 Normal", "PRESENCE"], 0)
        self.assertGreater(rough[cab, "LPF"], 0)
        self.assertNotIn(("Brit Plexi 100 Normal", "TREBLE"), rough)
        round_ = {(edit["model"], edit["name"]): edit["to"] - edit["from"]
                  for edit in knobs.moves(source, {"texture": -.5})}
        self.assertGreater(round_[cab, "POSITION"], 0)          # off centre
        self.assertEqual(knobs.PAD, ("brightness", "texture"))


class CheckTest(unittest.TestCase):
    BEFORE = {"brightness_oct": .1, "low_end_db": 0., "tail_db": -40., "compression": .2,
              "probe": {"distortion_db": -30., "distortion_soft_db": -40., "tristimulus3": .1}}

    def test_each_knob_is_judged_by_its_own_measurement(self):
        after = {"brightness_oct": .3, "low_end_db": -.5, "tail_db": -45., "compression": .2,
                 "probe": {"distortion_db": -29.5, "distortion_soft_db": -39., "tristimulus3": .2}}
        found = knobs.check(self.BEFORE, after, {"brightness": .5, "texture": .5, "body": .5, "space": .5,
                                                 "drive": .5, "mids": .4, "attack": .3, "sustain": 0.})
        self.assertEqual(found["brightness"], {"measure": "brightness_oct", "change": .2, "sign": 1,
                                               "verdict": "as intended", "units": "octave"})
        self.assertEqual(found["texture"]["verdict"], "as intended")       # tristimulus 3 rose
        self.assertEqual(found["body"]["verdict"], "too small")
        self.assertEqual(found["space"]["verdict"], "opposite")
        self.assertEqual(found["drive"]["verdict"], "too small")
        self.assertEqual(found["mids"]["verdict"], "not measurable")       # no measurement for it
        self.assertEqual(found["attack"]["verdict"], "not measurable")     # attack_ms in neither record
        self.assertEqual(found["attack"]["sign"], -1)
        self.assertNotIn("sustain", found)                                # not turned

    def test_a_knob_turned_down_is_meant_to_lower_its_measurement(self):
        after = {**self.BEFORE, "brightness_oct": -.1, "low_end_db": 2.}
        found = knobs.check(self.BEFORE, after, {"brightness": -.5, "tightness": .5})
        self.assertEqual((found["brightness"]["sign"], found["brightness"]["verdict"]), (-1, "as intended"))
        self.assertEqual((found["tightness"]["sign"], found["tightness"]["verdict"]), (-1, "opposite"))

    def test_a_small_turn_is_held_to_a_quarter_turn_and_missing_records_are_not_measurable(self):
        after = {**self.BEFORE, "brightness_oct": .13}
        self.assertEqual(knobs.check(self.BEFORE, after, {"brightness": .05})["brightness"]["verdict"], "too small")
        self.assertEqual(knobs.check(None, after, {"brightness": .5})["brightness"]["verdict"], "not measurable")
        self.assertEqual(knobs.check(self.BEFORE, {"brightness_oct": None}, {"brightness": .5})["brightness"]["verdict"],
                         "not measurable")
        self.assertEqual(set(knobs.EXPECTED) <= set(knobs.KNOBS), True)


if __name__ == "__main__":
    unittest.main()
