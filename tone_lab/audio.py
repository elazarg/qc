"""48 kHz audio I/O and file analysis."""

from contextlib import contextmanager
from dataclasses import dataclass
import math
import os
from pathlib import Path
from threading import Event
import time

import numpy as np
import pyloudnorm
from scipy import signal
from scipy.io import wavfile

RATE = 48_000
MAX_SECONDS = 45
BANDS = (65, 125, 250, 500, 1_000, 2_000, 4_000, 8_000, 12_000)


def read_wave(path):
    rate, samples = wavfile.read(path)
    if rate <= 0 or not 0 < len(samples) <= (MAX_SECONDS + 3) * rate:
        raise ValueError(f"Choose a WAV excerpt at most {MAX_SECONDS + 3} seconds long.")
    if samples.ndim == 1:
        samples = samples[:, None]
    if samples.shape[1] not in (1, 2):
        raise ValueError("Use a mono or stereo WAV.")
    if np.issubdtype(samples.dtype, np.unsignedinteger):
        midpoint = (np.iinfo(samples.dtype).max + 1) / 2
        samples = (samples.astype(np.float32) - midpoint) / midpoint
    elif np.issubdtype(samples.dtype, np.signedinteger):
        samples = samples.astype(np.float32) / float(-np.iinfo(samples.dtype).min)
    else:
        samples = samples.astype(np.float32)
    if not np.isfinite(samples).all():
        raise ValueError("The WAV contains invalid samples.")
    if rate != RATE:
        factor = math.gcd(rate, RATE)
        samples = signal.resample_poly(samples, RATE // factor, rate // factor, axis=0)
    if len(samples) < RATE // 2:
        raise ValueError("Use at least half a second of audio.")
    return samples.astype(np.float32)


def write_wave(path, samples, *, pcm16=False):
    # Float preserves measurement and reamp gain without quantization or clipping.
    path = Path(path)
    temp = path.with_suffix(".part")
    samples = np.asarray(samples, dtype=np.float32)
    if pcm16:
        samples = np.rint(np.clip(samples, -1, 1) * 32767).astype(np.int16)
    wavfile.write(temp, RATE, samples)
    temp.replace(path)


def db(amplitude):
    return 20 * math.log10(max(float(amplitude), 1e-10))


def describe(samples):
    peak = float(np.max(np.abs(samples)))
    loudness = float(pyloudnorm.Meter(RATE).integrated_loudness(samples))
    freq, spectrum = signal.welch(samples, RATE, nperseg=4096, axis=0)
    power = spectrum.mean(axis=1)
    bands = np.array([power[(freq >= lo) & (freq < hi)].sum()
                      for lo, hi in zip(BANDS, BANDS[1:])])
    shape = 10 * np.log10(np.maximum(bands / max(bands.sum(), 1e-20), 1e-8))
    rms = np.sqrt(np.mean(np.square(samples, dtype=np.float64)))
    return {"peak_dbfs": db(peak), "rms_dbfs": db(rms),
            "lufs": loudness if math.isfinite(loudness) else None,
            "crest_db": db(peak) - db(rms), "spectrum_db": shape.tolist(),
            "clipped_samples": int(np.count_nonzero(np.abs(samples) >= 0.999))}


def distance(candidate, reference):
    bands = np.asarray(candidate["spectrum_db"]) - reference["spectrum_db"]
    return round(float(np.mean(np.minimum(np.abs(bands), 30))), 2)


def loudness_plan(metrics, *, target_lufs=-23.0, offset_db=0.0, ceiling_db=-3.0, max_boost_db=0.0):
    """Plan a static common loudness with headroom for every participating tone."""
    metrics = tuple(metrics)
    if not all(math.isfinite(value) for value in (target_lufs, offset_db, ceiling_db, max_boost_db)):
        raise ValueError("Use finite loudness targets and limits.")
    if not metrics:
        return {"reference_lufs": None, "requested_lufs": None, "target_lufs": None, "trims_db": []}
    lufs = [item["lufs"] for item in metrics]
    peaks = [item.get("true_peak_dbtp_estimate", item.get("peak_dbfs")) for item in metrics]
    lane_limits = [item.get("max_gain_db", 6.) for item in metrics]
    if any(not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in (*lufs, *peaks, *lane_limits)):
        raise ValueError("Use audible measurements with verified peak levels.")
    headroom = min(level + ceiling_db - peak for level, peak in zip(lufs, peaks))
    reference = min(target_lufs, min(lufs), headroom)
    requested = reference + offset_db
    allowed_boost = max(0., min(offset_db, max_boost_db, 6.))
    gain_ceiling = min(level + max(0., min(allowed_boost, limit)) for level, limit in zip(lufs, lane_limits))
    target = min(requested, headroom, gain_ceiling)
    return {"reference_lufs": reference, "requested_lufs": requested, "target_lufs": target,
            "trims_db": [target - level for level in lufs]}


def audition_gains(metrics):
    return [10 ** (db / 20) for db in loudness_plan(metrics)["trims_db"]]


def asio_device():
    os.environ["SD_ENABLE_ASIO"] = "1"
    import sounddevice as sd

    candidates = [(i, device) for i, device in enumerate(sd.query_devices())
                  if sd.query_hostapis(device["hostapi"])["name"] == "ASIO"
                  and ("neuraldsp usb audio" in device["name"].lower()
                       or "quad cortex" in device["name"].lower())
                  and min(device["max_input_channels"], device["max_output_channels"]) >= 8]
    if len(candidates) != 1:
        raise RuntimeError("Expected one eight-channel Quad Cortex ASIO device. Connect it and close other audio sessions.")
    return sd, *candidates[0]


def check_asio():
    sd, device_id, info = asio_device()
    # Opening a stream checks driver, format and channel mapping without starting it.
    stream = sd.Stream(device=device_id, samplerate=RATE, dtype="float32", channels=(2, 2),
                       extra_settings=(sd.AsioSettings([4, 5]), sd.AsioSettings([4, 5])))
    stream.close()
    return {"device": info["name"], "inputs": info["max_input_channels"],
            "outputs": info["max_output_channels"], "rate": RATE}


def record_di(seconds, input_channel, *, stop=None):
    return record_inputs(seconds, (input_channel,), stop=stop)


def record_inputs(seconds, channels, *, stop=None, ready=None):
    if (not 0.5 <= seconds <= MAX_SECONDS or not channels
            or len(set(channels)) != len(channels) or any(channel not in range(1, 5) for channel in channels)):
        raise ValueError("Record 0.5–45 seconds from distinct Quad Cortex channels 1–4.")
    samples = np.empty((round(RATE * seconds), len(channels)), dtype=np.float32)
    if stop is not None and stop.is_set():
        return samples[:0]
    sd, device_id, _ = asio_device()
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

    indexes = [channel - 1 for channel in channels]
    with sd.InputStream(device=device_id, samplerate=RATE, channels=len(channels), dtype="float32",
                        extra_settings=sd.AsioSettings(indexes), callback=receive,
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


class Cancelled(RuntimeError):
    """A replay was ended before it finished because the unit was wanted elsewhere."""


def reamp(dry, *, tail=2., cancel=None):
    """Play a dry take through USB 5 and return what comes back. `tail` is the seconds kept after it.

    Setting `cancel` ends the replay within a twentieth of a second and raises Cancelled.
    """
    if dry.shape[1] != 1:
        raise ValueError("Use a mono dry guitar recording to reamp.")
    if np.max(np.abs(dry)) >= 0.999:
        raise ValueError("The dry recording clips. Capture a new DI with more headroom.")
    sd, device_id, _ = asio_device()
    # A mono guitar goes to USB 5 only. Keep USB 6 silent and collect a stereo return.
    padding, tail = RATE // 4, round(RATE * tail)
    playback = np.zeros((len(dry) + padding + tail, 2), dtype=np.float32)
    playback[padding:padding + len(dry), 0] = dry[:, 0]
    result = np.empty_like(playback)
    cursor = 0
    issues = []

    def process(indata, outdata, frames, timing, status):
        nonlocal cursor
        if status and cursor > 0:
            issues.append(str(status))
        count = min(frames, len(playback) - cursor)
        outdata.fill(0)
        outdata[:count] = playback[cursor:cursor + count]
        result[cursor:cursor + count] = indata[:count]
        cursor += count
        if cursor == len(playback):
            raise sd.CallbackStop

    with sd.Stream(device=device_id, samplerate=RATE, dtype="float32", channels=(2, 2),
                   extra_settings=(sd.AsioSettings([4, 5]), sd.AsioSettings([4, 5])),
                   callback=process) as stream:
        deadline = time.monotonic() + len(playback) / RATE + 5
        while stream.active and time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                stream.abort()
                raise Cancelled("The replay was stopped.")
            sd.sleep(50)
    if cursor != len(playback) or issues:
        raise RuntimeError("The reamp stream was interrupted or dropped audio. Retry after checking the ASIO driver.")
    # Leave a small lead-in for driver latency. Save the same window for every pass.
    return result[padding - RATE // 20:]


@dataclass
class PhraseCursor:
    """Keep the next DI sample across input changes and brief preset reloads."""

    frame: int = 0
    muted: bool = False
    returns: bool = False
    level_probe: object = None


@contextmanager
def loop_feed(dry, *, monitor_return=False, return_channels=None, transport=None):
    """Send guitar to Grid USB 5 with an optional shared phrase cursor."""
    if (dry.ndim != 2 or dry.shape[1] != 1 or not np.isfinite(dry).all()
            or not 0.001 <= np.max(np.abs(dry)) < .999):
        raise ValueError("Use a mono, unclipped DI for the loop.")
    sd, index, _ = asio_device()
    riff = np.pad(dry[:, 0], (0, RATE // 2))
    fade = np.linspace(0, 1, RATE // 100, dtype=np.float32)
    riff[:len(fade)] *= fade
    riff[len(dry) - len(fade):len(dry)] *= fade[::-1]
    transport = PhraseCursor() if transport is None else transport
    cursor = transport.frame % len(riff)
    meters = {"frames": 0, "peak": 0.0, "dropouts": 0, "longest_callback_gap_ms": 0.0,
              "cursor": cursor, "loop_frames": len(riff)}
    last_callback = None

    def feed(outdata, frames, status):
        nonlocal cursor
        nonlocal last_callback
        now = time.monotonic()
        if last_callback is not None:
            meters["longest_callback_gap_ms"] = max(meters["longest_callback_gap_ms"], (now - last_callback) * 1000)
        last_callback = now
        if meters["frames"] and status:
            meters["dropouts"] += 1
        outdata.fill(0)
        if transport.muted:
            cursor = (cursor + frames) % len(riff)
        else:
            written = 0
            while written < frames:
                count = min(frames - written, len(riff) - cursor)
                outdata[written:written + count, 0] = riff[cursor:cursor + count]
                cursor = (cursor + count) % len(riff)
                written += count
        transport.frame = cursor
        meters["cursor"] = cursor
        meters["frames"] += frames

    settings = sd.AsioSettings([4, 5])
    options = {"device": index, "samplerate": RATE, "dtype": "float32", "latency": "high"}
    if monitor_return:
        def duplex(indata, outdata, frames, timing, status):
            feed(outdata, frames, status)
            meters["peak"] = max(meters["peak"], abs(float(indata.min())), abs(float(indata.max())))
            probe = transport.level_probe
            if probe is not None and not transport.muted:
                probe.write(indata, interrupted=bool(status))

        inputs = sd.AsioSettings([channel - 1 for channel in return_channels]) if return_channels else settings
        stream = sd.Stream(**options, channels=(2, 2), extra_settings=(inputs, settings), callback=duplex)
    else:
        def output(outdata, frames, timing, status):
            feed(outdata, frames, status)

        stream = sd.OutputStream(**options, channels=2, extra_settings=settings, callback=output)
    with stream:
        transport.returns = monitor_return
        try:
            yield stream, meters
        finally:
            transport.returns = False
