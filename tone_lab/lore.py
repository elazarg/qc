"""What guitarists mean by the words they use, and what players and engineers do about it.

Each word carries its remedies in the order the community tries them. The first
one the loaded chain can perform is applied, and the rest stay one step away as
other ways to get there. docs/TONE_KNOWLEDGE.md gives the reasoning and the sources.
"""

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re

from . import models, styles


@dataclass(frozen=True)
class Remedy:
    moves: dict
    say: str


@dataclass(frozen=True)
class Word:
    """`away` cures the word as a complaint. `toward` gets more of it as a goal."""
    away: tuple = ()
    toward: tuple = ()
    region: str = ""
    when: dict = None  # technique -> remedies that come first for that way of playing
    fault: bool = False  # said on its own, the word is a complaint even though it can also be asked for
    tips: dict = None  # technique (or "") -> what to check on the guitar or in the room, which no knob here does


def r(say, **moves):
    return Remedy(moves, say)


LEXICON = {
    # Low end
    "muddy": Word(region="150–400 Hz", away=(
        r("leaner lows in the drive and amp, where mud is made", tightness=.3),
        r("a higher low cut at the cab", low_cut=.4),
        r("less gain, which smears low notes together", drive=-.2),
        r("less bass in the amp", body=-.3)),
        when={"palm": (r("leaner lows in the drive and amp, so muted notes stop blurring", tightness=.35),
                       r("less gain, which is what smears muted notes", drive=-.2, tightness=.15)),
              "chords": (r("less gain, because distortion turns stacked notes into mud", drive=-.25),)},
        tips={"palm": "Under high gain the lasting fix is a boost in front that cuts lows before they distort."}),
    "boomy": Word(region="80–200 Hz", away=(
        r("less bass in the amp and its low-end resonance", body=-.3),
        r("a higher low cut at the cab", low_cut=.45),
        r("leaner lows in the drive and amp", tightness=.3))),
    "flubby": Word(region="below 200 Hz, in time", away=(
        r("leaner lows ahead of and in the amp, so the low strings track", tightness=.35),
        r("less gain, which loosens the low strings", drive=-.2),
        r("a higher low cut at the cab", low_cut=.35))),
    "thin": Word(region="100–500 Hz", away=(
        r("more bass and low-end weight in the amp", body=.3),
        r("more midrange, where a guitar's size comes from", mids=.25),
        r("a lower low cut at the cab", low_cut=-.4),
        r("a little more gain for thickness", drive=.15)),
        toward=(r("less bass in the amp", body=-.3),), fault=True,
        when={"leads": (r("more midrange, which is what makes a single note sound big", mids=.3),
                        r("more sustain under the note", sustain=.3))},
        tips={"leads": "On the guitar, the neck pickup or series wiring thickens single notes at the source.",
              "": "Check the listening level first: quiet monitoring makes any tone sound thin."}),
    "fat": Word(toward=(
        r("more bass and low-end weight in the amp", body=.3),
        r("more midrange", mids=.2),
        r("a slightly darker top", warmth=.2)),
        away=(r("less bass in the amp", body=-.3), r("leaner lows in the drive and amp", tightness=.3))),
    "tight": Word(toward=(
        r("leaner lows in the drive and amp", tightness=.3),
        r("a higher low cut at the cab", low_cut=.35),
        r("less gain, for a firmer attack", drive=-.15)),
        away=(r("fuller lows in the drive and amp", tightness=-.3),)),
    "loose": Word(away=(
        r("leaner lows ahead of and in the amp", tightness=.35),
        r("less gain", drive=-.2)),
        toward=(r("fuller lows in the drive and amp", tightness=-.3),), fault=True),
    # Mids
    "boxy": Word(region="300–600 Hz", away=(
        r("less of the amp's midrange", mids=-.25),
        r("less bass in the amp", body=-.2))),
    "honky": Word(region="700 Hz–1.2 kHz", away=(
        r("less of the amp's midrange", mids=-.3),
        r("a touch more presence to balance it", presence=.2))),
    "scooped": Word(region="400 Hz–1 kHz", away=(
        r("more midrange", mids=.3),
        r("a midrange lift that also trims the lows and ambience", band_fit=.5)),
        toward=(r("less midrange", mids=-.3),), fault=True),
    # Top end
    "harsh": Word(region="2–5 kHz", away=(
        r("less presence, which is where harshness sits", presence=-.3),
        r("a softer top across the amp and drive", warmth=.25),
        r("a lower low-pass at the cab", soft_top=.3),
        r("less gain, which adds upper harmonics", drive=-.15)),
        when={"leads": (r("less presence and more midrange, so high notes sing instead of pierce",
                          presence=-.3, mids=.2),),
              "hard": (r("more even dynamics, so hard picking does not spike", compression=.3, presence=-.15),)},
        tips={"leads": "On the guitar, the neck pickup or the tone knob at 7 tames high notes without dulling the rest."}),
    "fizzy": Word(region="5–10 kHz", away=(
        r("a lower low-pass at the cab, where fizz lives", soft_top=.4),
        r("the cab microphones moved off the centre of the speaker, where fizz is strongest", mic=.4),
        r("less gain, the usual source of fizz", drive=-.2),
        r("less presence", presence=-.3))),
    "bright": Word(toward=(
        r("a brighter top across the amp and drive", warmth=-.3),
        r("more presence", presence=.3),
        r("a higher low-pass at the cab", soft_top=-.5),
        r("the cab microphones moved toward the centre of the speaker", mic=-.4)),
        away=(r("a softer top across the amp and drive", warmth=.3),
              r("less presence", presence=-.3),
              r("a lower low-pass at the cab", soft_top=.3),
              r("the cab microphones moved toward the speaker's edge", mic=.4))),
    "dark": Word(toward=(
        r("a softer top across the amp and drive", warmth=.35),
        r("less presence", presence=-.3),
        r("the cab microphones moved toward the speaker's edge", mic=.4)),
        away=(r("a brighter top across the amp and drive", warmth=-.3),
              r("more presence", presence=.3),
              r("a higher low-pass at the cab", soft_top=-.5),
              r("the cab microphones moved toward the centre of the speaker", mic=-.4)),
        tips={"": "Check the listening level first: quiet monitoring makes any tone sound dull."}),
    "warm": Word(toward=(
        r("a softer top across the amp and drive", warmth=.3),
        r("more bass and low mids", body=.2, mids=.1)),
        away=(r("a brighter top across the amp and drive", warmth=-.3),)),
    "glassy": Word(region="3–8 kHz", toward=(
        r("more presence and less gain, for a clean edge", presence=.3, drive=-.15),
        r("a brighter top across the amp and drive", warmth=-.25)),
        away=(r("less presence", presence=-.3), r("a softer top", warmth=.25))),
    "airy": Word(region="above 8 kHz", toward=(
        r("a higher low-pass at the cab and more presence", soft_top=-.5, presence=.2),
        r("a little more room", space=.2))),
    # Pick and dynamics
    "punchy": Word(toward=(
        r("more pick bite in the upper mids", punch=.3),
        r("a slower compressor attack, so the pick gets through", attack=.3),
        r("leaner lows, which firm up the front of the note", tightness=.2)),
        away=(r("less pick bite", punch=-.3),)),
    "bite": Word(region="2–4 kHz", toward=(
        r("more pick bite in the upper mids", punch=.3),
        r("more presence", presence=.25)),
        away=(r("less pick bite", punch=-.3), r("less presence", presence=-.25))),
    "plinky": Word(region="pick transient", away=(
        r("less pick bite", punch=-.3),
        r("a faster compressor attack, which rounds the pick off", attack=-.3),
        r("more sustain behind the pick", sustain=.25))),
    "stiff": Word(away=(
        r("more bloom: softer attack, a longer note", bloom=.35),
        r("a little more gain, which adds give", drive=.15),
        r("more even dynamics", compression=.25))),
    "spongy": Word(away=(
        r("less compression", compression=-.3),
        r("less gain", drive=-.2),
        r("leaner lows in the drive and amp", tightness=.25))),
    "compressed": Word(away=(
        r("less compression", compression=-.3),
        r("less gain, which compresses on its own", drive=-.2)),
        toward=(r("more even dynamics", compression=.3),), fault=True),
    "dynamic": Word(toward=(
        r("less gain, so your picking sets the dirt", drive=-.2),
        r("less compression", compression=-.3))),
    "mushy": Word(away=(
        r("less gain, the first cure for lost note definition", drive=-.25),
        r("leaner lows in the drive and amp", tightness=.25),
        r("more midrange for note separation", mids=.2),
        r("less ambience", space=-.3))),
    "clear": Word(toward=(
        r("less gain, the first cure for lost note definition", drive=-.2),
        r("more midrange for note separation", mids=.2),
        r("leaner lows in the drive and amp", tightness=.2),
        r("less ambience", space=-.25))),
    # Gain and sustain
    "smooth": Word(toward=(
        r("more bloom and a softer top", bloom=.3, warmth=.2),
        r("less presence", presence=-.25),
        r("more sustain", sustain=.25))),
    "singing": Word(toward=(
        r("more sustain and midrange under the note", sustain=.3, mids=.2),
        r("more bloom", bloom=.3),
        r("a little more gain", drive=.15))),
    "sustain": Word(toward=(
        r("more sustain from compression and gain", sustain=.3),
        r("a little more gain", drive=.2),
        r("more even dynamics", compression=.25)),
        away=(r("less sustain", sustain=-.3),),
        tips={"": "Headphones feed nothing back to the strings, so sustain has to come from compression and gain."}),
    "dirty": Word(toward=(r("more gain", drive=.25),), away=(r("less gain", drive=-.3),)),
    "clean": Word(toward=(r("less gain", drive=-.3),), away=(r("more gain", drive=.3),)),
    "chunky": Word(toward=(
        r("leaner lows with a little more gain, for a firm chug", tightness=.25, drive=.15),
        r("more low-end weight in the amp", body=.2))),
    # Space
    "washy": Word(away=(
        r("less delay and reverb", space=-.35),
        r("shorter tails", tail=-.3))),
    "dry": Word(toward=(r("less delay and reverb", space=-.3),),
                away=(r("more delay and reverb", space=.3), r("more reverb", reverb=.3))),
    "wet": Word(toward=(r("more delay and reverb", space=.3),), away=(r("less delay and reverb", space=-.3),)),
    "spacious": Word(toward=(
        r("more delay and reverb", space=.3),
        r("longer tails", tail=.3),
        r("wider effects", width=.3))),
    "wide": Word(toward=(r("wider effects", width=.3),), away=(r("narrower effects", width=-.3),)),
    # Noise
    "noisy": Word(away=(
        r("a higher gate threshold", gate=.3),
        r("less gain, where the noise is amplified", drive=-.2))),
    "choppy": Word(away=(r("a lower gate threshold, so notes ring out", gate=-.3),)),
    # In a band
    "buried": Word(region="800 Hz–3 kHz", away=(
        r("a midrange lift with leaner lows and less ambience, which is what cuts through a band", band_fit=.5),
        r("more midrange", mids=.3),
        r("more pick bite", punch=.25)),
        tips={"": "Judge it against a backing track: a tone that fills the room alone often vanishes in a band."}),
    "jazzy": Word(toward=(r("a rounder, cleaner direction with more mids", jazz=.4),)),
}

