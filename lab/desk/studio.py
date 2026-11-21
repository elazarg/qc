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
  `__init__`: the phrase in use, the requests with the assistant, the last
  remark, the knobs waiting to be checked, what is going on (`doing`, shown
  as the working line) and who holds the audio input.

Work that takes time (anything on the unit, any call to the assistant) runs
elsewhere and its result is handled here, on the window's thread, by `poll`.
A result is applied only if what it was for is still what is shown: an answer
for a sound that has since left B is offered, not applied.

While the unit is free (`_use_free_unit`), the studio listens, silently, to
what is on show and has not been heard as it plays now.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
import time
import traceback

from lab.sound import design, knobs, words
from lab import knowledge
from lab.sound.assistant import Assistant, Cancelled, Refused
from lab.hearing.measure import summary
from lab.sound.sounds import Sound, Store, describe, new_id, write_whole

SETTLE_SECONDS = 4.     # a turn is checked by listening once no knob has moved for this long
IDLE_SECONDS = 2.       # silent listening starts once the unit has been left alone for this long
PHRASE_SECONDS = 3.5    # the part of a take that loops, until its edges are dragged
RATE = 48000
# The one correction a check may make to a knob, as a factor on its position: further when it did too little,
# back when it did the opposite of what it is for.
CORRECTION = {"too small": 1.6, "opposite": .5}
CORRECTION_LIMIT = .4   # never more than this much of a knob's travel
# Who may hold the audio input outside the engine, as the desk names them.
HOLDERS = {"learner": "The Guitar Learner", "gestures": "Gesture listening"}
# Holders beside whom the guitar may still be heard through the unit: that route is made on the unit and opens no
# audio driver, and gestures are for playing the sound on show. (The Guitar Learner records the dry guitar alone.)
GUITAR_GOES_ON = frozenset({"gestures"})
# Why a silent listen can end without saying anything about the sound it was listening to: it was stopped, or the
# unit was not the app's to use just then.
NOT_THE_SOUND = frozenset({"Stopped", "Moved", "Unsaved", "NotOurs", "EditorActive", "WorkerGone"})


@dataclass(frozen=True)
class State:
    a: str | None = None              # sound ids
    b: str | None = None
    side: str = "b"
    mode: str | None = None           # None, "guitar" or "phrase"
    shown: str | None = None          # "pair" or "recall" once A and B are ready on the unit
    round: str | None = None          # the round on show
    status: str = ""
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


