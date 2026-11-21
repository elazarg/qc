"""One silent listening, pass by pass, against a made-up unit: nothing here opens an audio driver."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import numpy as np

from lab.hearing import listen as listening
from lab.hearing.audio import RATE
from lab.hearing.measure import measured_lines
from lab.sound.chain import Chain, Row

LATENCY = 96


class Unit:
    firmware = "9.9.9"

    def guard(self):
        pass

    def select_scene(self, scene):
        pass


class Rig:
    def __init__(self):
        self.unit = Unit()

    def ready(self, chain, *, stop=None):
        return 0, chain

    def connect(self, chain, routes, **options):
        return chain


class Audio:
    """A chain on a made-up unit: an optional clipper, then repeats (seconds after, gain), then steady noise. What
    still rings when a pass ends is heard at the start of the next, as on the unit. `gate` silences any pass
    whose peak is under it."""

    def __init__(self, *, drive=None, taps=(), noise=0., gate=0., latency=LATENCY):
        self.drive, self.taps, self.noise, self.gate, self.latency = drive, taps, noise, gate, latency
        self.carry = np.zeros(0)
        self.sent = []
        self.random = np.random.default_rng(0)

    def reamp(self, dry, *, tail=2., cancel=None):
        sound = np.concatenate([dry[:, 0].astype(np.float64), np.zeros(round(tail * RATE))])
        self.sent.append(float(np.max(np.abs(dry))) if len(dry) else 0.)
        shaped = np.tanh(self.drive * sound) * .3 if self.drive else sound.copy()
        if float(np.max(np.abs(sound))) < self.gate:
            shaped[:] = 0
        longest = max([round(seconds * RATE) for seconds, _ in self.taps], default=0)
        out = np.zeros(len(sound) + longest + self.latency)
        out[self.latency:self.latency + len(sound)] += shaped
        for seconds, gain in self.taps:
            start = self.latency + round(seconds * RATE)
            out[start:start + len(sound)] += gain * shaped
        out[:len(self.carry)] += self.carry[:len(out)]
        rest = out[len(sound):]
        if len(self.carry) > len(out):
            rest = self.carry[len(sound):].copy()
            rest[:len(out) - len(sound)] += out[len(sound):] - self.carry[len(sound):len(out)]
        self.carry = rest
        back = out[:len(sound)] + self.random.normal(0, self.noise, len(sound)) if self.noise else out[:len(sound)]
        return np.stack([back, back], axis=1).astype(np.float32)

    @property
    def silences(self):
        return sum(1 for peak in self.sent if peak == 0.)


def phrase(seconds=2.):
    time = np.arange(round(seconds * RATE)) / RATE
    notes = sum(np.sin(2 * np.pi * 196 * k * time) / k for k in range(1, 12)) * np.exp(-3 * (time % .5))
    return (.2 * notes / np.max(np.abs(notes)))[:, None].astype(np.float32)


def hear(audio, offset=LATENCY):
    return listening.listen(Rig(), audio, Chain((Row(),)), phrase(), offset)


def repeats(seconds, gain, feedback, count=14):
    return tuple((seconds * k, gain * feedback ** (k - 1)) for k in range(1, count + 1))


class ListenTest(unittest.TestCase):
    def test_a_dry_chain_is_three_passes_and_nothing_else(self):
        audio = Audio(drive=8)
        found = hear(audio)
        self.assertEqual(len(audio.sent), 3)
        self.assertEqual(audio.silences, 0)
        self.assertEqual(found.probe["version"], 2)
        self.assertAlmostEqual(found.probe["click_ms"], 0., delta=.1)
        self.assertGreater(found.probe["distortion_db"], -30)
        self.assertFalse(found.soft_gated)
        self.assertEqual(found.tail, "dry")

    def test_a_gate_that_closes_on_the_softer_pass_is_a_finding_not_a_routing_fault(self):
        # B3: this raised "Nothing came back ... Check that the chain passes USB 5/6 through".
        found = hear(Audio(gate=.1))
        self.assertTrue(found.soft_gated)
        self.assertEqual(found.compression, -1.)
        self.assertIsNone(found.attack_soft_ms)
        self.assertIn("Gate: the phrase played 12 dB softer did not come through.", measured_lines(found.to_record()))

    def test_nothing_coming_back_at_all_is_still_refused(self):
        with self.assertRaisesRegex(RuntimeError, "Nothing came back"):
            hear(Audio(gate=10.))

    def test_a_click_back_before_the_round_trip_allows_refuses_the_listening(self):
        # C1: a round trip measured with another buffer size went unnoticed, and the click's echo and decay were
        # read from the wrong place.
        with self.assertRaisesRegex(listening.RoundTripChanged, "round trip has changed"):
            hear(Audio(drive=8), offset=LATENCY + 480)             # measured 10 ms longer than it now is
        self.assertTrue(issubclass(listening.RoundTripChanged, RuntimeError))

    def test_a_chain_that_is_slow_is_measured_from_where_its_click_arrives(self):
        found = hear(Audio(taps=((.35, .5),), latency=LATENCY + 480))   # 10 ms of its own
        self.assertAlmostEqual(found.probe["click_ms"], 10., delta=.1)
        self.assertAlmostEqual(found.probe["echo_ms"], 350., delta=3)

    def test_a_long_delay_is_given_time_to_fall_silent_between_passes(self):
        # C3: the softer phrase's repeats rang into the click's two seconds, where the first repeat (500 ms) was
        # then not found.
        audio = Audio(taps=repeats(.5, .6, .6))
        found = hear(audio)
        self.assertGreaterEqual(audio.silences, 2)
        self.assertAlmostEqual(found.probe["echo_ms"], 500., delta=3)
        self.assertAlmostEqual(found.probe["echo_db"], -4.4, delta=1.)
        self.assertEqual(found.tail, "wet")
        self.assertIsNone(found.probe["intermod_db"])              # not measurable behind these repeats

    def test_steady_noise_is_not_waited_for(self):
        audio = Audio(noise=.01)
        hear(audio)
        self.assertLessEqual(audio.silences, 2)                     # one look after each pass, no more

    def test_the_wait_has_an_end(self):
        audio = Audio(taps=repeats(.5, .3, .7, count=60))           # 6 dB a second: over ten seconds to fall 75 dB
        hear(audio)
        self.assertGreaterEqual(audio.silences, listening.MOST_WAIT_S)
        self.assertLessEqual(audio.silences, 2 * listening.MOST_WAIT_S)


class CalibrationTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_a_file_that_cannot_be_used_reads_as_not_measured(self):
        # C1: {"firmware": ...} without an offset raised KeyError, and a list AttributeError.
        for text in ('{"firmware": "9.9.9"}', "[1, 2]", "nonsense", '{"firmware": "9.9.9", "offset": "7750"}',
                     '{"firmware": "9.9.9", "offset": -5}', '{"firmware": "1.0.0", "offset": 7750}'):
            (self.folder / "calibration.json").write_text(text, encoding="utf-8")
            self.assertIsNone(listening.calibration(self.folder, "9.9.9"), text)
        self.assertIsNone(listening.calibration(self.folder / "missing", "9.9.9"))

    def test_the_round_trip_is_measured_and_kept_whole(self):
        offset = listening.calibrate(Rig(), Audio(latency=7750), self.folder)
        self.assertEqual(offset, 7750)
        self.assertEqual(json.loads((self.folder / "calibration.json").read_text(encoding="utf-8")),
                         {"firmware": "9.9.9", "offset": 7750})
        self.assertEqual(listening.calibration(self.folder, "9.9.9"), 7750)
        self.assertEqual([path.name for path in self.folder.iterdir()], ["calibration.json"])


if __name__ == "__main__":
    unittest.main()
