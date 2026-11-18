import unittest

from tone_lab.guitar_gestures import GestureDetector, GestureMapping, replay


def observations(labels, *, first=0.0, hop=0.12):
    return [{"time": first + step * hop, "label": label,
             "confidence": 0 if label is None else .95} for step, label in enumerate(labels)]


class GuitarGesturesTest(unittest.TestCase):
    def setUp(self):
        self.mapping = GestureMapping("neck", "bridge", "space", -.15)
        self.pattern = ["neck"] * 3 + ["bridge"] * 3 + ["neck"] * 3

    def test_opt_in_and_completed_return(self):
        detector = GestureDetector(self.mapping)
        for row in observations(self.pattern):
            self.assertIsNone(detector.observe(row["time"], row["label"], row["confidence"]))
        detector.arm(0)
        events = [detector.observe(row["time"], row["label"], row["confidence"])
                  for row in observations(self.pattern)]
        self.assertEqual(events[:-1], [None] * 8)
        self.assertIsNotNone(events[-1])
        self.assertAlmostEqual(events[-1].at, .96)
        knobs = {"space": .3, "drive": -.2}
        self.assertEqual(events[-1].apply(knobs), {"space": .15, "drive": -.2})
        self.assertEqual(knobs["space"], .3)

    def test_ignores_ordinary_switches_uncertainty_and_long_gaps(self):
        for labels in (
            ["bridge"] * 3 + ["neck"] * 3,
            ["neck"] * 3 + ["bridge"] * 16 + ["neck"] * 3,
            ["neck"] * 3 + ["bridge"] * 2 + [None, "bridge"] + ["neck"] * 3,
            ["neck"] * 3 + ["bridge"] * 3 + [None] * 4 + ["neck"] * 3,
            ["neck"] * 3 + ["bridge"] * 3 + ["middle"] + ["neck"] * 3,
        ):
            with self.subTest(labels=labels):
                self.assertEqual(replay(self.mapping, observations(labels)), [])

    def test_only_a_short_dropout_can_bridge_two_stable_states(self):
        pattern = ["neck"] * 3 + [None] + ["bridge"] * 3 + [None] + ["neck"] * 3
        self.assertEqual(len(replay(self.mapping, observations(pattern))), 1)

    def test_cooldown_and_arm_expiry(self):
        twice = self.pattern + ["bridge"] * 3 + ["neck"] * 3
        self.assertEqual(len(replay(self.mapping, observations(twice))), 1)
        detector = GestureDetector(self.mapping, arm_seconds=.85)
        detector.arm(0)
        for row in observations(self.pattern):
            self.assertIsNone(detector.observe(row["time"], row["label"], row["confidence"]))
        self.assertFalse(detector.is_armed(1))

    def test_out_of_order_observation_cancels_pending_return(self):
        track = observations(self.pattern)
        track.insert(6, {"time": track[4]["time"], "label": "bridge", "confidence": .99})
        self.assertEqual(replay(self.mapping, track), [])

    def test_mapping_serializes_and_clamps_to_control_range(self):
        mapping = GestureMapping.from_dict(GestureMapping("neck", "bridge", "jazz", -.2).to_dict())
        event, = replay(mapping, observations(self.pattern))
        self.assertEqual(event.apply({"jazz": .1}), {"jazz": 0})
        with self.assertRaises(ValueError):
            GestureMapping("neck", "bridge", "not-a-control")
        with self.assertRaises(ValueError):
            GestureMapping("neck", "bridge", "space", 1)


if __name__ == "__main__":
    unittest.main()
