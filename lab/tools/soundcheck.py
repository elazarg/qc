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

from dataclasses import replace
import tkinter as tk

import numpy as np
from scipy import signal

from lab.hearing.audio import RATE, db
from lab.hearing.measure import measure
from lab.learner import idle as guitar_idle
from lab.learner.dataset import suggested_setup
from lab.sound.chain import Block, INTERNAL, SCENES
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
UTILITY_GATE = 16002


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


def take_report(dry, wet=None, *, input_calibration=False):
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
    dry_peak = readings["dry"]["peak_db"]
    if input_calibration and -45. < dry_peak < -18. and readings["dry"]["lufs"] is not None:
        lift = min(6, round(-12. - dry_peak))
        checks.append(f"If this was your hardest playing, try +{lift} dB in Quad Cortex I/O Settings for this "
                      "input, then repeat with the louder guitar. This changes how hard presets drive their amps.")
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
    live = dry is None
    if live:
        dry = engine.record(seconds, stop=stop)[:, :1]
    dry = np.asarray(dry, dtype=np.float32)
    if len(dry) < RATE // 2:
        raise RuntimeError("Nothing was recorded.")
    wet = render(engine, chain, dry, scene=scene, tail=.5, stop=stop) if chain is not None else None
    return take_report(dry, wet, input_calibration=live)


def stage_render(engine, chain, dry, scene=None, *, stop=None):
    return render(engine, chain, dry, scene=scene, tail=.5, stop=stop)


def _idle_rms(samples):
    return db(float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))))


def _input_gate(chain, threshold, scene):
    """Insert before processing on a single input path. Preserve an existing gate and split layout."""
    fed = [r for r, row in enumerate(chain.rows) if row.inlet not in INTERNAL]
    if len(fed) != 1 or any("Gate" in block.name for _, _, block in chain.blocks):
        return None
    r = fed[0]
    row = chain.rows[r]
    empty = next((col for col, block in enumerate(row.cells) if block is None), None)
    if empty is None or (row.split and empty >= row.split[0]):
        return None
    bypass = tuple(index != scene for index in range(SCENES)) if chain.scenes_in_use() > 1 else False
    gate = (Block(UTILITY_GATE, bypass=bypass).set_display(0, threshold).set_display(1, 1.)
            .set_display(2, 100.).set_display(3, 250.).set_display(4, -18.))
    return chain.with_row(r, replace(row, cells=(gate, *row.cells[:empty], *row.cells[empty + 1:])))


