"""POD Go presets as starting points for the Quad Cortex.

A POD Go backup (`.pgb`) starts with "AF6L" and holds zlib-compressed JSON
folders, one per setlist ("User", "Factory"), each with 128 presets of four
per bank (https://github.com/john-baxter-dev/fretwire/blob/master/docs/pod-go.md;
a Line 6 forum thread describes the same layout without staff confirmation).
Every preset stores its blocks in order, with Line 6's model id, every value,
and four snapshots of which blocks are on and of the values assigned to them.

A preset becomes a Quad Cortex chain through the real gear, block by block:

1. the real amp or pedal Line 6 says the model is based on
   (`research/podgo-models.json`, from Line 6's published model lists);
2. the Quad Cortex model based on the same gear, found in the app's gear table
   (`knowledge.gear`) and, for the bass amps and cabs that table leaves out,
   Neural DSP's device list (`QC_SUPPLEMENT` below);
3. the values whose controls correspond: an amp's or pedal's knobs (0 to 1 on
   the POD Go, 0 to 10 on the Quad Cortex), delay time, feedback and mix,
   reverb mix and pre-delay, gate thresholds, graphic EQ bands, cab cuts.

Each block's match is one of:

- "same": the Quad Cortex model is based on the same gear;
- "family": the same maker and model line, another version or channel;
- "kind": no Quad Cortex model of that gear, or a Line 6 original; a block of
  the same kind stands in (a tape delay for a tape delay, a plate for a plate,
  an overdrive for an overdrive; for a cab, the stock cab the research pairs
  with the preset's amp);
- "omitted": a block with no sound of its own here (volume pedal, FX loop, looper);
- "none": nothing on the Quad Cortex will do.

A preset gives a chain when every amp matches as "same" or "family", nothing
that is ever switched on is "none", and the chain fits the unit. Every preset
also gives a description of the original in words, which is the request for
the designer when there is no chain.

A chain made this way is a starting point, and says so block by block
(`Imported.lines`): what each block became and why, which of its values were
set from the POD Go's, and that every other value is the Quad Cortex's own.

A backup that cannot be read says which folder of it and why; a preset in a
shape this reader does not know is listed with that as its problem, and the
others come across.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import zlib

from lab.sound import catalog
from lab import knowledge
from lab.sound.chain import MULTI_OUT, Block, Chain, Row, SCENES
from lab.sound.sounds import Sound, settings

RESEARCH = Path(__file__).resolve().parents[2] / "research" / "podgo-models.json"
INPUT, SCENE_NAME = 1, 12
SNAPSHOT_CONTROLLER = 11          # the controller number that assigns a value to snapshots
SNAPSHOTS = 4

# Quad Cortex models the gear table has no entry for, as Neural DSP's device list gives them.
QC_SUPPLEMENT = {
    2010: "Marshall Super Bass 50", 2011: "Marshall Super Bass 50", 2012: "Marshall Super Bass 50",
    2013: "Gallien Krueger 800RB", 2014: "Mesa Boogie Bass 400+", 2015: "Mesa Boogie Bass 400+",
    2016: "Ampeg Heritage SVT-CL", 2017: "Ampeg Heritage B15N", 2018: "Ampeg Heritage B15N",
    2019: "Ampeg Heritage B15N", 2020: "Ampeg Heritage B15N", 2021: "Hiwatt DR103 Mod", 2022: "Hiwatt DR103 Mod",
    21001: "Darkglass D210C 2x10 with custom Eminence ceramic drivers",
    21002: "Fender Bassman 4x10 with Jensen P10Q drivers", 21003: "Ampeg SVT 810 8x10 with custom Eminence ceramic drivers",
    21004: "Ampeg SVT 115HE 1x15", 21005: "Darkglass DG212N 2x12 with custom Eminence neodymium drivers",
    21006: "Marshall 1935A 4x12 with Celestion G12M25 drivers", 21008: "Ampeg B410HE 4x10 with 90s speakers",
    21009: "Ampeg SVT810 8x10 with 90s Eminence speakers",
}

# The Quad Cortex sections a Line 6 category may be matched in.
SECTIONS = {
    "amp": ("Guitar Amplifier", "Bass Amplifier"), "preamp": ("Guitar Amplifier", "Bass Amplifier"),
    "drive": ("Guitar Overdrive", "Bass Overdrive"), "fuzz": ("Guitar Overdrive", "Bass Overdrive"),
    "boost": ("Guitar Overdrive", "Bass Overdrive"), "cab": ("Cabsim Guitar (M)", "Cabsim Bass (M)"),
    "compressor": ("Compressor",), "wah": ("Wah",), "filter": ("Filter",), "pitch": ("Pitch",),
    "chorus": ("Modulation",), "flanger": ("Modulation",), "phaser": ("Modulation",), "tremolo": ("Modulation",),
    "rotary": ("Modulation",), "modulation": ("Modulation",), "delay": ("Delay",), "reverb": ("Reverb",),
    "eq": ("Equalizer",),
}

# Stand-ins of the same kind, first word found in the Line 6 name or based-on wins.
KIND = {
    "delay": (("tape", 6004), ("echoplex", 6004), ("analog", 6010), ("dm-2", 6010), ("bucket", 6010),
              ("memory", 6010), ("echo", 6004), ("ping", 6002), ("reverse", 6013), ("dual", 6012), ("", 6011)),
    "reverb": (("spring", 8001), ("plate", 8013), ("hall", 8003), ("room", 8000), ("chamber", 8000),
               ("tile", 8000), ("cave", 8009), ("ambien", 8008), ("shimmer", 8010), ("plateaux", 8010),
               ("searchlight", 8007), ("glitz", 8007), ("ganymede", 8007), ("", 8003)),
    "chorus": (("dimension", 7027), ("", 7000)), "flanger": (("", 7021),), "phaser": (("vibe", 7029), ("", 7028)),
    "tremolo": (("", 7004),), "rotary": (("", 7008),), "modulation": (("vibrato", 7002), ("", 7000)),
    "compressor": (("opto", 5007), ("la-2a", 5007), ("1176", 5001), ("", 5005)),
    "wah": (("", 9013),), "filter": (("", 24003),), "pitch": (("whammy", 18006), ("wham", 18006), ("octa", 18004),
                                                          ("", 18001)),
    "eq": (("graphic", 4002), ("cut", 4003), ("", 4001)), "gate": (("", 16003),),
    "boost": (("", 23),), "fuzz": (("big muff", 15), ("muff", 15), ("", 22)),
    # An overdrive the Quad Cortex has no model of: a bass drive for a bass drive, the Bluesbreaker-type pedal for
    # its descendants, a distortion for a distortion, and otherwise the plain overdrive most rigs have.
    "drive": (("bass", 3001), ("darkglass", 3000), ("of tone", 9), ("distortion", 10), ("", 5)),
}
RELATION_WORDS = {"same": "same gear", "family": "same family", "kind": "same kind"}
OMITTED = {"utility", "volume", "looper", "acoustic", "synth"}
GENERIC = set("""amp amplifier head combo channel ch normal nrm bright brt brilliant jumped jump vibrato vib clean lead
crunch rhythm drive overdrive od the of with and model version mod modded custom series pedal speaker speakers drivers
cab cabinet open back closed straight angled made japan style based original design circuit boost booster treble
distortion fuzz compressor comp delay echo chorus flanger phaser tremolo wah tweed gain high low""".split())
# Speaker types, as words that appear in Line 6's and the gear table's descriptions.
SPEAKERS = (("v30", r"v30|vintage 30"), ("greenback", r"greenback|g12m|\bm2[05]\b"), ("h30", r"h30|g12h"),
            ("t75", r"t75"), ("blue", r"alnico blue|\bblue\b"), ("silver", r"silver"), ("c12n", r"c12n"),
            ("c12q", r"c12q"), ("c12k", r"c12k"), ("c10r", r"c10r"), ("p10r", r"p10r|pr10"), ("p10q", r"p10q"),
            ("oxford", r"oxford"), ("fane", r"fane"), ("cream", r"cream"), ("cl80", r"cl80|classic lead 80"),
            ("g65", r"g12-?65|\bg65\b"), ("ev", r"\bev\b|electro-voice|evm12"), ("copperhead", r"copperhead"))
# Matches the general rules get wrong, each with the reason. (Line 6 id: (Quad Cortex id, relation, why))
OVERRIDES = {
    "HD2_DM4Screamer": (5, "family", "Line 6 names an Ibanez Tube Screamer without the version; the TS808 is one"),
    "HD2_DistTopSecretODMono": (3, "same", "DOD OD-250 is the DOD Overdrive Preamp 250"),
    "HD2_DistArbitratorFuzzMono": (22, "same", "the Arbiter Fuzz Face, now made by Dunlop"),
    "HD2_DM4FacialFuzz": (22, "same", "the Arbiter Fuzz Face, now made by Dunlop"),
    "HD2_AmpBritTremNrm": (1081, "family", "the JTM-50 is the first Marshall 50 W model 1987, which Brit Plexi 50 is"),
    "HD2_AmpBritTremBrt": (1079, "family", "the JTM-50 is the first Marshall 50 W model 1987, which Brit Plexi 50 is"),
    "HD2_AmpBritTremJump": (1080, "family", "the JTM-50 is the first Marshall 50 W model 1987, which Brit Plexi 50 is"),
    "HD2_Chorus70sChorusStereo": (7025, "family", "the BOSS CE-2W has a CE-1 mode"),
    "HD2_MM4AnalogChorus": (7025, "family", "the BOSS CE-2W has a CE-1 mode"),
    "HD2_AmpGermanUbersonic": (1131, "same", "Line 6's Ubersonic is the Überschall's high-gain channel, which is "
                                             "Bogna Uber Lead"),
    "HD2_AmpGermanMahadeva": (1117, "family", "the Quad Cortex has the Shiva's clean channel alone, and Line 6 does "
                                              "not say which channel it modelled"),
}
MAKERS = {"mesa/boogie": "mesa", "mesa boogie": "mesa", "boogie": "mesa", "pro co": "proco", "pro-co": "proco",
          "electro-harmonix": "ehx", "electro harmonix": "ehx", "gallien-krueger": "gk", "gallien krueger": "gk",
          "paul reed smith": "prs", "dr z": "drz", "dr. z": "drz", "teletronix": "teletronix",
          "universal audio": "ua", "line 6": "line6"}
# Channel words: how Line 6 says it, and how a Quad Cortex model's name says it.
CHANNELS = ((r"\bnormal\b|\bnrm\b", "normal"), (r"\bbright\b|\bbrilliant\b|\bbrt\b", "bright"),
            (r"\bvibrato\b", "vibrato"), (r"\bjumped\b|\bpatch\b", "patch"), (r"\blead\b|\boverdrive\b", "lead"),
            (r"\bcrunch\b", "crunch"), (r"\bclean\b", "clean"), (r"\brhythm\b", "rhythm"),
            (r"top ?boost", "topboost"), (r"\bdrive\b", "drive"), (r"\b(channel|ch\.?) ?(1|i)\b|\bch1\b", "ch1"),
            (r"\b(channel|ch\.?) ?(2|ii)\b|\bch2\b", "ch2"), (r"\b(channel|ch\.?) ?3\b|\bch3\b", "ch3"),
            (r"\b(channel|ch\.?) ?4\b|\bch4\b", "ch4"))


@lru_cache(maxsize=1)
def research():
    return json.loads(RESEARCH.read_text(encoding="utf-8"))


def _ids(model_id):
    """A Line 6 id and its twin: most effects exist as a mono and a stereo model under two ids, and the tables
    here list one of the two."""
    model_id = str(model_id)
    yield model_id
    for mine, other in (("Mono", "Stereo"), ("Stereo", "Mono")):
        if model_id.endswith(mine):
            yield model_id[:-len(mine)] + other


def model_info(model_id):
    """What Line 6 says about one of its models: name, category, based_on, maker, certainty, sources, note."""
    for known in _ids(model_id):
        found = research()["models"].get(known)
        if found:
            return found
    return {"name": model_id, "category": None, "based_on": None, "maker": None, "certainty": "unknown",
            "sources": [], "note": "not in research/podgo-models.json"}


def _override(model_id):
    return next((OVERRIDES[known] for known in _ids(model_id) if known in OVERRIDES), None)


# ---- Reading a backup ------------------------------------------------------------------------------------

@dataclass
class Preset:
    setlist: str
    slot: str           # "01A" .. "32D"
    name: str
    tone: dict


def _folder(tag, payload, compressed, expanded):
    """One setlist folder of a backup as (name, [Preset])."""
    if compressed:
        inflater = zlib.decompressobj()
        payload = inflater.decompress(payload, expanded + 1)
        if not inflater.eof:
            raise ValueError("its compressed data is incomplete")
    if len(payload) != expanded:
        raise ValueError("its size is not the one the backup states")
    folder = json.loads(payload)
    name = str(folder["meta"]["name"])
    presets = []
    for i, item in enumerate(folder["data"]["presets"]):
        if not item:
            continue                                # an empty slot
        slot = f"{i // 4 + 1:02}{'ABCD'[i % 4]}"
        meta = item.get("meta") if isinstance(item, dict) else None
        title = str(meta.get("name") or "").strip() if isinstance(meta, dict) else ""
        # A preset in a shape this reader does not know is kept, and said to be unreadable when it is translated.
        presets.append(Preset(name, slot, title or f"Preset {slot}", item.get("tone") if isinstance(item, dict) else None))
    return name, presets


def read_backup(path):
    """The setlists in a POD Go backup, as {"setlists": {name: [Preset]}, "irs": [entry tags]}.

    A file that is not a backup, or a folder of it that cannot be read, is a `ValueError` that says which and why.
    """
    raw = Path(path).read_bytes()
    if len(raw) < 48 or raw[:8] != b"AF6L\x01\0\0\0":
        raise ValueError("Choose a POD Go .pgb backup.")
    index = struct.unpack_from("<I", raw, 8)[0]
    if not 48 <= index < len(raw) or (len(raw) - index) % 36:
        raise ValueError("Invalid POD Go backup index.")
    setlists, irs = {}, []
    for reversed_tag, start, size, compressed, expanded in struct.iter_unpack("<4sQQIQ4x", raw[index:]):
        tag = reversed_tag[::-1].decode("ascii", "replace")
        if start + size > index or expanded > 20_000_000 or compressed not in (0, 1):
            raise ValueError(f"Invalid POD Go backup entry {tag}.")
        if tag.startswith("I"):
            irs.append(tag)
        if not re.fullmatch(r"SL\d\d", tag):       # SL00, SL01: the setlists ("SLNM" holds their names)
            continue
        try:
            name, presets = _folder(tag, raw[start:start + size], compressed, expanded)
        except (zlib.error, ValueError, KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"The folder {tag} of the POD Go backup could not be read: "
                             f"{type(error).__name__}: {error}.") from error
        setlists[name] = presets
    if not setlists:
        raise ValueError("No POD Go setlists found in that archive.")
    return {"setlists": setlists, "irs": irs}


# ---- Matching gear ---------------------------------------------------------------------------------------

def _norm(text, *, watts=False):
    text = (text or "").lower().replace("®", "").replace("™", "").replace("ü", "u").replace('"', " ")
    text = text.replace("×", "x")
    for long, short in MAKERS.items():
        text = text.replace(long, short)
    text = re.sub(r"([a-z])-(\d)", r"\1\2", text)          # JCM-800 -> jcm800, AC-30 -> ac30
    text = re.sub(r"(\d)-(\d)", r"\1\2", text)
    if watts:                                               # the gear table's "100 W" is a rating, not a model
        text = re.sub(r"\b\d+\s*(w|watt|watts)\b", " ", text)
    return text


def _tokens(text, *, watts=False):
    return {word for word in re.findall(r"[a-z0-9+]+", _norm(text, watts=watts)) if len(word) > 1}


def _speakers(text):
    lowered = _norm(text)
    return {name for name, pattern in SPEAKERS if re.search(pattern, lowered)}


def _config(text):
    found = re.search(r"\b(\d)\s*x\s*(\d{1,2})\b", _norm(text))
    return f"{found.group(1)}x{found.group(2)}" if found else None


@lru_cache(maxsize=1)
def _qc_models():
    """Quad Cortex models that can stand in: (id, section, name, real gear), the locked and the non-stock cabs left out."""
    locked = knowledge.locked()
    found = []
    for model in catalog.models():
        if model.id in locked or model.category.startswith("Cabsim") and not model.is_stock_cab:
            continue
        entry = knowledge.gear(model.id) or {}
        real = entry.get("real") or QC_SUPPLEMENT.get(model.id) or ""
        if model.category.startswith("Cabsim") and real:
            real = " ".join(filter(None, (real, entry.get("config"), entry.get("speaker"))))
        found.append((model.id, model.category, model.name, real))
    return found


def _channel(text):
    lowered = (text or "").lower()
    return {word for pattern, word in CHANNELS if re.search(pattern, lowered)}


def find_qc(info, *, bass=False):
    """The Quad Cortex model for a Line 6 model: (model id, relation, why) or None.

    The maker must be the same, and the model words (not the channel or the
    kind of pedal) must meet. "same" when every model word of Line 6's
    description is in the Quad Cortex model's, or every model number is;
    otherwise "family". A cab must also be the same size, and may match on
    its speakers alone as "kind".
    """
    category = info.get("category")
    sections = SECTIONS.get(category)
    based_on = info.get("based_on")
    if not sections or not based_on:
        return None
    if category == "cab":
        return _find_cab(info, sections, bass)
    maker = _tokens(info.get("maker") or based_on.split()[0])
    wanted = _tokens(based_on) - GENERIC - maker
    channels = _channel(based_on)
    best, rivals = None, []
    for model_id, section, name, real in _qc_models():
        if section not in sections or not real:
            continue
        theirs = _tokens(real, watts=True)
        if not maker & theirs:
            continue
        shared = wanted & (theirs - GENERIC)
        if not shared:
            continue
        # Prefer more shared words, then the matching channel, then the section for the instrument, then fewer
        # words the Line 6 gear does not have.
        channel = len(channels & _channel(name))
        # A model name ("jcm800") says more than a word ("twin"), which says more than a bare number ("50").
        weight = sum(3 if re.search(r"\d", w) and re.search(r"[a-z]", w) else 1 if w.isdigit() else 2 for w in shared)
        score = (weight, channel, section.startswith("Bass") == bass, -len(theirs - wanted), -model_id)
        rivals.append((weight, name))
        if best is None or score > best[0]:
            best = (score, model_id, shared, real, name)
    if best is None:
        return None
    score, model_id, shared, real, name = best
    numbers = {word for word in wanted if re.search(r"\d", word)}
    relation = "same" if wanted <= shared or (numbers and numbers <= shared) else "family"
    why = f"{based_on} -> {real}"
    if relation == "same" and channels and _channel(name) and not channels & _channel(name):
        relation = "family"                     # the same amp, another channel
    elif (relation == "same" and category in ("amp", "preamp") and not channels
          and any(weight == score[0] and _channel(other) != _channel(name) for weight, other in rivals)):
        # The Quad Cortex has this gear's channels as separate models and Line 6 does not say which it modelled.
        relation, why = "family", why + f"; Line 6 names no channel, and {name} is one of several"
    return model_id, relation, why


def _find_cab(info, sections, bass):
    based_on = info["based_on"]
    maker = _tokens(info.get("maker") or "") - {"celestion", "jensen", "eminence"}
    size, speakers = _config(based_on), _speakers(" ".join(filter(None, (based_on, info.get("speaker")))))
    wanted = _tokens(based_on) - GENERIC - maker - {"celestion", "jensen", "eminence", size or ""}
    best = None
    for model_id, section, name, real in _qc_models():
        if section not in sections or not real:
            continue
        if size and _config(real) and _config(real) != size:
            continue
        theirs = _tokens(real, watts=True)
        same_maker = bool(maker & theirs)
        shared = wanted & theirs - set(re.findall(r"[a-z0-9]+", " ".join(speakers))) if same_maker else set()
        same_speaker = bool(speakers & _speakers(real))
        same_size = bool(size) and _config(real) == size
        if not (same_maker and (shared or same_speaker or same_size)) and not same_speaker:
            continue
        if same_maker and shared and (same_speaker or not speakers):
            relation = "same" if not wanted - theirs - {s for s in wanted if _speakers(s)} else "family"
        elif same_maker:
            relation = "family"
        else:
            relation = "kind"
        score = ({"same": 2, "family": 1, "kind": 0}[relation], len(shared), same_speaker,
                 section.startswith("Cabsim Bass") == bass, -model_id)
        if best is None or score > best[0]:
            best = (score, model_id, relation, real)
    if best is None:
        return None
    _, model_id, relation, real = best
    why = f"{based_on} -> {real}" if relation != "kind" else f"{based_on}: no Quad Cortex cab of that make; " \
                                                              f"{real} is the same size with the same speakers"
    return model_id, relation, why


def _stand_in(info, key):
    choices = KIND.get(key)
    if not choices:
        return None
    text = " ".join(filter(None, (info.get("name"), info.get("based_on"), info.get("note") if key == "eq" else "")))
    lowered = text.lower()
    for word, model_id in choices:
        if word in lowered and catalog.model(model_id) and model_id not in knowledge.locked():
            return model_id
    return None


# ---- Values ----------------------------------------------------------------------------------------------

def _param(model, *names):
    for name in names:
        found = model.named(name)
        if found:
            return found[0]
    return None


def _knob(block, param, value, scale=10.):
    """Set a 0..1 POD Go value on a Quad Cortex control, scaled to its range when it runs 0 to 10."""
    if param is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return block
    if param.low is None or param.high is None:
        return block
    if scale == 10. and not (0. <= value <= 1. and (param.low, param.high) == (0., 10.)):
        return block
    display = value * scale if scale else value
    display = min(max(display, param.low), param.high)
    try:
        return block.set_display(param.index, display)
    except (TypeError, ValueError, ZeroDivisionError):
        return block


def _scene_values(values):
    """A value that differs by snapshot, as eight scenes (the last four repeat the first snapshot)."""
    return tuple(values) + (values[0],) * (SCENES - len(values))


def carry(block, info, params):
    """The block with the POD Go values whose controls correspond set on it. `params` maps a Line 6 name to a
    value, or to a list of one value per snapshot."""
    model = block.info
    if model is None:
        return block
    category = info.get("category")

    def put(block, param, value, scale=10.):
        if isinstance(value, list):
            parts = [_knob(Block(block.model), param, item, scale).values.get(param.index) if param else None
                     for item in value]
            if param is None or any(part is None for part in parts):
                return _knob(block, param, value[0], scale)
            return block.set(param.index, _scene_values(parts))
        return _knob(block, param, value, scale)

    if category in ("amp", "preamp"):
        entry = knowledge.gear(block.model) or {}
        drive = _param(model, *(filter(None, (entry.get("drive_control"),))), "GAIN", "INPUT GAIN", "GAIN 1",
                       "DRIVE", "VOLUME", "VOLUME NORMAL", "VOLUME BRIGHT")
        level = next((params[name] for name in ("Drive", "DriveNorm", "NrmDrive", "BrtDrive", "DriveBright",
                                                "Ch2Drive", "LeadGain") if name in params), None)
        if level is not None:
            block = put(block, drive, level)
        for name, targets in (("Bass", ("BASS",)), ("Mid", ("MID", "MIDDLE")), ("Treble", ("TREBLE",)),
                              ("Presence", ("PRESENCE",)), ("Master", ("MASTER",))):
            if name in params:
                block = put(block, _param(model, *targets), params[name])
    elif category in ("drive", "fuzz", "boost"):
        gain = next((params[name] for name in ("Gain", "Drive", "Fuzz", "Distortion", "Sustain") if name in params), None)
        if gain is not None:
            block = put(block, _param(model, "GAIN", "OVERDRIVE", "DRIVE", "DISTORTION", "FUZZ", "SUSTAIN"), gain)
        for name, targets in (("Tone", ("TONE",)), ("Bass", ("BASS",)), ("Treble", ("TREBLE",)), ("Mid", ("MID",))):
            if name in params:
                block = put(block, _param(model, *targets), params[name])
    elif category == "delay":
        if "Time" in params and not _truthy(params.get("TempoSync1")):
            block = put(block, _param(model, "DELAY TIME"), _times(params["Time"], 1000.), scale=None)
            sync = _param(model, "SYNC")
            if sync is not None:
                block = block.set_display(sync.index, 0)
        for name, target in (("Feedback", "FEEDBACK"), ("Mix", "MIX")):
            if name in params:
                block = put(block, _param(model, target), _times(params[name], 100.), scale=None)
    elif category == "reverb":
        if "Mix" in params:
            block = put(block, _param(model, "MIX"), _times(params["Mix"], 100.), scale=None)
        if "Predelay" in params:
            block = put(block, _param(model, "PRE DELAY"), _times(params["Predelay"], 1000.), scale=None)
    elif category in ("chorus", "flanger", "phaser", "tremolo", "rotary", "modulation", "compressor", "filter"):
        if "Mix" in params:
            block = put(block, _param(model, "MIX"), _times(params["Mix"], 100.), scale=None)
    elif category == "gate":
        threshold = params.get("Threshold", params.get("OpenThreshold"))
        if threshold is not None:
            block = put(block, _param(model, "THRESHOLD"), threshold, scale=None)
    elif category == "eq":
        if model.name.startswith("Graphic"):
            for name, value in params.items():
                block = put(block, _param(model, name), value, scale=None)
        elif model.name == "Low-High Cut":
            for name, target in (("LowCut", "HPF FREQ"), ("HighCut", "LPF FREQ")):
                if name in params:
                    block = put(block, _param(model, target), params[name], scale=None)
    elif category == "cab":
        for name, target in (("LowCut", "HPF"), ("Low Cut", "HPF"), ("HighCut", "LPF"), ("High Cut", "LPF")):
            if name in params:
                block = put(block, _param(model, target), params[name], scale=None)
    return block


def _truthy(value):
    return any(value) if isinstance(value, list) else bool(value)


def _times(value, factor):
    return [item * factor for item in value] if isinstance(value, list) else value * factor


# ---- A preset, block by block ----------------------------------------------------------------------------

@dataclass
class Piece:
    """One POD Go block and what it became."""
    position: int
    model: str                  # Line 6 id
    name: str
    category: str | None
    based_on: str | None
    certainty: str
    on: tuple                   # on or off in each snapshot used
    params: dict                # Line 6 name -> value, or a list per snapshot
    qc: int | None = None
    relation: str = "none"
    why: str = ""
    dropped: bool = False       # matched, but left out of the chain: a row holds eight blocks
    carried: dict = field(default_factory=dict)   # the Quad Cortex settings set from the POD Go's values

    @property
    def ever_on(self):
        return any(self.on)

    def words(self):
        """The block as a phrase for a request: what it is, what it is based on, its main settings."""
        if self.category == "ir":
            text = f"an impulse response (IR {_first(self.params.get('Index'))} on the POD Go)"
            return text + ("" if self.ever_on else " [off]")
        text = self.name
        if self.based_on:
            text += f" (based on {self.based_on})"
        elif self.certainty == "line6_original":
            text += " (a Line 6 original)"
        shown = _shown(self)
        if shown:
            text += ": " + ", ".join(shown)
        if not self.ever_on:
            text += " [off]"
        elif not all(self.on):
            text += " [on in snapshots " + ",".join(str(i + 1) for i, flag in enumerate(self.on) if flag) + "]"
        return text


def _first(value):
    return value[0] if isinstance(value, list) else value


def _shown(piece):
    """Up to six settings in the POD Go's own terms."""
    shown = []
    knobs = ("Drive", "Gain", "Fuzz", "Sustain", "Bass", "Mid", "Treble", "Presence", "Master", "Tone",
             "DriveNorm", "NrmDrive", "BrtDrive", "Ch2Drive")
    for name, value in piece.params.items():
        value = _first(value)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if piece.category in ("amp", "preamp", "drive", "fuzz", "boost") and name in knobs and 0 <= value <= 1:
            shown.append(f"{name} {value * 10:.1f}")
        elif name == "Mix" and piece.category == "wah":
            continue
        elif name in ("Mix", "Feedback") and 0 <= value <= 1:
            shown.append(f"{name} {value * 100:.0f}%")
        elif name == "Time" and piece.category == "delay":
            shown.append(f"Time {value * 1000:.0f} ms")
        elif name == "Decay" and piece.category == "reverb":
            shown.append(f"Decay {value:g}")
        elif piece.category == "eq" and "Gain" in name or piece.category == "eq" and name.endswith("Hz"):
            if abs(value) >= .5:
                shown.append(f"{name} {value:+.1f} dB")
        if len(shown) >= 6:
            break
    return shown


