"""Levels: how loud the desk plays, and matching the loudness of a song's parts.

Carried over from the old Levels card (`volume_ui`) and its offline trim planner (`level_plan`), onto the new
core:

- **Listening level.** One offset for both sides, from −40 dB up to the app's ceiling (`unit.CEILING_DB`),
  with Quieter, Louder, Reset and Mute. "Matched" keeps the desk's own A/B matching and moves both together;
  "manual" sets the level outright. The engine plays each side at a level of its own (`Engine.level`); the
  tool's offset is added to whatever the desk asks for there, and never takes a row past the ceiling. While
  an offset or a mute is on it is kept in `studio.listening` (None otherwise), and the desk says so under the
  tiles with a way back to its own level, so it stays on when this tool is left.
- **Matching a song's parts.** The phrase is played silently through each part's sound on the unit, each
  return measured (`hearing.measure`: gated loudness, true peak), and one common loudness planned: only cuts
  unless a lift is asked for, never past a true-peak ceiling, a sound at full scale left out with the reason.
  The plan is written into each part as a clean Gain block at the end of the chain (the app's own output
  control), so the parts stay matched when they are saved to the unit; a part's trim never goes above the
  ceiling.

A and B are matched by the desk itself whenever it plays (`Studio._match_levels`), from what listening measured
of each; this tool does not set their levels.
"""

import math
import re
import tkinter as tk

from lab.sound import catalog
from lab.hearing.measure import measure
from lab.sound.chain import Block, COLUMNS, INTERNAL, SCENES
from lab.desk.theme import INK, PAPER
from lab.qc.unit import CEILING_DB
from lab.tools.common import (Chips, Tool, button, caption, heard_chain, label, listbox, phrase, render_many,
                              room, section, sized)

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


def at_rest(settings):
    """Whether the settings change nothing: the desk plays at its own levels."""
    return settings == saved_settings(None)


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
    from lab.qc.engine import PREVIEW_DB

    return max(FLOOR_DB, min(CEILING_DB, PREVIEW_DB + settings["master_db"]))


# ---- planning (from level_plan) ----

