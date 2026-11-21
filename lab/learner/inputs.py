"""The Quad Cortex's inputs as the learner uses them: which USB channels carry the dry guitar, and a recording
held open until Stop.

The desk's engine holds the unit for the whole session, in a process of its
own, and nothing else can open it meanwhile. So the learner never opens the
unit: `routing` asks the engine, through the desk's host, where the dry guitar
arrives and what the unit is on. The learner opens only the audio driver, and
only after the seat has taken the desk's audio hold (`lab.learner.seat`).
"""

import json
from threading import Event
import time

import numpy as np

from lab.hearing.audio import MAX_SECONDS, RATE, Audio

SCENES = "ABCDEFGH"
ROUTING_TIMEOUT = 8.0


def routing(host, physical_input, *, timeout=ROUTING_TIMEOUT):
    """((dry, wet left, wet right) USB channels, what the wet sound was) for a guitar input, from the engine."""
    if physical_input not in (1, 2):
        raise ValueError("Choose physical guitar input 1 or 2.")
    answer = host.call("inputs", physical_input).result(timeout)
    channels = tuple(int(channel) for channel in answer["channels"])
    if len(channels) != 3 or len(set(channels)) != 3 or any(not 1 <= channel <= 4 for channel in channels):
        raise RuntimeError("The Quad Cortex did not say which USB channels carry the dry guitar.")
    scene = answer.get("scene")
    context = {"wet_preset": answer.get("preset"),
               "wet_scene": SCENES[scene] if type(scene) is int and 0 <= scene < len(SCENES) else None,
               "wet_position": answer.get("position"), "coros": answer.get("firmware")}
    kept = {}
    for key, value in context.items():
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            continue
        if value is not None:
            kept[key] = value
    return channels, kept


def asio_device():
    """(sounddevice, device index) of the Quad Cortex's ASIO driver."""
    found = Audio()
    return found.sd, found.device


def record_inputs(seconds, channels, *, stop=None, ready=None):
    """Record ASIO inputs (1-based) until `stop` is set or `seconds` pass. `ready` is set at the first sample.

    Returns (samples, dropouts): the audio received, and the places in it, in seconds, where the driver said it
    had dropped input. A dropout does not end the recording: what was played is kept, with the gap noted."""
    if (not 0.5 <= seconds <= MAX_SECONDS or not channels
            or len(set(channels)) != len(channels) or any(channel not in range(1, 5) for channel in channels)):
        raise ValueError("Record 0.5–45 seconds from distinct Quad Cortex channels 1–4.")
    samples = np.empty((round(RATE * seconds), len(channels)), dtype=np.float32)
    if stop is not None and stop.is_set():
        return samples[:0], []
    sd, device_id = asio_device()
    cursor = 0
    finished = Event()
    dropouts = []

    def receive(indata, frames, timing, status):
        nonlocal cursor
        if stop is not None and stop.is_set():
            raise sd.CallbackStop
        if status.input_overflow and len(dropouts) < 100:
            dropouts.append(round(cursor / RATE, 3))
        count = min(frames, len(samples) - cursor)
        samples[cursor:cursor + count] = indata[:count]
        if not cursor and ready is not None:
            ready.set()
        cursor += count
        if cursor == len(samples):
            raise sd.CallbackStop

    with sd.InputStream(device=device_id, samplerate=RATE, channels=len(channels), dtype="float32",
                        extra_settings=sd.AsioSettings([channel - 1 for channel in channels]), callback=receive,
                        finished_callback=finished.set):
        previous = 0
        last_data = time.monotonic()
        while not finished.wait(0.05):
            if stop is not None and stop.is_set():
                break
            now = time.monotonic()
            if cursor != previous:
                previous, last_data = cursor, now
            elif now - last_data >= 3:
                raise RuntimeError("The Quad Cortex stopped sending audio. Close other audio apps and retry.")
    return samples[:cursor], dropouts
