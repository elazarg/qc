"""What the desk does, without the window: one state, and the actions that change it.

The desk shows two sounds, A and B, and plays one of them from the guitar or
from a recorded phrase. The player asks for things in words: a remark about
the sound moves the Shape knobs at once; any other request for the sound on
B revises it; a request for a new sound starts a round of three designs, the
first of which goes on B. A preset saved on the unit can be brought in to
work on, and the result saved back to the Tone Lab setlist.

All state is in `Studio.state`, replaced, never changed in place, so that
the window can draw it and undo can keep it. Work that takes time (anything
on the unit, any call to the assistant) runs elsewhere and its result is
handled here, on the window's thread, by `poll`. A result is applied only if
what it was for is still what is shown: an answer for a sound that has since
left B is offered, not applied.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
from pathlib import Path

from . import design, knobs, knowledge, words
from .assistant import Assistant
from .sounds import Sound, Store, describe


@dataclass(frozen=True)
class State:
    a: str | None = None              # sound ids
    b: str | None = None
    side: str = "b"
    mode: str | None = None           # None, "guitar" or "phrase"
    shown: str | None = None          # "pair" or "recall" once A and B are ready on the unit
    round: str | None = None          # the round on show
    status: str = ""
    working: str = ""                 # what is being done, while it is
    instrument: str = "Charvel"
    song: str | None = None
    revision: int = 0                 # bumped whenever A or B changes


class Studio:
    def __init__(self, host, folder=None, *, assistant=None, phrase=None):
        self.folder = Path(folder or knowledge.home())
        self.store = Store(self.folder)
        self.host = host
        self.assistant = assistant or Assistant(self.folder)
        self.thinking = ThreadPoolExecutor(max_workers=2, thread_name_prefix="assistant")
        self.state = State()
        self.history = []                 # earlier states, for undo
        self.jobs = []                    # (future, done, failed)
        self.phrase = phrase              # (name, mono dry audio) or None
        self.listeners = []

    # ---- state ----

    def set(self, **changes):
        self.state = replace(self.state, **changes)
        for listener in self.listeners:
            listener(self.state)

    def say(self, text):
        self.set(status=text)

    def sound(self, side):
        sound_id = getattr(self.state, side)
        return self.store.sound(sound_id) if sound_id else None

    def checkpoint(self):
        self.history = (self.history + [self.state])[-40:]

    def undo(self):
        if not self.history:
            self.say("Nothing to undo.")
            return
        before = self.history.pop()
        self.state = before
        self._show()
        self.say("Undone.")

    # ---- work done elsewhere ----

    def later(self, future, done, failed=None):
        self.jobs.append((future, done, failed))

    def poll(self):
        """Handle every finished piece of work. Called from the window's clock."""
        finished = [job for job in self.jobs if job[0].done()]
        self.jobs = [job for job in self.jobs if not job[0].done()]
        for future, done, failed in finished:
            try:
                result = future.result()
            except Exception as error:  # noqa: BLE001
                (failed or (lambda error: self.set(status=str(error), working="")))(error)
                continue
            done(result)

    # ---- the session ----

    def open(self):
        self.set(working="Connecting to the Quad Cortex…")

        def opened(result):
            stale = knowledge.stale(getattr(self.host, "firmware", None) or "4.1.1")
            note = " Gave back the preset a crash had left borrowed." if result.get("recovered") else ""
            self.set(working="", status="Ready." + note + (" Some measured tables were made on another firmware."
                                                            if stale else ""))

        self.later(self.host.call("open"), opened)

    def put(self, side, sound):
        """Put a sound on A or B, and get both ready on the unit."""
        self.checkpoint()
        self.store.save(sound)
        self.set(**{side: sound.id, "side": side, "revision": self.state.revision + 1})
        self._show()

    def _show(self):
        a, b = self.sound("a"), self.sound("b")
        if a is None and b is None:
            return
        revision = self.state.revision
        self.set(working="Getting A and B ready on the Quad Cortex…", shown=None)

        def shown(how):
            if self.state.revision != revision:
                return
            self.set(working="", shown=how)
            if self.state.mode:
                self.play(self.state.mode)
            self.listen_when_free()

        self.later(self.host.call("show", a.chain if a else None, b.chain if b else None), shown)

    def play(self, mode, side=None):
        side = side or self.state.side
        if self.state.shown is None:
            self.set(mode=mode, side=side, status="Getting ready. It plays as soon as it is.")
            return
        sound = self.sound(side)
        if sound is None:
            self.say(f"Nothing is on {side.upper()} yet.")
            return
        if mode == "phrase" and self.phrase is None:
            self.say("Record or choose a phrase first.")
            return
        phrase = self.phrase[1] if mode == "phrase" else None
        self.set(mode=mode, side=side)

        def playing(result):
            sound = self.sound(side)
            if sound and not sound.played:
                sound.played = True
                self.store.save(sound)
            if sound and sound.knobs:
                self.later(self.host.call("turn", side, sound.knobs), lambda moves: None)
            self.say(f"{side.upper()} · {sound.name} · {mode}")

        self.later(self.host.call("play", side, mode, phrase), playing)

    def select(self, side):
        if self.state.mode:
            self.play(self.state.mode, side)
        else:
            self.set(side=side)

    def stop(self):
        self.host.stop()
        self.later(self.host.call("quiet"), lambda result: None)
        self.set(mode=None, status="Stopped.")

    def close(self):
        self.host.close()
        self.thinking.shutdown(wait=False, cancel_futures=True)

    # ---- shaping ----

    def turn(self, values, side=None):
        side = side or self.state.side
        sound = self.sound(side)
        if sound is None:
            return
        self.checkpoint()
        sound.knobs = {key: value for key, value in values.items() if value}
        self.store.save(sound)
        if self.state.shown:
            self.later(self.host.call("turn", side, sound.knobs), lambda moves: None)

    def available(self, side=None):
        sound = self.sound(side or self.state.side)
        return knobs.available(sound.chain) if sound else []

    # ---- asking ----

    def ask(self, text):
        text = text.strip()
        if not text:
            return
        side = self.state.side
        sound = self.sound(side)
        reading = words.read(text)
        moves = None
        if reading and sound:
            ways = words.usable(reading, set(knobs.available(sound.chain)))
            if ways:
                moves = dict(ways[0].moves)
                said = f"{reading.said}: {ways[0].say}."
        if moves is None and sound:
            moves = knobs.direction(text)
            said = text
        if moves and sound:
            values = dict(sound.knobs)
            for key, step in moves.items():
                low = knobs.KNOBS[key][1]
                values[key] = round(max(low, min(1., values.get(key, 0.) + step)), 2)
            self.turn(values, side)
            sound.notes.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "text": text})
            self.store.save(sound)
            self.say(said)
            return
        b = self.sound("b")
        if b is not None and not _wants_new(text):
            self.revise(b, text)
        else:
            self.new_round(text)

    def _instrument(self):
        return self.store.instrument(self.state.instrument)

    def new_round(self, text, *, earlier=None, by="player"):
        self.set(working="Designing three ways to get there. About a minute…")
        instrument = self._instrument()
        future = self.thinking.submit(design.round_of_three, self.assistant, text, instrument, earlier=earlier, by=by)
        revision = self.state.revision

        def arrived(result):
            aim, designs = result
            record = {"id": knowledge_id(), "request": text, "aim": aim, "created": _now(),
                      "candidates": []}
            for sound, differs in designs:
                sound.tags = [f"aim: {aim['drive']}, {aim['tone']}, {aim['space']}"]
                if self.state.song:
                    sound.origin = {**sound.origin, "song": self.state.song}
                self.store.save(sound)
                record["candidates"].append({"sound": sound.id, "differs": differs})
            self._save_round(record)
            self.set(working="", round=record["id"])
            if self.state.revision == revision:
                self.put("b", self.store.sound(record["candidates"][0]["sound"]))
                self.say(f"Aiming for {aim['drive']}, {aim['tone']}, {aim['space']}. "
                         f"1 of {len(designs)} is on B · {designs[0][0].name}")
            else:
                self.say(f"{len(designs)} designs are ready. B changed meanwhile, so it was left alone.")

        self.later(future, arrived)

    def revise(self, sound, text):
        self.set(working=f"Revising {sound.name}…")
        future = self.thinking.submit(design.revise, self.assistant, sound, text, self._instrument())
        revision = self.state.revision

        def revised(result):
            self.set(working="")
            result.notes.append({"at": _now(), "text": text})
            self.store.save(result)
            if self.state.revision == revision:
                self.put("b", result)
                self.say(f"{result.name}, revised: {text}")
            else:
                self.say(f"The revision of {sound.name} is ready in History. B changed meanwhile.")

        self.later(future, revised)

    # ---- rounds ----

    def _save_round(self, record):
        folder = self.folder / "rounds"
        folder.mkdir(exist_ok=True)
        (folder / f"{record['id']}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")

    def round(self, round_id=None):
        round_id = round_id or self.state.round
        if not round_id:
            return None
        try:
            return json.loads((self.folder / "rounds" / f"{round_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def step(self, place):
        found = self.round()
        if found and 0 <= place < len(found["candidates"]):
            self.put("b", self.store.sound(found["candidates"][place]["sound"]))

    def listen_when_free(self):
        """Measure the next design of the round nobody has measured, while the unit is idle."""
        found = self.round()
        if not found or self.state.mode or self.host.busy or self.phrase is None:
            return
        for candidate in found["candidates"]:
            sound = self.store.sound(candidate["sound"])
            if sound.heard is None:
                self.set(working=f"Listening to {sound.name}, silently…")

                def heard(result, sound=sound):
                    sound.heard = {"phrase": self.phrase[0], "digest": sound.chain.digest(), **result.to_record()}
                    self.store.save(sound)
                    self.set(working="")
                    self._show() if self.state.shown is None else None
                    self.listen_when_free()

                self.later(self.host.call("hear", sound.chain, self.phrase[1]), heard)
                return

    # ---- presets on the unit ----

    def work_on(self, slot, name):
        """Bring a preset saved on the unit in: it goes on A as it is, and a copy on B to change."""
        self.set(working=f"Reading {name}…")

        def read(result):
            chain, held_name = result
            original = Sound(held_name, chain, {"kind": "preset", "slot": slot})
            copy = Sound(held_name, chain, {"kind": "revised", "from": original.id}, parent=original.id)
            self.store.save(original)
            self.store.save(copy)
            self.checkpoint()
            self.set(a=original.id, b=copy.id, side="b", working="", revision=self.state.revision + 1,
                     status=f"{held_name} is on A as saved, and on B to work on.")
            self._show()

        self.later(self.host.call("read", slot), read)

    def save(self, name=None):
        sound = self.sound(self.state.side)
        if sound is None:
            return
        name = (name or sound.name)[:24]
        self.stop()
        self.set(working=f"Saving {name} to the Tone Lab setlist…")
        from .knobs import apply as with_knobs

        chain = with_knobs(sound.chain, sound.knobs) if sound.knobs else sound.chain

        def saved(result):
            slot, _ = result
            sound.saved = {"slot": slot, "name": name}
            self.store.save(sound)
            self.set(working="", shown=None, status=f"Saved · Tone Lab {slot['position'] + 1} · {name}")
            self._show()

        self.later(self.host.call("save", chain, name), saved)

    # ---- songs ----

    def add_part(self, part):
        if not self.state.song:
            self.say("Name the song first.")
            return
        song = self.store.song(self.state.song)
        song["instrument"] = self.state.instrument
        sound = self.sound(self.state.side)
        song["parts"] = [item for item in song["parts"] if item["name"] != part] + [{"name": part, "sound": sound.id}]
        self.store.save_song(song)
        self.say(f"{sound.name} is the {part} of {song['name']}.")

    def chain_text(self, side=None):
        sound = self.sound(side or self.state.side)
        return describe(sound.chain) if sound else []


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def knowledge_id():
    from .sounds import new_id

    return new_id()


NEW = ("make", "new sound", "build", "design", "give me", "something like", "i want a", "preset for", "tone for")


def _wants_new(text):
    lowered = text.casefold()
    return any(lowered.startswith(word) or f" {word} " in f" {lowered} " for word in NEW) or " - " in text
