"""The desk's window on the simulated unit, kept off the screen: what it does with a session it cannot put back,
with a fault while drawing or in a click, with a second launch, and what it draws again and when.

Each test here failed on the desk as the whole-codebase review found it (docs/WHOLE_CODEBASE_REVIEW.md)."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import gc
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import threading
import time
import tkinter as tk
import unittest

import numpy as np

from lab.desk import studio as studio_module, window as desk_module
from lab.desk.studio import Studio
from lab.desk.window import SHORTEST_LOOP, Desk, history_rows, loop_edges
from lab.hearing.audio import write_wave
from lab.qc.host import Host
from lab.sound.assistant import Assistant
from lab.sound.chain import Block, Chain, Row
from lab.sound.sounds import Sound
from test_lab_design import GREENBACK, PLEXI, V30, Scripted, answer, block, reply


def three(*names):
    return reply(*[answer(name, [block(PLEXI, 0, gain=gain), block(GREENBACK, 1)], differs=name)
                   for name, gain in zip(names, (3, 6, 9))])


def one_row(*models):
    return Chain((Row(tuple(Block(model) for model in models), inlet=1, outlet=19),))


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def take(home, name, seconds=8, setting="Neck"):
    folder = Path(home) / "phrases" / name
    folder.mkdir(parents=True)
    tone = (np.sin(np.arange(48000 * seconds) / 48000 * 2 * np.pi * 196) * .2)[:, None].astype(np.float32)
    write_wave(folder / "dry.wav", tone)
    item = {"id": name, "instrument": "Charvel", "setting": setting, "recorded": "2026-11-20T10:00:00+00:00"}
    (folder / "phrase.json").write_text(json.dumps(item), encoding="utf-8")
    return item


class DeskCase(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        self.script = Scripted()
        # The window and its Tk variables are freed here, on this thread, not later on another one: the workers are
        # waited for and the test lets go of the desk first, so that the collection that follows finds it.
        self.studio = self.desk = self.root = None
        self.addCleanup(gc.collect)
        self.addCleanup(self.let_go)

    def let_go(self):
        if self.studio is not None:
            self.studio.thinking.shutdown(wait=True)
            self.studio.reading.shutdown(wait=True)
        self.studio = self.desk = self.root = None

    def open(self, *, keep_open=True):
        """A desk on what `self.home` holds. It is closed with the test unless the test closes it itself."""
        host = Host(self.home / "unit", simulated=True, local=True)
        host.ready.result(10)
        self.studio = Studio(host, self.home, assistant=Assistant(self.home, automatic=False, run=self.script))
        self.studio.idle_seconds = 0.
        self.root = tk.Tk()
        self.desk = Desk(self.root, self.studio)
        if keep_open:
            self.addCleanup(self.root.destroy)
            self.addCleanup(self.studio.close)
        return self.desk

    def settle(self, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.studio.poll()
            if not self.studio.jobs and not self.studio.host.busy:
                self.studio.poll()
                if not self.studio.jobs:
                    return
            time.sleep(.02)
        raise AssertionError("work did not finish")

    def a_round(self, *names):
        self.script.answers.append(three(*(names or ("One", "Two", "Three"))))
        self.studio.ask("make a classic rock crunch")
        self.settle()

    def log(self):
        path = self.home / "errors.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def panel_text(self):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, (tk.Label, tk.Button)):
                    found.append(child["text"])
                walk(child)

        walk(self.desk.tray)
        return found


class ComingBackTest(DeskCase):
    """Whatever the last session left behind, the desk opens."""

    def left_behind(self):
        """A session with a round on show, A and B, and the Chain panel open; returns the ids of the designs."""
        self.open(keep_open=False)
        self.a_round()
        self.studio.copy_to_a()
        self.settle()
        self.desk.toggle("Chain")
        candidates = [item["sound"] for item in self.studio.round()["candidates"]]
        self.desk.close()
        gc.collect()
        return candidates

    def test_a_design_of_the_round_that_is_gone_leaves_a_and_b_and_drops_the_round(self):
        candidates = self.left_behind()
        (self.home / "sounds" / f"{candidates[2]}.json").unlink()
        self.open()
        self.assertEqual(self.studio.state.b, candidates[0])
        self.assertIsNotNone(self.studio.state.a)
        self.assertIsNone(self.studio.state.round)
        self.assertEqual(self.desk.panel, "Chain")

    def test_a_session_that_is_not_a_record_is_set_aside_and_the_desk_opens_empty(self):
        self.left_behind()
        (self.home / "session.json").write_text("[]", encoding="utf-8")
        self.open()
        self.assertEqual((self.studio.state.a, self.studio.state.b), (None, None))
        self.assertTrue((self.home / studio_module.SET_ASIDE).exists())
        self.assertIn("could not be put back", self.desk.line["text"])
        self.settle()
        self.assertIn("could not be put back", self.studio.state.status)    # and still said once the unit is open

    def test_a_take_that_cannot_be_read_does_not_keep_the_desk_from_opening(self):
        self.left_behind()
        take(self.home, "broken")
        (self.home / "phrases" / "broken" / "dry.wav").write_bytes(b"not a wave file")
        record = json.loads((self.home / "session.json").read_text(encoding="utf-8"))
        (self.home / "session.json").write_text(json.dumps({**record, "phrase": {"id": "broken"}}), encoding="utf-8")
        self.open()
        self.assertTrue((self.home / studio_module.SET_ASIDE).exists())
        self.assertIn("could not be put back", self.desk.line["text"])
        self.assertIn("Traceback", self.log())


class FaultsTest(DeskCase):
    def test_a_fault_while_drawing_is_said_and_logged_once_and_the_action_goes_on(self):
        self.open()
        self.a_round()
        candidates = [item["sound"] for item in self.studio.round()["candidates"]]
        (self.home / "sounds" / f"{candidates[2]}.json").unlink()
        self.studio.step(1)                               # the chips must be drawn again, and one cannot be
        self.settle()
        self.assertEqual(self.studio.state.b, candidates[1])               # the step itself went through
        self.assertEqual(self.studio.state.shown, "pair")
        self.assertIn("Something went wrong drawing the desk", self.desk.line["text"])
        self.studio.say("again")
        self.assertEqual(self.log().count("--- 20"), 1)

    def test_a_fault_in_a_click_is_written_with_its_trace(self):
        self.open()
        self.settle()

        def a_click_that_fails():
            raise ValueError("no such thing")

        self.root.after(0, a_click_that_fails)
        for _ in range(5):
            self.root.update()
        self.assertIn("Something went wrong: no such thing", self.studio.state.status)
        self.assertIn("in a_click_that_fails", self.log())
        self.assertNotIn("traceback object", self.log())


class SecondLaunchTest(DeskCase):
    def setUp(self):
        super().setUp()
        port, wait = desk_module.PORT, desk_module.WAIT_FOR_CLOSING
        self.addCleanup(setattr, desk_module, "PORT", port)
        self.addCleanup(setattr, desk_module, "WAIT_FOR_CLOSING", wait)
        desk_module.PORT, desk_module.WAIT_FOR_CLOSING = free_port(), .6

    def knock(self):
        """What a second launch finds, while this desk's clock runs."""
        found = []
        knocking = threading.Thread(target=lambda: found.append(desk_module.only_desk()))
        knocking.start()
        deadline = time.monotonic() + 10
        while knocking.is_alive() and time.monotonic() < deadline:
            if self.desk is not None and not self.desk.closing:
                self.desk.come_forward()
            time.sleep(.02)
        knocking.join(5)
        return found[0]

    def test_an_open_desk_answers_and_a_closed_one_leaves_the_door_free(self):
        self.assertTrue(self.knock())                     # nobody there
        self.open(keep_open=False)
        self.desk.answer_second_launch()
        self.assertFalse(self.knock())                    # a desk is open: the second launch leaves
        self.desk.close()
        self.assertIsNone(self.desk.door)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", desk_module.PORT), timeout=.5).close()
        self.assertTrue(self.knock())

    def test_something_else_on_that_port_does_not_keep_the_desk_from_opening(self):
        with socket.socket() as other:
            other.bind(("127.0.0.1", desk_module.PORT))
            other.listen(2)                               # it takes the knock and never answers as a desk does
            self.assertTrue(self.knock())


