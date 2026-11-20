"""What the player keeps, learned from their own choices.

Research (docs/TONE_RESEARCH.md, findings 3, 4 and 11, proposal 4) says listeners
weight the dimensions of timbre differently, and that an automatic ranking
should be corrected by the player's own choices. This module does that and
nothing more:

- `features` turns a heard record (`lab.hearing.measure.Heard.to_record`, with the
  test-signal record from `lab.hearing.probe` under "probe") into numbers, each named
  for the quantity measured. Level is left out: sounds are compared at matched
  loudness. Words ("crunch", "bright") are left out: they are derived from the
  same numbers.
- Evidence is pairwise: "this sound was kept over that one". It comes from
  an append-only `choices.jsonl` in the data folder (`record_choice`, called
  by the studio when the player picks in a blind or an A/B comparison), and
  from what the store already says: sounds marked "keeper" or saved against
  sounds marked "not this" or heard and passed over, within one request.
- `fit` learns one weight per measured quantity: features standardized over
  the sounds compared, an L2-regularized pairwise logistic regression (no
  intercept, so the order of a pair does not matter), with a bootstrap for how
  sure each weight is. Below `MINIMUM` usable comparisons it claims nothing.
- `describe` says what was found in sentences that name the measured
  quantity and count the choices behind it; `rank` orders candidates by the
  learned weights and says how sure the order is.
- The model is kept in `taste.json` in the data folder: personal data, never
  in the repository.
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path

import numpy as np

MINIMUM = 8            # usable comparisons before anything is claimed
L2 = 1.               # pull of each weight toward zero
BOOTSTRAP = 200        # resamples of the comparisons, for how sure each weight is
KEPT_SAMPLES = 100     # bootstrap weight vectors kept in the model, for how sure a ranking is
HOW = ("blind", "a/b", "mark", "round")
NOT_ENOUGH = "not enough evidence yet"
CHOICES = "choices.jsonl"
MODEL = "taste.json"

# Every feature: where it is in a heard record ("" at the top, "probe" in the test-signal record), and how a
# sentence names a higher and a lower value of it. The phrases name the measured quantity, never an adjective.
FEATURES = {
    "distortion_db": ("probe", "more harmonic distortion of a firmly played tone",
                      "less harmonic distortion of a firmly played tone"),
    "distortion_soft_db": ("probe", "more harmonic distortion of a tone played 12 dB softer",
                           "less harmonic distortion of a tone played 12 dB softer"),
    "weighted_distortion_db": ("probe", "more order-weighted harmonic distortion",
                               "less order-weighted harmonic distortion"),
    "low_order_db": ("probe", "more energy in the low-order harmonics", "less energy in the low-order harmonics"),
    "high_order_db": ("probe", "more energy in the high-order harmonics", "less energy in the high-order harmonics"),
    "harmonic_centroid": ("probe", "a higher harmonic centroid (energy further up the harmonic series)",
                          "a lower harmonic centroid (energy further down the harmonic series)"),
    "tristimulus3": ("probe", "a larger share of harmonic energy from the 5th harmonic up (tristimulus 3)",
                     "a smaller share of harmonic energy from the 5th harmonic up (tristimulus 3)"),
    "even_odd_db": ("probe", "more even harmonics against odd ones", "fewer even harmonics against odd ones"),
    "intermod_db": ("probe", "more intermodulation between two notes played together",
                    "less intermodulation between two notes played together"),
    "hnr_db": ("probe", "a higher harmonic-to-noise ratio", "a lower harmonic-to-noise ratio"),
    "decay_s": ("probe", "a longer decay after a short sound", "a shorter decay after a short sound"),
    "level_wobble": ("probe", "more level movement on a held note", "less level movement on a held note"),
    "pitch_wobble": ("probe", "more pitch movement on a held note", "less pitch movement on a held note"),
    "compression": ("", "more compression (less of a 12 dB drop in the playing comes back)",
                    "less compression (more of a 12 dB drop in the playing comes back)"),
    "crest_db": ("", "a higher crest factor (peaks further above the average level)",
                 "a lower crest factor (peaks closer to the average level)"),
    "brightness_oct": ("", "a higher spectral centre against the dry guitar",
                       "a lower spectral centre against the dry guitar"),
    "low_end_db": ("", "more energy below 200 Hz against the dry guitar", "less energy below 200 Hz against the dry guitar"),
    "tail_db": ("", "a louder tail after the playing stops", "a quieter tail after the playing stops"),
    "attack_ms": ("", "a longer onset time (slower attack)", "a shorter onset time (faster attack)"),
    "attack_soft_ms": ("", "a longer onset time when played 12 dB softer", "a shorter onset time when played 12 dB softer"),
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
    for name, (where, _, _) in FEATURES.items():
        first, second = (probe, heard_record) if where == "probe" else (heard_record, probe)
        value = _number(first.get(name))
        if value is None:
            value = _number(second.get(name))
        if value is not None:
            found[name] = value
    return found


def _heard_of(item):
    if isinstance(item, dict):
        return item
    return getattr(item, "heard", None)


# ---- Evidence ----

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_choice(folder, kept_id, over_id, how, *, round_id=None, context=None):
    """Note that the player kept one sound over another. `how`: "blind" (picked with the names hidden), "a/b"
    (picked with the names shown), "mark" (marked one keeper or the other not this), "round" (kept or saved one
    design of a round over another). `context` is what the audition was: the phrase and the part of it, the scene,
    and each sound's knob positions. Appended to `choices.jsonl`; nothing is ever rewritten."""
    if how not in HOW:
        raise ValueError(f"how must be one of {', '.join(HOW)}")
    if not kept_id or not over_id or kept_id == over_id:
        return None
    entry = {"at": _now(), "kept": kept_id, "over": over_id, "how": how}
    if round_id:
        entry["round"] = round_id
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
    """Every pairwise comparison the evidence gives, as {"kept", "over", "how", "weight"}.

    - Each recorded choice, weight 1.
    - Within one request (the candidates of every round asked with the same words): a sound marked keeper or
      saved over one marked not this (weight 1), and over one played and left unmarked (weight 0.5); one played
      and left unmarked over one marked not this (weight 0.5).
    - Outside rounds: a keeper over a not-this when one was revised from the other or both from the same sound.
    Keepers and not-thises of different requests are never compared: what was asked for would decide the pair,
    not the player's taste. A pair given more than once counts once, at its largest weight; a pair given both
    ways (the player changed their mind) keeps both, and they cancel.
    """
    if sounds is None:
        from lab.sound.sounds import Store

        sounds = Store(folder).sounds()
    by_id = {sound.id: sound for sound in sounds}
    pairs = {}

    def add(kept, over, how, weight, decision, context=None):
        if kept == over:
            return
        key = (kept, over)
        if key not in pairs or pairs[key]["weight"] < weight:
            pairs[key] = {"kept": kept, "over": over, "how": how, "weight": weight, "decision": decision,
                          **({"context": context} if context else {})}

    for number, entry in enumerate(choices(folder)):
        add(entry["kept"], entry["over"], entry.get("how", "a/b"), 1., f"choice {number}", entry.get("context"))

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
                add(kept.id, over.id, how, weight / len(others), f"keeper {kept.id}")
        for over in rejected:
            if passed:
                for kept in passed:
                    add(kept.id, over.id, "round", .5 / len(passed), f"not this {over.id}")

    loose = [sound for sound in sounds if sound.id not in in_rounds]
    for kept in (sound for sound in loose if _chosen(sound)):
        for over in (sound for sound in loose if _rejected(sound)):
            related = (kept.parent == over.id or over.parent == kept.id
                       or (kept.parent is not None and kept.parent == over.parent))
            if related:
                add(kept.id, over.id, "mark", 1., f"keeper {kept.id}")
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
    key = f"{sound_id}|{json.dumps(knobs, sort_keys=True)}|{phrase}"
    rows[key] = features(record if not knobs else {**record, "probe": record.get("probe")})
    return key


