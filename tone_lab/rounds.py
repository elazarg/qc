"""A round: one request answered with an aim and a few designs, each listened to before it is served.

The assistant states what the sound is like and returns designs that reach it
by different routes. The app builds each one silently, measures the same three
things the aim names, turns its own Shape knobs where a design misses, and
ranks what is left. What the player then says and does goes to the next round.

Every design of a round is listened to on the same phrase, which the round
records. Listening for the desk runs in a process of its own (`audition`), so
that a driver failure cannot take the desk down and the playing preset can be
recovered, and it starts only when the unit is where Tone Lab left it and the
guitar is silent.

Run from the repository root:
    python -m tone_lab.rounds "the solo of Pink Floyd's Time"
    python -m tone_lab.rounds --again <round id> "the second is close, brighter"
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

from . import audio, device, diagnostics, draft_render, hearing, iterate, preset_design, session
from . import models as catalog

AIM = preset_design.AIM
WEIGHT = {"drive": 2, "tone": 1, "space": 1}  # A sound at the wrong amount of drive is the wrong sound.
# The Shape knob that moves each measure, and what to try when a design falls short of the aim or overshoots it.
REMEDY = {"drive": ("drive", (.6, 1.), (-.4, -.8)),
          "tone": ("warmth", (-.4, -.8), (.4, .8)),
          "space": ("space", (.4, .8), (-.4, -.8))}
SHORT = {"drive": "less driven than the aim", "tone": "darker than the aim", "space": "drier than the aim"}
OVER = {"drive": "more driven than the aim", "tone": "brighter than the aim", "space": "wetter than the aim"}
EXTRA_LISTENS = 3  # per design, so that checking a round stays near two minutes
PLAYING = 10 ** (-50 / 20)  # A dry input peak above this means somebody is playing the guitar.


class Stopped(Exception):
    """The player wants the unit back. What was finished is kept and the rest waits."""


class Busy(Exception):
    """The unit is not where Tone Lab left it, or the guitar is sounding. It is left alone."""


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


def everything():
    """Every round on disk, oldest first."""
    found = []
    for path in folder().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["id"] == path.stem and data["candidates"] and all(kind in data["aim"] for kind in AIM):
                found.append(data)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(found, key=lambda data: data["created_utc"])


def unchecked(found):
    """The designs still to be listened to. One that could not be listened to is not tried again."""
    return [candidate for candidate in found["candidates"]
            if candidate["heard"] is None and not candidate.get("failed")]


def draft_of(candidate):
    data = json.loads((session.SESSIONS / "drafts" / f"{candidate['draft']}.json").read_text(encoding="utf-8"))
    return {**data, **preset_design.validate(data)}


def start(text, guitar, take=None, *, earlier=None, research=False, cancel=None, parent=None, phrase=None):
    """Ask for an aim and designs. Nothing is built yet.

    `phrase` is the name of the take every design of the round will be listened to on.
    """
    aim, designs = preset_design.design_round(text, guitar, take or {}, earlier=earlier, research=research,
                                              cancel=cancel)
    return save({"id": uuid4().hex[:12], "created_utc": datetime.now(timezone.utc).isoformat(), "request": text,
                 "guitar": guitar, "aim": aim, "parent": parent, "phrase": phrase, "candidates": [
                     {"draft": draft["id"], "name": draft["name"], "differs": differs, "heard": None, "shape": {},
                      "served": None, "served_shape": None, "tags": [], "user_tags": [], "review": "",
                      "played": False}
                     for draft, differs in designs]})


def idle(qc, expect, physical_input):
    """Raise Busy unless the unit is where Tone Lab last saw it and nobody is playing the guitar."""
    if expect and device.live_position(qc) != expect:
        raise Busy("Another preset is selected on the Quad Cortex.")
    channel = device.recording_channels_from(qc, physical_input)[0]
    if float(abs(audio.record_di(.6, channel)).max()) > PLAYING:
        raise Busy("The guitar is being played.")


@contextmanager
def unit(dry, *, stop=None, expect=None, physical_input=None, marker=None):
    """Hold the working slot and return `hear(draft, values)`, which builds a draft once and listens to it.

    Setting `stop` makes the next step raise Stopped and ends a replay in
    progress, so that the player never waits long for the unit. `marker` is a
    file that says where to return to if this process dies holding the unit.
    """
    from . import live

    def wanted():
        if stop is not None and stop.is_set():
            raise Stopped()

    wanted()
    with device.connect(write=True) as qc:
        if physical_input is not None:
            idle(qc, expect, physical_input)
        wanted()
        offset = iterate.calibration_offset(qc)
        if marker is not None:
            session.write_json(marker, {"event": "restore", "position": device.live_position(qc),
                                        "name": device.read_preset(qc).name, "scene": device.active_scene(qc),
                                        "fixtures": [draft_render.WORK_SLOT]})
        try:
            with draft_render.working_grid(qc):
                built = {}

                def hear(draft, values):
                    wanted()
                    try:
                        if built.get("id") != draft["id"]:
                            chain = draft_render.compile_chain(draft, draft_render.PREVIEW_GAIN)
                            built.clear()  # A build that is abandoned leaves nothing to listen to.
                            base = draft_render.build_grid(qc, device.read_preset(qc), chain, draft, wanted=wanted)
                            built.update(id=draft["id"], base=base, moved={})
                            wanted()
                        if values or built["moved"]:
                            built["moved"] = live.update(qc, built["base"], 0, values, built["moved"],
                                                         position=draft_render.WORK_SLOT)
                            time.sleep(.25)
                        return hearing.proof(qc, dry, offset, cancel=stop).to_record()
                    except (audio.Cancelled, hearing.Moved) as error:
                        # The player stopped it, or selected another preset. Either way the unit is theirs.
                        raise Stopped() from error

                yield hear
        finally:
            if marker is not None:
                Path(marker).unlink(missing_ok=True)


def check_one(aim, candidate, draft, hear):
    """Listen to one design, and turn the app's own knobs where it misses the aim.

    A design the player has already heard is theirs: it is listened to as
    designed and as they left it, and its knobs are not touched. What was
    measured is recorded with the knob positions it was measured at.
    """
    heard = hear(draft, {})
    if candidate["played"]:
        shape = dict(candidate["shape"])
        served = hear(draft, shape) if shape else heard
        candidate.update(heard=heard, served=served, served_shape=shape, tags=tags(aim, served),
                         miss=distance(miss(aim, served)))
        return candidate
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
    candidate.update(heard=heard, shape=best[0], served=best[1], served_shape=dict(best[0]),
                     tags=tags(aim, best[1]), miss=distance(miss(aim, best[1])))
    return candidate


def check(found, hear=None, report=None, *, stop=None, reorder=True, persist=True, expect=None,
          physical_input=None, marker=None):
    """Listen to every design of a round that has not been listened to, then put the closest first.

    On the desk the designs keep the places the player has seen them in, so
    `reorder` is off there, and the desk is the only writer of a round, so
    `persist` is off too. A design that cannot be listened to is marked and
    the others still are.
    """
    keep = save if persist else (lambda found: found)

    def run(hear):
        for candidate in unchecked(found):
            progress = dict(candidate)
            try:
                check_one(found["aim"], progress, draft_of(candidate), hear)
            except Stopped:
                raise
            except (RuntimeError, ValueError, OSError) as error:
                diagnostics.LOG.info("A design could not be listened to: %s", error)
                progress = {**candidate, "failed": str(error)}
            candidate.update(progress)  # Only a design that was listened to in full is marked as heard.
            keep(found)
            if report:
                report(candidate)

    try:
        if hear is None:
            # One phrase for the whole round, so that its designs can be compared.
            name, dry = hearing.phrase(found.get("phrase"))
            found["phrase"] = name
            with unit(dry, stop=stop, expect=expect, physical_input=physical_input, marker=marker) as hear:
                run(hear)
        else:
            run(hear)
    except Stopped:
        return keep(found)
    if reorder:
        # A design the player has heard keeps its place. The rest are ordered by how near they came.
        found["candidates"].sort(key=lambda candidate: (not candidate["played"], candidate.get("miss", 99)))
    return keep(found)


def closest(found):
    """The place of the design that came nearest the aim, once every design has been listened to."""
    measured = [place for place, candidate in enumerate(found["candidates"]) if "miss" in candidate]
    if unchecked(found) or not measured:
        return None
    return min(measured, key=lambda place: found["candidates"][place]["miss"])


def wants_more(found):
    """Whether the next three should be prepared without being asked for.

    Only once per request, and only when there is something to learn from: a
    design that missed the aim, or anything the player has said.
    """
    said = any(candidate["review"] or candidate["user_tags"] for candidate in found["candidates"])
    missed = any(candidate.get("miss") for candidate in found["candidates"])
    return not found.get("parent") and not found.get("followed") and not unchecked(found) and (said or missed)


def summary(found):
    """What a later round is told about this one."""
    def named(block):
        asset = preset_design.asset_attachment(block)
        info = catalog.lookup(block["model_id"])
        return asset.name if asset else info["name"] if info else str(block["model_id"])

    def design(candidate):
        draft = draft_of(candidate)
        blocks = [{"model": named(block),
                   "settings": {setting["name"]: setting["value"] for setting in block["settings"]}}
                  for path in ("chain", "parallel") for block in draft.get(path) or [] if not block.get("reserved")]
        # Tags describe the knob positions they were measured at. If the player has moved the knobs since,
        # the measurement is given with its own positions and theirs are given beside it.
        return {"name": candidate["name"], "differs": candidate["differs"], "blocks": blocks,
                "measured_as_designed": candidate["heard"] and {kind: candidate["heard"][kind] for kind in AIM},
                "knobs_when_measured": candidate.get("served_shape"),
                "measured_with_those_knobs": candidate["served"] and {kind: candidate["served"][kind] for kind in AIM},
                "knobs_now": candidate["shape"],
                "could_not_be_measured": candidate.get("failed"),
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
    return start(text, found["guitar"], earlier=earlier[-3:], research=research, cancel=cancel, parent=found["id"],
                 phrase=found.get("phrase"))


# ---- Listening in a process of its own ----

def audition(found, stop, *, expect=None, physical_input=1):
    """Listen to a round's unchecked designs in a child process. Returns (round, status, message).

    The status is "done", "stopped", "busy" when the unit was in use and was
    left alone, or "error". Nothing is written to the round on disk: the
    caller merges what was measured. If the child dies, the preset that was
    playing is recalled.
    """
    from . import live_client, live_worker

    with TemporaryDirectory(prefix="tone-audition-") as directory:
        job, result, halt = (Path(directory) / name for name in ("job.json", "result.json", "stop"))
        job.write_text(json.dumps({"round": found, "expect": expect, "input": physical_input,
                                   "result": str(result), "stop": str(halt),
                                   "marker": str(live_worker.RECOVERY)}), encoding="utf-8")
        command = [live_client.worker_command()[0], "-m", "tone_lab.rounds", "--audition", str(job)]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, encoding="utf-8",
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            while process.poll() is None:
                if stop is not None and stop.is_set() and not halt.exists():
                    halt.write_text("stop", encoding="utf-8")
                time.sleep(.05)
        finally:
            if process.poll() is None:
                process.kill()
        if process.returncode == 0 and result.is_file():
            reply = json.loads(result.read_text(encoding="utf-8"))
            return reply["round"], reply["status"], reply.get("message", "")
        diagnostics.LOG.error("The audition process failed: %s", (process.stderr.read() or "")[-600:])
        recovery = subprocess.run(live_client.worker_command() + ["--recover"], capture_output=True,
                                  encoding="utf-8", timeout=20,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        note = "" if recovery.returncode == 0 else " Check the preset selected on the Quad Cortex."
        return found, "error", "The listening process stopped unexpectedly." + note


class _File:
    """A stop signal another process raises by creating a file."""

    def __init__(self, path):
        self.path = Path(path)

    def is_set(self):
        return self.path.exists()


def _audition(job_file):
    job = json.loads(Path(job_file).read_text(encoding="utf-8"))
    found, status, message = job["round"], "done", ""
    stop = _File(job["stop"])
    try:
        check(found, stop=stop, reorder=False, persist=False, expect=job["expect"],
              physical_input=job["input"], marker=job["marker"])
        if stop.is_set():
            status = "stopped"
    except Busy as error:
        status, message = "busy", str(error)
    except Exception as error:  # noqa: BLE001
        diagnostics.LOG.exception("Listening to the designs failed")
        status, message = "error", str(error)
    Path(job["result"]).write_text(json.dumps({"round": found, "status": status, "message": message}),
                                   encoding="utf-8")


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
        elif candidate.get("failed"):
            out(f"       could not be listened to: {candidate['failed']}")
        out(f"       {preset_design.chain_text(draft_of(candidate))}")


def main(arguments):
    if arguments[:1] == ["--audition"]:
        diagnostics.configure()
        _audition(arguments[1])
        return
    started = time.monotonic()
    if arguments[:1] == ["--again"]:
        found = again(load(arguments[1]), " ".join(arguments[2:]))
    else:
        found = start(" ".join(arguments), "Charvel")
    print(f"designed in {time.monotonic() - started:.0f} s", flush=True)
    started = time.monotonic()
    check(found, report=lambda candidate: print(f"  listened to {candidate['name']}", flush=True))
    print(f"listened in {time.monotonic() - started:.0f} s on {found['phrase']}")
    show(found)


if __name__ == "__main__":
    main(sys.argv[1:])
