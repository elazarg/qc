"""The desk's top row: the instrument chooser, with a picture of each instrument, and what follows when the
instrument is changed. (The learning symbol beside it and the learning screen are in test_lab_one_window.py.)

Everything is on the real desk over a simulated unit, kept off the screen.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import json
import tkinter as tk
import unittest
from unittest import mock

import numpy as np

from lab.desk import pictures
from lab.desk.theme import ACCENT, SOFT, TILE
from lab.hearing.audio import write_wave

from support import DEFAULT_SIZE, SMALLEST_SIZE, DeskCase, inside


def widgets(widget):
    found = []
    for child in widget.winfo_children():
        found.append(child)
        found += widgets(child)
    return found


def take(home, name, instrument, setting, recorded):
    """A phrase recorded on an instrument, as the app keeps it."""
    folder = home / "phrases" / name
    folder.mkdir(parents=True)
    write_wave(folder / "dry.wav", (np.sin(np.arange(48000 * 4) / 48000 * 2 * np.pi * 196) * .2)[:, None].astype(np.float32))
    item = {"id": name, "instrument": instrument, "setting": setting, "recorded": recorded}
    (folder / "phrase.json").write_text(json.dumps(item), encoding="utf-8")
    return item


class PicturesTest(unittest.TestCase):
    def drawn(self, kind, width=56, height=24):
        return pictures.rows(pictures.SHAPES[kind], (pictures.ACROSS, pictures.DOWN), width, height,
                             {"body": SOFT, "neck": "#8a8ca0", "cut": TILE})

    def test_an_instrument_has_the_picture_of_its_kind_and_a_les_paul_the_single_cut(self):
        kinds = {name: pictures.kind_of({"name": name, "kind": kind})
                 for name, kind in (("Charvel", "electric"), ("Les Paul", "electric"), ("Bass", "bass"),
                                    ("Acoustic", "acoustic"), ("Classical", "classical"))}
        self.assertEqual(kinds, {"Charvel": "superstrat", "Les Paul": "single-cut", "Bass": "bass",
                                 "Acoustic": "acoustic", "Classical": "classical"})
        self.assertEqual(set(kinds.values()), set(pictures.SHAPES))
        # One the player adds has its kind's picture; a kind the app does not know, or none, the electric's.
        self.assertEqual(pictures.kind_of({"name": "The old Yamaha", "kind": "classical"}), "classical")
        self.assertEqual(pictures.kind_of({"name": "Gibson Les Paul Studio", "kind": "electric"}), "single-cut")
        self.assertEqual(pictures.kind_of({"name": "Tele"}), "superstrat")
        self.assertEqual(pictures.kind_of({"name": "Oud", "kind": "oud"}), "superstrat")
        self.assertEqual(pictures.kind_of(None), "superstrat")

    def test_every_picture_is_drawn_from_its_shapes_with_smooth_edges_and_none_is_like_another(self):
        drawn = {kind: self.drawn(kind) for kind in pictures.SHAPES}
        for kind, rows in drawn.items():
            self.assertEqual((len(rows), {len(row) for row in rows}), (24, {56}), kind)
            colours = {pixel for row in rows for pixel in row}
            self.assertTrue({SOFT, TILE} < colours, kind)       # the body and what is behind it
            self.assertGreater(len(colours), 12, kind)          # and the shades between them, at the edges
            body = sum(pixel == SOFT for row in rows for pixel in row)
            self.assertTrue(150 < body < 700, (kind, body))     # a body that is seen, in a picture that is not all body
        kinds = list(drawn)
        for index, one in enumerate(kinds):
            for other in kinds[index + 1:]:
                differing = sum(a != b for row, other_row in zip(drawn[one], drawn[other]) for a, b in zip(row, other_row))
                self.assertGreater(differing, 60, (one, other))
        larger = self.drawn("acoustic", 84, 36)
        self.assertEqual((len(larger), len(larger[0])), (36, 84))

    def test_the_acoustics_have_a_sound_hole_and_the_classical_a_slotted_head(self):
        for kind in ("acoustic", "classical"):
            rows = self.drawn(kind, 112, 48)
            self.assertEqual(rows[24][76 if kind == "acoustic" else 77], TILE, kind)      # cut out of the body
            self.assertEqual(rows[24][90], SOFT, kind)
        slotted, flat = self.drawn("classical", 112, 48), self.drawn("acoustic", 112, 48)
        self.assertEqual(slotted[21][12], TILE)                 # a slot in the head
        self.assertNotEqual(flat[21][12], TILE)


class ChooserTest(DeskCase):
    def click(self, widget):
        widget.event_generate("<ButtonPress-1>")
        widget.event_generate("<ButtonRelease-1>")
        self.root.update()

    def test_the_instrument_is_on_a_tile_at_the_top_left_larger_than_the_panel_names(self):
        desk, chooser = self.desk, self.desk.chooser
        self.root.update()
        self.assertEqual(chooser.name.cget("text"), self.studio.state.instrument)
        self.assertEqual(chooser.name.cget("text"), "Charvel")
        names = desk.names.winfo_children()
        self.assertEqual(len(names), 8)
        from tkinter import font

        def size(widget):
            return font.Font(root=self.root, font=widget.cget("font")).actual("size")

        self.assertGreater(size(chooser.name), max(size(name) for name in names))
        self.assertEqual(int(chooser.cget("highlightthickness")), 1)            # a tile with an edge, not a word
        self.assertEqual(chooser.arrow.cget("text"), "▾")
        self.assertTrue(chooser.picture.cget("image"))
        # From the left: the learning symbol, the instrument; the panel names at the right.
        for size_ in (DEFAULT_SIZE, SMALLEST_SIZE):
            self.resize(size_)
            self.assertLess(desk.learn_symbol.winfo_rootx(), chooser.winfo_rootx())
            self.assertLess(chooser.winfo_rootx() + chooser.winfo_width(), desk.names.winfo_rootx())
            self.assertTrue(inside(desk.names, self.root) and inside(chooser, self.root))
        self.resize(DEFAULT_SIZE)
        self.assertFalse(hasattr(desk, "dot"))                                  # the dot that said nothing is gone

    def test_a_click_opens_the_list_beneath_it_inside_the_window_with_every_instrument_and_its_picture(self):
        desk, chooser = self.desk, self.desk.chooser
        for size in (DEFAULT_SIZE, SMALLEST_SIZE):
            self.resize(size)
            self.click(chooser.name)
            found = chooser.list
            self.assertIsNotNone(found)
            self.assertEqual((found.winfo_class(), found.master, found.winfo_toplevel()), ("Frame", self.root, self.root))
            self.assertEqual([widget.winfo_class() for widget in widgets(self.root) if widget.winfo_class()
                              in ("Toplevel", "Menu", "Menubutton")], [])
            self.assertEqual(found.winfo_rootx(), chooser.winfo_rootx())
            self.assertTrue(0 <= found.winfo_rooty() - (chooser.winfo_rooty() + chooser.winfo_height()) <= 4)
            self.assertTrue(inside(found, self.root), size)
            self.assertGreaterEqual(found.winfo_width(), chooser.winfo_width())
            self.assertEqual(chooser.arrow.cget("text"), "▴")
            self.assertEqual(list(chooser.rows), [item["name"] for item in self.studio.store.instruments()])
            self.assertEqual(len(chooser.rows), 5)
            for name, row in chooser.rows.items():
                mark, picture, title = row.winfo_children()
                self.assertTrue(picture.cget("image"), name)
                self.assertEqual(title.cget("text"), name)
                self.assertEqual(mark.cget("bg") == ACCENT, name == "Charvel", name)      # the one in use is marked
                self.assertTrue(inside(row, found), name)
            self.click(chooser.name)                    # the tile again: put away
            self.assertIsNone(chooser.list)
        self.resize(DEFAULT_SIZE)

    def test_the_row_under_the_pointer_is_lit(self):
        chooser = self.desk.chooser
        chooser.open()
        self.root.update()
        mark, picture, title = chooser.rows["Bass"].winfo_children()
        before = (title.cget("bg"), str(picture.cget("image")))
        title.event_generate("<Enter>")
        self.root.update()
        self.assertNotEqual((title.cget("bg"), str(picture.cget("image"))), before)
        title.event_generate("<Leave>")
        self.root.update()
        self.assertEqual((title.cget("bg"), str(picture.cget("image"))), before)

    def test_choosing_one_sets_the_instrument_for_the_whole_app_at_once_and_closes_the_list(self):
        desk, chooser, studio = self.desk, self.desk.chooser, self.studio
        desk.toggle("Guitar")
        self.assertIn("What the Charvel puts out", self.texts(desk.tray))
        desk.record_setting = "Bridge · Series"         # a pickup setting of the Charvel
        self.click(chooser)
        self.click(chooser.rows["Les Paul"].winfo_children()[2])
        self.pump()
        self.assertIsNone(chooser.list)
        self.assertEqual(studio.state.instrument, "Les Paul")
        self.assertEqual(chooser.name.cget("text"), "Les Paul")
        self.assertEqual(studio.state.status, "Now on the Les Paul.")
        said = self.texts(desk.tray)                    # the open panel, drawn for it at once
        self.assertIn("What the Les Paul puts out", said)
        self.assertIn("Record a phrase on the Les Paul", said)
        self.assertTrue({"Bridge", "Both", "Neck"} <= set(said))                # its own pickup settings
        self.assertFalse(any("Outer coils" in text for text in said))
        self.assertIsNone(desk.record_setting)
        self.assertEqual(studio._instrument()["name"], "Les Paul")              # what the designer is told
        self.assertEqual(json.loads((self.home / "session.json").read_text(encoding="utf-8"))["instrument"],
                         "Les Paul")                                            # and it is kept for the next start
        desk.toggle("More")                             # the instrument is no longer chosen under More
        self.assertFalse({"Charvel", "Les Paul", "Bass", "Instrument"} & set(self.texts(desk.tray)))
        seat = desk.learner_seat()                      # the learner's seat reads the same
        self.addCleanup(seat.worker.shutdown, wait=True)
        from lab.learner import folders

        self.addCleanup(folders.log_to, None)
        self.addCleanup(folders.use, None)
        self.assertEqual(seat.guitar, "Les Paul")
        self.click(chooser)                             # the one in use, chosen again: nothing changes
        self.click(chooser.rows["Les Paul"].winfo_children()[1])
        self.assertEqual((studio.state.instrument, chooser.list), ("Les Paul", None))

    def test_a_click_anywhere_else_closes_the_list(self):
        desk, chooser = self.desk, self.desk.chooser
        for elsewhere in (desk.tiles["a"][0], desk.request, desk.names.winfo_children()[0], desk.learn_symbol):
            self.click(chooser.arrow)
            self.assertIsNotNone(chooser.list)
            elsewhere.event_generate("<ButtonPress-1>")
            self.root.update()
            self.assertIsNone(chooser.list, elsewhere)
        self.assertEqual(self.studio.state.instrument, "Charvel")
        self.click(chooser.arrow)                       # a press inside the list does not close it before the choice
        row = chooser.rows["Bass"]
        row.event_generate("<ButtonPress-1>")
        self.root.update()
        self.assertIsNotNone(chooser.list)
        chooser.close()

    def test_the_tile_follows_an_instrument_set_any_other_way_and_shows_one_the_list_does_not_hold(self):
        chooser = self.desk.chooser
        self.studio.set(instrument="Acoustic")          # as the last session's is put back
        self.assertEqual(chooser.name.cget("text"), "Acoustic")
        self.studio.set(instrument="A guitar no longer listed")
        self.assertEqual(chooser.name.cget("text"), "A guitar no longer listed")
        chooser.open()
        self.assertEqual(len(chooser.rows), 5)          # none of them marked
        self.assertFalse(any(row.winfo_children()[0].cget("bg") == ACCENT for row in chooser.rows.values()))
        chooser.close()

    def test_an_instrument_the_player_adds_is_listed_with_its_kinds_picture(self):
        listed = [*self.studio.store.instruments(), {"name": "Flamenca", "kind": "classical", "input": 1,
                                                     "settings": [], "wiring": []}]
        (self.home / "instruments.json").write_text(json.dumps(listed), encoding="utf-8")
        chooser = self.desk.chooser
        chooser.open()
        self.root.update()
        self.assertEqual(list(chooser.rows)[-1], "Flamenca")
        self.assertEqual(pictures.kind_of(chooser.record("Flamenca")), "classical")
        self.click(chooser.rows["Flamenca"].winfo_children()[2])
        self.assertEqual((self.studio.state.instrument, chooser.name.cget("text")), ("Flamenca", "Flamenca"))


class FollowingTheInstrumentTest(DeskCase):
    def test_gesture_listening_for_the_instrument_before_is_stopped(self):
        from lab.learner import gestures

        stopped = []
        with (mock.patch.object(gestures, "running", lambda: not stopped),
              mock.patch.object(gestures, "stop", lambda: stopped.append(True))):
            self.desk.choose_instrument("Les Paul")
        self.assertEqual(stopped, [True])
        self.assertEqual(self.studio.state.instrument, "Les Paul")

    def test_the_phrase_follows_the_instrument_when_one_was_recorded_on_it(self):
        desk, studio = self.desk, self.studio
        charvel = take(self.home, "one", "Charvel", "Neck · Series", "2026-11-20T10:00:00+00:00")
        take(self.home, "two", "Les Paul", "Bridge", "2026-11-20T11:00:00+00:00")
        take(self.home, "three", "Les Paul", "Neck", "2026-11-21T09:00:00+00:00")
        desk.choose_recorded(charvel)
        self.pump()
        self.assertEqual(studio.guitar.setting(), "Neck · Series")
        desk.choose_instrument("Les Paul")
        self.pump()
        self.assertEqual(studio.phrase[0], "three")     # the last one recorded on it
        self.assertEqual(studio.state.status, "Now on the Les Paul. The phrase is the last one recorded on it (Neck).")
        self.assertEqual(studio.guitar.setting(), "Neck")
        self.assertEqual(desk.phrase_link.cget("text"), "Phrase · Neck")       # the instrument is at the top left
        desk.choose_instrument("Bass")                  # nothing recorded on it: the phrase stays, and that is said
        self.pump()
        self.assertEqual(studio.phrase[0], "three")
        self.assertEqual(desk.phrase_link.cget("text"), "Phrase · Les Paul · Neck")     # another's: it names whose
        self.assertEqual(studio.state.status, "Now on the Bass. No phrase is recorded on it yet: the phrase is still the Les Paul's.")
        # A phrase of another instrument says nothing of this one's pickup settings: the designer is not told one.
        self.assertIsNone(studio.guitar.setting())
        desk.choose_instrument("Les Paul")              # back on the instrument of the phrase in use: it stays
        self.pump()
        self.assertEqual((studio.phrase[0], studio.state.status), ("three", "Now on the Les Paul."))


class NarrowDeskTest(DeskCase):
    """The smallest window with a panel open: seen in a picture, not in a measurement, that the row under the
    tiles pushed Save to QC out and that a long name was cut at the tile's edge."""

    def test_saving_stays_on_show_and_a_long_name_is_wrapped_to_its_tile(self):
        from lab.sound.sounds import Sound
        from support import DELAY, PLEXI, V30, chain

        desk = self.desk
        self.put("a", Sound("Clean chime", chain(gain_db=0.), {"kind": "designed"}))
        self.put("b", Sound("Plexi lead with a slap echo behind it", chain(PLEXI, V30, DELAY), {"kind": "designed"}))
        self.put("b", Sound("Plexi lead with a slap echo behind it, again", chain(PLEXI, V30), {"kind": "designed"}))
        long = take(self.home, "long", "Les Paul", "Both humbuckers · Parallel", "2026-11-20T10:00:00+00:00")
        desk.choose_recorded(long)                      # of another instrument: its name is the longest it can be
        desk.toggle("Sounds")
        self.resize(SMALLEST_SIZE)
        self.pump()
        self.root.update()
        row = desk.guitar.master
        save, = [child for child in row.winfo_children() if child.winfo_class() == "Button"
                 and child.cget("text") == "Save to QC"]
        for stays in (desk.guitar, desk.loop, desk.copy_link, desk.blind_link, desk.undo_link, save):
            self.assertTrue(stays.winfo_ismapped() and inside(stays, row), stays.cget("text"))
            self.assertGreaterEqual(stays.winfo_width(), stays.winfo_reqwidth(), stays.cget("text"))
        for side in ("a", "b"):
            tile, name = desk.tiles[side][:2]
            self.assertTrue(inside(name, tile), side)
            self.assertLessEqual(name.winfo_reqwidth(), tile.winfo_width(), side)       # wrapped, not cut
        self.resize(DEFAULT_SIZE)


if __name__ == "__main__":
    unittest.main()
