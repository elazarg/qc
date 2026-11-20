"""Every side tool opens on the desk, does its main work on the simulated unit through the host, and closes."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import tkinter as tk
import unittest

from lab.sound import catalog
from lab.sound.sounds import Sound
from lab.tools.menu import TOOLS, opener

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

    def test_levels_match_a_and_b_and_a_song_s_parts(self):
        quiet = Sound("Quiet", chain(gain_db=-9.), {"kind": "designed"})
        loud = Sound("Loud", chain(gain_db=0.), {"kind": "designed"})
        self.put("a", quiet)
        self.put("b", loud)
        window = self.open("levels")
        window.measure()
        pump(self.studio, self.root)
        trims = {row["name"]: row["trim_db"] for row in window.planned["rows"]}
        self.assertAlmostEqual(trims["B"] - trims["A"], -9., delta=.3)
        window.apply()
        pump(self.studio, self.root)
        window.change({**window.settings, "loudness_db": 3})
        pump(self.studio, self.root)

        self.studio.set(song="Time")
        self.studio.set(side="a")
        self.studio.add_part("Clean")
        self.studio.set(side="b")
        self.studio.add_part("Lead")
        window.what.set("The song's parts")
        window.measure()
        pump(self.studio, self.root)
        window.apply()
        parts = self.studio.store.song("Time")["parts"]
        leveled = [self.studio.store.sound(part["sound"]) for part in parts]
        self.assertTrue(all(sound.chain.rows[0].blocks[-1][1].model == catalog.GAIN_BLOCK for sound in leveled))
        self.assertNotIn(quiet.id, {part["sound"] for part in parts})

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
