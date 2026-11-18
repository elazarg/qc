"""Match a tone pair with Quad Cortex post-chain output trims."""

from hashlib import sha256
import json
import math
from pathlib import Path

import numpy as np
from qc_mcp import autolevel, leveling

from . import audio, level_plan, session


def settings(value):
    value = value or {}
    master = float(value.get("master_db", 0))
    if not math.isfinite(master) or not -40 <= master <= 0:
        raise ValueError("Choose a monitoring volume between −40 and 0 dB.")
    return master, bool(value.get("muted")), bool(value.get("match", True)), value.get("measurements") or {}


def valid(measurement):
    return (isinstance(measurement, dict) and measurement.get("clipped_samples") == 0
            and all(isinstance(measurement.get(key), (int, float)) and math.isfinite(measurement[key])
                    for key in ("lufs", "peak_dbfs")))


def matched_pair(measurements):
    if all(valid(measurements.get(side)) for side in ("a", "b")):
        pair = [measurements[side] for side in ("a", "b")]
        return dict(zip(("a", "b"), [audio.db(gain) for gain in audio.audition_gains(pair)]))
    return {}


def key(config, values, take):
    return json.dumps((take, config.get("preset"), config.get("scene"), config.get("draft"),
                       config.get("gain"), config.get("physical_input"), values), sort_keys=True)


def remembered_key(config, values, take, firmware):
    # A full draft defines the chain. A saved preset can change at the same address.
    if not config.get("draft") or not take or not firmware:
        return None
    try:
        folder = Path(take).resolve()
        files = [(item.st_size, item.st_mtime_ns)
                 for item in ((folder / filename).stat() for filename in ("clip.json", "dry.wav"))]
    except OSError:
        return None
    raw = json.dumps((1, firmware, files, key(config, values, str(folder))))
    return sha256(raw.encode("utf-8")).hexdigest()