class Rounds:
    """The rounds on disk, one small JSON file each, written whole or not at all.

    A round is {"id", "request", "aim", "created", "previous", "next", "by", "candidates": [{"sound", "differs"}]}.
    """

    def __init__(self, folder):
        self.folder = Path(folder) / "rounds"

    def save(self, record):
        self.folder.mkdir(parents=True, exist_ok=True)
        write_whole(self.folder / f"{record['id']}.json", json.dumps(record, indent=1))
        return record

    def get(self, round_id):
        if not round_id:
            return None
        try:
            found = json.loads((self.folder / f"{round_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return found if isinstance(found, dict) else None

    def of(self, sound_id):
        """The round a sound was designed in, if any."""
        for path in self.folder.glob("*.json"):
            found = self.get(path.stem)
            if found and any(candidate.get("sound") == sound_id for candidate in found.get("candidates", [])):
                return found
        return None

    def follow(self, earlier_id, next_id):
        """Note on a round which one was designed to follow it."""
        earlier = self.get(earlier_id)
        if earlier is not None:
            earlier["next"] = next_id
            self.save(earlier)


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
        self.jobs = []                    # (future, done, failed, the activity it ends)
        self.progress = []                # (handler, value) reported by the unit's worker, handled by `poll`
        self.doing = {}                   # what is going on, by key, oldest first: the working line shows the newest
        # The assistant.
        self.requests = set()             # the player's requests in flight, each its own cancel signal
        self.background = set()           # the app's own requests in flight, cancelled only when the app closes
        self.latest = None                # the newest of the player's
        self.preparing = None             # the round whose next three are being designed
        self.remark = None                # the last remark that moved knobs: {"text", "sound", "turn", "before", "after"}
        # The unit.
        self.opened = False               # the engine has noted the player's preset: the unit may be borrowed
        self.retry_open_at = None         # when to try opening the unit again after it refused
        self.watched_at = 0.              # when the unit was last asked whether the player moved it
        self.said_stale = False           # the remark about tables from another firmware was made
        self.showing = None               # the making-ready of A and B in flight
        self.shows = self.plays = 0       # counted, so that only the newest one's outcome is acted on
        self.level_sent = {}              # side -> the level last asked of the unit
        self.unit_scene = 0               # the scene last asked of the unit
        self.hearing_base = set()         # sides heard without their Shape knobs for now
        self.audio_held_by = None         # who holds the audio input outside the engine: a key of HOLDERS
        self.audio_quiet = None           # the future of the `quiet` sent for them
        # Listening while the unit is free.
        self.phrase = phrase              # (id, mono dry audio: the part of the take that loops) or None
        self.phrase_whole = None          # (the whole take, where the loop starts, where it ends) or None
        self.shaping = None               # (sound id, knobs turned, when) waiting to be checked by listening
        self.settle_seconds = SETTLE_SECONDS
        self.idle_seconds = IDLE_SECONDS
        self.free_since = None            # since when the unit has been left alone
        self.hearing = None               # the sound a silent listen in flight is for
        self.listen_after = 0.            # no silent listen before this (after one the unit turned away)
        self.looking = False              # asking the unit what it is on, before borrowing it
        self.looked = False               # ... and it was on one of the app's own slots, in this quiet stretch
        self.away = False                 # the player chose a preset of their own on the unit: it is left alone
        self.unit_borrowed = False        # a listen used the unit: A and B are made ready again afterwards
        self.settled = None               # what was on show when nothing was left to hear or prepare
        # The session, the guitar, the player.
        self.desk_session = {}            # the window's part of session.json (the open panel)
        self.restored = False             # the last session was put back and waits for the unit
        self.notice = ""                  # said once, when the unit opens
        self.profiles = {}                # instrument -> (the takes it was made from, (profile, lines))
        self.profile_lock = threading.Lock()
        self.profiling = None             # the takes a profile is being worked out from
        self.profile_failed = None        # (those takes, why)
        self.profile_version = 0          # bumped when a profile arrives, for the window
        self.taste = None                 # the taste model, once fitted
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
        before = self.state
        self.state = replace(self.state, **changes, working=self._working())
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

    def sound(self, side):
        sound_id = getattr(self.state, side)
        return self.store.sound(sound_id) if sound_id else None

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
            self._set(status=f"{spare.name.removesuffix('.bad')} could not be read, so it was set aside as "
                             f"{spare.name} in the data folder and the app's own is used until it is mended.")
        if self.state.working != self._working():
            self._set()
        now = time.monotonic()
        self._use_free_unit(now)
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
            self._set(status=f"Something went wrong: {error}. The details are in errors.log in the data folder.")

    def _failed(self, error):
        """What a failure says when its job has nothing of its own to say. A command dropped by Stop says nothing:
        Stop has said it."""
        if getattr(error, "kind", "") != "Stopped":
            self._set(status=str(error))

    def _watched(self, result):
        if result.get("moved") and self.state.mode:
            self.away = True
            self._set(mode=None, shown=None, status="Another preset was chosen on the Quad Cortex, so Tone Lab "
                                                    "stopped sending sound and leaves it alone. Press Guitar or "
                                                    "Phrase to come back.")

    def log_error(self, error, trace):
        try:
            with open(self.folder / "errors.log", "a", encoding="utf-8") as log:
                log.write(f"\n--- {_now()} {type(error).__name__}: {error}\n{trace}")
        except OSError:
            pass

    # ---- the unit ----

    def open(self):
        self.begin("open", "Connecting to the Quad Cortex…")

        def opened(result):
            self.opened = True
            note = " Gave back the preset a crash had left borrowed." if result.get("recovered") else ""
            if self.notice:
                note += " " + self.notice
                self.notice = ""
            note += self._stale_words()
            self._set(status="Ready." + note)
            if self.restored:
                self.restored = False
                shown = [f"{side.upper()} {self.sound(side).name}" for side in ("a", "b") if self.sound(side)]
                found = self.round()
                self.say("Back where you left off: " + ", ".join(shown)
                         + (f"; round: {found['request']}" if found else "") + "." + note)
                self._show()

        def refused(error):
            self._set(status=f"{error}")
            if self.host.failed is None:
                self.retry_open_at = time.monotonic() + 5     # tried again until the unit lets it

        self.later(self.host.call("open"), opened, refused, doing="open")
        # The player's presets, for the Sounds panel and for requests that name one: read again when the last
        # read is over half a day old (it holds the unit about ten seconds). Quiet if it fails.
        listing = self.folder / "directory.json"
        if not listing.exists() or time.time() - listing.stat().st_mtime > 12 * 3600:
            self.later(self.host.call("lab.qc.library.directory", folder=str(self.folder)),
                       lambda entries: self._say_stale(), lambda error: None)

    def _stale_words(self):
        """A remark, made once, when the unit's firmware (as the last directory read found it) is not the one some
        measured tables were made on. Nothing when the firmware is not known."""
        try:
            firmware = json.loads((self.folder / "directory.json").read_text(encoding="utf-8")).get("firmware")
        except (OSError, ValueError, AttributeError):
            firmware = None
        if self.said_stale or not firmware or not knowledge.stale(firmware):
            return ""
        self.said_stale = True
        return f" Some measured tables were made on another firmware than the unit's {firmware}."

    def _say_stale(self):
        said = self._stale_words()
        if said:
            self.say((self.state.status + said).strip())

    def put(self, side, sound, **changes):
        """Put a sound on A or B, and get both ready on the unit. `changes` are other things on show that change
        with it (the round, the song), so that one Undo takes all of it back."""
        self.checkpoint()
        self.store.save(sound)
        self._set(**{side: sound.id, "side": side, "revision": self.state.revision + 1, **changes})
        self._show()

    def _show(self):
        a, b = self.sound("a"), self.sound("b")
        if a is None and b is None:
            return
        revision = self.state.revision
        self.shows += 1
        key = self.showing = ("show", self.shows)
        self.hearing_base.clear()
        self.unit_borrowed = self.away = False
        self.listen_after = 0.
        # A scene the sounds now on show do not have is not kept.
        scene = self.state.scene
        if scene and not any(sound.chain.scenes_in_use() > scene for sound in (a, b) if sound):
            scene = 0
        self.begin(key, "Getting A and B ready on the Quad Cortex…", shown=None, scene=scene)

        def shown(how):
            if self.showing == key:
                self.showing = None
            if self.state.revision != revision:
                return
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
            # Nothing is ready, so nothing plays: the desk must not say it does.
            self._set(mode=None, status=str(error))

        self.later(self.host.call("show", a.chain if a else None, b.chain if b else None), shown, failed, doing=key)

    def play(self, mode, side=None):
        side = side or self.state.side
        if self._input_held(mode):
            return
        if self.sound("a") is None and self.sound("b") is None:
            self.say("Nothing is on A or B yet.")
            return
        self.away, self.listen_after = False, 0.    # the player is back at the desk's sounds
        # A silent listen has the unit: the player comes first, and it is listened to again later.
        interrupted = self.hearing is not None
        if interrupted:
            self.host.stop()                # which also drops whatever waited behind the listen
            self.level_sent, self.showing = {}, None
        if self.state.shown is None:
            self._set(mode=mode, side=side, status="Getting ready. It plays as soon as it is.")
            if self.showing is None:
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
            if sound.knobs:
                self.later(self.host.call("turn", side, sound.knobs), lambda moves: None)
            self.say(f"{side.upper()} · {mode}" if self.state.blind else f"{side.upper()} · {sound.name} · {mode}")

        def failed(error):
            # Nothing plays: the desk must not go on saying it does. A play that Stop dropped, or that a newer
            # one has replaced, has nothing to say.
            if ticket == self.plays and getattr(error, "kind", "") != "Stopped":
                self._set(mode=None, status=str(error))

        self.later(self.host.call("play", side, mode, phrase), playing, failed)

    def _match_levels(self):
        """Play A and B equally loud, when both have been measured: the louder is turned down to the quieter.

        Matched by integrated loudness (ITU-R BS.1770, LUFS), which follows perceived loudness across sounds of
        different brightness better than a plain level does (docs/TONE_RESEARCH.md, 13); by RMS level only when one of
        the two was measured before loudness was recorded. A level the unit already has is not sent again."""
        from lab.qc.engine import PREVIEW_DB

        sounds = {side: self.sound(side) for side in ("a", "b")}
        heard = {side: self.evidence(sound) for side, sound in sounds.items() if sound}
        heard = {side: record for side, record in heard.items()
                 if record and record.get("level_db") is not None and record["level_db"] > -90}
        key = "loudness_lufs" if all(record.get("loudness_lufs") is not None for record in heard.values()) \
            else "level_db"
        levels = {side: record[key] for side, record in heard.items()}
        if len(levels) < 2:
            # Nothing fair to match by: both play at the preview level, and no earlier trim stays on either.
            wanted = {side: PREVIEW_DB for side, sound in sounds.items() if sound is not None}
        else:
            quietest = min(levels.values())
            wanted = {side: PREVIEW_DB + quietest - level for side, level in levels.items()}
        for side, level in wanted.items():
            if side not in self.level_sent or abs(self.level_sent[side] - level) > .05:
                self.level_sent[side] = level
                self.later(self.host.call("level", side, level), lambda result: None)

    def scene(self, scene):
        """Hear another scene of the sounds on show."""
        self.unit_scene = scene
        self._set(scene=scene)
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

    def select(self, side):
        if self.state.mode:
            self.play(self.state.mode, side)
        else:
            self._set(side=side)

    def stop(self):
        self.host.stop()
        self.level_sent = {}            # a level that waited behind what was stopped never reached the unit
        self.later(self.host.call("quiet"), lambda result: None)
        self._set(mode=None, status="Stopped.")

    def close(self):
        self.remember()
        for signal in (*self.requests, *self.background):
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

    def _playing_as(self, sound):
        """What a measurement of `sound` must have been made on to hold for what plays now: this take and this
        part of it, this chain, and the scene heard (any scene, when the sound's scenes do not differ). Every
        question of whether a measurement holds is answered by comparing with this."""
        return (self.phrase_key(), sound.chain.digest(),
                self.state.scene if sound.chain.scenes_in_use() > 1 else 0)

    @staticmethod
    def _made_on(record):
        return record.get("phrase"), record.get("digest"), record.get("scene", 0)

    def _heard(self, sound):
        """The record of listening to `sound` without its knobs, when it holds for what plays now, or None. It is
        the record of a listen that failed when it has "failed"."""
        heard = sound.heard
        return heard if heard and self._made_on(heard) == self._playing_as(sound) else None

    def evidence(self, sound):
        """What was measured of `sound` as it plays now (this phrase, this chain, this scene, these knobs), or
        None."""
        heard = self._heard(sound)
        if heard is None or "failed" in heard:
            return None
        if not sound.knobs:
            return heard
        shaped = heard.get("shaped") or {}
        # The shaped measurement is kept inside the one it was compared with; one written before that was so says
        # itself what it was made on.
        made_on = self._made_on({**heard, **{key: shaped[key] for key in ("phrase", "scene") if key in shaped}})
        return shaped if shaped.get("knobs") == sound.knobs and made_on == self._playing_as(sound) else None

    def unheard(self, sound):
        """Why `sound` could not be listened to as it plays now, or None when it could or has not been tried."""
        heard = self._heard(sound) if sound is not None else None
        return heard.get("failed") if heard else None

    def _current(self, inputs):
        """Whether the take, part, scene and sound (its chain unchanged) a check was started on are still what is
        on show. `inputs` is (phrase key, the audio, scene, chain digest, sound id)."""
        key, _, scene, digest, sound_id = inputs
        if sound_id not in (self.state.a, self.state.b):
            return False
        try:
            sound = self.store.sound(sound_id)
        except (OSError, ValueError):
            return False
        return self._playing_as(sound) == (key, digest, scene if sound.chain.scenes_in_use() > 1 else 0)

    # ---- listening while the unit is free ----

    def _use_free_unit(self, now):
        """While nothing plays, nobody else has the input and the unit has nothing to do: check knobs that were
        turned, then listen to what is on show and has not been heard as it plays now, then make A and B ready
        again, then prepare three more. Listening starts only once the unit has been left alone for a moment."""
        if self.hearing is not None or self.looking:
            return                                  # the studio's own listening: the quiet stretch goes on
        if not (self.opened and not self.state.mode and self.audio_held_by is None and self.phrase is not None
                and not self.host.busy and self.host.failed is None):
            self.free_since, self.looked = None, False
            return
        if self.free_since is None:
            self.free_since = now
        if self.away or now < self.listen_after:
            return
        if self.shaping is not None and now - self.shaping[2] >= self.settle_seconds:
            if not self.looked:
                self._look_first()
                return
            if self._check_shaping(now):
                return
        on_show = (self.state.a, self.state.b, self.state.round, self.phrase_key(), self.state.scene)
        if now - self.free_since < self.idle_seconds or self.settled == on_show:
            return
        sound = self._next_to_hear()
        if sound is not None and not self.looked:
            self._look_first()
        elif sound is not None:
            self._listen(sound)
        elif self.unit_borrowed:
            self.unit_borrowed = False
            self._show()                            # listening used the unit: A and B go back, knobs as they stand
        else:
            self.settled = on_show
            self._prepare_next()

    def _look_first(self):
        """Before the unit is borrowed to listen: ask what it is on. When the player has chosen a preset of their
        own there, by hand, it is left alone until they come back to the desk's sounds."""
        from lab.qc.slots import BUILD_NAME, CACHE_PREFIX

        self.looking = True

        def found(result):
            self.looking = False
            name = str(result.get("preset") or "")
            if name == BUILD_NAME or name.startswith(CACHE_PREFIX):
                self.looked = True
            else:
                self._turned_away("Moved", "Another preset is selected on the Quad Cortex, so Tone Lab leaves it "
                                           "alone and listens to nothing. Press Guitar or Phrase to come back.")

        def failed(error):
            self.looking = False
            self._turned_away(getattr(error, "kind", ""), error, wait=60)

        self.later(self.host.call("inputs"), found, failed)

    def _turned_away(self, kind, error, wait=15):
        """The unit was not the app's to listen with. After the player chose another preset there, nothing is
        listened to until they come back; otherwise it is tried again after `wait` seconds."""
        if kind == "Moved":
            self.away = True
        self.listen_after = time.monotonic() + wait
        self._set(status=str(error))

    def _next_to_hear(self):
        """The next sound to listen to: the sounds on A and B first, then the designs of the round on show. One
        that could not be heard is not tried again until its chain, the phrase or the scene changes. A sound whose
        chain was already listened to on another sound takes what came of it."""
        ids = [sound_id for sound_id in (self.state.a, self.state.b) if sound_id]
        ids += [item["sound"] for item in (self.round() or {}).get("candidates", [])]
        sounds, known = [], {}
        for sound_id in dict.fromkeys(ids):
            try:
                sound = self.store.sound(sound_id)
            except Exception:  # noqa: BLE001  a file that is gone or unreadable is not listened to
                continue
            sounds.append(sound)
            heard = self._heard(sound)
            if heard is not None and ("probe" in heard or "failed" in heard):
                known.setdefault(self._playing_as(sound), heard)
        for sound in sounds:
            heard = self._heard(sound)
            if heard is not None and ("probe" in heard or "failed" in heard):
                continue
            twin = known.get(self._playing_as(sound))
            if twin is None:
                return sound
            sound.heard = {key: value for key, value in twin.items() if key != "shaped"}
            self.store.save(sound)
        return None

    def _listen(self, sound, then=None, dropped=None):
        """Listen to `sound` without its knobs, silently, as it plays now, and keep what was measured with what it
        was measured on. `then(sound)` follows a measurement; `dropped()` a listen that was stopped."""
        phrase_key, digest, scene = self._playing_as(sound)
        sound_id, name = sound.id, "a sound" if self.state.blind else sound.name
        self.hearing, self.unit_borrowed = sound_id, True
        self.begin("listen", f"Listening to {name}, silently…")

        def heard(result):
            self.hearing = None
            fresh = self.store.sound(sound_id)        # knobs or marks may have changed meanwhile
            # "probe" marks a record as made with the current tests: without it the sound would be listened to again.
            fresh.heard = {"probe": None, "phrase": phrase_key, "digest": digest, "scene": scene, **result.to_record()}
            if result.level_db is not None and result.level_db > -90:
                fresh.tags = [tag for tag in fresh.tags if not tag.startswith("heard:")] + [
                    f"heard: {result.said()}"]
            self.store.save(fresh)
            self._set()
            if then is not None:
                then(fresh)

        def failed(error):
            self.hearing = None
            kind = getattr(error, "kind", "")
            if kind in NOT_THE_SOUND:
                # Nothing was learned about the sound: it is listened to again later.
                if kind != "Stopped":
                    self._turned_away(kind, error)
                if dropped is not None:
                    dropped()
                return
            fresh = self.store.sound(sound_id)
            fresh.heard = {"failed": str(error), "phrase": phrase_key, "digest": digest, "scene": scene}
            fresh.tags = [tag for tag in fresh.tags if not tag.startswith("heard:")]
            self.store.save(fresh)
            self._set(status=f"Could not listen to {name}: {error}")

        self.later(self.host.call("hear", sound.chain, self.phrase[1], scene), heard, failed, doing="listen")

    def _prepare_next(self):
        """When every design of the round on show has been listened to, prepare three more in the background, if
        the assistant may be called without asking. Only for a round the player asked for: the app's own rounds
        lead to no further call."""
        found = self.round()
        if found is None or found.get("next") or self.preparing or not self.assistant.automatic:
            return
        if found.get("by") == "app":
            return
        try:
            heard = [self.store.sound(candidate["sound"]).heard for candidate in found["candidates"]]
        except Exception:  # noqa: BLE001
            return
        if all(heard):
            self.more(by="app")

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
        self.hearing_base.discard(side)
        if self.state.shown:
            self.later(self.host.call("turn", side, sound.knobs), lambda moves: None)
        turned = {key for key in set(before) | set(sound.knobs) if before.get(key, 0) != sound.knobs.get(key, 0)}
        if turned:
            self._check_later(sound.id, turned)
        self._set()

    def _check_later(self, sound_id, turned):
        """Have knobs that were turned on a sound checked by listening, once the turning has stopped and the unit
        is free (`_check_shaping`)."""
        earlier = self.shaping[1] if self.shaping and self.shaping[0] == sound_id else set()
        self.shaping = (sound_id, set(turned) | earlier, time.monotonic())

    def base(self):
        """Hear the sound on show without its Shape knobs, or with them again."""
        side = self.state.side
        sound = self.sound(side)
        if sound is None or not sound.knobs:
            self.say("Nothing shaped to compare with.")
            return
        if not self.state.shown:
            self.say("A and B are not ready on the Quad Cortex yet: press Guitar or Phrase first.")
            return
        without = side not in self.hearing_base
        self.hearing_base ^= {side}
        self.later(self.host.call("turn", side, {} if without else sound.knobs), lambda moves: None)
        self._set(status=f"{sound.name}: " + ("the base, without the Shape knobs" if without else "shaped"))

    # ---- knobs that check themselves ----

    def _check_shaping(self, now):
        """Once the knobs of a sound have not been turned for a few seconds (and the unit is free), listen to the
        sound as shaped, silently, and compare it with the sound heard without its knobs. True when a listen was
        started.

        A knob can do something else on this amp than the table says (amp controls interact: docs/TONE_RESEARCH.md,
        finding 8). One knob that measured the wrong way, or too little, is corrected once; nothing is measured
        again for that correction, so this never loops."""
        if self.shaping is None:
            return False
        sound_id, turned, at = self.shaping
        if now - at < self.settle_seconds:
            return False
        self.shaping = None
        if sound_id not in (self.state.a, self.state.b):
            return False                            # no longer on show
        try:
            sound = self.store.sound(sound_id)
        except (OSError, ValueError):
            return False
        if not sound.knobs:
            return False
        # The take, the part of it, the scene and the chain are fixed for the whole check: both measurements are
        # made on them, and a correction is made only while they are still what is on show.
        inputs = (self.phrase_key(), self.phrase[1], self.state.scene, sound.chain.digest(), sound.id)
        heard = self._heard(sound)
        if heard is not None and "failed" in heard:
            return False                            # it cannot be listened to: nothing to compare with
        if heard is not None and "probe" in heard:
            self._hear_shaped(sound, turned, inputs)
            return True

        # The sound without its knobs has not been heard as it plays now: that first. A listen the player's own
        # playing stopped is made again later.
        self._listen(sound, then=lambda fresh: self._current(inputs) and self._hear_shaped(fresh, turned, inputs),
                     dropped=lambda: self._check_later(sound_id, turned))
        return True

    def _hear_shaped(self, sound, turned, inputs):
        sound_id, values = sound.id, dict(sound.knobs)
        phrase_key, _, scene = self._playing_as(sound)
        name = "a sound" if self.state.blind else sound.name
        self.hearing, self.unit_borrowed = sound_id, True
        self.begin("listen", f"Listening to {name} as shaped, silently…")

        def heard(result):
            self.hearing = None
            fresh = self.store.sound(sound_id)
            if fresh.knobs != values or self._heard(fresh) is None or not self._current(inputs):
                return                              # turned again meanwhile: that turn is checked in its turn
            after = result.to_record()
            found = knobs.check(fresh.heard, after, values)
            correction = _correction(found, turned, values)
            fresh.heard = {**fresh.heard, "shaped": {"phrase": phrase_key, "knobs": values,
                                                     "scene": fresh.heard.get("scene", 0), **after, "check": found,
                                                     "corrected": correction}}
            if correction:
                key, old, new = correction
                fresh.knobs = {name: value for name, value in {**values, key: new}.items() if value}
                if self.remark and self.remark["sound"] == sound_id and self.remark["after"] == values:
                    self.remark["after"] = dict(fresh.knobs)     # the remedy as corrected is still the remedy
            self.store.save(fresh)
            if correction and self.state.mode and self.state.shown:
                # Playing began meanwhile, which made A and B ready again: the corrected knobs go on as it plays.
                for side in ("a", "b"):
                    if getattr(self.state, side) == sound_id:
                        self.later(self.host.call("turn", side, fresh.knobs), lambda moves: None)
            self.say(_checked_words(found, turned, correction))

        def failed(error):
            self.hearing = None
            kind = getattr(error, "kind", "")
            if kind == "Stopped":
                self._check_later(sound_id, turned)     # the player's own playing stopped it: checked afterwards
                return
            if kind in NOT_THE_SOUND:
                self._turned_away(kind, error)
            else:
                self._set(status=f"Could not listen to the shaped sound: {error}")

        self.later(self.host.call("hear_shaped", sound.chain, values, inputs[1], scene), heard, failed,
                   doing="listen")

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
        self.say(f"{block.name} set to its named setting {setting}. Undo takes it back.")

    def difference(self):
        """What listening heard differ between A and B, in plain words, when both were heard as they play now."""
        a, b = self.sound("a"), self.sound("b")
        if not (a and b):
            return ["Put a sound on both A and B to compare them."]
        # What was measured of each as it plays now: this take, this scene, these knob positions.
        ha, hb = self.evidence(a), self.evidence(b)
        if ha and hb and "probe" in ha and "probe" in hb:
            return _difference(ha, hb)
        return [line for side, sound in (("A", a), ("B", b)) for line in [self._not_measured(side, sound)] if line]

    def _not_measured(self, side, sound):
        """Why there is no measurement of the sound on a side as it plays now, and what will bring one; None when
        there is one."""
        why = self.unheard(sound)
        if why:
            return f"{side} could not be listened to: {why}"
        heard = self._heard(sound)
        if heard is None or "probe" not in heard:
            return (f"{side} has not been listened to as it is set now (this take and scene). That happens by itself "
                    "while nothing plays.")
        if self.evidence(sound) is not None:
            return None
        shaped = heard.get("shaped") or {}
        if shaped.get("corrected") and self.shaping is None:
            return (f"{side}'s Shape knobs were corrected after they were measured, and are not measured again by "
                    "themselves: turn one to have them measured where they stand.")
        return (f"{side}'s Shape knobs have not been measured where they stand. That happens by itself a few seconds "
                "after they were last turned, while nothing plays.")

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
                remark = {"text": text.casefold(), "sound": sound.id, "turn": turn, "before": dict(base)}
                following = f" Say it again for: {ways[turn + 1].say}." if turn + 1 < len(ways) else ""
                said = f"{reading.said}: {ways[turn].say}.{following}"
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
            sound.notes.append({"at": _now(), "text": text})
            self.store.save(sound)
            self.remark = {**remark, "after": dict(sound.knobs)} if remark else None
            self.say(said)
            return
        named = self._preset_named(text)
        if named is not None:
            slot, name = named
            self.work_on(slot, name, note=f"That is your preset's name. For a new design in its spirit, "
                                          f"ask \"make {name}\".")
            return
        b = self.sound("b")
        if b is not None and reads_as_change(text):
            self.revise(b, text)
        else:
            self.new_round(text)

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
            self._set(status=f"{len(records)} IRs and captures on the unit.")
            if done:
                done(records)

        self.later(self.host.call("lab.qc.assets.listing", folder=str(self.folder)), read, doing="assets")

    def read_directory(self, done=None):
        """Read the presets on the unit (about ten seconds) and remember them."""
        self.begin("directory", "Reading the presets on the Quad Cortex…")

        def read(entries):
            self._set(status=f"{len(entries)} presets on the unit.")
            self._say_stale()
            if done:
                done(entries)

        self.later(self.host.call("lab.qc.library.directory", folder=str(self.folder)), read, doing="directory")

    def use_asset(self, asset, *, has_cab=None, version=None):
        """A copy of the sound on B with one of the player's IRs in place of its cab, or a capture in place of
        its amp (and cab, when the capture has one)."""
        from lab.qc import assets

        sound = self.sound("b") or self.sound("a")
        if sound is None:
            self.say("Put a sound on B first; the IR or capture goes into it.")
            return
        try:
            if asset.kind == "ir":
                chain, what = assets.with_ir(sound.chain, asset)
            else:
                chain, what = assets.with_capture(sound.chain, asset, has_cab=has_cab, version=version)
        except ValueError as error:
            self.say(str(error))
            return
        name = f"{sound.name[:19]} {'IR' if asset.kind == 'ir' else 'Cap'}"
        copy = Sound(name, chain, {"kind": "revised", "from": sound.id, "request": what}, parent=sound.id,
                     explanation=f"{sound.explanation} {what}".strip(), knobs=dict(sound.knobs))
        self.put("b", copy)
        self.say(what + " Undo takes it back.")

    def _instrument(self):
        return self.store.instrument(self.state.instrument)

    def _setting(self):
        """The pickup setting the phrase in use was recorded on, if it was noted."""
        if not self.phrase:
            return None
        found = next((item for item in self.recorded_phrases() if item["id"] == self.phrase[0]), None)
        setting = (found or {}).get("setting")
        return None if not setting or setting == "unknown" else setting

    def _listening(self):
        """What guitar music the player listens to most, if the listening history was analysed (personal; kept in
        the data folder)."""
        from lab.player.listening import prior

        try:
            record = json.loads((self.folder / "personal" / "listening.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return prior(record)

    def _design(self, function, *args, **options):
        """Run a call to the designer on the assistant's thread. What the instrument puts out (`profile.facts`) is
        worked out there first, from the takes as they are now: reading them takes a second or more."""
        from lab.hearing.profile import facts

        instrument, setting = self.state.instrument, self._setting()
        takes = self._takes(instrument)

        def work():
            found = self._profile(instrument, takes)[0] if takes else None
            return function(*args, guitar=facts(found, setting) if found else None, setting=setting, **options)

        return self.thinking.submit(work)

    def _request(self, quietly=False):
        """A cancel signal for a request to the assistant: the player's (cancelled by Cancel) or, `quietly`, the
        app's own (cancelled only when the app closes)."""
        signal = threading.Event()
        (self.background if quietly else self.requests).add(signal)
        if not quietly:
            self.latest = signal            # the player's newest request: only its answer goes on show
        return signal

    def cancellable(self):
        return any(not signal.is_set() for signal in self.requests)

    def cancel(self):
        """Drop every request the assistant is working on for the player; answers that still come are ignored."""
        active = [signal for signal in self.requests if not signal.is_set()]
        if not active:
            return False
        for signal in active:
            signal.set()
            self.doing.pop(signal, None)
        self.requests.clear()
        self._set(status="Request cancelled.")
        return True

    def new_round(self, text, *, earlier=None, by="player", after=None, saying=None):
        """Three designs for `text`. `after` is the round these follow, whose designs and what was heard of them
        (`earlier`) inform these. `saying` is the working line, when it is not the usual one."""
        quietly = by == "app"
        stop = self._request(quietly)
        if not quietly:
            self.begin(stop, saying or "Designing three ways to get there. About a minute…")
        future = self._design(design.round_of_three, self.assistant, text, self._instrument(), earlier=earlier,
                              by=by, cancel=stop, assets_known=self._assets(),
                              listening=None if quietly else self._listening())
        revision = self.state.revision
        if after:
            self.preparing = after

        def over():
            (self.background if quietly else self.requests).discard(stop)
            if after and self.preparing == after:
                self.preparing = None

        def arrived(result):
            over()
            if stop.is_set():
                return
            aim, designs = result
            record = {"id": new_id(), "request": text, "aim": aim, "created": _now(),
                      "previous": after, "by": by, "candidates": []}
            # The song is the one this request is for, or the one the round it follows was for: never one left
            # over from an earlier, unrelated request.
            song = aim.get("song") or (self.rounds.get(after) or {}).get("aim", {}).get("song")
            if song:
                record["aim"]["song"] = song
            for sound, differs in designs:
                sound.tags = [f"aim: {summary(aim)}"]
                if song:
                    sound.origin = {**sound.origin, "song": song_name(song)}
                self.store.save(sound)
                record["candidates"].append({"sound": sound.id, "differs": differs})
            self.rounds.save(record)
            if after:
                self.rounds.follow(after, record["id"])
            if quietly:
                self.say("Three more designs are ready, informed by what was heard: click › next three.")
                return
            if stop is not self.latest:
                # A newer request is on its way or already answered: this one is kept, not put on show.
                self.say(f"The designs for an earlier request ({text[:40]}) are in History; the latest request's "
                         "are the ones shown.")
                return
            on_show = {"round": record["id"], "song": song_name(song) if song else None}
            if self.state.revision == revision:
                self.put("b", self.store.sound(record["candidates"][0]["sound"]), **on_show)
                self.say(f"Aiming for {summary(aim)}. "
                         f"1 of {len(designs)} is on B · {designs[0][0].name}")
            else:
                self._set(**on_show)
                self.say(f"{len(designs)} designs are ready. B changed meanwhile, so it was left alone.")

        def failed(error):
            over()
            if not stop.is_set() and not quietly:
                self._request_failed(stop, error)

        self.later(future, arrived, failed, doing=stop)

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
                            if sound.heard and "failed" not in sound.heard else None,
                            "player_marks": sound.marks, "player_said": [note["text"] for note in sound.notes],
                            "played": sound.played})
        self.new_round(found["request"], earlier=earlier, by=by, after=found["id"],
                       saying="Designing three more, from what was heard and what you marked…")

    def revise(self, sound, text):
        stop = self._request()
        self.begin(stop, f"Revising {sound.name}…")
        future = self._design(design.revise, self.assistant, sound, text, self._instrument(), cancel=stop,
                              assets_known=self._assets())
        revision = self.state.revision

        def revised(result):
            self.requests.discard(stop)
            if stop.is_set():
                return
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

        def failed(error):
            self.requests.discard(stop)
            if not stop.is_set():
                self._request_failed(stop, error)

        self.later(future, revised, failed, doing=stop)

    def _request_failed(self, stop, error):
        """A request that failed. The working line and Cancel stay while a newer one is still on its way: each
        request is an activity of its own."""
        if stop is self.latest:
            self._set(status=str(error))
        else:
            self.say(f"An earlier request failed ({error}); the latest is still on its way.")

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
            self.more()

    def step(self, place):
        found = self.round()
        if found and 0 <= place < len(found["candidates"]):
            self.put("b", self.store.sound(found["candidates"][place]["sound"]))

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
        if blind_choice:
            # The choice is made: now say which was which.
            side = (side or self.state.side).upper()
            self._set(blind=False,
                      status=f"{side}: {word}. A was {self.sound('a').name}, B was {self.sound('b').name}.")
        else:
            self.say(f"{sound.name}: " + (", ".join(sound.marks) if sound.marks else "no marks"))
        if word == "keeper" and word in sound.marks:
            self._into_song(sound)
        self._learn()

    def _into_song(self, sound):
        """A sound kept or saved from a round that was for a song becomes that song's part: the request said
        which song and part, so nothing has to be named by hand."""
        song = (self.rounds.of(sound.id) or {}).get("aim", {}).get("song")
        if not song and sound.parent:
            song = (self.rounds.of(sound.parent) or {}).get("aim", {}).get("song")
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
        self._set(song=name)

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

        model = self.taste or taste.load(self.folder)
        return taste.describe(model) if model else ["No choices yet: marks and A/B choices teach it."]

    def copy_to_a(self):
        """A copy of B, as it is now, on A: a sound of its own, so turning B's knobs leaves it as it is."""
        b = self.sound("b")
        if b is None:
            self.say("Nothing is on B to copy.")
            return
        copy = Sound(b.name, b.chain, {"kind": "revised", "from": b.id, "request": "a copy to compare against"},
                     parent=b.id, knobs=dict(b.knobs), explanation=b.explanation, heard=b.heard)
        self.put("a", copy)
        self._set(side="b")
        self.say(f"{b.name} is on A too, to compare the next change on B against.")

    def blind(self):
        """Hide the names, and swap A and B or not, at random. Calling it again shows what was where."""
        import random

        if self.state.blind:
            self._set(blind=False, status=f"A was {self.sound('a').name}, B was {self.sound('b').name}.")
            return
        if self.sound("a") is None or self.sound("b") is None:
            self.say("Blind comparison needs a sound on both A and B.")
            return
        if random.random() < .5:
            self.checkpoint()
            self._set(a=self.state.b, b=self.state.a, revision=self.state.revision + 1)
            self._show()
        self._set(blind=True, status="Names hidden. Play A and B, then choose Keep or Not this on one of them, or "
                                     "click Show which was which.")

    # ---- presets on the unit ----

    def work_on(self, slot, name, *, note=""):
        """Bring a preset saved on the unit in: it goes on A as it is, and a copy on B to change."""
        self.begin("read", f"Reading {name}…")

        def read(result):
            chain, held_name = result
            original = Sound(held_name, chain, {"kind": "preset", "slot": slot})
            copy = Sound(held_name, chain, {"kind": "revised", "from": original.id}, parent=original.id)
            self.store.save(original)
            self.store.save(copy)
            self.checkpoint()
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
        self.stop()
        self.begin("save", f"Saving {name} to the Tone Lab setlist…")
        chain = sound.chain

        def saved(result):
            slot, _ = result
            fresh = self.store.sound(sound.id)
            fresh.saved = {"slot": slot, "name": name, "knobs": knob_values}
            self.store.save(fresh)
            self._into_song(fresh)
            self._learn()
            self._set(shown=None, status=f"Saved · Tone Lab {slot['position'] + 1} · {name}")
            self._show()

        self.later(self.host.call("save", chain, name, knob_values or None), saved, doing="save")

    def measure_round_trip(self):
        """Measure the delay of the USB round trip, which every silent listen is lined up by."""
        if self._input_held():
            return
        self.begin("round trip", "Measuring the USB round trip…")
        self.unit_borrowed = True
        self.later(self.host.call("measure_round_trip"),
                   lambda offset: self._set(status=f"Round trip: {offset} samples."), doing="round trip")

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

    def record(self, setting, seconds=8.):
        """Record a phrase on the current instrument, noting the pickup setting it was played on: the dry input and,
        when a sound is on show, the selected sound's stereo return as it was heard while playing."""
        import math

        if self._input_held():
            return
        self.stop()
        side = self.state.side if self.sound(self.state.side) is not None and self.state.shown else None
        heard_through = self.sound(side) if side else None
        self.unit_borrowed = True
        self.begin("record", "Opening the input… wait for “play now”.")

        def opened(_):
            if "record" in self.doing:
                self.begin("record", f"Recording {seconds:g} s · play now"
                           + (f" (you hear {side.upper()})" if side else "") + "…")

        def recorded(audio):
            from lab.hearing.audio import write_wave

            dry = audio[:, :1]
            peak = float(abs(dry).max()) if len(dry) else 0.
            level = 20 * math.log10(max(peak, 1e-9))
            if peak < .003:
                self._set(status=f"Nothing came in on the guitar input (peak {level:.0f} dBFS). The take was not "
                                 "kept.")
                return
            if peak >= .999:
                self._set(status="The take clipped at the input. Turn the input gain down and record again; it "
                                 "was not kept.")
                return
            phrase_id = new_id()
            folder = self.folder / "phrases" / phrase_id
            folder.mkdir(parents=True)
            write_wave(folder / "dry.wav", dry)
            info = {"id": phrase_id, "instrument": self.state.instrument, "setting": setting, "recorded": _now(),
                    "seconds": round(len(dry) / RATE, 2)}
            wet = audio[:, 1:3]
            if wet.shape[1] == 2 and float(abs(wet).max()) > 1e-4:
                write_wave(folder / "wet.wav", wet)
                info["wet"] = ({"sound": heard_through.id, "name": heard_through.name} if heard_through
                               else {"sound": None, "name": "the preset on the unit"})
            (folder / "phrase.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
            self.profile_version += 1            # the guitar's profile has a new take to take in
            self.use_phrase(phrase_id, dry.copy(), 0, len(dry))
            self._set(status=f"Recorded {info['seconds']:g} s on {setting}, peaking at {level:.0f} dBFS. "
                             "It is now the phrase.")

        def failed(error):
            if getattr(error, "kind", "") != "Stopped":
                self._set(status=f"Recording: {error}")

        self.later(self.host.call("record", seconds, side, dict(heard_through.knobs) if heard_through else None,
                                  progress=lambda value: self.progress.append((opened, value))), recorded, failed,
                   doing="record")

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

    def _takes(self, instrument=None):
        """The takes recorded on an instrument with their pickup setting noted, as (phrase id, setting, the dry
        take's file)."""
        instrument = instrument or self.state.instrument
        return tuple((item["id"], item["setting"], self.folder / "phrases" / item["id"] / "dry.wav")
                     for item in self.recorded_phrases()
                     if item.get("instrument") == instrument and item.get("setting", "unknown") != "unknown")

    def _profile(self, instrument, takes):
        """What an instrument puts out at each pickup setting, from these takes, as (profile, lines). Reading the
        takes takes a second or more, so this runs on a worker; what it finds is kept until the takes change."""
        from lab.hearing.audio import read_wave
        from lab.hearing.profile import profile

        made_from = tuple((phrase_id, setting) for phrase_id, setting, _ in takes)
        with self.profile_lock:
            held = self.profiles.get(instrument)
        if held is not None and held[0] == made_from:
            return held[1]
        found = profile([(setting, read_wave(path)[:, :1]) for _, setting, path in takes])
        with self.profile_lock:
            self.profiles[instrument] = (made_from, found)
        return found

    def guitar_profile(self):
        """What the current instrument puts out at each pickup setting, from the phrases recorded on it, as
        (profile, lines). Worked out here and now: the desk uses `guitar_lines`, which does not wait."""
        takes = self._takes()
        if not takes:
            return {}, [f"No phrase has been recorded on the {self.state.instrument} with its pickup setting noted. "
                        "Record the same short phrase on each setting."]
        return self._profile(self.state.instrument, takes)

    def guitar_lines(self):
        """What the current instrument puts out, as lines for the Guitar panel. When it has not been worked out
        from the takes as they are now, that is started on a worker and the lines say so; `profile_version`
        changes when it arrives."""
        instrument, takes = self.state.instrument, self._takes()
        if not takes:
            return self.guitar_profile()[1]
        made_from = tuple((phrase_id, setting) for phrase_id, setting, _ in takes)
        held = self.profiles.get(instrument)
        if held is not None and held[0] == made_from:
            return held[1][1]
        if self.profile_failed is not None and self.profile_failed[0] == made_from:
            return [f"The takes could not be read: {self.profile_failed[1]}"]
        if self.profiling != made_from:
            self.profiling = made_from

            def arrived(found=None, error=None):
                self.profiling = None
                self.profile_failed = (made_from, str(error)) if error is not None else None
                self.profile_version += 1
                self._set()

            self.later(self.reading.submit(self._profile, instrument, takes), arrived,
                       lambda error: arrived(error=error))
        return [f"Working out what the {instrument} puts out from {len(takes)} takes…"]

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


def _difference(ha, hb):
    """What two measurements differ by, A's and B's, in plain words."""
    lines = []
    level = hb["level_db"] - ha["level_db"]
    if abs(level) >= 1:
        lines.append(f"B is {abs(level):.0f} dB {'louder' if level > 0 else 'quieter'}.")
    pa, pb = ha.get("probe") or {}, hb.get("probe") or {}
    if pa and pb and abs(pb["distortion_db"] - pa["distortion_db"]) >= 3:
        lines.append(f"Distortion: A {ha['distortion']} ({pa['distortion_db']:.0f} dB of harmonics), "
                     f"B {hb['distortion']} ({pb['distortion_db']:.0f} dB).")
    ca, cb = ha["compression"], hb["compression"]
    if abs(cb - ca) > .1:
        lines.append(f"B is {'more' if cb > ca else 'less'} compressed: of a 12 dB drop in your playing, "
                     f"A gives back {12 * (1 - ca):.0f} dB and B {12 * (1 - cb):.0f} dB.")
    bright = hb["brightness_oct"] - ha["brightness_oct"]
    if abs(bright) >= .1:
        lines.append(f"B is {abs(bright):.1f} octave {'brighter' if bright > 0 else 'darker'}.")
    low = hb["low_end_db"] - ha["low_end_db"]
    if abs(low) >= 1.5:
        lines.append(f"B has {abs(low):.0f} dB {'more' if low > 0 else 'less'} low end.")
    space = hb["tail_db"] - ha["tail_db"]
    if abs(space) >= 3:
        lines.append(f"B rings on {abs(space):.0f} dB {'more' if space > 0 else 'less'} after you stop.")
    return lines or ["Listening heard no clear difference between A and B."]


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


# ---- a new sound, or a change to the one on B ----

NEW = ("make", "new sound", "build", "design", "give me", "something like", "i want a", "preset for", "tone for")
# Words that ask for a change. One of them alone does not make a request a change ("More Than a Feeling" and
# "Turn the Page" are songs): it must be about something of a sound.
_CHANGE = frozenset("""more less add adding remove without turn switch swap replace change raise lower boost cut
    reduce increase decrease tame drop lose keep too bit little touch tad slightly much enough instead no not try use
    put move set shorten lengthen tighten brighten darken soften""".split())
# The blocks of a chain. Named with "the" ("the delay in dotted eighths"), one of them makes a request a change.
_BLOCKS = frozenset("""amp amps cab cabs cabinet speaker mic microphone ir capture pedal pedals overdrive drive fuzz
    boost compressor gate eq delay delays reverb reverbs chorus flanger phaser tremolo vibrato wah octaver""".split())
# What else a change can be about: what is heard of a sound, and the sound itself.
_ABOUT = _BLOCKS | frozenset("""effect effects block blocks scene scenes level volume output input tone sound knob
    knobs gain distortion dirt saturation crunch sustain compression mids mid midrange middle bass treble highs lows
    presence bite punch attack body warmth brightness noise hum hiss feedback mud fizz tail tails repeats echo
    echoes mix decay ambience width modulation it its it's this that b""".split())
# Qualities of a sound. In the comparative, one of them makes a request a change ("tighter", "crunchier, like
# AC/DC"); plain, it does so with a change word ("too bright in the neck position", "not clean enough").
_PLAIN = frozenset("""bright dark dull warm round smooth rough harsh tight loose dirty heavy crunchy clean dry wet
    roomy full thick fat thin punchy wide narrow shrill fizzy muddy boomy washy noisy compressed distorted""".split())
_COMPARED = frozenset("""brighter darker duller warmer rounder smoother rougher harsher tighter looser dirtier heavier
    crunchier cleaner drier dryer wetter roomier fuller thicker fatter thinner punchier wider narrower louder quieter
    softer harder longer shorter bigger smaller higher lower faster slower fizzier muddier boomier""".split())
# "make it heavier", "make the delay shorter": a change, although it begins like a request for a new sound.
_MAKE_IT = re.compile(r"(?:can you |could you |please )?make (?:it|this|that|them|b|the (?:tone|sound|"
                      + "|".join(sorted(_BLOCKS)) + r"))\b")
_THE_BLOCK = re.compile(r"\bthe (?:" + "|".join(sorted(_BLOCKS)) + r")\b")
_A_SOUND = re.compile(r"(?:a|an|some) (?!bit\b|little\b|touch\b|tad\b|lot\b).*\b(?:tone|sound|preset|patch)\b")


def _wants_new(text):
    """Whether a request says outright that it is for a new sound."""
    lowered = text.casefold()
    return any(lowered.startswith(word) or f" {word} " in f" {lowered} " for word in NEW) or " - " in text


def reads_as_change(text):
    """Whether a request reads as a change to the sound on B rather than as a request for a new one.

    A request starts a round unless it reads as a change: "make it …", a remark the app understands, a quality
    in the comparative ("tighter", "a bit cleaner"), a change word together with something of a sound ("more
    sustain from the fuzz", "swap the amp", "less of it"), or a block of the chain named with "the" ("the delay in
    dotted eighths"). An artist, a song or a description ("Comfortably Numb, the second solo", "More Than a
    Feeling", "jazzy clean") is a request for a new sound, and so is anything that says so ("make …")."""
    lowered = " ".join(re.findall(r"[a-z0-9'/&-]+", text.casefold().replace("’", "'")))
    if _MAKE_IT.match(lowered):
        return True
    if _wants_new(text) or _A_SOUND.match(lowered):
        return False
    if words.read(text) or knobs.direction(text):
        return True
    tokens = set(re.findall(r"[a-z']+", lowered))
    if tokens & _COMPARED:
        return True
    if tokens & _CHANGE and tokens & (_ABOUT | _PLAIN):
        return True
    return bool(_THE_BLOCK.search(lowered))
