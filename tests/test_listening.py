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
        self.assertEqual(listening.route("Less bite, keep the attack.", has_draft=True)[0], "refine")
        self.assertEqual(listening.route("Less bite on my current QC sound.", has_draft=True)[0], "adjust-live")
        self.assertEqual(listening.route("Match the reference.", has_draft=True)[0], "match")
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
