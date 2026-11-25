"""Time and state shown on the desk instead of said (docs/UX_REVIEW.md, R8, N2, N3, S3): one line where the app
speaks, the tools included; scenes as the unit's footswitches; a recording as a lamp and a strip that drains;
the playhead moved by the clock; takes that say what they hold and can be put away. On the real desk over a
simulated unit, kept off the screen."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import time
import unittest

from lab.desk.theme import FAULT, RECORDING, SIDE, SOFT
from lab.desk.window import take_words
from lab.sound.chain import Chain
from lab.sound.sounds import Sound

from support import PLEXI, V30, DeskCase, chain, phrase
from test_lab_desk_faults import every, texts
from test_lab_desk_top import take


class OneLineTest(DeskCase):
    def test_a_tool_says_what_happened_on_the_desks_status_line_and_has_no_line_of_its_own(self):
        from lab.tools.menu import opener

        self.desk.toggle("More")
        opener("levels")(self.desk)
        tool = self.desk.hosted
        tool.say("Matched to the music.")
        self.assertEqual(self.studio.state.status, "Matched to the music.")
        tool.fault("The unit did not answer.")
        self.assertEqual((self.studio.state.status, self.studio.state.status_kind),
                         ("The unit did not answer.", "fault"))
        self.root.update()
        self.assertEqual(str(self.desk.line.cget("fg")), FAULT)
        lines = [widget for widget in every(tool.frame) if widget.winfo_class() == "Label"
                 and str(widget.cget("textvariable")) == str(tool.status)]
        self.assertEqual(lines, [])


class FootswitchTest(DeskCase):
    def test_scenes_are_drawn_as_footswitches_with_their_names_the_chosen_one_lit(self):
        named = Chain(rows=chain(PLEXI, V30).rows, labels=("Rhythm", "Lead"))
        self.put("b", Sound("Two scenes", named, {"kind": "designed"}))
        self.root.update()
        said = texts(self.desk.scene_row)
        self.assertEqual(said, ["Rhythm", "Lead"])                       # names, not A and B
        lamps = [widget for widget in every(self.desk.scene_row) if widget.winfo_class() == "Canvas"]
        self.assertEqual(len(lamps), 2)
        fills = [lamp.itemcget(lamp.find_withtag("switch")[0], "fill") for lamp in lamps]
        self.assertEqual(fills[self.studio.state.scene], SIDE["b"])
        other = 1 - self.studio.state.scene
        self.assertNotEqual(fills[other], SIDE["b"])
        lamps[other].event_generate("<ButtonRelease-1>")
        self.assertEqual(self.studio.state.scene, other)


class RecordingLampTest(DeskCase):
    def test_while_a_take_is_recorded_the_dot_is_lit_and_the_strip_drains(self):
        self.root.update()
        strip = self.desk.phrase_strip
        self.studio.doing["record"] = "Opening the input…"
        self.studio.recording = (None, 8.)
        self.studio._set()
        self.root.update()
        self.assertNotEqual(str(self.desk.record_symbol.cget("fg")), RECORDING)   # half lit while it opens
        self.assertTrue(strip.find_withtag("recording"))
        self.assertFalse(strip.find_withtag("left"))
        self.studio.recording = (time.monotonic() - 4., 8.)
        self.studio._set()
        self.root.update()
        self.assertEqual(str(self.desk.record_symbol.cget("fg")), RECORDING)
        left, _, right, _ = strip.coords(strip.find_withtag("left")[0])
        width = max(strip.winfo_width(), 40)
        self.assertAlmostEqual(left / width, .5, delta=.05)                  # half of it still to come
        self.assertEqual(right, width)
        del self.studio.doing["record"]
        self.studio._set()
        self.root.update()
        self.assertEqual(str(self.desk.record_symbol.cget("fg")), SOFT)
        self.assertFalse(strip.find_withtag("recording"))


class PlayheadTest(DeskCase):
    """The line where the loop is now was placed only when the desk was drawn again, not by the clock: it stood
    still while the phrase looped."""

    def test_the_clock_moves_the_line_where_the_loop_is(self):
        self.root.update()
        audio = phrase(4.)
        strip = self.desk.phrase_strip
        strip.show((audio, 0, len(audio)), time.monotonic())
        x = strip.coords(strip.head)[0]
        strip.since -= 1.                                   # a second later
        self.desk.tick()
        self.assertGreater(strip.coords(strip.head)[0], x + 5)


class TakesTest(DeskCase):
    def test_a_take_says_its_setting_length_what_was_played_and_its_instrument_only_when_another(self):
        item = {"instrument": "Charvel", "setting": "Neck", "seconds": 8.2, "gesture": "open strings strummed once",
                "recorded": "2026-11-20T10:00:00+00:00"}
        self.assertEqual(take_words(item, "Charvel"), "Neck · 8 s · open strings strummed once · 11-20 10:00")
        self.assertEqual(take_words(item, "Les Paul"),
                         "Charvel · Neck · 8 s · open strings strummed once · 11-20 10:00")

    def test_a_take_is_put_away_not_deleted_and_the_phrase_in_use_stays(self):
        for name in ("first", "second"):
            take(self.home, name, self.studio.state.instrument, "Neck", f"2026-11-20T10:00:0{len(name)}+00:00")
        self.studio.use_phrase("first", phrase(), 0, len(phrase()))
        self.studio.put_take_away("first")
        self.assertIn("phrase in use", self.studio.state.status)
        self.assertTrue((self.home / "phrases" / "first").exists())
        self.studio.put_take_away("second")
        self.assertFalse((self.home / "phrases" / "second").exists())
        self.assertTrue((self.home / "phrases put away" / "second" / "phrase.json").exists())
        self.assertEqual([item["id"] for item in self.studio.recorded_phrases()], ["first"])


class WorkedOnTest(DeskCase):
    def test_the_edge_of_the_tile_a_request_is_for_breathes_and_rests_when_it_is_done(self):
        self.put("a", Sound("Lead", chain(PLEXI, V30), {"kind": "designed"}))
        self.put("b", Sound("Rhythm", chain(PLEXI, V30), {"kind": "designed"}))
        self.studio.select("a")
        key = object()
        self.studio.toward[key] = "b"
        self.studio.begin(key, "Revising B…")
        self.assertEqual(self.studio.worked_on(), {"b"})
        tile = self.desk.tiles["b"][0]
        self.desk.breathe(now=0.)
        bright = str(tile.cget("highlightbackground"))
        self.desk.breathe(now=.7)
        self.assertNotEqual(str(tile.cget("highlightbackground")), bright)
        self.assertEqual(bright, SIDE["b"])
        self.assertEqual(str(self.desk.tiles["a"][0].cget("highlightbackground")), SIDE["a"])   # selected, at rest
        self.studio.end(key)
        self.assertEqual(self.studio.worked_on(), set())
        self.desk.breathe(now=.7)
        from lab.desk.theme import RULE

        self.assertEqual(str(tile.cget("highlightbackground")), RULE)


class LetterFitsTest(DeskCase):
    def test_a_tiles_letter_is_never_cut_off_and_is_large_again_when_there_is_room(self):
        from support import DEFAULT_SIZE, SMALLEST_SIZE, inside
        from lab.desk.window import BADGE

        from unittest import mock

        from test_lab_desk_symbols import three

        self.script.answers.append(three())
        self.studio.ask("make a classic rock crunch")
        self.pump()
        self.studio.copy_to_a()
        self.studio.turn({"brightness": .3, "tightness": .2, "body": .1})
        heard = {"distortion": "crunch", "brightness": "bright", "tail": "dry"}
        for side in ("a", "b"):
            sound = self.studio.sound(side)
            sound.heard = heard
            self.studio.store.save(sound)
        mock.patch.object(self.studio, "unheard", return_value=None).start()
        self.addCleanup(mock.patch.stopall)
        self.studio.say("A status that is long enough to take two lines of the column under the tiles when a "
                        "panel is open in the smallest window the desk allows.")
        self.desk.toggle("Phrase")
        for size in (SMALLEST_SIZE, DEFAULT_SIZE):
            self.resize(size)
            self.desk.fit_badges()
            self.root.update()
            for side in ("a", "b"):
                tile, badge = self.desk.tiles[side][0], self.desk.tiles[side][5]
                self.assertTrue(inside(badge, tile), (size, side))
                self.assertGreaterEqual(badge.winfo_height(), badge.winfo_reqheight(), (size, side))
        self.desk.toggle("Phrase")
        self.desk.fit_badges()
        self.root.update()
        self.assertEqual(getattr(self.desk.tiles["b"][5], "size", BADGE), BADGE)


class TypeScaleTest(unittest.TestCase):
    def test_text_is_set_in_four_sizes_and_never_under_nine_points(self):
        import re
        from pathlib import Path

        lab = Path(__file__).resolve().parent.parent / "lab"
        found = set()
        for folder in ("desk", "tools", "learner"):
            for path in (lab / folder).glob("*.py"):
                if path.name == "theme.py":         # the icon font's size is a symbol's, not text's
                    continue
                text = path.read_text(encoding="utf-8")
                found |= {(path.name, int(size)) for size in re.findall(r"\(FONT, (\d+)", text)}
                found |= {(path.name, int(size)) for size in re.findall(r"[(,]\s*size=(\d+)", text)}
        self.assertEqual({(name, size) for name, size in found if size not in (9, 10, 12, 14, 34)}, set())


if __name__ == "__main__":
    unittest.main()
