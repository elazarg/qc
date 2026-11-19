"""The command line on the new core: the desk without a command, and each command on the simulated unit."""

import contextlib
import gc
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import numpy as np

from lab import cli
from lab.audio import read_wave, write_wave
from lab.engine import Engine
from lab.sim import MY_PRESETS, SimAudio, SimUnit
from lab.slots import Slots
from lab.unit import Unit


def run(*arguments):
    """Exit code, stdout and stderr of `python -m lab ...`."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(arguments), desk=lambda args: (_ for _ in ()).throw(AssertionError("the desk opened")))
    return code, out.getvalue(), err.getvalue()


def result(*arguments):
    code, out, err = run(*arguments)
    if code:
        raise AssertionError(f"exit {code}: {err}")
    return json.loads(out)


class Home(unittest.TestCase):
    """Each test with a data folder of its own."""

    def setUp(self):
        # Windows other test modules left for the collector are collected here, on Tk's thread: collected on
        # the engine's thread, Tcl aborts the process.
        gc.collect()
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        patcher = mock.patch.dict(os.environ, {"TONE_LAB_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)


class DispatchTest(unittest.TestCase):
    def test_no_command_opens_the_desk_with_its_own_flags(self):
        opened = []
        self.assertEqual(cli.main([], desk=opened.append), 0)
        self.assertEqual(cli.main(["--simulated"], desk=opened.append), 0)
        self.assertEqual(opened, [[], ["--simulated"]])

    def test_a_command_does_not_open_the_desk(self):
        with mock.patch.dict(cli.RUN, {"presets": lambda args: ["listed"]}):
            self.assertEqual(run("presets")[:2], (0, "listed\n"))

    def test_every_command_parses(self):
        parser = cli.parser()
        for line in (["doctor", "--unit"], ["migrate", "--dry-run"], ["pod", "x.pgb", "--user-slot", "02B"],
                     ["history", "a.json", "b.json"], ["latest-clip"], ["presets"], ["shortcut", "--desk", "new"],
                     ["test-compensation", "a.wav", "b.wav", "c.wav"], ["directory", "--simulated"], ["inspect"],
                     ["snapshot"], ["record", "x.wav", "--seconds", "3"], ["calibrate"],
                     ["reamp", "d.wav", "--slot", "2F", "--scenes", "AB"],
                     ["sweep", "d.wav", "--slot", "2F", "--row", "1", "--column", "1", "--parameter", "0",
                      "--values", "4", "5"],
                     ["budget"], ["named-settings", "--only", "1001", "6011"]):
            self.assertEqual(parser.parse_args(line).action, line[0])
        self.assertEqual(set(cli.RUN), set(cli.COMMANDS))

    def test_an_error_is_one_line_and_exit_code_one(self):
        def failing(args):
            raise RuntimeError("The unit is off.")

        with mock.patch.dict(cli.RUN, {"presets": failing}):
            self.assertEqual(run("presets"), (1, "", "Tone Lab: The unit is off.\n"))

    def test_the_desk_holding_the_unit_refuses_a_unit_command(self):
        with mock.patch.object(cli, "desk_open", lambda: True):
            with self.assertRaises(cli.Refused):
                with cli.session(False):
                    pass

    def test_ctrl_c_stops_at_the_next_safe_point(self):
        class Host:
            stopped = 0

            def stop(self):
                self.stopped += 1

        class Future:
            calls = 0

            def result(self, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise KeyboardInterrupt
                return "done"

        host, said = Host(), []
        self.assertEqual(cli.wait(host, Future(), say=said.append), "done")
        self.assertEqual(host.stopped, 1)
        self.assertIn("next safe point", said[0])


class SimulatedUnitTest(Home):
    def test_inspect_shows_blocks_with_parameter_indices(self):
        found = result("inspect", "--simulated")
        self.assertEqual(found["name"], "Brit 2203")
        block = found["rows"][0]["blocks"][0]
        self.assertEqual((block["column"], block["model"], block["name"]), (1, 1001, "Brit 2203"))
        self.assertEqual(block["params"][0], [0, "GAIN", 5.0])
        self.assertTrue(found["summary"][0].startswith("Row 1: Brit 2203"))

    def test_snapshot_keeps_the_selected_preset_as_a_sound(self):
        kept = result("snapshot", "--simulated")
        listed = result("presets", "--simulated")
        self.assertEqual([(item["id"], item["name"]) for item in listed], [(kept["sound"], "Brit 2203")])
        self.assertEqual(result("presets"), [])          # the real unit's data is apart from the simulated one

    def test_directory_lists_what_the_unit_holds(self):
        self.assertEqual(result("directory", "--simulated"), [])
        self.assertTrue((self.home / "simulated" / "directory.json").is_file())

    def test_calibrate_measures_the_round_trip(self):
        self.assertEqual(result("calibrate", "--simulated"), {"offset_samples": SimAudio.LATENCY,
                                                                "ms": SimAudio.LATENCY / 48})
        self.assertTrue((self.home / "simulated" / "calibration.json").is_file())

    def test_budget_on_the_simulated_unit_never_touches_the_real_table(self):
        found = result("budget", "--simulated")
        self.assertEqual(Path(found["path"]), self.home / "simulated" / "costs.json")
        self.assertGreater(found["models"], 100)

    def test_named_settings_on_the_simulated_unit(self):
        code, out, err = run("named-settings", "--simulated", "--only", "1001")
        self.assertEqual(code, 0, err)
        found = json.loads(out)
        self.assertEqual((found["read"], found["recorded"]), (1, 1))
        self.assertEqual(Path(found["path"]), self.home / "simulated" / "device-presets.json")
        self.assertIn("1/1 Brit 2203: 3 settings", err)

    def test_record_makes_a_phrase_and_latest_clip_finds_it(self):
        phrase = result("record", "--simulated", "--seconds", "1", "--setting", "Bridge", "--instrument", "Charvel")
        self.assertEqual((phrase["setting"], phrase["instrument"], phrase["seconds"]), ("Bridge", "Charvel", 1.0))
        self.assertTrue(Path(phrase["dry"]).is_file())
        latest = result("latest-clip", "--simulated")
        self.assertEqual(latest["id"], phrase["id"])

    def test_record_to_a_file_refuses_one_that_exists(self):
        target = self.home / "di.wav"
        self.assertEqual(result("record", str(target), "--simulated", "--seconds", "1")["file"], str(target.resolve()))
        self.assertEqual(len(read_wave(target)), 48000)
        code, _, err = run("record", str(target), "--simulated")
        self.assertEqual(code, 1)
        self.assertIn("new .wav", err)

    def test_doctor_with_the_unit(self):
        from lab import setup

        fine = lambda: setup.finding("x", True, "fine")   # noqa: E731
        with mock.patch.object(setup, "checks", lambda home: [fine()]):
            code, out, err = run("doctor", "--unit", "--simulated")
        self.assertEqual(code, 0, err)
        found = {item["check"]: item for item in json.loads(out)}
        self.assertTrue(found["unit"]["ok"])
        self.assertIn("firmware 4.1.1", found["unit"]["say"])
        self.assertTrue(found["setlist"]["ok"])
        self.assertEqual(found["usb"]["say"], "The dry guitar arrives on USB channel 1.")

    def test_doctor_says_when_the_unit_cannot_be_reached(self):
        from lab import setup

        def unreachable(*args, **options):
            raise RuntimeError("no device")

        with mock.patch.object(setup, "checks", lambda home: []), mock.patch.object(cli, "call", unreachable):
            code, out, _ = run("doctor", "--unit")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)[0]["say"], "The Quad Cortex could not be reached: no device")


class RendersTest(Home):
    def setUp(self):
        super().setUp()
        self.device = SimUnit()
        self.device.save_slot(MY_PRESETS, 13, "Vai", [(1081, {}), (12001, {})])
        unit = Unit(self.device, settle=.05, sleep=lambda seconds: None)
        self.engine = Engine(unit, Slots(unit, self.home / "lab"), SimAudio(self.device))
        self.engine.open()
        self.dry = (.1 * np.sin(np.arange(24000) / 10)).astype(np.float32)[:, None]

    def test_a_saved_preset_is_played_scene_by_scene_and_with_a_parameter_moved(self):
        found = cli.renders(self.engine, "2F", "my presets", self.dry, [{"scene": 0}, {"edit": (0, 0, 0, 9.)}])
        self.assertEqual((found["name"], found["slot"]["position"]), ("Vai", 13))
        self.assertEqual([render.shape[0] for render in found["renders"]], [24000 + 48000] * 2)
        louder = [float(np.abs(render).max()) for render in found["renders"]]
        self.assertGreater(louder[1], louder[0])          # more gain drives the simulated amp harder
        self.engine.close()

    def test_a_value_out_of_range_or_a_missing_preset_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "goes from 0 to 10"):
            cli.renders(self.engine, "2F", "My Presets", self.dry, [{"edit": (0, 0, 0, 11.)}])
        with self.assertRaisesRegex(RuntimeError, "holds no block"):
            cli.renders(self.engine, "2F", "My Presets", self.dry, [{"edit": (1, 0, 0, 1.)}])
        with self.assertRaisesRegex(RuntimeError, "No saved preset"):
            cli.renders(self.engine, "3A", "My Presets", self.dry, [{"scene": 0}])

    def fake_call(self, simulated, name, *args, **options):
        self.called = (name, args)
        place, setlist, dry, variants = args
        return {"name": "Vai", "slot": {"folder_key": MY_PRESETS, "position": 13},
                "renders": [np.full((len(dry), 2), .1 * (i + 1), dtype=np.float32) for i in range(len(variants))]}

    def test_reamp_writes_one_render_per_scene(self):
        dry = self.home / "dry.wav"
        write_wave(dry, self.dry)
        with mock.patch.object(cli, "call", self.fake_call):
            found = result("reamp", str(dry), "--slot", "2F", "--scenes", "ab", "--simulated")
        self.assertEqual(self.called[1][3], [{"scene": 0}, {"scene": 1}])
        self.assertEqual([item["label"] for item in found["renders"]], ["scene-A", "scene-B"])
        folder = Path(found["folder"])
        self.assertTrue((folder / "scene-B.wav").is_file())
        record = json.loads((folder / "renders.json").read_text(encoding="utf-8"))
        self.assertEqual((record["preset"], record["scenes"]), ("Vai", "AB"))

    def test_sweep_moves_one_parameter_counted_from_one(self):
        dry = self.home / "dry.wav"
        write_wave(dry, self.dry)
        out = self.home / "out"
        with mock.patch.object(cli, "call", self.fake_call):
            found = result("sweep", str(dry), "--slot", "2F", "--row", "1", "--column", "2", "--parameter", "3",
                           "--values", "4", "4.5", "--out", str(out))
        self.assertEqual(self.called[1][3], [{"edit": (0, 1, 3, 4.), "scene": None},
                                             {"edit": (0, 1, 3, 4.5), "scene": None}])
        self.assertEqual(Path(found["folder"]), out)
        self.assertTrue((out / "value-4.5.wav").is_file())
        code, _, err = run("sweep", str(dry), "--slot", "2F", "--row", "1", "--column", "1", "--parameter", "0",
                           "--values", "4")
        self.assertEqual(code, 1)
        self.assertIn("two to seven", err)
        code, _, err = run("reamp", str(dry), "--slot", "2F", "--scenes", "AZ")
        self.assertIn("letters A to H", err)


class LocalTest(Home):
    def test_pod_lists_starting_points_and_shows_one_slot(self):
        from test_lab_podgo import time_preset, write_backup

        backup = write_backup(self.home, {"User": [("Time", time_preset())]})
        listed = result("pod", str(backup))
        self.assertEqual(listed["setlists"], ["User"])
        self.assertEqual(listed["presets"][0]["slot"], "01A")
        self.assertTrue(listed["presets"][0]["starting_point"])
        one = result("pod", str(backup), "--user-slot", "1a")
        self.assertEqual(one["name"], "Time")
        self.assertTrue(one["chain"][0].startswith("Row 1"))
        self.assertTrue(any(piece.get("quad_cortex") for piece in one["pieces"]))
        code, _, err = run("pod", str(backup), "--user-slot", "02B")
        self.assertEqual(code, 1)
        self.assertIn("empty or missing", err)

    def test_history_ranks_songs_and_can_leave_out_known_ones(self):
        path = self.home / "StreamingHistory_music_0.json"
        path.write_text(json.dumps([{"artistName": "Steve Vai", "trackName": "For the Love of God", "msPlayed": 400000},
                                    {"artistName": "Rush", "trackName": "Limelight", "msPlayed": 200000}]),
                        encoding="utf-8")
        ranked = result("history", str(path))
        self.assertEqual([item["title"] for item in ranked], ["For the Love of God", "Limelight"])
        from lab.sounds import Store

        Store(self.home).save_song({"name": "Steve Vai – For the Love of God"})
        missing = result("history", str(path), "--missing")
        self.assertEqual([item["title"] for item in missing], ["Limelight"])

    def test_migrate_dry_run_prints_the_summary(self):
        old = self.home / "old"
        (old / "drafts").mkdir(parents=True)
        from lab import migrate

        with mock.patch.object(migrate, "OLD", old), mock.patch.object(migrate.migrate, "__defaults__", (old,)):
            code, out, _ = run("migrate", "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("0 designs and 0 presets as sounds"))
        self.assertFalse((self.home / "sounds").exists())

    def test_latest_clip_with_none(self):
        code, _, err = run("latest-clip", "--simulated")
        self.assertEqual(code, 1)
        self.assertIn("No phrase", err)

    def test_shortcut_passes_the_chosen_desk(self):
        chosen = []
        with mock.patch.object(cli, "shortcut", lambda desk=None: chosen.append(desk) or Path("Tone Lab.lnk")):
            code, out, _ = run("shortcut", "--desk", "new")
        self.assertEqual((code, out.strip(), chosen), (0, "Tone Lab.lnk", ["new"]))

    def test_the_launcher_is_not_rebuilt_when_current(self):
        root = self.home / "program"
        (root / "desktop_launcher").mkdir(parents=True)
        for name in ("desktop_launcher/Program.cs", "ToneLab.ico", "Tone Lab.exe"):
            (root / name).write_text("x", encoding="utf-8")
        later = (root / "Tone Lab.exe").stat().st_mtime + 10
        os.utime(root / "Tone Lab.exe", (later, later))
        ran = []
        self.assertEqual(cli.build_launcher(root, run=lambda *a, **k: ran.append(a)), root / "Tone Lab.exe")
        self.assertEqual(ran, [])


if __name__ == "__main__":
    unittest.main()
