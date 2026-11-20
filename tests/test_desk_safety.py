"""What must hold when the player, the unit or the app does something at an awkward moment.

Each test here answers a finding of the review of 2026-11-19 (docs/archive/CLAUDE_WORK_REVIEW.md).
"""

from contextlib import contextmanager
from pathlib import Path
from queue import Queue
import sys
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_draft_forms import DELAY, EQ, RECTIFIER, FakeUnit, dual  # noqa: E402
from tone_lab import draft_render, iterate, listening, listening_room, lore, models, monitor  # noqa: E402
from tone_lab.tone_identity import Tone  # noqa: E402

ELSEWHERE = {"folder_key": "/media/p4/Presets/My Presets", "position": 3, "is_factory": False}
WRITES = ("add_block", "set_param", "delete_block", "set_split_points", "set_lane_param", "assign_param_to_scenes",
          "set_scene", "set_scene_label", "set_block_bypass", "set_routing")


class Wanderer(FakeUnit):
    """A unit whose player selects another preset after a given number of writes."""

    def __init__(self, leave_after=None):
        super().__init__()
        self.leave_after, self.log, self.stray = leave_after, [], []

    def get_setlist_position(self):
        gone = self.leave_after is not None and len(self.log) >= self.leave_after
        return ELSEWHERE if gone else draft_render.WORK_SLOT


def _recorded(kind):
    def write(self, *args, **kwargs):
        if self.leave_after is not None and len(self.log) >= self.leave_after:
            self.stray.append(kind)  # This landed on the preset the player switched to.
        self.log.append(kind)
        return getattr(FakeUnit, kind)(self, *args, **kwargs)
    return write


for _kind in WRITES:
    setattr(Wanderer, _kind, _recorded(_kind))


def build(unit):
    chain = draft_render.compile_chain(dual(), -12.)
    with patch("time.sleep"), patch.object(draft_render.measured, "polarity", return_value=()):
        return chain, draft_render.build_grid(unit, unit.get_current_preset(), chain, dual())


class SwitchedPresetTest(unittest.TestCase):
    def test_no_split_mixer_or_scene_write_follows_a_switch_made_just_before_it(self):
        whole = Wanderer()
        build(whole)
        # One check guards each short run of like writes, such as the mixer's four values.
        starts = [index for index, kind in enumerate(whole.log)
                  if kind in ("set_split_points", "set_scene")
                  or kind in ("set_lane_param", "assign_param_to_scenes") and whole.log[index - 1] != kind]
        self.assertGreaterEqual(len(starts), 6)  # The split, the mixer, the scene assignments and each scene.
        for index in starts:
            unit = Wanderer(leave_after=index)
            with self.assertRaisesRegex(RuntimeError, "selected preset changed"):
                build(unit)
            self.assertEqual(unit.stray, [], f"after write {index}, before {whole.log[index]}")

    def test_a_switch_at_any_moment_stops_the_build_within_one_block(self):
        whole = Wanderer()
        build(whole)
        for index in range(1, len(whole.log)):
            unit = Wanderer(leave_after=index)
            with self.assertRaises(RuntimeError):
                build(unit)
            # What is left of the block or scene being written may still land. Nothing after it does.
            self.assertLessEqual(len(unit.stray), 16, index)
            self.assertLess(len(unit.log), len(whole.log))


class ReadingTest(unittest.TestCase):
    def test_no_of_a_fault_asks_for_less_and_no_of_a_quality_says_it_is_missing(self):
        for words, said, move in (("no fizz", "Fizzy", {"soft_top": .4}), ("no mud", "Muddy", {"tightness": .3}),
                                  ("no boom", "Boomy", {"body": -.3}), ("no bite", "More bite", {"punch": .3}),
                                  ("no sustain", "More sustain", {"sustain": .3})):
            reading = lore.read(words)
            self.assertEqual((reading.said, dict(reading.remedies[0].moves)), (said, move), words)
        self.assertEqual(lore.read("no fizz").remedies, lore.read("less fizz").remedies)


class FakeLevels:
    """Stands in for the output trim and mute, and notes when they are written."""

    order = []

    def __init__(self, value, *, firmware=None):
        self.enabled, self.return_unavailable, self.side = True, False, None

    def configure(self, value):
        pass

    def bind(self, base, scene, routes):
        self.bound = base.name

    def select(self, tone, values, take, *, source="phrase"):
        self.side = tone.get("tone_id")

    def apply(self, qc, check_active, events):
        check_active()
        FakeLevels.order.append(("level", self.side, self.bound))

    def clear_boost(self, qc, check_active):
        pass

    def observe(self, cursor, meters):
        return False