@dataclass
class Imported:
    """A POD Go preset as a starting point: a sound when the mapping is confident, and always a description."""
    setlist: str
    slot: str
    name: str
    pieces: list
    description: str
    sound: Sound | None = None
    problems: list = field(default_factory=list)

    @property
    def confident(self):
        return self.sound is not None

    @property
    def request(self):
        """The text to hand the designer: the description, as a request for a starting point."""
        return self.description

    def lines(self):
        """What became of each block of the original, one line each: its counterpart, how close a match that is
        and why, and, when there is a chain, which of its settings were set from the POD Go's values."""
        out = []
        for piece in self.pieces:
            if piece.dropped:
                out.append(f"{piece.name}: left out, as a row holds eight blocks")
            elif piece.qc and piece.relation in RELATION_WORDS:
                text = f"{piece.name} → {catalog.model(piece.qc).name} ({RELATION_WORDS[piece.relation]}): {piece.why}"
                if self.sound is not None:
                    text += (". Set from the POD Go: " + ", ".join(f"{name} {value}" for name, value in
                                                                   piece.carried.items())
                             + "; the rest is the Quad Cortex's own" if piece.carried
                             else ". Every value is the Quad Cortex's own")
                out.append(text)
            elif piece.relation == "omitted":
                out.append(f"{piece.name}: left out ({piece.why})")
            else:
                out.append(f"{piece.name}: nothing stands in ({piece.why})")
        return out


