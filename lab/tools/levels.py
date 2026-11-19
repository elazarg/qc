"""Levels: how loud the desk plays, and matching the loudness of sounds, or of a song's parts.

Carried over from the old Levels card (`volume_ui`), its offline trim planner (`level_plan`) and the pair
matcher of its live engine (`live_levels`), onto the new core:

- **Listening level.** One offset for both sides, from −40 dB up to the app's ceiling (`unit.CEILING_DB`),
  with Quieter, Louder, Reset and Mute. "Matched" keeps the desk's own A/B matching and moves both together;
  "manual" sets the level outright. The engine plays each side at a level of its own (`Engine.level`); the
  tool's offset is added to whatever the desk asks for there, and never takes a row past the ceiling.
- **Matching sounds.** The phrase is played silently through each sound on the unit, each return measured
  (gated loudness over the phrase, true peak over all of it), and one common loudness planned: only cuts
  unless a lift is asked for, never past a true-peak ceiling, a sound near full scale left out with the
  reason. For A and B the plan is played at once. For a song's parts it is written into each part as a
  clean Gain block at the end of the chain (the app's own output control), so the parts stay matched when
  they are saved to the unit; a part's trim never goes above the ceiling.
"""

import math
import tkinter as tk

import numpy as np
import pyloudnorm
from scipy import signal

from .. import catalog
from ..audio import RATE, db
from ..chain import Block, COLUMNS, INTERNAL
from ..theme import DIM, INK, PAPER
from ..unit import CEILING_DB
from .common import (Window, button, heard_chain, label, listbox, menu_choice, phrase, render_many, section)

FLOOR_DB = -40.
DIGITAL_NOTE = ("These are levels of the sound returned over USB. Check the Quad Cortex input meter with your "
                "hardest playing, and the analog output, interface or mixer, separately.")
TRIM_NOTE = ("A trim is applied after the blocks that make the tone, so the amp's drive and compression stay "
             "as they are.")


# ---- the listening level (from volume_ui) ----

def bounded_level(value, high=CEILING_DB):
    try:
        level = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return round(max(FLOOR_DB, min(high, level))) if math.isfinite(level) else 0


def saved_settings(value):
    value = value if isinstance(value, dict) else {}
    master = bounded_level(value.get("master_db", 0), 0)
    match = value.get("match") is not False
    return {"master_db": master, "loudness_db": bounded_level(value.get("loudness_db", master if match else 0)),
            "muted": value.get("muted") is True, "match": match}


def listening_command(settings, command):
    if command in ("mute", "unmute"):
        return {**settings, "muted": command == "mute"}
    key = "loudness_db" if settings["match"] else "master_db"
    level = settings[key] + {"quieter": -3, "louder": 3, "slightly quieter": -1,
                             "slightly louder": 1, "normal": -settings[key]}[command]
    return saved_settings({**settings, key: level})


def reading(settings):
    if settings["muted"]:
        return "Muted"
    if not settings["match"]:
        return f"{settings['master_db']} dB"
    offset = settings["loudness_db"]
    return f"{offset:+d} dB from matched" if offset else "Matched level"


def side_level(asked_db, settings):
    """What a side plays at, given what the desk asked for it and the listening settings."""
    if settings["muted"]:
        return FLOOR_DB
    if settings["match"]:
        return max(FLOOR_DB, min(CEILING_DB, asked_db + settings["loudness_db"]))
    from ..engine import PREVIEW_DB

    return max(FLOOR_DB, min(CEILING_DB, PREVIEW_DB + settings["master_db"]))


# ---- measuring (from level_plan) ----

