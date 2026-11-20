"""Setup and sound check: input and output levels, and a stage EQ check, before playing out.

Carried over from the old Sound check card (`soundcheck_ui`) and its stage preview (`stage_check`):

- **Input and output levels.** Play your hardest; the dry input is recorded as it reaches the unit, then
  played silently through the selected sound. Dry peak and headroom, samples near full scale, the wet
  loudness and true peak, and how much a stereo sound loses folded to mono. The desk's phrase can be
  checked the same way without playing again.
- **The Quad Cortex.** Firmware, whether the USB round trip is measured, the Tone Lab setlist and the cache.
- **Stage EQ.** The selected sound, played silently, as it stands and with two small output EQ moves for
  the place it will be heard (a full-range speaker or PA, a guitar cabinet, a band mix): a low cut and a
  gentler top. The three are level-matched and played blind on the computer, as listening tests. Nothing is
  written to the sound or the unit; a move that wins belongs in the output or Global EQ, as the notes say.

The relative levels of sounds, which this card also planned on the old desk, are in the Levels tool.
"""

from datetime import datetime, timezone
import json
import random
import tkinter as tk
from uuid import uuid4

import numpy as np
from scipy import signal

from lab.hearing.audio import RATE, db
from lab.hearing.measure import match, measure as measure_audio
from lab.tools.common import (Window, button, gained, heard_chain, label, menu_choice, phrase, play, render, section,
                     write_pcm16)
from lab.tools.levels import measure

CONTEXTS = {
    "frfr": {"name": "Full-range speaker or PA", "high_pass_hz": 65, "shelf_hz": 4200, "shelf_db": -2.0},
    "cab": {"name": "Physical guitar cabinet", "high_pass_hz": 50, "shelf_hz": 4000, "shelf_db": -1.0},
    "mix": {"name": "Band mix", "high_pass_hz": 80, "shelf_hz": 4800, "shelf_db": -1.5},
}
CHOICES = {"Full-range / PA": "frfr", "Guitar cabinet": "cab", "Backing mix": "mix"}
SOURCES = [
    {"title": "Quad Cortex manual", "url": "https://neuraldsp.com/manual/quad-cortex"},
    {"title": "Yamaha PA beginner's guide",
     "url": "https://usa.yamaha.com/audio/portable-pa/explore/pa-beginners-guide/setting/band-pa.html"},
]
NOTES = [
    "Play the comparison at rehearsal level alongside the rest of the band. These small EQ moves are listening tests.",
    "Compare the low cut and the top trim separately. Keep enough bass for your tuning and enough presence to "
    "hear the guitar in the mix.",
    "Use post-chain EQ to adjust a tone that already plays well. Use pre-drive EQ when low notes drive the amp too "
    "hard, then recheck the tone and the output level.",
    "Keep output or room correction apart from the preset. Quad Cortex Global EQ can be assigned to particular "
    "output pairs, and the unit may bypass it on heavy presets.",
]
HEADROOM_DB = -6.      # a dry peak above this leaves little room for a harder attack


# ---- the stage EQ variants (from stage_check) ----

def high_shelf(hz, gain_db, q=.707):
    """A biquad high shelf (the Audio EQ Cookbook's), as (b, a)."""
    a_gain = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * hz / RATE
    alpha = np.sin(w0) / (2 * q)
    cos = np.cos(w0)
    root = 2 * np.sqrt(a_gain) * alpha
    b = np.array([a_gain * ((a_gain + 1) + (a_gain - 1) * cos + root), -2 * a_gain * ((a_gain - 1) + (a_gain + 1) * cos),
                  a_gain * ((a_gain + 1) + (a_gain - 1) * cos - root)])
    a = np.array([(a_gain + 1) - (a_gain - 1) * cos + root, 2 * ((a_gain - 1) - (a_gain + 1) * cos),
                  (a_gain + 1) - (a_gain - 1) * cos - root])
    return b / a[0], a / a[0]


def _trim_peak(samples):
    peak = float(np.max(np.abs(samples)))
    gain = min(0., -1 - db(peak))
    return (samples * 10 ** (gain / 20)).astype(np.float32), round(gain, 3)