# ---- The model ----

def _solve(diffs, weights, l2):
    """Weights w minimizing sum c log(1 + exp(-d.w)) + l2/2 |w|^2, by Newton's method, and its Hessian."""
    w = np.zeros(diffs.shape[1])
    hessian = np.eye(diffs.shape[1]) * l2
    for _ in range(50):
        margin = np.clip(diffs @ w, -30, 30)
        p = 1 / (1 + np.exp(-margin))
        gradient = -(weights * (1 - p)) @ diffs + l2 * w
        hessian = (diffs * (weights * p * (1 - p))[:, None]).T @ diffs + l2 * np.eye(diffs.shape[1])
        move = np.linalg.solve(hessian, gradient)
        w -= move
        if np.max(np.abs(move)) < 1e-8:
            break
    return w, hessian


def _strength(z, tests):
    """How strong a weight's evidence is, from its bootstrap z, corrected for testing `tests` features at once."""
    p = math.erfc(abs(z) / math.sqrt(2)) * tests
    if p < .01:
        return "strong"
    if p < .05:
        return "moderate"
    if p < .2:
        return "weak"
    return "none"


def _not_enough(n, minimum, message=NOT_ENOUGH):
    return {"version": 1, "enough": False, "message": message, "n": n, "minimum": minimum, "fitted": _now(),
            "features": {}}