SYNONYMS = {
    "muddy": "mud murky muddled", "boomy": "boom woofy tubby bassy bloated", "flubby": "flub farty flabby floppy",
    "thin": "weak small anemic tinny weedy", "fat": "thick full big huge beefy meaty heavy massive",
    "tight": "focused", "loose": "saggy", "boxy": "cardboard congested", "honky": "honk nasal",
    "scooped": "hollow", "harsh": "shrill piercing spiky brittle strident abrasive icepick",
    "fizzy": "fizz buzzy sizzly grainy raspy", "bright": "trebly", "dark": "dull muffled wooly woolly veiled",
    "warm": "round mellow", "glassy": "chimey sparkly jangly", "airy": "open", "punchy": "punch",
    "bite": "cut attack", "plinky": "clicky clanky", "stiff": "hard sterile lifeless",
    "spongy": "squishy", "compressed": "squashed choked", "dynamic": "responsive",
    "mushy": "smeared blurry undefined indistinct", "clear": "clarity definition defined articulate articulation",
    "smooth": "creamy liquid", "singing": "sing violin", "dirty": "gritty crunchy aggressive angry saturated",
    "chunky": "chug chuggy", "washy": "swimmy distant cavernous drowned", "spacious": "ambient roomy lush dreamy",
    "noisy": "hissy hiss hum noise", "choppy": "gated", "buried": "lost",
}
COMPARATIVES = {
    "tighter": "tight", "looser": "loose", "fatter": "fat", "thicker": "fat", "fuller": "fat", "bigger": "fat",
    "heavier": "fat", "thinner": "thin", "brighter": "bright", "darker": "dark", "warmer": "warm",
    "rounder": "warm", "duller": "dark", "punchier": "punchy", "smoother": "smooth", "creamier": "smooth",
    "clearer": "clear", "dirtier": "dirty", "grittier": "dirty", "crunchier": "dirty", "cleaner": "clean",
    "drier": "dry", "dryer": "dry", "wetter": "wet", "roomier": "spacious", "wider": "wide", "glassier": "glassy",
    "chunkier": "chunky", "jazzier": "jazzy", "airier": "airy", "stiffer": "stiff",
}
ALIASES = {**{word: word for word in LEXICON}, **COMPARATIVES,
           **{alias: word for word, aliases in SYNONYMS.items() for alias in aliases.split()}}
