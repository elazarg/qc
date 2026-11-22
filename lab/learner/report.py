"""What the Guitar Learner says about its examples, its checks and an estimate: the words, without the window.

    examples, tests, profile, checks, said = evaluate("Charvel", "dk24_hh_toggle")
    report_score(checks, layout, examples)      # the score line
    headline, details, notes = guess_lines(estimate, layout, examples)

Words used here, and what each stands for:

- "close / near / far match": how near the notes are to the nearest taught example (`configuration`'s "match").
  It is a distance, not a chance of being right, so nothing is called strong or confident.
- "by dry level": a volume reading comes from the level of the dry signal. It holds only for picking as hard as
  in the examples, at the same input gain.
- "from another recording session": a check recorded on another day than every teaching example
  (`dataset.session_of`). Only such a check is called independent.
"""

import numpy as np

from lab.learner import configuration as model, dataset as data

CONTROL_NAMES = {"pickup": "Pickup", "wiring": "Mini-toggle", "tone": "Tone", "volume": "Volume"}
MATCH_NAMES = {"close": "Close match", "near": "Near match", "far": "Far match", "none": "No match"}
VOLUME_NOTE = ("Volume is read from the level of the dry signal: it holds only if you pick as hard as in the "
               "examples, at the same input gain.")


def setting(value):
    """A knob position as it is written: 7, or 7.5."""
    return f"{float(value):g}"


def summarize(layout, values):
    parts = [data.LAYOUTS[layout]["pickups"][values["pickup"] - 1]]
    if values.get("wiring"):
        parts.append(values["wiring"])
    parts.extend((f"Vol {setting(values['volume'])}", f"Tone {setting(values['tone'])}"))
    return "  ·  ".join(parts)


def prediction_value(key, value, layout):
    if value is None:
        return "Uncertain"
    if key == "pickup":
        return f"{value} · {data.LAYOUTS[layout]['pickups'][value - 1]}"
    if key == "wiring":
        return value
    low, high = setting(value["low"]), setting(value["high"])
    return (low if low == high else f"{low}–{high}") + (" by dry level" if key == "volume" else "")


def teaching_hint(layout, examples, current):
    if not examples:
        return "Record a short phrase in a setup you know, then tag it on the board."
    setups = [row["configuration"] for row in examples]
    pickup_pairs = {(row["wiring"], row["pickup"]) for row in setups} if "wiring" in current else {
        (None, row["pickup"]) for row in setups}
    pickup_seen = any(wiring == other and pickup != position for wiring, pickup in pickup_pairs
                      for other, position in pickup_pairs)
    wiring_seen = any(wiring != other and pickup == position for wiring, pickup in pickup_pairs
                      for other, position in pickup_pairs)
    if not pickup_seen:
        return "To teach the blade, record the same notes at another pickup position with the other controls held fixed."
    if "wiring" in data.LAYOUTS[layout] and not wiring_seen:
        return "To teach the mini-toggle, repeat a recording with the same pickup and knobs, then flip only the toggle."
    positions = {(row["tone"], row["volume"]) for row in setups
                 if row["pickup"] == current["pickup"] and row.get("wiring") == current.get("wiring")}
    if len(positions) < 3 or np.linalg.matrix_rank(np.asarray(sorted(positions)) - min(positions), tol=.15) < 2:
        return "For knob estimates here, repeat the same notes while changing just tone or just volume. Keep input gain fixed."
    return "Add another setup, or check a fresh performance, best on another day, in your usual playing style."


def evaluate(guitar, layout):
    """Fit the examples and estimate every check that was never used to teach:
    (examples, checks kept, profile, [(check, estimate)], what went wrong)."""
    examples = data.records(guitar, layout, role="example")
    tests = data.records(guitar, layout, role="test")
    if len(examples) < 2:
        return examples, tests, None, [], ""
    try:
        profile = model.fit(guitar, examples)
    except ValueError as error:
        return examples, tests, None, [], str(error)
    checks = []
    for row in tests:
        try:
            prediction = model.estimate(profile, row["wav"], source_id=row["source_id"])
        except (OSError, ValueError):
            continue
        checks.append((row, prediction))
    return examples, tests, profile, checks, ""


def setup_key(configuration):
    return configuration["pickup"], configuration.get("wiring")


def training_coverage(examples):
    if not examples:
        return ""
    settings = [row["configuration"] for row in examples]
    positions = ", ".join(map(str, sorted({row["pickup"] for row in settings})))
    knobs = []
    for key in ("tone", "volume"):
        values = sorted({row[key] for row in settings})
        knobs.append(f"{key.title()} {setting(values[0])}" if len(values) == 1 else
                     f"{key.title()} {setting(values[0])}–{setting(values[-1])}")
    return f"Taught blade positions {positions}  ·  {'  ·  '.join(knobs)}"