class PhrasePanelTest(DeskCase):
    def test_an_edge_that_would_leave_less_than_half_a_second_stays_where_it_is(self):
        self.assertEqual(loop_edges((100000, 200000), 150001), (100000, 150001))       # the nearer edge moves
        self.assertEqual(loop_edges((100000, 200000), 110000), (110000, 200000))
        self.assertEqual(loop_edges((100000, 130000), 110000), (100000, 130000))       # too near the other: it stays
        self.assertEqual(loop_edges((100000, 130000), 120000), (100000, 130000))
        self.assertEqual(loop_edges((100000, 148000), 124000), (100000, 100000 + SHORTEST_LOOP))   # exactly half a second

    def test_dragging_the_left_edge_to_the_right_end_keeps_a_loop_of_half_a_second(self):
        take(self.home, "long")
        self.open()
        self.desk.toggle("Phrase")
        self.root.update()
        canvas = [child for child in self.desk.tray.winfo_children() if isinstance(child, tk.Canvas)][-1]
        whole, start, end = self.studio.phrase_whole
        for x in range(int(start / len(whole) * 400), 400, 4):
            canvas.event_generate("<B1-Motion>", x=x, y=60, when="now")
        canvas.event_generate("<ButtonRelease-1>", x=396, y=60, when="now")
        whole, start, end = self.studio.phrase_whole
        self.assertGreaterEqual(end - start, SHORTEST_LOOP)
        self.assertEqual(len(self.studio.phrase[1]), end - start)
        kept = json.loads((self.home / "session.json").read_text(encoding="utf-8"))["phrase"]
        self.assertEqual((kept["start"], kept["end"]), (start, end))
        self.assertFalse(self.desk.dragging)