PHRASES = {"ice pick": "icepick", "ice picky": "icepick", "lost in the mix": "lost", "in the mix": "",
           "low end": "lows", "bottom end": "lows", "top end": "highs", "high end": "highs",
           "note definition": "definition", "cut through": "cut", "cuts through": "cut",
           "pick attack": "attack", "does not": "doesnt", "doesn't": "doesnt", "do not": "dont", "don't": "dont",
           "it's": "it is", "that's": "that is", "they're": "they are", "i'm": "i am",
           r"(?:the |my )?notes? (?:die|dies|fade|fades|decay|decays)(?: out| away)?(?: too)?(?: fast| quickly| soon| early)?":
               "no sustain"}
GOAL_NAMES = {}
for _comparative, _word in COMPARATIVES.items():
    GOAL_NAMES.setdefault(_word, _comparative)

TECHNIQUES = {
    "palm": r"(?:palm mut\w*|chug\w*|muted (?:notes|riffs?))",
    "chords": r"(?:(?:big |open |full |power |bar+e )?chords?|strum\w*)",
    "leads": r"(?:leads?|solos?|single notes?|(?:the )?high (?:strings|notes|frets)|up high|high up|"
             r"above the (?:12th|twelfth) fret|bends?)",
    "hard": r"(?:dig in|digging in|pick(?:ing)? hard|hit(?:ting)? (?:it |the strings )?hard|play(?:ing)? hard)",
}
TECHNIQUE_NAMES = {"palm": "on palm mutes", "chords": "on chords", "leads": "on single notes",
                   "hard": "when you dig in"}