def purpose(label):
    """What a row of the dataset is used for, in the table's words."""
    if label is None:
        return "Untagged"
    if label["role"] == "example":
        return "Teaching example"
    if not label.get("score_eligible", True):
        return "Check · heard in training"
    return "Independent check · another day" if label.get("separate_session") else "Check · same day as teaching"


def report_score(checks, layout, examples):
    """The score line. Every count says what it is out of: a control is right or wrong only where it was
    estimated, and the checks left uncertain are counted beside it."""
    if not checks:
        return ""
    taught_setups = {setup_key(row["configuration"]) for row in examples}
    supported = [(row, guess) for row, guess in checks if setup_key(row["configuration"]) in taught_setups]
    count = len(supported)
    outside = len(checks) - count
    parts = []
    if count:
        separate = sum(bool(row.get("separate_session")) for row, _ in supported)
        parts.append(f"{count} check{'s' if count != 1 else ''} on taught setups, {separate} from another recording "
                     f"session")
    controls = ("pickup", "wiring") if "wiring" in data.LAYOUTS[layout] else ("pickup",)
    for control in controls:
        called = [(row, guess) for row, guess in supported if guess.get(control) is not None]
        if called:
            correct = sum(guess[control] == row["configuration"][control] for row, guess in called)
            parts.append(f"{CONTROL_NAMES[control]} {correct}/{len(called)} correct" + _uncertain(count - len(called)))
        elif count:
            parts.append(f"{CONTROL_NAMES[control]} uncertain")
    for control in ("volume", "tone"):
        estimates = [(abs(guess[control]["value"] - row["configuration"][control]),
                      guess[control]["low"] <= row["configuration"][control] <= guess[control]["high"])
                     for row, guess in supported if guess.get(control) is not None]
        if estimates:
            covered = sum(inside for _, inside in estimates)
            error = float(np.mean([difference for difference, _ in estimates]))
            name = "Volume by dry level" if control == "volume" else CONTROL_NAMES[control]
            parts.append(f"{name} in range {covered}/{len(estimates)}, mean error {error:.1f}"
                         + _uncertain(count - len(estimates)))
    if outside:
        parts.append(f"{outside} outside taught setups")
    return "  ·  ".join(parts)


def _uncertain(count):
    return f", {count} uncertain" if count else ""


def guess_lines(guess, layout, examples, label=None, *, stale=False):
    """An estimate as (headline, what was estimated, the lines of notes under it)."""
    keys = ["pickup", "volume", "tone"]
    if "wiring" in data.LAYOUTS[layout]:
        keys.insert(1, "wiring")
    predicted = [key for key in keys if guess.get(key) is not None]
    held = {key for key in ("volume", "tone") if len({row["configuration"][key] for row in examples}) <= 1}
    present = [f"{CONTROL_NAMES[key]}  {prediction_value(key, guess[key], layout)}" for key in predicted]
    unresolved = [CONTROL_NAMES[key] for key in keys if key not in predicted and key not in held]
    if present and unresolved:
        present.append(f"{' and '.join(unresolved)} uncertain")
    match = MATCH_NAMES.get(guess.get("match"), MATCH_NAMES["none"])
    if stale:
        headline = "Last estimate"
    elif not predicted:
        headline = "Insufficient evidence"
    elif predicted == ["volume"]:
        headline = "Volume read from the dry level"
    elif len(predicted) == 1:
        headline = f"{match} · {CONTROL_NAMES[predicted[0]].lower()}"
    else:
        headline = f"{match} to the taught examples"
    notes = [guess["reason"]]
    if "volume" in predicted:
        notes.append(VOLUME_NOTE)
    if len(predicted) == 1 and (evidence := guess.get("evidence", {}).get(predicted[0])):
        count = len(evidence["notes_used"])
        notes.append(f"{count} comparable pitch{'es' if count != 1 else ''} supported this estimate.")
    if guess.get("nearest") and not predicted:
        nearest = guess["nearest"]
        reference = prediction_value("pickup", nearest["pickup"], layout)
        if nearest.get("wiring"):
            reference += f" · {nearest['wiring']}"
        notes.append(f"Closest taught setup · {reference}")
    if held:
        notes.append(f"{' and '.join(CONTROL_NAMES[key] for key in keys if key in held)} need examples at different settings.")
    if label is not None:
        actual = label["configuration"]
        caption = f"Actual · {summarize(layout, actual)}"
        if setup_key(actual) not in {setup_key(row["configuration"]) for row in examples}:
            caption += " · outside taught setups"
        notes.insert(0, caption)
    return headline, "  ·  ".join(present), notes
