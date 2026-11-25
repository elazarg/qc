"""What the desk shows instead of saying it (docs/UX_REVIEW.md, section 11): dragging onto a tile, the aim and what
was heard as marks on a scale, the round's pips, controls dimmed while they cannot act with the reason on hover,
the heart and the cross, and the phrase as a strip with a dot to record. On the real desk over a simulated unit,
kept off the screen."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
from types import SimpleNamespace
import unittest
from unittest import mock

from lab.desk import scales
from lab.desk.theme import DIM, GLYPH, INK, SIDE
from lab.sound.sounds import Sound

from support import GREENBACK, HIWATT, PLEXI, V30, DeskCase, chain, phrase
from test_lab_design import answer, block, reply


def at(widget, dx=10, dy=10):
    """A pointer event over a widget, in the screen's coordinates."""
    return SimpleNamespace(x_root=widget.winfo_rootx() + dx, y_root=widget.winfo_rooty() + dy, x=dx, y=dy,
                           widget=widget)


def three():
    return reply(*[answer(name, [block(PLEXI, 0, gain=gain), block(GREENBACK, 1)], differs=f"{name} differs")
                   for name, gain in (("One", 3), ("Two", 6), ("Three", 9))])


class DragTest(DeskCase):
    def setUp(self):
        super().setUp()
        self.put("a", Sound("Reference", chain(PLEXI, V30), {"kind": "designed"}))
        self.put("b", Sound("Work", chain(HIWATT, GREENBACK), {"kind": "designed"}, knobs={"brightness": .2}))

    def drag(self, source, what, onto, name=lambda what: "x"):
        drags = self.desk.drags
        drags.press(at(source), what, name)
        drags.move(at(source, 30, 30))
        drags.move(at(onto, 20, 20))
        self.assertTrue(drags.ghost is not None and drags.ghost.winfo_manager() == "place")  # the name follows
        drags.release(at(onto, 20, 20))
        self.assertIsNone(drags.ghost)
        self.pump()

    def test_b_dragged_onto_a_is_copied_there_and_b_stays_as_it_is(self):
        b = self.studio.state.b
        self.drag(self.desk.tiles["b"][0], ("side", "b"), self.desk.tiles["a"][0])
        self.assertEqual(self.studio.state.b, b)
        a = self.studio.sound("a")
        self.assertEqual((a.parent, a.name, a.knobs), (b, "Work", {"brightness": .2}))

    def test_a_design_of_the_round_dragged_onto_a_is_kept_there_to_compare_against(self):
        self.script.answers.append(three())
        self.studio.ask("make a classic rock crunch")
        self.pump()
        designs = [item["sound"] for item in self.studio.round()["candidates"]]
        chips = [chip for chip in self.desk.chips.winfo_children() if "Three" in str(chip.cget("text"))]
        self.drag(chips[0], ("design", designs[2]), self.desk.tiles["a"][0])
        self.assertEqual(self.studio.sound("a").parent, designs[2])
        self.assertEqual(self.studio.state.b, designs[0])           # B is still the design it was

    def test_a_sound_from_history_dragged_onto_a_tile_goes_there(self):
        old = Sound("From before", chain(PLEXI, V30), {"kind": "designed"})
        self.studio.store.save(old)
        self.desk.toggle("History")
        box = [widget for widget in self.desk.tray.winfo_children() if widget.winfo_class() == "Listbox"][0]
        self.drag(box, ("sound", old.id), self.desk.tiles["b"][0])
        self.assertEqual(self.studio.state.b, old.id)

    def test_let_go_anywhere_else_nothing_happens_and_a_press_that_did_not_move_is_a_click(self):
        before = (self.studio.state.a, self.studio.state.b)
        drags = self.desk.drags
        drags.press(at(self.desk.tiles["b"][0]), ("side", "b"), lambda what: "x")
        drags.move(at(self.desk.request, 5, 5))
        drags.release(at(self.desk.request, 5, 5))
        self.pump()
        self.assertEqual((self.studio.state.a, self.studio.state.b), before)
        # The click handler of the same release is told it was a drag, and does nothing.
        clicked = []
        drags.click(lambda: clicked.append(1))()
        self.assertEqual(clicked, [])
        drags.press(at(self.desk.tiles["a"][0]), ("side", "a"), lambda what: "x")
        drags.move(at(self.desk.tiles["a"][0], 12, 12))          # two pixels: not a drag
        drags.click(lambda: clicked.append(1))()
        drags.release(at(self.desk.tiles["a"][0], 12, 12))
        self.assertEqual(clicked, [1])

    def test_while_blind_a_tile_dragged_onto_the_other_does_nothing(self):
        with mock.patch("random.random", return_value=.9):
            self.studio.blind()
        before = (self.studio.state.a, self.studio.state.b)
        self.drag(self.desk.tiles["b"][0], ("side", "b"), self.desk.tiles["a"][0])
        self.assertEqual((self.studio.state.a, self.studio.state.b), before)