class ConnectOrderTest(unittest.TestCase):
    def test_a_stored_draft_stays_on_usb_until_the_caller_connects_it(self):
        state = {"position": {"position": 1}}
        blank = SimpleNamespace(name="", chains=[])
        built = SimpleNamespace(name=draft_render.WORK_NAME, chains=[], signature="draft")
        qc = SimpleNamespace(firmware="4.1.1", write_preset_file=Mock())

        def recall(qc, position, name):
            state["position"] = position
            return built if name else blank

        config = {"tone_id": "a", "draft": {"id": "d1", "name": "Draft"}, "physical_input": 1}
        events = []
        with (patch.object(monitor.device, "require_clean"), patch("time.sleep"),
              patch.object(monitor.device, "live_position", side_effect=lambda _: state["position"]),
              patch.object(monitor.device, "active_scene", return_value=0),
              patch.object(monitor.device, "read_preset", return_value=built),
              patch.object(iterate, "recall", recall),
              patch.object(iterate, "routes", return_value=iterate.USB_ROUTES),
              patch.object(iterate, "sound_state", side_effect=lambda preset: getattr(preset, "signature", None)),
              patch.object(iterate, "set_routes") as set_routes,
              patch.object(monitor, "set_input_routes") as connect,
              patch.object(draft_render, "name_preview_slot", return_value=blank),
              patch.object(draft_render, "compile_chain", return_value="chain"),
              patch.object(draft_render, "build_grid") as build_grid,
              patch.object(draft_render, "label_preview"),
              monitor.resident_grids(qc, events.append) as choose):
            base, scene, position, physical, inlet = choose(config, "guitar")
            # Recalled from its slot the second time, and still not connected by the selection itself.
            choose(config, "guitar")
        build_grid.assert_called_once()
        connect.assert_not_called()
        set_routes.assert_not_called()
        self.assertEqual((position, physical, inlet), (iterate.SCRATCH, draft_render.guitar_routes(1), 1))
        qc.write_preset_file.assert_called_once()
        # A crash while the unit sits on the Preview slot is recovered like one on a side's own slot.
        self.assertIn(draft_render.WORK_SLOT, events[0]["fixtures"])

    def test_trim_and_mute_are_written_before_a_newly_chosen_tone_is_connected(self):
        stop = Event()
        first = {"tone_id": "a", "preset": {"position": {"position": 10}, "name": "Clean"}, "name": "Clean",
                 "mode": "guitar", "level": {"muted": True}}
        second = {"tone_id": "b", "preset": {"position": {"position": 11}, "name": "Lead"}, "name": "Lead"}
        messages = iter([{"tone": {"config": second}}])
        physical = [(1, 19), (0, 0), (0, 0), (0, 0)]
        FakeLevels.order = order = []

        @contextmanager
        def connect(**_):
            yield object()

        @contextmanager
        def grid(qc, config, report):
            yield SimpleNamespace(name=config["preset"]["name"]), 0, iterate.SCRATCH, physical, 1

        @contextmanager
        def unavailable(qc, report):
            def choose(config, mode):
                raise iterate.FixtureUnavailable("The optional slot is in use.")
            yield choose

        def switch(qc, position, routes, inlet, message, stack, report):
            message["prepare_guitar"]()
            order.append(("route", "start"))
            return None, {}

        def drain(queue):
            message = next(messages, None)
            if message is None:
                stop.set()
            return message or {}

        with (patch.object(monitor.device, "connect", connect),
              patch.object(monitor.device, "active_scene", return_value=0),
              patch.object(monitor.device, "live_position", return_value=iterate.SCRATCH),
              patch.object(monitor, "grid", grid), patch.object(monitor, "resident_grids", unavailable),
              patch.object(monitor, "switch_source", switch), patch.object(monitor, "drain", drain),
              patch.object(monitor, "set_input_routes", lambda qc, position, routes: order.append(("route", routes[0]))),
              patch.object(monitor.live_levels, "Levels", FakeLevels),
              patch.object(monitor.styles, "available_knobs", return_value=[]),
              patch.object(monitor.live, "update", return_value={})):
            monitor.run(first, stop, Queue(), lambda event: None)
        # At the start, and again for the second tone: its own level first, then the guitar.
        self.assertLess(order.index(("level", "a", "Clean")), order.index(("route", "start")))
        connected = order.index(("route", (1, 19)))
        self.assertIn(("level", "b", "Lead"), order[:connected])
        self.assertNotIn(("level", "b", "Clean"), order)