def load_memory():
    try:
        data = json.loads((session.SESSIONS / "live-levels.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {identity: reading for identity, reading in list(data.items())[-48:] if valid(reading)}


def save_memory(data):
    try:
        session.write_json(session.SESSIONS / "live-levels.json", data)
    except OSError:
        # Device playback continues if this local, reusable estimate cannot be written.
        pass


def terminal_lanes(base, scene, physical_routes):
    """Get baseline volume and mute only on rows that leave the preset."""
    result = {}
    for row, (_, outlet) in enumerate(physical_routes):
        if outlet in autolevel.INTERNAL_OUT_PORTS:
            continue
        controls = base.chains[row].output_control
        if len(controls) != 1:
            return {}
        params = controls[0].params
        if len(params) <= leveling.MUTE:
            return {}
        values = []
        for index in (leveling.VOLUME, leveling.MUTE):
            param = params[index]
            slot = scene if param.scene_mode else 0
            if param.expression or slot >= len(param.param_values):
                return {}
            value = param.param_values[slot]
            if value.WhichOneof("value") != "float_value":
                return {}
            values.append(value.float_value)
        result[row] = (leveling.db_of(values[0]), values[1] >= .5)
    return result


def lane_targets(lanes, requested_db, muted):
    low, high = leveling.db_range()
    # Uniform gain preserves the blend of parallel physical outputs.
    floor = max((low - db for db, silent in lanes.values() if not silent), default=low)
    applied = min(0, max(requested_db, floor))
    return applied, {
        row: (leveling.norm_of(min(high, max(low, db + applied))), float(silent or muted))
        for row, (db, silent) in lanes.items()
    }


def write(qc, targets, previous):
    for row, values in targets.items():
        old = previous[row]
        for parameter, target, before in zip((leveling.VOLUME, leveling.MUTE), values, old):
            if abs(target - before) > 1e-5:
                qc.set_lane_param(row, "output_control", parameter, target)


class MeasureLoop:
    """Collect one settled loop of the processed USB return."""

    def __init__(self, frames, offset_db):
        self.samples = np.empty((frames, 2), dtype=np.float32)
        self.wait = audio.RATE // 2
        self.used = 0
        self.offset_db = offset_db
        self.interrupted = False

    @property
    def ready(self):
        return self.used == len(self.samples)

    def write(self, block, *, interrupted=False):
        skip = min(self.wait, len(block))
        self.wait -= skip
        count = min(len(block) - skip, len(self.samples) - self.used)
        if count:
            self.interrupted |= interrupted
            self.samples[self.used:self.used + count] = block[skip:skip + count]
            self.used += count

    def metrics(self):
        if self.interrupted:
            return None
        reading = level_plan.measure(self.samples)
        if reading.get("near_rail_samples") or reading.get("lufs") is None:
            return None
        return {"lufs": reading["lufs"] - self.offset_db, "clipped_samples": 0,
                "peak_dbfs": reading["true_peak_dbtp_estimate"] - self.offset_db}


class Levels:
    def __init__(self, value, *, firmware=None):
        self.enabled = value is not None
        self.configure(value)
        self.firmware = firmware
        self.memory = load_memory() if self.enabled and firmware else {}
        self.keys = {}
        self.observed = {}
        self.take = self.side = self.identity = self.remembered = None
        self.lanes = self.applied = {}
        self.gain = 0.
        self.last_event = None
        self.return_unavailable = False

    def configure(self, value):
        self.master, self.muted, self.match, self.measurements = settings(value)
        self.contexts = value.get("contexts", {}) if value else {}

    def select(self, tone, values, take):
        self.side = tone.get("tone_id")
        self.take = take
        self.identity = key(tone, values, take)
        self.remembered = remembered_key(tone, values, take, self.firmware)
        self.keys.setdefault(take, {})[self.side] = self.identity

    def bind(self, base, scene, routes):
        if self.enabled:
            self.lanes = terminal_lanes(base, scene, routes)
            _, self.applied = lane_targets(self.lanes, 0., False)

    def match_for_take(self):
        if trims := matched_pair(self.measurements):
            return trims, "capture"
        observations, previously_measured = {}, False
        for side in ("a", "b"):
            if side == self.side:
                identity, remembered = self.identity, self.remembered
            elif selected := self.contexts.get(side):
                values = selected.get("values", {})
                identity = key(selected, values, self.take)
                remembered = remembered_key(selected, values, self.take, self.firmware)
            else:
                identity, remembered = self.keys.get(self.take, {}).get(side), None
            if identity in self.observed:
                observations[side] = self.observed[identity]
            elif remembered in self.memory:
                observations[side] = self.memory[remembered]
                previously_measured = True
        return matched_pair(observations), "prior" if previously_measured else "phrase"

    def apply(self, qc, check_active, events):
        if not self.enabled:
            return
        trims, basis = self.match_for_take() if self.match else ({}, None)
        trim = trims.get(self.side)
        requested = self.master + (trim or 0.)
        gain, targets = lane_targets(self.lanes, requested, self.muted)
        if targets != self.applied:
            check_active()
            write(qc, targets, self.applied)
            self.applied = targets
        self.gain = gain if self.lanes else 0.
        failed = any(identity in self.observed and self.observed[identity] is None
                     for identity in self.keys.get(self.take, {}).values())
        reason = None
        if not self.lanes:
            status = "unavailable"
            reason = "This tone does not expose a plain post-chain output level."
        elif not self.match:
            status = "off"
        elif trim is not None:
            status = "matched"
        elif failed:
            status = "unavailable"
            reason = "The phrase return was silent, clipped, or interrupted."
        elif self.return_unavailable:
            status = "unavailable"
            reason = "The Quad Cortex USB return is unavailable for measuring this phrase."
        else:
            status = "pending"
        event = {"event": "level", "tone_id": self.side, "master_db": self.master, "muted": self.muted,
                 "match": self.match, "match_db": round(trim, 1) if trim is not None else None,
                 "applied_db": round(self.gain, 1), "status": status, "basis": basis if trim is not None else None,
                 "limited": bool(self.lanes and self.gain > requested + .05), "reason": reason}
        if event != self.last_event:
            events(event)
            self.last_event = event

    def observe(self, cursor, meters):
        """Return true when a complete phrase may have changed the matching trim."""
        if (not self.enabled or not self.match or self.muted or not cursor.returns or not self.lanes
                or self.identity in self.observed or self.remembered in self.memory or matched_pair(self.measurements)):
            cursor.level_probe = None
            return False
        capture = cursor.level_probe
        if capture is None:
            cursor.level_probe = MeasureLoop(meters["loop_frames"], self.gain)
            return False
        if not capture.ready:
            return False
        measurement = capture.metrics()
        self.observed[self.identity] = measurement
        if measurement is not None and self.remembered:
            self.memory[self.remembered] = measurement
            self.memory = dict(list(self.memory.items())[-48:])
            save_memory(self.memory)
        cursor.level_probe = None
        return True
