"""What the player's decisions say about the sounds they prefer, counted and tested, and nothing more.

Research (docs/TONE_RESEARCH.md, findings 3, 4 and 11, proposal 4) says listeners
weight the dimensions of timbre differently, and that an automatic ranking
should be corrected by the player's own choices.

- `features` gathers the measured quantities of a heard record
  (`lab.hearing.measure.Heard.to_record`, with the test-signal record from
  `lab.hearing.probe` under "probe"). Level is left out: sounds are compared at
  matched loudness. Words ("crunch", "bright") are left out: they are derived
  from the same numbers.
- AXES are the five things the decisions are tested on, each one measured
  quantity or the difference of two: amount of distortion, order balance,
  compression, brightness, tail. Quantities that move together (a dozen
  figures follow an amp's gain) are not tested one by one: with a handful of
  decisions no test can tell them apart, and each extra test costs evidence.
- Evidence is pairwise: "this sound was preferred to that one". It comes from
  an append-only `choices.jsonl` in the data folder (`record_choice`, called
  by the studio when the player marks or picks with two sounds on show), and
  from what the store already says: sounds marked "keeper" or saved against
  sounds marked "not this" or heard and passed over, within one request. One
  press is one decision: what the marks say again of a recorded choice is not
  counted a second time, and what else they imply joins that same decision.
- `fit` counts, per axis, the decisions in which the preferred sound had more
  of it and those in which it had less, and tests the count with an exact
  two-sided sign test, corrected for the five axes tested (Bonferroni). The
  test is valid at any number of decisions: with random choices a "strong"
  finding turns up in fewer than one fit in a hundred. Eight unanimous
  decisions on one axis are "moderate"; "strong" takes ten. Below `MINIMUM`
  usable decisions nothing is claimed.
- `describe` says what was found in sentences that name the axis, count the
  decisions behind it, and say when two axes went together in the sounds
  compared, so that the decisions cannot tell which was chosen by.
- The model is kept in `taste.json` in the data folder: personal data, never
  in the repository.
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path

VERSION = 2            # of the kept model; an earlier one is fitted again when it is loaded
MINIMUM = 8            # usable decisions before anything is claimed
HOW = ("blind", "a/b", "mark", "round")
EVENTS = ("keeper", "not this")
NOT_ENOUGH = "not enough evidence yet"
CHOICES = "choices.jsonl"
MODEL = "taste.json"
STRENGTHS = ((.01, "strong"), (.05, "moderate"), (.2, "weak"))      # corrected p below this
TOGETHER = .8          # two axes leaning the same way in this share of the decisions cannot be told apart
TAIL_FLOOR_DB = -60.   # late energy under this is all the same to a listener: no tail

# The measured quantities gathered from a heard record: where each is ("" at the top, "probe" in the test-signal
# record). brightness_oct (the spectral centre) is gathered, since a record may hold nothing else, but is not an
# axis: see lab.hearing.measure.
FEATURES = {"distortion_db": "probe", "distortion_soft_db": "probe", "low_order_db": "probe", "high_order_db": "probe",
            "late_energy_db": "probe", "compression": "", "brightness_db": "", "brightness_oct": ""}


def _order_balance(row):
    low, high = row.get("low_order_db"), row.get("high_order_db")
    if low is None or high is None or low <= -69.9 or high <= -69.9:     # at the floor there is no balance to speak of
        return None
    return high - low


def _tail(row):
    late = row.get("late_energy_db")
    return None if late is None else max(late, TAIL_FLOOR_DB)


# Every axis: how its value is read from a feature row (None when not measured), the smallest difference that
# counts as a difference (provisional: about what a careful listener might notice, not measured on anyone), and
# how a sentence names more and less of it. The phrases name the measured quantity, never an adjective.
AXES = {
    "distortion": (lambda row: row.get("distortion_db"), 2.,
                   "more harmonic distortion of a firmly played tone",
                   "less harmonic distortion of a firmly played tone"),
    "order balance": (_order_balance, 1.,
                      "its harmonics weighted more to the high orders (5th to 10th against 2nd and 3rd)",
                      "its harmonics weighted more to the low orders (2nd and 3rd against 5th to 10th)"),
    "compression": (lambda row: row.get("compression"), .05,
                    "more compression (less of a 12 dB drop in the playing comes back)",
                    "less compression (more of a 12 dB drop in the playing comes back)"),
    "brightness": (lambda row: row.get("brightness_db"), 1.,
                   "more energy above 1 kHz against 200 Hz to 1 kHz",
                   "less energy above 1 kHz against 200 Hz to 1 kHz"),
    "tail": (_tail, 2.,
             "more echo and reverb after a click (energy later than 40 ms)",
             "less echo and reverb after a click (energy later than 40 ms)"),
}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def features(heard_record):
    """The measured quantities of a heard record by name, skipping any not measured. A quantity is looked for
    where it belongs and then in the other place, so a key moving between the record and its probe still counts."""
    if not heard_record:
        return {}
    probe = heard_record.get("probe") or {}
    found = {}
    for name, where in FEATURES.items():
        first, second = (probe, heard_record) if where == "probe" else (heard_record, probe)
        value = _number(first.get(name))
        if value is None:
            value = _number(second.get(name))
        if value is not None:
            found[name] = value
    return found


def axes(row):
    """The value of every axis for a feature row, leaving out any that was not measured."""
    found = {name: read(row) for name, (read, _, _, _) in AXES.items()}
    return {name: value for name, value in found.items() if value is not None}


# ---- Evidence ----

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_choice(folder, kept_id, over_id, how, *, round_id=None, context=None, event=None):
    """Note that the player preferred one sound to another. `how`: "blind" (with the names hidden), "a/b" (with
    the names shown), "mark" (marked one keeper or the other not this), "round" (kept or saved one design of a
    round over another). `event` is the press it was, "keeper" (on the preferred sound) or "not this" (on the
    other), when known. `context` is what the audition was: the phrase and the part of it, the scene, and each
    sound's knob positions. Appended to `choices.jsonl`; nothing is ever rewritten."""
    if how not in HOW:
        raise ValueError(f"how must be one of {', '.join(HOW)}")
    if event is not None and event not in EVENTS:
        raise ValueError(f"event must be one of {', '.join(EVENTS)}")
    if not kept_id or not over_id or kept_id == over_id:
        return None
    entry = {"at": _now(), "kept": kept_id, "over": over_id, "how": how}
    if round_id:
        entry["round"] = round_id
    if event:
        entry["event"] = event
    if context:
        entry["context"] = context
    path = Path(folder) / CHOICES
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry) + "\n")
    return entry