class SceneTest(unittest.TestCase):
    def test_a_shape_on_one_scene_is_confirmed_there_and_the_other_scenes_are_confirmed_untouched(self):
        unit = FakeUnit()
        chain, _ = build(unit)
        routes = iterate.USB_ROUTES
        gain = (0, 1, 0)  # The Rectifier's Gain differs by scene. The equalizer's first control does not.
        band = (0, 3, 0)
        unit.scene = 1
        unit.set_param(*gain, models.to_norm(RECTIFIER, 0, 7.5))
        unit.set_param(*band, models.to_norm(EQ, 0, 2.))
        edits = {gain: {"to": 7.5}, band: {"to": 2.}}
        draft_render.assert_scenes(unit, chain, routes, dual(), edits=edits, scene=1)
        # Taken for a shape on the first scene, the same grid is refused: scene A still has its own gain.
        with self.assertRaisesRegex(RuntimeError, "did not confirm a setting"):
            draft_render.assert_scenes(unit, chain, routes, dual(), edits=edits, scene=0)
        # A scene that lost its own value is caught even when the shaped scene is right.
        unit.scene = 0
        unit.set_param(*gain, models.to_norm(RECTIFIER, 0, 9.))
        with self.assertRaisesRegex(RuntimeError, "did not confirm a setting"):
            draft_render.assert_scenes(unit, chain, routes, dual(), edits=edits, scene=1)

    def test_saving_refuses_a_scene_the_draft_does_not_have(self):
        for draft, scene in ((dual(), 2), ({"id": "plain", "name": "Plain"}, 1), (dual(), "B")):
            with self.assertRaisesRegex(ValueError, "no such scene"):
                draft_render.save(draft, scene=scene)

    def test_the_desk_names_the_chosen_scene_when_it_saves_and_when_it_plays(self):
        draft = {**dual(), "id": "d1"}
        lead = Tone("draft", "Two voices", draft_id="d1", scene=1)
        self.assertEqual(listening_room.Desk.draft_scene(lead, draft), 1)
        self.assertEqual(listening_room.Desk.draft_scene(Tone("draft", "Two voices", draft_id="d1"), draft), 0)
        self.assertEqual(listening_room.Desk.draft_scene(Tone("draft", "x", draft_id="d1", scene=5), draft), 0)
        self.assertIsNone(listening_room.Desk.draft_scene(lead, {"id": "d1", "chain": []}))
        submitted = {}
        desk = SimpleNamespace(job=None, side="b", drafts={"d1": draft}, alternative=None, input_channel=1, values={},
                               shape_tone=lambda: lead, shape_dirty=lambda: False,
                               draft_scene=listening_room.Desk.draft_scene,
                               submit=lambda *args, **options: submitted.update(args=args, options=options))
        listening_room.Desk.save(desk)
        self.assertIs(submitted["args"][2], draft_render.save)
        self.assertEqual(submitted["options"]["scene"], 1)

    def test_an_unsaved_draft_reopens_on_the_scene_it_was_left_on(self):
        with patch.object(listening_room.session, "SESSIONS", Path("no-such-folder")):
            lead = Tone("draft", "Two voices", draft_id="d1", scene=1)
            again = Tone.from_record(lead.to_record())
        self.assertEqual((again.kind, again.draft_id, again.scene), ("draft", "d1", 1))


class ReferenceTest(unittest.TestCase):
    """R is a recording that plays through Windows. It always answers, and says why when it cannot play yet."""

    def desk(self, **changes):
        desk = SimpleNamespace(reference=SimpleNamespace(name="Plini · E"), job=None, stop_signal=None, heard=[],
                               said=[], reference_waiting=False, task_if_active=lambda name: False,
                               stopped=[], check_qc=lambda: None, suggestions=Mock(),
                               root=SimpleNamespace(after_idle=lambda action: action()))
        desk.listen = desk.heard.append
        desk.set_status = lambda text, **options: desk.said.append((text, options.get("actions", ())))
        def stop():  # As the real Stop does: everything that was waiting for the unit is dropped.
            desk.stopped.append(True)
            desk.reference_waiting = False

        desk.stop = stop
        desk.hear_reference = lambda: listening_room.Desk.hear_reference(desk)
        desk.stop_for_reference = lambda: listening_room.Desk.stop_for_reference(desk)
        vars(desk).update(changes)
        return desk

    def test_it_plays_when_the_unit_is_free(self):
        desk = self.desk()
        desk.hear_reference()
        self.assertEqual((desk.heard, desk.said), (["reference"], []))

    def test_while_the_guitar_is_live_it_says_what_it_is_and_offers_to_stop_and_play(self):
        live = Event()
        desk = self.desk(job=object(), stop_signal=live, task_if_active=lambda name: name == "monitor")
        desk.hear_reference()
        self.assertEqual(desk.heard, [])
        text, actions = desk.said[0]
        self.assertIn("recording, Plini · E", text)
        self.assertIn("plays through Windows", text)
        self.assertEqual(actions[0][0], "Stop and play R")
        actions[0][1]()
        self.assertTrue(desk.stopped and desk.reference_waiting)
        # The unit has been released and its preset restored: now the recording plays.
        desk.job, desk.task_if_active = None, lambda name: False
        listening_room.Desk.restored(desk, None)
        self.assertEqual(desk.heard, ["reference"])
        self.assertFalse(desk.reference_waiting)

    def test_with_no_reference_it_says_where_to_choose_one(self):
        desk = self.desk(reference=None)
        desk.hear_reference()
        self.assertIn("no reference yet", desk.said[0][0])


