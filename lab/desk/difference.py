"""What two measurements of the phrase differ by, A's and B's, in plain words.

Each line names what was measured (`lab.hearing.measure`, `lab.hearing.probe`). A quantity is compared only
when both records have it as a number: records of every age are read, and a value that could not be measured
(intermodulation or wobble behind an echo, a decay too short to time) is simply not compared.

Where two quantities stand for one quality, the one the words are taken from is preferred when both records
have it: brightness from `brightness_db` (the energy above 1 kHz against 200 Hz to 1 kHz), and only otherwise
from `brightness_oct`, which is called what it is, the spectral centre; the tail from the click of the test
signals (`probe.late_energy_db`), and only otherwise from what followed the phrase (`tail_db`).
"""

import math

# The three qualities a round aims for, as the designer and listening both name them (`lab.sound.design.AIM`,
# `lab.hearing.measure.WORDS`), and what they are called where a short name is wanted.
QUALITIES = (("distortion", "gain"), ("brightness", "bright"), ("tail", "tail"))


def misses(aim, heard):
    """The qualities (their short names) where what was heard is not the word the round aimed for. Only words
    both have count: a quality either leaves out is not a miss."""
    found = []
    for key, short in QUALITIES:
        wanted, got = (aim or {}).get(key), (heard or {}).get(key)
        if isinstance(wanted, str) and isinstance(got, str) and wanted and got and wanted != got:
            found.append(short)
    return found


def aim_against(aim, heard, unheard=None):
    """One line for a design of a round: what the round aims for, and what listening took the design to be, with
    the qualities that miss named. The words of listening are an inference from what it measured, and are called
    what was heard, not what was measured."""
    from lab.hearing.measure import summary

    line = f"aim {summary(aim)}"
    if unheard:
        return f"{line} · could not be listened to"
    if not summary(heard):
        return f"{line} · not listened to yet"
    missed = misses(aim, heard)
    return f"{line} · heard as {summary(heard)}" + (f" · misses on {', '.join(missed)}" if missed else "")


def against(sound, other, other_letter):
    """What `sound` has that `other` has not, in a few words, for its tile: blocks added or taken out, blocks
    whose settings differ, and Shape knobs that stand elsewhere. "" when they are the same, or there is no other.

    Said of the chain as it is written and of the knobs as they stand, not of what was heard: that is the
    Difference panel's."""
    from collections import Counter

    from lab.sound import knobs

    if sound is None or other is None or sound.id == other.id:
        return ""
    parts = []
    mine = Counter(block.name for _, _, block in sound.chain.blocks)
    theirs = Counter(block.name for _, _, block in other.chain.blocks)
    parts += [f"+ {name}" for name in (mine - theirs)]
    parts += [f"no {name}" for name in (theirs - mine)]
    placed = {(r, column): block for r, column, block in other.chain.blocks}
    changed = [block.name for r, column, block in sound.chain.blocks
               if (r, column) in placed and placed[(r, column)].model == block.model and placed[(r, column)] != block]
    if changed:
        parts.append(f"{', '.join(dict.fromkeys(changed))} set differently")
    for key, (title, _, _) in knobs.KNOBS.items():
        step = sound.knobs.get(key, 0.) - other.knobs.get(key, 0.)
        if abs(step) >= .01:
            parts.append(f"{title} {step:+.2f}")
    if not parts:
        return ""
    shown = parts[:3] + ([f"{len(parts) - 3} more"] if len(parts) > 3 else [])
    return f"against {other_letter}: " + ", ".join(shown)


def number(record, path):
    """The value at a dotted path of a record ("probe.late_energy_db") when it is a finite number, else None."""
    value = record
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def gap(ha, hb, path):
    """B's value less A's at a path, when both are numbers; else None."""
    a, b = number(ha, path), number(hb, path)
    return None if a is None or b is None else b - a


