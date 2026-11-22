"""The silent measuring harness on the simulated unit, and the research scripts made of it."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)

import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest import mock

import numpy as np

import support  # noqa: E402
from lab.hearing.audio import write_wave
from lab.hearing.measure import CENTRES
from lab.qc import sim
from lab.qc.sim import MY_PRESETS, SimUnit
from lab.qc.slots import BUILD
from lab.qc.unit import Moved, same_slot
from lab.sound import catalog
from lab.sound.chain import Block, Chain, Row
from lab.tools import sweeps

RECTIFIER, PLEXI, BRIT, HIWATT, V30, GREENBACK = 1018, 1081, 1001, 1063, 12001, 12006
GAIN, MASTER = 0, 5                 # parameters of the Rectifier
REFUSED = HIWATT                    # the simulated unit of these tests will not load it
ROW = {"label", "rms_db", "crest_db", "centroid_hz", "bands_db", "seconds"}
RESEARCH = Path(__file__).resolve().parents[1] / "research"


def trimmed(*blocks):
    return Chain((Row((*blocks, Block(catalog.GAIN_BLOCK).set_display(0, -12.))),))


def display(device, column, index):
    """What a block on the first row of the simulated unit holds for a parameter: text, or the display value."""
    cell = device.working.chains[0].models[column]
    value = cell.params[index].param_values[0]
    if value.WhichOneof("value") == "string_value":
        return value.string_value
    return round(catalog.to_display(cell.hash, index, value.float_value), 2)


class HarnessTest(unittest.TestCase):
    def setUp(self):
        support.free_now()    # what other modules left of Tk is collected here, on its own thread, not the engine's
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.device = SimUnit(refuse=lambda grid, model_id: model_id == REFUSED)
        self.device.save_slot(MY_PRESETS, 3, "Player's own", [(PLEXI, {}), (V30, {})])
        self.device.save_slot(MY_PRESETS, 5, "Another of the player's", [(BRIT, {})])
        self.device.player_selects(MY_PRESETS, 3)
        self.engine = support.engine_on(self.device, Path(folder.name) / "lab")
        self.folder = Path(folder.name) / "lab"
        self.dry = support.phrase(.6)
        self.replays = 0
        self.during = []          # what `watch` saw at each replay of the phrase
        self.at_replay = {}       # replay number -> something to do just before it
        self.after_replay = {}    # replay number -> something to do when it has come back
        self.watch = lambda: None
        played = self.engine.audio.reamp

        def reamp(dry, **options):
            if len(dry) != len(self.dry):       # the click that measures the round trip, not the phrase
                return played(dry, **options)
            self.replays += 1
            number = self.replays
            self.at_replay.get(number, lambda: None)()
            self.during.append(self.watch())
            returned = played(dry, **options)
            self.after_replay.get(number, lambda: None)()
            return returned

        self.engine.audio.reamp = reamp

    def calls(self, kind):
        return [args for call, args in self.device.log if call == kind]

    def on_the_build_slot_with_nothing_connected(self):
        return same_slot(self.device.position, BUILD) and support.inputs(self.device) == []

    # ---- conditions on one chain ----

    def test_each_condition_is_measured_with_its_values_in_place_and_they_are_put_back(self):
        self.engine.open()
        self.watch = lambda: (display(self.device, 0, GAIN), display(self.device, 0, MASTER))
        conditions = [("default", []), ("gain 2", [(0, 0, GAIN, 2.)]), ("gain 8 master 6", [(0, 0, GAIN, 8.),
                                                                                           (0, 0, MASTER, 6.)]),
                      ("default again", [])]
        found = sweeps.sweep(self.engine, trimmed(Block(RECTIFIER), Block(V30)), conditions, self.dry)
        self.assertEqual(self.during, [(5., 3.), (2., 3.), (8., 6.), (5., 3.)])
        self.assertEqual((found["skipped"], found["stopped"], found["firmware"]), ([], False, "4.1.1"))
        rows = found["rows"]
        self.assertEqual([row["label"] for row in rows], [label for label, _ in conditions])
        for row in rows:
            self.assertEqual(set(row), ROW)
            self.assertEqual(len(row["bands_db"]), len(CENTRES))
        self.assertLess(rows[1]["rms_db"], rows[0]["rms_db"])           # the simulated amp is quieter at less gain
        self.assertGreater(rows[2]["rms_db"], rows[0]["rms_db"])
        self.assertEqual(rows[3]["rms_db"], rows[0]["rms_db"])          # the same chain again: the same return
        self.assertEqual((display(self.device, 0, GAIN), display(self.device, 0, MASTER)), (5., 3.))

    def test_the_chain_is_built_once_and_not_again_for_each_condition(self):
        conditions = [("default", [])] + [(f"gain {value:g}", [(0, 0, GAIN, value)]) for value in (1., 3., 7., 9.)]
        sweeps.sweep(self.engine, trimmed(Block(RECTIFIER), Block(V30)), conditions, self.dry)
        self.assertEqual(self.replays, 5)
        self.assertEqual(sorted(args[2] for args in self.calls("add")), [RECTIFIER, V30, catalog.GAIN_BLOCK])

    def test_the_chain_is_heard_over_usb_alone_and_nothing_stays_connected(self):
        self.engine.open()
        self.watch = lambda: support.inputs(self.device)
        sweeps.sweep(self.engine, trimmed(Block(RECTIFIER), Block(V30)),
                     [("default", []), ("gain 2", [(0, 0, GAIN, 2.)])], self.dry)
        self.assertEqual(self.during, [[12], [12]])                     # fed from the computer while it is measured
        self.assertTrue(all(args[1] in (0, 12) and args[2] in (0, 14) for args in self.calls("route")),
                        self.calls("route"))                            # never an instrument input or an output
        self.assertTrue(self.on_the_build_slot_with_nothing_connected())
        self.assertNotIn(BUILD["position"], [args[1] for args in self.calls("save")])   # nothing is kept there
        self.engine.close()
        self.assertEqual(self.device.position["position"], 3)
        self.assertEqual(self.device.get_current_preset().name, "Player's own")

    def test_without_a_session_the_players_preset_is_borrowed_and_given_back(self):
        sweeps.sweep(self.engine, trimmed(Block(RECTIFIER), Block(V30)), [("default", [])], self.dry)
        self.assertEqual(self.device.position["position"], 3)
        self.assertEqual(self.device.get_current_preset().name, "Player's own")
        self.assertFalse((self.folder / "borrowed.json").exists())

    def test_a_microphone_is_chosen_by_name_and_the_cab_put_back(self):
        selector = catalog.model(GREENBACK).index(catalog.MIC_SELECTOR)
        before = None

        def note():
            nonlocal before
            before = before if before is not None else display(self.device, 1, selector)
            return display(self.device, 1, selector)

        self.watch = note
        self.engine.open()
        found = sweeps.sweep(self.engine, trimmed(Block(PLEXI), Block(GREENBACK)),
                             [("default", []), ("ribbon", [(0, 1, selector, "ML_412 Brit 60B GB 71_Ribbon 121")])],
                             self.dry)
        self.assertEqual(self.during, [before, "ML_412 Brit 60B GB 71_Ribbon 121"])
        self.assertEqual(found["skipped"], [])
        self.assertEqual(display(self.device, 1, selector), before)

    def test_a_condition_the_unit_does_not_take_is_not_measured_and_the_rest_are(self):
        write = self.device.set_param
        self.device.set_param = lambda row, column, index, value: (
            None if (column, index) == (0, MASTER) else write(row, column, index, value))
        told = []
        found = sweeps.sweep(self.engine, trimmed(Block(RECTIFIER), Block(V30)),
                             [("default", []), ("master 9", [(0, 0, MASTER, 9.)]), ("gain 8", [(0, 0, GAIN, 8.)])],
                             self.dry, progress=told.append)
        self.assertEqual(found["skipped"], ["master 9"])
        self.assertEqual([row["label"] for row in found["rows"]], ["default", "gain 8"])
        self.assertEqual(self.replays, 2)                               # nothing was replayed for it
        self.assertEqual([event.get("skipped") or event["row"]["label"] for event in told],
                         ["default", "master 9", "gain 8"])

    def test_when_a_value_does_not_go_back_the_blocks_are_added_anew_and_the_run_goes_on(self):
        write, lost = self.device.set_param, []

        def set_param(row, column, index, value):
            # The unit loses the writes that would put the gain back (the app writes a value twice before giving up).
            if (column, index) == (0, GAIN) and abs(value - .5) < .01 and len(lost) < 2:
                lost.append(value)
                return
            write(row, column, index, value)

        self.device.set_param = set_param
        self.watch = lambda: display(self.device, 0, GAIN)
        found = sweeps.sweep(self.engine, trimmed(Block(RECTIFIER), Block(V30)),
                             [("default", []), ("gain 8", [(0, 0, GAIN, 8.)]), ("default again", [])], self.dry)
        self.assertEqual(len(lost), 2)
        self.assertEqual(self.during, [5., 8., 5.])                     # the third condition is the chain as built
        self.assertEqual(found["rows"][2]["rms_db"], found["rows"][0]["rms_db"])
        self.assertEqual([args[2] for args in self.calls("add")], [RECTIFIER, V30, catalog.GAIN_BLOCK] * 2)

    def test_the_return_comes_with_its_margin_when_it_is_asked_for(self):
        chain, conditions = trimmed(Block(RECTIFIER), Block(V30)), [("default", [])]
        plain = sweeps.sweep(self.engine, chain, conditions, self.dry, audio=True)["rows"][0]
        wide = sweeps.sweep(self.engine, chain, conditions, self.dry, audio=True, margin=2400)["rows"][0]
        self.assertEqual(plain["audio"].shape, (len(self.dry), 2))
        self.assertEqual(wide["audio"].shape, (len(self.dry) + 4800, 2))
        self.assertTrue(np.array_equal(wide["audio"][2400:-2400], plain["audio"]))   # lined up the same
        self.assertEqual(float(np.abs(wide["audio"][:2000]).max()), 0.)             # before anything was sent
        self.assertEqual({key: value for key, value in wide.items() if key not in ("audio", "seconds")},
                         {key: value for key, value in plain.items() if key not in ("audio", "seconds")})

    def test_a_silent_return_ends_the_run_instead_of_being_measured(self):
        self.engine.audio.reamp = lambda dry, **options: np.zeros((len(dry) + 48000, 2), dtype=np.float32) \
            if len(dry) == len(self.dry) else sim.SimAudio.reamp(self.engine.audio, dry, **options)
        self.engine.open()
        with self.assertRaisesRegex(RuntimeError, "returned silence"):
            sweeps.sweep(self.engine, trimmed(Block(RECTIFIER), Block(V30)), [("default", []), ("more", [])], self.dry)
        self.assertTrue(self.on_the_build_slot_with_nothing_connected())

    # ---- many models in one cell ----

    def candidates(self, *models):
        return [(model_id, [("default", []), ("first|high|8|5", [(0, 8.)])]) for model_id in models]

    def test_each_model_takes_the_cell_in_turn_and_is_reported_as_it_is_finished(self):
        self.watch = lambda: (self.device.working.chains[0].models[0].hash, display(self.device, 0, 0))
        told = []
        found = sweeps.sweep_models(self.engine, trimmed(Block(RECTIFIER), Block(V30)), (0, 0),
                                    self.candidates(PLEXI, REFUSED, BRIT), self.dry, progress=told.append)
        self.assertEqual(found, {"firmware": "4.1.1", "measured": [PLEXI, BRIT], "refused": [REFUSED], "left": 0,
                                 "stopped": False})
        self.assertEqual([hash_ for hash_, _ in self.during], [PLEXI, PLEXI, BRIT, BRIT])
        self.assertEqual([value for _, value in self.during], [5., 8., 5., 8.])
        self.assertEqual([(event["model"], event["name"]) for event in told],
                         [(PLEXI, "Brit Plexi 50 Normal"), (REFUSED, catalog.model(REFUSED).name),
                          (BRIT, catalog.model(BRIT).name)])
        self.assertIsNone(told[1]["rows"])                              # the unit would not load it: said, not fatal
        for event in (told[0], told[2]):
            self.assertEqual([row["label"] for row in event["rows"]], ["default", "first|high|8|5"])
            self.assertEqual(set(event["rows"][0]), ROW)
            self.assertEqual((event["skipped"], event["firmware"]), ([], "4.1.1"))

    def test_only_the_cell_is_exchanged_between_models(self):
        sweeps.sweep_models(self.engine, trimmed(Block(RECTIFIER), Block(V30)), (0, 0),
                            self.candidates(PLEXI, REFUSED, BRIT), self.dry)
        added = [args[2] for args in self.calls("add")]
        # The chain once, then each model once; the one the unit refused was asked for a second time.
        self.assertEqual(added, [RECTIFIER, V30, catalog.GAIN_BLOCK, PLEXI, REFUSED, REFUSED, BRIT])
        self.assertEqual(self.device.position["position"], 3)           # and the player's preset is back

    def test_a_model_that_is_the_chains_own_stand_in_is_added_anew_with_the_units_values(self):
        self.watch = lambda: display(self.device, 0, GAIN)
        chain = trimmed(Block(RECTIFIER).set_display(GAIN, 8.), Block(V30))
        found = sweeps.sweep_models(self.engine, chain, (0, 0), [(RECTIFIER, [("default", [])])], self.dry)
        self.assertEqual(found["measured"], [RECTIFIER])
        self.assertEqual(self.during, [5.])                             # its own default, not the stand-in's gain

    def test_a_stop_during_a_replay_ends_the_run_and_puts_the_value_back(self):
        stop = threading.Event()
        self.at_replay[2] = stop.set                                    # with the second condition's value in place
        self.engine.open()
        found = sweeps.sweep(self.engine, trimmed(Block(RECTIFIER), Block(V30)),
                             [("default", []), ("gain 8", [(0, 0, GAIN, 8.)]), ("gain 2", [(0, 0, GAIN, 2.)])],
                             self.dry, stop=stop)
        self.assertEqual(([row["label"] for row in found["rows"]], found["stopped"]), (["default"], True))
        self.assertEqual(display(self.device, 0, GAIN), 5.)
        self.assertTrue(self.on_the_build_slot_with_nothing_connected())

    def test_a_stop_ends_the_run_between_two_conditions_and_leaves_nothing_connected(self):
        stop, values_written = threading.Event(), []

        def asked():
            stop.set()
            values_written.append(len(self.calls("param")))

        self.after_replay[3] = asked                                    # after the second model's first condition
        self.engine.open()
        told = []
        found = sweeps.sweep_models(self.engine, trimmed(Block(RECTIFIER), Block(V30)), (0, 0),
                                    self.candidates(PLEXI, BRIT, RECTIFIER), self.dry, stop=stop, progress=told.append)
        self.assertEqual(found, {"firmware": "4.1.1", "measured": [PLEXI], "refused": [], "left": 2, "stopped": True})
        self.assertEqual([event["model"] for event in told], [PLEXI])   # a model cut short is not reported
        self.assertEqual(self.replays, 3)
        self.assertEqual(len(self.calls("param")), values_written[0])   # the next condition's value was never written
        self.assertTrue(self.on_the_build_slot_with_nothing_connected())
        self.engine.close()
        self.assertEqual(self.device.position["position"], 3)
        self.assertEqual(self.device.get_current_preset().name, "Player's own")

    def test_a_stop_before_it_starts_measures_nothing(self):
        stop = threading.Event()
        stop.set()
        found = sweeps.sweep(self.engine, trimmed(Block(RECTIFIER), Block(V30)), [("default", [])], self.dry, stop=stop)
        self.assertEqual((found["rows"], found["stopped"]), ([], True))
        self.assertEqual(self.device.position["position"], 3)

    def test_a_failure_leaves_nothing_connected_and_the_values_as_they_were(self):
        def fail():
            raise RuntimeError("The replay was interrupted or dropped audio.")

        self.at_replay[2] = fail
        self.engine.open()
        with self.assertRaisesRegex(RuntimeError, "dropped audio"):
            sweeps.sweep(self.engine, trimmed(Block(RECTIFIER), Block(V30)),
                         [("default", []), ("gain 8", [(0, 0, GAIN, 8.)])], self.dry)
        self.assertTrue(self.on_the_build_slot_with_nothing_connected())
        self.assertEqual(display(self.device, 0, GAIN), 5.)
        self.engine.close()
        self.assertEqual(self.device.position["position"], 3)

    def test_a_failure_without_a_session_gives_the_players_preset_back(self):
        def fail():
            raise RuntimeError("The replay was interrupted or dropped audio.")

        self.at_replay[1] = fail
        with self.assertRaisesRegex(RuntimeError, "dropped audio"):
            sweeps.sweep_models(self.engine, trimmed(Block(RECTIFIER), Block(V30)), (0, 0), self.candidates(PLEXI),
                                self.dry)
        self.assertEqual(self.device.position["position"], 3)
        self.assertEqual(self.device.get_current_preset().name, "Player's own")
        self.assertFalse((self.folder / "borrowed.json").exists())

    def test_when_the_player_chooses_another_preset_nothing_more_is_written(self):
        written = []

        def choose():
            self.device.player_selects(MY_PRESETS, 5)
            written.append(len(self.device.log))

        self.at_replay[2] = choose                                      # while a condition's value is in place
        self.engine.open()
        with self.assertRaises(Moved):
            sweeps.sweep(self.engine, trimmed(Block(RECTIFIER), Block(V30)),
                         [("default", []), ("gain 8", [(0, 0, GAIN, 8.)]), ("gain 2", [(0, 0, GAIN, 2.)])], self.dry)
        self.assertEqual(len(self.device.log), written[0])
        self.engine.close()
        self.assertEqual(self.device.position["position"], 5)           # the preset the player chose is left alone
        self.assertEqual(self.device.get_current_preset().name, "Another of the player's")

    def test_a_preset_with_unsaved_changes_is_refused_before_anything_is_loaded(self):
        self.device.dirty = True
        with self.assertRaisesRegex(RuntimeError, "unsaved changes"):
            sweeps.sweep(self.engine, trimmed(Block(RECTIFIER), Block(V30)), [("default", [])], self.dry)
        self.device.dirty = False
        self.engine.open()
        self.device.dirty = True
        with self.assertRaisesRegex(RuntimeError, "unsaved changes"):
            sweeps.sweep_models(self.engine, trimmed(Block(RECTIFIER), Block(V30)), (0, 0), self.candidates(PLEXI),
                                self.dry)
        self.assertEqual(self.device.log, [])
        self.assertEqual(self.device.position["position"], 3)

    def test_it_runs_as_an_operation_of_the_host_and_reports_through_it(self):
        with TemporaryDirectory() as folder:
            host = support.host(folder)
            self.addCleanup(host.close)
            host.call("open").result(10)
            told = []
            found = host.call("lab.tools.sweeps.sweep_models", trimmed(Block(RECTIFIER), Block(V30)), (0, 0),
                              self.candidates(PLEXI, BRIT), self.dry, progress=told.append).result(30)
            self.assertEqual(found["measured"], [PLEXI, BRIT])
            self.assertEqual([event["model"] for event in told], [PLEXI, BRIT])     # each before the result


class ScriptsTest(unittest.TestCase):
    """The research scripts, run whole on the simulated unit into a folder of the test's own."""

    @classmethod
    def setUpClass(cls):
        _sys.path.insert(0, str(RESEARCH))
        cls.addClassCleanup(_sys.path.remove, str(RESEARCH))
        cls.scripts = {name: importlib.import_module(name)
                       for name in ("measure_controls", "measure_amps", "measure_polarity", "analyse_amps")}

    def setUp(self):
        support.free_now()
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name) / "out"
        self.wav = Path(folder.name) / "made-up phrase.wav"
        write_wave(self.wav, support.phrase(.8))
        self.devices = []

        def unit(**options):
            self.devices.append(SimUnit(refuse=lambda grid, model_id: model_id == REFUSED, **options))
            return self.devices[-1]

        patch = mock.patch.object(sim, "SimUnit", unit)        # the unit the host makes: one that refuses a model
        patch.start()
        self.addCleanup(patch.stop)

    def run_script(self, name, *words):
        with mock.patch("builtins.print"):
            return self.scripts[name].main(["--simulated", "--out", str(self.out), "--phrase", str(self.wav), *words])

    def read(self, *parts):
        return json.loads(self.out.joinpath(*parts).read_text(encoding="utf-8"))

    def left_as_it_was(self):
        """Every simulated unit a script opened ended on the preset it began on, with its own routing."""
        return all(device.position["folder_key"] == sim.FACTORY and device.working.name == "Brit 2203"
                   for device in self.devices)

    def test_the_simulated_unit_is_refused_without_a_folder_for_its_numbers(self):
        for name in ("measure_controls", "measure_amps", "measure_polarity"):
            with self.assertRaisesRegex(ValueError, "--out"):
                self.scripts[name].main(["--simulated", "--phrase", str(self.wav)])
        self.assertEqual(self.devices, [])

    def test_measure_controls_writes_both_sweeps(self):
        self.assertEqual(self.run_script("measure_controls"), 0)
        for name, conditions in (("cab-microphones", 18), ("amp-master-output", 14)):
            found = self.read(f"{name}.json")
            self.assertEqual(list(found), ["take", "seconds", "firmware", "bands_hz", "chain", "summary",
                                           "conditions", "skipped"])
            self.assertEqual((found["take"], found["firmware"], found["skipped"]), (self.wav.name, "4.1.1", []))
            self.assertEqual((len(found["conditions"]), len(found["summary"])), (conditions, conditions))
            self.assertEqual(set(found["conditions"][0]), ROW)
            self.assertEqual(list(found["summary"][0]), ["label", "level_db", "centroid_hz", "crest_db", "below_200_db",
                                                         "200_to_800_db", "800_to_3k_db", "3k_to_6k_db", "above_6k_db"])
        self.assertEqual(self.read("amp-master-output.json")["chain"], ["CA Duo Ch3 Modern",
                                                                        "412 CA Stand OS S V30 90s (M)", "Gain"])
        self.assertTrue(self.left_as_it_was())

    def test_measure_amps_writes_a_file_for_each_amp_and_goes_on_where_it_stopped(self):
        self.assertEqual(self.run_script("measure_amps", "--limit", "2"), 0)
        first = sorted(path.name for path in (self.out / "amps").glob("*.json"))
        self.assertEqual(len(first), 2)
        kept = {name: (self.out / "amps" / name).read_bytes() for name in first}
        self.assertEqual(self.run_script("measure_amps", "--limit", "2"), 0)
        second = sorted(path.name for path in (self.out / "amps").glob("*.json"))
        self.assertEqual(len(second), 4)                                # two more, not the same two again
        self.assertEqual({name: (self.out / "amps" / name).read_bytes() for name in first}, kept)
        found = self.read("amps", first[0])
        self.assertEqual(list(found), ["id", "name", "take", "seconds", "firmware", "bands_hz", "cab", "conditions",
                                       "refused", "skipped"])
        self.assertEqual((found["firmware"], found["refused"], found["cab"], found["bands_hz"]),
                         ("4.1.1", False, "412 CA Stand OS S V30 90s (M)", list(CENTRES)))
        self.assertEqual(found["conditions"][0]["label"], "default")
        self.assertEqual(set(found["conditions"][0]), ROW)
        self.assertTrue(all(len(row["label"].split("|")) == 4 for row in found["conditions"][1:]))
        self.assertTrue(self.left_as_it_was())

    def test_measure_amps_records_an_amp_the_unit_refuses_and_measures_named_amps_again(self):
        self.assertEqual(self.run_script("measure_amps", str(REFUSED), str(RECTIFIER)), 0)
        refused = self.read("amps", f"{REFUSED}.json")
        self.assertEqual((refused["refused"], refused["conditions"]), (True, None))
        before = (self.out / "amps" / f"{RECTIFIER}.json").stat().st_mtime_ns
        self.assertEqual(self.run_script("measure_amps", "low", str(RECTIFIER)), 0)
        low = self.read("amps-low", f"{RECTIFIER}.json")
        self.assertEqual([row["label"].split("|")[0] for row in low["conditions"]], ["default", "BASS", "BASS"])
        self.assertEqual((self.out / "amps" / f"{RECTIFIER}.json").stat().st_mtime_ns, before)
        self.assertEqual(self.run_script("measure_amps", str(RECTIFIER)), 0)        # named: measured again
        self.assertGreater((self.out / "amps" / f"{RECTIFIER}.json").stat().st_mtime_ns, before)
        # What the sweep wrote is what the analysis reads: the refused amp is listed and the other is in the table.
        with mock.patch("builtins.print"):
            self.scripts["analyse_amps"].main(["--measurements", str(self.out), "--out", str(self.out / "analysis")])
        table = self.read("analysis", "controls.json")
        self.assertEqual((table["firmware"], table["refused"], list(table["amps"])),
                         ("4.1.1", [catalog.model(REFUSED).name], [str(RECTIFIER)]))
        self.assertEqual(table["amps"][str(RECTIFIER)]["knobs"]["BASS"]["phrase"], "low strings")
        self.assertTrue((self.out / "analysis" / "amp-controls-report.md").is_file())

    def test_measure_polarity_goes_on_from_the_passes_it_kept_and_writes_the_record_and_the_table(self):
        passes = isolated.HOME / "simulated" / "polarity-passes" / "amps"
        amps = [model_id for model_id in self.scripts["measure_polarity"].candidates("amps")
                if model_id in (PLEXI, REFUSED, RECTIFIER, BRIT)]             # in the order they are measured
        first = [model_id for model_id in amps[:2] if model_id != REFUSED]
        models = [str(PLEXI), str(REFUSED), str(RECTIFIER), str(BRIT), str(V30), str(GREENBACK)]
        self.assertEqual(self.run_script("measure_polarity", *models, "--limit", "2"), 0)
        self.assertFalse((self.out / "polarity.json").exists())                    # the group is not finished
        self.assertEqual(sorted(path.name for path in passes.glob("*.npz")),
                         sorted(f"{model_id}.npz" for model_id in first))
        self.assertEqual(self.run_script("measure_polarity", *models), 0)
        self.assertFalse(passes.exists())                                           # kept only while unfinished
        record = self.read("polarity.json")
        self.assertEqual(list(record), ["amps", "cabs"])
        self.assertEqual(list(record["amps"]), ["take", "seconds", "firmware", "signs", "against_reference",
                                                "typical_strength", "pairs", "pairs_explained", "weak_pairs"])
        self.assertEqual(sorted(record["amps"]["signs"]), sorted([str(BRIT), str(RECTIFIER), str(PLEXI)]))
        self.assertEqual(sorted(record["cabs"]["signs"]), [str(V30), str(GREENBACK)])
        table = self.read("tables", "polarity.json")
        self.assertEqual(list(table), ["firmware", "amps", "cabs"])
        self.assertEqual(table["firmware"], "4.1.1")
        for group in ("amps", "cabs"):
            self.assertEqual(list(table[group]), ["measured", "inverted"])
        # The second run measured only what the first had not kept: the rest came from the passes in the data folder.
        tried = [{args[2] for call, args in device.log if call == "add" and args[2] in amps} for device in self.devices]
        self.assertEqual(tried, [set(amps[:2]), set(amps) - set(first)])
        self.assertTrue(self.left_as_it_was())

    def test_a_stop_leaves_the_simulated_unit_as_it_was_and_keeps_the_amps_that_were_finished(self):
        from lab import cli

        waited = cli.wait

        def stop_after_the_first_amp(host, future, **options):
            support.until(lambda: list((self.out / "amps").glob("*.json")) or future.done())
            host.stop()
            return waited(host, future, **options)

        with mock.patch.object(cli, "wait", stop_after_the_first_amp):
            self.assertEqual(self.run_script("measure_amps"), 1)        # every amp there is: stopped long before
        written = list((self.out / "amps").glob("*.json"))
        self.assertTrue(0 < len(written) < 30, written)
        self.assertTrue(self.left_as_it_was())
        self.assertEqual(support.inputs(self.devices[0]), [1])          # its own routing, nothing of the run's


if __name__ == "__main__":
    unittest.main()