def _snapshots(tone):
    """The indices of the snapshots in use (at least the first)."""
    used = [i for i in range(SNAPSHOTS) if tone.get(f"snapshot{i}", {}).get("@valid")]
    return used or [0]


def pieces(preset):
    """The preset's blocks in signal order, with their snapshot states and values."""
    tone = preset.tone
    used = _snapshots(tone)
    found = []
    for dsp in ("dsp0", "dsp1"):
        blocks = tone.get(dsp) or {}
        ordered = sorted(blocks.items(), key=lambda item: (item[1].get("@position", 99) if isinstance(item[1], dict)
                                                           else 99, item[0]))
        for key, block in ordered:
            if not key.startswith("block") or not isinstance(block, dict) or "@model" not in block:
                continue
            info = model_info(block["@model"])
            on = tuple(bool(tone.get(f"snapshot{i}", {}).get("blocks", {}).get(dsp, {}).get(key, block.get("@enabled", True)))
                       for i in used)
            controls = tone.get("controller", {}).get(dsp, {}).get(key, {})
            params = {}
            for name, value in block.items():
                if name.startswith("@"):
                    continue
                if controls.get(name, {}).get("@controller") == SNAPSHOT_CONTROLLER:
                    values = [tone.get(f"snapshot{i}", {}).get("controllers", {}).get(dsp, {}).get(key, {})
                              .get(name, {}).get("@value", value) for i in used]
                    value = values if len(set(map(repr, values))) > 1 else values[0]
                params[name] = value
            category = info.get("category") or ("gate" if "Gate" in block["@model"] else None)
            found.append(Piece(len(found), block["@model"], info.get("name") or block["@model"], category,
                               info.get("based_on"), info.get("certainty", "unknown"), on, params))
    return found


