"""A loudness reference from music playing on the Quad Cortex Windows output."""

import math
import os

from lab.hearing.audio import RATE
from lab.hearing.measure import measure

SPOTIFY_NORMAL = {"lufs": -14., "endpoint": "Spotify Normal"}


def reference(value):
    """Read a saved calibration, if it still has a usable target and endpoint name."""
    if isinstance(value, dict):
        lufs, endpoint = value.get("lufs"), value.get("endpoint")
        if type(lufs) in (int, float) and math.isfinite(lufs) and -65 <= lufs <= 3 and isinstance(endpoint, str):
            return {"lufs": lufs, "endpoint": endpoint}
    return None


def capture(seconds=8):
    """Measure only what Windows sends to the default Quad Cortex playback endpoint. Keep no recording."""
    if os.environ.get("TONE_LAB_TESTING"):
        raise RuntimeError("A test tried to open the Windows playback device.")
    import pythoncom

    # This worker needs COM even when SoundCard was already imported during setup on the desk thread.
    pythoncom.CoInitialize()
    try:
        endpoint, samples = _loopback(seconds)
    finally:
        pythoncom.CoUninitialize()
    result = measure(samples)
    if result.lufs is None or result.lufs < -65:
        raise ValueError("Little or no music reached this output. Play a typical section and measure again.")
    return {"lufs": round(result.lufs, 1), "endpoint": endpoint}


def _loopback(seconds):
    import soundcard

    output = soundcard.default_speaker()
    if not output or "quad cortex" not in output.name.casefold() or "3/4" in output.name:
        raise ValueError("Set Quad Cortex Out 1/2 as the Windows default output, then measure again.")
    loopback = soundcard.get_microphone(output.id, include_loopback=True)
    with loopback.recorder(samplerate=RATE) as recorder:
        return output.name, recorder.record(numframes=round(seconds * RATE))