_AMOUNT = "|".join(sorted(map(re.escape, styles.AMOUNTS), key=len, reverse=True))
_SUBJECT = (r"(?:(?:the |my |this |that )?(?:tone|sound|guitar|amp|lows|bass|bottom|highs|treble|top|mids|midrange|"
            r"notes|strings|it|this|that|they|everything|low strings|high strings))")
_STATES = r"(?:is|are|gets|get|got|sounds|sound|feels|feel|becomes|become|goes|go|seems|seem|'s)"
_WANTS = (r"(?:(?:can you |could you |please )?(?:make (?:it|this|that|the tone|the sound|them) |"
          r"i want (?:it |this |that |the tone )?(?:to be |to sound )?|i need (?:it |this )?(?:to be )?|"
          r"(?:it )?needs (?:to be |to sound )?|(?:it )?should (?:be|sound) |give (?:me|it) |let it ))")
_LINKS = r"(?:when(?:ever)? i(?:'m| am)? |when |if i |as i |while i(?:'m| am)? |on (?:the |my )?|during (?:the |my )?|with (?:the |my )?|for (?:the |my )?|in (?:the |my )?)"


@dataclass(frozen=True)
class Reading:
    word: str
    technique: str
    wanted: bool          # True when the player asks for more of the word
    remedies: tuple
    tip: str = ""

    @property
    def said(self):
        subject = GOAL_NAMES.get(self.word, f"more {self.word}") if self.wanted else self.word
        return (f"{subject} {TECHNIQUE_NAMES[self.technique]}" if self.technique else subject).capitalize()


