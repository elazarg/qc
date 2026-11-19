"""A round: one request answered with an aim and a few designs, each listened to before it is served.

The assistant states what the sound is like and returns designs that reach it
by different routes. The app builds each one silently, measures the same three
things the aim names, turns its own Shape knobs where a design misses, and
ranks what is left. What the player then says and does goes to the next round.

Run from the repository root:
    python -m tone_lab.rounds "the solo of Pink Floyd's Time"
    python -m tone_lab.rounds --again <round id> "the second is close, brighter"
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sys
import time
from uuid import uuid4

from . import device, draft_render, hearing, iterate, preset_design, session

AIM = preset_design.AIM
WEIGHT = {"drive": 2, "tone": 1, "space": 1}  # A sound at the wrong amount of drive is the wrong sound.
# The Shape knob that moves each measure, and what to try when a design falls short of the aim or overshoots it.
REMEDY = {"drive": ("drive", (.6, 1.), (-.4, -.8)),
          "tone": ("warmth", (-.4, -.8), (.4, .8)),
          "space": ("space", (.4, .8), (-.4, -.8))}
SHORT = {"drive": "less driven than the aim", "tone": "darker than the aim", "space": "drier than the aim"}
OVER = {"drive": "more driven than the aim", "tone": "brighter than the aim", "space": "wetter than the aim"}
EXTRA_LISTENS = 3  # per design, so that checking a round stays near two minutes


def folder():
    return session.SESSIONS / "rounds"


def miss(aim, heard):
    """How many words each measure of a heard design sits from the aim. Negative is short of it."""
    return {kind: AIM[kind].index(heard[kind]) - AIM[kind].index(aim[kind]) for kind in AIM}


def distance(steps):
    return sum(WEIGHT[kind] * abs(steps[kind]) for kind in AIM)


def tags(aim, heard):
    """What the app would say of a design it has listened to: the three words, then where it misses."""
    steps = miss(aim, heard)
    missed = [(SHORT if steps[kind] < 0 else OVER)[kind] for kind in AIM if steps[kind]]
    return [heard[kind] for kind in AIM] + (missed or ["on the aim"])


def save(found):
    folder().mkdir(parents=True, exist_ok=True)
    session.write_json(folder() / f"{found['id']}.json", found)
    return found


def load(round_id):
    return json.loads((folder() / f"{round_id}.json").read_text(encoding="utf-8"))


def draft_of(candidate):
    data = json.loads((session.SESSIONS / "drafts" / f"{candidate['draft']}.json").read_text(encoding="utf-8"))
    return {**data, **preset_design.validate(data)}


def start(text, guitar, take=None, *, earlier=None, research=False, cancel=None, parent=None):
    """Ask for an aim and designs. Nothing is built yet."""
    aim, designs = preset_design.design_round(text, guitar, take or {}, earlier=earlier, research=research,
                                              cancel=cancel)
    return save({"id": uuid4().hex[:12], "created_utc": datetime.now(timezone.utc).isoformat(), "request": text,
                 "guitar": guitar, "aim": aim, "parent": parent, "candidates": [
                     {"draft": draft["id"], "name": draft["name"], "differs": differs, "heard": None, "shape": {},
                      "served": None, "tags": [], "user_tags": [], "review": "", "played": False}
                     for draft, differs in designs]})


@contextmanager
def unit(dry=None):
    """Hold the working slot and return `hear(draft, values)`, which builds a draft once and listens to it."""
    from . import live

    dry = hearing.phrase()[1] if dry is None else dry
    with device.connect(write=True) as qc:
        offset = iterate.calibration_offset(qc)
        with draft_render.working_grid(qc):
            built = {}

            def hear(draft, values):
                if built.get("id") != draft["id"]:
                    chain = draft_render.compile_chain(draft, draft_render.PREVIEW_GAIN)
                    base = draft_render.build_grid(qc, device.read_preset(qc), chain, draft)
                    built.update(id=draft["id"], base=base, moved={})
                if values or built["moved"]:
                    built["moved"] = live.update(qc, built["base"], 0, values, built["moved"],
                                                 position=draft_render.WORK_SLOT)
                    time.sleep(.25)
                return hearing.proof(qc, dry, offset).to_record()

            yield hear


def check_one(aim, candidate, draft, hear):
    """Listen to one design, and turn the app's own knobs where it misses the aim."""
    heard = hear(draft, {})
    best = ({}, heard)
    listens = 0
    for kind in AIM:
        steps = miss(aim, best[1])[kind]
        if not steps:
            continue
        knob, short, over = REMEDY[kind]
        moves = short if steps < 0 else over
        # A design two or more words from the aim gets the large move first. That is usually the one needed.
        for value in (moves[::-1] if abs(steps) >= 2 else moves):
            if listens >= EXTRA_LISTENS:
                break
            listens += 1
            values = {**best[0], knob: value}
            tried = hear(draft, values)
            if distance(miss(aim, tried)) < distance(miss(aim, best[1])):
                best = (values, tried)
            if not miss(aim, best[1])[kind]:
                break
    if best[0]:
        hear(draft, best[0])  # Leave the unit as the design will be served.
    candidate.update(heard=heard, shape=best[0], served=best[1], tags=tags(aim, best[1]),
                     miss=distance(miss(aim, best[1])))
    return candidate