def fit(comparisons, feature_rows, *, minimum=MINIMUM, l2=L2, bootstrap=BOOTSTRAP, seed=0):
    """A model of what the player keeps. `comparisons`: [{"kept", "over", "weight"?}], `feature_rows`: {sound id:
    features}. Comparisons whose sounds were not both measured are not used. With fewer than `minimum` usable
    comparisons, the model says "not enough evidence yet" and claims nothing."""
    usable = [pair for pair in comparisons
              if feature_rows.get(pair["kept"]) and feature_rows.get(pair["over"])]
    # Comparisons implied by one decision of the player's count once, together.
    decision_of = [pair.get("decision", f"pair {index}") for index, pair in enumerate(usable)]
    decisions = sorted(set(decision_of))
    members = {decision: [i for i, d in enumerate(decision_of) if d == decision] for decision in decisions}
    if len(decisions) < minimum:
        return _not_enough(len(decisions), minimum)
    shared = {name: len({decision_of[i] for i, pair in enumerate(usable)
                         if name in feature_rows[pair["kept"]] and name in feature_rows[pair["over"]]})
              for name in FEATURES}
    names = [name for name in FEATURES if shared[name] >= minimum]
    ids = sorted({pair[side] for pair in usable for side in ("kept", "over")})
    means, stds = {}, {}
    for name in list(names):
        values = np.array([feature_rows[key][name] for key in ids if name in feature_rows[key]])
        if len(values) < 2 or float(values.std()) < 1e-9:
            names.remove(name)
            continue
        means[name], stds[name] = float(values.mean()), float(values.std())
    if not names:
        return _not_enough(len(decisions), minimum, "no measured quantity differs between the sounds compared")

    def z(key):
        row = feature_rows[key]
        return np.array([(row[name] - means[name]) / stds[name] if name in row else 0. for name in names])

    diffs = np.array([z(pair["kept"]) - z(pair["over"]) for pair in usable])
    weights = np.array([float(pair.get("weight", 1.)) for pair in usable])
    w, hessian = _solve(diffs, weights, l2)
    se = np.sqrt(np.diag(np.linalg.inv(hessian)))
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(bootstrap):
        chosen = rng.integers(0, len(decisions), len(decisions))
        pick = np.array([i for d in chosen for i in members[decisions[d]]])
        samples.append(_solve(diffs[pick], weights[pick], l2)[0])
    samples = np.array(samples)
    spread = samples.std(axis=0)
    right = float(np.sum(weights * (diffs @ w > 0)) / weights.sum())

    found = {}
    for i, name in enumerate(names):
        higher = lower = 0
        for decision in decisions:
            # Which way this decision leans on the quantity: its pairs, by their weights.
            lean = 0.
            for index in members[decision]:
                pair = usable[index]
                kept, over = feature_rows[pair["kept"]], feature_rows[pair["over"]]
                if name in kept and name in over and abs(kept[name] - over[name]) > .05 * stds[name]:
                    lean += weights[index] * np.sign(kept[name] - over[name])
            higher += lean > 0
            lower += lean < 0
        boot_z = float(w[i] / spread[i]) if spread[i] > 1e-12 else 0.
        found[name] = {"weight": round(float(w[i]), 4), "se": round(float(se[i]), 4),
                       "spread": round(float(spread[i]), 4),
                       "agree": round(float(np.mean(np.sign(samples[:, i]) == np.sign(w[i]))), 3),
                       "strength": _strength(boot_z, len(names)), "mean": means[name], "std": stds[name],
                       "n": shared[name], "kept_higher": int(higher), "kept_lower": int(lower)}
    return {"version": 1, "enough": True, "message": None, "n": len(decisions), "minimum": minimum, "fitted": _now(),
            "l2": l2, "right": round(right, 3), "features": found,
            "samples": [[round(float(value), 4) for value in row] for row in samples[:KEPT_SAMPLES]],
            "sample_names": names}


