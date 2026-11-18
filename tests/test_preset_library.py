from contextlib import ExitStack, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from qc_mcp import preset as wire, protocol

from tone_lab import audio, device, iterate, preset_library, session, tone_search


class PresetLibraryTest(unittest.TestCase):
    def setUp(self):
        scope = ExitStack()
        self.addCleanup(scope.close)
        self.root = Path(scope.enter_context(TemporaryDirectory()))
        scope.enter_context(patch.object(session, "SESSIONS", self.root))
        self.base = preset_library.SavedPreset("My baseline", "My Presets", iterate.SCRATCH["folder_key"], 5)
        self.choice = preset_library.SavedPreset("My reference", "Band setlist", "1_q", 19)
        self.position = self.base.position
        self.scene = 4
        self.dirty = False
        self.presets = {self.base.name: self.preset(self.base.name), self.choice.name: self.preset(self.choice.name)}
        self.recalled = []
        self.qc = SimpleNamespace(firmware="4.1.1", get_setlist_position=lambda: self.position,
                                  read_state=lambda _: SimpleNamespace(action=protocol.ACTION["UPDATE"],
                                                                       is_dirty=self.dirty))
        scope.enter_context(patch.object(device, "connect", side_effect=lambda **_: nullcontext(self.qc)))
        scope.enter_context(patch.object(device, "read_preset", return_value=self.presets[self.base.name]))
        scope.enter_context(patch.object(device, "active_scene", side_effect=lambda _: self.scene))
        scope.enter_context(patch.object(device, "select_scene", side_effect=self.select_scene))
        scope.enter_context(patch.object(iterate, "recall", side_effect=self.recall))
        scope.enter_context(patch.object(iterate, "prepare_fixture", side_effect=self.prepare_fixture))
        scope.enter_context(patch.object(iterate, "verify_fixture"))
        scope.enter_context(patch.object(iterate, "calibration_offset", return_value=10))

    @staticmethod
    def preset(name):
        return wire.build({"name": name, "default_scene": 1,
                           "chains": [{"in_portid": 1, "out_portid": 19}, {}, {}, {}]})

    def select_scene(self, _qc, scene):
        self.scene = scene

    def recall(self, _qc, position, name):
        self.recalled.append(name)
        self.position, self.scene = position, 0
        return self.presets[name]

    def prepare_fixture(self, _qc, _position, _source, physical_input):
        self.assertEqual(physical_input, 1)
        self.position, self.scene = iterate.SCRATCH, 0

    def take(self):
        folder = self.root / "recordings" / "clip-01"
        folder.mkdir(parents=True)
        wave = (.15 * np.sin(2 * np.pi * 155 * np.arange(audio.RATE) / audio.RATE)).astype(np.float32)
        self.wet = np.column_stack((wave, wave))
        audio.write_wave(folder / "dry.wav", wave[:, None])
        audio.write_wave(folder / "wet.wav", self.wet)
        session.write_json(folder / "clip.json", {"duration_seconds": 1, "physical_input": 2,
                                                 "selected_passage_seconds": [.125, .875]})
        return folder

    def test_directory_uses_saved_slot_addresses_and_hides_fixtures(self):
        def folder(key, files, *, is_factory=False, is_downloads=False):
            return {"key": key, "name": "Setlist", "is_factory": is_factory, "is_downloads": is_downloads,
                    "files": [{"index": index, "name": name} for index, name in files]}

        rows = preset_library.saved_presets({"presets": [
            folder(self.base.folder_key, [(0, ""), (5, "Named"), (254, "Tone Lab Style"), (255, "Tone Lab Vai")]),
            folder("1_q", [(19, "Another")]), folder("cloud-0-1", [(0, "Cloud")], is_downloads=True),
            folder("/opt/neuraldsp/Plugins/User", [(0, "Plugin")]), folder("2_q", [(0, "Factory")], is_factory=True),
        ]})
        self.assertEqual([(row.name, row.slot) for row in rows], [("Another", "3D"), ("Named", "1F")])
        self.assertEqual(rows[0].position, self.choice.position)

    def test_load_refuses_to_discard_an_unsaved_grid(self):
        self.dirty = True
        with self.assertRaisesRegex(RuntimeError, "Save or discard"):
            preset_library.load_preset(self.choice)
        self.assertEqual(self.recalled, [])
        self.assertEqual(self.position, self.base.position)

    def test_reference_links_the_same_passage_and_restores_preset_scene(self):
        take = self.take()
        selection = slice(audio.RATE // 8, 7 * audio.RATE // 8)

        def reamp(dry):
            self.assertEqual(self.position, iterate.SCRATCH)
            self.assertEqual(self.scene, 1)
            self.assertEqual(dry.shape, (3 * audio.RATE // 4, 1))
            return np.pad(self.wet[selection], ((10, 12), (0, 0)))

        with patch.object(audio, "reamp", side_effect=reamp):
            result = preset_library.render_reference(self.choice, take)
        self.assertEqual((self.position, self.scene), (self.base.position, 4))
        self.assertEqual(result.scene, "B")
        np.testing.assert_array_equal(audio.read_wave(result.path), self.wet[selection])
        self.assertEqual(tone_search.search(reference=result.path, same_take_only=True)[0]["take_id"], take.name)

    def test_reference_restores_preset_scene_when_audio_fails(self):
        with patch.object(audio, "reamp", side_effect=RuntimeError("Audio interface unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Audio interface unavailable"):
                preset_library.render_reference(self.choice, self.take(), scene=3)
        self.assertEqual(self.recalled, [self.choice.name, self.base.name])
        self.assertEqual((self.position, self.scene), (self.base.position, 4))
        self.assertEqual(list(self.root.glob("*-preset-reference-*")), [])