def choices(folder):
    """The recorded choices, oldest first; a damaged line is skipped."""
    found = []
    try:
        lines = (Path(folder) / CHOICES).read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("kept") and entry.get("over") and entry["kept"] != entry["over"]:
            found.append(entry)
    return found


def _rounds(folder):
    found = []
    for path in sorted((Path(folder) / "rounds").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("candidates"), list):
            found.append(record)
    return found


def _chosen(sound):
    return "keeper" in sound.marks or bool(sound.saved)


def _rejected(sound):
    return "not this" in sound.marks and not _chosen(sound)


def comparisons(folder, sounds=None):
    """Every pairwise comparison the evidence gives, as {"kept", "over", "how", "weight", "decision"}.

    - Each recorded choice, weight 1. The same choice recorded again in the same audition (the same take, scene
      and knob positions) counts once, since marking and unmarking is not new evidence; except a blind pick,
      which is one each time it is made: picking the same sound again without knowing which it is, is.
    - Within one request (the candidates of every round asked with the same words): a sound marked keeper or
      saved over one marked not this (weight 1), and over one played and left unmarked (weight 0.5); one played
      and left unmarked over one marked not this (weight 0.5).
    - Outside rounds: a keeper over a not-this when one was revised from the other or both from the same sound.
    One press is one decision. A pair the marks give that a recorded choice already gave (the press that set the
    mark recorded it) is left out, and what else that mark implies (the keeper over the round's other designs)
    joins the recorded choice's decision instead of making another.
    Keepers and not-thises of different requests are never compared: what was asked for would decide the pair,
    not the player's taste. A pair given both ways (the player changed their mind) keeps both, and they cancel.
    """
    if sounds is None:
        from lab.sound.sounds import Store

        sounds = Store(folder).sounds()
    by_id = {sound.id: sound for sound in sounds}
    pairs = {}
    recorded, of_kept, of_over = set(), {}, {}

    def add(kept, over, how, weight, decision, context=None, again=None):
        if kept == over:
            return
        key = (kept, over, json.dumps(context, sort_keys=True) if context else None, again)
        if key not in pairs or pairs[key]["weight"] < weight:
            pairs[key] = {"kept": kept, "over": over, "how": how, "weight": weight, "decision": decision,
                          **({"context": context} if context else {})}

    for number, entry in enumerate(choices(folder)):
        how, decision = entry.get("how", "a/b"), f"choice {number}"
        add(entry["kept"], entry["over"], how, 1., decision, entry.get("context"), number if how == "blind" else None)
        recorded.add((entry["kept"], entry["over"]))
        of_kept.setdefault(entry["kept"], decision)
        of_over.setdefault(entry["over"], decision)

    def implied(kept, over, how, weight, decision):
        if (kept, over) not in recorded:
            add(kept, over, how, weight, decision)

    requests, in_rounds = {}, set()
    for record in _rounds(folder):
        group = requests.setdefault(str(record.get("request", "")).casefold().strip(), [])
        for candidate in record["candidates"]:
            sound = by_id.get(candidate.get("sound") if isinstance(candidate, dict) else None)
            if sound is not None and sound not in group:
                group.append(sound)
                in_rounds.add(sound.id)
    for group in requests.values():
        chosen = [sound for sound in group if _chosen(sound)]
        rejected = [sound for sound in group if _rejected(sound)]
        passed = [sound for sound in group if sound.played and not _chosen(sound) and not _rejected(sound)]
        # A keeper among several designs is one decision: its pairs share one decision's weight.
        for kept in chosen:
            others = [(over, "mark", 1.) for over in rejected] + [(over, "round", .5) for over in passed]
            for over, how, weight in others:
                implied(kept.id, over.id, how, weight / len(others), of_kept.get(kept.id, f"keeper {kept.id}"))
        for over in rejected:
            for kept in passed:
                implied(kept.id, over.id, "round", .5 / len(passed), of_over.get(over.id, f"not this {over.id}"))

    loose = [sound for sound in sounds if sound.id not in in_rounds]
    for kept in (sound for sound in loose if _chosen(sound)):
        for over in (sound for sound in loose if _rejected(sound)):
            related = (kept.parent == over.id or over.parent == kept.id
                       or (kept.parent is not None and kept.parent == over.parent))
            if related:
                implied(kept.id, over.id, "mark", 1., of_kept.get(kept.id, f"keeper {kept.id}"))
    return list(pairs.values())