def _pair_cab(amp_id):
    """A stock cab the research pairs with an amp."""
    entry = knowledge.gear(amp_id) or {}
    for item in entry.get("cabs") or []:
        model_id = item.get("model") if isinstance(item, dict) else item
        model = catalog.model(model_id) if isinstance(model_id, int) else None
        if model and model.is_stock_cab and model_id not in knowledge.locked():
            return model_id
    # Otherwise a cab the research says pairs with the amp's family.
    family = entry.get("family")
    for model_id, section, _, _ in _qc_models():
        if section.startswith("Cabsim") and family and family in ((knowledge.gear(model_id) or {}).get("pairs_with") or ()):
            return model_id
    return None


def _first_word(name):
    found = re.match(r"[^\W\d_]+", (name or "").casefold())
    return found.group() if found else ""


def for_bass(name):
    """Whether a preset's name says it is for bass: "bass" as a word of it, or "bas" as its first word."""
    return _first_word(name) == "bas" or bool(re.search(r"\bbass\b", (name or "").casefold()))


def _instrument(preset, found, instruments=()):
    """The player's instrument a preset was made for, or None.

    `instruments` are the player's own ({"name", "kind"}, as the app keeps them). A preset for bass (its name
    says so, or it uses a bass amp or cab) is for the one of the bass kind. Otherwise it is for the one
    instrument its name opens with: the whole name, its initials or its first letters, as players prefix their
    presets ("LP Crunch" for a Les Paul, "Strat clean" for a Stratocaster). Without instruments, only "Bass".
    """
    names = [(str(item["name"]), item.get("kind")) for item in instruments if item.get("name")]
    if for_bass(preset.name) or any(
            catalog.model(p.qc) and catalog.model(p.qc).category.startswith(("Bass", "Cabsim Bass"))
            for p in found if p.qc):
        return next((name for name, kind in names if kind == "bass"), "Bass")
    first = _first_word(preset.name)
    if len(first) < 2:
        return None
    matches = []
    for name, _ in names:
        words = name.casefold().split()
        if first in (name.casefold(), "".join(word[0] for word in words)) or words[0].startswith(first):
            matches.append(name)
    return matches[0] if len(matches) == 1 else None