class HistoryPanelTest(DeskCase):
    def press(self, text):
        buttons = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Button) and child["text"] == text:
                    buttons.append(child)
                walk(child)

        walk(self.desk.tray)
        buttons[0].invoke()

    def test_every_sound_is_listed_and_one_put_on_b_keeps_what_was_measured_since(self):
        self.open()
        for index in range(70):
            self.studio.store.save(Sound(f"Sound {index:02d}", one_row(PLEXI, V30), {"kind": "designed"},
                                         created=f"2026-11-01T10:{index:02d}:00+00:00"))
        self.desk.toggle("History")
        box = [child for child in self.desk.tray.winfo_children() if isinstance(child, tk.Listbox)][0]
        self.assertEqual(box.size(), 70)                                   # all of them, newest first
        self.assertTrue(box.get(0).startswith("Sound 69"))
        chosen = history_rows(self.home)[5][0]
        measured = self.studio.store.sound(chosen)                         # measured after the panel was drawn
        measured.heard = {"phrase": "p", "digest": measured.chain.digest(), "level_db": -20.}
        self.studio.store.save(measured)
        box.selection_set(5)
        self.press("Put on B")
        self.assertEqual(self.studio.state.b, chosen)
        self.assertEqual(self.studio.store.sound(chosen).heard["level_db"], -20.)

    def test_put_with_nothing_chosen_says_so(self):
        self.open()
        self.desk.toggle("History")
        self.press("Put on A")
        self.assertIn("Choose a sound", self.studio.state.status)