def _technique(words):
    for name, pattern in TECHNIQUES.items():
        match = re.search(rf"(?:^|\s)(?:{_LINKS})?(?:my |the )?{pattern}(?=\s|$)", words)
        if match:
            return name, " ".join((words[:match.start()] + " " + words[match.end():]).split())
    return None, words


def read(text):
    """Read a short remark about the sound as a word, a direction and, if given, a way of playing.

    Only whole remarks are read, such as "the low end gets muddy when I palm mute",
    "a bit fizzy", "more bite" or "not tight enough". Anything longer goes to the assistant.
    """
    words = " ".join(re.findall(r"[a-z0-9']+", text.lower().replace("’", "'")))
    for phrase, short in PHRASES.items():
        words = re.sub(rf"\b{phrase}\b", short, words)
    words = re.sub(r"\s*(?:please|thanks|thank you)$", "", " ".join(words.split()))
    technique, words = _technique(words)
    lead = re.match(rf"(?:(?P<want>{_WANTS})|(?:{_SUBJECT} )?(?:(?P<state>{_STATES}) )?)", words)
    rest = words[lead.end():].strip()
    asked, described = bool(lead["want"]), bool(lead[0]) and not lead["want"]
    scale = 1.
    amount = re.match(rf"(?:just )?(?:{_AMOUNT}) ", rest)
    if amount:
        scale = styles.AMOUNTS.get(amount[0].strip().removeprefix("just "), 1.)
        rest = rest[amount.end():]
    shape = (re.fullmatch(r"(?P<too>too|way too|far too|really|very|so|kind of|kinda|pretty|quite) (?P<word>\w+)", rest)
             or re.fullmatch(r"(?P<more>more|less|no|not enough|too much|too little|lacks|lacking|needs more|needs less) "
                             r"(?P<word>\w+)", rest)
             or re.fullmatch(r"not (?P<word>\w+) enough", rest)
             or re.fullmatch(r"(?:doesnt|dont|wont|cant) (?P<neg>\w+)", rest)
             or re.fullmatch(r"(?P<word>\w+)", rest))
    if not shape:
        return None
    groups = shape.groupdict()
    token = groups.get("word") or groups.get("neg")
    key = ALIASES.get(token)
    if not key:
        return None
    entry = LEXICON[key]
    comparative = token in COMPARATIVES
    quantity = groups.get("more")
    if quantity == "no":
        # "No bite" says a quality is missing. "No fizz" asks to be rid of a fault.
        wanted = bool(entry.toward)
    elif groups.get("neg") or shape.re.pattern.startswith("not ") or quantity in (
            "more", "no", "not enough", "too little", "lacks", "lacking", "needs more") or comparative:
        wanted = True
    elif quantity in ("less", "too much", "needs less") or groups.get("too") in ("too", "way too", "far too"):
        wanted = False
    elif groups.get("too") or described:
        # "It sounds bright" and "really dark" describe a fault when the word can be one.
        wanted = not entry.away
    else:
        # "Make it bright" and a bare "bright" ask for it. A bare "thin" or "muddy" complains.
        wanted = bool(entry.toward) and (asked or not entry.fault) or not entry.away
    chosen = entry.toward if wanted else entry.away
    if not chosen:
        opposite = (entry.away if wanted else entry.toward)[0]
        chosen = (Remedy({knob: -step for knob, step in opposite.moves.items()}, _inverse(opposite.say)),)
    first = () if wanted else (entry.when or {}).get(technique, ())
    taken = {frozenset(remedy.moves) for remedy in first}
    remedies = tuple(Remedy({knob: round(step * scale, 2) for knob, step in remedy.moves.items()}, remedy.say)
                     for remedy in (*first, *(remedy for remedy in chosen if frozenset(remedy.moves) not in taken)))
    tips = entry.tips or {}
    tip = tips.get(technique if first else "", "") if (not wanted or key == "sustain") else ""
    return Reading(key, technique if first else None, wanted, remedies, tip)


def _inverse(say):
    swaps = (("less ", "more "), ("leaner ", "fuller "), ("softer ", "brighter "), ("lower ", "higher "),
             ("shorter ", "longer "), ("narrower ", "wider "))
    for first, second in swaps:
        if say.startswith(first):
            return second + say[len(first):]
        if say.startswith(second):
            return first + say[len(second):]
    return "the opposite of " + say