def sound_id(preset, chain):
    """The id of the sound a preset becomes: the same every time the same preset gives the same chain, so that
    reading the backup again does not make another sound of it."""
    return sha256(f"POD Go {preset.setlist} {preset.slot} {preset.name} {chain.digest()}".encode("utf-8")).hexdigest()[:12]


def translate(preset, instruments=()):
    """One POD Go preset as an `Imported`."""
    found = pieces(preset)
    problems = []
    bass = for_bass(preset.name)
    for piece in found:
        info = model_info(piece.model)
        info = {**info, "category": piece.category}
        if piece.category in OMITTED or piece.category is None and info["certainty"] == "unknown":
            piece.relation, piece.why = ("omitted", "no sound of its own here") if piece.category in OMITTED else \
                ("none", "not in the research table")
            continue
        if piece.category == "ir":
            piece.relation, piece.why = "kind", "an impulse response from the POD Go; a stock cab stands in"
            continue
        match = _override(piece.model) or find_qc(info, bass=bass)
        if match:
            piece.qc, piece.relation, piece.why = match
            continue
        stand_in = _stand_in(info, piece.category)
        if stand_in is not None:
            piece.qc, piece.relation = stand_in, "kind"
            piece.why = ("a Line 6 original" if info["certainty"] == "line6_original"
                         else f"no Quad Cortex model of the {info.get('based_on') or info['name']}") + \
                f"; {catalog.model(stand_in).name} of the same kind"
            continue
        piece.why = ("a Line 6 original amp with no published real model" if info["certainty"] == "line6_original"
                     else f"no Quad Cortex model of the {info.get('based_on') or info['name']}")

    # An impulse response, or an amp without its cab, takes the stock cab the research pairs with the amp.
    amps = [p for p in found if p.category == "amp" and p.qc]
    for piece in found:
        if piece.category == "ir":
            cab = _pair_cab(amps[0].qc) if amps else None
            if cab:
                piece.qc = cab
                piece.why = f"impulse response #{_first(piece.params.get('Index'))} is on the POD Go; " \
                            f"{catalog.model(cab).name} is the cab the research pairs with {catalog.model(amps[0].qc).name}"
            elif amps:
                piece.relation, piece.why = "none", "an impulse response, and no stock cab the research pairs with the amp"
            else:
                piece.relation, piece.why = "none", "an impulse response with no matched amp to pair a stock cab with"
        elif piece.category == "cab" and piece.qc is None:
            # A cab the Quad Cortex has nothing like takes the stock cab the research pairs with the amp, as an
            # impulse response does.
            cab = _pair_cab(amps[0].qc) if amps else None
            if cab:
                piece.qc, piece.relation = cab, "kind"
                piece.why = (f"no Quad Cortex cab like the {piece.based_on or piece.name}; {catalog.model(cab).name} "
                             f"is the cab the research pairs with {catalog.model(amps[0].qc).name}")

    for piece in found:
        if piece.category in ("amp", "preamp") and piece.relation not in ("same", "family"):
            problems.append(f"The amp {piece.name} has no Quad Cortex model of the same gear ({piece.why}).")
        elif piece.relation == "none" and piece.ever_on and not (piece.category == "ir" and not amps):
            problems.append(f"{piece.name}: {piece.why}.")
    if not any(p.category in ("amp", "preamp") for p in found):
        problems.append("The preset has no amp.")

    description = describe(preset, found)
    chain = None
    if not problems:
        chain, more = build(preset, found)
        problems += more
    sound = None
    if chain is not None and not problems:
        instrument = _instrument(preset, found, instruments)
        sound = Sound(name=preset.name[:24] or "POD Go preset", chain=chain, id=sound_id(preset, chain),
                      origin={"kind": "imported", "from": "POD Go", "slot": preset.slot, "name": preset.name},
                      explanation=_explain(found)[:900], tags=[t for t in ("POD Go", instrument) if t])
    return Imported(preset.setlist, preset.slot, preset.name, found, description, sound, problems)