def learn(folder, sounds=None, **options):
    """Fit the model from everything in the data folder, keep it in `taste.json`, and return it."""
    model = fit(*evidence(folder, sounds), **options)
    save(folder, model)
    return model


# ---- Saying it ----

STRENGTH_ORDER = {"strong": 0, "moderate": 1, "weak": 2}


def describe(model, *, most=4):
    """What the model found, in sentences that name the measured quantity and count the choices behind it."""
    if not model or not model.get("enough"):
        n, minimum = (model or {}).get("n", 0), (model or {}).get("minimum", MINIMUM)
        reason = (model or {}).get("message") or NOT_ENOUGH
        if reason != NOT_ENOUGH:
            return [f"Of {n} choices, nothing can be said yet: {reason}."]
        return [f"{reason[0].upper()}{reason[1:]}: {n} usable choice{'s' if n != 1 else ''} between measured sounds, "
                f"{minimum} needed before anything is said about your taste."]
    n = model["n"]
    claims = sorted(((name, item) for name, item in model["features"].items() if item["strength"] in STRENGTH_ORDER),
                    key=lambda pair: (STRENGTH_ORDER[pair[1]["strength"]], -abs(pair[1]["weight"])))
    lines = []
    for name, item in claims[:most]:
        _, more, less = FEATURES[name]
        phrase = more if item["weight"] > 0 else less
        agreeing = item["kept_higher"] if item["weight"] > 0 else item["kept_lower"]
        differed = item["kept_higher"] + item["kept_lower"]
        if item["strength"] == "weak" or agreeing * 2 <= differed:
            lines.append(f"Your choices so far lean toward {phrase} (weak evidence).")
            continue
        where = f"Of {differed} choices where it differed" if differed < n else f"Of {n} choices"
        lines.append(f"{where}, you kept the sound with {phrase} {agreeing} times"
                     + (" (moderate evidence)." if item["strength"] == "moderate" else "."))
    if not lines:
        lines.append(f"Of {n} choices, none of the measured quantities predicts which sound you keep yet.")
    return lines


def rank(model, candidates):
    """Candidates (sounds, or heard records) ordered by predicted preference, best first, each as
    {"candidate", "chance", "top"}: `chance` is the predicted probability it is kept over a candidate with the
    average of the measured quantities; `top` the share of bootstrap fits that put it first, how sure the order is.
    Without enough evidence the order is left as given and both are None."""
    candidates = list(candidates)
    if not model or not model.get("enough") or not candidates:
        return [{"candidate": item, "chance": None, "top": None} for item in candidates]
    names = model["sample_names"]
    rows = []
    for item in candidates:
        row = features(_heard_of(item))
        rows.append([(row[name] - model["features"][name]["mean"]) / model["features"][name]["std"]
                     if name in row else 0. for name in names])
    rows = np.array(rows)
    rows = rows - rows.mean(axis=0)
    w = np.array([model["features"][name]["weight"] for name in names])
    scores = rows @ w
    samples = np.array(model.get("samples") or [w])
    best = np.argmax(rows @ samples.T, axis=0)
    top = np.bincount(best, minlength=len(candidates)) / len(samples)
    order = sorted(range(len(candidates)), key=lambda i: -scores[i])
    return [{"candidate": candidates[i], "chance": round(float(1 / (1 + math.exp(-scores[i]))), 3),
             "top": round(float(top[i]), 3)} for i in order]


# ---- Keeping it ----

def save(folder, model):
    path = Path(folder) / MODEL
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    part.write_text(json.dumps(model, indent=1), encoding="utf-8")
    part.replace(path)
    return model


def load(folder):
    """The kept model, or None when there is none or it cannot be read."""
    try:
        model = json.loads((Path(folder) / MODEL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return model if isinstance(model, dict) and "enough" in model else None
