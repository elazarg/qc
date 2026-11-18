import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import numpy as np
from qc_mcp import leveling, preset

from tone_lab import audio, live_levels, session


def grid():
    value = preset.BinaryPreset()
    for db, silent in ((0, False), (2, False), (-4, False), (-2, True)):
        p = value.chains.add().output_control.add()
        p.params.add(scene_mode=True).param_values.add().float_value = leveling.norm_of(0)
        p.params[0].param_values.add().float_value = leveling.norm_of(db)
        p.params.add()
        p.params.add().param_values.add().float_value = float(silent)
    return value


class LiveLevelsTest(unittest.TestCase):
    def test_loudness_matching_only_uses_two_measurable_tracks_and_preserves_headroom(self):
        a = {"clipped_samples": 0, "lufs": -17., "peak_dbfs": -2.}
        b = {"clipped_samples": 0, "lufs": -24., "peak_dbfs": -8.}
        self.assertEqual(live_levels.matched_pair({"a": a}), {})
        trims = live_levels.matched_pair({"a": a, "b": b})
        self.assertAlmostEqual(trims["a"], -7.)
        self.assertAlmostEqual(trims["b"], 0.)
        b["clipped_samples"] = 1
        self.assertEqual(live_levels.matched_pair({"a": a, "b": b}), {})

    def test_native_trim_preserves_scene_baseline_parallel_routing_and_existing_mute(self):
        routes = [(1, 16), (0, 19), (0, 19), (0, 19)]
        levels = live_levels.Levels({"master_db": -6, "match": False})
        levels.select({"tone_id": "b"}, {}, "phrase")
        levels.bind(grid(), 1, routes)
        self.assertEqual(set(levels.lanes), {1, 2, 3})
        qc, verify, events = Mock(), Mock(), Mock()
        levels.apply(qc, verify, events)
        targets = {row: round(leveling.db_of(value), 2)
                   for row, field, index, value in (call.args for call in qc.set_lane_param.call_args_list)
                   if index == leveling.VOLUME}
        self.assertEqual(targets, {1: -4., 2: -10., 3: -8.})
        qc.set_lane_param.reset_mock()
        levels.configure({"master_db": -6, "match": False, "muted": True})
        levels.apply(qc, verify, events)
        self.assertEqual([call.args[0] for call in qc.set_lane_param.call_args_list], [1, 2])
        self.assertTrue(all(call.args[2:] == (leveling.MUTE, 1.) for call in qc.set_lane_param.call_args_list))
        qc.set_lane_param.reset_mock()
        levels.configure({"master_db": -6, "match": False, "muted": False})
        levels.apply(qc, verify, events)
        self.assertEqual([call.args[0] for call in qc.set_lane_param.call_args_list], [1, 2])
        self.assertTrue(all(call.args[2:] == (leveling.MUTE, 0.) for call in qc.set_lane_param.call_args_list))

    def test_dynamic_match_waits_for_both_exact_shapes_from_the_same_phrase(self):
        levels = live_levels.Levels({})
        levels.bind(grid(), 1, [(1, 0), (0, 19), (0, 0), (0, 0)])
        cursor = audio.PhraseCursor(returns=True)
        meters = {"loop_frames": audio.RATE}
        angles = np.arange(audio.RATE * 2, dtype=np.float32) * (2 * np.pi * 240 / audio.RATE)
        a, b = {"tone_id": "a"}, {"tone_id": "b", "scene": 1}

        def capture(tone, amplitude, master):
            levels.select(tone, {}, "phrase")
            levels.gain = master
            self.assertFalse(levels.observe(cursor, meters))
            cursor.level_probe.write(np.repeat((np.sin(angles) * amplitude)[:, None], 2, axis=1))
            self.assertTrue(levels.observe(cursor, meters))

        capture(a, .15, -6.)
        self.assertFalse(levels.match_for_take()[0])
        capture(b, .15, 0.)
        trims, basis = levels.match_for_take()
        self.assertEqual(basis, "phrase")
        self.assertAlmostEqual(trims["a"] - trims["b"], -6.)
        levels.select(b, {"drive": .5}, "phrase")
        self.assertFalse(levels.match_for_take()[0])
        levels.select(b, {}, "another phrase")
        self.assertFalse(levels.match_for_take()[0])

    def test_remembered_draft_pair_requires_exact_shapes_firmware_and_take(self):
        a = {"tone_id": "a", "draft": {"id": "clean"}, "values": {"drive": .2}}
        b = {"tone_id": "b", "draft": {"id": "lead"}, "values": {}}
        reading = {"clipped_samples": 0, "lufs": -24., "peak_dbfs": -8.}
        with TemporaryDirectory() as folder, patch.object(session, "SESSIONS", Path(folder)):
            root = Path(folder)
            (root / "clip.json").write_text("{}", encoding="utf-8")
            (root / "dry.wav").write_bytes(b"stamped take")
            take = str(root)
            seed = {live_levels.remembered_key(tone, tone["values"], take, "4.1.1"): reading for tone in (a, b)}
            live_levels.save_memory(seed)

            def loaded(opposite, firmware):
                levels = live_levels.Levels({"contexts": {"b": opposite}}, firmware=firmware)
                levels.select(a, a["values"], take)
                return levels.match_for_take()

            self.assertEqual(loaded(b, "4.1.1"), ({"a": 0., "b": 0.}, "prior"))
            self.assertFalse(loaded({**b, "values": {"drive": .4}}, "4.1.1")[0])
            self.assertFalse(loaded(b, "4.2.0")[0])
            (root / "dry.wav").write_bytes(b"updated take on disk")
            self.assertFalse(loaded(b, "4.1.1")[0])


if __name__ == "__main__":
    unittest.main()