def build(preset, found):
    """The chain for the matched pieces, and what kept it from being built."""
    problems = []
    kept = [p for p in found if p.qc and p.relation in ("same", "family", "kind")]
    labels = ()
    used = _snapshots(preset.tone)
    differs = any(len(set(p.on)) > 1 for p in kept) or any(isinstance(v, list) for p in kept for v in p.params.values())
    if differs and len(used) > 1:
        names = [str(preset.tone.get(f"snapshot{i}", {}).get("@name") or f"Snapshot {i + 1}")[:SCENE_NAME]
                 for i in used]
        labels = tuple(names) + ("",) * (SCENES - len(names))
    while len(kept) > 8:
        idle = next((p for p in kept if not p.ever_on), None)
        drop = idle or next((p for p in reversed(kept) if p.category == "eq"), kept[-1])
        kept.remove(drop)
        drop.dropped = True
        drop.why += "; left out, as a row holds eight blocks"
    cells = []
    for piece in kept:
        block = carry(Block(piece.qc), model_info(piece.model) | {"category": piece.category}, piece.params)
        piece.carried = {**settings(block, every=True), **settings(block)}       # the block began with nothing stated: these came across
        if labels and len(set(piece.on)) > 1:
            block = Block(block.model, block.params, _scene_values([not flag for flag in piece.on]))
        elif not piece.ever_on:
            block = Block(block.model, block.params, True)
        if not labels:
            # One sound: a value that differs by snapshot keeps the first snapshot's.
            block = Block(block.model, tuple((i, v[0] if isinstance(v, tuple) else v) for i, v in block.params),
                          block.bypass)
        cells.append(block)
    chain = Chain((Row(tuple(cells), INPUT, MULTI_OUT),), labels or ("",) * SCENES)
    load = knowledge.weigh([block.model for block in cells])
    if not load.fits:
        problems.append(f"The chain needs more processing than the unit has: "
                        f"{catalog.model(load.refused).name} does not fit.")
    return chain, problems