def plan(readings, *, reference=None, target_lufs=None, ceiling_dbtp=-3., offsets=None):
    """One common loudness for named readings, as trims in dB.

    `readings` is {name: a `hearing.measure` record} or {name: {"issue": why}}. Without a reference or target
    the plan only cuts. `offsets` ({name: dB}, within ±12) make a sound deliberately louder or quieter than
    the rest.
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
            if row.get("clipped"):
                row["issue"] = "At full scale in the return. Lower the level inside the sound before matching."
            elif row.get("lufs") is None:
                row["issue"] = "Too quiet for a loudness reading."
        rows[name] = row
    valid = {name: row for name, row in rows.items() if "issue" not in row}
    if reference and reference not in valid:
        raise ValueError("Choose a reference with a measurable level and digital headroom.")
    if len(valid) < 2:
        raise ValueError("Use at least two measurable sounds without samples at full scale.")
    bases = {name: row["lufs"] - row["requested_offset_db"] for name, row in valid.items()}
    requested = target_lufs if target_lufs is not None else bases[reference] if reference else min(bases.values())
    allowed = min(bases[name] + ceiling_dbtp - row["true_peak_db"] for name, row in valid.items())
    base = min(requested, allowed)
    for name, row in valid.items():
        # Rounded down, so a trim copied to a tenth-of-a-dB control still respects the ceiling.
        trim = math.floor((base - bases[name] + 1e-9) * 10) / 10
        row.update(trim_db=trim, expected_lufs=round(row["lufs"] + trim, 2),
                   expected_true_peak_db=round(row["true_peak_db"] + trim, 2))
    return {"reference": reference, "requested_base_lufs": round(requested, 2), "planned_base_lufs": round(base, 2),
            "true_peak_ceiling_dbtp": ceiling_dbtp, "lowered_for_headroom": base < requested - .005,
            "rows": list(rows.values()), "measurement_note": TRIM_NOTE}


# ---- a trim written into a sound ----

def with_trim(chain, trim_db, *, scene=None):
    """The chain with `trim_db` more on a clean Gain block at the end of each row that leaves the preset.

    An existing Gain block at the end of the row takes the trim; otherwise one is added after the last block.
    Returns (chain, applied dB); applied is less than asked when the ceiling or the block's range stopped it.
    """
    gain = catalog.model(catalog.GAIN_BLOCK)
    low = gain.params[0].low
    applied = None
    rows = [r for r, row in enumerate(chain.rows) if (row.blocks or row.split) and row.outlet not in INTERNAL]
    rows = rows or [r for r, row in enumerate(chain.rows) if row.blocks][:1]
    if not rows:
        raise ValueError("The sound has no blocks to trim.")
    for r in rows:
        row = chain.rows[r]
        column, last = row.blocks[-1] if row.blocks else (-1, None)
        join = row.split[1] if row.split else -1
        if last and column >= join and last.model == catalog.GAIN_BLOCK and not (
                last.bypass or last.expression or last.stomp):
            block, where = last, column
        else:
            where = max(column + 1, join)
            if where >= COLUMNS:
                raise ValueError(f"Row {r + 1} has no free place at its end for a level trim.")
            block = Block(catalog.GAIN_BLOCK)
        before = [block.display(0, index) or 0. for index in range(SCENES)]
        affected = range(SCENES) if scene is None else (scene,)
        after = list(before)
        for index in affected:
            after[index] = max(low, min(CEILING_DB, before[index] + trim_db))
            change = after[index] - before[index]
            applied = change if applied is None else min(applied, change, key=abs)
        levels = tuple(catalog.to_norm(catalog.GAIN_BLOCK, 0, value) for value in after)
        chain = chain.with_block(r, where, block.set(0, levels[0] if len(set(levels)) == 1 else levels))
    return chain, round(applied, 2)


# ---- operations, run where the unit is held ----

def measure_sounds(engine, chains, dry, scenes=None, *, stop=None):
    """{name: `hearing.measure` record} for {name: chain}, each the phrase played silently through it on the unit."""
    names = list(chains)
    returns = render_many(engine, [(chains[name], (scenes or {}).get(name)) for name in names], dry,
                          tail=.5, stop=stop)
    return {name: measure(samples).to_record() for name, samples in zip(names, returns)}


def part_offset(name):
    """An explicit relative level in a part name, such as Solo +2 dB."""
    found = re.search(r"([+-]\d+(?:\.\d+)?)\s*dB\s*$", name, re.IGNORECASE)
    return float(found[1]) if found else 0.


def install(engine, settings, *, stop=None):
    """Play the desk at the listening settings from now on, and at once.

    What the desk asks for a side is kept, and the level played is that plus the listening offset, within the
    floor and the ceiling.
    """
    settings = saved_settings(settings)
    engine.listen_at(lambda asked_db: side_level(asked_db, settings))
    return {side: engine.levels[side] for side in ("a", "b")}


def clear(engine, *, stop=None):
    """Play the desk at its own levels again: no offset, no mute."""
    return engine.listen_at(None)


# ---- the tool ----

class LevelsTool(Tool):
    TITLE = "Levels"

    def build(self):
        self.settings = saved_settings(self.studio.listening)
        self.readings = self.planned = None
        self.targets_measured = {}
        listen = section(self.body, "Listening level",
                         f"Both sides together, from {FLOOR_DB:+.0f} dB up to {CEILING_DB:+.0f} dB, the app's ceiling. "
                         "Matched keeps A and B equally loud, as the desk matches them, and moves both; Manual sets "
                         "the level outright. While an offset or a mute is on, the desk says so and offers the way "
                         "back.")
        row = tk.Frame(listen, bg=listen["bg"])
        row.pack(fill="x")
        button(row, "Quieter", lambda: self.command("quieter")).pack(side="left")
        self.level = tk.IntVar(master=self.frame)
        self.slider = tk.Scale(row, from_=FLOOR_DB, to=CEILING_DB, orient="horizontal", resolution=1, length=190,
                               showvalue=False, variable=self.level, bg=listen["bg"], fg=INK,
                               troughcolor=PAPER, highlightthickness=0, borderwidth=0)
        self.slider.pack(side="left", padx=6)
        self.slider.bind("<ButtonRelease-1>", lambda _: self.slid())
        button(row, "Louder", lambda: self.command("louder")).pack(side="left")
        self.reading = label(listen, size=10, bold=True)
        self.reading.pack(fill="x", pady=(6, 2))
        options = tk.Frame(listen, bg=listen["bg"])
        options.pack(fill="x")
        self.manual = button(options, "Manual", self.toggle_match, quiet=True)
        self.manual.pack(side="left")
        self.mute = button(options, "Mute", self.toggle_mute, quiet=True)
        self.mute.pack(side="left", padx=4)
        button(options, "Reset", lambda: self.command("normal"), quiet=True).pack(side="left")

        match = section(self.body, "Match a song's parts",
                        "Measure the same phrase through each part and scene. A part named Solo +2 dB stays "
                        "2 dB above the other parts. Apply checks the new levels before keeping them. "
                        + TRIM_NOTE + " A and B are matched by the desk "
                        "itself whenever it plays. " + DIGITAL_NOTE)
        row = tk.Frame(match, bg=match["bg"])
        row.pack(fill="x")
        caption(row, "Bring them to the").pack(side="left")
        self.anchor = tk.StringVar(master=self.frame, value="Quietest")
        Chips(row, self.anchor, ("Quietest", "First"), width=140).pack(side="left", padx=(4, 0))
        row = tk.Frame(match, bg=match["bg"])
        row.pack(fill="x", pady=(4, 0))
        button(row, "Measure", self.measure).pack(side="left")
        self.apply_button = button(row, "Apply", self.apply)
        self.apply_button.pack(side="left", padx=6)
        # What was measured of each part, once there is something measured.
        self.measured = room(match)
        self.rows = listbox(self.measured, height=6)
        self.rows.pack(fill="x", pady=(8, 0))
        self.sync()

    def shown(self):
        """On show again: with what the desk holds now (its own way back may have taken the offset off)."""
        super().shown()
        self.settings = saved_settings(self.studio.listening)
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
        """Play at these settings from now on (None: the desk's own levels). They stay on when the tool is left:
        `studio.listening` holds them, and the desk shows them with a way back."""
        self.settings = saved_settings(settings)
        self.studio.listening = None if at_rest(self.settings) else self.settings
        self.sync()
        if at_rest(self.settings):
            self.call("lab.tools.levels.clear", done=lambda levels: None, working="Back to the desk's own level…")
        else:
            self.call("lab.tools.levels.install", self.settings, done=lambda levels: None,
                      working="Setting the level…")

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
        """The sounded chain, stored sound and requested scene for each part."""
        song = self.studio.state.song
        if not song:
            return {}
        found = {}
        for part in self.studio.store.song(song)["parts"]:
            sound = self.studio.store.sound(part["sound"])
            found[part["name"]] = (heard_chain(sound), sound, part.get("scene"))
        return found

    def measure(self):
        if self.busy:
            return
        targets = self.targets()
        if len(targets) < 2:
            self.say("A song needs two parts or more: keep or save the sounds you asked for a song.")
            return
        taken = phrase(self.studio)
        if taken is None:
            self.say("Record or choose a phrase on the desk first.")
            return
        self.targets_measured = targets
        self.song_measured = self.studio.state.song
        self.phrase_measured = self.studio.phrase_key()
        self.planned = None

        def measured(readings):
            self.readings = readings
            reference = None if self.anchor.get() == "Quietest" else next(iter(readings))
            try:
                self.planned = plan(readings, reference=reference, offsets={name: part_offset(name) for name in readings})
            except ValueError as error:
                self.planned = None
                self.say(str(error))
            self.show()

        self.call("lab.tools.levels.measure_sounds", {name: chain for name, (chain, _, _) in targets.items()},
                  taken[1], {name: scene for name, (_, _, scene) in targets.items()},
                  done=measured, working="Playing the phrase silently through each part…", renders=True)

    def show(self):
        self.rows.delete(0, "end")
        rows = self.planned["rows"] if self.planned else [{"name": name, **found}
                                                         for name, found in (self.readings or {}).items()]
        self.measured.show(bool(rows))
        for row in rows:
            if row.get("issue") or row.get("lufs") is None:
                self.rows.insert("end", f"{row['name']}   {row.get('issue') or 'Too quiet for a loudness reading.'}")
                continue
            trim = row.get("trim_db")
            self.rows.insert("end", f"{row['name']}   {row['lufs']:.1f} LUFS, true peak {row['true_peak_db']:.1f}"
                                    f" dB" + (f"   →  {trim:+.1f} dB" if trim is not None else ""))
        sized(self.rows, 6)
        if self.planned and self.planned["lowered_for_headroom"]:
            self.say("Lowered further to keep every sound's peaks under the ceiling.")

    def still_current(self):
        def signature(targets):
            return {name: (chain, sound.id, scene) for name, (chain, sound, scene) in targets.items()}

        return (self.studio.state.song == self.song_measured and self.studio.phrase_key() == self.phrase_measured
                and signature(self.targets()) == signature(self.targets_measured))

    def apply(self):
        if self.busy:
            return
        if not self.planned or not self.still_current():
            self.say("Measure first.")
            return
        from lab.sound.sounds import Sound

        planned = {row["name"]: row for row in self.planned["rows"] if row.get("trim_db") is not None}
        ceiling = self.planned["true_peak_ceiling_dbtp"]
        song = self.studio.store.song(self.studio.state.song)
        limited, groups = [], {}
        for part in song["parts"]:
            if part["name"] not in planned or part["name"] not in self.targets_measured:
                continue
            trim = planned[part["name"]]["trim_db"]
            _, sound, scene = self.targets_measured[part["name"]]
            group = groups.setdefault((sound.id, scene is None), [sound, sound.chain, []])
            already = next((entry for entry in group[2] if entry.get("scene") == scene), None)
            if already is not None:
                if abs(trim - planned[already["name"]]["trim_db"]) > .05:
                    self.say("Two parts use the same sound and scene at different levels. Give one its own sound.")
                    return
                group[2].append(part)
                continue
            try:
                group[1], applied = with_trim(group[1], trim, scene=scene)
            except ValueError as error:
                limited.append(f"{part['name']}: {error}")
                continue
            if abs(applied - trim) > .05:
                limited.append(f"{part['name']} stopped at {applied:+.1f} dB")
            group[2].append(part)
        proposed = []
        for sound, chain, parts in groups.values():
            if not parts:
                continue
            leveled = Sound(sound.name, chain, {**sound.origin, "kind": "revised", "from": sound.id,
                                               "request": "level matched"},
                            parent=sound.id, knobs=dict(sound.knobs), explanation=sound.explanation)
            proposed.append((leveled, parts))
        by_part = {part["name"]: (heard_chain(sound), part.get("scene"))
                   for sound, parts in proposed for part in parts}
        if len(by_part) < 2:
            self.say("Two parts need a usable output trim. " + " ".join(limited))
            return

        def verified(readings):
            if not self.still_current():
                self.say("The song or phrase changed. Measure again.")
                return
            failed = [name for name, found in readings.items() if found["clipped"] or found["lufs"] is None
                      or found["true_peak_db"] > ceiling + .2
                      or abs(found["lufs"] - planned[name]["expected_lufs"]) > 1.]
            if failed:
                self.say("Level verification needs another pass for " + ", ".join(failed) + ".")
                return
            for sound, parts in proposed:
                self.studio.store.save(sound)
                for part in parts:
                    part["sound"] = sound.id
            self.studio.store.save_song(song)
            self.planned = None
            self.say("Levels verified. Save the matched sounds to the Quad Cortex."
                     + (" " + " ".join(limited) if limited else ""))

        self.call("lab.tools.levels.measure_sounds", {name: chain for name, (chain, _) in by_part.items()},
                  phrase(self.studio)[1], {name: scene for name, (_, scene) in by_part.items()}, done=verified,
                  working="Checking the new levels through the same scenes…", renders=True)


def open(desk):  # noqa: A001
    return LevelsTool.open(desk)
