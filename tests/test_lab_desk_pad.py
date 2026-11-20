"""The Shape panel's Brightness x Texture pad: where knob positions sit on it, and what it draws."""


import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
from types import SimpleNamespace
import tkinter as tk
import unittest

from lab.desk import window as desk_module
from lab.desk.window import Desk, pad_point, pad_values
from lab.desk.theme import DARK


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
        self.addCleanup(self.root.destroy)

    def panel(self, available, knobs):
        turned = []
        sound = SimpleNamespace(knobs=knobs)
        studio = SimpleNamespace(state=SimpleNamespace(side="b"), sound=lambda side: sound,
                                 available=lambda: available, turn=turned.append)
        desk = Desk.__new__(Desk)
        desk.studio, desk.tray = studio, tk.Frame(self.root, bg=DARK)
        desk.panel_shape()
        return desk.tray

    def test_the_pad_comes_above_the_other_knobs_and_takes_the_place_of_their_sliders(self):
        tray = self.panel(["drive", "brightness", "texture", "body"], {"brightness": .5, "texture": -.25})
        children = tray.winfo_children()
        self.assertIsInstance(children[0], tk.Canvas)
        self.assertEqual(int(children[0]["width"]), desk_module.PAD_SIZE)
        texts = [children[0].itemcget(item, "text") for item in children[0].find_all()
                 if children[0].type(item) == "text"]
        self.assertEqual(set(texts), {"dark", "bright", "round", "rough"})
        dot = [item for item in children[0].find_all() if children[0].type(item) == "oval"][0]
        x0, y0, x1, y1 = children[0].coords(dot)
        self.assertEqual(((x0 + x1) / 2, (y0 + y1) / 2), pad_point(.5, -.25))
        self.assertEqual(children[1]["text"], "Brightness +0.50 · Texture -0.25")
        rows = [child for child in children if isinstance(child, tk.Frame)]
        self.assertEqual([row.winfo_children()[0]["text"] for row in rows], ["Distortion", "Body"])

    def test_no_pad_when_the_sound_has_nothing_for_either_axis(self):
        tray = self.panel(["drive"], {})
        self.assertFalse(any(isinstance(child, tk.Canvas) for child in tray.winfo_children()))


if __name__ == "__main__":
    unittest.main()


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
        root = tk.Tk()
        host = Host(home / "unit", simulated=True, local=True)
        host.ready.result(10)
        studio = Studio(host, home)
        desk = Desk(root, studio)
        try:
            self.assertEqual(desk.panel, "Chain")
            self.assertEqual(studio.state.side, "a")
            self.assertEqual(len(studio.phrase[1]), 48000)
            self.assertEqual(studio.phrase_whole[1:], (4800, 52800))
        finally:
            studio.close()
            root.destroy()