def evidence(folder, sounds=None):
    """(comparisons, feature rows by sound id) from the data folder, ready for `fit`."""
    if sounds is None:
        from lab.sound.sounds import Store

        sounds = Store(folder).sounds()
    by_id = {sound.id: sound for sound in sounds}
    rows = {sound.id: features(sound.heard) for sound in sounds}
    pairs = []
    for pair in comparisons(folder, sounds):
        context = pair.get("context")
        if context:
            # An audition with its own context: each sound as it was heard then, or the pair is left out.
            pair = dict(pair)
            for side in ("kept", "over"):
                pair[side] = _as_auditioned(by_id.get(pair[side]), pair[side], context, rows)
        pairs.append(pair)
    return pairs, {key: value for key, value in rows.items() if value}


def _as_auditioned(sound, sound_id, context, rows):
    """The feature row of a sound as it was auditioned: with the knobs it had, on the take it was played on.
    Returns the key of that row (added to `rows`), or a key with no row when it was not measured so."""
    missing = f"{sound_id}|unmeasured"
    if sound is None or not sound.heard:
        return missing
    knobs = (context.get("knobs") or {}).get(sound_id) or {}
    phrase = context.get("phrase")
    record = sound.heard if not knobs else sound.heard.get("shaped")
    if not record or (knobs and record.get("knobs") != knobs) or (phrase and record.get("phrase") != phrase):
        return missing
    # The scene heard, when the sound's scenes differ: a measurement of another scene is not that audition.
    scene = int(context.get("scene") or 0)
    if sound.chain.scenes_in_use() > 1:
        if int(record.get("scene", sound.heard.get("scene", 0)) or 0) != scene:
            return missing
    else:
        scene = 0
    key = f"{sound_id}|{json.dumps(knobs, sort_keys=True)}|{phrase}|{scene}"
    rows[key] = features(record if not knobs else {**record, "probe": record.get("probe")})
    return key