def _comes_back(compression):
    """How much of a 12 dB drop in the playing comes back, in words. Below zero the chain widens the drop."""
    back = f"{12 * (1 - compression):.0f} dB"
    if compression <= -1:
        back += " or more"
    return back + (" (it widens the drop)" if compression < 0 else "")


def difference(ha, hb):
    """The lines that say how B differs from A, from the measurement of each as it plays now."""
    lines = []
    level = gap(ha, hb, "level_db")
    if level is not None and abs(level) >= 1:
        lines.append(f"B is {abs(level):.0f} dB {'louder' if level > 0 else 'quieter'}.")
    harmonics = gap(ha, hb, "probe.distortion_db")
    if harmonics is not None and abs(harmonics) >= 3:
        lines.append(f"Distortion: A {ha.get('distortion')} ({number(ha, 'probe.distortion_db'):.0f} dB of harmonics), "
                     f"B {hb.get('distortion')} ({number(hb, 'probe.distortion_db'):.0f} dB).")
    together = gap(ha, hb, "probe.intermod_db")
    if together is not None and abs(together) >= 3:
        lines.append(f"Two notes together: B is {abs(together):.0f} dB {'rougher' if together > 0 else 'cleaner'} "
                     "(intermodulation).")
    lean = gap(ha, hb, "probe.even_odd_db")
    if lean is not None and abs(lean) >= 6:
        lines.append(f"B's harmonics lean {abs(lean):.0f} dB more to the {'even' if lean > 0 else 'odd'} ones.")
    squeeze = gap(ha, hb, "compression")
    if squeeze is not None and abs(squeeze) > .1:
        lines.append(f"B is {'more' if squeeze > 0 else 'less'} compressed: of a 12 dB drop in your playing, "
                     f"A gives back {_comes_back(number(ha, 'compression'))} and B "
                     f"{_comes_back(number(hb, 'compression'))}.")
    bright = gap(ha, hb, "brightness_db")
    if bright is not None:
        if abs(bright) >= 1:
            lines.append(f"B is {abs(bright):.0f} dB {'brighter' if bright > 0 else 'darker'} (the energy above 1 kHz "
                         "against 200 Hz to 1 kHz).")
    else:
        centre = gap(ha, hb, "brightness_oct")
        if centre is not None and abs(centre) >= .1:
            lines.append(f"B's spectral centre sits {abs(centre):.1f} octave {'brighter' if centre > 0 else 'darker'} "
                         f"({'higher' if centre > 0 else 'lower'}) than A's.")
    low = gap(ha, hb, "low_end_db")
    if low is not None and abs(low) >= 1.5:
        lines.append(f"B has {abs(low):.0f} dB {'more' if low > 0 else 'less'} low end.")
    late = gap(ha, hb, "probe.late_energy_db")
    if late is not None:
        if abs(late) >= 3:
            lines.append(f"After a click, B rings on {abs(late):.0f} dB {'more' if late > 0 else 'less'} (echo and "
                         "reverb).")
    else:
        tail = gap(ha, hb, "tail_db")
        if tail is not None and abs(tail) >= 3:
            lines.append(f"B rings on {abs(tail):.0f} dB {'more' if tail > 0 else 'less'} after you stop.")
    decay = gap(ha, hb, "probe.decay_rt60_s")
    if decay is not None and abs(decay) >= .3:
        lines.append(f"What rings takes {number(hb, 'probe.decay_rt60_s'):.1f} s to fall 60 dB on B, "
                     f"{number(ha, 'probe.decay_rt60_s'):.1f} s on A.")
    wobble = gap(ha, hb, "probe.level_wobble")
    if wobble is not None and abs(wobble) >= 3:
        lines.append(f"A held note's level swings {number(hb, 'probe.level_wobble'):.0f}% on B, "
                     f"{number(ha, 'probe.level_wobble'):.0f}% on A.")
    return lines or ["Listening heard no clear difference between A and B."]
