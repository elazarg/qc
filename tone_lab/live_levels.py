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


def lane_boost_headroom(lanes):
    _, high = leveling.db_range()
    return max(0., min((high - db for db, silent in lanes.values() if not silent), default=0.))


def lane_targets(lanes, requested_db, muted, *, max_boost_db=0.):
    low, high = leveling.db_range()
    # Uniform gain preserves the blend of parallel physical outputs.
    floor = max((low - db for db, silent in lanes.values() if not silent), default=low)
    ceiling = max(0., min(max_boost_db, lane_boost_headroom(lanes)))
    applied = min(ceiling, max(requested_db, floor))
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
        self.measurements = {}
        self.capture_identities = {}
        self.configure(value)
        self.firmware = firmware
        self.memory = load_memory() if self.enabled and firmware else {}
        self.keys = {}
        self.observed = {}
        self.take = self.side = self.identity = self.remembered = None
        self.source = "phrase"
        self.lanes = self.applied = {}
        self.headroom = {}
        self.settled_trims = {}
        self.recent_trims = {}
        self.gain = 0.
        self.last_event = None
        self.return_unavailable = False

    def configure(self, value):
        self.master, self.muted, self.match, measurements = settings(value)
        for side in tuple(self.capture_identities):
            if self.measurements.get(side) != measurements.get(side):
                self.capture_identities.pop(side)
        self.measurements = measurements
        self.contexts = value.get("contexts", {}) if value else {}
        self.loudness = float((value or {}).get("loudness_db", self.master if self.match else 0.))
        self.target = float((value or {}).get("target_lufs", -23.))
        if not math.isfinite(self.loudness) or not -40 <= self.loudness <= 6:
            raise ValueError("Choose an audition loudness offset between −40 and +6 dB.")
        if not math.isfinite(self.target) or not -60 <= self.target <= 0:
            raise ValueError("Choose a digital loudness reference between −60 and 0 LUFS.")

    def select(self, tone, values, take, *, source="phrase"):
        self.side = tone.get("tone_id")
        self.take = take
        self.source = source
        self.identity = key(tone, values, take)
        self.remembered = remembered_key(tone, values, take, self.firmware)
        self.keys.setdefault(take, {})[self.side] = self.identity

    def bind(self, base, scene, routes):
        if self.enabled:
            self.lanes = terminal_lanes(base, scene, routes)
            _, self.applied = lane_targets(self.lanes, 0., False)

    def capture_measurement(self, side, identity):
        reading = self.measurements.get(side)
        if not valid(reading) or identity is None:
            return None
        # The same reported capture cannot follow a new sound or phrase after an edit.
        if self.capture_identities.setdefault(side, identity) != identity:
            return None
        return reading

    def measurements_for_take(self):
        observations, bases = {}, set()
        for side in ("a", "b"):
            if side == self.side:
                identity, remembered = self.identity, self.remembered
            elif selected := self.contexts.get(side):
                values = selected.get("values", {})
                identity = key(selected, values, self.take)
                remembered = remembered_key(selected, values, self.take, self.firmware)
            elif self.contexts:
                continue
            else:
                identity, remembered = self.keys.get(self.take, {}).get(side), None
            reading, basis = self.capture_measurement(side, identity), "capture"
            if not valid(reading):
                reading, basis = self.observed.get(identity), "phrase"
                if not valid(reading) and remembered in self.memory:
                    reading, basis = self.memory[remembered], "prior"
            if valid(reading):
                observations[side] = {**reading, "max_gain_db": self.headroom.get(identity, 6.)}
                bases.add(basis)
        basis = "phrase" if "phrase" in bases else "prior" if "prior" in bases else "capture"
        return observations, basis

    def match_for_take(self):
        observations, basis = self.measurements_for_take()
        return matched_pair(observations), basis

    def smart_plan(self, observations):
        held = self.settled_trims.get((self.take, self.side), self.recent_trims.get(self.side, 0.))
        comparing = len(self.contexts or self.keys.get(self.take, {})) == 2
        if self.side not in observations:
            return None, held + min(0., self.loudness), comparing, held < -.05
        paired = len(observations) == 2
        waiting = comparing and not paired
        rows = list(observations)
        metrics = [observations[side] for side in rows]
        reference = self.target
        if waiting:
            reference = min(reference, observations[self.side]["lufs"] + held)
        base_plan = audio.loudness_plan(metrics, target_lufs=reference)
        for side, trim in zip(rows, base_plan["trims_db"]):
            self.settled_trims[self.take, side] = trim
            self.recent_trims[side] = trim
        offset = min(0., self.loudness) if waiting else self.loudness
        # A phrase measurement cannot predict a harder pick during free playing.
        boost = max(0., offset) if self.source == "phrase" else 0.
        plan = audio.loudness_plan(metrics, target_lufs=reference, offset_db=offset,
                                   max_boost_db=boost)
        # Keep the user's requested upward change visible while a comparison is measured.
        if waiting:
            plan["requested_lufs"] = plan["reference_lufs"] + self.loudness
        return plan, plan["trims_db"][rows.index(self.side)], waiting, waiting and held < -.05

    def apply(self, qc, check_active, events):
        if not self.enabled:
            return
        self.headroom[self.identity] = lane_boost_headroom(self.lanes)
        observations, basis = self.measurements_for_take() if self.match else ({}, None)
        if self.match:
            if self.side in observations:
                observations[self.side] = {**observations[self.side], "max_gain_db": self.headroom[self.identity]}
            plan, requested, waiting, held = self.smart_plan(observations)
        else:
            plan, requested, waiting, held = None, self.master, False, False
        can_boost = plan is not None and not waiting and self.source == "phrase"
        max_boost = max(0., self.loudness) if can_boost else 0.
        gain, targets = lane_targets(self.lanes, requested, self.muted, max_boost_db=max_boost)
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
        elif plan is not None and not waiting:
            status = "matched" if len(observations) == 2 else "leveled"
        elif failed:
            status = "unavailable"
            reason = "The phrase return was silent, clipped, or interrupted."
        elif self.return_unavailable:
            status = "unavailable"
            reason = "The Quad Cortex USB return is unavailable for measuring this phrase."
        else:
            status = "pending"
            if self.loudness > .05:
                reason = "Measure this phrase before increasing its level."
        lane_limited = bool(self.lanes and abs(self.gain - requested) > .05)
        if lane_limited:
            status = "limited"
            reason = "The Quad Cortex output control reached its adjustment limit."
        planned = {name: round(plan[name], 1) if plan is not None else None
                   for name in ("reference_lufs", "requested_lufs", "target_lufs")}
        measured = observations.get(self.side)
        estimated = None
        if measured and self.lanes and not self.muted and self.source == "phrase":
            estimated = round(measured["lufs"] + self.gain, 1)
        event = {"event": "level", "tone_id": self.side, "master_db": self.master, "muted": self.muted,
                 "loudness_db": self.loudness, "match": self.match,
                 "match_db": round(requested, 1) if plan is not None else None,
                 "applied_db": round(self.gain, 1), "status": status, "basis": basis if plan is not None else None,
                 **planned, "estimated_lufs": estimated, "held": held, "reason": reason,
                 "limited": (lane_limited or (self.match and plan is None and self.loudness > .05)
                             or (plan is not None and plan["target_lufs"] < plan["requested_lufs"] - .05))}
        if event != self.last_event:
            events(event)
            self.last_event = event

    def clear_boost(self, qc, check_active):
        """Remove a verified lift before changing what the measurement describes."""
        if self.gain > 0:
            gain, targets = lane_targets(self.lanes, 0., self.muted)
            check_active()
            write(qc, targets, self.applied)
            self.gain, self.applied = gain, targets

    def observe(self, cursor, meters):
        """Return true when a complete phrase may have changed the matching trim."""
        if (not self.enabled or not self.match or self.muted or not cursor.returns or not self.lanes
                or self.identity in self.observed or self.remembered in self.memory
                or self.capture_measurement(self.side, self.identity)):
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