def _loudness(samples):
    try:
        value = float(pyloudnorm.Meter(RATE).integrated_loudness(samples))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def measure(samples, *, passage=None):
    """Peaks over the whole of it, gated loudness over the passage (seconds), if one is given."""
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim == 1:
        samples = samples[:, None]
    if (samples.ndim != 2 or samples.shape[1] not in (1, 2) or len(samples) < RATE // 2
            or not np.isfinite(samples).all()):
        raise ValueError("Use at least half a second of finite mono or stereo audio.")
    active = samples
    if passage is not None:
        start, end = (round(second * RATE) for second in passage)
        if not 0 <= start < end <= len(samples) or end - start < RATE // 2:
            raise ValueError("The selected passage does not fit the audio or is shorter than half a second.")
        active = samples[start:end]
    peak = float(np.max(np.abs(samples)))
    # Interpolating four times over estimates the peaks between samples.
    true_peak = max(peak, float(np.max(np.abs(signal.resample_poly(samples, 4, 1, axis=0)))))
    result = {"sample_peak_dbfs": db(peak), "true_peak_dbtp_estimate": db(true_peak),
              "lufs": _loudness(active), "near_rail_samples": int(np.count_nonzero(np.abs(samples) >= .999)),
              "duration_seconds": len(samples) / RATE, "measured_seconds": len(active) / RATE,
              "channels": samples.shape[1]}
    if active.shape[1] == 2:
        stereo = float(np.sqrt(np.mean(np.square(active, dtype=np.float64))))
        mono = float(np.sqrt(np.mean(np.square(active.mean(axis=1), dtype=np.float64))))
        result["mono_fold_down_db"] = db(mono / stereo) if stereo > 1e-10 else None
    return result


def plan(readings, *, reference=None, target_lufs=None, ceiling_dbtp=-3., offsets=None):
    """One common loudness for named readings, as trims in dB.

    `readings` is {name: measure(...)} or {name: {"issue": why}}. Without a reference or target the plan only
    cuts. `offsets` ({name: dB}, within ±12) make a sound deliberately louder or quieter than the rest.
    """
    if not math.isfinite(ceiling_dbtp) or not -24 <= ceiling_dbtp <= 0:
        raise ValueError("Choose a true-peak ceiling between -24 and 0 dBTP.")
    if target_lufs is not None and (not math.isfinite(target_lufs) or not -60 <= target_lufs <= 0):
        raise ValueError("Choose a common loudness target between -60 and 0 LUFS.")
    offsets = offsets or {}
    for name, value in offsets.items():
        if name not in readings:
            raise ValueError(f"Unknown sound: {name}.")
        if not math.isfinite(value) or not -12 <= value <= 12:
            raise ValueError("Choose relative loudness offsets within ±12 dB.")
    if reference is not None and reference not in readings:
        raise ValueError(f"Unknown sound: {reference}.")
    rows = {}
    for name, found in readings.items():
        row = {"name": name, "requested_offset_db": float(offsets.get(name, 0.)), "trim_db": None, **found}
        if "issue" not in row:
            if row.get("near_rail_samples"):
                row["issue"] = "Near full scale in the return. Lower the level inside the sound before matching."
            elif row.get("lufs") is None:
                row["issue"] = "Too quiet for a loudness reading."
        rows[name] = row
    valid = {name: row for name, row in rows.items() if "issue" not in row}
    if reference and reference not in valid:
        raise ValueError("Choose a reference with a measurable level and digital headroom.")
    if len(valid) < 2:
        raise ValueError("Use at least two measurable sounds without samples near full scale.")
    bases = {name: row["lufs"] - row["requested_offset_db"] for name, row in valid.items()}
    requested = target_lufs if target_lufs is not None else bases[reference] if reference else min(bases.values())
    allowed = min(bases[name] + ceiling_dbtp - row["true_peak_dbtp_estimate"] for name, row in valid.items())
    base = min(requested, allowed)
    for name, row in valid.items():
        # Rounded down, so a trim copied to a tenth-of-a-dB control still respects the ceiling.
        trim = math.floor((base - bases[name] + 1e-9) * 10) / 10
        row.update(trim_db=trim, expected_lufs=round(row["lufs"] + trim, 2),
                   expected_true_peak_dbtp=round(row["true_peak_dbtp_estimate"] + trim, 2))
    return {"reference": reference, "requested_base_lufs": round(requested, 2), "planned_base_lufs": round(base, 2),
            "true_peak_ceiling_dbtp": ceiling_dbtp, "lowered_for_headroom": base < requested - .005,
            "rows": list(rows.values()), "measurement_note": TRIM_NOTE}


# ---- the pair matcher (from live_levels and the old audio.loudness_plan) ----