def stage_variants(wet, context="frfr"):
    """The wet sound as it is, with a low cut, and with a gentler top: [(record, audio)]."""
    if context not in CONTEXTS:
        raise ValueError("Choose frfr, cab or mix.")
    wet = np.asarray(wet, dtype=np.float32)
    if wet.ndim == 1:
        wet = wet[:, None]
    found = measure(wet)
    if found["lufs"] is None or found["near_rail_samples"]:
        raise ValueError("Start with an audible, unclipped wet sound.")
    spec = CONTEXTS[context]
    cut = signal.butter(2, spec["high_pass_hz"], fs=RATE, btype="highpass", output="sos")
    low, low_trim = _trim_peak(signal.sosfilt(cut, wet, axis=0))
    b, a = high_shelf(spec["shelf_hz"], spec["shelf_db"])
    high, high_trim = _trim_peak(signal.lfilter(b, a, wet, axis=0))
    variants = [
        ({"name": "As it is", "post_eq": [], "post_eq_trim_db": 0.}, wet),
        ({"name": "Low-end cleanup", "post_eq": [{"type": "high_pass", "frequency_hz": spec["high_pass_hz"],
                                                   "slope_db_per_octave": 12}], "post_eq_trim_db": low_trim}, low),
        ({"name": "Gentler top", "post_eq": [{"type": "high_shelf", "frequency_hz": spec["shelf_hz"],
                                              "gain_db": spec["shelf_db"]}], "post_eq_trim_db": high_trim}, high),
    ]
    if any(measure(audio)["lufs"] is None for _, audio in variants):
        raise ValueError("The filtered versions are too quiet. Use a phrase with guitar across all of it.")
    return variants


def level_matched(variants, *, ceiling_db=-3.):
    """Each variant's audio trimmed to one loudness, with the trims noted on its record."""
    measured = [measure_audio(audio) for _, audio in variants]
    trims = match(measured, ceiling_db=ceiling_db)
    return [({**record, "audition_trim_db": round(trim, 2), "metrics": {"lufs": found.lufs, "rms_dbfs": found.rms_db}},
             gained(audio, trim)) for (record, audio), found, trim in zip(variants, measured, trims)]


def blind(variants, seed=None):
    """Variants under the letters A, B, C… in a shuffled order: {letter: (record, audio)}."""
    order = list(range(len(variants)))
    random.Random(seed).shuffle(order)
    return {"ABCDEFGH"[place]: variants[index] for place, index in enumerate(order)}


