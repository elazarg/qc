"""Setup and sound check: input and output levels, and a stage EQ check, before playing out.

Carried over from the old Sound check card (`soundcheck_ui`) and its stage preview (`stage_check`):

- **Input and output levels.** Play your hardest; the dry input is recorded as it reaches the unit, then
  played silently through the selected sound. Dry peak and headroom, samples near full scale, the wet
  loudness and true peak, and how much a stereo sound loses folded to mono. The desk's phrase can be
  checked the same way without playing again.
- **The Quad Cortex.** Firmware, whether the USB round trip is measured, the Tone Lab setlist and the cache.
- **Stage EQ.** The selected sound, played silently, as it stands and with two small output EQ moves for
  the place it will be heard (a full-range speaker or PA, a guitar cabinet, a band mix): a low cut and a
  gentler top. The three are level-matched and played blind on the computer, as listening tests: a blind
  comparison like any other (`workbench.comparison`), kept with the others under `comparisons/`. Nothing is
  written to the sound or the unit; a move that wins belongs in the output or Global EQ, as the notes say.

The relative levels of a song's parts, which this card also planned on the old desk, are in the Levels tool.
Every level here is measured the one way the app measures (`hearing.measure`).
"""

import tkinter as tk

import numpy as np
from scipy import signal

from lab.hearing.audio import RATE, db
from lab.hearing.measure import measure
from lab.tools.common import (Chips, Tool, button, caption, disclosure, flow, heard_chain, phrase, play, render,
                              said, section)
from lab.tools.workbench import auditions, comparison

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
    if found.lufs is None or found.clipped:
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
    if any(measure(audio).lufs is None for _, audio in variants):
        raise ValueError("The filtered versions are too quiet. Use a phrase with guitar across all of it.")
    return variants


def stage_comparison(folder, wet, context, *, sound=None, phrase_name=None, seed=None):
    """The three stage versions of a wet sound as a blind comparison, kept with the others: (folder, record)."""
    variants = stage_variants(wet, context)
    return comparison(folder, [record for record, _ in variants], [audio for _, audio in variants], sound=sound,
                      phrase_name=phrase_name, kind="Stage EQ", seed=seed,
                      about={"context": context, "context_spec": CONTEXTS[context], "notes": NOTES,
                             "sources": SOURCES})


# ---- the take check (from level_plan.take_report) ----

def mono_fold_down_db(samples):
    """How much a stereo sound loses when its two channels are added to one, in dB; None for mono or silence."""
    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim != 2 or samples.shape[1] != 2:
        return None
    stereo = float(np.sqrt(np.mean(np.square(samples))))
    mono = float(np.sqrt(np.mean(np.square(samples.mean(axis=1)))))
    return db(mono / stereo) if stereo > 1e-10 else None


def take_report(dry, wet=None):
    """Levels of a dry take and, if there is one, of its return through a sound: `hearing.measure` records."""
    readings = {"dry": measure(dry).to_record()}
    if wet is not None:
        readings["wet"] = {**measure(wet).to_record(), "mono_fold_down_db": mono_fold_down_db(wet)}
    checks = [f"The {kind} signal reached full scale. Lower this gain stage and check again."
              for kind, found in readings.items() if found["clipped"]]
    checks += [f"The {kind} signal is too quiet for a loudness reading. Check the routing."
               for kind, found in readings.items() if found["lufs"] is None]
    if readings["dry"]["peak_db"] > HEADROOM_DB and not readings["dry"]["clipped"]:
        cut = round(readings["dry"]["peak_db"] + 12.)
        checks.append(f"Try reducing the Quad Cortex input gain by about {cut} dB, then play your hardest again. "
                      "That leaves about 12 dB of headroom on this take.")
    if wet is not None and readings["wet"]["true_peak_db"] > -3.:
        checks.append("The USB return is within 3 dB of full scale. Lower clean output level after the drive and "
                      "check it again.")
    mono_loss = readings.get("wet", {}).get("mono_fold_down_db")
    if mono_loss is not None and mono_loss < -6.:
        checks.append("The stereo sound loses more than 6 dB folded to mono. Check phase and stereo effects "
                      "before sending it to a mono PA.")
    return {**readings, "checks": checks, "headroom_db": round(-readings["dry"]["peak_db"], 1)}


# ---- operations, run where the unit is held ----

def check(engine, chain=None, dry=None, seconds=6., scene=None, *, stop=None):
    """Record the dry input for `seconds` (or take `dry`), and play it silently through `chain`, if one."""
    if dry is None:
        dry = engine.record(seconds, stop=stop)[:, :1]
    dry = np.asarray(dry, dtype=np.float32)
    if len(dry) < RATE // 2:
        raise RuntimeError("Nothing was recorded.")
    wet = render(engine, chain, dry, scene=scene, tail=.5, stop=stop) if chain is not None else None
    return take_report(dry, wet)


