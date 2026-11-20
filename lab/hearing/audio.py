"""Sound in and out of the Quad Cortex over USB, through its ASIO driver.

The unit's USB channels, as the app uses them: the computer sends the dry
phrase on output pair 5/6 (ASIO channels 4 and 5) and the unit returns the
processed sound on input pair 5/6. Its guitar inputs arrive on inputs 1 and
2 (dry) and 3 and 4 (wet) when its USB setting "dry/wet" is at its default.

Only one stream can hold the driver at a time.

`Audio` is the real driver. `lab.qc.sim.SimAudio` stands in for it in tests.
"""

import math
import os
from pathlib import Path
import time

import numpy as np
from scipy import signal
from scipy.io import wavfile

RATE = 48_000
MAX_SECONDS = 45
PAD = RATE // 4          # silence sent before a phrase, so the stream is settled when it starts
USB_PAIR = [4, 5]        # ASIO channels of USB 5/6


class Cancelled(RuntimeError):
    """A replay was ended early because the unit was wanted elsewhere."""


def read_wave(path):
    rate, samples = wavfile.read(path)
    if samples.ndim == 1:
        samples = samples[:, None]
    if np.issubdtype(samples.dtype, np.signedinteger):
        samples = samples.astype(np.float32) / float(-np.iinfo(samples.dtype).min)
    else:
        samples = samples.astype(np.float32)
    if rate != RATE:
        factor = math.gcd(rate, RATE)
        samples = signal.resample_poly(samples, RATE // factor, rate // factor, axis=0).astype(np.float32)
    if not np.isfinite(samples).all():
        raise ValueError("The recording contains invalid samples.")
    return samples


def write_wave(path, samples):
    path = Path(path)
    part = path.with_suffix(".part")
    wavfile.write(part, RATE, np.asarray(samples, dtype=np.float32))
    part.replace(path)


def db(amplitude):
    return 20 * math.log10(max(float(amplitude), 1e-10))


class Audio:
    """The Quad Cortex's ASIO driver."""

    def __init__(self):
        os.environ["SD_ENABLE_ASIO"] = "1"
        import sounddevice

        self.sd = sounddevice
        found = [(index, device) for index, device in enumerate(sounddevice.query_devices())
                 if sounddevice.query_hostapis(device["hostapi"])["name"] == "ASIO"
                 and ("neuraldsp usb audio" in device["name"].lower() or "quad cortex" in device["name"].lower())
                 and min(device["max_input_channels"], device["max_output_channels"]) >= 8]
        if len(found) != 1:
            raise RuntimeError("Expected one eight-channel Quad Cortex ASIO device. Connect it and close other "
                               "audio programs.")
        self.device = found[0][0]

    def reamp(self, dry, *, tail=2., cancel=None):
        """Send a mono phrase out of USB 5 and return what comes back on USB 5/6, from the moment it was sent.

        The return includes the unit's round-trip delay; `calibrate` measures it.
        """
        if dry.ndim != 2 or dry.shape[1] != 1:
            raise ValueError("Send a mono phrase.")
        if np.max(np.abs(dry)) >= .999:
            raise ValueError("The phrase clips. Record it again with more headroom.")
        playback = np.zeros((PAD + len(dry) + round(RATE * tail), 2), dtype=np.float32)
        playback[PAD:PAD + len(dry), 0] = dry[:, 0]
        result = np.zeros_like(playback)
        state = {"cursor": 0, "issues": []}
        sd = self.sd

        def process(indata, outdata, frames, timing, status):
            cursor = state["cursor"]
            if status and cursor > 0:
                state["issues"].append(str(status))
            count = min(frames, len(playback) - cursor)
            outdata.fill(0)
            outdata[:count] = playback[cursor:cursor + count]
            result[cursor:cursor + count] = indata[:count]
            state["cursor"] = cursor + count
            if state["cursor"] == len(playback):
                raise sd.CallbackStop

        with sd.Stream(device=self.device, samplerate=RATE, dtype="float32", channels=(2, 2),
                       extra_settings=(sd.AsioSettings(USB_PAIR), sd.AsioSettings(USB_PAIR)),
                       callback=process) as stream:
            deadline = time.monotonic() + len(playback) / RATE + 5
            while stream.active and time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():
                    stream.abort()
                    raise Cancelled("The replay was stopped.")
                sd.sleep(50)
        if state["cursor"] != len(playback) or state["issues"]:
            raise RuntimeError("The replay was interrupted or dropped audio. Check the ASIO driver.")
        return result[PAD:]

    def record(self, seconds, channels, *, cancel=None, started=None):
        """Record the unit's inputs (1-based ASIO channels) for a number of seconds. `started` is called once the
        driver delivers the first audio. A take with dropped audio, or shorter than asked, raises."""
        samples = np.zeros((round(RATE * seconds), len(channels)), dtype=np.float32)
        state = {"cursor": 0, "issues": [], "told": False}
        sd = self.sd

        def process(indata, frames, timing, status):
            cursor = state["cursor"]
            if status:
                state["issues"].append(str(status))
            count = min(frames, len(samples) - cursor)
            samples[cursor:cursor + count] = indata[:count]
            state["cursor"] = cursor + count
            if state["cursor"] >= len(samples):
                raise sd.CallbackStop

        with sd.InputStream(device=self.device, samplerate=RATE, channels=len(channels), dtype="float32",
                            extra_settings=sd.AsioSettings([channel - 1 for channel in channels]),
                            callback=process) as stream:
            deadline = time.monotonic() + seconds + 5
            while stream.active and time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():
                    stream.abort()
                    raise Cancelled("The recording was stopped.")
                if started is not None and not state["told"] and state["cursor"] > 0:
                    state["told"] = True
                    started()
                sd.sleep(50)
        if state["issues"]:
            raise RuntimeError("The recording dropped audio (" + state["issues"][0] + "). Record it again.")
        if state["cursor"] < len(samples):
            raise RuntimeError(f"The recording stopped after {state['cursor'] / RATE:.1f} s of {seconds:g} s. "
                               "Record it again.")
        return samples


def click(seconds=.5):
    """A single click a tenth of a second in, for measuring the round trip."""
    sound = np.zeros((round(RATE * seconds), 1), dtype=np.float32)
    sound[RATE // 10] = .5
    return sound


def onset(returned, sent_at=RATE // 10):
    """The delay in samples between a click sent at `sent_at` and its arrival in `returned`."""
    mono = np.abs(np.asarray(returned, dtype=np.float64)).max(axis=1)
    peak = float(mono.max())
    if peak < 1e-4:
        raise RuntimeError("Nothing came back. Check that the chain passes USB 5/6 through.")
    return int(np.argmax(mono > .2 * peak)) - sent_at


class Loop:
    """A phrase played round and round into USB 5, with the processed return's peak noted.

    `muted` silences what is sent without losing the place in the phrase, so
    that switching between sounds does not restart it. The audio itself runs
    on the driver's own thread; `start` and `stop` may be called from any.
    """

    def __init__(self, audio, dry):
        if dry.ndim != 2 or dry.shape[1] != 1 or not .001 <= float(np.max(np.abs(dry))) < .999:
            raise ValueError("Loop a mono phrase that is neither silent nor clipped.")
        self.audio = audio
        riff = np.pad(dry[:, 0].astype(np.float32), (0, RATE // 2))
        fade = np.linspace(0, 1, RATE // 100, dtype=np.float32)
        riff[:len(fade)] *= fade
        riff[len(dry) - len(fade):len(dry)] *= fade[::-1]
        self.riff, self.cursor, self.muted, self.peak, self.stream = riff, 0, False, 0., None

    def _process(self, indata, outdata, frames, timing, status):
        outdata.fill(0)
        if not self.muted:
            written = 0
            while written < frames:
                count = min(frames - written, len(self.riff) - self.cursor)
                outdata[written:written + count, 0] = self.riff[self.cursor:self.cursor + count]
                self.cursor = (self.cursor + count) % len(self.riff)
                written += count
            self.peak = max(self.peak, float(np.max(np.abs(indata))) if len(indata) else 0.)
        else:
            self.cursor = (self.cursor + frames) % len(self.riff)

    def start(self):
        sd = getattr(self.audio, "sd", None)
        if sd is None:          # the simulated unit has no audio driver: the loop is silent
            return self
        self.stream = sd.Stream(device=self.audio.device, samplerate=RATE, dtype="float32", channels=(2, 2),
                                latency="high", extra_settings=(sd.AsioSettings(USB_PAIR), sd.AsioSettings(USB_PAIR)),
                                callback=self._process)
        self.stream.start()
        return self

    def stop(self):
        if self.stream is not None:
            self.stream.abort()
            self.stream.close()
            self.stream = None
