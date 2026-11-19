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
    blind: bool = False               # names hidden while comparing; A and B may have been swapped
    scene: int = 0                    # the scene heard, 0 to 7


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
        self.preparing = None             # the round whose next three are being designed
        self.remark = None                # (text, sound id, remedies, the one applied) of the last remark
        self.cancel_signal = None         # set to drop the request the assistant is working on
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
        # The player's presets, for the Sounds panel and for requests that name one: read again when the last
        # read is over half a day old (it holds the unit about ten seconds). Quiet if it fails.
        import time

        listing = self.folder / "directory.json"
        if not listing.exists() or time.time() - listing.stat().st_mtime > 12 * 3600:
            self.later(self.host.call("lab.library.directory", folder=str(self.folder)), lambda entries: None,
                       lambda error: None)

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

        self._match_levels()

        def playing(result):
            sound = self.sound(side)
            if sound and not sound.played:
                sound.played = True
                self.store.save(sound)
            if sound and sound.knobs:
                self.later(self.host.call("turn", side, sound.knobs), lambda moves: None)
            self.say(f"{side.upper()} · {sound.name} · {mode}")

        self.later(self.host.call("play", side, mode, phrase), playing)

    def _match_levels(self):
        """Play A and B equally loud, when both have been measured: the louder is turned down to the quieter."""
        from .engine import PREVIEW_DB

        sounds = {side: self.sound(side) for side in ("a", "b")}
        levels = {side: sound.heard["level_db"] for side, sound in sounds.items()
                  if sound and sound.heard and sound.heard.get("level_db", -200) > -90}
        if len(levels) < 2:
            return
        quietest = min(levels.values())
        for side, level in levels.items():
            self.later(self.host.call("level", side, PREVIEW_DB + quietest - level), lambda result: None)

    def scene(self, scene):
        """Hear another scene of the sounds on show."""
        self.set(scene=scene)
        sound = self.sound(self.state.side)
        label = sound.chain.labels[scene] if sound and scene < len(sound.chain.labels) else ""
        self.later(self.host.call("scene", scene), lambda result: None)
        self.say(f"Scene {'ABCDEFGH'[scene]}" + (f" · {label}" if label else ""))

    def scenes(self, side=None):
        """The scenes of the sound on a side that make a difference, as (index, label)."""
        sound = self.sound(side or self.state.side)
        if sound is None:
            return []
        count = sound.chain.scenes_in_use()
        return [(scene, sound.chain.labels[scene]) for scene in range(count)] if count > 1 else []

    def cancel(self):
        """Drop the request the assistant is working on; its answer, if it still comes, is ignored."""
        if self.cancel_signal is None or self.cancel_signal.is_set():
            return False
        self.cancel_signal.set()
        self.set(working="", status="Request cancelled.")
        return True

    def _request(self):
        from threading import Event

        self.cancel_signal = Event()
        return self.cancel_signal

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
            again = self.remark is not None and self.remark[:2] == (text.casefold(), sound.id)
            turn = self.remark[3] + 1 if again else 0
            if again and turn < len(ways):
                # Said again: the first remedy was not it. Take it back and try the next.
                values = dict(sound.knobs)
                for key, step in ways[turn - 1].moves.items():
                    values[key] = round(values.get(key, 0.) - step, 2)
                sound.knobs = {key: value for key, value in values.items() if value}
            if ways and turn < len(ways):
                moves = dict(ways[turn].moves)
                self.remark = (text.casefold(), sound.id, ways, turn)
                following = f" Say it again for: {ways[turn + 1].say}." if turn + 1 < len(ways) else ""
                said = f"{reading.said}: {ways[turn].say}.{following}"
        if moves is None and sound:
            moves = knobs.direction(text)
            said = text
        if moves and sound:
            values = dict(sound.knobs)
            for key, step in moves.items():
                low = knobs.KNOBS[key][1]
                values[key] = round(max(low, min(1., values.get(key, 0.) + step)), 2)
            self.turn(values, side)
            sound = self.sound(side)          # as turn left it
            sound.notes.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "text": text})
            self.store.save(sound)
            self.say(said)
            return
        named = self._preset_named(text)
        if named is not None:
            slot, name = named
            self.work_on(slot, name, note=f"That is your preset's name. For a new design in its spirit, "
                                          f"ask \"make {name}\".")
            return
        b = self.sound("b")
        if b is not None and not _wants_new(text):
            self.revise(b, text)
        else:
            self.new_round(text)

    def _preset_named(self, text):
        """A request that is exactly the name of one of the player's presets on the unit means that preset."""
        try:
            from .library import cached
        except ImportError:
            return None
        wanted = text.strip().casefold()
        found = [item for item in cached(self.folder) or [] if not item["slot"].get("is_factory")
                 and item["name"].strip().casefold() == wanted]
        return (found[0]["slot"], found[0]["name"]) if len(found) == 1 else None

    def _instrument(self):
        return self.store.instrument(self.state.instrument)

    def new_round(self, text, *, earlier=None, by="player", after=None):
        """Three designs for `text`. `after` is the round these follow, whose designs and what was heard of them
        (`earlier`) inform these."""
        quietly = by == "app"
        if not quietly:
            self.set(working="Designing three ways to get there. About a minute…")
        instrument = self._instrument()
        stop = self._request() if not quietly else None
        future = self.thinking.submit(design.round_of_three, self.assistant, text, instrument, earlier=earlier, by=by,
                                      cancel=stop)
        revision = self.state.revision
        self.preparing = after

        def arrived(result):
            self.preparing = None
            if stop is not None and stop.is_set():
                return
            aim, designs = result
            record = {"id": knowledge_id(), "request": text, "aim": aim, "created": _now(),
                      "previous": after, "by": by, "candidates": []}
            for sound, differs in designs:
                sound.tags = [f"aim: {aim['drive']}, {aim['tone']}, {aim['space']}"]
                if self.state.song:
                    sound.origin = {**sound.origin, "song": self.state.song}
                self.store.save(sound)
                record["candidates"].append({"sound": sound.id, "differs": differs})
            self._save_round(record)
            if after:
                earlier_round = self.round(after)
                if earlier_round is not None:
                    earlier_round["next"] = record["id"]
                    self._save_round(earlier_round)
            if quietly:
                self.say("Three more designs are ready, informed by what was heard: press N.")
                return
            self.set(working="", round=record["id"])
            if self.state.revision == revision:
                self.put("b", self.store.sound(record["candidates"][0]["sound"]))
                self.say(f"Aiming for {aim['drive']}, {aim['tone']}, {aim['space']}. "
                         f"1 of {len(designs)} is on B · {designs[0][0].name}")
            else:
                self.say(f"{len(designs)} designs are ready. B changed meanwhile, so it was left alone.")

        def failed(error):
            self.preparing = None
            if stop is not None and stop.is_set():
                return
            if not quietly:
                self.set(working="", status=str(error))

        self.later(future, arrived, failed)

    def more(self, *, by="player"):
        """Three more for the round on show, informed by its designs, what listening heard and the player's marks."""
        found = self.round()
        if found is None:
            self.say("Ask for a sound first.")
            return
        if found.get("next"):
            self.go_round(found["next"])
            return
        if self.preparing == found["id"]:
            self.say("Three more are on their way.")
            return
        earlier = []
        for candidate in found["candidates"]:
            sound = self.store.sound(candidate["sound"])
            earlier.append({"name": sound.name, "differs": candidate["differs"], "chain": describe(sound.chain),
                            "heard": {key: sound.heard[key] for key in ("drive", "tone", "space")}
                            if sound.heard else None,
                            "player_marks": sound.marks, "player_said": [note["text"] for note in sound.notes],
                            "played": sound.played})
        if by == "player":
            self.set(working="Designing three more, from what was heard and what you marked…")
        self.new_round(found["request"], earlier=earlier, by=by, after=found["id"])

    def go_round(self, round_id):
        found = self.round(round_id)
        if found is None:
            return
        self.set(round=round_id)
        self.put("b", self.store.sound(found["candidates"][0]["sound"]))
        self.say(f"Round: {found['request']} · {found['aim']['drive']}, {found['aim']['tone']}, "
                 f"{found['aim']['space']}")

    def round_back(self):
        found = self.round()
        if found and found.get("previous"):
            self.go_round(found["previous"])

    def round_forward(self):
        found = self.round()
        if found and found.get("next"):
            self.go_round(found["next"])
        elif found:
            self.more()

    def _prepare_next(self):
        """When every design of the round on show has been heard, prepare three more in the background, if the
        assistant may be called without asking. Never more than two rounds ahead of the player."""
        found = self.round()
        if found is None or found.get("next") or self.preparing or not self.assistant.automatic:
            return
        if found.get("by") == "app":
            return
        if all(self.store.sound(candidate["sound"]).heard for candidate in found["candidates"]):
            self.more(by="app")

    # ---- the player's marks, copying, blind comparison ----

    def mark(self, word, side=None):
        """Put a mark on the sound on a side, or take it off. "keeper" and "not this" exclude each other."""
        sound = self.sound(side or self.state.side)
        if sound is None:
            return
        word = word.strip()[:24]
        if not word:
            return
        if word in sound.marks:
            sound.marks.remove(word)
        else:
            opposite = {"keeper": "not this", "not this": "keeper"}.get(word)
            sound.marks = [item for item in sound.marks if item != opposite] + [word]
        self.store.save(sound)
        self.say(f"{sound.name}: " + (", ".join(sound.marks) if sound.marks else "no marks"))

    def copy_to_a(self):
        b = self.sound("b")
        if b is None:
            return
        self.put("a", b)
        self.set(side="b")
        self.say(f"{b.name} is on A too, to compare the next change on B against.")

    def blind(self):
        """Hide the names, and swap A and B or not, at random. Calling it again shows what was where."""
        import random

        if self.state.blind:
            self.set(blind=False, status=f"A was {self.sound('a').name}, B was {self.sound('b').name}.")
            return
        if self.sound("a") is None or self.sound("b") is None:
            self.say("Blind comparison needs a sound on both A and B.")
            return
        if random.random() < .5:
            self.checkpoint()
            self.set(a=self.state.b, b=self.state.a, revision=self.state.revision + 1)
            self._show()
        self.set(blind=True, status="Names hidden. Play A and B, then press X to see which was which.")

    def revise(self, sound, text):
        self.set(working=f"Revising {sound.name}…")
        stop = self._request()
        future = self.thinking.submit(design.revise, self.assistant, sound, text, self._instrument(), cancel=stop)
        revision = self.state.revision

        def revised(result):
            if stop.is_set():
                return
            self.set(working="")
            result.notes.append({"at": _now(), "text": text})
            self.store.save(result)
            if self.state.revision == revision:
                self.put("b", result)
                self.say(f"{result.name}, revised: {text}")
            else:
                self.say(f"The revision of {sound.name} is ready in History. B changed meanwhile.")

        self.later(future, revised, lambda error: None if stop.is_set() else self.set(working="", status=str(error)))

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
                    if result.level_db is not None and result.level_db > -90:
                        sound.tags = [tag for tag in sound.tags if not tag.startswith("heard:")] + [
                            f"heard: {result.drive}, {result.tone}, {result.space}"]
                        self.store.save(sound)
                    self.set(working="")
                    self._show() if self.state.shown is None else None
                    self.listen_when_free()

                self.later(self.host.call("hear", sound.chain, self.phrase[1]), heard)
                return
        self._prepare_next()

    # ---- presets on the unit ----

    def work_on(self, slot, name, *, note=""):
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
                     status=f"{held_name} is on A as saved, and on B to work on. {note}".strip())
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

    # ---- phrases and the guitar ----

    def record(self, setting, seconds=8.):
        """Record a phrase on the current instrument, noting the pickup setting it was played on."""
        self.stop()
        self.set(working=f"Recording {seconds:g} s · play now…")

        def recorded(dry):
            from .audio import write_wave
            from .sounds import new_id

            phrase_id = new_id()
            folder = self.folder / "phrases" / phrase_id
            folder.mkdir(parents=True)
            write_wave(folder / "dry.wav", dry)
            info = {"id": phrase_id, "instrument": self.state.instrument, "setting": setting, "recorded": _now(),
                    "seconds": round(len(dry) / 48000, 2)}
            (folder / "phrase.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
            self.phrase = (phrase_id, dry[:, :1])
            peak = float(abs(dry).max()) if len(dry) else 0.
            level = 20 * __import__("math").log10(max(peak, 1e-9))
            self.set(working="", status=f"Recorded {info['seconds']:g} s on {setting}, peaking at {level:.0f} dBFS. "
                                        "It is now the phrase.")

        self.later(self.host.call("record", seconds), recorded)

    def recorded_phrases(self):
        found = []
        for folder in (self.folder / "phrases").glob("*"):
            try:
                found.append((folder.stat().st_mtime_ns, json.loads((folder / "phrase.json").read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
        # Newest first, by when it was played; the file time only breaks ties.
        found.sort(key=lambda pair: (pair[1].get("recorded", ""), pair[0]), reverse=True)
        return [item for _, item in found]

    def guitar_profile(self):
        """What the current instrument puts out at each pickup setting, from the phrases recorded on it."""
        from .audio import read_wave
        from .profile import profile

        takes = [(item["setting"], read_wave(self.folder / "phrases" / item["id"] / "dry.wav")[:, :1])
                 for item in self.recorded_phrases()
                 if item["instrument"] == self.state.instrument and item.get("setting", "unknown") != "unknown"]
        if not takes:
            return {}, [f"No phrase has been recorded on the {self.state.instrument} with its pickup setting noted. "
                        "Record the same short phrase on each setting."]
        return profile(takes)

    # ---- songs ----

    def add_part(self, part, scene=None):
        """Make the sound on show (or one scene of it, 0 to 7) a part of the song being worked on."""
        if not self.state.song:
            self.say("Name the song first.")
            return
        sound = self.sound(self.state.side)
        if sound is None:
            return
        song = self.store.song(self.state.song)
        song["instrument"] = self.state.instrument
        entry = {"name": part, "sound": sound.id, **({"scene": scene} if scene is not None else {})}
        song["parts"] = [item for item in song["parts"] if item["name"] != part] + [entry]
        if self.phrase and not song.get("phrase"):
            song["phrase"] = self.phrase[0]
        self.store.save_song(song)
        where = f", scene {'ABCDEFGH'[scene]}" if scene is not None else ""
        self.say(f"{sound.name}{where} is the {part} of {song['name']}.")

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
