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
import time

from lab.sound import design, knobs, words
from lab import knowledge
from lab.sound.assistant import Assistant
from lab.hearing.measure import summary
from lab.sound.sounds import Sound, Store, describe

SETTLE_SECONDS = 4.     # a turn is checked by listening once no knob has moved for this long
# The one correction a check may make to a knob, as a factor on its position: further when it did too little,
# back when it did the opposite of what it is for.
CORRECTION = {"too small": 1.6, "opposite": .5}
CORRECTION_LIMIT = .4   # never more than this much of a knob's travel


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


SESSION = "session.json"
# What is on show, kept so that the next start puts it back: never playing, never blind.
KEPT = ("a", "b", "side", "round", "scene", "song", "instrument")


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
        self.requests = set()             # the player's requests in flight, each its own cancel signal
        self.latest = None                # the newest of them
        self.progress = []                # (handler, value) reported by the unit's worker, handled by `poll`
        self.retry_open_at = None         # when to try opening the unit again after it refused
        self.watched_at = 0.              # when the unit was last asked whether the player moved it
        self.phrase = phrase              # (name, mono dry audio) or None
        self.listeners = []
        self.shaping = None               # (sound id, knobs turned, when) waiting to be checked by listening
        self.settle_seconds = SETTLE_SECONDS

    # ---- state ----

    def set(self, **changes):
        before = self.state
        self.state = replace(self.state, **changes)
        if any(getattr(before, key) != getattr(self.state, key) for key in KEPT):
            self.remember()
        for listener in self.listeners:
            listener(self.state)

    # ---- the session, kept between starts ----

    def remember(self, **desk):
        """Keep what is on show (and anything the desk adds, such as the open panel) for the next start."""
        record = {key: getattr(self.state, key) for key in KEPT}
        whole = getattr(self, "phrase_whole", None)
        if self.phrase:
            record["phrase"] = {"id": self.phrase[0], **({"start": int(whole[1]), "end": int(whole[2])}
                                                         if whole else {})}
        record["desk"] = {**getattr(self, "desk_session", {}), **desk}
        self.desk_session = record["desk"]
        path = self.folder / SESSION
        try:
            part = path.with_suffix(".part")
            part.write_text(json.dumps(record, indent=1), encoding="utf-8")
            part.replace(path)
        except OSError:
            pass

    def restore(self):
        """Put back what was on show when the app last closed, as far as it still exists. Returns the record (the
        phrase and the desk's part are the desk's to use), or None. The unit is made ready when it opens."""
        try:
            record = json.loads((self.folder / SESSION).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        def exists(sound_id):
            return bool(sound_id) and (self.folder / "sounds" / f"{sound_id}.json").exists()

        changes = {key: record[key] for key in ("side", "scene", "song", "instrument") if record.get(key) is not None}
        changes.update(a=record.get("a") if exists(record.get("a")) else None,
                       b=record.get("b") if exists(record.get("b")) else None)
        if record.get("round") and self.round(record["round"]) is not None:
            changes["round"] = record["round"]
        self.desk_session = record.get("desk") or {}
        self.state = replace(self.state, **changes)
        self.restored = bool(self.state.a or self.state.b)
        return record

    def say(self, text):
        self.set(status=text)

    def sound(self, side):
        sound_id = getattr(self.state, side)
        return self.store.sound(sound_id) if sound_id else None

    def checkpoint(self):
        """Keep what is on show and the knob positions of both sounds, for undo."""
        knob_values = {}
        for side in ("a", "b"):
            sound = self.sound(side)
            if sound is not None:
                knob_values[sound.id] = dict(sound.knobs)
        self.history = (self.history + [(self.state, knob_values)])[-40:]

    def undo(self):
        """Put back the sounds on show and their knob positions as they were. Playing is not undone: what plays
        (or not) stays as it is."""
        if not self.history:
            self.say("Nothing to undo.")
            return
        before, knob_values = self.history.pop()
        for sound_id, values in knob_values.items():
            try:
                sound = self.store.sound(sound_id)
            except (OSError, ValueError):
                continue
            if sound.knobs != values:
                sound.knobs = dict(values)
                self.store.save(sound)
        same_pair = (before.a, before.b) == (self.state.a, self.state.b)
        # What is heard stays as it is: the same side, playing or not. Only when that side would be left empty does
        # the selection move; with nothing left on either side, nothing plays.
        side = self.state.side
        if getattr(before, side) is None:
            side = "a" if side == "b" else "b"
        if before.a is None and before.b is None:
            self.stop()
        self.set(a=before.a, b=before.b, side=side, round=before.round, scene=before.scene,
                 blind=False, revision=self.state.revision + (0 if same_pair else 1), status="Undone.")
        if same_pair and self.state.shown:
            for side in ("a", "b"):
                sound = self.sound(side)
                if sound is not None:
                    self.later(self.host.call("turn", side, sound.knobs), lambda moves: None)
        else:
            self._show()

    # ---- work done elsewhere ----

    def later(self, future, done, failed=None):
        self.jobs.append((future, done, failed))

    def poll(self):
        """Handle every finished piece of work. Called from the window's clock. A fault is said on the line and
        written with its trace to errors.log in the data folder."""
        import traceback

        finished, waiting = [], []
        for job in self.jobs:                  # each future looked at once: one that finishes now waits a tick
            (finished if job[0].done() else waiting).append(job)
        self.jobs = waiting
        while self.progress:
            handler, value = self.progress.pop(0)
            handler(value)
        for future, done, failed in finished:
            try:
                result = future.result()
            except Exception as error:  # noqa: BLE001
                self.log_error(error, "".join(traceback.format_exception(error)))
                (failed or (lambda error: self.set(status=str(error), working="")))(error)
                continue
            try:
                done(result)
            except Exception as error:  # noqa: BLE001
                self.log_error(error, traceback.format_exc())
                self.set(working="", status=f"Something went wrong: {error}. The details are in errors.log in the "
                                            "data folder.")
        self._check_shaping()
        now = time.monotonic()
        if self.retry_open_at is not None and now >= self.retry_open_at:
            self.retry_open_at = None
            self.open()
        if self.state.mode and not self.host.busy and now - self.watched_at > 1.5:
            self.watched_at = now
            self.later(self.host.call("watch"), self._watched, lambda error: None)

    def _watched(self, result):
        if result.get("moved") and self.state.mode:
            self.set(mode=None, shown=None, status="Another preset was chosen on the Quad Cortex, so Tone Lab stopped "
                                                   "sending sound and leaves it alone. Press Guitar or Phrase to "
                                                   "come back.")

    def log_error(self, error, trace):
        try:
            with open(self.folder / "errors.log", "a", encoding="utf-8") as log:
                log.write(f"\n--- {_now()} {type(error).__name__}: {error}\n{trace}")
        except OSError:
            pass

    # ---- the session ----

    def open(self):
        self.set(working="Connecting to the Quad Cortex…")

        def opened(result):
            stale = knowledge.stale(getattr(self.host, "firmware", None) or "4.1.1")
            note = " Gave back the preset a crash had left borrowed." if result.get("recovered") else ""
            if getattr(self, "notice", ""):
                note += " " + self.notice
                self.notice = ""
            self.set(working="", status="Ready." + note + (" Some measured tables were made on another firmware."
                                                            if stale else ""))
            if getattr(self, "restored", False):
                self.restored = False
                shown = [f"{side.upper()} {self.sound(side).name}" for side in ("a", "b") if self.sound(side)]
                found = self.round()
                self.say("Back where you left off: " + ", ".join(shown)
                         + (f"; round: {found['request']}" if found else "") + "." + note)
                self._show()

        def refused(error):
            self.set(working="", status=f"{error}")
            if getattr(self.host, "failed", None) is None:
                self.retry_open_at = time.monotonic() + 5     # tried again until the unit lets it

        self.later(self.host.call("open"), opened, refused)
        # The player's presets, for the Sounds panel and for requests that name one: read again when the last
        # read is over half a day old (it holds the unit about ten seconds). Quiet if it fails.
        listing = self.folder / "directory.json"
        if not listing.exists() or time.time() - listing.stat().st_mtime > 12 * 3600:
            self.later(self.host.call("lab.qc.library.directory", folder=str(self.folder)), lambda entries: None,
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
            if self.state.scene:
                self.later(self.host.call("scene", self.state.scene), lambda result: None)
            if self.state.mode:
                self.play(self.state.mode)
            self.listen_when_free()

        self.later(self.host.call("show", a.chain if a else None, b.chain if b else None), shown)

    def play(self, mode, side=None):
        side = side or self.state.side
        if self.state.shown is None:
            self.set(mode=mode, side=side, status="Getting ready. It plays as soon as it is.")
            if not self.state.working:
                self._show()
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
            self.say(f"{side.upper()} · {mode}" if self.state.blind else f"{side.upper()} · {sound.name} · {mode}")

        self.later(self.host.call("play", side, mode, phrase), playing)

    def _match_levels(self):
        """Play A and B equally loud, when both have been measured: the louder is turned down to the quieter.

        Matched by integrated loudness (ITU-R BS.1770, LUFS), which follows perceived loudness across sounds of
        different brightness better than a plain level does (docs/TONE_RESEARCH.md, 13); by RMS level only when one of
        the two was measured before loudness was recorded."""
        from lab.qc.engine import PREVIEW_DB

        sounds = {side: self.sound(side) for side in ("a", "b")}
        heard = {side: self.evidence(sound) for side, sound in sounds.items() if sound}
        heard = {side: record for side, record in heard.items()
                 if record and record.get("level_db") is not None and record["level_db"] > -90}
        key = "loudness_lufs" if all(record.get("loudness_lufs") is not None for record in heard.values()) \
            else "level_db"
        levels = {side: record[key] for side, record in heard.items()}
        if len(levels) < 2 or len([sound for sound in sounds.values() if sound]) < 2:
            # Nothing fair to match by: both play at the preview level, and no earlier trim stays on either.
            for side, sound in sounds.items():
                if sound is not None:
                    self.later(self.host.call("level", side, PREVIEW_DB), lambda result: None)
            return
        quietest = min(levels.values())
        for side, level in levels.items():
            self.later(self.host.call("level", side, PREVIEW_DB + quietest - level), lambda result: None)

    def phrase_key(self):
        """Which phrase, and which part of it, a measurement was made on: a measurement holds only for that."""
        if not self.phrase:
            return None
        whole = getattr(self, "phrase_whole", None)
        return f"{self.phrase[0]}@{whole[1]}-{whole[2]}" if whole else self.phrase[0]

    def evidence(self, sound):
        """What was measured of `sound` as it plays now (this phrase, this chain, these knobs), or None."""
        heard = sound.heard
        if not heard or heard.get("phrase") != self.phrase_key() or heard.get("digest") != sound.chain.digest():
            return None
        # Listening recalls the sound, which starts at its first scene: what was measured holds for that scene, or
        # for any when the scenes do not differ.
        if sound.chain.scenes_in_use() > 1 and self.state.scene != heard.get("scene", 0):
            return None
        if not sound.knobs:
            return heard
        shaped = heard.get("shaped") or {}
        return shaped if shaped.get("knobs") == sound.knobs and shaped.get("phrase") == self.phrase_key() else None

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

    def cancellable(self):
        return any(not signal.is_set() for signal in self.requests)

    def cancel(self):
        """Drop every request the assistant is working on for the player; answers that still come are ignored."""
        active = [signal for signal in self.requests if not signal.is_set()]
        if not active:
            return False
        for signal in active:
            signal.set()
        self.requests.clear()
        self.set(working="", status="Request cancelled.")
        return True

    def _request(self):
        from threading import Event

        signal = Event()
        self.requests.add(signal)
        self.latest = signal            # the player's newest request: only its answer goes on show
        return signal

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
        self.remember()
        for signal in self.requests:
            signal.set()
        self.host.close()
        self.thinking.shutdown(wait=False, cancel_futures=True)

    # ---- shaping ----

    def turn(self, values, side=None):
        side = side or self.state.side
        sound = self.sound(side)
        if sound is None:
            return
        self.checkpoint()
        before = dict(sound.knobs)
        sound.knobs = {key: value for key, value in values.items() if value}
        self.store.save(sound)
        if self.state.shown:
            self.later(self.host.call("turn", side, sound.knobs), lambda moves: None)
        turned = {key for key in set(before) | set(sound.knobs) if before.get(key, 0) != sound.knobs.get(key, 0)}
        if turned:
            # Checked by listening once the turning has stopped (`_check_shaping`).
            earlier = self.shaping[1] if self.shaping and self.shaping[0] == sound.id else set()
            self.shaping = (sound.id, turned | earlier, time.monotonic())

    # ---- knobs that check themselves ----

    def _check_shaping(self):
        """Once the knobs of a sound have not been turned for a few seconds, the unit is idle and nothing plays,
        listen to the sound as shaped, silently, and compare it with the sound heard without its knobs.

        A knob can do something else on this amp than the table says (amp controls interact: docs/TONE_RESEARCH.md,
        finding 8). One knob that measured the wrong way, or too little, is corrected once; nothing is measured
        again for that correction, so this never loops."""
        if self.shaping is None:
            return
        sound_id, turned, at = self.shaping
        if time.monotonic() - at < self.settle_seconds:
            return
        if self.state.mode or self.state.working or self.host.busy or self.phrase is None:
            return                                  # wait: never interrupt playing or other work
        self.shaping = None
        if sound_id not in (self.state.a, self.state.b):
            return                                  # no longer on show
        try:
            sound = self.store.sound(sound_id)
        except (OSError, ValueError):
            return
        if not sound.knobs:
            return
        # The take, the part of it, the scene and the chain are fixed for the whole check: both measurements are
        # made on them, and a correction is made only while they are still what is on show.
        inputs = (self.phrase_key(), self.phrase[1], self.state.scene, sound.chain.digest())
        phrase_id = inputs[0]
        if sound.heard and "probe" in sound.heard and sound.heard.get("phrase") == phrase_id \
                and sound.heard.get("digest") == sound.chain.digest():
            self._hear_shaped(sound, turned, inputs)
            return
        # The sound without its knobs has not been heard on this phrase: that first.
        self.set(working=f"Listening to {sound.name} without its Shape knobs, silently…")

        def heard(result):
            fresh = self.store.sound(sound_id)
            fresh.heard = {"phrase": phrase_id, "digest": inputs[3], "scene": 0, **result.to_record()}
            self.store.save(fresh)
            self.set(working="")
            if self._current(inputs):
                self._hear_shaped(fresh, turned, inputs)
            elif not self.state.mode:
                self._show()

        self.later(self.host.call("hear", sound.chain, inputs[1]), heard, self._shaping_failed)

    def _current(self, inputs):
        """Whether the take, part, scene and chain a check was started on are still what is on show."""
        key, _, scene, digest = inputs
        return (self.phrase_key() == key and self.state.scene == scene
                and any(sound is not None and sound.chain.digest() == digest
                        for sound in (self.sound("a"), self.sound("b"))))

    def _shaping_failed(self, error):
        self.set(working="", status=f"Could not listen to the shaped sound: {error}")
        if not self.state.mode:
            self._show()

    def _hear_shaped(self, sound, turned, inputs):
        sound_id, values, phrase_id = sound.id, dict(sound.knobs), inputs[0]
        self.set(working=f"Listening to {sound.name} as shaped, silently…")

        def heard(result):
            fresh = self.store.sound(sound_id)
            self.set(working="")
            if fresh.knobs != values or not fresh.heard or fresh.heard.get("phrase") != phrase_id \
                    or not self._current(inputs):
                # Turned again meanwhile: that turn is checked in its turn.
                if not self.state.mode:
                    self._show()
                return
            after = result.to_record()
            found = knobs.check(fresh.heard, after, values)
            correction = _correction(found, turned, values)
            fresh.heard = {**fresh.heard, "shaped": {"phrase": phrase_id, "knobs": values, **after, "check": found,
                                                     "corrected": correction}}
            if correction:
                key, old, new = correction
                fresh.knobs = {name: value for name, value in {**values, key: new}.items() if value}
            self.store.save(fresh)
            if not self.state.mode:
                self._show()        # listening used the unit; A and B go back, with the knobs as they now stand
            elif correction and self.state.shown:
                # Playing began meanwhile, which made A and B ready again: the corrected knobs go on as it plays.
                for side in ("a", "b"):
                    if getattr(self.state, side) == sound_id:
                        self.later(self.host.call("turn", side, fresh.knobs), lambda moves: None)
            self.say(_checked_words(found, turned, correction))

        self.later(self.host.call("hear_shaped", sound.chain, values, inputs[1]), heard, self._shaping_failed)

    def base(self):
        """Hear the sound on show without its Shape knobs, or with them again (the 0 key)."""
        side = self.state.side
        sound = self.sound(side)
        if sound is None or not sound.knobs or not self.state.shown:
            self.say("Nothing shaped to compare with.")
            return
        self.hearing_base = not getattr(self, "hearing_base", False)
        self.later(self.host.call("turn", side, {} if self.hearing_base else sound.knobs), lambda moves: None)
        self.say(f"{sound.name}: " + ("the base, without the Shape knobs" if self.hearing_base else "shaped"))

    def named(self, row, column, setting, side=None):
        """A copy of the sound with one block set to one of its model's named settings, put on the same side."""
        from lab.sound import catalog
        from lab.sound.sounds import Sound

        side = side or self.state.side
        sound = self.sound(side)
        block = sound.chain.block(row, column) if sound else None
        if block is None:
            return
        values = knowledge.named_settings(block.model).get(setting)
        if not values:
            self.say(f"{setting} is not a named setting of {block.name}.")
            return
        info = catalog.model(block.model)
        for name, value in values.items():
            try:
                block = block.set_display(info.index(name), value)
            except (KeyError, ValueError):
                continue
        chain = sound.chain.with_block(row, column, block)
        copy = Sound(f"{sound.name}"[:24], chain, {"kind": "revised", "from": sound.id,
                                                   "request": f"{block.name}: {setting}"},
                     parent=sound.id, knobs=dict(sound.knobs), explanation=sound.explanation)
        self.put(side, copy)
        self.say(f"{block.name} set to its named setting {setting}. Z undoes it.")

    def difference(self):
        """What listening heard differ between A and B, in plain words, when both were heard on the same phrase."""
        a, b = self.sound("a"), self.sound("b")
        if not (a and b and a.heard and b.heard and "probe" in a.heard and "probe" in b.heard):
            return ["Both sounds need to have been listened to with the current tests. That happens by itself "
                    "while the unit is free."]
        if a.heard.get("phrase") != b.heard.get("phrase"):
            return ["A and B were listened to on different phrases, so they cannot be compared fairly yet."]
        lines = []
        level = b.heard["level_db"] - a.heard["level_db"]
        if abs(level) >= 1:
            lines.append(f"B is {abs(level):.0f} dB {'louder' if level > 0 else 'quieter'}.")
        pa, pb = a.heard.get("probe") or {}, b.heard.get("probe") or {}
        if pa and pb and abs(pb["distortion_db"] - pa["distortion_db"]) >= 3:
            lines.append(f"Distortion: A {a.heard['distortion']} ({pa['distortion_db']:.0f} dB of harmonics), "
                         f"B {b.heard['distortion']} ({pb['distortion_db']:.0f} dB).")
        ca, cb = a.heard["compression"], b.heard["compression"]
        if abs(cb - ca) > .1:
            lines.append(f"B is {'more' if cb > ca else 'less'} compressed: of a 12 dB drop in your playing, "
                         f"A gives back {12 * (1 - ca):.0f} dB and B {12 * (1 - cb):.0f} dB.")
        bright = b.heard["brightness_oct"] - a.heard["brightness_oct"]
        if abs(bright) >= .1:
            lines.append(f"B is {abs(bright):.1f} octave {'brighter' if bright > 0 else 'darker'}.")
        low = b.heard["low_end_db"] - a.heard["low_end_db"]
        if abs(low) >= 1.5:
            lines.append(f"B has {abs(low):.0f} dB {'more' if low > 0 else 'less'} low end.")
        space = b.heard["tail_db"] - a.heard["tail_db"]
        if abs(space) >= 3:
            lines.append(f"B rings on {abs(space):.0f} dB {'more' if space > 0 else 'less'} after you stop.")
        return lines or ["Listening heard no clear difference between A and B."]

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
            from lab.qc.library import cached
        except ImportError:
            return None
        wanted = text.strip().casefold()
        found = [item for item in cached(self.folder) or [] if not item["slot"].get("is_factory")
                 and item["name"].strip().casefold() == wanted]
        return (found[0]["slot"], found[0]["name"]) if len(found) == 1 else None

    def _assets(self):
        from lab.qc.assets import cached

        return cached(self.folder) or []

    def read_assets(self, done=None):
        """Read the IRs and captures on the unit (about ten seconds) and remember them."""
        self.set(working="Reading the IRs and captures on the Quad Cortex…")

        def read(records):
            self.set(working="", status=f"{len(records)} IRs and captures on the unit.")
            if done:
                done(records)

        self.later(self.host.call("lab.qc.assets.listing", folder=str(self.folder)), read)

    def use_asset(self, asset, *, has_cab=None, version=None):
        """A copy of the sound on B with one of the player's IRs in place of its cab, or a capture in place of
        its amp (and cab, when the capture has one)."""
        from lab.qc import assets
        from lab.sound.sounds import Sound

        sound = self.sound("b") or self.sound("a")
        if sound is None:
            self.say("Put a sound on B first; the IR or capture goes into it.")
            return
        try:
            if asset.kind == "ir":
                chain, words = assets.with_ir(sound.chain, asset)
            else:
                chain, words = assets.with_capture(sound.chain, asset, has_cab=has_cab, version=version)
        except ValueError as error:
            self.say(str(error))
            return
        name = f"{sound.name[:19]} {'IR' if asset.kind == 'ir' else 'Cap'}"
        copy = Sound(name, chain, {"kind": "revised", "from": sound.id, "request": words}, parent=sound.id,
                     explanation=f"{sound.explanation} {words}".strip(), knobs=dict(sound.knobs))
        self.put("b", copy)
        self.say(words + " Z undoes it.")

    def _instrument(self):
        return self.store.instrument(self.state.instrument)

    def _setting(self):
        """The pickup setting the phrase in use was recorded on, if it was noted."""
        if not self.phrase:
            return None
        found = next((item for item in self.recorded_phrases() if item["id"] == self.phrase[0]), None)
        setting = (found or {}).get("setting")
        return None if not setting or setting == "unknown" else setting

    def _guitar(self):
        """What the instrument puts out, for the designer (`profile.facts`), worked out once per instrument."""
        from lab.hearing.profile import facts

        cached = getattr(self, "guitar_cache", None)
        if cached is None or cached[0] != self.state.instrument:
            cached = (self.state.instrument, self.guitar_profile()[0])
            self.guitar_cache = cached
        return facts(cached[1], self._setting()) if cached[1] else None

    def _listening(self):
        """What guitar music the player listens to most, if the listening history was analysed (personal; kept in
        the data folder)."""
        from lab.player.listening import prior

        try:
            record = json.loads((self.folder / "personal" / "listening.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return prior(record)

    def new_round(self, text, *, earlier=None, by="player", after=None):
        """Three designs for `text`. `after` is the round these follow, whose designs and what was heard of them
        (`earlier`) inform these."""
        quietly = by == "app"
        stop = self._request() if not quietly else None
        if not quietly:
            self.set(working="Designing three ways to get there. About a minute…")
        instrument = self._instrument()
        future = self.thinking.submit(design.round_of_three, self.assistant, text, instrument, earlier=earlier, by=by,
                                      cancel=stop, assets_known=self._assets(), guitar=self._guitar(),
                                      setting=self._setting(), listening=None if quietly else self._listening())
        revision = self.state.revision
        self.preparing = after

        def arrived(result):
            self.preparing = None
            self.requests.discard(stop)
            if stop is not None and stop.is_set():
                return
            aim, designs = result
            record = {"id": knowledge_id(), "request": text, "aim": aim, "created": _now(),
                      "previous": after, "by": by, "candidates": []}
            song = aim.get("song") or (self.round(after) or {}).get("aim", {}).get("song")
            if song:
                record["aim"]["song"] = song
                if not quietly:
                    self.set(song=song_name(song))
            for sound, differs in designs:
                sound.tags = [f"aim: {summary(aim)}"]
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
            if stop is not self.latest:
                # A newer request is on its way or already answered: this one is kept, not put on show.
                self.say(f"The designs for an earlier request ({text[:40]}) are in History; the latest request's "
                         "are the ones shown.")
                return
            self.set(working="", round=record["id"])
            if self.state.revision == revision:
                self.put("b", self.store.sound(record["candidates"][0]["sound"]))
                self.say(f"Aiming for {summary(aim)}. "
                         f"1 of {len(designs)} is on B · {designs[0][0].name}")
            else:
                self.say(f"{len(designs)} designs are ready. B changed meanwhile, so it was left alone.")

        def failed(error):
            self.preparing = None
            self.requests.discard(stop)
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
                            "heard": {key: sound.heard.get(key) for key in ("distortion", "compressed",
                                                                               "brightness", "tail")}
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
        self.say(f"Round: {found['request']} · {summary(found['aim'])}")

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
        blind_choice = self.state.blind and word in ("keeper", "not this")
        if word in sound.marks and not blind_choice:
            sound.marks.remove(word)
        else:
            # A choice made blind is a choice even when that sound already had the mark: it is kept, and counted.
            opposite = {"keeper": "not this", "not this": "keeper"}.get(word)
            sound.marks = [item for item in sound.marks if item not in (opposite, word)] + [word]
            self._choice(sound, word)
        self.store.save(sound)
        if self.state.blind and word in ("keeper", "not this"):
            # The choice is made: now say which was which.
            side = (side or self.state.side).upper()
            self.set(blind=False, status=f"{side}: {word}. A was {self.sound('a').name}, B was {self.sound('b').name}.")
        else:
            self.say(f"{sound.name}: " + (", ".join(sound.marks) if sound.marks else "no marks"))
        if word == "keeper" and word in sound.marks:
            self._into_song(sound)
        self._learn()

    def _round_of(self, sound):
        """The round a sound was designed in, if any."""
        for path in (self.folder / "rounds").glob("*.json"):
            try:
                found = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if any(candidate["sound"] == sound.id for candidate in found.get("candidates", [])):
                return found
        return None

    def _into_song(self, sound):
        """A sound kept or saved from a round that was for a song becomes that song's part: the request said
        which song and part, so nothing has to be named by hand."""
        found = self._round_of(sound) or {}
        song = found.get("aim", {}).get("song")
        if not song and sound.parent:
            parent = self.store.sound(sound.parent) if (self.folder / "sounds" / f"{sound.parent}.json").exists() else None
            song = ((self._round_of(parent) or {}).get("aim", {}) if parent else {}).get("song")
        if not song:
            return
        name = song_name(song)
        record = self.store.song(name)
        record.update(artist=song.get("artist"), instrument=self.state.instrument)
        part = (song.get("part") or "main").capitalize()
        record["parts"] = [item for item in record["parts"] if item["name"] != part] + [{"name": part, "sound": sound.id}]
        if self.phrase and not record.get("phrase"):
            record["phrase"] = self.phrase[0]
        self.store.save_song(record)
        self.set(song=name)

    def _choice(self, sound, word):
        """A keeper or a not-this given while A and B are both on show is a choice between them (blind when names
        are hidden), recorded for the taste model."""
        from lab.player import taste

        side = "a" if self.state.a == sound.id else "b" if self.state.b == sound.id else None
        other = self.sound("b" if side == "a" else "a") if side else None
        if other is None or other.id == sound.id or word not in ("keeper", "not this"):
            return
        kept, over = (sound, other) if word == "keeper" else (other, sound)
        context = {"phrase": self.phrase_key(), "scene": self.state.scene,
                   "knobs": {kept.id: dict(kept.knobs), over.id: dict(over.knobs)}}
        taste.record_choice(self.folder, kept.id, over.id, "blind" if self.state.blind else "a/b",
                            round_id=self.state.round, context=context)

    def _learn(self):
        """Fit the taste model again from everything chosen so far; cheap, and quiet if it fails."""
        from lab.player import taste

        try:
            self.taste = taste.learn(self.folder)
        except Exception:  # noqa: BLE001
            self.taste = None

    def taste_lines(self):
        """What the player's choices so far say, in measured quantities, or that there is not enough yet."""
        from lab.player import taste

        model = getattr(self, "taste", None) or taste.load(self.folder)
        return taste.describe(model) if model else ["No choices yet: marks and A/B choices teach it."]

    def copy_to_a(self):
        """A copy of B, as it is now, on A: a sound of its own, so turning B's knobs leaves it as it is."""
        b = self.sound("b")
        if b is None:
            return
        copy = Sound(b.name, b.chain, {"kind": "revised", "from": b.id, "request": "a copy to compare against"},
                     parent=b.id, knobs=dict(b.knobs), explanation=b.explanation, heard=b.heard)
        self.put("a", copy)
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
        stop = self._request()
        self.set(working=f"Revising {sound.name}…")
        future = self.thinking.submit(design.revise, self.assistant, sound, text, self._instrument(), cancel=stop,
                                      assets_known=self._assets(), guitar=self._guitar(), setting=self._setting())
        revision = self.state.revision

        def revised(result):
            self.requests.discard(stop)
            if stop.is_set():
                return
            self.set(working="")
            result.notes.append({"at": _now(), "text": text})
            self.store.save(result)
            if stop is not self.latest:
                self.say(f"The revision for an earlier request ({text[:40]}) is in History; the latest request's "
                         "answer is the one shown.")
            elif self.state.revision == revision:
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
        key = self.phrase_key()
        for candidate in found["candidates"]:
            sound = self.store.sound(candidate["sound"])
            heard = sound.heard or {}
            # Never heard, heard before the test tones, or heard on another phrase or part of it.
            if "probe" not in heard or heard.get("phrase") != key or heard.get("digest") != sound.chain.digest():
                self.set(working=f"Listening to {sound.name}, silently…")

                def heard(result, sound_id=sound.id, key=key, digest=sound.chain.digest()):
                    fresh = self.store.sound(sound_id)        # knobs or marks may have changed meanwhile
                    fresh.heard = {"phrase": key, "digest": digest, "scene": 0, **result.to_record()}
                    if result.level_db is not None and result.level_db > -90:
                        fresh.tags = [tag for tag in fresh.tags if not tag.startswith("heard:")] + [
                            f"heard: {result.said()}"]
                    self.store.save(fresh)
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

    def save(self, name=None, sound_id=None, knob_values=None):
        """Save a sound to the unit. `sound_id` and `knob_values` are the sound and knobs the name was confirmed
        for; by default the sound on the selected side as it is now."""
        sound = self.store.sound(sound_id) if sound_id else self.sound(self.state.side)
        if sound is None:
            return
        knob_values = dict(sound.knobs if knob_values is None else knob_values)
        name = (name or sound.name)[:24]
        self.stop()
        self.set(working=f"Saving {name} to the Tone Lab setlist…")
        chain = sound.chain

        def saved(result):
            slot, _ = result
            fresh = self.store.sound(sound.id)
            fresh.saved = {"slot": slot, "name": name, "knobs": knob_values}
            self.store.save(fresh)
            self._into_song(fresh)
            self._learn()
            self.set(working="", shown=None, status=f"Saved · Tone Lab {slot['position'] + 1} · {name}")
            self._show()

        self.later(self.host.call("save", chain, name, knob_values or None), saved)

    # ---- phrases and the guitar ----

    def record(self, setting, seconds=8.):
        """Record a phrase on the current instrument, noting the pickup setting it was played on: the dry input and,
        when a sound is on show, the selected sound's stereo return as it was heard while playing."""
        import math

        self.stop()
        side = self.state.side if self.sound(self.state.side) is not None and self.state.shown else None
        heard_through = self.sound(side) if side else None
        self.set(working="Opening the input… wait for “play now”.")

        def opened(_):
            self.set(working=f"Recording {seconds:g} s · play now" + (f" (you hear {side.upper()})" if side else "")
                     + "…")

        def recorded(audio):
            from lab.hearing.audio import write_wave
            from lab.sound.sounds import new_id

            dry = audio[:, :1]
            peak = float(abs(dry).max()) if len(dry) else 0.
            level = 20 * math.log10(max(peak, 1e-9))
            if peak < .003:
                self.set(working="", status=f"Nothing came in on the guitar input (peak {level:.0f} dBFS). The take "
                                            "was not kept.")
                return
            if peak >= .999:
                self.set(working="", status="The take clipped at the input. Turn the input gain down and record "
                                            "again; it was not kept.")
                return
            phrase_id = new_id()
            folder = self.folder / "phrases" / phrase_id
            folder.mkdir(parents=True)
            write_wave(folder / "dry.wav", dry)
            info = {"id": phrase_id, "instrument": self.state.instrument, "setting": setting, "recorded": _now(),
                    "seconds": round(len(dry) / 48000, 2)}
            wet = audio[:, 1:3]
            if wet.shape[1] == 2 and float(abs(wet).max()) > 1e-4:
                write_wave(folder / "wet.wav", wet)
                info["wet"] = ({"sound": heard_through.id, "name": heard_through.name} if heard_through
                               else {"sound": None, "name": "the preset on the unit"})
            (folder / "phrase.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
            self.phrase = (phrase_id, dry.copy())
            self.phrase_whole = (dry.copy(), 0, len(dry))
            self.remember()
            self.set(working="", status=f"Recorded {info['seconds']:g} s on {setting}, peaking at {level:.0f} dBFS. "
                                        "It is now the phrase.")

        def failed(error):
            self.set(working="", status=f"Recording: {error}")

        self.later(self.host.call("record", seconds, side, dict(heard_through.knobs) if heard_through else None,
                                  progress=lambda value: self.progress.append((opened, value))), recorded, failed)

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
        from lab.hearing.audio import read_wave
        from lab.hearing.profile import profile

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


def _correction(found, turned, values):
    """The one correction a check allows: the knob turned that measured the wrong way or too little and is turned
    furthest, its position scaled (further for too little, back for the wrong way), never past its limits or by
    more than `CORRECTION_LIMIT`. Returns (knob, old position, new position) or None."""
    wrong = [key for key in turned if key in found and found[key]["verdict"] in CORRECTION and values.get(key)]
    for key in sorted(wrong, key=lambda key: -abs(values[key])):
        old = values[key]
        wanted = old * CORRECTION[found[key]["verdict"]]
        wanted = old + max(-CORRECTION_LIMIT, min(CORRECTION_LIMIT, wanted - old))
        new = round(max(knobs.KNOBS[key][1], min(1., wanted)), 2)
        if new != old:
            return key, old, new
    return None


def _measured(entry):
    change, units = entry["change"], entry["units"]
    digits = 2 if units == "octave" else 0 if units == "ms" else 1 if units == "dB" else 3
    return f"{knobs.MEASURES.get(entry['measure'], entry['measure'])} {change:+.{digits}f}{' ' + units if units else ''}"


def _checked_words(found, turned, correction):
    """What listening measured of the knobs just turned, in one line for the status."""
    parts, unmeasured = [], []
    for key in sorted(turned, key=lambda key: list(knobs.KNOBS).index(key) if key in knobs.KNOBS else 99):
        entry = found.get(key)
        if entry is None:
            continue
        title = knobs.KNOBS[key][0]
        if entry["verdict"] == "not measurable":
            unmeasured.append(title)
            continue
        parts.append(f"{title}: {_measured(entry)}, {entry['verdict']}")
    line = "Measured with the knobs, against without: " + ("; ".join(parts) if parts else "nothing that listening "
                                                                                           "measures") + "."
    if unmeasured:
        line += f" Not measured: {', '.join(unmeasured)}."
    if correction:
        key, old, new = correction
        how = "further" if abs(new) > abs(old) else "back"
        line += (f" {knobs.KNOBS[key][0]} turned {how}, from {old:+.2f} to {new:+.2f}, once; "
                 "not measured again.")
    return line


def song_name(song):
    """"Artist - Title" for a song the assistant named."""
    return " - ".join(part for part in (song.get("artist"), song.get("title")) if part)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def knowledge_id():
    from lab.sound.sounds import new_id

    return new_id()


NEW = ("make", "new sound", "build", "design", "give me", "something like", "i want a", "preset for", "tone for")


def _wants_new(text):
    lowered = text.casefold()
    return any(lowered.startswith(word) or f" {word} " in f" {lowered} " for word in NEW) or " - " in text