# ---- The model ----

def sign_test(more, fewer, tests=1):
    """The exact two-sided sign test: the chance, were each decision a coin toss, of a split at least as uneven
    as `more` against `fewer`, multiplied by the number of things tested at once (Bonferroni), at most 1."""
    n, k = more + fewer, max(more, fewer)
    if not n:
        return 1.
    tail = sum(math.comb(n, j) for j in range(k, n + 1)) / 2 ** n
    return min(1., 2 * tail * tests)


def _strength(p):
    return next((word for below, word in STRENGTHS if p < below), "none")


def _not_enough(n, minimum, message=NOT_ENOUGH):
    return {"version": VERSION, "enough": False, "message": message, "n": n, "minimum": minimum, "fitted": _now(),
            "axes": {}, "together": []}


def fit(comparisons, feature_rows, *, minimum=MINIMUM):
    """What the decisions say, axis by axis. `comparisons`: [{"kept", "over", "weight"?, "decision"?}],
    `feature_rows`: {sound id: features}. Comparisons whose sounds were not both measured are not used, and the
    comparisons of one decision count once, together. With fewer than `minimum` usable decisions the model says
    "not enough evidence yet" and claims nothing."""
    usable = [pair for pair in comparisons if feature_rows.get(pair["kept"]) and feature_rows.get(pair["over"])]
    decision_of = [pair.get("decision", f"pair {index}") for index, pair in enumerate(usable)]
    decisions = sorted(set(decision_of))
    members = {decision: [i for i, d in enumerate(decision_of) if d == decision] for decision in decisions}
    if len(decisions) < minimum:
        return _not_enough(len(decisions), minimum)
    values = {key: axes(row) for key, row in feature_rows.items()}
    leans = {}          # axis -> {decision: +1 the preferred sound had more of it, -1 less}
    for name, (_, least, _, _) in AXES.items():
        leans[name] = {}
        for decision in decisions:
            lean = 0.       # which way this decision leans on the axis: its pairs, by their weights
            for index in members[decision]:
                pair = usable[index]
                kept, over = values[pair["kept"]].get(name), values[pair["over"]].get(name)
                if kept is not None and over is not None and abs(kept - over) >= least:
                    lean += float(pair.get("weight", 1.)) * (1 if kept > over else -1)
            if lean:
                leans[name][decision] = 1 if lean > 0 else -1
    if not any(leans.values()):
        return _not_enough(len(decisions), minimum, "no measured quantity differs between the sounds compared")
    found = {}
    for name, by_decision in leans.items():
        higher = sum(1 for lean in by_decision.values() if lean > 0)
        lower = len(by_decision) - higher
        p = sign_test(higher, lower, tests=len(AXES))
        found[name] = {"kept_higher": higher, "kept_lower": lower, "n": higher + lower, "p": round(p, 4),
                       "strength": _strength(p), "lean": (higher > lower) - (higher < lower)}
    together = []
    claimed = [name for name, item in found.items() if item["strength"] != "none"]
    for i, first in enumerate(claimed):
        for second in claimed[i + 1:]:
            both = [d for d in leans[first] if d in leans[second]]
            same = sum(1 for d in both if leans[first][d] * found[first]["lean"] == leans[second][d] * found[second]["lean"])
            if len(both) >= 5 and same >= TOGETHER * len(both):
                together.append({"axes": [first, second], "same": same, "n": len(both)})
    return {"version": VERSION, "enough": True, "message": None, "n": len(decisions), "minimum": minimum,
            "fitted": _now(), "axes": found, "together": together}


