"""POD Go presets as starting points: reading a backup, matching through the real gear, carrying values."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import struct
import unittest
import zlib

from lab.sound import catalog
from lab.gear import podgo
from lab.sound.chain import SCENES

GREEN_808, JTM45_NORMAL, TWIN_VIBRATO = "Green 808", "Brit TM45 Normal", "US TWN Vibrato"


def write_backup(folder, setlists):
    """A .pgb in the layout POD Go Edit writes: a header, zlib folders, and an index of entries at the end."""
    payloads = []
    for number, (name, presets) in enumerate(setlists.items()):
        folder_json = {"meta": {"name": name},
                       "data": {"presets": [None if preset is None else {"meta": {"name": preset[0]}, "tone": preset[1]}
                                            for preset in presets]}}
        payloads.append((f"SL{number:02}", json.dumps(folder_json).encode("utf-8")))
    payloads.append(("SLNM", b"not json"))
    body, entries, offset = b"", b"", 48
    for tag, data in payloads:
        packed = zlib.compress(data)
        entries += struct.pack("<4sQQIQ4x", tag.encode("ascii")[::-1], offset + len(body), len(packed), 1, len(data))
        body += packed
    index = 48 + len(body)
    raw = b"AF6L\x01\0\0\0" + struct.pack("<I", index) + b"\0" * 36 + body + entries
    path = Path(folder) / "backup.pgb"
    path.write_bytes(raw)
    return path


def snapshot(name, flags, values=None, valid=True):
    return {"@name": name, "@valid": valid, "blocks": {"dsp0": flags},
            "controllers": {"dsp0": values or {}}}


def time_preset():
    """A JTM-45 preset: a Scream 808, the amp with its gain on snapshots, a cab, a delay off in snapshot 2."""
    blocks = {
        "block0": {"@model": "HD2_VolPanVolStereo", "@enabled": True, "@position": 0, "Pedal": 1.0},
        "block1": {"@model": "HD2_DistScream808Mono", "@enabled": True, "@position": 1, "Gain": 0.5, "Tone": 0.6,
                   "Level": 0.0},
        "block2": {"@model": "HD2_AmpBritJ45Nrm", "@enabled": True, "@position": 2, "Drive": 0.5, "Bass": 0.38,
                   "Mid": 0.91, "Treble": 0.65, "Presence": 0.57, "Master": 1.0, "ChVol": 0.5},
        "block3": {"@model": "HD2_Cab4x12Greenback25", "@enabled": True, "@position": 3, "LowCut": 80.0,
                   "HighCut": 8000.0, "Distance": 2.0},
        "block4": {"@model": "HD2_DelaySimpleDelayStereo", "@enabled": True, "@position": 4, "Time": 0.35,
                   "Feedback": 0.33, "Mix": 0.32, "TempoSync1": False},
        "block5": {"@model": "HD2_ReverbPlateStereo", "@enabled": True, "@position": 5, "Mix": 0.25,
                   "Predelay": 0.02, "Decay": 0.5},
    }
    on = {f"block{i}": True for i in range(6)}
    return {
        "dsp0": {**blocks, "input": {"@model": "P34_AppDSPFlowInput"}, "output": {"@model": "P34_AppDSPFlowOutput"}},
        "dsp1": {},
        "controller": {"dsp0": {"block0": {"Pedal": {"@controller": 1}},
                                "block2": {"Drive": {"@controller": podgo.SNAPSHOT_CONTROLLER}}}},
        "snapshot0": snapshot("Rhythm", on, {"block2": {"Drive": {"@value": 0.5}}}),
        "snapshot1": snapshot("Lead", {**on, "block4": False}, {"block2": {"Drive": {"@value": 0.8}}}),
        "snapshot2": snapshot("SNAPSHOT 3", on, valid=False),
        "snapshot3": snapshot("SNAPSHOT 4", on, valid=False),
    }


def litigator_preset():
    return {"dsp0": {"block0": {"@model": "HD2_AmpLine6Litigator", "@enabled": True, "Drive": 0.4},
                     "block1": {"@model": "HD2_ImpulseResponse1024Mono", "@enabled": True, "Index": 12, "Mix": 1.0}},
            "snapshot0": snapshot("SNAPSHOT 1", {"block0": True, "block1": True})}


def ir_preset():
    return {"dsp0": {"block0": {"@model": "HD2_AmpUSDoubleVib", "@enabled": True, "Drive": 0.3, "Bass": 0.5},
                     "block1": {"@model": "HD2_ImpulseResponse1024Mono", "@enabled": True, "Index": 3, "Mix": 1.0}},
            "snapshot0": snapshot("SNAPSHOT 1", {"block0": True, "block1": True})}


class Backup(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.path = write_backup(self.folder.name, {
            "User": [("Time", time_preset()), None, ("Litigator", litigator_preset()), ("CH Twin", ir_preset())],
            "Factory": [("Factory One", ir_preset())]})

    def tearDown(self):
        self.folder.cleanup()

    def test_reads_setlists_with_slots_and_skips_empty_ones(self):
        found = podgo.read_backup(self.path)["setlists"]
        self.assertEqual(set(found), {"User", "Factory"})
        self.assertEqual([(p.slot, p.name) for p in found["User"]], [("01A", "Time"), ("01C", "Litigator"),
                                                                     ("01D", "CH Twin")])

    def test_rejects_a_file_that_is_not_a_backup(self):
        bad = Path(self.folder.name) / "bad.pgb"
        bad.write_bytes(b"PK\x03\x04" + b"\0" * 60)
        with self.assertRaises(ValueError):
            podgo.read_backup(bad)

    def test_a_preset_of_known_gear_becomes_a_sound(self):
        item = podgo.import_backup(self.path)[0]
        self.assertTrue(item.confident, item.problems)
        sound = item.sound
        self.assertEqual(sound.origin, {"kind": "imported", "from": "POD Go", "slot": "01A", "name": "Time"})
        names = [block.name for _, _, block in sound.chain.blocks]
        self.assertEqual(names[:2], [GREEN_808, JTM45_NORMAL])
        self.assertTrue(catalog.model(sound.chain.block(0, 2).model).is_stock_cab)
        self.assertEqual(sound.chain.routes[0], (podgo.INPUT, podgo.MULTI_OUT))
        self.assertNotIn("Volume", " ".join(names))           # the volume pedal has no sound of its own here

    def test_values_are_carried_where_the_controls_correspond(self):
        chain = podgo.import_backup(self.path)[0].sound.chain
        drive, amp, cab, delay, reverb = (chain.block(0, column) for column in range(5))
        self.assertAlmostEqual(drive.display(0), 5.0, places=1)              # OVERDRIVE from Gain 0.5
        gain = amp.info.index("GAIN")
        self.assertAlmostEqual(amp.display(gain, 0), 5.0, places=1)          # snapshot 1
        self.assertAlmostEqual(amp.display(gain, 1), 8.0, places=1)          # snapshot 2
        self.assertAlmostEqual(amp.display(amp.info.index("MID")), 9.1, places=1)
        self.assertAlmostEqual(cab.display(cab.info.index("HPF")), 80, delta=1)
        self.assertAlmostEqual(delay.display(delay.info.index("DELAY TIME")), 350, delta=1)
        self.assertEqual(delay.display(delay.info.index("SYNC")), 0)
        self.assertAlmostEqual(delay.display(delay.info.index("FEEDBACK")), 33, delta=.5)
        self.assertAlmostEqual(reverb.display(reverb.info.index("MIX")), 25, delta=.5)

    def test_snapshots_become_scenes(self):
        chain = podgo.import_backup(self.path)[0].sound.chain
        self.assertEqual(chain.labels[:3], ("Rhythm", "Lead", ""))
        delay = chain.block(0, 3)
        self.assertEqual(len(delay.bypass), SCENES)
        self.assertEqual(delay.bypass[:2], (False, True))

    def test_a_line6_original_amp_gives_a_request_and_no_chain(self):
        item = podgo.import_backup(self.path)[1]
        self.assertFalse(item.confident)
        self.assertIsNone(item.sound)
        self.assertIn("Line 6 Litigator", item.request)
        self.assertIn("a Line 6 original", item.request)
        self.assertIn("\"Litigator\" (User 01C)", item.request)
        self.assertTrue(any("Litigator" in problem for problem in item.problems))

    def test_an_impulse_response_takes_the_cab_paired_with_the_amp(self):
        item = podgo.import_backup(self.path)[2]
        self.assertTrue(item.confident, item.problems)
        amp, cab = item.sound.chain.block(0, 0), item.sound.chain.block(0, 1)
        self.assertEqual(amp.name, TWIN_VIBRATO)
        self.assertTrue(cab.info.is_stock_cab)
        ir = next(piece for piece in item.pieces if piece.category == "ir")
        self.assertEqual(ir.relation, "kind")
        self.assertIn("impulse response", item.description)
        self.assertEqual(item.sound.tags, ["POD Go"])       # no instruments given: none is guessed
        mine = [{"name": "Charvel", "kind": "electric"}, {"name": "Les Paul", "kind": "electric"},
                {"name": "Bass", "kind": "bass"}]
        self.assertEqual(podgo.import_backup(self.path, instruments=mine)[2].sound.tags, ["POD Go", "Charvel"])

    def test_another_setlist_can_be_imported(self):
        self.assertEqual([item.name for item in podgo.import_backup(self.path, "Factory")], ["Factory One"])
        with self.assertRaises(ValueError):
            podgo.import_backup(self.path, "Missing")

    def imported(self, *presets):
        return podgo.import_backup(write_backup(self.folder.name, {"User": list(presets)}))

    def test_one_preset_in_a_shape_nobody_expected_is_named_and_the_others_come_across(self):
        # Regression: one such preset failed the whole backup with a bare Python error and no preset's name.
        import copy

        odd = {}
        for name, change in (("no snapshot blocks", lambda tone: tone["snapshot1"].update(blocks=None)),
                             ("no controllers", lambda tone: tone.update(controller=None)),
                             ("a time in words", lambda tone: tone["dsp0"]["block4"].update(Time="fast")),
                             ("a place in words", lambda tone: tone["dsp0"]["block3"].update({"@position": "3"}))):
            odd[name] = copy.deepcopy(time_preset())
            change(odd[name])
        found = self.imported(("Good", time_preset()), *odd.items(), ("No tone", None), (None, time_preset()))
        self.assertEqual([item.name for item in found], ["Good", *odd, "No tone", "Preset 02C"])
        self.assertTrue(found[0].confident and found[-1].confident)
        for item in found[1:-1]:
            self.assertFalse(item.confident)
            self.assertEqual(item.pieces, [])
            self.assertRegex(item.problems[0], rf'User {item.slot} "{item.name}" could not be read \(\w+Error: ')

    def test_a_folder_that_cannot_be_read_says_which_and_why(self):
        # Regression: a damaged archive raised zlib's own error, which nothing caught.
        import struct

        junk = b"not compressed at all"
        raw = (b"AF6L\x01\0\0\0" + struct.pack("<I", 48 + len(junk)) + b"\0" * 36 + junk
               + struct.pack("<4sQQIQ4x", b"00LS", 48, len(junk), 1, 100))
        bad = Path(self.folder.name) / "damaged.pgb"
        bad.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "folder SL00 of the POD Go backup could not be read"):
            podgo.read_backup(bad)
        body = zlib.compress(json.dumps({"data": {"presets": []}}).encode())       # no name for the setlist
        raw = (b"AF6L\x01\0\0\0" + struct.pack("<I", 48 + len(body)) + b"\0" * 36 + body
               + struct.pack("<4sQQIQ4x", b"00LS", 48, len(body), 1, len(json.dumps({"data": {"presets": []}}))))
        bad.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "SL00.*KeyError"):
            podgo.read_backup(bad)

    def ten_blocks(self):
        tone = time_preset()
        more = {"block6": {"@model": "HD2_ChorusStereo", "@enabled": True, "@position": 6, "Mix": .5},
                "block7": {"@model": "HD2_CompressorDeluxeCompMono", "@enabled": True, "@position": 7},
                "block8": {"@model": "HD2_TremoloOpticalTremStereo", "@enabled": True, "@position": 8},
                "block9": {"@model": "HD2_EQGraphic10BandStereo", "@enabled": True, "@position": 9}}
        tone["dsp0"].update(more)
        for i in range(4):
            tone[f"snapshot{i}"]["blocks"]["dsp0"].update({key: True for key in more})
        return tone

    def test_a_block_left_out_for_room_is_said_to_be_and_is_not_in_the_explanation_as_if_it_were_there(self):
        # Regression: the explanation listed all the matched blocks, the ninth included, though a row holds eight.
        item = self.imported(("Many", self.ten_blocks()))[0]
        self.assertTrue(item.confident, item.problems)
        self.assertEqual(len(item.sound.chain.blocks), 8)
        self.assertNotIn("10 Band Graphic ->", item.sound.explanation)
        self.assertIn("Left out, as a row holds eight blocks: 10 Band Graphic.", item.sound.explanation)
        self.assertIn("10 Band Graphic: left out, as a row holds eight blocks", item.lines())

    def test_each_block_says_what_it_became_and_which_values_came_across(self):
        item = podgo.import_backup(self.path)[0]
        lines = item.lines()
        self.assertEqual(len(lines), len(item.pieces))
        amp = next(line for line in lines if line.startswith("Brit J45 Nrm"))
        self.assertIn("→ Brit TM45 Normal (same gear)", amp)
        self.assertIn("Set from the POD Go: ", amp)
        self.assertIn("MID 9.1", amp)
        self.assertIn("the rest is the Quad Cortex's own", amp)
        self.assertTrue(lines[0].startswith("Volume Pedal: left out") or "left out" in lines[0])
        self.assertIn("starting point", item.sound.explanation)
        self.assertIn("the Quad Cortex's own elsewhere", item.sound.explanation)
        refused = podgo.import_backup(self.path)[1]             # no chain: what each block matched, and no values
        self.assertTrue(any("nothing stands in" in line for line in refused.lines()))
        self.assertFalse(any("Set from the POD Go" in line for line in refused.lines()))

    def test_the_same_preset_is_the_same_sound_each_time_the_backup_is_read(self):
        # Regression: each reading gave the sound a new id, so putting it on B again made another sound of it.
        first, again = podgo.import_backup(self.path)[0].sound, podgo.import_backup(self.path)[0].sound
        self.assertEqual(first.id, again.id)
        self.assertNotEqual(first.id, podgo.import_backup(self.path)[2].sound.id)

    def test_an_effect_under_its_other_id_is_the_same_effect(self):
        # Regression: the table lists an effect as mono or as stereo, and the other id was "not in the table".
        tone = time_preset()
        tone["dsp0"]["block1"]["@model"] = "HD2_DistScream808Stereo"
        tone["dsp0"]["block4"]["@model"] = "HD2_DelaySimpleDelayMono"
        self.assertNotIn("HD2_DistScream808Stereo", podgo.research()["models"])
        item = self.imported(("Twin", tone))[0]
        self.assertTrue(item.confident, item.problems)
        self.assertEqual(item.sound.chain.block(0, 0).name, GREEN_808)
        self.assertEqual(podgo.model_info("HD2_DistScream808Stereo")["name"], "Scream 808")
        self.assertEqual(podgo.model_info("HD2_NoSuchThingStereo")["certainty"], "unknown")

    def one(self, name, *models):
        blocks = {f"block{i}": {"@model": model, "@enabled": True, "@position": i} for i, model in enumerate(models)}
        tone = {"dsp0": blocks, "snapshot0": snapshot("SNAPSHOT 1", {key: True for key in blocks})}
        return podgo.translate(podgo.Preset("User", "01A", name, tone))

    def test_a_drive_the_quad_cortex_has_no_model_of_gets_one_of_its_kind(self):
        # Regression: any such drive left the whole preset without a chain.
        item = self.one("Timmy", "HD2_DistTeemahMono", "HD2_AmpUSDoubleVib", "HD2_ImpulseResponse1024Mono")
        self.assertTrue(item.confident, item.problems)
        drive = item.pieces[0]
        self.assertEqual((drive.relation, catalog.model(drive.qc).name), ("kind", GREEN_808))
        self.assertIn("same kind", item.sound.explanation)
        bass = self.one("x", "HD2_DistObsidian7000Mono").pieces[0]
        self.assertTrue(catalog.model(bass.qc).category.startswith("Bass"))

    def test_a_cab_the_quad_cortex_has_nothing_like_takes_the_cab_paired_with_the_amp(self):
        # Regression: an impulse response got the paired stock cab, a cab without a counterpart got nothing.
        lost = next(model for model, info in podgo.research()["models"].items()
                    if info.get("category") == "cab" and podgo.find_qc(info) is None)
        item = self.one("Twin", "HD2_AmpUSDoubleVib", lost)
        self.assertTrue(item.confident, item.problems)
        cab = item.pieces[1]
        self.assertEqual(cab.relation, "kind")
        self.assertTrue(catalog.model(cab.qc).is_stock_cab)
        self.assertIn("the cab the research pairs with", cab.why)
        self.assertEqual(self.one("No amp", lost).pieces[0].relation, "none")     # nothing to pair it with


class Matching(unittest.TestCase):
    def test_same_gear_and_channel(self):
        found = podgo.find_qc({"category": "amp", "based_on": "Fender Twin Reverb (vibrato channel)", "maker": "Fender"})
        self.assertEqual((catalog.model(found[0]).name, found[1]), (TWIN_VIBRATO, "same"))

    def test_another_version_is_the_same_family(self):
        found = podgo.find_qc({"category": "amp", "based_on": "Marshall JCM-800 (50 watt)", "maker": "Marshall"})
        self.assertEqual((catalog.model(found[0]).name, found[1]), ("Brit 2203", "family"))

    def test_a_pedal_of_another_maker_is_not_matched(self):
        self.assertIsNone(podgo.find_qc({"category": "drive", "based_on": "Paul Cochrane Timmy Overdrive",
                                         "maker": "Paul Cochrane"}))

    def test_a_cab_matches_on_size_and_speakers(self):
        found = podgo.find_qc({"category": "cab", "based_on": '4x12" Park 75 G12 H30', "maker": "Park"})
        self.assertEqual(found[1], "kind")
        self.assertEqual(catalog.model(found[0]).name.split()[0], "412")

    def test_an_amp_whose_channel_line_6_does_not_name_is_not_called_the_same_gear_by_guessing(self):
        # Regression: the lowest-numbered model won: the Überschall's clean channel for Line 6's high-gain Ubersonic.
        models = podgo.research()["models"]
        uber = podgo._override("HD2_AmpGermanUbersonic")
        self.assertEqual((catalog.model(uber[0]).name, uber[1]), ("Bogna Uber Lead", "same"))
        self.assertEqual(podgo._override("HD2_AmpGermanMahadeva")[1], "family")
        panama = next(info for info in models.values() if info["name"] == "PV Panama")
        model_id, relation, why = podgo.find_qc(panama)
        self.assertEqual((catalog.model(model_id).name, relation), ("PV-505 Lead", "family"))
        self.assertIn("names no channel", why)
        named = next(info for info in models.values() if info["name"] == "Brit J45 Nrm")
        self.assertEqual(podgo.find_qc(named)[1], "same")           # a channel that is named still is

    def test_a_preset_is_for_bass_when_its_name_says_bass(self):
        # Regression: any name beginning "bas" was taken for a bass preset ("Basket Case", "Basic Clean").
        self.assertEqual([podgo.for_bass(name) for name in ("Bass Clean", "BAS: fuzz", "Slap bass", "Basket Case",
                                                            "Basic Clean", "Bassman Crunch")],
                         [True, True, True, False, False, False])

    def test_the_instrument_is_one_of_the_player_s_own_by_how_the_preset_is_named(self):
        # Regression: two instruments of one player were written into the code ("lp", "ch").
        mine = [{"name": "Stratocaster", "kind": "electric"}, {"name": "Les Paul", "kind": "electric"},
                {"name": "Jazz Bass", "kind": "bass"}]

        def instrument(name):
            return podgo._instrument(podgo.Preset("User", "01A", name, {}), [], mine)

        self.assertEqual([instrument(name) for name in ("LP Crunch", "Strat clean", "les paul lead", "Bass: slap",
                                                        "Crunch", "S")],
                         ["Les Paul", "Stratocaster", "Les Paul", "Jazz Bass", None, None])
        self.assertIsNone(podgo._instrument(podgo.Preset("User", "01A", "CH Lead", {}), []))   # no instruments, no guess
        self.assertEqual(podgo._instrument(podgo.Preset("User", "01A", "Bass", {}), []), "Bass")

    def test_research_table_is_sourced(self):
        table = podgo.research()
        allowed = {"published", "community", "line6_original", "unknown"}
        for model_id, entry in table["models"].items():
            self.assertIn(entry["certainty"], allowed, model_id)
            self.assertTrue(set(entry["sources"]) <= set(table["sources"]), model_id)
            if entry["certainty"] == "published":
                self.assertTrue(entry["based_on"], model_id)
        for source in table["sources"].values():
            self.assertTrue(source["url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
