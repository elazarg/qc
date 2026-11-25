"""What the desk does, without the window: what is on show, and the actions that change it.

The desk shows two sounds, A and B, and plays one of them from the guitar or
from a recorded phrase. The player asks for things in words: a remark about
the sound moves the Shape knobs at once; a request that reads as a change to
the sound on B revises it; anything else (a sound, an artist, a song) starts
a round of three designs, the first of which goes on B. A preset saved on the
unit can be brought in to work on, and the result saved back to the Tone Lab
setlist.

Where things are kept:

- What is on show is `Studio.state`, replaced, never changed in place, so
  that the window can draw it. `session.json` keeps part of it for the next
  start.
- Sounds, songs and phrases are files (`Store`), and so are rounds
  (`Rounds`). A sound is read from its file whenever it is needed and written
  whole; its knob positions, marks and measurements live there.
- Everything else the studio keeps track of is an attribute set up in
  `__init__`: the phrase in use, the last remark, what is going on (`doing`,
  shown as the working line) and who holds the audio input.

Four parts do work of their own and keep their own state; the studio makes
them, and each reaches the rest through it:

- `ear` (`listening.Listening`): listening, silently, while the unit is free,
  to what is on show and has not been heard as it plays now, and checking
  knobs that were turned; and whether a measurement holds for what plays now.
- `asking` (`requests.Requests`): the requests with the assistant, rounds and
  revisions, of which only the newest answer goes on show.
- `guitar` (`guitar.Guitar`): the takes recorded, and what the instrument
  puts out, worked out from them away from the window's thread.
- `marks` (`marks.Marks`): the player's marks, the choices they amount to,
  and the songs a kept sound becomes a part of.

What the window, the side tools and the Guitar Learner's seat call is here,
on `Studio`, under one name each, whichever part does it.

Work that takes time (anything on the unit, any call to the assistant) runs
elsewhere and its result is handled here, on the window's thread, by `poll`.
A result is applied only if what it was for is still what is shown: an answer
for a sound that has since left B is offered, not applied.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import json
from pathlib import Path
import time
import traceback

from lab.sound import knobs, words
from lab import knowledge
from lab.sound.assistant import Assistant, Cancelled, Refused
from lab.hearing.measure import summary
from lab.sound.sounds import Sound, Store, now, write_whole
from lab.desk.difference import difference
from lab.desk.guitar import Guitar
from lab.desk.listening import Listening
from lab.desk.marks import Marks
from lab.desk.requests import Requests, Rounds, reads_as_change, song_name

GETTING_READY = "Getting ready. It plays as soon as it is."
COPY_REQUEST = "a copy to compare against"     # what a copy of B on A is, in its origin
PHRASE_SECONDS = 3.5    # the part of a take that loops, until its edges are dragged
TAKE_SECONDS = 45.      # a take the player has not ended by then ends by itself
RATE = 48000
# Who may hold the audio input outside the engine, as the desk names them.
HOLDERS = {"learner": "The Guitar Learner", "gestures": "Gesture listening"}
# Holders beside whom the guitar may still be heard through the unit: that route is made on the unit and opens no
# audio driver, and gestures are for playing the sound on show. (The Guitar Learner records the dry guitar alone.)
GUITAR_GOES_ON = frozenset({"gestures"})
STRINGS_QUIET = 1.      # the strings count as quiet once no note has been played for this long (seconds)
# What plays: the guitar ("guitar"), or the phrase round and round ("phrase") or once through ("once").
PHRASE_MODES = ("phrase", "once")
ONCE_TAIL = .8          # a pass played once is heard out this long after the phrase, for its echoes to ring


@dataclass(frozen=True)
class State:
    a: str | None = None              # sound ids
    b: str | None = None
    side: str = "b"
    mode: str | None = None           # None, "guitar" or "phrase"
    shown: str | None = None          # "pair" or "recall" once A and B are ready on the unit
    round: str | None = None          # the round on show
    status: str = ""
    status_kind: str = ""             # "fault" when the status says something went wrong or was refused
    working: str = ""                 # what is being done, while it is: the newest of `Studio.doing`
    instrument: str = "Charvel"
    song: str | None = None           # the song the round on show is for
    revision: int = 0                 # bumped whenever A or B changes
    blind: bool = False               # names hidden while comparing; A and B may have been swapped
    scene: int = 0                    # the scene heard, 0 to 7


SESSION = "session.json"
SET_ASIDE = "session.set-aside.json"
# What is on show, kept so that the next start puts it back: never playing, never blind.
KEPT = ("a", "b", "side", "round", "scene", "song", "instrument")


class Studio:
    def __init__(self, host, folder=None, *, assistant=None, phrase=None):
        self.folder = Path(folder or knowledge.home())
        self.store = Store(self.folder)
        self.rounds = Rounds(self.folder)
        self.host = host
        self.assistant = assistant or Assistant(self.folder)
        self.thinking = ThreadPoolExecutor(max_workers=2, thread_name_prefix="assistant")
        self.reading = ThreadPoolExecutor(max_workers=1, thread_name_prefix="takes")
        self.state = State()
        self.listeners = []
        self.history = []                 # earlier (state, knob positions), for undo
        self.undo_names = []              # what each of them came before, in a few words
        self.blind_swapped = False        # A and B were swapped for the blind comparison on show
        self.loop_since = None            # (phrase key, when it began to loop) while the phrase plays
        self.sounding = None              # (side, mode) the unit confirmed it plays, until it stops
        # The guitar is heard through the selected sound whenever A or B is ready and nothing else has the unit
        # (`guitar_back`); the player pauses it, and a failure to play pauses it too.
        self.live = True
        self.strings_heard = None         # (seconds the strings had been quiet, when the unit said so), or None
        self.jobs = []                    # (future, done, failed, the activity it ends)
        self.progress = []                # (handler, value) reported by the unit's worker, handled by `poll`
        self.doing = {}                   # what is going on, by key, oldest first: the working line shows the newest
        self.toward = {}                  # the side a request or a revision under way will put its sound on, by key
        self.ear = Listening(self)        # listening while the unit is free, and whether a measurement holds
        self.asking = Requests(self)      # the requests with the assistant
        self.guitar = Guitar(self)        # the takes, and what the instrument puts out
        self.marks = Marks(self)          # the player's marks, choices and songs
        self.remark = None                # the last remark that moved knobs: {"text", "sound", "turn", "before", "after"}
        # The unit.
        self.opened = False               # the engine has noted the player's preset: the unit may be borrowed
        self.firmware = None              # the unit's firmware, as it said when it opened
        self.retry_open_at = None         # when to try opening the unit again after it refused
        self.watched_at = 0.              # when the unit was last asked whether the player moved it
        self.said_stale = False           # the remark about tables from another firmware was made
        self.showing = None               # the making-ready of A and B in flight
        self.shows = self.plays = 0       # counted, so that only the newest one's outcome is acted on
        self.level_sent = {}              # side -> the level and peak limit last asked of the unit
        self.prepared_audition = None     # temporary chains and their clean level compensation
        self.refused_audition = None
        self.unit_scene = 0               # the scene last asked of the unit
        self.hearing_base = set()         # sides heard without their Shape knobs for now
        self.audio_held_by = None         # who holds the audio input outside the engine: a key of HOLDERS
        self.recording = None             # the take being recorded: (when it began, or None while the input
                                          # opens; how many seconds), while "record" is being done
        self.audio_quiet = None           # the future of the `quiet` sent for them
        # The phrase.
        self.phrase = phrase              # (id, mono dry audio: the part of the take that loops) or None
        self.phrase_whole = None          # (the whole take, where the loop starts, where it ends) or None
        # The session.
        self.desk_session = {}            # the window's part of session.json (the open panel)
        self.restored = False             # the last session was put back and waits for the unit
        self.notice = ""                  # said once, when the unit opens
        self.listening = None             # the Levels tool's listening level, kept here by it
        self.drawing_fault = None         # the last fault of a listener, so that it is logged once
        self.aside_said = 0               # how many of the files the store set aside have been said

    # ---- state ----

    def set(self, **changes):
        """Change what is on show. `working=` from a caller that keeps its own working line (the side tools) counts
        as one activity among the others; the studio's own work uses `begin` and `end`."""
        if "working" in changes:
            text = changes.pop("working")
            self.doing.pop("caller", None)
            if text:
                self.doing["caller"] = text
        self._set(**changes)

    def _set(self, **changes):
        if "status" in changes:
            changes.setdefault("status_kind", "")      # a new status is plain unless it says it is a fault
        before = self.state
        self.state = replace(self.state, **changes, working=self._working())
        if self.state.mode not in PHRASE_MODES:
            self.loop_since = None
        if self.state.mode is None:
            self.sounding = None
        if any(getattr(before, key) != getattr(self.state, key) for key in KEPT):
            self.remember()
        for listener in list(self.listeners):
            try:
                listener(self.state)
            except Exception as error:  # noqa: BLE001  a fault in drawing must not cut an action short
                said = f"{type(error).__name__}: {error}"
                if said != self.drawing_fault:
                    self.drawing_fault = said
                    self.log_error(error, traceback.format_exc())

    def _working(self):
        return next(reversed(self.doing.values()), "")

    def begin(self, key, text, **changes):
        """Something that takes time has begun: `text` is the working line until it ends or something newer begins."""
        self.doing.pop(key, None)
        self.doing[key] = text
        self._set(**changes)

    def end(self, key, **changes):
        self.doing.pop(key, None)
        self._set(**changes)

    def say(self, text):
        self._set(status=text)

    def fault(self, text):
        """Say that something went wrong or could not be done: the desk shows it in the colour of a fault."""
        self._set(status=text, status_kind="fault")

    def sound(self, side):
        sound_id = getattr(self.state, side)
        return self.store.sound(sound_id) if sound_id else None

    # ---- what the parts do, under the names the window, the tools, the seat and the tests call ----

    def evidence(self, sound):
        """What was measured of `sound` as it plays now, or None (`Listening.evidence`)."""
        return self.ear.evidence(sound)

    def unheard(self, sound):
        """Why `sound` could not be listened to as it plays now, or None."""
        return self.ear.unheard(sound)

    def _current(self, inputs):
        return self.ear.current(inputs)

    hearing = property(lambda self: self.ear.hearing)
    shaping = property(lambda self: self.ear.shaping)
    settle_seconds = property(lambda self: self.ear.settle_seconds,
                              lambda self, seconds: setattr(self.ear, "settle_seconds", seconds))
    idle_seconds = property(lambda self: self.ear.idle_seconds,
                            lambda self, seconds: setattr(self.ear, "idle_seconds", seconds))

    def new_round(self, text, **options):
        """Three designs for `text`, the first of which goes on B (`Requests.new_round`)."""
        self.asking.new_round(text, **options)

    def more(self, **options):
        self.asking.more(**options)

    def revise(self, sound, text, side="b"):
        self.asking.revise(sound, text, side)

    def cancel(self):
        """Drop every request the assistant is working on for the player. True when there was one."""
        return self.asking.cancel()

    def cancellable(self):
        return self.asking.cancellable()

    def _request(self, quietly=False):
        return self.asking.signal(quietly)

    preparing = property(lambda self: self.asking.preparing)

    def recorded_phrases(self):
        """The takes recorded, newest first, as their records."""
        return self.guitar.recorded()

    def put_take_away(self, take_id):
        """Move a take out of the takes, into "phrases put away" in the data folder: it is no longer listed or
        taken into what the guitar puts out, and moving its folder back brings it back. The phrase in use stays."""
        if self.phrase and self.phrase[0] == take_id:
            self.say("That take is the phrase in use: choose another first.")
            return
        source = self.folder / "phrases" / take_id
        if not (source / "phrase.json").exists():
            return
        target = self.folder / "phrases put away"
        target.mkdir(exist_ok=True)
        source.replace(target / take_id)
        self.guitar.version += 1            # what the guitar puts out is worked out again without it
        self._set(status="Take put away.")

    def guitar_profile(self):
        """What the current instrument puts out, as (profile, lines), worked out here and now."""
        return self.guitar.now()

    def guitar_lines(self):
        """The same as lines for the Guitar panel, without waiting (`Guitar.lines`)."""
        return self.guitar.lines()

    def guitar_summary(self):
        """What the instrument puts out at each setting, once worked out (`Guitar.summary`)."""
        return self.guitar.summary()

    def step_by(self, places):
        """Put the design so many places on from the one on B in the round on show, stopping at either end."""
        found = self.round()
        if not found or not found["candidates"]:
            return
        ids = [item["sound"] for item in found["candidates"]]
        at = ids.index(self.state.b) if self.state.b in ids else 0
        to = max(0, min(len(ids) - 1, at + places))
        if to != at or self.state.b not in ids:
            self.step(to)

    profile_version = property(lambda self: self.guitar.version)

    def mark(self, word, side=None):
        """Put a mark on the sound on a side, or take it off (`Marks.mark`)."""
        self.marks.mark(word, side)

    def taste_lines(self):
        return self.marks.taste_lines()

    def add_part(self, part, scene=None):
        self.marks.add_part(part, scene)

    # ---- the session, kept between starts ----

    def remember(self, **desk):
        """Keep what is on show (and anything the desk adds, such as the open panel) for the next start."""
        record = {key: getattr(self.state, key) for key in KEPT}
        whole = self.phrase_whole
        if self.phrase:
            record["phrase"] = {"id": self.phrase[0], **({"start": int(whole[1]), "end": int(whole[2])}
                                                         if whole else {})}
        record["desk"] = self.desk_session = {**self.desk_session, **desk}
        try:
            write_whole(self.folder / SESSION, json.dumps(record, indent=1))
        except OSError:
            pass

    def restore(self):
        """Put back what was on show when the app last closed, as far as it still exists. Returns the record (the
        phrase and the desk's part are the desk's to use), or None. The unit is made ready when it opens.

        A session that cannot be read is set aside (`set_aside`) and the desk starts empty."""
        try:
            text = (self.folder / SESSION).read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            record = json.loads(text)
            if not isinstance(record, dict):
                raise ValueError("the session is not a record")
            changes = {}
            if record.get("side") in ("a", "b"):
                changes["side"] = record["side"]
            if isinstance(record.get("scene"), int) and 0 <= record["scene"] <= 7:
                changes["scene"] = record["scene"]
            for key in ("song", "instrument"):
                if isinstance(record.get(key), str) and record[key]:
                    changes[key] = record[key]
            changes.update(a=record.get("a") if self._loads(record.get("a")) else None,
                           b=record.get("b") if self._loads(record.get("b")) else None)
            found = self.rounds.get(record.get("round")) if isinstance(record.get("round"), str) else None
            if found is not None and all(self._loads(item.get("sound")) for item in found.get("candidates", [])):
                changes["round"] = record["round"]
            desk = record.get("desk")
            self.desk_session = dict(desk) if isinstance(desk, dict) else {}
            from lab.tools.levels import at_rest, saved_settings

            listening = saved_settings(self.desk_session.get("listening"))
            self.listening = None if at_rest(listening) else listening
        except Exception as error:  # noqa: BLE001  whatever is wrong with it, the desk must still open
            self.set_aside(error)
            return None
        self.state = replace(self.state, **changes)
        self.restored = bool(self.state.a or self.state.b)
        return record

    def _loads(self, sound_id):
        """Whether a sound's file is there and this version can read it."""
        if not isinstance(sound_id, str) or not sound_id:
            return False
        try:
            self.store.sound(sound_id)
        except Exception:  # noqa: BLE001  missing, cut short, or written by another version
            return False
        return True

    def set_aside(self, error):
        """The last session could not be put back: keep its file under another name, start empty, and say so."""
        try:
            (self.folder / SESSION).replace(self.folder / SET_ASIDE)
            where = f" It was set aside as {SET_ASIDE} in the data folder."
        except OSError:
            where = ""
        self.log_error(error, "".join(traceback.format_exception(error)))
        said = f"The last session could not be put back ({error}), so the desk starts empty.{where}"
        self.desk_session, self.restored = {}, False
        self.notice = f"{self.notice} {said}".strip()
        self.state = replace(State(), status=said, working=self._working())

    # ---- undo ----

    def checkpoint(self, what=""):
        """Keep what is on show and the knob positions of both sounds, for undo. `what` names the change that
        follows, for the desk to say what Undo would take back."""
        knob_values = {}
        for side in ("a", "b"):
            sound = self.sound(side)
            if sound is not None:
                knob_values[sound.id] = dict(sound.knobs)
        self.history = (self.history + [(self.state, knob_values)])[-40:]
        self.undo_names = (self.undo_names + [what])[-40:]

    def undo_words(self):
        """What Undo would take back, in a few words, or "Nothing to undo"."""
        if not self.history:
            return "Nothing to undo"
        what = self.undo_names[-1] if self.undo_names else ""
        return f"Undo {what}" if what else "Undo the last change"

    def undo(self):
        """Put back the sounds on show and their knob positions as they were. Playing is not undone: what plays
        (or not) stays as it is."""
        if not self.history:
            self.say("Nothing to undo.")
            return
        before, knob_values = self.history.pop()
        self._used("undo", self.undo_names[-1] if self.undo_names else "")
        if self.undo_names:
            self.undo_names.pop()
        self.remark = None
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
        # The scene stays as the unit has it: the knob positions put back are sent for the scene heard now.
        self._set(a=before.a, b=before.b, side=side, round=before.round, song=before.song,
                  blind=False, revision=self.state.revision + (0 if same_pair else 1), status="Undone.")
        if same_pair and self.state.shown:
            self.hearing_base.clear()
            for side in ("a", "b"):
                sound = self.sound(side)
                if sound is not None:
                    self.later(self.host.call("turn", side, sound.knobs), lambda moves: None)
        else:
            self._show()

    # ---- work done elsewhere ----

    def worked_on(self):
        """The sides whose tile something is being done for now: a request or a revision on its way to it, or a
        silent listen to the sound on it."""
        for key in [key for key in self.toward if key not in self.doing]:
            del self.toward[key]
        sides = set(self.toward.values())
        hearing = self.ear.hearing
        if hearing:
            sides |= {side for side in ("a", "b") if getattr(self.state, side) == hearing}
        return sides

    def later(self, future, done, failed=None, doing=None):
        """Handle a future's result on the window's thread. `doing` is the activity that ends with it."""
        self.jobs.append((future, done, failed, doing))

    def poll(self):
        """Handle every finished piece of work, then use the unit if it is free. Called from the window's clock.

        A fault is said on the line and written with its trace to errors.log in the data folder: the trace from
        where the unit is held when the fault came from there. Outcomes of ordinary use (stopped, cancelled, the
        unit not the app's to use just now) are said and not logged."""
        finished, waiting = [], []
        for job in self.jobs:                  # each future looked at once: one that finishes now waits a tick
            (finished if job[0].done() else waiting).append(job)
        self.jobs = waiting
        while self.progress:
            handler, value = self.progress.pop(0)
            handler(value)
        for future, done, failed, doing in finished:
            if doing is not None:
                self.doing.pop(doing, None)
            try:
                result = future.result()
            except Exception as error:  # noqa: BLE001
                if not _expected(error):
                    self.log_error(error, getattr(error, "trace", "") or "".join(traceback.format_exception(error)))
                self._handle(failed or self._failed, error)
                continue
            self._handle(done, result)
        if len(self.store.aside) > self.aside_said:
            # A file of the player's that could not be read (a slip in a hand edit) was set aside, not written over.
            spare = self.store.aside[self.aside_said]
            self.aside_said += 1
            self._set(status=f"{spare.name.removesuffix('.bad')} could not be read: kept as {spare.name} in the "
                             "data folder; the app's own is used meanwhile.")
        if self.state.working != self._working():
            self._set()
        now = time.monotonic()
        if (self.state.mode == "once" and self.loop_since and self.phrase
                and now - self.loop_since[1] > len(self.phrase[1]) / RATE + ONCE_TAIL):
            self.stop()                     # played once through: the guitar is heard again, unless it is muted
        self.guitar_back()
        self.ear.use_free_unit(now)
        if self.retry_open_at is not None and now >= self.retry_open_at:
            self.retry_open_at = None
            self.open()
        if self.state.mode and not self.host.busy and now - self.watched_at > 1.5:
            self.watched_at = now
            self.later(self.host.call("watch"), self._watched, lambda error: None)

    def _handle(self, handler, value):
        try:
            handler(value)
        except Exception as error:  # noqa: BLE001
            self.log_error(error, traceback.format_exc())
            self.fault(f"Something went wrong: {error}. The details are in errors.log in the data folder.")

    def _failed(self, error):
        """What a failure says when its job has nothing of its own to say. A command dropped by Stop says nothing:
        Stop has said it."""
        if getattr(error, "kind", "") != "Stopped":
            self.fault(str(error))

    def _watched(self, result):
        quiet_for = result.get("quiet_for")
        self.strings_heard = None if quiet_for is None else (quiet_for, time.monotonic())
        if result.get("moved") and self.state.mode:
            self.ear.left_alone()
            self._set(mode=None, shown=None, status="Another preset is selected on the Quad Cortex. Click A or "
                                                    "B to come back.")

    def strings_quiet(self, now):
        """How long the strings have been quiet while the guitar plays, in seconds: 0 while the unit last said they
        sounded within `STRINGS_QUIET` (a pause between notes is not quiet); None when it has not said (the
        guitar is not playing, or its dry input could not be watched)."""
        if self.state.mode != "guitar" or self.strings_heard is None:
            return None
        quiet_for, at = self.strings_heard
        return quiet_for + (now - at) if quiet_for >= STRINGS_QUIET else 0.

    def guitar_back(self):
        """While the guitar is live (`live`), it plays through the selected sound whenever nothing else has the
        unit: after a silent listen, a save, a recording or a tool's measurement, and once A and B are first
        ready. Not while the player has chosen a preset of their own on the unit, nor while the Guitar Learner
        has the input."""
        if not (self.live and self.opened and self.state.mode is None and self.state.shown is not None
                and self.ear.hearing is None and not self.ear.looking and not self.ear.away
                and self.showing is None and not self.host.busy and self.host.failed is None
                and self.audio_held_by in (None, *GUITAR_GOES_ON) and "record" not in self.doing
                and self.sound(self.state.side) is not None):
            return
        self.ear.borrowed = False           # playing makes A and B ready again on the unit, knobs as they stand
        self.play("guitar", by_itself=True)

    def pause(self):
        """The player pauses the guitar (to tune, to talk, to use the unit by hand): nothing plays until they play
        again."""
        self._used("mute", "guitar")
        self.live = False
        self.stop()

    def _used(self, event, what):
        """Note in the usage log what the player did on the desk (`lab.tools.usage`): which panels are opened,
        how requests are read, what is played and what is taken back. Kept on this computer; it tells what to
        move nearer or further, never what to drop."""
        from lab.tools import usage

        usage.record(self.folder, event, what, side=self.state.side, playing=self.state.mode or "")

    def log_error(self, error, trace):
        try:
            with open(self.folder / "errors.log", "a", encoding="utf-8") as log:
                log.write(f"\n--- {now()} {type(error).__name__}: {error}\n{trace}")
        except OSError:
            pass

    # ---- the unit ----

    def open(self):
        self.begin("open", "Connecting to the Quad Cortex…")

        def opened(result):
            self.opened, self.firmware = True, result.get("firmware")
            note = " Gave back the preset a crash had left borrowed." if result.get("recovered") else ""
            if self.notice:
                note += " " + self.notice
                self.notice = ""
            note += self._stale_words()
            self._set(status=note.strip())
            if self.listening is not None:
                self.later(self.host.call("lab.tools.levels.install", self.listening), lambda levels: None)
            if self.restored:
                self.restored = False
                shown = [f"{side.upper()} {self.sound(side).name}" for side in ("a", "b") if self.sound(side)]
                found = self.round()
                self.say("Back where you left off: " + ", ".join(shown)
                         + (f"; round: {found['request']}" if found else "") + "." + note)
                self._show()

        def refused(error):
            self.fault(f"{error}")
            if self.host.failed is None:
                self.retry_open_at = time.monotonic() + 5     # tried again until the unit lets it

        self.later(self.host.call("open"), opened, refused, doing="open")
        # The player's presets, for the Sounds panel and for requests that name one: read again when the last
        # read is over half a day old (it holds the unit about ten seconds). Quiet if it fails.
        listing = self.folder / "directory.json"
        if not listing.exists() or time.time() - listing.stat().st_mtime > 12 * 3600:
            self.later(self.host.call("lab.qc.library.directory", folder=str(self.folder)),
                       lambda entries: None, lambda error: None)

    def _stale_words(self):
        """A remark, made once, when the unit's firmware (as it said when it opened) is not the one some measured
        tables were made on. Nothing when the unit did not say."""
        if self.said_stale or not self.firmware or not knowledge.stale(self.firmware):
            return ""
        self.said_stale = True
        return f" Some measured tables were made on another firmware than the unit's {self.firmware}."

    def put(self, side, sound, **changes):
        """Put a sound on A or B, and get both ready on the unit. `changes` are other things on show that change
        with it (the round, the song), so that one Undo takes all of it back."""
        self.checkpoint(f"{sound.name} on {side.upper()}")
        self.store.save(sound)
        self._set(**{side: sound.id, "side": side, "revision": self.state.revision + 1, **changes})
        self._show()

    def _audition(self, a, b):
        from lab.tools.levels import audition_gain, saved_settings

        reference = self.media_reference
        settings = saved_settings(self.listening)
        scene = self.state.scene
        audition = []
        for sound in (a, b):
            chain, boost = (sound.chain if sound else None), 0.
            if sound and reference and settings["match"]:
                chain, boost = audition_gain(chain, self.ear.level_evidence(sound), reference["lufs"], scene,
                                             settings["loudness_db"])
            audition.append((chain, boost, scene if sound and sound.chain.scenes_in_use() > 1 else None))
        return tuple(audition)

    def _show(self, *, native=False):
        a, b = self.sound("a"), self.sound("b")
        if a is None and b is None:
            return
        revision = self.state.revision
        self.shows += 1
        key = self.showing = ("show", self.shows)
        self.hearing_base.clear()
        self.ear.made_ready()
        # A scene the sounds now on show do not have is not kept.
        scene = self.state.scene
        if scene and not any(sound.chain.scenes_in_use() > scene for sound in (a, b) if sound):
            scene = 0
        self.begin(key, "Getting A and B ready on the Quad Cortex…", shown=None, scene=scene)
        planned = self._audition(a, b) if not native else tuple(
            (sound.chain if sound else None, 0., None) for sound in (a, b))
        if planned == self.refused_audition:
            planned = tuple((sound.chain if sound else None, 0., None) for sound in (a, b))

        def shown(how):
            if self.showing == key:
                self.showing = None
            if self.state.revision != revision:
                return
            self.prepared_audition = planned
            self._set(shown=how)
            if self.state.scene != self.unit_scene:
                self.unit_scene = self.state.scene
                self.later(self.host.call("scene", self.state.scene), lambda result: None)
            if self.state.mode:
                self.play(self.state.mode)

        def failed(error):
            if self.showing == key:
                self.showing = None
            if self.state.revision != revision or getattr(error, "kind", "") == "Stopped":
                return
            if any(boost for _, boost, _ in planned):
                self.refused_audition = planned
                self._show(native=True)
                return
            # Nothing is ready, so nothing plays: the desk must not say it does, nor try again by itself.
            self.live = False
            self._set(mode=None, status=str(error), status_kind="fault")

        self.later(self.host.call("show", *(chain for chain, _, _ in planned)), shown, failed, doing=key)

    def play(self, mode, side=None, by_itself=False):
        """Play the guitar ("guitar") or the phrase ("phrase") through the sound on a side. `by_itself` when the
        guitar comes back without the player asking (`guitar_back`)."""
        side = side or self.state.side
        if self._input_held(mode):
            return
        if self.sound("a") is None and self.sound("b") is None:
            self.say("Nothing is on A or B yet.")
            return
        if mode == "guitar":
            self.live = True
        self.strings_heard = None
        if not by_itself:
            self.ear.came_back()
            if mode != self.state.mode:
                self._used("play", mode)
        # A silent listen has the unit: the player comes first, and it is listened to again later.
        interrupted = self.ear.hearing is not None
        if interrupted:
            self.host.stop()                # which also drops whatever waited behind the listen
            self.level_sent, self.showing = {}, None
        if self.state.shown is None:
            self._set(mode=mode, side=side, status=GETTING_READY)
            if self.showing is None:
                self._show()
            return
        sound = self.sound(side)
        if sound is None:
            self.say(f"Nothing is on {side.upper()} yet.")
            return
        if mode in PHRASE_MODES and self.phrase is None:
            self.say("Record or choose a phrase first.")
            return
        if mode == "once" and self.state.mode != "once":
            self.loop_since = None              # a pass played once starts at the beginning
        if self.state.mode is None:
            planned = self._audition(self.sound("a"), self.sound("b"))
            if (planned != self.prepared_audition and planned != self.refused_audition
                    and any(boost for pair in (planned, self.prepared_audition or ()) for _, boost, _ in pair)):
                self._set(mode=mode, side=side)
                self._show()
                return
        phrase = self.phrase[1] if mode in PHRASE_MODES else None
        if interrupted:
            # Stopping the listen dropped what waited behind it, so the scene is asked for again (the knobs and
            # the levels are, below).
            self.later(self.host.call("scene", self.state.scene), lambda result: None)
        self._set(mode=mode, side=side)
        self._match_levels()
        self.hearing_base.discard(side)
        self.plays += 1
        ticket = self.plays

        def playing(result):
            sound = self.sound(side)
            if sound is None:
                return
            if not sound.played:
                sound.played = True
                self.store.save(sound)
            self.sounding = (side, mode)
            if mode == "guitar":
                self.watched_at = 0.        # asked at once how long the strings have been quiet
            if mode in PHRASE_MODES and (self.loop_since is None or self.loop_since[0] != self.phrase_key()):
                self.loop_since = (self.phrase_key(), time.monotonic())     # the loop starts again with a new phrase
            if sound.knobs:
                self.later(self.host.call("turn", side, sound.knobs), lambda moves: None)
            # The tile and the buttons show what plays; the line says only what they cannot.
            gap = "" if self.state.blind else self._level_gap(side, sound)
            if gap:
                self.say(f"{sound.name}{gap}")
            elif self.state.status == GETTING_READY:
                self.say("")                # it plays now: the tile shows it
            else:
                self._set()                 # drawn again: the tile shows it plays

        def failed(error):
            # Nothing plays: the desk must not go on saying it does. A play that Stop dropped, or that a newer
            # one has replaced, has nothing to say.
            # The guitar is paused then, so that it is not tried again and again.
            if ticket == self.plays and getattr(error, "kind", "") != "Stopped":
                self.live = False
                self._set(mode=None, status=str(error), status_kind="fault")

        # The dry guitar is watched while it plays, for when the strings are quiet: not beside gesture listening,
        # which reads the dry input itself.
        self.later(self.host.call("play", side, mode, phrase, strings=self.audio_held_by is None), playing, failed)

    def _match_levels(self):
        """Play A and B equally loud, when both have been measured: the louder is turned down to the quieter.

        Matched by integrated loudness (ITU-R BS.1770, LUFS), which follows perceived loudness across sounds of
        different brightness better than a plain level does (docs/TONE_RESEARCH.md, 13); by RMS level only when one of
        the two was measured before loudness was recorded. A level the unit already has is not sent again."""
        from lab.qc.engine import PREVIEW_DB

        sounds = {side: self.sound(side) for side in ("a", "b")}
        heard = {side: self.ear.level_evidence(sound) for side, sound in sounds.items() if sound}
        heard = {side: record for side, record in heard.items()
                 if record and record.get("level_db") is not None and record["level_db"] > -90}
        key = "loudness_lufs" if all(record.get("loudness_lufs") is not None for record in heard.values()) \
            else "level_db"
        levels = {side: record[key] for side, record in heard.items()}
        limits = {side: -3. - record["true_peak_db"] for side, record in heard.items()
                  if record.get("true_peak_db") is not None}
        if self.media_reference is not None:
            limits = {side: limit - (2. if heard[side].get("short") else 0.) for side, limit in limits.items()}
            measured = {side: record["loudness_lufs"] for side, record in heard.items()
                        if record.get("loudness_lufs") is not None and side in limits and not record.get("clipped")}
            target = self.media_reference["lufs"]
            wanted = {side: min(target - measured[side], limits[side]) if side in measured else 0.
                      for side, sound in sounds.items() if sound is not None}
        elif len(levels) < 2:
            # Nothing fair to match by: both play at the preview level, and no earlier trim stays on either.
            wanted = {side: PREVIEW_DB for side, sound in sounds.items() if sound is not None}
        else:
            baseline = min(PREVIEW_DB + min(levels.values()),
                           min((levels[side] + limit for side, limit in limits.items()), default=float("inf")))
            wanted = {side: baseline - level for side, level in levels.items()}
        for side, level in wanted.items():
            boost = self.prepared_boost(side)
            level -= boost
            limit = limits.get(side)
            if limit is not None:
                limit -= boost
            request = round(level, 3), limit
            if self.level_sent.get(side) != request:
                self.level_sent[side] = request
                self.later(self.host.call("level", side, level, peak_limit=request[1]), lambda result: None)

    def prepared_boost(self, side):
        if self.prepared_audition is not None:
            _, extra, scene = self.prepared_audition[side == "b"]
            if scene is None or scene == self.state.scene:
                return extra
        return 0.

    def _level_gap(self, side, sound):
        from lab.tools.levels import lane_headroom, saved_settings

        reference, evidence = self.media_reference, self.ear.level_evidence(sound)
        settings = saved_settings(self.listening)
        if not reference or not settings["match"] or settings["muted"] or not evidence or any(
                evidence.get(key) is None for key in ("loudness_lufs", "true_peak_db")):
            return ""
        boost = self.prepared_boost(side)
        peak_room = -3. - evidence["true_peak_db"] - (2. if evidence.get("short") else 0.)
        maximum = evidence["loudness_lufs"] + min(peak_room, boost + lane_headroom(sound.chain, self.state.scene))
        gap = reference["lufs"] + settings["loudness_db"] - maximum
        return f" · about {gap:.0f} dB below music target" if gap >= 3. else ""

    def scene(self, scene):
        """Hear another scene of the sounds on show."""
        self.unit_scene = scene
        self._set(scene=scene)
        self.later(self.host.call("scene", scene), lambda result: None)
        if self.state.mode:
            self._match_levels()

    def scenes(self, side=None):
        """The scenes of the sound on a side that make a difference, as (index, label)."""
        sound = self.sound(side or self.state.side)
        if sound is None:
            return []
        count = sound.chain.scenes_in_use()
        return [(scene, sound.chain.labels[scene]) for scene in range(count)] if count > 1 else []

    def select(self, side):
        """Hear the sound on a side. After the player chose a preset of their own on the unit, this is the way back
        to the desk's sounds."""
        if self.state.mode or (self.live and self.ear.away):
            self.play(self.state.mode or "guitar", side)
        else:
            self._set(side=side)

    def stop(self):
        """Stop what plays. Nothing is said: the buttons and the tile show it."""
        self.host.stop()
        self.level_sent = {}            # a level that waited behind what was stopped never reached the unit
        self.later(self.host.call("quiet"), lambda result: None)
        self._set(mode=None)

    def close(self):
        self.remember()
        for signal in (*self.asking.requests, *self.asking.background):
            signal.set()
        self.host.close()
        self.thinking.shutdown(wait=False, cancel_futures=True)
        self.reading.shutdown(wait=False, cancel_futures=True)
        # Nothing is drawn or handled any more. A worker that is still ending holds the studio for a moment longer:
        # it must not be the last to hold the window with it (Tk objects may only be freed on the window's thread).
        self.listeners.clear()
        self.jobs = []

    # ---- who has the audio input ----

    def hold_audio(self, who):
        """Take the audio input for something outside the engine (`who` is "learner" or "gestures"): whatever plays
        stops, and nothing here opens the input until `release_audio`. Returns the future of the unit going quiet,
        after which the driver is free. Asking again for the same holder returns the same future; while someone
        else holds the input it raises.

        Beside gesture listening the guitar may still be heard (`GUITAR_GOES_ON`): if it was playing, it comes
        back once the unit has gone quiet, and Guitar can be pressed while the hold lasts."""
        if self.audio_held_by not in (None, who):
            raise RuntimeError(self.held_words())
        if self.audio_held_by == who and self.audio_quiet is not None:
            return self.audio_quiet
        goes_on = who in GUITAR_GOES_ON and self.state.mode == "guitar"
        self.audio_held_by = who
        self.host.stop()
        self.level_sent = {}
        self.audio_quiet = self.host.call("quiet")

        def quiet(result):
            if goes_on and self.audio_held_by == who and self.state.mode is None:
                self.play("guitar")

        self.later(self.audio_quiet, quiet, lambda error: None)
        self._set(mode=None)
        return self.audio_quiet

    def release_audio(self, who):
        if self.audio_held_by == who:
            self.audio_held_by = self.audio_quiet = None
            self._set()

    def held_words(self):
        """Who has the audio input, as a sentence, or "" when nobody does."""
        who = self.audio_held_by
        return f"{HOLDERS.get(who, str(who).capitalize())} is using the input." if who else ""

    def _input_held(self, mode=None):
        """Say who has the input, if someone does and what is asked for (`mode`, when it is playing) cannot go on
        beside them. True when it cannot."""
        if self.audio_held_by is None or (mode == "guitar" and self.audio_held_by in GUITAR_GOES_ON):
            return False
        self.say(self.held_words())
        return True

    # ---- measurements, and whether they hold ----

    def phrase_key(self):
        """Which phrase, and which part of it, a measurement was made on: a measurement holds only for that."""
        if not self.phrase:
            return None
        whole = self.phrase_whole
        return f"{self.phrase[0]}@{whole[1]}-{whole[2]}" if whole else self.phrase[0]

    # ---- listening while the unit is free ----

    # ---- shaping ----

    def turn(self, values, side=None):
        side = side or self.state.side
        sound = self.sound(side)
        if sound is None:
            return
        before = dict(sound.knobs)
        moved = [knobs.KNOBS[key][0] for key in knobs.KNOBS
                 if before.get(key, 0) != (values.get(key) or 0)]
        self.checkpoint(f"{', '.join(moved[:2])}{' and more' if len(moved) > 2 else ''} on {side.upper()}"
                        if moved else "")
        sound.knobs = {key: value for key, value in values.items() if value}
        self.store.save(sound)
        self.hearing_base.discard(side)
        if self.state.shown:
            self.later(self.host.call("turn", side, sound.knobs), lambda moves: None)
        turned = {key for key in set(before) | set(sound.knobs) if before.get(key, 0) != sound.knobs.get(key, 0)}
        if turned:
            self.ear.check_later(sound.id, turned)
        self._set()

    def base(self):
        """Hear the sound on show without its Shape knobs, or with them again."""
        side = self.state.side
        sound = self.sound(side)
        if sound is None or not sound.knobs:
            self.say("Nothing shaped to compare with.")
            return
        if not self.state.shown:
            self.say("A and B are not ready on the Quad Cortex yet: try again once they are.")
            return
        without = side not in self.hearing_base
        self.hearing_base ^= {side}
        self.later(self.host.call("turn", side, {} if without else sound.knobs), lambda moves: None)
        self._set(status=f"{sound.name}: " + ("the base, without the Shape knobs" if without else "shaped"))

    # ---- knobs that check themselves ----

    def named(self, row, column, setting, side=None):
        """A copy of the sound with one block set to one of its model's named settings, put on the same side."""
        from lab.sound import catalog

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
        self.say(f"{block.name}: {setting}.")

    def difference(self):
        """What listening heard differ between A and B, in plain words, when both were heard as they play now."""
        a, b = self.sound("a"), self.sound("b")
        if not (a and b):
            return ["Put a sound on both A and B to compare them."]
        # What was measured of each as it plays now: this take, this scene, these knob positions.
        ha, hb = self.ear.evidence(a), self.ear.evidence(b)
        if ha and hb and "probe" in ha and "probe" in hb:
            return difference(ha, hb)
        return [line for line in (self.ear.not_measured("A", a), self.ear.not_measured("B", b)) if line]

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
        if sound and text.casefold().rstrip(".! ") in ("add parallel space", "add a stereo layer"):
            self.add_parallel_space()
            return
        reading = words.read(text)
        moves = said = None
        base = dict(sound.knobs) if sound else {}
        remark = None
        if reading and sound:
            ways = words.usable(reading, set(knobs.available(sound.chain)))
            last = self.remark
            # Said again, with the knobs still where that remedy left them: it was not it.
            again = (last is not None and (last["text"], last["sound"]) == (text.casefold(), sound.id)
                     and last["after"] == sound.knobs)
            turn = last["turn"] + 1 if again else 0
            if ways and turn < len(ways):
                if again:
                    base = dict(last["before"])       # the earlier remedy is taken back, and the next one tried
                moves = dict(ways[turn].moves)
                remark = {"text": text.casefold(), "sound": sound.id, "turn": turn, "before": dict(base),
                          "asked": text, "next": ways[turn + 1].say if turn + 1 < len(ways) else None}
                said = f"{reading.said}: {ways[turn].say}."
            elif ways and again:
                # Every remedy this chain can perform was tried, and the last one is still on. Nothing is sent
                # to the assistant for a word said once more: that is asked for on purpose.
                self.say(f"Nothing more to try for “{reading.said.lower()}” on this chain. For a rework, say what to "
                         "change and what to keep.")
                return
        if moves is None and sound:
            moves = knobs.direction(text)
            said = text
        if moves and sound:
            values = dict(base)
            for key, step in moves.items():
                low = knobs.KNOBS[key][1]
                values[key] = round(max(low, min(1., values.get(key, 0.) + step)), 2)
            self.turn(values, side)
            sound = self.sound(side)          # as turn left it
            sound.notes.append({"at": now(), "text": text})
            self.store.save(sound)
            self.remark = {**remark, "after": dict(sound.knobs)} if remark else None
            self._used("ask", "remark" if remark else "direction")
            self.say(said)
            return
        named = self._preset_named(text)
        if named is not None:
            self._used("ask", "preset name")
            slot, name = named
            self.work_on(slot, name, note=f"That is your preset's name. For a new design in its spirit, "
                                          f"ask \"make {name}\".")
            return
        # The line under the tiles no longer says what it said before the request: the working line says what
        # was asked, until the answer comes.
        self._set(status="")
        if sound is not None and reads_as_change(text):
            self._used("ask", "revision")
            self.asking.revise(sound, text, side)
        else:
            self._used("ask", "new round")
            self.asking.new_round(text)

    def next_remedy(self):
        """What the last remark would try next if said again, while the knobs are still where it left them:
        the desk offers it as something to click. None when there is no next one."""
        remark, sound = self.remark, self.sound(self.state.side)
        if not remark or sound is None or remark["sound"] != sound.id or remark["after"] != sound.knobs:
            return None
        return remark.get("next")

    def try_next_remedy(self):
        """The last remark said again: its remedy is taken back and the next one tried."""
        if self.next_remedy() is not None:
            self.ask(self.remark["asked"])

    def _preset_named(self, text):
        """A request that is exactly the name of one of the player's presets on the unit means that preset."""
        from lab.qc.library import cached

        wanted = text.strip().casefold()
        found = [item for item in cached(self.folder) or [] if not item["slot"].get("is_factory")
                 and item["name"].strip().casefold() == wanted]
        return (found[0]["slot"], found[0]["name"]) if len(found) == 1 else None

    def _assets(self):
        from lab.qc.assets import cached

        return cached(self.folder) or []

    def read_assets(self, done=None):
        """Read the IRs and captures on the unit (about ten seconds) and remember them."""
        self.begin("assets", "Reading the IRs and captures on the Quad Cortex…")

        def read(records):
            self._set()
            if done:
                done(records)

        self.later(self.host.call("lab.qc.assets.listing", folder=str(self.folder)), read, doing="assets")

    def read_directory(self, done=None):
        """Read the presets on the unit (about ten seconds) and remember them."""
        self.begin("directory", "Reading the presets on the Quad Cortex…")

        def read(entries):
            self._set(status=f"{len(entries)} presets on the unit.")
            if done:
                done(entries)

        self.later(self.host.call("lab.qc.library.directory", folder=str(self.folder)), read, doing="directory")

    def use_asset(self, asset, *, has_cab=None, version=None):
        """A copy of the sound on B with one of the player's IRs in its cab, or a capture in place of
        its amp (and cab, when the capture has one)."""
        from lab.qc import assets

        side = self.state.side
        sound = self.sound(side)
        if sound is None:
            self.say(f"Put a sound on {side.upper()} first; the IR or capture goes into it.")
            return
        try:
            if asset.kind == "ir":
                chain, what = assets.with_ir(sound.chain, asset, keep_cab=True)
            else:
                chain, what = assets.with_capture(sound.chain, asset, has_cab=has_cab, version=version)
        except ValueError as error:
            self.say(str(error))
            return
        copy = Sound(sound.name, chain, {**sound.origin, "kind": "revised", "from": sound.id, "request": what},
                     parent=sound.id, explanation=sound.explanation, sources=list(sound.sources), knobs=dict(sound.knobs))
        self.put(side, copy)
        self.say(what)

    def add_parallel_space(self):
        from lab.sound.layers import parallel_space

        side = self.state.side
        sound = self.sound(side)
        if sound is None:
            return
        try:
            chain = parallel_space(sound.chain)
        except ValueError as error:
            self.say(str(error))
            return
        copy = Sound(sound.name, chain, {**sound.origin, "kind": "revised", "from": sound.id,
                                        "request": "Added parallel stereo echoes and plate"}, parent=sound.id,
                     explanation=sound.explanation, sources=list(sound.sources), knobs=dict(sound.knobs))
        self.put(side, copy)
        self.say("Added parallel stereo echoes and plate.")

    def _instrument(self):
        return self.store.instrument(self.state.instrument)

    def _listening(self):
        """What guitar music the player listens to most, if the listening history was analysed (personal; kept in
        the data folder)."""
        from lab.player.listening import prior

        try:
            record = json.loads((self.folder / "personal" / "listening.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return prior(record)

    # ---- rounds ----

    def round(self, round_id=None):
        return self.rounds.get(round_id or self.state.round)

    def go_round(self, round_id):
        found = self.round(round_id)
        if found is None:
            self.say("That round's file is gone.")
            return
        song = found.get("aim", {}).get("song")
        self.put("b", self.store.sound(found["candidates"][0]["sound"]), round=round_id,
                 song=song_name(song) if song else None)
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
            self.asking.more()

    def step(self, place):
        found = self.round()
        if found and 0 <= place < len(found["candidates"]):
            self.put("b", self.store.sound(found["candidates"][place]["sound"]))

    # ---- the player's marks, copying, blind comparison ----

    def copy_to_a(self):
        """A copy of B, as it is now, on A: a sound of its own, so turning B's knobs leaves it as it is. B stays
        the side selected: it is the one worked on."""
        if self.sound("b") is None:
            self.say("Nothing is on B to copy.")
            return
        self.copy_over(self.sound("b"), "a")
        self._set(side="b")

    def copy_over(self, sound, side):
        """A copy of `sound`, as it is now (its knobs, what was heard of it), put on `side`: dropping a tile or a
        design of the round on a tile. A copy, so that changing one leaves the other as it is."""
        copy = Sound(sound.name, sound.chain, {"kind": "revised", "from": sound.id, "request": COPY_REQUEST},
                     parent=sound.id, knobs=dict(sound.knobs), explanation=sound.explanation, heard=sound.heard)
        self.put(side, copy)

    def blind(self):
        """Hide the names, and swap A and B or not, at random. Calling it again shows what was where."""
        import random

        if self.state.blind:
            self.reveal()
            return
        if self.sound("a") is None or self.sound("b") is None:
            self.say("Blind comparison needs a sound on both A and B.")
            return
        self.blind_swapped = random.random() < .5
        if self.blind_swapped:
            self._set(a=self.state.b, b=self.state.a, revision=self.state.revision + 1)
            self._show()
        self._set(blind=True, status="")    # the names are "?" and the eye is struck through

    def reveal(self, said=""):
        """End a blind comparison: say which was which, and put A and B back on their own sides if they were
        swapped for it, so that what was being worked on is on B again and the round's chips light it."""
        a, b = self.sound("a"), self.sound("b")
        which = f"A was {a.name}, B was {b.name}" if a and b else ""
        if self.blind_swapped:
            self.blind_swapped = False
            self._set(a=self.state.b, b=self.state.a, side="b" if self.state.side == "a" else "a",
                      revision=self.state.revision + 1, blind=False,
                      status=f"{said}{which}; they are back on their own sides.")
            self._show()
        else:
            self._set(blind=False, status=f"{said}{which}.")

    # ---- presets on the unit ----

    def work_on(self, slot, name, *, note="", side=None):
        """Bring a preset onto one side, or onto A with a copy on B to change."""
        revision = self.state.revision
        self.begin("read", f"Reading {name}…")

        def read(result):
            chain, held_name = result
            original = Sound(held_name, chain, {"kind": "preset", "slot": slot})
            if side is not None:
                if self.state.revision == revision:
                    self.put(side, original, status=f"{held_name} on {side.upper()}.")
                return
            copy = Sound(held_name, chain, {"kind": "revised", "from": original.id}, parent=original.id)
            self.store.save(original)
            self.store.save(copy)
            self.checkpoint(f"bringing in {held_name}")
            self._set(a=original.id, b=copy.id, side="b", revision=self.state.revision + 1,
                      status=f"{held_name} is on A as saved, and on B to work on. {note}".strip())
            self._show()

        self.later(self.host.call("read", slot), read, doing="read")

    def save(self, name=None, sound_id=None, knob_values=None):
        """Save a sound to the unit. `sound_id` and `knob_values` are the sound and knobs the name was confirmed
        for; by default the sound on the selected side as it is now."""
        sound = self.store.sound(sound_id) if sound_id else self.sound(self.state.side)
        if sound is None:
            self.say("Nothing is on the selected side to save.")
            return
        knob_values = dict(sound.knobs if knob_values is None else knob_values)
        name = (name or sound.name)[:24]
        chain, level_note = self._saved_level(sound, knob_values)
        self.stop()
        self.begin("save", f"Saving {name} to the Tone Lab setlist…")

        def saved(result):
            slot, _ = result
            fresh = self.store.sound(sound.id)
            fresh.saved = {"slot": slot, "name": name, "knobs": knob_values}
            self.store.save(fresh)
            self.marks.into_song(fresh)
            self.marks.learn()
            self._set(shown=None, status=f"Saved · Tone Lab {slot['position'] + 1} · {name}{level_note}")
            self._show()

        self.later(self.host.call("save", chain, name, knob_values or None), saved, doing="save")

    def _saved_level(self, sound, knob_values):
        from lab.tools.levels import fitted_chain, saved_settings

        reference = self.media_reference
        if reference is None:
            return sound.chain, ""
        settings = saved_settings(self.listening)
        evidence = self.ear.level_evidence(sound) if knob_values == sound.knobs else None
        if not settings["match"] or not evidence or evidence.get("clipped") or any(
                evidence.get(key) is None for key in ("true_peak_db", "loudness_lufs")):
            return sound.chain, " · preset level kept"
        target = reference["lufs"] + settings["loudness_db"]
        limit = -3. - evidence["true_peak_db"] - (2. if evidence.get("short") else 0.)
        trim = min(target - evidence["loudness_lufs"], limit)
        scene = self.state.scene if sound.chain.scenes_in_use() > 1 else None
        try:
            chain, applied = fitted_chain(sound.chain, trim, scene)
        except ValueError:
            return sound.chain, " · no room for clean level trim"
        gap = target - evidence["loudness_lufs"] - applied
        if gap >= 1.:
            return chain, f" · about {gap:.0f} dB below music target"
        return chain, " · level matched"

    def measure_round_trip(self):
        """Measure the delay of the USB round trip, which every silent listen is lined up by."""
        if self._input_held():
            return
        self.begin("round trip", "Measuring the USB round trip…")
        self.ear.lent()
        self.later(self.host.call("measure_round_trip"),
                   lambda offset: self._set(status=f"Round trip: {offset} samples."), doing="round trip")

    def clear_listening(self):
        """Play at the desk's own levels again: no offset and no mute from the Levels tool (`self.listening`)."""
        self.set_listening(None)
        self._set(status="Back at the desk's own level.")

    def set_listening(self, value):
        """Apply the desk-wide volume and remember it. Mute is temporary."""
        from lab.tools.levels import at_rest, saved_settings

        settings = saved_settings(value)
        self.listening = None if at_rest(settings) else settings
        remembered = {**settings, "muted": False}
        self.remember(listening=None if at_rest(remembered) else remembered)
        if self.listening is None:
            self.later(self.host.call("lab.tools.levels.clear"), lambda levels: None)
        else:
            self.later(self.host.call("lab.tools.levels.install", self.listening), lambda levels: None)
        self._set()

    @property
    def media_reference(self):
        from lab.hearing.media import reference

        return reference(self.desk_session.get("media_reference"))

    def set_media_reference(self, value):
        self.remember(media_reference=value)
        if self.state.mode:
            self._match_levels()
        self._set()

    # ---- phrases and the guitar ----

    def use_phrase(self, phrase_id, whole, start=None, end=None):
        """Make a take the phrase: `whole` is its mono dry audio, and `start` to `end` (in samples) the part of it
        that loops; without them, or when they do not fit the take, its loudest few seconds. The loop that plays is
        replaced, and the choice is kept for the next start."""
        if start is None or end is None or not 0 <= start < end <= len(whole):
            start, length = loudest_at(whole)
            end = start + length
        self.phrase = (phrase_id, whole[start:end].copy())
        self.phrase_whole = (whole, int(start), int(end))
        self.remember()
        if self.state.mode == "phrase":
            self.play("phrase")          # the new take, or the new part of it, replaces the one looping
        self._set()

    def record(self, setting=None, seconds=TAKE_SECONDS, gesture=None):
        """Record a take on the current instrument until `finish_recording` (or `seconds` at most), noting the
        pickup setting it was played on when it is known: the dry input and, when a sound is on show, the selected
        sound's stereo return as it was heard while playing. `gesture` says what was played ("open strings
        strummed once…": `lab.hearing.profile.GESTURES`, or the player's own words): two pickup settings are
        compared only on takes of the same gesture and setting. The take is kept by `lab.tools.phrases.keep`,
        which refuses one in which nothing came in or which clipped."""
        if self._input_held():
            return
        # While the guitar plays through the selected sound, the take is cut from the input already open (from a
        # moment before it was asked for) and the guitar goes on; otherwise the input is opened for it.
        if self.sounding != (self.state.side, "guitar") or self.state.mode != "guitar":
            self.stop()
            self.ear.lent()
        side = self.state.side if self.sound(self.state.side) is not None and self.state.shown else None
        heard_through = self.sound(side) if side else None
        self.recording = (None, seconds)
        self.begin("record", "Opening the input…")

        def opened(_):
            if "record" in self.doing:
                import time

                self.recording = (time.monotonic(), seconds)
                self.begin("record", "Recording · play")

        def recorded(audio):
            from lab.tools import phrases

            try:
                info, dry, level = phrases.keep(self.folder, audio, instrument=self.state.instrument,
                                                setting=setting, heard_through=heard_through, gesture=gesture)
            except phrases.NotKept as error:
                self.fault(str(error))
                return
            self.guitar.version += 1            # the guitar's profile has a new take to take in
            self.use_phrase(info["id"], dry.copy(), 0, len(dry))
            self._set(status=f"Recorded {info['seconds']:.0f} s · peak {level:.0f} dBFS")

        def failed(error):
            if getattr(error, "kind", "") != "Stopped":
                self.fault(f"Recording: {error}")

        self.later(self.host.call("record", seconds, side, dict(heard_through.knobs) if heard_through else None,
                                  progress=lambda value: self.progress.append((opened, value))), recorded, failed,
                   doing="record")

    def finish_recording(self):
        """End the take being recorded where it is, and keep it."""
        if "record" in self.doing:
            self.host.enough()

    # ---- songs ----


def _expected(error):
    """Whether a failure is an outcome of ordinary use (stopped, cancelled, the unit not the app's to use just
    now, the day's calls spent): said on the line, never logged as a fault."""
    return bool(getattr(error, "expected", False)) or isinstance(error, (Cancelled, Refused))


def loudest_at(dry, seconds=PHRASE_SECONDS):
    """Where the loudest `seconds` of a take start, and how long they are."""
    import numpy as np

    length = round(seconds * RATE)
    if len(dry) <= length:
        return 0, len(dry)
    energy = np.concatenate(([0.], np.cumsum(np.square(dry[:, 0], dtype=np.float64))))
    return int(np.argmax(energy[length:] - energy[:-length])), length