def stage_render(engine, chain, dry, scene=None, *, stop=None):
    return render(engine, chain, dry, scene=scene, tail=.5, stop=stop)


# ---- the tool ----

class SoundCheckTool(Tool):
    TITLE = "Setup and sound check"

    def build(self):
        self.auditions, self.record = {}, None
        take = section(self.body, "Input and output levels",
                       "Check: play now records six seconds: play your hardest. The dry input is measured as it "
                       "reaches the unit, then played silently through the selected sound. Check the phrase does "
                       "the same with the desk's phrase.")
        row = tk.Frame(take, bg=take["bg"])
        row.pack(fill="x")
        button(row, "Check: play now", lambda: self.check(record=True)).pack(side="left")
        button(row, "Check the phrase", lambda: self.check(record=False), quiet=True).pack(side="left", padx=6)
        self.take_text = said(take, pady=(6, 0))

        # The two rarer checks are shown on request.
        unit = disclosure(self.body, "The Quad Cortex",
                          note="Its firmware, whether the USB round trip was measured, and how much of the Tone Lab "
                               "setlist's cache is in use.")
        button(unit, "Check the Quad Cortex", self.unit_check, quiet=True).pack(anchor="w")
        self.unit_text = said(unit, pady=(6, 0))

        stage = disclosure(self.body, "Stage EQ",
                           note="The selected sound as it is and with two small EQ moves for what it will be heard "
                                "through, level-matched and in a blind order. " + NOTES[0])
        caption(stage, "Heard through").pack(fill="x")
        self.context = tk.StringVar(master=self.frame, value=next(iter(CHOICES)))
        Chips(stage, self.context, tuple(CHOICES)).pack(fill="x", pady=(2, 4))
        button(stage, "Make the three", self.stage).pack(anchor="w")
        self.players = tk.Frame(stage, bg=stage["bg"])
        self.players.pack(fill="x", pady=(6, 0))
        self.stage_text = said(stage, pady=(6, 0))
        self.unit_part, self.stage_part = unit, stage

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
        self.call("lab.tools.soundcheck.check", chain, dry, 6., self.studio.state.scene,
                  done=self.show_take, renders=True,
                  working="Recording 6 s: play your hardest now…" if record else "Measuring the phrase…")

    def show_take(self, report):
        dry = report["dry"]
        lines = [f"Dry peak {dry['peak_db']:.1f} dBFS, {report['headroom_db']:.1f} dB of headroom, "
                 f"{dry['clipped']} samples at full scale"]
        wet = report.get("wet")
        if wet:
            loud = f"{wet['lufs']:.1f} LUFS" if wet["lufs"] is not None else "too quiet to read"
            mono = wet.get("mono_fold_down_db")
            lines.append(f"Through the sound: {loud}, true peak {wet['true_peak_db']:.1f} dB"
                         + (f", mono fold-down {mono:+.1f} dB" if mono is not None else ""))
        lines += report["checks"]
        self.take_text.configure(text="\n".join(lines))

    def unit_check(self):
        def shown(found):
            lines = [f"CorOS {found['firmware']} · round trip {'measured' if found['calibrated'] else 'not measured'}"
                     f" · {found['cached']} of {found['cache_slots']} cache slots in use",
                     "The Tone Lab setlist is there." if found["tone_lab"] else (found["problem"] or "")]
            self.unit_text.configure(text="\n".join(lines))

        self.call("lab.tools.probe.unit_state", done=shown, working="Asking the Quad Cortex…")

    def stage(self):
        sound = self.selected()
        taken = phrase(self.studio)
        if sound is None or taken is None:
            self.say("Put a sound on the desk and choose a phrase first.")
            return
        context = CHOICES[self.context.get()]

        def rendered(wet):
            try:
                target, self.record = stage_comparison(self.folder, wet, context, sound=sound, phrase_name=taken[0])
            except ValueError as error:
                self.say(str(error))
                return
            self.auditions = auditions(target, self.record)
            for child in self.players.winfo_children():
                child.destroy()
            flow(self.players, [button(self.players, f"Play {letter}", lambda letter=letter: self.play(letter))
                                for letter in self.auditions]
                 + [button(self.players, "Which was which?", self.reveal, quiet=True)])
            self.stage_text.configure(text=f"{CONTEXTS[context]['name']} · {sound.name}")

        self.call("lab.tools.soundcheck.stage_render", heard_chain(sound), taken[1], self.studio.state.scene,
                  done=rendered,
                  working="Playing the phrase silently through the sound…", renders=True)

    def play(self, letter):
        try:
            play(self.auditions[letter], self.folder / "previews")
            self.say(f"Playing {letter}")
        except (RuntimeError, ImportError) as error:
            self.say(str(error))

    def reveal(self):
        self.stage_text.configure(text="  ·  ".join(f"{letter}: {row['name']}"
                                                    for letter, row in self.record["auditions"].items()))


def open(desk):  # noqa: A001
    return SoundCheckTool.open(desk)
