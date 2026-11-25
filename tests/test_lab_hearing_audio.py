"""Wave files and the loop, without an audio driver."""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
import warnings

import numpy as np
from scipy.io import wavfile

from lab.hearing import audio
from lab.hearing.audio import RATE


def tone(rate=RATE, pitch=440., peak=.5):
    return peak * np.sin(2 * np.pi * pitch * np.arange(rate) / rate)


def pitch_of(samples):
    spectrum = np.abs(np.fft.rfft(samples[:, 0] * np.hanning(len(samples))))
    return float(np.argmax(spectrum)) * RATE / len(samples)


class WaveTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        warnings.simplefilter("ignore", wavfile.WavFileWarning)

    def read(self, rate, data):
        wavfile.write(self.folder / "in.wav", rate, data)
        return audio.read_wave(self.folder / "in.wav")

    def test_every_format_reads_as_the_same_sound(self):
        files = {"16 bit": (RATE, (tone() * 32767).astype(np.int16)),
                 "32 bit": (RATE, (tone() * 2147483647).astype(np.int32)),
                 "8 bit": (RATE, (tone() * 127 + 128).astype(np.uint8)),          # C4: read as 0..255, peak 191
                 "float 32 stereo": (RATE, np.stack([tone(), tone()], axis=1).astype(np.float32)),
                 "float 64": (RATE, tone()),
                 "44.1 kHz": (44100, (tone(44100) * 32767).astype(np.int16)),
                 "96 kHz": (96000, tone(96000).astype(np.float32))}
        for name, (rate, data) in files.items():
            found = self.read(rate, data)
            self.assertEqual((len(found), found.dtype), (RATE, np.float32), name)
            self.assertAlmostEqual(float(np.abs(found).max()), .5, delta=.01, msg=name)
            self.assertAlmostEqual(float(found.mean()), 0., delta=.01, msg=name)
            self.assertAlmostEqual(pitch_of(found), 440., delta=1., msg=name)
        self.assertEqual(self.read(RATE, np.stack([tone(), tone()], axis=1).astype(np.float32)).shape, (RATE, 2))

    def test_24_bit_reads_at_its_level(self):
        body = b"".join(int(value).to_bytes(3, "little", signed=True)
                        for value in (tone()[:4800] * (2 ** 23 - 1)).astype(np.int64))
        header = (b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt "
                  + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 3, 3, 24) + b"data" + struct.pack("<I", len(body)))
        (self.folder / "pcm24.wav").write_bytes(header + body)
        self.assertAlmostEqual(float(np.abs(audio.read_wave(self.folder / "pcm24.wav")).max()), .5, delta=.01)

    def test_writing_keeps_the_samples_and_leaves_no_part_behind(self):
        loud = (tone() * 3)[:, None]
        audio.write_wave(self.folder / "take.dry.wav", loud)
        audio.write_wave(self.folder / "take.wet.wav", loud * .1)        # its part file is its own, not take.part
        rate, back = wavfile.read(self.folder / "take.dry.wav")
        self.assertEqual((rate, back.dtype), (RATE, np.float32))
        self.assertAlmostEqual(float(np.abs(back).max()), 1.5, places=3)  # float: nothing is clipped on the way
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["take.dry.wav", "take.wet.wav"])

    def test_the_two_decibel_helpers_are_named_apart(self):
        self.assertAlmostEqual(audio.db(.1), -20.)
        self.assertAlmostEqual(audio.power_db(.1), -10.)
        self.assertEqual((audio.db(0.), audio.power_db(0.)), (-200., -70.))


class LoopTest(unittest.TestCase):
    def play(self, loop, frames):
        out = np.ones((frames, 2), dtype=np.float32)
        loop._process(np.zeros((frames, 2), dtype=np.float32), out, frames, None, None)
        return out

    def test_the_phrase_goes_round_on_the_first_channel_only(self):
        dry = (tone()[:1000] * .5)[:, None].astype(np.float32)
        loop = audio.Loop(object(), dry)
        length = len(loop.riff)
        out = np.concatenate([self.play(loop, 4096) for _ in range(8)])
        self.assertEqual(loop.cursor, (8 * 4096) % length)
        self.assertEqual(float(np.abs(out[:, 1]).max()), 0.)
        self.assertTrue(np.array_equal(out[length:length + 1000, 0], out[:1000, 0]))
        self.assertAlmostEqual(float(np.abs(out[:, 0]).max()), .25, delta=.01)

    def test_a_phrase_played_once_goes_through_once_and_then_is_silent(self):
        dry = (tone()[:1000] * .5)[:, None].astype(np.float32)
        loop = audio.Loop(object(), dry, once=True)
        length = len(loop.riff)
        out = np.concatenate([self.play(loop, 4096) for _ in range(8)])
        self.assertTrue(loop.over)
        self.assertGreater(float(np.abs(out[:1000, 0]).max()), .1)
        self.assertEqual(float(np.abs(out[length:, 0]).max()), 0.)

    def test_a_phrase_shorter_than_its_fades_still_loops(self):
        # C4: under 480 samples the fade-out could not be laid over the phrase and the loop raised.
        loop = audio.Loop(object(), (tone()[:300] * .5)[:, None].astype(np.float32))
        out = self.play(loop, 2048)
        self.assertGreater(float(np.abs(out[:, 0]).max()), .1)
        self.assertEqual(float(out[0, 0]), 0.)

    def test_a_silent_or_clipped_phrase_is_refused(self):
        for dry in (np.zeros((RATE, 1), dtype=np.float32), np.ones((RATE, 1), dtype=np.float32)):
            with self.assertRaises(ValueError):
                audio.Loop(object(), dry)


if __name__ == "__main__":
    unittest.main()