def check(found, hear=None, report=None):
    """Listen to every design of a round that has not been listened to, then put the closest first."""
    def run(hear):
        for candidate in found["candidates"]:
            if candidate["heard"] is None:
                check_one(found["aim"], candidate, draft_of(candidate), hear)
                save(found)
                if report:
                    report(candidate)

    if hear is None:
        with unit() as hear:
            run(hear)
    else:
        run(hear)
    # A design the player has heard keeps its place. The rest are ordered by how near they came.
    found["candidates"].sort(key=lambda candidate: (not candidate["played"], candidate.get("miss", 99)))
    return save(found)


def summary(found):
    """What a later round is told about this one."""
    def design(candidate):
        draft = draft_of(candidate)
        models = preset_design.choices()
        blocks = [{"model": models[block["model_id"]]["name"],
                   "settings": {setting["name"]: setting["value"] for setting in block["settings"]}}
                  for path in ("chain", "parallel") for block in draft.get(path) or [] if not block.get("reserved")]
        return {"name": candidate["name"], "differs": candidate["differs"], "blocks": blocks,
                "measured_as_designed": candidate["heard"] and {kind: candidate["heard"][kind] for kind in AIM},
                "knobs_moved": candidate["shape"],
                "measured_with_those_knobs": candidate["served"] and {kind: candidate["served"][kind] for kind in AIM},
                "player_heard_it": candidate["played"], "player_said": candidate["review"],
                "player_tags": candidate["user_tags"]}

    return {"aim": found["aim"], "designs": [design(candidate) for candidate in found["candidates"]]}


def again(found, remark="", *, research=True, cancel=None):
    """The next round for the same request, told what the earlier ones came to."""
    earlier, node = [], found
    while node:
        earlier.insert(0, summary(node))
        node = load(node["parent"]) if node.get("parent") else None
    text = found["request"] if not remark else f"{found['request']}\nThe player now says: {remark}"
    return start(text, found["guitar"], earlier=earlier[-3:], research=research, cancel=cancel, parent=found["id"])


def show(found, out=print):
    aim = found["aim"]
    out(f"Round {found['id']} · {found['request'].splitlines()[0]}")
    out(f"  Aiming for: {aim['drive']}, {aim['tone']}, {aim['space']}. {aim['note']}")
    for place, candidate in enumerate(found["candidates"], 1):
        out(f"  {place}. {candidate['name']} — {candidate['differs']}")
        if candidate["heard"]:
            first = ", ".join(candidate["heard"][kind] for kind in AIM)
            knobs = ", ".join(f"{name} {value:+g}" for name, value in candidate["shape"].items())
            out(f"       as designed: {first}" + (f"   → with {knobs}: " + ", ".join(
                candidate["served"][kind] for kind in AIM) if knobs else ""))
            out(f"       {' · '.join(candidate['tags'][3:])}")
        out(f"       {preset_design.chain_text(draft_of(candidate))}")


def main(arguments):
    started = time.monotonic()
    if arguments[:1] == ["--again"]:
        found = again(load(arguments[1]), " ".join(arguments[2:]))
    else:
        found = start(" ".join(arguments), "Charvel")
    print(f"designed in {time.monotonic() - started:.0f} s", flush=True)
    started = time.monotonic()
    check(found, report=lambda candidate: print(f"  listened to {candidate['name']}", flush=True))
    print(f"listened in {time.monotonic() - started:.0f} s")
    show(found)


if __name__ == "__main__":
    main(sys.argv[1:])
