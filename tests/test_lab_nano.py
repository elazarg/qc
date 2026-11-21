"""The Nano Cortex sheet: what to capture, which cab, and what goes in the fixed slots."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import unittest

from lab.gear import nano
from lab.sound.chain import Block, Chain, Row

GATE, OPTO, GREEN_808, MYTH, BRIT_2203, CAB_MESA_OS_A, VINTAGE_CHORUS, TAPE, PLATE = \
    16003, 5007, 5, 1, 1001, 12031, 7000, 6004, 8013
WHAM, CRYING_WAH, LEGENDARY_M, LEGENDARY_ST, PLEXI, CAB_PRINCETON, GRAPHIC = \
    18006, 9013, 5001, 5012, 1093, 12045, 4002
SCENE_SWITCHED = (True, False, False, False, False, False, False, False)


def chain(*blocks):
    return Chain((Row(tuple(blocks), 1, 19),))


class Sheet(unittest.TestCase):
    def test_amp_and_drives_that_stay_on_are_captured(self):
        sheet = nano.sheet(chain(Block(GATE), Block(GREEN_808).set_display(0, 3), Block(MYTH, (), SCENE_SWITCHED),
                                 Block(BRIT_2203).set_display(0, 7), Block(CAB_MESA_OS_A), Block(TAPE).set_display(8, 420),
                                 Block(PLATE)))
        self.assertEqual(sheet.capture["blocks"], ["Green 808", "Brit 2203"])
        self.assertEqual(sheet.capture["settings"]["Brit 2203"], {"GAIN": 7.0})
        self.assertIn("Myth Drive", " ".join(sheet.left_out))       # switched by scene and no Klon on the Nano
        self.assertTrue(sheet.input_gate.startswith("on"))

    def test_the_same_stock_cab_is_named(self):
        sheet = nano.sheet(chain(Block(BRIT_2203), Block(CAB_MESA_OS_A)))
        self.assertEqual((sheet.ir["cab"], sheet.ir["match"]), ("412 CA Stand OS A V30 '01", "same"))

    def test_effects_go_to_their_fixed_slots_with_values(self):
        sheet = nano.sheet(chain(Block(OPTO), Block(BRIT_2203), Block(CAB_MESA_OS_A), Block(VINTAGE_CHORUS),
                                 Block(TAPE).set_display(8, 420), Block(PLATE)))
        slots = {slot["slot"]: slot for slot in sheet.slots}
        self.assertEqual(slots["Pre 1"]["device"], "Opto Comp (M)")
        self.assertEqual((slots["Post 1"]["device"], slots["Post 1"]["match"]), ("Chief CE2W (ST)", "nearest"))
        self.assertEqual(slots["Post 2"]["device"], "Tape Delay (ST)")
        self.assertEqual(slots["Post 2"]["values"]["DELAY TIME"], "420 ms")
        self.assertEqual((slots["Post 3"]["device"], slots["Post 3"]["match"]), ("Hall", "nearest"))

    def test_pitch_takes_pre_1_and_extra_blocks_are_left_out(self):
        sheet = nano.sheet(chain(Block(CRYING_WAH), Block(LEGENDARY_M), Block(WHAM), Block(PLEXI), Block(CAB_PRINCETON),
                                 Block(LEGENDARY_ST), Block(GRAPHIC)))
        slots = {slot["slot"]: slot["device"] for slot in sheet.slots}
        self.assertEqual(slots["Pre 1"], "Wham")
        self.assertEqual(slots["Pre 2"], "Crying Wah")
        self.assertEqual({slots["Post 1"], slots["Post 2"]}, {"Legendary 87 (ST)", "Graphic-9"})
        self.assertTrue(any("Legendary 87 (M)" in note for note in sheet.left_out))
        self.assertEqual(sheet.ir["cab"], "110 US PRN C10R")

    def test_a_chorus_before_the_amp_moves_after_the_capture(self):
        sheet = nano.sheet(chain(Block(VINTAGE_CHORUS), Block(BRIT_2203), Block(CAB_MESA_OS_A)))
        post = next(slot for slot in sheet.slots if slot["slot"] == "Post 1")
        self.assertTrue(post["moved"])
        self.assertEqual(sheet.left_out, [])

    def test_no_amp_means_no_capture(self):
        sheet = nano.sheet(chain(Block(TAPE)))
        self.assertEqual(sheet.capture["blocks"], [])
        self.assertIn("Capture: none", sheet.lines()[0])

    def test_lines_read_as_a_sheet(self):
        lines = nano.sheet(chain(Block(BRIT_2203), Block(CAB_MESA_OS_A), Block(TAPE, (), SCENE_SWITCHED))).lines()
        self.assertTrue(lines[0].startswith("Capture: Brit 2203"))
        self.assertTrue(lines[1].startswith("IR Loader: 412 CA Stand OS A V30 '01"))
        self.assertIn("switch by hand", lines[2])

    def test_a_value_that_differs_by_scene_is_given_for_scene_a_and_marked(self):
        # Regression: a value that differed by scene was dropped from a slot without a word, and shown raw on the
        # capture, though the Nano has no scenes.
        gain = (.2, .9) + (.2,) * 6
        mix = (.1, .6) + (.1,) * 6
        sheet = nano.sheet(Chain((Row((Block(BRIT_2203).set(0, gain), Block(CAB_MESA_OS_A), Block(TAPE).set(0, mix)),
                                      1, 19),), ("Rhythm", "Lead") + ("",) * 6))
        self.assertIn("scene A's", str(sheet.capture["settings"]["Brit 2203"]["GAIN"]))
        tape = next(slot for slot in sheet.slots if slot["slot"] == "Post 2")
        self.assertTrue(any("scene A's" in value for value in tape["values"].values()), tape["values"])
        self.assertIn("the Nano has none", sheet.scenes)
        self.assertIn("Rhythm", sheet.scenes)
        self.assertTrue(any(line.startswith("Scenes: ") for line in sheet.lines()))
        self.assertEqual(nano.sheet(chain(Block(BRIT_2203), Block(CAB_MESA_OS_A))).scenes, "")

    def test_what_sat_between_a_captured_drive_and_the_amp_is_said_to_have_moved(self):
        # Regression: the capture takes the drive with the amp, which puts a compressor between them before the
        # drive; the sheet did not say so.
        sheet = nano.sheet(chain(Block(GREEN_808), Block(OPTO), Block(BRIT_2203), Block(CAB_MESA_OS_A)))
        self.assertEqual(sheet.capture["blocks"], ["Green 808", "Brit 2203"])
        pre = next(slot for slot in sheet.slots if slot["slot"] == "Pre 1")
        self.assertEqual(pre["ahead_of"], "Green 808")
        self.assertTrue(any("moved" in line and "Green 808" in line for line in sheet.lines()))
        before = nano.sheet(chain(Block(OPTO), Block(GREEN_808), Block(BRIT_2203), Block(CAB_MESA_OS_A)))
        self.assertEqual(next(slot for slot in before.slots if slot["slot"] == "Pre 1")["ahead_of"], "")

    def test_values_are_said_to_be_the_quad_cortex_s(self):
        # Regression: that the Nano's device has the same controls is an assumption, and the sheet did not label it.
        lines = nano.sheet(chain(Block(BRIT_2203), Block(CAB_MESA_OS_A), Block(TAPE).set_display(8, 420))).lines()
        self.assertTrue(any("Quad Cortex's" in line and "Cortex Cloud" in line for line in lines))
        bare = nano.sheet(chain(Block(BRIT_2203), Block(CAB_MESA_OS_A))).lines()
        self.assertFalse(any("Cortex Cloud" in line for line in bare))      # no values given, nothing to label

    def test_research_slots_follow_the_manual(self):
        table = nano.research()
        self.assertEqual(table["chain"], ["Input Gate", "Pre 1", "Pre 2", "Neural Capture", "IR Loader",
                                          "Post 1", "Post 2", "Post 3"])
        self.assertIn("pitch", table["slots"]["Pre 1"])
        self.assertNotIn("pitch", table["slots"]["Pre 2"])
        self.assertEqual([kind for kind in ("delay", "reverb", "modulation")
                          if kind in table["slots"]["Post 2"]], ["delay"])
        self.assertEqual(len(table["cabs"]), 10)
        for source in table["sources"].values():
            self.assertTrue(source["url"].startswith("https://neuraldsp.com/")
                            or source["url"].startswith("https://support.neuraldsp.com/"))


if __name__ == "__main__":
    unittest.main()
