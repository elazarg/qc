"""Every side tool opens on the desk, does its main work on the simulated unit through the host, and closes."""


import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest import mock

from lab.sound import catalog
from lab.sound.sounds import Sound
from lab.tools.menu import TOOLS, opener
from lab.tools.usage import opens as usage_opens

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lab_tools_support import DELAY, PLEXI, V30, chain, desk, phrase, pump  # noqa: E402


class WindowsTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"No display: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.desk = desk(self.folder, self.root)
        self.studio = self.desk.studio
        self.addCleanup(self.release)
        self.studio.phrase = ("phrase-1", phrase())

    def release(self):
        """Close every window and let go of it here, on the thread Tk runs on, before the root goes."""
        import gc

        for window in list(getattr(self.desk, "tool_windows", {}).values()):
            if window.alive():
                window.close()
        self.desk.tool_windows = {}
        self.studio.close()
        self.desk = self.studio = None
        gc.collect()

    def put(self, side, sound):
        self.studio.put(side, sound)
        pump(self.studio, self.root)

    def open(self, name):
        window = opener(name)(self.desk)
        self.root.update()
        return window

    def test_every_tool_opens_once_and_closes(self):
        self.assertEqual(len({name for _, name in TOOLS}), len(TOOLS))
        for title, name in TOOLS:
            window = self.open(name)
            self.assertTrue(window.alive(), title)
            self.assertIs(opener(name)(self.desk), window)      # opening it again brings the same one forward
            window.close()
            self.assertFalse(window.alive())
        from lab.tools import usage

        self.assertEqual(usage.opens(self.folder)["Levels"], 1)

    def test_levels_leave_a_and_b_to_the_desk_and_match_a_song_s_parts(self):
        quiet = Sound("Quiet", chain(gain_db=-9.), {"kind": "designed"})
        loud = Sound("Loud", chain(gain_db=0.), {"kind": "designed"})
        self.put("a", quiet)
        self.put("b", loud)
        window = self.open("levels")
        # Regression: the tool set A's and B's levels itself, and the desk undid it at the next play.
        window.measure()
        self.assertEqual(self.studio.jobs, [])                  # no song: nothing is measured, A and B are the desk's
        self.assertIn("song", window.status.get())
        self.assertFalse(hasattr(window, "what"))

        self.studio.set(song="Time")
        self.studio.set(side="a")
        self.studio.add_part("Clean")
        self.studio.set(side="b")
        self.studio.add_part("Lead")
        window.measure()
        pump(self.studio, self.root)
        trims = {row["name"]: row["trim_db"] for row in window.planned["rows"]}
        self.assertAlmostEqual(trims["Lead"] - trims["Clean"], -9., delta=.3)
        window.apply()
        parts = self.studio.store.song("Time")["parts"]
        leveled = [self.studio.store.sound(part["sound"]) for part in parts]
        self.assertTrue(all(sound.chain.rows[0].blocks[-1][1].model == catalog.GAIN_BLOCK for sound in leveled))
        self.assertNotIn(quiet.id, {part["sound"] for part in parts})

    def test_a_listening_level_or_a_mute_does_not_outlive_the_levels_tool(self):
        # Regression: a mute set in the tool stayed on after it was closed, with nothing on the desk saying so.
        self.put("a", Sound("Quiet", chain(gain_db=-9.), {"kind": "designed"}))
        self.put("b", Sound("Loud", chain(gain_db=0.), {"kind": "designed"}))
        window = self.open("levels")
        window.toggle_mute()
        pump(self.studio, self.root)
        self.assertEqual(self.look()["levels"], {"a": -40., "b": -40.})
        self.assertTrue(self.studio.listening["muted"])
        window.close()
        pump(self.studio, self.root)
        found = self.look()
        self.assertEqual(found["levels"], {"a": -12., "b": -12.})
        self.assertFalse(found["listening"])
        self.assertIsNone(self.studio.listening)                # where the desk can read what is on: nothing is

    def look(self):
        """The engine's own state, read where it is held."""
        import lab.tools.probe as probe

        probe.peek = lambda engine, *, stop=None: {"levels": dict(engine.levels), "mode": engine.mode,
                                                   "shown": engine.shown[0] if engine.shown else None,
                                                   "listening": engine.listening is not None}
        self.addCleanup(lambda: vars(probe).pop("peek", None))
        return self.studio.host.call("lab.tools.probe.peek").result(10)

    def test_after_a_tool_played_through_the_unit_the_desk_says_what_is_true(self):
        # Regression: the desk went on showing Phrase as playing, and A and B as ready, after a tool had taken both.
        self.put("a", Sound("Clean", chain(gain_db=0.), {"kind": "designed"}))
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        self.studio.play("phrase", "b")
        pump(self.studio, self.root)
        self.assertEqual((self.studio.state.mode, self.look()["mode"]), ("phrase", "phrase"))
        window = self.open("field")
        window.measure()
        self.assertEqual((self.studio.state.mode, self.studio.state.shown), (None, None))   # said at once
        pump(self.studio, self.root)
        self.assertEqual((self.look()["mode"], self.look()["shown"]), (None, None))
        self.assertEqual((self.studio.state.mode, self.studio.state.shown), (None, None))
        self.studio.play("phrase", "b")                         # and one press plays again
        pump(self.studio, self.root)
        self.assertEqual((self.studio.state.mode, self.look()["mode"]), ("phrase", "phrase"))

    def test_a_tool_leaves_the_desk_s_own_working_line_alone(self):
        # Regression: a finished tool call blanked whatever the desk was saying it was working on.
        window = self.open("usage")
        self.studio.begin("design", "Designing three sounds…")
        window.check_unit()
        pump(self.studio, self.root)
        self.assertEqual(self.studio.state.working, "Designing three sounds…")
        self.studio.end("design")
        window.check_unit()
        self.assertEqual(self.studio.state.working, "Asking the Quad Cortex…")      # its own line, while it works
        pump(self.studio, self.root)
        self.assertEqual(self.studio.state.working, "")
        # Two tools at once: the line stays until the last of them is done.
        other = self.open("soundcheck")
        window.check_unit()
        other.unit_check()
        self.studio.poll()
        self.assertTrue(self.studio.state.working or not (window.busy or other.busy))
        pump(self.studio, self.root)
        self.assertEqual(self.studio.state.working, "")

    def test_nothing_is_played_through_the_unit_while_something_else_holds_the_audio_input(self):
        self.put("a", Sound("Clean", chain(gain_db=0.), {"kind": "designed"}))
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        window = self.open("field")
        self.studio.audio_held_by = "learner"
        self.addCleanup(lambda: setattr(self.studio, "audio_held_by", None) if self.studio else None)
        window.measure()
        self.assertEqual(self.studio.jobs, [])
        self.assertIn("The Guitar Learner is using the input.", window.status.get())
        self.assertEqual(self.studio.state.shown, "pair")       # and the desk was told nothing
        used = self.open("usage")
        used.check_unit()                                       # reading the unit plays nothing: it goes ahead
        pump(self.studio, self.root)
        self.assertIn("cache slots", used.unit.cget("text"))
        self.studio.audio_held_by = None
        window.measure()
        pump(self.studio, self.root)
        self.assertEqual(len(window.result["spectrum_db"]), 8)

    def test_a_drag_on_the_field_is_one_step_for_undo(self):
        # Regression: every tick of a drag and every step of a sweep was a checkpoint, and forty of them pushed
        # everything else out of Undo.
        first = Sound("First", chain(PLEXI, V30), {"kind": "designed"})
        self.put("b", first)
        self.put("b", Sound("Second", chain(PLEXI, V30, DELAY), {"kind": "designed"}))
        window = self.open("field")
        before = len(self.studio.history)
        event = SimpleNamespace(x=40, y=40)
        window.push(event)
        for step in range(45):
            window.point = window.normal(-1 + step * 2 / 44, 0.)
            window.hear_point()                                 # what each tick of the drag does
        self.assertEqual(len(self.studio.history), before)      # heard, not kept, while it lasts
        self.assertEqual(self.studio.sound("b").knobs, {})
        window.apply_point()                                    # the button is let go
        pump(self.studio, self.root)
        self.assertEqual(len(self.studio.history), before + 1)
        self.assertGreater(self.studio.sound("b").knobs.get("drive", 0), 0)
        self.studio.undo()
        self.assertEqual(self.studio.sound("b").knobs, {})
        self.studio.undo()
        pump(self.studio, self.root)
        self.assertEqual(self.studio.sound("b").name, "First")  # the sound before is still within reach

    def test_a_sweep_on_the_field_is_one_step_for_undo(self):
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        window = self.open("field")
        window.set_point(-1., 0.)
        window.mark("A")
        window.set_point(1., 0.)
        window.mark("B")
        window.set_point(0., 0.)
        pump(self.studio, self.root)
        before = len(self.studio.history)
        window.toggle_sweep()
        for step in range(1, 9):
            window.after_cancel(window.sweep_job)
            window.sweep_step(step)
        pump(self.studio, self.root)
        self.assertIsNone(window.sweep_job)
        self.assertEqual(len(self.studio.history) - before, 1)       # eight steps heard, one kept
        self.assertEqual(self.studio.sound("b").knobs, {key: value for key, value in window.marks["B"].values.items()
                                                        if value})

    def test_the_field_has_no_keys(self):
        window = self.open("field")
        self.assertEqual([key for key in ("<Home>", "<Left>", "<Right>", "<Up>", "<Down>") if window.canvas.bind(key)],
                         [])

    def test_tone_search_keeps_what_it_described_when_its_window_is_closed(self):
        # Regression: closing the window while it rendered threw every description away.
        from lab.tools import search

        self.put("a", Sound("Echo Lead", chain(DELAY), {"kind": "designed"}))
        self.put("b", Sound("Dry Rhythm", chain(gain_db=0.), {"kind": "designed"}))
        window = self.open("search")
        window.reference_from_a()
        pump(self.studio, self.root)
        window.match()
        window.close()
        pump(self.studio, self.root)
        self.assertEqual(len(search.load_cache(self.folder)), 2)

    def test_a_tool_that_cannot_be_built_leaves_no_window_behind(self):
        # Regression: each click left another empty window when a tool failed while it was being built.
        from lab.tools import pod

        def titles():
            return [child.title() for child in self.root.winfo_children() if isinstance(child, tk.Toplevel)]

        with mock.patch.object(pod.PodWindow, "build", lambda window: (_ for _ in ()).throw(KeyError("broken"))):
            for _ in range(2):
                with self.assertRaises(KeyError):
                    opener("pod")(self.desk)
        self.assertNotIn("POD Go presets", titles())
        self.assertNotIn("POD Go presets", self.desk.tool_windows)
        self.assertNotIn("POD Go presets", usage_opens(self.folder))

    def test_the_pod_window_opens_on_a_backup_that_cannot_be_read_and_on_one_odd_preset(self):
        # Regression: a damaged backup that was remembered raised out of the window as it opened.
        from test_lab_podgo import time_preset, write_backup

        bad = self.folder / "bad.pgb"
        bad.write_bytes(b"AF6L\x01\0\0\0" + (48 + 5).to_bytes(4, "little") + b"\0" * 36 + b"junk!"
                        + b"00LS" + (48).to_bytes(8, "little") + (5).to_bytes(8, "little") + (1).to_bytes(4, "little")
                        + (100).to_bytes(8, "little") + b"\0" * 4)
        (self.folder / "podgo.json").write_text(json.dumps({"backup": str(bad)}), encoding="utf-8")
        window = self.open("pod")
        self.assertTrue(window.alive())
        self.assertIn("SL00", window.status.get())
        window.close()

        odd = time_preset()
        odd["controller"] = None
        good = write_backup(self.folder, {"User": [("Time", time_preset()), ("Odd", odd)]})
        (self.folder / "podgo.json").write_text(json.dumps({"backup": str(good)}), encoding="utf-8")
        window = self.open("pod")
        self.assertEqual(window.list.size(), 2)
        self.assertIn("could not be read", window.list.get(1))
        window.list.selection_set(0)
        window.describe()
        told = window.note.cget("text")
        self.assertIn("Scream 808 → Green 808 (same gear)", told)      # each block, and why
        self.assertIn("Set from the POD Go:", told)                    # and which values came across
        window.put()
        pump(self.studio, self.root)
        first = self.studio.sound("b")
        self.assertEqual(first.origin["from"], "POD Go")
        # The same preset read again is the same sound, with what was done to it since.
        first.marks = ["keeper"]
        self.studio.store.save(first)
        window.load(str(good))
        window.list.selection_set(0)
        window.put()
        pump(self.studio, self.root)
        self.assertEqual((self.studio.sound("b").id, self.studio.sound("b").marks), (first.id, ["keeper"]))
        self.assertEqual(len(self.studio.store.sounds()), 1)

    def test_the_sound_check_s_stage_versions_are_a_comparison_the_workbench_lists(self):
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        check = self.open("soundcheck")
        check.stage()
        pump(self.studio, self.root)
        self.assertEqual(set(check.auditions), set("ABC"))
        check.reveal()
        self.assertIn("As it is", check.stage_text.cget("text"))
        bench = self.open("workbench")
        self.assertEqual(bench.earlier.size(), 1)
        self.assertIn("Stage EQ", bench.earlier.get(0))


    def test_search_finds_by_words_and_matches_a_reference(self):
        self.put("a", Sound("Echo Lead", chain(DELAY), {"kind": "designed"}))
        self.put("b", Sound("Dry Rhythm", chain(gain_db=0.), {"kind": "designed"}))
        window = self.open("search")
        window.query.insert(0, "echo")
        window.find()
        self.assertEqual(window.results[0]["title"], "Echo Lead")
        window.query.delete(0, "end")
        window.reference_from_a()
        pump(self.studio, self.root)
        self.assertIsNotNone(window.reference)
        window.match()
        pump(self.studio, self.root)
        self.assertEqual(window.results[0]["title"], "Echo Lead")
        self.assertTrue(window.results[0]["same_take"])

    def test_the_field_measures_a_and_b_and_moves_the_knobs(self):
        self.put("a", Sound("Clean", chain(gain_db=0.), {"kind": "designed"}))
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        window = self.open("field")
        window.measure()
        pump(self.studio, self.root)
        self.assertEqual(len(window.result["spectrum_db"]), 8)
        window.set_point(1., 0.)
        pump(self.studio, self.root)
        self.assertGreater(self.studio.sound("b").knobs.get("drive", 0), 0)

    def test_the_workbenches_render_versions_and_read_the_unit_back(self):
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        bench = self.open("workbench")
        bench.kind.set("Strengths")
        bench.kind_changed()
        bench.argument.insert(0, "darker")
        bench.render()
        pump(self.studio, self.root)
        self.assertEqual(len(bench.auditions), 3)
        bench.last = next(iter(bench.auditions))
        bench.keep()
        bench.put_kept()
        pump(self.studio, self.root)
        self.assertNotEqual(self.studio.sound("b").name, "Plexi")

        controls = self.open("controls")
        controls.refresh(force=True)
        controls.read_back()
        pump(self.studio, self.root)
        self.assertIn("Brit Plexi", controls.held.cget("text"))      # B, on its own rows of the pair
        controls.step(-1)                                    # back to the version it was made from
        pump(self.studio, self.root)
        self.assertEqual(self.studio.sound("b").name, "Plexi")

    def test_the_sound_check_and_the_library_and_usage_ask_the_unit(self):
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        check = self.open("soundcheck")
        check.check(record=False)
        check.unit_check()
        check.stage()
        pump(self.studio, self.root)
        self.assertIn("Dry peak", check.take_text.cget("text"))
        self.assertIn("4.1.1", check.unit_text.cget("text"))
        self.assertEqual(set(check.auditions), set("ABC"))

        found = self.open("library")
        found.refresh()
        pump(self.studio, self.root)
        self.assertIn("saved presets", found.hint.cget("text").lower())

        used = self.open("usage")
        used.check_unit()
        pump(self.studio, self.root)
        self.assertIn("cache slots", used.unit.cget("text"))
        used.note.insert("1.0", "B sounds boxy")
        used.keep()
        self.assertEqual(used.notes.size(), 1)


def tearDownModule():
    # Tk objects left as garbage here must be freed on the main thread: if a later test's worker thread runs
    # the collector, Tcl aborts the whole interpreter ("Tcl_AsyncDelete").
    import gc

    gc.collect()


if __name__ == "__main__":
    unittest.main()
