import unittest
from unittest.mock import Mock, call, patch

from qc_mcp import preset as wire

from tone_lab import iterate


class LiveFixtureTest(unittest.TestCase):
    def setUp(self):
        self.source_position = {**iterate.SCRATCH, "position": 5}
        self.source = self.preset("Original guitar preset")
        self.qc = Mock(firmware="4.1.1")
        self.qc.get_setlist_position.return_value = self.source_position
        self.recall = self.enterContext(patch.object(iterate, "recall"))
        self.enterContext(patch.object(iterate.presets, "archive"))
        self.enterContext(patch.object(iterate.time, "sleep"))

    @staticmethod
    def preset(name, *, inlet=1):
        return wire.build({"name": name, "chains": [{"in_portid": inlet, "out_portid": 19}, {}, {}, {}]})

    def directory(self, **entry):
        files = [{"index": index} for index in range(256)]
        files[iterate.LIVE_B["position"]].update(entry)
        self.qc.list_directory.return_value = {"presets": [{**iterate.LIVE_B, "key": iterate.LIVE_B["folder_key"],
                                                           "files": files}]}

    def prepare(self, mode="guitar"):
        return iterate.prepare_fixture(self.qc, self.source_position, self.source, 1,
                                       position=iterate.LIVE_B, monitor_mode=mode)

    def test_occupied_slot_is_never_written(self):
        self.recall.return_value = self.preset("Tone Lab Live B edited")
        with self.assertRaisesRegex(iterate.FixtureUnavailable, "contains another preset"):
            self.prepare()
        self.qc.write_preset_file.assert_not_called()
        self.recall.assert_called_once_with(self.qc, iterate.LIVE_B, None)

    def test_empty_looking_saved_preset_is_never_written(self):
        self.recall.return_value = self.preset("Unsaved", inlet=0)
        self.directory(key="Unsaved.pb")
        with self.assertRaisesRegex(iterate.FixtureUnavailable, "contains another preset"):
            self.prepare()
        self.qc.write_preset_file.assert_not_called()

    def test_a_guitar_copy_is_stored_with_no_input_so_that_recalling_it_is_silent(self):
        copy, held = self.preset(iterate.LIVE_B_NAME), self.preset(iterate.LIVE_B_NAME, inlet=0)
        self.recall.side_effect = [self.preset("Unsaved", inlet=0), self.source, copy, held]
        self.directory()
        silent = [(0, 19), (0, 0), (0, 0), (0, 0)]
        with (patch.object(iterate, "set_routes") as set_routes,
              patch.object(iterate, "verify_fixture", return_value=held) as verify):
            self.assertEqual(self.prepare("guitar"), held)
        set_routes.assert_called_once_with(self.qc, silent)
        self.assertEqual([args.args[2] for args in verify.call_args_list], [silent, silent])
        # Written as copied, then again once its input is disconnected.
        self.assertEqual(self.qc.write_preset_file.call_count, 2)
        # A copy already held that way is used as it is.
        self.qc.write_preset_file.reset_mock()
        self.recall.side_effect = [held]
        self.assertEqual(self.prepare("guitar"), held)
        self.qc.write_preset_file.assert_not_called()

    def test_vacant_slot_is_written_only_under_the_exact_app_name(self):
        scratch = self.preset(iterate.LIVE_B_NAME, inlet=12)
        self.recall.side_effect = [self.preset("Unsaved", inlet=0), self.source, scratch]
        self.directory()
        self.assertEqual(self.prepare("phrase"), scratch)
        self.qc.write_preset_file.assert_called_once_with(iterate.LIVE_B["folder_key"],
                                                          iterate.LIVE_B["position"], iterate.LIVE_B_NAME)
        self.assertEqual(self.recall.call_args_list, [call(self.qc, iterate.LIVE_B, None),
                                                     call(self.qc, self.source_position, self.source.name),
                                                     call(self.qc, iterate.LIVE_B, iterate.LIVE_B_NAME)])

    def test_owned_phrase_slot_keeps_its_analog_output_and_needs_no_save(self):
        scratch = self.preset(iterate.LIVE_B_NAME, inlet=12)
        self.recall.return_value = scratch
        self.assertEqual(self.prepare("phrase"), scratch)
        self.qc.write_preset_file.assert_not_called()
        self.qc.list_directory.assert_not_called()
