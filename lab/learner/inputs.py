"""The Quad Cortex's inputs as the learner uses them: its ASIO driver, a recording held open until
Stop, and which USB channels carry the dry guitar.

The desk's engine holds the unit for writing in a process of its own. The
learner only reads: a shared connection, for the USB dry/wet setting and the
preset a take was played through, and the ASIO inputs, which it opens after
the desk has stopped playing (see `lab.desk.window.LearnerSeat.submit`).
"""

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
import time

import numpy as np

from lab.hearing.audio import MAX_SECONDS, RATE, Audio
from lab.learner.folders import LOG

SCENES = "ABCDEFGH"


class EditorActive(RuntimeError):
    """Cortex Control owns the hardware. The learner can still use local audio."""


def editor_running():
    if sys.platform != "win32":
        return False
    tasklist = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tasklist.exe"
    try:
        result = subprocess.run([str(tasklist), "/FI", "IMAGENAME eq Cortex Control.exe", "/FO", "CSV", "/NH"],
                                capture_output=True, timeout=2.0, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return b'"cortex control.exe"' in result.stdout.lower()


@contextmanager
def connect():
    """A shared, read-only connection. The stock editor has priority."""
    from qc_mcp.transport import QuadCortex

    handoff = "Cortex Control is using the Quad Cortex. Close it to use this device action."
    if editor_running():
        raise EditorActive(handoff)
    qc = QuadCortex(share=True)
    try:
        try:
            if editor_running():
                raise EditorActive(handoff)
            qc.open()
        except (OSError, RuntimeError) as error:
            if ("exclusively" in str(error).casefold() or "sharing violation" in str(error).casefold()) and editor_running():
                raise EditorActive(handoff) from error
            raise
        if not qc.protocol_version_verified:
            raise RuntimeError("The Quad Cortex firmware could not be verified.")
        yield qc
    finally:
        qc.close()


def channels_from(qc, physical_input):
    """(dry, wet left, wet right) USB channels for a guitar input, from the unit's USB Dry/Wet setting."""
    if physical_input not in (1, 2):
        raise ValueError("Choose physical guitar input 1 or 2.")
    io = qc.read_message("IOSettings")
    if io is None:
        io = qc.read_message("IOSettings")
    if (io is None or not io.HasField("settings") or not io.settings.HasField("usb_port")
            or io.settings.usb_port.dry_wet not in (0, 1)):
        raise RuntimeError("Could not read the Quad Cortex USB Dry/Wet setting. Check the connection and retry.")
    return (physical_input + 2, 1, 2) if io.settings.usb_port.dry_wet else (physical_input, 3, 4)


def recording_channels(physical_input, *, include_context=False):
    with connect() as qc:
        channels = channels_from(qc, physical_input)
        context = {}
        if include_context:
            try:
                preset = qc.get_current_preset()
                scene = qc.read_state("Scene", timeout_ms=1800)
                if preset is None or scene is None or not 0 <= scene.selected_scene < len(SCENES):
                    raise RuntimeError("The Quad Cortex did not report its preset and scene.")
                context = {"wet_preset": preset.name, "wet_scene": SCENES[scene.selected_scene],
                           "wet_position": qc.get_setlist_position(), "coros": qc.firmware}
            except Exception:
                LOG.warning("Recording preset context unavailable", exc_info=True)
    return (channels, context) if include_context else channels


def asio_device():
    """(sounddevice, device index) of the Quad Cortex's ASIO driver."""
    found = Audio()
    return found.sd, found.device


def record_inputs(seconds, channels, *, stop=None, ready=None):
    """Record ASIO inputs (1-based) until `stop` is set or `seconds` pass. `ready` is set at the first sample."""
    if (not 0.5 <= seconds <= MAX_SECONDS or not channels
            or len(set(channels)) != len(channels) or any(channel not in range(1, 5) for channel in channels)):
        raise ValueError("Record 0.5–45 seconds from distinct Quad Cortex channels 1–4.")
    samples = np.empty((round(RATE * seconds), len(channels)), dtype=np.float32)
    if stop is not None and stop.is_set():
        return samples[:0]
    sd, device_id = asio_device()
    cursor = 0
    finished = Event()
    overflowed = False

    def receive(indata, frames, timing, status):
        nonlocal cursor, overflowed
        if (stop is not None and stop.is_set()) or status.input_overflow:
            overflowed = status.input_overflow
            raise sd.CallbackStop
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
    if overflowed:
        raise RuntimeError("The driver dropped input samples. Close other audio applications and retry.")
    return samples[:cursor]
