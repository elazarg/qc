"""The Shape panel's Brightness x Texture pad: where knob positions sit on it, and what it draws."""


import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
from types import SimpleNamespace
import tkinter as tk
import unittest

from lab.desk import window as desk_module
from lab.desk.window import Desk, pad_point, pad_values
from lab.desk.theme import DARK
from lab.sound.assistant import Assistant

import support
from test_lab_design import Scripted


class PadGeometryTest(unittest.TestCase):
    def test_a_point_and_its_positions_agree_and_stay_on_the_pad(self):
        self.assertEqual(pad_point(0., 0., 200), (100., 100.))
        self.assertEqual(pad_point(1., 1., 200), (200., 0.))         # bright and rough: top right
        self.assertEqual(pad_point(-1., -1., 200), (0., 200.))       # dark and round: bottom left
        self.assertEqual(pad_values(150., 50., 200), (.5, .5))
        self.assertEqual(pad_values(-40., 260., 200), (-1., -1.))    # dragged off the pad: held at its edge
        self.assertEqual(pad_values(*pad_point(.35, -.6, 200), 200), (.35, -.6))
        self.assertEqual(pad_values(103., 97., 200), (.05, .05))     # on steps of .05


class PadPanelTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"No display: {error}")
        self.root.withdraw()
        self.addCleanup(support.free_now)
        self.addCleanup(lambda: setattr(self, "root", None))
        self.addCleanup(self.root.destroy)

    def panel(self, available, knobs):
        turned = []
        sound = SimpleNamespace(knobs=knobs)
        studio = SimpleNamespace(state=SimpleNamespace(side="b"), sound=lambda side: sound,
                                 available=lambda: available, turn=turned.append,
                                 undo=lambda: None, base=lambda: None, add_parallel_space=lambda: None,
                                 hearing_base=set())
        desk = Desk.__new__(Desk)
        desk.studio, desk.tray = studio, tk.Frame(self.root, bg=DARK)
        desk.panel_shape()
        return desk.tray

    def test_the_pad_comes_above_the_other_knobs_and_takes_the_place_of_their_sliders(self):
        tray = self.panel(["drive", "brightness", "texture", "body"], {"brightness": .5, "texture": -.25})
        children = tray.winfo_children()
        # Hearing it without the knobs is placed first, at the bottom, so it stays on show (Undo is the desk's, by
        # the tiles); the pad is the first thing from the top, and the sliders scroll in the room between.
        self.assertEqual([part["text"] for part in children[0].winfo_children()], ["Hear it without the knobs"])
        self.assertEqual(children[0].pack_info()["side"], "bottom")
        pad = children[1]
        self.assertIsInstance(pad, tk.Canvas)
        self.assertEqual(int(pad["width"]), desk_module.PAD_SIZE)
        texts = [pad.itemcget(item, "text") for item in pad.find_all() if pad.type(item) == "text"]
        self.assertEqual(set(texts), {"dark", "bright", "round", "rough"})
        dot = [item for item in pad.find_all() if pad.type(item) == "oval"][0]
        x0, y0, x1, y1 = pad.coords(dot)
        self.assertEqual(((x0 + x1) / 2, (y0 + y1) / 2), pad_point(.5, -.25))
        self.assertEqual(children[2]["text"], "Brightness +0.50 · Texture -0.25")
        self.assertEqual([row.winfo_children()[0]["text"] for row in self.slider_rows(tray)], ["Distortion", "Body"])

    def slider_rows(self, widget):
        found = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Frame) and any(isinstance(part, tk.Scale) for part in child.winfo_children()):
                found.append(child)
            found += self.slider_rows(child)
        return found

    def test_no_pad_when_the_sound_has_nothing_for_either_axis(self):
        tray = self.panel(["drive"], {})
        self.assertFalse(any(isinstance(child, tk.Canvas) and child["cursor"] == "crosshair"
                             for child in tray.winfo_children()))
        self.assertEqual(len(self.slider_rows(tray)), 1)