class DrawingTest(DeskCase):
    def test_the_rounds_chips_stay_for_a_status_change_and_are_made_again_when_b_changes(self):
        self.open()
        self.a_round()
        chips = self.desk.chips.winfo_children()
        self.studio.say("Something was said.")
        self.studio.begin("x", "Something is going on…")
        self.studio.end("x")
        self.assertEqual(self.desk.chips.winfo_children(), chips)          # a click begun on one still lands
        self.studio.step(1)
        self.assertNotEqual(self.desk.chips.winfo_children(), chips)
        chosen = [chip for chip in self.desk.chips.winfo_children() if "bold" in str(chip["font"])]
        self.assertIn("Two", chosen[0]["text"])

    def test_the_panel_is_left_alone_while_something_in_it_is_dragged(self):
        self.open()
        self.a_round()
        self.desk.toggle("Shape")
        before = self.desk.tray.winfo_children()
        self.desk.dragging = True                         # the pointer is down on the pad
        sound = self.studio.sound("b")
        sound.heard = {"level_db": -33.}                  # a measurement arrives meanwhile
        self.studio.store.save(sound)
        self.studio.say("Measured.")
        self.assertEqual(self.desk.tray.winfo_children(), before)
        self.desk.drag_ends()
        self.assertNotEqual(self.desk.tray.winfo_children(), before)

    def test_undo_is_on_the_desk_once_there_is_something_to_take_back(self):
        self.open()
        self.assertEqual(self.desk.undo_link.winfo_manager(), "")
        self.a_round()
        self.assertEqual(self.desk.undo_link.winfo_manager(), "pack")
        self.studio.undo()
        self.assertEqual(self.desk.undo_link.winfo_manager(), "")

    def test_the_button_for_the_base_says_what_pressing_it_does(self):
        self.open()
        self.a_round()
        self.studio.settle_seconds = 999.
        self.studio.turn({self.studio.available()[0]: .5})
        self.desk.toggle("Shape")
        self.assertIn("Hear it without the knobs", self.panel_text())
        self.studio.base()
        self.assertIn("Hear it with the knobs again", self.panel_text())

    def test_cancel_stays_while_a_request_is_with_the_assistant_whatever_the_working_line_says(self):
        gate, script = threading.Event(), self.script
        self.addCleanup(gate.set)

        def run(prompt, schema, *, web, seconds, cancel):
            if not script.answers:
                gate.wait(20)
            return script(prompt, schema, web=web, seconds=seconds, cancel=cancel) if script.answers else {}

        self.open()
        self.studio.assistant.run = run
        self.a_round()
        self.studio.ask("more sustain from the amp, without more mud")
        self.studio.step(1)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (self.studio.host.busy or len(self.studio.jobs) > 1):
            self.studio.poll()
            time.sleep(.02)
        self.assertEqual(self.desk.cancel_link.winfo_manager(), "pack")
        self.assertIn("Revising", self.desk.working["text"])
        self.studio.cancel()
        self.assertEqual(self.desk.cancel_link.winfo_manager(), "")

    def test_a_sound_that_could_not_be_heard_says_so_on_its_tile(self):
        self.open()
        sound = Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"})
        sound.heard = {"failed": "Nothing came back.", "phrase": self.studio.phrase_key(),
                       "digest": sound.chain.digest(), "scene": 0}
        self.studio.idle_seconds = 999.
        self.studio.put("b", sound)
        self.assertIn("could not be listened to", self.desk.tiles["b"][2]["text"])
        self.desk.toggle("Chain")
        self.assertTrue(any("Nothing came back." in text for text in self.panel_text()))

    def test_the_two_buttons_say_who_has_the_input(self):
        self.open()
        self.a_round()
        self.studio.hold_audio("learner")
        self.assertEqual(str(self.desk.guitar["fg"]), desk_module.DIM)
        self.desk.toggle_play("guitar")
        self.assertEqual(self.desk.line["text"], "The Guitar Learner is using the input.")
        self.studio.release_audio("learner")
        self.assertEqual(str(self.desk.guitar["fg"]), desk_module.INK)

    def test_the_guitar_panel_opens_at_once_and_is_drawn_again_when_the_profile_arrives(self):
        for name, setting in (("one", "Bridge · Series"), ("two", "Neck · Series")):
            take(self.home, name, seconds=1, setting=setting)
        self.open()
        read_on = []
        original = self.studio._profile
        self.studio._profile = lambda *args: (read_on.append(threading.current_thread().name), original(*args))[1]
        self.desk.toggle("Guitar")
        self.assertTrue(any("Working out" in text for text in self.panel_text()))
        self.settle()
        self.assertFalse(any("Working out" in text for text in self.panel_text()))
        self.assertTrue(read_on)
        self.assertNotIn(threading.current_thread().name, read_on)


if __name__ == "__main__":
    unittest.main()