class NamesTest(unittest.TestCase):
    def test_no_desk_method_is_defined_twice(self):
        # A second definition silently replaces the first. That once broke requests to the assistant.
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(listening_room))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                names = [item.name for item in node.body if isinstance(item, ast.FunctionDef)]
                repeated = sorted({name for name in names if names.count(name) > 1})
                self.assertEqual(repeated, [], node.name)

    def test_the_sounds_gathered_for_the_assistant_are_the_captured_ones(self):
        desk = SimpleNamespace(baseline="a.wav", alternative="b.wav", reference="r.wav",
                               rendered_values=lambda sound: {"drive": .2})
        self.assertEqual(listening_room.Desk.heard(desk, "b"), ("b.wav", "a.wav", "r.wav", {"drive": .2}))


class KeepTest(unittest.TestCase):
    def desk(self, **changes):
        old, new = Tone("preset", "Old", preset=None), Tone("preset", "New", preset=None)
        desk = SimpleNamespace(side="a", base_bypassed=False, shapes={"a": {"drive": .4}, "b": {}},
                               tone_revisions={"a": 8, "b": 1}, confirmed_tones={"a": (old, {"drive": .2}, 7)},
                               tone=lambda side: new, take=None, active_source="guitar", reference=None, blind=None,
                               kept_live=None, kept_note="", qc_name="", on_air=lambda: "a", statuses=[],
                               update=lambda: None)
        desk.tone_snapshot = lambda side: (desk.tone(side), dict(desk.shapes[side]), desk.tone_revisions[side])
        desk.sounding = lambda side: listening_room.Desk.sounding(desk, side)
        desk.set_status = lambda text, **_: desk.statuses.append(text)
        vars(desk).update(changes)
        return desk

    def test_keep_records_the_tone_still_sounding_while_the_next_one_loads(self):
        desk = self.desk()
        with patch.object(listening, "keep_tone") as keep_tone:
            listening_room.Desk.keep(desk)
        record, shape = keep_tone.call_args.args[:2]
        self.assertEqual((record["name"], shape), ("Old", {"drive": .2}))
        self.assertEqual(desk.kept_live, ("a", 7, (("drive", .2),)))
        self.assertTrue(listening_room.Desk.is_kept(desk))
        # Once the unit confirms the new tone, that one is what Keep means, and it has not been kept.
        desk.confirmed_tones["a"] = desk.tone_snapshot("a")
        self.assertFalse(listening_room.Desk.is_kept(desk))

    def test_keep_records_the_base_tone_while_the_shape_is_switched_out(self):
        desk = self.desk(base_bypassed=True)
        desk.confirmed_tones["a"] = desk.tone_snapshot("a")
        with patch.object(listening, "keep_tone") as keep_tone:
            listening_room.Desk.keep(desk)
        self.assertEqual(keep_tone.call_args.args[1], {})
        self.assertTrue(listening_room.Desk.is_kept(desk))
        desk.base_bypassed = False  # The shape is back in, and that sound was not the one kept.
        self.assertFalse(listening_room.Desk.is_kept(desk))



class ClockTest(unittest.TestCase):
    def test_a_failure_in_one_turn_does_not_stop_the_desk(self):
        scheduled = []
        desk = SimpleNamespace(root=SimpleNamespace(after=lambda ms, action: scheduled.append(ms)), tick=None)
        desk.turn = Mock(side_effect=RuntimeError("an event the desk did not expect"))
        with self.assertLogs("tone_lab", level="ERROR"):
            listening_room.Desk.tick(desk)
        self.assertEqual(scheduled, [65])
        desk.turn = Mock(return_value=False)  # The window is closing: no further turn.
        listening_room.Desk.tick(desk)
        self.assertEqual(scheduled, [65])

if __name__ == "__main__":
    unittest.main()
