"""What guitarists mean by the words they use, and what players and engineers do about it.

This is the one table of the player's words. Each word carries its remedies in
the order the community tries them. The first one the loaded chain can perform
is applied, and the rest stay one step away as other ways to get there.
docs/TONE_KNOWLEDGE.md gives the reasoning and the sources.

A remedy is a set of Shape knob moves (`lab.sound.knobs.KNOBS`). `read` takes a short
remark such as "the low end gets muddy when I palm mute", "a bit tighter" or
"no fizz"; `usable` keeps the remedies a chain can perform. `direction` reads
what is left for a table to read: a plain request for more or less of
something a knob stands for and no word here covers ("more gain", "a bit less
reverb", "too much treble"). The two never read the same remark, so they
cannot disagree. Anything else goes to the assistant.
"""

from dataclasses import dataclass
import re


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
        r("a tighter low end: leaner lows in the drive and amp and a higher low cut, where mud is made", tightness=.3),
        r("less gain, which smears low notes together", drive=-.2),
        r("less bass in the amp", body=-.3)),
        when={"palm": (r("leaner lows in the drive and amp, so muted notes stop blurring", tightness=.35),
                       r("less gain, which is what smears muted notes", drive=-.2, tightness=.15)),
              "chords": (r("less gain, because distortion turns stacked notes into mud", drive=-.25),)},
        tips={"palm": "Under high gain the lasting fix is a boost in front that cuts lows before they distort."}),
    "boomy": Word(region="80–200 Hz", away=(
        r("less bass in the amp and its low-end resonance", body=-.3),
        r("leaner lows in the drive and amp", tightness=.3))),
    "flubby": Word(region="below 200 Hz, in time", away=(
        r("leaner lows ahead of and in the amp, so the low strings track", tightness=.35),
        r("less gain, which loosens the low strings", drive=-.2))),
    "thin": Word(region="100–500 Hz", away=(
        r("more body: low-end weight in the amp, a lower low cut, the microphones closer", body=.3),
        r("more midrange, where a guitar's size comes from", mids=.25),
        r("a little more gain for thickness", drive=.15)),
        toward=(r("less bass in the amp", body=-.3),), fault=True,
        when={"leads": (r("more midrange, which is what makes a single note sound big", mids=.3),
                        r("more sustain under the note", sustain=.3))},
        tips={"leads": "On the guitar, the neck pickup or series wiring thickens single notes at the source.",
              "": "Check the volume first: quiet monitoring makes any tone sound thin."}),
    "fat": Word(toward=(
        r("more bass and low-end weight in the amp", body=.3),
        r("more midrange", mids=.2),
        r("a slightly darker top", brightness=-.2)),
        away=(r("less bass in the amp", body=-.3), r("leaner lows in the drive and amp", tightness=.3))),
    "tight": Word(toward=(
        r("leaner lows in the drive and amp", tightness=.3),
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
        r("a rounder top: fewer high harmonics from the drive, the cab's low-pass lower", texture=-.3),
        r("a darker top across the amp and drive", brightness=-.25),
        r("less gain, which adds upper harmonics", drive=-.15)),
        toward=(r("a rougher top: more high harmonics from the drive, the cab's low-pass higher", texture=.3),),
        fault=True,
        when={"leads": (r("less presence and more midrange, so high notes sing instead of pierce",
                          presence=-.3, mids=.2),),
              "hard": (r("more even dynamics, so hard picking does not spike", compression=.3, presence=-.15),)},
        tips={"leads": "On the guitar, the neck pickup or the tone knob at 7 tames high notes without dulling the rest."}),
    "fizzy": Word(region="5–10 kHz", away=(
        r("a rounder top: the cab's low-pass lower and its microphones off the centre of the speaker, "
          "where fizz lives", texture=-.4),
        r("less gain, the usual source of fizz", drive=-.2),
        r("less presence", presence=-.3))),
    "bright": Word(toward=(
        r("a brighter top across the amp, drive and cab", brightness=.3),
        r("more presence", presence=.3)),
        away=(r("a darker tone across the amp, drive and cab microphones", brightness=-.3),
              r("less presence", presence=-.3),
              r("a rounder top: the cab's low-pass lower", texture=-.3))),
    "dark": Word(toward=(
        r("a darker top across the amp and drive", brightness=-.35),
        r("less presence", presence=-.3)),
        away=(r("a brighter tone across the amp, drive and cab microphones", brightness=.3),
              r("more presence", presence=.3)),
        tips={"": "Check the volume first: quiet monitoring makes any tone sound dull."}),
    # Warm is round against rough, not dark against bright (docs/TONE_RESEARCH.md, finding 1).
    "warm": Word(toward=(
        r("a rounder top: fewer high harmonics, less presence, the cab's low-pass lower", texture=-.3),
        r("a darker top across the amp and drive", brightness=-.2),
        r("more bass and low mids", body=.2, mids=.1)),
        away=(r("a rougher top: more high harmonics, more presence, the cab's low-pass higher", texture=.3),)),
    "glassy": Word(region="3–8 kHz", toward=(
        r("more presence and less gain, for a clean edge", presence=.3, drive=-.15),
        r("a brighter top across the amp and drive", brightness=.25)),
        away=(r("less presence", presence=-.3), r("a darker top", brightness=-.25))),
    "airy": Word(region="above 8 kHz", toward=(
        r("an open top and more presence", brightness=.4, presence=.2),
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
        r("more bloom and a rounder top", bloom=.3, texture=-.2),
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
    "narrow": Word(toward=(r("narrower effects", width=-.3),), away=(r("wider effects", width=.3),)),
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
    "scooped": "hollow", "harsh": "shrill piercing spiky brittle strident abrasive icepick rough",
    "fizzy": "fizz buzzy sizzly grainy raspy", "bright": "trebly", "dark": "dull muffled wooly woolly veiled",
    "warm": "round mellow", "glassy": "chimey sparkly jangly", "airy": "open", "punchy": "punch",
    "bite": "cut attack", "plinky": "clicky clanky", "stiff": "hard sterile lifeless",
    "spongy": "squishy", "compressed": "squashed choked", "dynamic": "responsive",
    "mushy": "smeared blurry undefined indistinct", "clear": "clarity definition defined articulate articulation",
    "smooth": "creamy liquid", "singing": "sing violin", "dirty": "gritty crunchy aggressive angry saturated distorted",
    "chunky": "chug chuggy", "washy": "swimmy distant cavernous drowned", "spacious": "ambient roomy lush dreamy",
    "noisy": "hissy hiss hum noise", "choppy": "gated", "buried": "lost",
}
COMPARATIVES = {
    "tighter": "tight", "looser": "loose", "fatter": "fat", "thicker": "fat", "fuller": "fat", "bigger": "fat",
    "heavier": "fat", "thinner": "thin", "brighter": "bright", "darker": "dark", "warmer": "warm",
    "rounder": "warm", "rougher": "harsh", "harsher": "harsh", "duller": "dark", "punchier": "punchy",
    "smoother": "smooth", "creamier": "smooth",
    "clearer": "clear", "dirtier": "dirty", "grittier": "dirty", "crunchier": "dirty", "cleaner": "clean",
    "drier": "dry", "dryer": "dry", "wetter": "wet", "roomier": "spacious", "wider": "wide", "narrower": "narrow", "glassier": "glassy",
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
# How much: said before a comparative ("a bit tighter", "much brighter") it scales the wish; said before a plain
# word ("a bit bright", "really dark") it says how far the sound is that way, and scales the cure.
AMOUNTS = {"a touch": .5, "a tad": .5, "a bit": .5, "a little bit": .5, "a little": .5, "slightly": .5,
           "just a bit": .5, "somewhat": .7, "a lot": 1.7, "much": 1.7, "way": 2., "far": 1.7, "really": 1.5,
           "very": 1.5, "so much": 1.7}
_AMOUNT = "|".join(sorted(map(re.escape, AMOUNTS), key=len, reverse=True))
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
    scale, degree = 1., False
    amount = re.match(rf"(?:just )?(?:{_AMOUNT}) ", rest)
    if amount:
        scale, degree = AMOUNTS.get(amount[0].strip().removeprefix("just "), 1.), True
        rest = rest[amount.end():]
    shape = (re.fullmatch(r"(?P<too>too|so|kind of|kinda|pretty|quite) (?P<word>\w+)", rest)
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
    elif quantity in ("less", "too much", "needs less") or groups.get("too") == "too":
        wanted = False
    elif asked:
        # "Make it bright" and "I want it really tight" ask for it, however much.
        wanted = bool(entry.toward) or not entry.away
    elif groups.get("too") or described or degree:
        # "It sounds bright", "really dark", "a bit bright" and "kind of boxy" say how the sound is: a fault
        # when the word can be one. (With a comparative, "a bit brighter", the same words scale a wish.)
        wanted = not entry.away
    else:
        # A bare "bright" asks for it. A bare "thin" or "muddy" complains.
        wanted = bool(entry.toward) and not entry.fault or not entry.away
    chosen = entry.toward if wanted else entry.away
    if not chosen:
        # The word has no remedies of its own this way round: the first remedy of the other way, turned over.
        opposite = {knob: -step for knob, step in (entry.away if wanted else entry.toward)[0].moves.items()}
        chosen = (Remedy(opposite, saying(opposite)),)
    first = () if wanted else (entry.when or {}).get(technique, ())
    taken = {frozenset(remedy.moves) for remedy in first}
    remedies = tuple(Remedy({knob: round(step * scale, 2) for knob, step in remedy.moves.items()}, remedy.say)
                     for remedy in (*first, *(remedy for remedy in chosen if frozenset(remedy.moves) not in taken)))
    tips = entry.tips or {}
    tip = tips.get(technique if first else "", "") if (not wanted or key == "sustain") else ""
    return Reading(key, technique if first else None, wanted, remedies, tip)


# What a turn of each knob is called, down and up, for a remedy made by turning another over: it says what the
# moves do, whatever the remedy it was made from said.
TURNS = {
    "jazz": ("less of the jazz direction", "a rounder, cleaner direction with more mids"),
    "drive": ("less gain", "more gain"), "brightness": ("a darker top", "a brighter top"),
    "texture": ("a rounder top", "a rougher top"), "space": ("less delay and reverb", "more delay and reverb"),
    "tightness": ("fuller lows", "leaner lows"), "attack": ("a faster compressor attack", "a slower compressor attack"),
    "sustain": ("less sustain", "more sustain"), "gate": ("a lower gate threshold", "a higher gate threshold"),
    "body": ("less body", "more body"), "mids": ("less midrange", "more midrange"),
    "presence": ("less presence", "more presence"), "delay": ("less delay", "more delay"),
    "delay_time": ("shorter echoes", "longer echoes"), "reverb": ("less reverb", "more reverb"),
    "modulation": ("less movement", "more movement"), "width": ("narrower effects", "wider effects"),
    "compression": ("less compression", "more even dynamics"), "punch": ("less pick bite", "more pick bite"),
    "tail": ("shorter tails", "longer tails"), "motion": ("slower movement", "faster movement"),
    "bloom": ("less bloom", "more bloom"),
    "band_fit": ("less of the midrange lift that cuts through a band", "a midrange lift with leaner lows and less ambience"),
}


def saying(moves):
    """Knob moves in the words of `TURNS`: "less bloom and a rougher top"."""
    return " and ".join(TURNS[knob][step > 0] for knob, step in moves.items())


def usable(reading, available):
    """The remedies the loaded chain can perform, in order."""
    if available is None:
        return reading.remedies
    return tuple(remedy for remedy in reading.remedies if all(knob in available for knob in remedy.moves))


# ---- More or less of what a knob stands for ----

# What a player may ask for more or less of that no word above covers: a knob, or (knob, -1) for one whose "more"
# turns the knob down ("more warmth" is rounder). A word `read` reads ("fizz", "bite", "sustain") is not here.
QUANTITIES = {
    "gain": "drive", "drive": "drive", "distortion": "drive", "dirt": "drive", "saturation": "drive",
    "crunch": "drive", "reverb": "reverb", "delay": "delay", "echo": "delay", "echoes": "delay", "repeats": "tail",
    "roughness": "texture", "texture": "texture", "brightness": "brightness",
    "smoothness": ("texture", -1), "warmth": ("texture", -1), "movement": "modulation",
    "mids": "mids", "midrange": "mids", "body": "body", "bass": "body", "low end": "body",
    "lows": "body", "presence": "presence",
    "treble": "presence", "highs": "presence", "top end": "presence", "space": "space", "room": "space",
    "ambience": "space", "compression": "compression", "modulation": "modulation", "tremolo": "modulation",
    "chorus": "modulation", "width": "width", "jazz": "jazz", "tightness": "tightness",
    "gate": "gate",
}
STEPS = {"jazz": .4, "delay": .3}
_QUANTITY = "|".join(sorted(map(re.escape, QUANTITIES), key=len, reverse=True))
_LEAD = (r"(?:(?:can you |could you |please )?(?:make (?:it|this|that|the tone|the sound) "
         r"|it(?:'s| is| sounds| feels| has) |sounds |needs |i need |i want |give (?:me|it) )?)")


def direction(text):
    """A plain request for more or less of one quantity, as a bounded knob move, or None.

    Only whole phrases are read: "more gain", "a bit less reverb", "way too much treble", "not enough mids".
    A remark `read` reads is never read here.
    """
    words = " ".join(re.findall(r"[a-z']+", text.lower().replace("’", "'"))).replace("moar ", "more ")
    words = re.sub(r"\s*(?:please|thanks|thank you)$", "", words)
    found = re.fullmatch(rf"{_LEAD}(?:({_AMOUNT}) )?(more|less|too much|not enough|too little) ({_QUANTITY})", words)
    if not found or read(text) is not None:
        return None
    found_knob = QUANTITIES[found[3]]
    key, sign = found_knob if isinstance(found_knob, tuple) else (found_knob, 1)
    step = STEPS.get(key, .3) * AMOUNTS.get(found[1], 1.) * sign
    return {key: round(step if found[2] in ("more", "not enough", "too little") else -step, 2)}