def loudness_plan(metrics, *, target_lufs=-23., offset_db=0., ceiling_db=-3., max_boost_db=0.):
    """A common loudness with headroom for every sound; a lift only when asked for and at most 6 dB."""
    metrics = tuple(metrics)
    if not all(math.isfinite(value) for value in (target_lufs, offset_db, ceiling_db, max_boost_db)):
        raise ValueError("Use finite loudness targets and limits.")
    if not metrics:
        return {"reference_lufs": None, "requested_lufs": None, "target_lufs": None, "trims_db": []}
    lufs = [item["lufs"] for item in metrics]
    peaks = [item.get("true_peak_dbtp_estimate", item.get("peak_dbfs")) for item in metrics]
    limits = [item.get("max_gain_db", CEILING_DB) for item in metrics]
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in (*lufs, *peaks, *limits)):
        raise ValueError("Use audible measurements with verified peak levels.")
    headroom = min(level + ceiling_db - peak for level, peak in zip(lufs, peaks))
    reference = min(target_lufs, min(lufs), headroom)
    requested = reference + offset_db
    allowed_boost = max(0., min(offset_db, max_boost_db, CEILING_DB))
    gain_ceiling = min(level + max(0., min(allowed_boost, limit)) for level, limit in zip(lufs, limits))
    target = min(requested, headroom, gain_ceiling)
    return {"reference_lufs": reference, "requested_lufs": requested, "target_lufs": target,
            "trims_db": [target - level for level in lufs]}


def valid(measurement):
    """A measurement with a loudness and a peak, and nothing at full scale."""
    if not isinstance(measurement, dict):
        return False
    clipped = measurement.get("clipped_samples", measurement.get("near_rail_samples", 0))
    numbers = (measurement.get("lufs"), measurement.get("true_peak_dbtp_estimate", measurement.get("peak_dbfs")))
    return clipped == 0 and all(isinstance(value, (int, float)) and math.isfinite(value) for value in numbers)


def matched_pair(measurements):
    """Trims in dB that play A and B equally loud, or {} unless both are measurable."""
    if all(valid(measurements.get(side)) for side in ("a", "b")):
        return dict(zip(("a", "b"), loudness_plan([measurements[side] for side in ("a", "b")])["trims_db"]))
    return {}


# ---- output trims on rows (from live_levels.lane_targets) ----

def lane_targets(lanes, requested_db, muted, *, max_boost_db=0.):
    """Uniform gain over rows that leave the preset, keeping their blend and any mute already set.

    `lanes` is {row: (volume dB, muted)}. Returns (gain applied, {row: (volume dB, mute flag)}). No row goes
    below the floor or above the ceiling.
    """
    floor = max((FLOOR_DB - level for level, silent in lanes.values() if not silent), default=FLOOR_DB)
    headroom = max(0., min((CEILING_DB - level for level, silent in lanes.values() if not silent), default=0.))
    ceiling = max(0., min(max_boost_db, headroom))
    applied = min(ceiling, max(requested_db, floor))
    return applied, {row: (min(CEILING_DB, max(FLOOR_DB, level + applied)), float(silent or muted))
                     for row, (level, silent) in lanes.items()}


def lanes(chain, scene=0):
    """{row: (output volume dB, muted)} for the rows of a chain that leave the preset."""
    from qc_mcp import leveling

    found = {}
    for r, row in enumerate(chain.rows):
        if row.outlet in INTERNAL:
            continue
        output = dict(row.output)
        volume = output.get(catalog.LANE_VOLUME, leveling.norm_of(0.))
        mute = output.get(catalog.LANE_MUTE, 0.)
        volume = volume[scene] if isinstance(volume, tuple) else volume
        mute = mute[scene] if isinstance(mute, tuple) else mute
        found[r] = (leveling.db_of(volume), bool(mute >= .5))
    return found


# ---- a trim written into a sound ----

def with_trim(chain, trim_db):
    """The chain with `trim_db` more on a clean Gain block at the end of each row that leaves the preset.

    An existing Gain block at the end of the row takes the trim; otherwise one is added after the last block.
    Returns (chain, applied dB); applied is less than asked when the ceiling or the block's range stopped it.
    """
    gain = catalog.model(catalog.GAIN_BLOCK)
    low = gain.params[0].low
    applied = None
    rows = [r for r, row in enumerate(chain.rows) if row.blocks and row.outlet not in INTERNAL] or \
           [r for r, row in enumerate(chain.rows) if row.blocks][:1]
    if not rows:
        raise ValueError("The sound has no blocks to trim.")
    for r in rows:
        row = chain.rows[r]
        column, last = row.blocks[-1]
        if last.model == catalog.GAIN_BLOCK and not isinstance(last.get(0), tuple):
            before = last.display(0) or 0.
            block, where = last, column
        else:
            if column + 1 >= COLUMNS:
                raise ValueError(f"Row {r + 1} has no free place at its end for a level trim.")
            before, block, where = 0., Block(catalog.GAIN_BLOCK), column + 1
        after = max(low, min(CEILING_DB, before + trim_db))
        chain = chain.with_block(r, where, block.set_display(0, after))
        applied = after - before if applied is None else min(applied, after - before, key=abs)
    return chain, round(applied, 2)