def _explain(found):
    """What the chain was made from, block by block, and what was left out of it."""
    lines = [f"{piece.name} -> {catalog.model(piece.qc).name} ({RELATION_WORDS[piece.relation]})"
             for piece in found if piece.relation in RELATION_WORDS and piece.qc and not piece.dropped]
    text = "A starting point from the POD Go: " + "; ".join(lines) + "."
    left = [piece.name for piece in found if piece.dropped]
    if left:
        text += " Left out, as a row holds eight blocks: " + ", ".join(left) + "."
    return text + " Values are the POD Go's where the controls correspond, and the Quad Cortex's own elsewhere."


def describe(preset, found=None):
    """The original chain in words, usable as a request for the designer."""
    found = pieces(preset) if found is None else found
    parts = [piece.words() for piece in found if piece.relation != "omitted" and piece.category not in OMITTED]
    text = (f"A starting point from my POD Go preset \"{preset.name}\" ({preset.setlist} {preset.slot}). "
            f"In order: " + "; ".join(parts) + ".")
    used = _snapshots(preset.tone)
    if len(used) > 1 and any(len(set(p.on)) > 1 for p in found):
        names = [str(preset.tone.get(f"snapshot{i}", {}).get("@name") or f"Snapshot {i + 1}") for i in used]
        text += " Snapshots: " + ", ".join(names) + "."
    fx_loop = [p for p in found if p.model.startswith("HD2_FXLoop") and p.ever_on]
    if fx_loop:
        text += " It sends to an external effects loop, which this description cannot include."
    return text


def import_backup(path, setlist="User", instruments=()):
    """Every preset in one setlist of a backup, as `Imported`, in slot order. `instruments` are the player's
    own, for saying which one a preset was made for (see `_instrument`).

    A preset that cannot be translated (a shape of preset this reader does not know) is listed with that as
    its problem, and the others come across.
    """
    lists = read_backup(path)["setlists"]
    if setlist not in lists:
        raise ValueError(f"The backup has no setlist {setlist!r}; it has {', '.join(lists)}.")
    found = []
    for preset in lists[setlist]:
        try:
            found.append(translate(preset, instruments))
        except Exception as error:  # noqa: BLE001  whatever shape the preset has, the others are still read
            found.append(Imported(preset.setlist, preset.slot, preset.name, [],
                                  f'My POD Go preset "{preset.name}" ({preset.setlist} {preset.slot}).', None,
                                  [f"{preset.setlist} {preset.slot} \"{preset.name}\" could not be read "
                                   f"({type(error).__name__}: {error})"]))
    return found
