"""POD Go presets as starting points: reading a backup, matching through the real gear, carrying values."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import struct
import unittest
import zlib

from lab import catalog, podgo
from lab.chain import SCENES

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
        self.assertEqual(item.sound.tags, ["POD Go", "Charvel"])

    def test_another_setlist_can_be_imported(self):
        self.assertEqual([item.name for item in podgo.import_backup(self.path, "Factory")], ["Factory One"])
        with self.assertRaises(ValueError):
            podgo.import_backup(self.path, "Missing")


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