# ---- operations, run where the unit is held ----

def measure_sounds(engine, chains, dry, *, stop=None):
    """{name: measure(return)} for {name: chain}, each the phrase played silently through it on the unit."""
    names = list(chains)
    returns = render_many(engine, [(chains[name], None) for name in names], dry, tail=0., stop=stop)
    found = {}
    for name, samples in zip(names, returns):
        try:
            found[name] = measure(samples)
        except ValueError as error:
            found[name] = {"issue": str(error)}
    return found


def install(engine, settings, *, stop=None):
    """Play the desk at the listening settings from now on, and at once.

    The engine's own `level` is wrapped on this engine once: what the desk asks for a side is kept, and the
    level played is that plus the listening offset, within the floor and the ceiling.
    """
    settings = saved_settings(settings)
    engine.listen_at(lambda asked_db: side_level(asked_db, settings))
    return {side: engine.levels[side] for side in ("a", "b")}


# ---- the window ----

class LevelsWindow(Window):
    TITLE = "Levels"
    SIZE = "620x640"

    def build(self):
        self.settings = saved_settings(getattr(self.studio, "listening", None))
        self.readings = self.planned = None
        listen = section(self.body, "Listening level", f"Both sides together, up to {CEILING_DB:+.0f} dB, the "
                                                       "app's ceiling. Matched keeps A and B equally loud.")
        row = tk.Frame(listen, bg=listen["bg"])
        row.pack(fill="x")
        button(row, "Quieter", lambda: self.command("quieter")).pack(side="left")
        self.level = tk.IntVar(master=self.top)
        self.slider = tk.Scale(row, from_=FLOOR_DB, to=CEILING_DB, orient="horizontal", resolution=1, length=220,
                               showvalue=False, variable=self.level, bg=listen["bg"], fg=INK,
                               troughcolor=PAPER, highlightthickness=0, borderwidth=0)
        self.slider.pack(side="left", padx=6)
        self.slider.bind("<ButtonRelease-1>", lambda _: self.slid())
        button(row, "Louder", lambda: self.command("louder")).pack(side="left")
        options = tk.Frame(listen, bg=listen["bg"])
        options.pack(fill="x", pady=(6, 0))
        self.reading = label(options, size=10, bold=True)
        self.reading.pack(side="left")
        button(options, "Reset", lambda: self.command("normal"), quiet=True).pack(side="right")
        self.mute = button(options, "Mute", self.toggle_mute, quiet=True)
        self.mute.pack(side="right", padx=4)
        self.manual = button(options, "Manual", self.toggle_match, quiet=True)
        self.manual.pack(side="right")

        match = section(self.body, "Match the loudness", "The phrase is played silently through each sound and "
                                                       "the returns measured. " + TRIM_NOTE)
        row = tk.Frame(match, bg=match["bg"])
        row.pack(fill="x")
        self.what = tk.StringVar(master=self.top, value="A and B")
        menu_choice(row, self.what, ("A and B", "The song's parts")).pack(side="left")
        self.anchor = tk.StringVar(master=self.top, value="Quietest")
        menu_choice(row, self.anchor, ("Quietest", "First")).pack(side="left", padx=6)
        button(row, "Measure", self.measure).pack(side="left")
        self.apply_button = button(row, "Apply", self.apply)
        self.apply_button.pack(side="right")
        self.rows = listbox(match, height=7)
        self.rows.pack(fill="x", pady=(8, 0))
        label(match, DIGITAL_NOTE, size=8, color=DIM, wrap=540).pack(fill="x", pady=(6, 0))
        self.sync()

    # listening

    def command(self, action):
        self.change(listening_command(self.settings, action))

    def slid(self):
        key = "loudness_db" if self.settings["match"] else "master_db"
        self.change(saved_settings({**self.settings, key: self.level.get()}))

    def toggle_mute(self):
        self.change({**self.settings, "muted": not self.settings["muted"]})

    def toggle_match(self):
        self.change(saved_settings({**self.settings, "match": not self.settings["match"]}))

    def change(self, settings):
        self.settings = saved_settings(settings)
        self.studio.listening = self.settings
        self.sync()
        self.call("lab.tools.levels.install", self.settings, done=lambda levels: None, working="Setting the level…")

    def sync(self):
        settings = self.settings
        match = settings["match"]
        self.slider.configure(to=CEILING_DB if match else 0)
        self.level.set(settings["loudness_db"] if match else settings["master_db"])
        self.reading.configure(text=reading(settings))
        self.mute.configure(text="Unmute" if settings["muted"] else "Mute")
        self.manual.configure(text="Matched" if not match else "Manual")

    # matching

    def targets(self):
        """{name: (chain, sound)} for what is to be matched."""
        if self.what.get() == "A and B":
            return {side.upper(): (heard_chain(sound), sound) for side in ("a", "b")
                    if (sound := self.studio.sound(side)) is not None}
        song = self.studio.state.song
        if not song:
            return {}
        found = {}
        for part in self.studio.store.song(song)["parts"]:
            sound = self.studio.store.sound(part["sound"])
            found[part["name"]] = (heard_chain(sound), sound)
        return found

    def measure(self):
        targets = self.targets()
        if len(targets) < 2:
            self.say("Put sounds on A and B first." if self.what.get() == "A and B" else
                     "Name a song on the desk and give it at least two parts.")
            return
        taken = phrase(self.studio)
        if taken is None:
            self.say("Record or choose a phrase on the desk first.")
            return
        self.targets_measured = targets

        def measured(readings):
            self.readings = readings
            reference = None if self.anchor.get() == "Quietest" else next(iter(readings))
            try:
                self.planned = plan(readings, reference=reference)
            except ValueError as error:
                self.planned = None
                self.say(str(error))
            self.show()

        self.call("lab.tools.levels.measure_sounds", {name: chain for name, (chain, _) in targets.items()},
                  taken[1], done=measured, working="Playing the phrase silently through each sound…")

    def show(self):
        self.rows.delete(0, "end")
        rows = self.planned["rows"] if self.planned else [{"name": name, **found}
                                                         for name, found in (self.readings or {}).items()]
        for row in rows:
            if row.get("issue"):
                self.rows.insert("end", f"{row['name']}   {row['issue']}")
                continue
            trim = row.get("trim_db")
            self.rows.insert("end", f"{row['name']}   {row['lufs']:.1f} LUFS, peak {row['true_peak_dbtp_estimate']:.1f}"
                                    f" dBTP" + (f"   →  {trim:+.1f} dB" if trim is not None else ""))
        if self.planned and self.planned["lowered_for_headroom"]:
            self.say("Lowered further to keep every sound's peaks under the ceiling.")

    def apply(self):
        if not self.planned:
            self.say("Measure first.")
            return
        trims = {row["name"]: row["trim_db"] for row in self.planned["rows"] if row.get("trim_db") is not None}
        if self.what.get() == "A and B":
            from ..engine import PREVIEW_DB

            for name, trim in trims.items():
                self.call("level", name.lower(), PREVIEW_DB + trim, done=lambda _: None, working="Matching A and B…")
            self.say("A and B now play equally loud.")
            return
        song = self.studio.store.song(self.studio.state.song)
        limited = []
        for part in song["parts"]:
            if part["name"] not in trims:
                continue
            _, sound = self.targets_measured[part["name"]]
            from ..sounds import Sound

            try:
                chain, applied = with_trim(sound.chain, trims[part["name"]])
            except ValueError as error:
                limited.append(f"{part['name']}: {error}")
                continue
            if abs(applied - trims[part["name"]]) > .05:
                limited.append(f"{part['name']} stopped at {applied:+.1f} dB")
            leveled = Sound(sound.name, chain, {"kind": "revised", "from": sound.id, "request": "level matched"},
                            parent=sound.id, knobs=dict(sound.knobs), explanation=sound.explanation)
            self.studio.store.save(leveled)
            part["sound"] = leveled.id
        self.studio.store.save_song(song)
        self.say("The parts are matched; save them to the Quad Cortex from the desk."
                 + (" " + "; ".join(limited) if limited else ""))


def open(desk):  # noqa: A001
    return LevelsWindow.open(desk)
