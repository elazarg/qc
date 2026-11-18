from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from tone_lab import audio, capture, device, listening, session


class ListeningSessionTest(unittest.TestCase):
    def test_routes_reference_and_revision_without_exposing_tools(self):
        self.assertEqual(listening.route("Use my Plini preset as a reference.", has_draft=True), ("reference", "Plini"))
        self.assertEqual(listening.route("Make me a Lenny Kravitz preset.", has_draft=True)[0], "create")
        self.assertEqual(listening.route("Heavy metal, Pantera style", has_draft=True)[0], "create")
        self.assertEqual(listening.route("Andy Timmons, but not naive. A real complex signal chain", has_draft=True)[0], "create")
        self.assertEqual(listening.route("More Andy Timmons than Andy Timmons", has_draft=True)[0], "refine")
        self.assertEqual(listening.route("Less bite, keep the attack.", has_draft=True)[0], "refine")
        self.assertEqual(listening.route("Less bite on my current QC sound.", has_draft=True)[0], "adjust-live")
        self.assertEqual(listening.route("Match the reference.", has_draft=True)[0], "match")
        self.assertEqual(listening.route("Hear the draft.", has_draft=True)[0], "draft")
        self.assertEqual(listening.route("Keep this, the attack feels right.", has_draft=True),
                         ("keep", "the attack feels right"))

    def test_switch_uses_same_cursor_across_chunks_and_loop_boundary(self):
        a = np.broadcast_to(np.arange(12, dtype=np.float32)[:, None], (12, 2))
        b = a + 100
        loop = listening.Loop((a, b), crossfade=4)
        first = np.empty((5, 2), dtype=np.float32)
        loop.process(first)
        self.assertEqual(first[:, 0].tolist(), [0, 1, 2, 3, 4])
        loop.selected = 1
        next_chunk = np.empty((9, 2), dtype=np.float32)
        loop.process(next_chunk)
        self.assertEqual(next_chunk[:, 0].tolist(), [30, 56, 82, 108, 109, 110, 111, 100, 101])
        self.assertEqual(loop.cursor, 2)
        self.assertEqual(loop.current, 1)

    def test_captured_matching_can_toggle_without_restarting_the_loop(self):
        track = np.tile(np.arange(1, 7, dtype=np.float32)[:, None], (1, 2))
        loop = listening.Loop((track,), gains=(.5,))
        chunk = np.empty((2, 2), dtype=np.float32)
        loop.process(chunk)
        self.assertEqual(chunk[:, 0].tolist(), [.5, 1])
        loop.process(chunk, match=False)
        self.assertEqual(chunk[:, 0].tolist(), [3, 4])
        self.assertEqual(loop.cursor, 4)

    def test_common_level_and_disagreement_between_performances(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            time = np.arange(audio.RATE, dtype=np.float32) / audio.RATE
            wave = (.1 * np.sin(2 * np.pi * 220 * time))[:, None]
            a = listening.Sound("Recorded", root / "a.wav", "phrase", (0, 1))
            b = listening.Sound("Draft", root / "b.wav", "phrase", (0, 1))
            c = listening.Sound("Reference", root / "b.wav", "different", (0, 1))
            audio.write_wave(a.path, wave)
            audio.write_wave(b.path, wave * 2)
            first, second = listening.prepare((a, b))
            np.testing.assert_allclose(first, second, atol=1e-7)
            with self.assertRaisesRegex(ValueError, "different performances"):
                listening.prepare((a, c))
            self.assertEqual(listening.Sound.from_record(a.to_record()), a)
            with patch.object(session, "SESSIONS", root):
                verdict = listening.keep(b, None, "Reference", a)
                self.assertTrue(verdict.is_file())

    def test_failed_reamp_reports_quiet_alternative_and_leaves_previous_pair_available(self):
        with TemporaryDirectory() as folder, patch.object(session, "SESSIONS", Path(folder)):
            root = Path(folder)
            take = listening.Performance(root / "phrase", {"wet": str(root / "wet.wav"), "duration_seconds": 1})
            time = np.arange(audio.RATE, dtype=np.float32) / audio.RATE
            wet = (.1 * np.sin(2 * np.pi * 220 * time))[:, None]
            original = ({"name": "Recorded baseline", "audition_role": "original"}, wet)
            for name, new_wet in (("audible", wet * .8), ("quiet", wet * 1e-4)):
                render = root / name
                render.mkdir()
                manifest = {"kind": "knobs", "source_clip": take.folder.name,
                            "selection_seconds": take.section, "candidates": []}
                session.make_shortlist(render, manifest, [original, ({"name": name}, new_wet)], None)

            with self.assertRaisesRegex(ValueError, "silent or too quiet"):
                listening.comparison(root / "quiet", take, required=True)
            self.assertIsNone(listening.comparison(root / "quiet", take))
            original, alternative = listening.latest_comparison(take)
            self.assertEqual(alternative.name, "audible")
            self.assertTrue(original.paired_with(alternative))

    def test_comparison_uses_sample_boundaries_and_requires_a_distinct_alternative(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            take = listening.Performance(root / "phrase", {"wet": str(root / "wet.wav"), "duration_seconds": 1})
            for filename in ("original.wav", "alternative.wav"):
                (root / filename).touch()
            manifest = {"source_clip": take.folder.name, "selection_seconds": [0, 1 + .1 / audio.RATE],
                        "candidates": [{"file": "original.wav", "audition_role": "original"},
                                       {"file": "original.wav", "name": "Duplicate entry"}],
                        "auditions": {"B": {"render": "original.wav"}}}
            session.write_json(root / "session.json", manifest)
            with self.assertRaisesRegex(ValueError, "playable alternative"):
                listening.comparison(root, take, required=True)

            manifest["candidates"].append({"file": "alternative.wav"})
            manifest["auditions"]["A"] = {"render": "alternative.wav"}
            session.write_json(root / "session.json", manifest)
            original, alternative = listening.comparison(root, take, required=True)
            self.assertEqual(original.path.name, "original.wav")
            self.assertEqual(alternative.path.name, "alternative.wav")
            previous = listening.Sound("Cached", alternative.path, take.folder.name, tuple(manifest["selection_seconds"]))
            self.assertTrue(alternative.paired_with(previous))

            manifest["selection_seconds"][1] += 1 / audio.RATE
            session.write_json(root / "session.json", manifest)
            with self.assertRaisesRegex(ValueError, "different part"):
                listening.comparison(root, take, required=True)

    def test_editor_active_does_not_open_hid(self):
        with patch.object(device, "editor_running", return_value=True), patch.object(device, "QuadCortex") as qc:
            for write in (False, True):
                with self.assertRaises(device.EditorActive):
                    with device.connect(write=write):
                        self.fail("Editor control should take precedence.")
            qc.assert_not_called()

    def test_editor_capture_requires_clear_stereo_and_one_quiet_input(self):
        frames = np.ones((audio.RATE, 4), dtype=np.float32) * .02
        quiet_input = frames.copy()
        quiet_input[:, 1] = 0
        self.assertEqual(capture.infer_editor_pair(quiet_input, 1), (1, 3, 4))
        self.assertEqual(capture.infer_editor_pair(quiet_input[:, [2, 3, 0, 1]], 1), (3, 1, 2))
        self.assertIsNone(capture.infer_editor_pair(frames, 1))
        quiet_input[:, 3] = 0
        self.assertIsNone(capture.infer_editor_pair(quiet_input, 1))


if __name__ == "__main__":
    unittest.main()