def idle_check(engine, chain, knobs, idle_dry, phrase_dry, scene, *, stop=None):
    """Compare real muted strings with a zero input, then verify any gate against a soft replay."""
    from lab.tools.common import with_knobs

    idle = np.asarray(idle_dry, dtype=np.float32)
    if len(idle) < RATE:
        raise ValueError("The idle reference is too short. Record another one in Guitar Learner.")
    quiet = np.zeros((2 * RATE, 1), dtype=np.float32)
    playing = quiet[:0] if phrase_dry is None else np.asarray(phrase_dry, dtype=np.float32).reshape(-1, 1) * .25
    probe = np.concatenate((quiet, idle, playing))
    where = len(quiet) + len(idle)

    def audition(candidate):
        return render(engine, with_knobs(candidate, knobs), probe, scene=scene, tail=1.5, stop=stop)

    before = audition(chain)
    window = RATE // 2
    zero_db, idle_db = (_idle_rms(before[end - window:end]) for end in (len(quiet), where))
    dry_db = _idle_rms(idle)
    result = {"dry_db": dry_db, "idle_db": idle_db, "zero_db": zero_db, "chain": None}
    if idle_db <= zero_db + 6.:
        result["note"] = ("Noise remains with a silent input. Check the amp or effects and the monitoring path."
                          if zero_db > -85. else "The USB return was quiet. If you still hear noise, check monitoring.")
        return result
    if not len(playing):
        result["note"] = "Choose a guitar phrase to verify a gate."
        return result
    threshold = max(-90., min(-20., db(float(np.quantile(np.abs(idle), .999))) + 6.))
    candidate = _input_gate(chain, threshold, scene)
    if candidate is None:
        result["note"] = "Noise rises with the guitar input. Check its cable, pickup and any existing gate."
        return result
    after = audition(candidate)
    gain = idle_db - _idle_rms(after[where - window:where])
    frame = RATE // 10
    soft = [_idle_rms(before[start:start + frame]) for start in range(where, len(before) - frame + 1, frame)]
    checked = [i for i, level in enumerate(soft) if level > idle_db + 10.]
    if gain < 6. or not checked or any(abs(soft[i] - _idle_rms(after[where + i * frame:where + (i + 1) * frame])) > 2.
                                      for i in checked):
        result["note"] = "The trial gate did not cut the noise cleanly while keeping soft notes. Check the input first."
        return result
    result.update(chain=candidate, reduction_db=round(gain, 1))
    return result


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

        idle = disclosure(self.body, "Noise while idle",
                          note="Checks the preset with the selected Guitar Learner idle reference and digital silence. "
                               "A phrase is only needed to verify a gate.")
        row = tk.Frame(idle, bg=idle["bg"])
        row.pack(fill="x")
        button(row, "Check preset", self.check_idle, quiet=True).pack(side="left")
        button(row, "Guitar Learner", self.open_idle_reference, quiet=True).pack(side="left", padx=4)
        self.idle_text = said(idle, pady=(6, 0))
        self.keep_idle = button(idle, "Use quieter copy", self.use_idle, quiet=True)
        self.idle_candidate = None

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

    def check_idle(self):
        if self.busy:
            return
        studio = self.studio
        sound, taken = self.selected(), phrase(studio)
        if sound is None:
            self.say("Choose a sound first.")
            return
        signature = self.idle_signature()
        self.idle_candidate = None
        self.keep_idle.pack_forget()
        self.idle_text.configure(text="Finding the guitar's idle reference…")
        self.pending += 1

        def lookup():
            item = guitar_idle.for_sound(studio.folder, signature[0], sound)
            return item, guitar_idle.reference(studio.folder, item) if item else None

        def found(lookup_result):
            self.pending -= 1
            if signature != self.idle_signature():
                self.idle_text.configure(text="The selected sound or guitar changed. Check again.")
                return
            item, source = lookup_result
            label = suggested_setup(item, signature[0]) if item else signature[0]
            if source is None:
                self.idle_text.configure(text=f"{label}\nNo idle reference. Open Guitar Learner to record once.")
                return
            self.idle_text.configure(text=f"{label} · {source[1]}")
            self.call("lab.tools.soundcheck.idle_check", sound.chain, sound.knobs, source[0],
                      taken[1] if taken else None, studio.state.scene, done=lambda report: checked(report, label, source[1]),
                      renders=True, working="Measuring idle noise through the preset…")

        def checked(found, label, source):
            if signature != self.idle_signature():
                self.idle_text.configure(text="The selected sound or guitar changed. Check again.")
                return
            result = (f"{label} · {source}\nDry {found['dry_db']:.0f} dBFS RMS · Wet {found['idle_db']:.0f} · "
                      f"With silent input {found['zero_db']:.0f}")
            if found["chain"] is not None:
                self.idle_candidate = signature, found["chain"]
                result += f"\nGate reduced idle noise by {found['reduction_db']:.0f} dB. Soft replay verified."
                self.keep_idle.pack(anchor="w", pady=(4, 0))
            else:
                result += "\n" + found["note"]
            self.idle_text.configure(text=result)

        def failed(error):
            self.pending -= 1
            self.idle_text.configure(text=str(error))

        studio.later(studio.reading.submit(lookup), found, failed)

    def idle_signature(self):
        studio, sound = self.studio, self.selected()
        if sound is not None:
            return (studio.state.instrument, studio.state.side, studio.state.scene, sound.id, sound.knobs,
                    studio.phrase_key(), guitar_idle.selected(studio.folder, studio.state.instrument))
        return None

    def open_idle_reference(self):
        from lab.learner.screen import IDLE

        self.desk.learn(IDLE)

    def use_idle(self):
        from lab.sound.sounds import Sound

        studio, sound = self.studio, self.selected()
        if self.idle_candidate is None or sound is None:
            return
        signature, chain = self.idle_candidate
        if signature != self.idle_signature():
            self.say("Your guitar, sound, scene or phrase changed. Run the idle check again.")
            return
        copy = Sound(sound.name, chain, {**sound.origin, "kind": "revised", "from": sound.id,
                                        "request": "measured idle noise reduction"}, parent=sound.id,
                     knobs=dict(sound.knobs), explanation=sound.explanation, sources=list(sound.sources))
        studio.put(studio.state.side, copy)
        self.keep_idle.pack_forget()
        self.idle_candidate = None
        self.say("Quieter copy ready. Play it, or use Save to QC.")

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