def learn(folder, sounds=None, **options):
    """Fit the model from everything in the data folder, keep it in `taste.json`, and return it."""
    model = fit(*evidence(folder, sounds), **options)
    save(folder, model)
    return model


# ---- Saying it ----

STRENGTH_ORDER = {"strong": 0, "moderate": 1, "weak": 2}
DECISION = ("A decision is one Keep or Not this with two sounds on show (the other sound is then the one passed "
            "over, or the one preferred), one blind pick, or one keeper among a round's designs.")


def _phrase(name, item):
    _, _, more, less = AXES[name]
    return more if item["lean"] > 0 else less


def describe(model, *, most=4):
    """What the model found, in sentences that name the axis and count the decisions behind it."""
    if not model or not model.get("enough") or "axes" not in model:
        n, minimum = (model or {}).get("n", 0), (model or {}).get("minimum", MINIMUM)
        reason = (model or {}).get("message") or NOT_ENOUGH
        if reason != NOT_ENOUGH:
            return [f"Of {n} decisions, nothing can be said yet: {reason}."]
        return [f"{reason[0].upper()}{reason[1:]}: {n} usable decision{'s' if n != 1 else ''} between measured "
                f"sounds, {minimum} needed before anything is said about your taste."]
    n = model["n"]
    claims = sorted(((name, item) for name, item in model["axes"].items() if item["strength"] in STRENGTH_ORDER),
                    key=lambda pair: (STRENGTH_ORDER[pair[1]["strength"]], pair[1]["p"]))[:most]
    lines = []
    for name, item in claims:
        agreeing, differed = max(item["kept_higher"], item["kept_lower"]), item["n"]
        if item["strength"] == "weak":
            lines.append(f"Your decisions so far lean toward {_phrase(name, item)} ({agreeing} of {differed} where "
                         "it differed; weak evidence).")
            continue
        lines.append(f"In {agreeing} of {differed} decisions where it differed, the sound you preferred had "
                     f"{_phrase(name, item)}" + (" (moderate evidence)." if item["strength"] == "moderate" else "."))
    if not lines:
        return [f"Of {n} decisions, none of the measured quantities tells which sound you prefer yet."]
    named = {name for name, _ in claims}
    for item in model.get("together") or []:
        first, second = item["axes"]
        if first in named and second in named:
            lines.append(f"{first[0].upper()}{first[1:]} and {second} went together in the sounds you compared "
                         f"({item['same']} of {item['n']} decisions), so these decisions cannot tell which of the "
                         "two you chose by.")
    return lines + [DECISION]


# ---- Keeping it ----

def save(folder, model):
    path = Path(folder) / MODEL
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(model, indent=1), encoding="utf-8")
    part.replace(path)
    return model


def load(folder):
    """The kept model, or None when there is none or it cannot be read. A model kept by an earlier version of
    this module (it claimed more than its evidence held) is not shown: it is fitted again from the evidence."""
    try:
        model = json.loads((Path(folder) / MODEL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(model, dict) or "enough" not in model:
        return None
    if model.get("version") != VERSION:
        try:
            return learn(folder)
        except Exception:  # noqa: BLE001  the evidence could not be read: say nothing rather than something stale
            return None
    return model