class SessionDeskTest(unittest.TestCase):
    """The real desk on the simulated unit, its windows kept off the screen: what was open comes back."""

    def test_the_desk_reopens_the_panel_and_the_phrase_loop_it_was_left_with(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory

        import numpy as np

        from lab.desk.studio import Studio
        from lab.hearing.audio import write_wave
        from lab.qc.host import Host

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        home = Path(folder.name)
        take = home / "phrases" / "take1"
        take.mkdir(parents=True)
        write_wave(take / "dry.wav", (np.sin(np.arange(48000 * 4) / 48000 * 2 * np.pi * 196) * .2)[:, None])
        (take / "phrase.json").write_text(json.dumps({"id": "take1", "instrument": "Charvel", "setting": "Neck",
                                                      "recorded": "2026-11-20T10:00:00+00:00"}), encoding="utf-8")
        (home / "session.json").write_text(json.dumps({"side": "a", "phrase": {"id": "take1", "start": 4800,
                                                                                 "end": 52800},
                                                       "desk": {"panel": "Chain"}}), encoding="utf-8")
        self.addCleanup(support.free_now)             # the window is freed on this thread once the test has let go of it
        root = tk.Tk()
        host = Host(home / "unit", simulated=True, local=True)
        host.ready.result(10)
        studio = Studio(host, home, assistant=Assistant(home, automatic=False, run=Scripted()))
        desk = Desk(root, studio)
        try:
            self.assertEqual(desk.panel, "Chain")
            self.assertEqual(studio.state.side, "a")
            self.assertEqual(len(studio.phrase[1]), 48000)
            self.assertEqual(studio.phrase_whole[1:], (4800, 52800))
        finally:
            studio.close()
            root.destroy()


class QuietDeskTest(unittest.TestCase):
    """No shortcuts, no dialogs: typing goes into the boxes, and the side windows open from the panels."""

    def setUp(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from lab.desk.studio import Studio
        from lab.qc.host import Host

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        home = Path(folder.name)
        self.root = tk.Tk()
        host = Host(home / "unit", simulated=True, local=True)
        host.ready.result(10)
        self.studio = Studio(host, home, assistant=Assistant(home, automatic=False, run=Scripted()))
        self.desk = Desk(self.root, self.studio)
        # The window and its Tk variables are freed here, on this thread, not later on another one: the test lets
        # go of the desk first, so that the collection that follows finds it.
        self.addCleanup(support.free_now)
        self.addCleanup(lambda: [setattr(self, name, None) for name in ("desk", "root", "studio")])
        self.addCleanup(self.root.destroy)
        self.addCleanup(self.studio.close)

    def test_letters_typed_anywhere_do_nothing_but_type(self):
        self.assertEqual(self.root.bind("<Key>"), "")
        before = self.studio.state
        for letter in "tkdxgpn":
            self.root.event_generate("<KeyPress>", keysym=letter)
        self.root.update()
        self.assertEqual(self.studio.state.mode, before.mode)
        self.assertEqual(self.studio.state.blind, before.blind)

    def test_the_learning_symbol_puts_the_guitar_learner_on(self):
        from lab.learner import folders

        self.addCleanup(folders.log_to, None)       # the seat points the learner's folders and log at this desk
        self.addCleanup(folders.use, None)
        self.root.update()
        self.desk.learn_symbol.event_generate("<ButtonRelease-1>")
        self.root.update()
        screen = self.desk.learning
        self.addCleanup(self.desk.seat.worker.shutdown, wait=True)
        self.addCleanup(screen.learner.worker.shutdown, wait=True)
        self.assertTrue(self.desk.learning_on)
        self.assertEqual((screen.view, screen.learner.guitar), ("Guitar Learner", "Charvel"))

    def test_saving_asks_for_the_name_in_place(self):
        self.two_sounds()
        self.root.update()
        self.assertFalse(self.desk.save_row.winfo_ismapped())
        self.desk.save()
        self.root.update()
        # The name is asked for in a row of the desk's own window, filled in with the sound's own. No dialog opens.
        self.assertTrue(self.desk.save_row.winfo_ismapped())
        self.assertIs(self.desk.save_name.winfo_toplevel(), self.root)
        self.assertEqual([child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel)], [])
        self.assertEqual(self.desk.save_name.get(), "Hiwatt")
        # Typed over and confirmed, the sound on show is saved to the unit under that name, and the row goes.
        self.desk.save_name.delete(0, "end")
        self.desk.save_name.insert(0, "Stage Hiwatt")
        self.desk.saved()
        support.settle(self.studio, 30, self.root)
        self.assertFalse(self.desk.save_row.winfo_ismapped())
        self.assertEqual(self.studio.sound("b").saved["name"], "Stage Hiwatt")
        self.assertIsNone(self.studio.sound("a").saved)

    def two_sounds(self):
        from lab.sound.chain import Block, Chain, Row
        from lab.sound.sounds import Sound

        for side, name, model in (("a", "Plexi", 1081), ("b", "Hiwatt", 1063)):
            sound = self.studio.store.save(Sound(name, Chain((Row((Block(model),), inlet=1, outlet=19),)),
                                                 {"kind": "designed"}))
            self.studio.set(**{side: sound.id})

    def test_the_save_name_stays_hidden_while_comparing_blind(self):
        self.two_sounds()
        self.studio.set(blind=True)
        self.desk.save()
        self.assertEqual(self.desk.save_name.get(), "")
        self.studio.set(blind=False)
        self.desk.save()
        self.assertEqual(self.desk.save_name.get(), "Hiwatt")

    def test_a_new_take_shows_on_the_phrase_link(self):
        import json

        import numpy as np

        from lab.hearing.audio import write_wave

        take = self.studio.folder / "phrases" / "new"
        take.mkdir(parents=True)
        write_wave(take / "dry.wav", (np.sin(np.arange(48000) / 30) * .2)[:, None])
        (take / "phrase.json").write_text(json.dumps({"id": "new", "instrument": "Les Paul", "setting": "Bridge",
                                                      "recorded": "2026-11-20T12:00:00+00:00"}), encoding="utf-8")
        self.studio.phrase = ("new", np.zeros((48000, 1)))
        self.studio.set(status="Recorded.")
        self.assertIn("Bridge", self.desk.phrase_link["text"])


if __name__ == "__main__":
    unittest.main()