def usable(reading, available):
    """The remedies the loaded chain can perform, in order."""
    if available is None:
        return reading.remedies
    return tuple(remedy for remedy in reading.remedies if all(knob in available for knob in remedy.moves))


# ---- What is known about each model -------------------------------------------------------------

KNOWLEDGE = Path(__file__).with_name("knowledge") / "gear.json"
SECTIONS = {"amps": "amp", "drives": "drive", "cabs": "cab", "effects": "effect"}
# Words in a maker's or model's name that are too common to tie a request to it.
COMMON = frozenset("""the and for with from that this tone sound like amp amps guitar pedal style type based clean lead
rhythm crunch gain high low mid mids drive channel classic modern vintage british american rock early late more less
normal bright boost master volume custom series brown blue red green black white power tube tubes watt head combo
john johns may
cab speaker heavy tight warm big full hard soft into over open edge back front all one two three very much any some
bit little touch way too not enough make something special standard original version model models hot""".split())


@lru_cache(maxsize=1)
def _knowledge():
    try:
        data = json.loads(KNOWLEDGE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    index = {}
    for key, family in data.get("amps", {}).items():
        for model_id in family["models"]:
            index[model_id] = ("amp", {**family, "family": key, "note": family["model_notes"].get(str(model_id), "")})
    for section in ("drives", "cabs", "effects"):
        for model_id, entry in data.get(section, {}).items():
            index[int(model_id)] = (SECTIONS[section], entry)
    return data, index


def gear(model_id):
    """What the community knows about one model, or None."""
    found = _knowledge()[1].get(model_id)
    return {"kind": found[0], **found[1]} if found else None


def about(model_id):
    """A few plain sentences about a model: what it is, how it sounds, what to watch for."""
    entry = gear(model_id)
    if not entry:
        return ""
    if entry["kind"] == "amp":
        parts = [entry["real"], entry["voice"], entry["note"], entry["watch_out"] and "Watch out: " + entry["watch_out"]]
    elif entry["kind"] == "drive":
        parts = [entry["real"], entry["signature"], entry["watch_out"] and "Watch out: " + entry["watch_out"]]
    elif entry["kind"] == "cab":
        build = " ".join(part for part in (entry["config"], entry["back"] not in ("", "unknown")
                                           and f"{entry['back']}-back", entry["speaker"]) if part)
        parts = [", ".join(part for part in (entry["real"], build) if part), entry["character"]]
    else:
        parts = [entry["real"], entry["character"]]
    return " ".join(part.rstrip(".") + "." for part in parts if part)


def about_asset(name):
    """What a named impulse response is, when its pack is known."""
    for prefix, pack in _knowledge()[0].get("irs", {}).items():
        if name.startswith(prefix):
            rest = name[len(prefix):].strip()
            if rest in pack["mixes"]:
                return f"{pack['about']} {rest} blends {pack['mixes'][rest]}."
            mic = re.split(r"[- ]", rest)[0] if rest else ""
            detail = f" Microphone: {pack['mics'][mic]}." if mic in pack["mics"] else ""
            return pack["about"] + detail
    return ""


def control_note(model_id, parameter):
    entry = gear(model_id) or {}
    return (entry.get("controls", {}).get(parameter) or {}).get("does", "")


@lru_cache(maxsize=1)
def _keywords():
    """What ties a request to a model: words of its name and maker, and whole names of artists and genres."""
    words, phrases = {}, {}
    for model_id, (kind, entry) in _knowledge()[1].items():
        if kind not in ("amp", "drive"):
            continue
        name = " ".join([re.split(r"[(,;]", entry.get("real", ""))[0], models.lookup(model_id)["name"],
                         entry.get("plugin", "")])
        for word in set(re.findall(r"[a-z0-9]+", name.lower())):
            if word not in COMMON and (len(word) >= 3 and not word.isdigit() or len(word) >= 4):
                words.setdefault(word, set()).add(model_id)
        named = [match for item in entry.get("known_for", [])
                 for match in re.findall(r"[A-Z][\w'’.&/-]*(?: (?:of |the |and )?[A-Z0-9][\w'’.&/-]*)*", item)]
        for phrase in (*named, *entry.get("tags", [])):
            phrase = " ".join(re.findall(r"[a-z0-9]+", phrase.lower()))
            if len(phrase) >= 4 and phrase not in COMMON:
                phrases.setdefault(phrase, set()).add(model_id)
    return words, phrases


def candidates(text, *, limit=6):
    """Models a request points at by name, artist or genre, most specific first."""
    scores = {}
    words, phrases = _keywords()
    said = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    hits = [words[word] for word in set(said.split()) if word in words]
    hits += [matched for phrase, matched in phrases.items() if re.search(rf"\b{re.escape(phrase)}\b", said)]
    for matched in hits:
        for model_id in matched:
            # A name shared by few models says more than one shared by many.
            scores[model_id] = scores.get(model_id, 0) + 1 / len(matched)
    ranked = sorted(scores, key=lambda model_id: (-scores[model_id], model_id))
    chosen = {"amp": [], "drive": []}
    for model_id in ranked:
        kind = _knowledge()[1][model_id][0]
        if len(chosen[kind]) < limit:
            chosen[kind].append(model_id)
    extra = []
    for model_id in chosen["amp"][:3]:
        family = gear(model_id)
        extra += [boost["model"] for boost in family["boosts"][:2]] + [cab["model"] for cab in family["cabs"][:2]]
    return list(dict.fromkeys([*chosen["amp"], *chosen["drive"], *extra]))


RECIPE_FILLER = frozenset("""a an the of for with into to and or ish type tone sound i ii 1 high gain tight clean lead rhythm
warm love god wall just up it edge modern dirty mellow touch sensitive breaking box""".split())


@lru_cache(maxsize=1)
def _recipe_words():
    rows = []
    for recipe in _knowledge()[0].get("recipes", []):
        target = set(re.findall(r"[a-z0-9]+", recipe["target"].lower())) - RECIPE_FILLER
        aliases = set(re.findall(r"[a-z0-9]+", " ".join(recipe["aliases"]).lower())) - RECIPE_FILLER - target
        rows.append((recipe, target, aliases))
    counts = {}
    for _, target, aliases in rows:
        for word in target | aliases:
            counts[word] = counts.get(word, 0) + 1
    return rows, counts


def recipes(text, *, limit=1):
    """The community's usual chain for the sound a request names, as building blocks."""
    said = set(re.findall(r"[a-z0-9]+", text.lower()))
    rows, counts = _recipe_words()
    scored = []
    for recipe, target, aliases in rows:
        # A word in the recipe's own name counts double, and a word many recipes share counts less.
        score = sum(2 / counts[word] for word in said & target) + sum(1 / counts[word] for word in said & aliases)
        if score >= .5:
            scored.append((score, recipe))
    scored.sort(key=lambda item: -item[0])
    return [recipe for _, recipe in scored[:limit]]


def guitar(name):
    """What is known about the player's guitar: pickups, switch positions and what each suits."""
    lowered = (name or "").lower()
    for key, notes in _knowledge()[0].get("guitars", {}).items():
        if key in lowered or key == "les paul" and "lp" in lowered.split():
            return notes
    return None


def brief(model_ids, text=""):
    """What the assistant should know before answering: the loaded gear and how players treat the remark."""
    blocks = []
    for model_id in model_ids:
        entry = gear(model_id)
        if not entry or entry["kind"] == "effect":
            continue
        note = {"model": models.lookup(model_id)["name"], "is": entry.get("real", ""),
                "sound": entry.get("voice") or entry.get("signature") or entry.get("character", "")}
        controls = {name: item["does"] for name, item in entry.get("controls", {}).items()}
        if controls:
            note["controls"] = controls
        if entry.get("watch_out"):
            note["watch_out"] = entry["watch_out"]
        blocks.append(note)
    said = []
    words = set(re.findall(r"[a-z']+", text.lower()))
    for alias in sorted(words & set(ALIASES)):
        entry = LEXICON[ALIASES[alias]]
        remedies = entry.away or entry.toward
        said.append({"word": alias, "where": entry.region,
                     "players_try_in_order": [remedy.say for remedy in remedies]})
    return {key: value for key, value in (("loaded_gear", blocks), ("remarks", said)) if value}
