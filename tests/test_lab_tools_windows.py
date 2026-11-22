"""Every side tool, shown in the desk's window, does its main work on the simulated unit through the host.

How each is hosted (in the one window, fitting, with the way back) is in test_lab_one_window.py; choosing files
in place is in test_lab_tools_choices.py. Here: what the tools do.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from lab.sound import catalog
from lab.sound.sounds import Sound
from lab.tools.menu import TOOLS, opener
from lab.tools.usage import opens as usage_opens

from support import DELAY, PLEXI, V30, DeskCase, chain


class ToolsTest(DeskCase):
    def look(self):
        """The engine's own state, read where it is held."""
        import lab.tools.probe as probe

        probe.peek = lambda engine, *, stop=None: {"levels": dict(engine.levels), "mode": engine.mode,
                                                   "shown": engine.shown[0] if engine.shown else None,
                                                   "listening": engine.listening is not None}
        self.addCleanup(lambda: vars(probe).pop("peek", None))
        return self.studio.host.call("lab.tools.probe.peek").result(10)

    def test_every_tool_is_made_once_and_shown_one_at_a_time(self):
        self.assertEqual(len({name for _, name in TOOLS}), len(TOOLS))
        self.assertNotIn("library", [name for _, name in TOOLS])     # it is the Sounds panel now
        for title, name in TOOLS:
            tool = self.tool(name)
            self.assertIs(self.desk.hosted, tool, title)
            self.assertEqual(tool.TITLE, self.desk.side_title.cget("text"))
            self.assertIs(opener(name)(self.desk), tool)            # opening it again shows the same one
        self.assertEqual(len(self.desk.tools), len(TOOLS))
        self.desk.back()
        self.assertEqual(usage_opens(self.home)["Levels"], 1)

    def test_levels_leave_a_and_b_to_the_desk_and_match_a_song_s_parts(self):
        quiet = Sound("Quiet", chain(gain_db=-9.), {"kind": "designed"})
        loud = Sound("Loud", chain(gain_db=0.), {"kind": "designed"})
        self.put("a", quiet)
        self.put("b", loud)
        tool = self.tool("levels")
        tool.measure()
        self.assertFalse(tool.busy)                             # no song: nothing is measured, A and B are the desk's
        self.assertIn("song", tool.status.get())

        self.studio.set(song="Time")
        self.studio.set(side="a")
        self.studio.add_part("Clean")
        self.studio.set(side="b")
        self.studio.add_part("Lead")
        tool.measure()
        self.pump()
        trims = {row["name"]: row["trim_db"] for row in tool.planned["rows"]}
        self.assertAlmostEqual(trims["Lead"] - trims["Clean"], -9., delta=.3)
        tool.apply()
        self.pump()
        self.assertIn("Levels verified", tool.status.get())
        parts = self.studio.store.song("Time")["parts"]
        leveled = [self.studio.store.sound(part["sound"]) for part in parts]
        self.assertTrue(all(sound.chain.rows[0].blocks[-1][1].model == catalog.GAIN_BLOCK for sound in leveled))
        self.assertNotIn(quiet.id, {part["sound"] for part in parts})

    def test_music_reference_is_saved_and_fits_a_preset_copy_without_the_preview_cut(self):
        original = Sound("Quiet lead", chain(gain_db=-9.), {"kind": "preset"})
        self.put("b", original)
        tool = self.tool("levels")
        taken = self.studio.phrase[1]
        reading = self.studio.host.call("lab.tools.levels.measure_sounds", {"lead": original.chain}, taken).result(20)
        target = round(reading["lead"]["lufs"] + 4., 1)
        reference = {"lufs": target, "endpoint": "Quad Cortex on the simulated desk"}
        with mock.patch("lab.tools.levels.media.capture", return_value=reference):
            tool.measure_music()
            self.pump()
        self.assertEqual(self.studio.media_reference, reference)
        tool.fit_selected()
        self.pump()
        self.assertIn("Verified +4.", tool.status.get())
        self.assertNotEqual(self.studio.sound("b").id, original.id)
        self.assertEqual(self.studio.store.sound(original.id).chain, original.chain)
        self.studio.play("phrase")
        self.pump()
        self.assertAlmostEqual(self.look()["levels"]["b"], 0., delta=.25)

    def test_music_reference_plays_unmeasured_at_native_level_then_matches_with_clean_gain(self):
        original = Sound("Quiet lead", chain(gain_db=-15.), {"kind": "preset"})
        self.put("b", original)
        taken = self.studio.phrase[1]
        reading = self.studio.host.call("lab.tools.levels.measure_sounds", {"lead": original.chain}, taken).result(20)
        target = round(reading["lead"]["lufs"] + 13., 1)
        self.studio.set_media_reference({"lufs": target, "endpoint": "Test music"})
        self.studio.play("phrase")
        self.pump()
        self.assertEqual(self.look()["levels"]["b"], 0.)
        self.studio.stop()
        self.pump()
        self.studio.ear.measure_level(self.studio.sound("b"))
        self.pump()
        self.studio.play("phrase")
        self.pump()
        preview, boost, _ = self.studio.prepared_audition[1]
        self.assertNotEqual(preview, original.chain)
        self.assertGreater(boost, 5.)
        self.assertAlmostEqual(self.look()["levels"]["b"] + boost, 13., delta=1.)
        self.assertEqual(self.studio.sound("b").chain, original.chain)
        self.studio.save()
        self.pump()
        saved = self.studio.sound("b").saved
        stored, _ = self.studio.host.call("read", saved["slot"]).result(10)
        after = self.studio.host.call("lab.tools.levels.measure_sounds", {"lead": stored}, taken).result(10)
        self.assertAlmostEqual(after["lead"]["lufs"], target, delta=1.)

    def test_swapping_cab_irs_preserves_the_sound_name(self):
        from lab.qc.assets import Asset

        self.put("b", Sound("Named lead", chain(PLEXI, V30), {"kind": "designed"}))
        for key in ("CIR_1ab", "CIR_2ab"):
            self.studio.use_asset(Asset("ir", key, "Test IR"))
            self.pump()
        sound = self.studio.sound("b")
        self.assertEqual(sound.name, "Named lead")
        self.assertEqual((sound.chain.block(0, 1).get(1), sound.chain.block(0, 1).get(9)), ("CIR_2ab", "CIR_2ab"))

    def test_the_learner_records_one_idle_reference_for_multiple_presets(self):
        from lab.learner import idle

        tool = self.tool("soundcheck")
        tool.open_idle_reference()
        screen = self.desk.learning
        self.assertEqual(screen.view, "Idle reference")
        screen.idle_reference.record()
        self.pump()
        setup = idle.selected(self.home, "Charvel")
        self.assertIsNotNone(setup)
        self.desk.leave_learning()
        for level in (-9., 0.):
            self.put("b", Sound("Example", chain(gain_db=level), {"kind": "designed"}))
            tool.check_idle()
            self.pump()
            self.assertIn("recorded reference", tool.idle_text.cget("text"))
            self.assertIn("With silent input", tool.idle_text.cget("text"))
        self.assertEqual(len(list((self.home / "learner" / "idle").rglob("*.wav"))), 1)

    def test_a_listening_level_stays_on_when_the_tool_is_left_and_the_desk_offers_the_way_back(self):
        self.put("a", Sound("Quiet", chain(gain_db=-9.), {"kind": "designed"}))
        self.put("b", Sound("Loud", chain(gain_db=0.), {"kind": "designed"}))
        tool = self.tool("levels")
        tool.toggle_mute()
        self.pump()
        self.assertEqual(self.look()["levels"], {"a": -40., "b": -40.})
        self.assertTrue(self.studio.listening["muted"])
        self.desk.back()
        self.pump()
        self.assertEqual(self.look()["levels"], {"a": -40., "b": -40.})     # still on, and the desk says so
        self.assertEqual(self.desk.level_row.winfo_manager(), "pack")
        self.assertEqual(self.desk.level_note["text"], "Listening level: Muted")
        self.desk.clear_listening()                                         # the desk's own way back
        self.pump()
        found = self.look()
        self.assertEqual(found["levels"], {"a": -12., "b": -12.})
        self.assertFalse(found["listening"])
        self.assertIsNone(self.studio.listening)
        self.assertEqual(self.desk.level_row.winfo_manager(), "")
        self.tool("levels")
        self.assertEqual(tool.mute.cget("text"), "Mute")                    # and the tool shows the same again

    def test_after_a_tool_played_through_the_unit_the_desk_says_what_is_true(self):
        self.put("a", Sound("Echo Lead", chain(DELAY), {"kind": "designed"}))
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        self.studio.play("phrase", "b")
        self.pump()
        self.assertEqual((self.studio.state.mode, self.look()["mode"]), ("phrase", "phrase"))
        tool = self.tool("search")
        tool.reference_from_a()
        self.assertEqual((self.studio.state.mode, self.studio.state.shown), (None, None))   # said at once
        self.pump()
        self.assertEqual((self.look()["mode"], self.look()["shown"]), (None, None))
        self.assertEqual((self.studio.state.mode, self.studio.state.shown), (None, None))
        self.studio.play("phrase", "b")                         # and one press plays again
        self.pump()
        self.assertEqual((self.studio.state.mode, self.look()["mode"]), ("phrase", "phrase"))

    def test_a_tool_leaves_the_desk_s_own_working_line_alone(self):
        tool = self.tool("usage")
        self.studio.begin("design", "Designing three sounds…")
        tool.check_unit()
        self.pump()
        self.assertEqual(self.studio.state.working, "Designing three sounds…")
        self.studio.end("design")
        tool.check_unit()
        self.assertEqual(self.studio.state.working, "Asking the Quad Cortex…")      # its own line, while it works
        self.pump()
        self.assertEqual(self.studio.state.working, "")

    def test_nothing_is_played_through_the_unit_while_something_else_holds_the_audio_input(self):
        self.put("a", Sound("Clean", chain(gain_db=0.), {"kind": "designed"}))
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        tool = self.tool("search")
        self.studio.audio_held_by = "learner"
        self.addCleanup(lambda: setattr(self.studio, "audio_held_by", None) if self.studio else None)
        tool.reference_from_a()
        self.assertFalse(tool.busy)
        self.assertIn("The Guitar Learner is using the input.", tool.status.get())
        self.assertEqual(self.studio.state.shown, "pair")       # and the desk was told nothing
        used = self.tool("usage")
        used.check_unit()                                       # reading the unit plays nothing: it goes ahead
        self.pump()
        self.assertIn("cache slots", used.unit.cget("text"))
        self.studio.audio_held_by = None
        self.tool("search")
        tool.reference_from_a()
        self.pump()
        self.assertIsNotNone(tool.reference)

    def test_search_finds_by_words_and_matches_a_reference(self):
        self.put("a", Sound("Echo Lead", chain(DELAY), {"kind": "designed"}))
        self.put("b", Sound("Dry Rhythm", chain(gain_db=0.), {"kind": "designed"}))
        tool = self.tool("search")
        tool.query.insert(0, "echo")
        tool.find()
        self.assertEqual(tool.results[0]["title"], "Echo Lead")
        tool.query.delete(0, "end")
        tool.reference_from_a()
        self.pump()
        self.assertIsNotNone(tool.reference)
        tool.match()
        self.pump()
        self.assertEqual(tool.results[0]["title"], "Echo Lead")
        self.assertTrue(tool.results[0]["same_take"])
        tool.box.selection_set(1)
        tool.put("a")
        self.pump()
        self.assertEqual(self.studio.sound("a").name, tool.results[1]["title"])

    def test_tone_search_goes_on_when_it_is_left_and_shows_what_it_found_when_it_is_back(self):
        from lab.tools import search

        self.put("a", Sound("Echo Lead", chain(DELAY), {"kind": "designed"}))
        self.put("b", Sound("Dry Rhythm", chain(gain_db=0.), {"kind": "designed"}))
        tool = self.tool("search")
        tool.reference_from_a()
        self.pump()
        tool.match()
        self.desk.back()                                        # left while the unit is still rendering
        self.pump()
        self.assertEqual(len(search.load_cache(self.home)), 2)
        self.assertIs(self.tool("search"), tool)
        self.assertEqual(tool.box.size(), 2)                    # ranked while it was away, and here now

    def test_a_drag_on_the_field_is_one_step_for_undo(self):
        first = Sound("First", chain(PLEXI, V30), {"kind": "designed"})
        self.put("b", first)
        self.put("b", Sound("Second", chain(PLEXI, V30, DELAY), {"kind": "designed"}))
        tool = self.tool("field")
        before = len(self.studio.history)
        tool.push(SimpleNamespace(x=40, y=40))
        for step in range(45):
            tool.point = tool.normal(-1 + step * 2 / 44, 0.)
            tool.hear_point()                                   # what each tick of the drag does
        self.assertEqual(len(self.studio.history), before)      # heard, not kept, while it lasts
        self.assertEqual(self.studio.sound("b").knobs, {})
        tool.apply_point()                                      # the button is let go
        self.pump()
        self.assertEqual(len(self.studio.history), before + 1)
        self.assertGreater(self.studio.sound("b").knobs.get("drive", 0), 0)
        self.studio.undo()
        self.assertEqual(self.studio.sound("b").knobs, {})
        self.studio.undo()
        self.pump()
        self.assertEqual(self.studio.sound("b").name, "First")  # the sound before is still within reach

    def test_a_sweep_on_the_field_is_one_step_for_undo_and_ends_where_it_is_when_the_field_is_left(self):
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        tool = self.tool("field")
        tool.set_point(-1., 0.)
        tool.mark("A")
        tool.set_point(1., 0.)
        tool.mark("B")
        tool.set_point(0., 0.)
        self.pump()
        before = len(self.studio.history)
        tool.toggle_sweep()
        for step in range(1, 9):
            tool.after_cancel(tool.sweep_job)
            tool.sweep_step(step)
        self.pump()
        self.assertIsNone(tool.sweep_job)
        self.assertEqual(len(self.studio.history) - before, 1)       # eight steps heard, one kept
        self.assertEqual(self.studio.sound("b").knobs, {key: value for key, value in tool.marks["B"].values.items()
                                                        if value})
        tool.toggle_sweep()
        tool.after_cancel(tool.sweep_job)
        tool.sweep_step(3)
        self.desk.back()                                             # left on the way from A to B
        self.assertIsNone(tool.sweep_job)
        self.pump()
        self.assertEqual({key: value for key, value in tool.values.items() if value}, self.studio.sound("b").knobs)

    def test_the_workbench_renders_versions_keeps_one_and_sweeps_a_control_chosen_in_place(self):
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        bench = self.tool("workbench")
        self.assertEqual(bench.hear.winfo_manager(), "")             # nothing to audition yet: not shown
        bench.kind.set("Strengths")
        bench.kind_changed()
        bench.argument.insert(0, "darker")
        bench.render()
        self.pump()
        self.assertEqual(len(bench.auditions), 3)
        self.assertEqual(bench.hear.winfo_manager(), "pack")
        bench.last = next(iter(bench.auditions))
        bench.keep()
        bench.put_kept()
        self.pump()
        self.assertNotEqual(self.studio.sound("b").name, "Plexi")

        bench.kind.set("Sweep")
        bench.kind_changed()
        names = bench.control_list.get(0, "end")
        self.assertTrue(any("GAIN" in name for name in names))      # the controls, listed in place
        bench.argument.insert(0, "3 7")
        bench.render()
        self.assertIn("Choose a control", bench.status.get())
        bench.control_list.selection_set(next(index for index, name in enumerate(names) if "GAIN" in name))
        bench.render()
        self.pump()
        self.assertEqual(len(bench.auditions), 2)
        self.assertEqual(bench.earlier.size(), 2)
        bench.earlier.selection_set(1)
        bench.open_earlier()                                         # the button beside the list
        self.assertEqual(len(bench.auditions), 3)

    def test_versions_and_the_unit_steps_between_versions_and_reads_the_unit_back(self):
        first = Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}, explanation="A plain Plexi.",
                      sources=[{"title": "A page about Plexis", "url": "https://example.com/plexi"}])
        self.put("b", first)
        self.put("b", Sound("Plexi darker", chain(PLEXI, V30, DELAY), {"kind": "revised", "request": "darker"},
                            parent=first.id))
        tool = self.tool("controls")
        self.assertIn("Version 2 of 2", tool.version.cget("text"))
        tool.read_back()
        self.pump()
        self.assertIn("Brit Plexi", tool.held.cget("text"))          # B, on its own rows of the pair
        tool.step(-1)                                                # back to the version it was made from
        self.pump()
        self.assertEqual(self.studio.sound("b").name, "Plexi")
        self.assertIn("Version 1 of 2", tool.version.cget("text"))
        why = tool.why_text.cget("text")
        self.assertIn("A plain Plexi.", why)
        self.assertIn("A page about Plexis · https://example.com/plexi", why)   # the address as text, in place
        self.assertIn("GAIN", tool.moves_text.cget("text"))          # what a knob moves in this chain
        for gone in ("turned", "toggle_base", "reset", "save", "fill_board"):  # the Shape panel's and the desk's
            self.assertFalse(hasattr(tool, gone), gone)

    def test_the_sound_check_and_usage_ask_the_unit(self):
        self.put("b", Sound("Plexi", chain(PLEXI, V30), {"kind": "designed"}))
        check = self.tool("soundcheck")
        check.check(record=False)
        check.unit_check()
        check.stage()
        self.pump()
        self.assertIn("Dry peak", check.take_text.cget("text"))
        self.assertIn("4.1.1", check.unit_text.cget("text"))
        self.assertEqual(set(check.auditions), set("ABC"))
        check.reveal()
        self.assertIn("As it is", check.stage_text.cget("text"))
        bench = self.tool("workbench")                               # a comparison like any other
        self.assertEqual(bench.earlier.size(), 1)
        self.assertIn("Stage EQ", bench.earlier.get(0))

        used = self.tool("usage")
        used.check_unit()
        self.pump()
        self.assertIn("cache slots", used.unit.cget("text"))
        used.note.insert("1.0", "B sounds boxy")
        used.keep()
        self.assertEqual(used.notes.size(), 1)

    def test_a_tool_that_cannot_be_built_leaves_nothing_behind(self):
        from lab.tools import pod

        before = len(self.desk.side.winfo_children())
        with mock.patch.object(pod.PodTool, "build", lambda tool: (_ for _ in ()).throw(KeyError("broken"))):
            for _ in range(2):
                with self.assertRaises(KeyError):
                    opener("pod")(self.desk)
        self.assertEqual(len(self.desk.side.winfo_children()), before)
        self.assertNotIn("POD Go presets", self.desk.tools)
        self.assertIsNone(self.desk.hosted)
        self.assertNotIn("POD Go presets", usage_opens(self.home))

    def test_pod_presets_on_a_backup_that_cannot_be_read_and_on_one_odd_preset(self):
        from test_lab_podgo import time_preset, write_backup

        bad = self.home / "bad.pgb"
        bad.write_bytes(b"AF6L\x01\0\0\0" + (48 + 5).to_bytes(4, "little") + b"\0" * 36 + b"junk!"
                        + b"00LS" + (48).to_bytes(8, "little") + (5).to_bytes(8, "little") + (1).to_bytes(4, "little")
                        + (100).to_bytes(8, "little") + b"\0" * 4)
        (self.home / "podgo.json").write_text(json.dumps({"backup": str(bad)}), encoding="utf-8")
        tool = self.tool("pod")
        self.assertIs(self.desk.hosted, tool)
        self.assertIn("SL00", tool.status.get())
        self.assertEqual(tool.backup.winfo_manager(), "pack")       # the choice of another backup is on show

        odd = time_preset()
        odd["controller"] = None
        good = write_backup(self.home, {"User": [("Time", time_preset()), ("Odd", odd)]})
        tool.choose(good)
        self.assertEqual(tool.backup.winfo_manager(), "")
        self.assertEqual(tool.list.size(), 2)
        self.assertIn("could not be read", tool.list.get(1))
        tool.list.selection_set(0)
        tool.describe()
        told = tool.note.cget("text")
        self.assertIn("Scream 808 → Green 808 (same gear)", told)      # each block, and why
        self.assertIn("Set from the POD Go:", told)                    # and which values came across
        tool.put()
        self.pump()
        first = self.studio.sound("b")
        self.assertEqual(first.origin["from"], "POD Go")
        # The same preset read again is the same sound, with what was done to it since.
        first.marks = ["keeper"]
        self.studio.store.save(first)
        tool.load(str(good))
        tool.list.selection_set(0)
        tool.put()
        self.pump()
        self.assertEqual((self.studio.sound("b").id, self.studio.sound("b").marks), (first.id, ["keeper"]))
        self.assertEqual(len(self.studio.store.sounds()), 1)


if __name__ == "__main__":
    unittest.main()