class ScalesTest(DeskCase):
    def test_a_word_stands_where_it_is_on_its_scale(self):
        self.assertEqual(scales.place("distortion", "clean"), 0.)
        self.assertEqual(scales.place("distortion", "saturated"), 1.)
        self.assertEqual(scales.place("brightness", "balanced"), .5)
        self.assertIsNone(scales.place("tail", "a word it does not have"))

    def test_a_design_of_the_round_shows_the_aim_as_a_ring_and_what_was_heard_as_a_dot(self):
        self.script.answers.append(three())
        self.studio.ask("make a classic rock crunch")
        self.pump()
        drawn = self.desk.scales["b"]
        self.assertEqual(drawn.drawn[0]["distortion"], "saturated")      # the round's aim
        self.assertEqual(len(drawn.find_withtag("all")) and [drawn.type(item) for item in drawn.find_all()].count("oval"), 3)
        sound = self.studio.sound("b")
        sound.heard = {"distortion": "crunch", "brightness": "bright", "tail": "dry"}
        self.studio.store.save(sound)
        with mock.patch.object(self.studio, "unheard", return_value=None):
            self.studio.say("")
        self.assertEqual([drawn.type(item) for item in drawn.find_all()].count("oval"), 6)
        self.assertIn("distortion: aim saturated, heard as crunch", drawn.words)
        self.assertEqual(self.desk.scales["a"].drawn, ({}, {}, SIDE["a"]))      # nothing on A: nothing drawn


class PipsTest(DeskCase):
    def test_each_design_says_whether_it_was_listened_to(self):
        self.script.answers.append(three())
        self.studio.ask("make a classic rock crunch")
        self.pump()
        texts = [str(chip.cget("text")) for chip in self.desk.chips.winfo_children()]
        self.assertEqual([text[0] for text in texts[:3]], ["○", "○", "○"])
        self.studio.idle_seconds = 0.
        self.pump()
        self.root.update()
        texts = [str(chip.cget("text")) for chip in self.desk.chips.winfo_children()]
        self.assertEqual([text[0] for text in texts[:3]], ["●", "●", "●"])


class DimmedTest(DeskCase):
    def words(self, widget):
        widget.event_generate("<Enter>")
        self.root.update()
        said = [child.cget("text") for child in self.root.winfo_children() if child.winfo_class() == "Label"]
        widget.event_generate("<Leave>")
        self.root.update()
        return said

    def test_a_control_that_cannot_act_is_dimmed_and_says_why(self):
        desk = self.desk
        self.studio.phrase = None
        self.studio.say("")
        for widget, why in ((desk.guitar, "Nothing is on A or B yet"), (desk.save_button, "Nothing is on B to save"),
                            (desk.copy_link, "Nothing is on B to copy"),
                            (desk.blind_link, "Blind comparison needs a sound on both A and B")):
            self.assertEqual(str(widget.cget("fg")), DIM, why)
            self.assertEqual(self.words(widget), [why])
        self.put("b", Sound("Work", chain(PLEXI, V30), {"kind": "designed"}))
        self.assertEqual(str(desk.guitar.cget("fg")), INK)
        self.assertEqual(str(desk.loop.cget("fg")), DIM)              # no phrase
        self.assertEqual(self.words(desk.loop), ["Record or choose a phrase first"])
        self.assertEqual(self.words(desk.guitar), ["Mute the guitar"])          # live by itself
        desk.mute_or_hear()
        self.assertEqual(self.words(desk.guitar), ["Hear the guitar through the selected sound"])
        self.assertEqual(desk.guitar.cget("text"), GLYPH["muted"])


class MarksTest(DeskCase):
    def test_keep_is_a_heart_filled_once_given_and_not_this_a_cross(self):
        self.put("b", Sound("Work", chain(PLEXI, V30), {"kind": "designed"}))
        heart, cross = self.desk.tiles["b"][4].winfo_children()
        self.assertEqual((heart.cget("text"), cross.cget("text")), (GLYPH["keep"], GLYPH["not this"]))
        heart.event_generate("<ButtonRelease-1>")
        self.root.update()
        self.assertEqual((heart.cget("text"), str(heart.cget("fg"))), (GLYPH["kept"], SIDE["b"]))
        cross.event_generate("<ButtonRelease-1>")
        self.root.update()
        self.assertEqual((heart.cget("text"), str(cross.cget("fg"))), (GLYPH["keep"], SIDE["b"]))


class PhraseStripTest(DeskCase):
    def test_the_phrase_is_drawn_and_a_line_shows_where_the_loop_is_while_it_plays(self):
        whole = phrase(4.)
        self.studio.use_phrase("p", whole, 0, len(whole) // 2)
        self.put("b", Sound("Work", chain(PLEXI, V30), {"kind": "designed"}))
        strip = self.desk.phrase_strip
        self.assertIsNone(strip.head)
        self.studio.play("phrase", "b")
        self.pump()
        self.assertIsNotNone(strip.head)
        first = strip.coords(strip.head)[0]
        strip.since -= .5
        strip.place_head()
        self.assertGreater(strip.coords(strip.head)[0], first)
        self.studio.pause()
        self.pump()
        self.assertIsNone(strip.head)

    def test_the_dot_records_at_once_with_the_setting_last_chosen_if_any(self):
        desk = self.desk
        with mock.patch.object(self.studio, "record") as record:
            desk.record_now()
            self.assertIsNone(desk.panel)                               # no detour through a panel
            desk.choose_setting("Bridge · Series")
            desk.record_now()
        self.assertEqual(record.call_args_list, [mock.call(None, gesture=None),
                                                 mock.call("Bridge · Series", gesture=None)])
        self.assertEqual(self.studio.desk_session["settings"], {"Charvel": "Bridge · Series"})
        self.desk.choose_instrument("Les Paul")
        self.assertIsNone(desk.record_setting)                         # this one has none yet
        self.desk.choose_instrument("Charvel")
        self.assertEqual(desk.record_setting, "Bridge · Series")       # kept for that instrument


if __name__ == "__main__":
    unittest.main()