def keep_preview(folder, sound_name, context, auditions):
    """Write a stage preview's level-matched audio and what each letter was, under the data folder."""
    stamp = datetime.now(timezone.utc)
    target = folder / "soundcheck" / f"{stamp:%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
    target.mkdir(parents=True)
    record = {"created": stamp.isoformat(timespec="seconds"), "sound": sound_name, "context": context,
              "context_spec": CONTEXTS[context], "notes": NOTES, "sources": SOURCES, "auditions": {}}
    for letter, (info, audio) in auditions.items():
        write_pcm16(target / f"audition-{letter}.wav", audio)
        record["auditions"][letter] = {**info, "file": f"audition-{letter}.wav"}
    (target / "preview.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return target


# ---- the take check (from level_plan.take_report) ----

def take_report(dry, wet=None):
    """Levels of a dry take and, if there is one, of its return through a sound."""
    readings = {"dry": measure(dry)}
    if wet is not None:
        readings["wet"] = measure(wet)
    checks = [f"The {kind} signal reached or approached full scale. Lower this gain stage and check again."
              for kind, found in readings.items() if found["near_rail_samples"]]
    checks += [f"The {kind} signal is too quiet for a loudness reading. Check the routing."
               for kind, found in readings.items() if found["lufs"] is None]
    if readings["dry"]["sample_peak_dbfs"] > HEADROOM_DB and not readings["dry"]["near_rail_samples"]:
        checks.append(f"The dry peak is within {-HEADROOM_DB:.0f} dB of full scale. A harder attack may clip "
                      "the input; lower the input gain on the unit.")
    return {**readings, "checks": checks, "headroom_db": round(-readings["dry"]["sample_peak_dbfs"], 1)}


# ---- operations, run where the unit is held ----

def check(engine, chain=None, dry=None, seconds=6., *, stop=None):
    """Record the dry input for `seconds` (or take `dry`), and play it silently through `chain`, if one."""
    if dry is None:
        dry = engine.record(seconds, stop=stop)[:, :1]
    dry = np.asarray(dry, dtype=np.float32)
    if len(dry) < RATE // 2:
        raise RuntimeError("Nothing was recorded.")
    wet = render(engine, chain, dry, tail=0., stop=stop) if chain is not None else None
    return take_report(dry, wet)


def unit_check(engine, *, stop=None):
    """What the app can say about the unit without changing anything."""
    from lab.qc.slots import CACHE_SLOTS

    found = {"firmware": engine.unit.firmware, "calibrated": engine.offset is not None,
             "cached": len(engine.slots.index()), "cache_slots": len(CACHE_SLOTS)}
    try:
        engine.slots.refresh()
        found["tone_lab"] = True
    except RuntimeError as error:
        found["tone_lab"], found["problem"] = False, str(error)
    position = engine.unit.position()
    found["selected"] = {"position": position, "ours": engine.slots.ours(position)}
    return found


def stage_render(engine, chain, dry, *, stop=None):
    return render(engine, chain, dry, tail=.5, stop=stop)


# ---- the window ----

class SoundCheckWindow(Window):
    TITLE = "Setup and sound check"
    SIZE = "620x660"

    def build(self):
        self.auditions = {}
        take = section(self.body, "Input and output levels",
                       "Play your hardest after pressing Check. The dry input is measured as it reaches the unit, "
                       "then played silently through the selected sound.")
        row = tk.Frame(take, bg=take["bg"])
        row.pack(fill="x")
        button(row, "Check: play now", lambda: self.check(record=True)).pack(side="left")
        button(row, "Check the phrase", lambda: self.check(record=False), quiet=True).pack(side="left", padx=6)
        self.take_text = label(take, size=9, wrap=540)
        self.take_text.pack(fill="x", pady=(6, 0))

        unit = section(self.body, "The Quad Cortex")
        button(unit, "Check the Quad Cortex", self.unit_check, quiet=True).pack(anchor="w")
        self.unit_text = label(unit, size=9, wrap=540)
        self.unit_text.pack(fill="x", pady=(6, 0))

        stage = section(self.body, "Stage EQ", "The selected sound as it is and with two small EQ moves, "
                                               "level-matched and in a blind order. Judge them at rehearsal level.")
        row = tk.Frame(stage, bg=stage["bg"])
        row.pack(fill="x")
        self.context = tk.StringVar(master=self.top, value=next(iter(CHOICES)))
        menu_choice(row, self.context, tuple(CHOICES)).pack(side="left")
        button(row, "Make the three", self.stage).pack(side="left", padx=6)
        self.players = tk.Frame(stage, bg=stage["bg"])
        self.players.pack(fill="x", pady=(6, 0))
        self.stage_text = label(stage, size=9, wrap=540)
        self.stage_text.pack(fill="x", pady=(6, 0))

    def selected(self):
        return self.studio.sound(self.studio.state.side)

    def check(self, *, record):
        sound = self.selected()
        chain = heard_chain(sound)
        dry = None
        if not record:
            taken = phrase(self.studio)
            if taken is None:
                self.say("Record or choose a phrase on the desk first.")
                return
            dry = taken[1]
        self.call("lab.tools.soundcheck.check", chain, dry, 6., done=self.show_take,
                  working="Recording 6 s: play your hardest now…" if record else "Measuring the phrase…")

    def show_take(self, report):
        dry = report["dry"]
        lines = [f"Dry peak {dry['sample_peak_dbfs']:.1f} dBFS, {report['headroom_db']:.1f} dB of headroom, "
                 f"{dry['near_rail_samples']} samples at full scale"]
        wet = report.get("wet")
        if wet:
            loud = f"{wet['lufs']:.1f} LUFS" if wet["lufs"] is not None else "too quiet to read"
            mono = wet.get("mono_fold_down_db")
            lines.append(f"Through the sound: {loud}, {wet['true_peak_dbtp_estimate']:.1f} dBTP estimated"
                         + (f", mono fold-down {mono:+.1f} dB" if mono is not None else ""))
        lines += report["checks"]
        self.take_text.configure(text="\n".join(lines))

    def unit_check(self):
        def shown(found):
            lines = [f"CorOS {found['firmware']} · round trip {'measured' if found['calibrated'] else 'not measured'}"
                     f" · {found['cached']} of {found['cache_slots']} cache slots in use",
                     "The Tone Lab setlist is there." if found["tone_lab"] else found.get("problem", "")]
            self.unit_text.configure(text="\n".join(lines))

        self.call("lab.tools.soundcheck.unit_check", done=shown, working="Asking the Quad Cortex…")

    def stage(self):
        sound = self.selected()
        taken = phrase(self.studio)
        if sound is None or taken is None:
            self.say("Put a sound on the desk and choose a phrase first.")
            return
        context = CHOICES[self.context.get()]

        def rendered(wet):
            try:
                self.auditions = blind(level_matched(stage_variants(wet, context)))
            except ValueError as error:
                self.say(str(error))
                return
            keep_preview(self.folder, sound.name, context, self.auditions)
            for child in self.players.winfo_children():
                child.destroy()
            for letter in self.auditions:
                button(self.players, f"Play {letter}", lambda letter=letter: self.play(letter)).pack(side="left",
                                                                                                 padx=(0, 4))
            button(self.players, "Which was which?", self.reveal, quiet=True).pack(side="right")
            self.stage_text.configure(text=f"{CONTEXTS[context]['name']} · {sound.name}: as it is and two small "
                                           "EQ changes, in a blind order.")

        self.call("lab.tools.soundcheck.stage_render", heard_chain(sound), taken[1], done=rendered,
                  working="Playing the phrase silently through the sound…")

    def play(self, letter):
        try:
            play(self.auditions[letter][1], self.folder / "previews")
            self.say(f"Playing {letter}")
        except (RuntimeError, ImportError) as error:
            self.say(str(error))

    def reveal(self):
        self.stage_text.configure(text="  ·  ".join(f"{letter}: {info['name']}"
                                                    for letter, (info, _) in self.auditions.items())
                                  + "\n" + NOTES[0])


def open(desk):  # noqa: A001
    return SoundCheckWindow.open(desk)
