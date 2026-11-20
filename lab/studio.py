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

from . import design, knobs, knowledge, words
from .assistant import Assistant
from .measure import summary
from .sounds import Sound, Store, describe

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
        self.shaping = None               # (sound id, knobs turned, when) waiting to be checked by listening
        self.settle_seconds = SETTLE_SECONDS

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
        self._check_shaping()

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
        """Play A and B equally loud, when both have been measured: the louder is turned down to the quieter.

        Matched by integrated loudness (ITU-R BS.1770, LUFS), which follows perceived loudness across sounds of
        different brightness better than a plain level does (TONE_RESEARCH.md, 13); by RMS level only when one of
        the two was measured before loudness was recorded."""
        from .engine import PREVIEW_DB

        sounds = {side: self.sound(side) for side in ("a", "b")}
        heard = {side: sound.heard for side, sound in sounds.items()
                 if sound and sound.heard and sound.heard.get("level_db", -200) > -90}
        key = "loudness_lufs" if all(record.get("loudness_lufs") is not None for record in heard.values()) \
            else "level_db"
        levels = {side: record[key] for side, record in heard.items()}
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

        A knob can do something else on this amp than the table says (amp controls interact: TONE_RESEARCH.md,
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
        phrase_id = self.phrase[0]
        if sound.heard and "probe" in sound.heard and sound.heard.get("phrase") == phrase_id:
            self._hear_shaped(sound, turned)
            return
        # The sound without its knobs has not been heard on this phrase: that first.
        self.set(working=f"Listening to {sound.name} without its Shape knobs, silently…")

        def heard(result):
            fresh = self.store.sound(sound_id)
            fresh.heard = {"phrase": phrase_id, "digest": fresh.chain.digest(), **result.to_record()}
            self.store.save(fresh)
            self.set(working="")
            self._hear_shaped(fresh, turned)

        self.later(self.host.call("hear", sound.chain, self.phrase[1]), heard, self._shaping_failed)

    def _shaping_failed(self, error):
        self.set(working="", status=f"Could not listen to the shaped sound: {error}")
        if not self.state.mode:
            self._show()

    def _hear_shaped(self, sound, turned):
        sound_id, values, phrase_id = sound.id, dict(sound.knobs), self.phrase[0]
        self.set(working=f"Listening to {sound.name} as shaped, silently…")

        def heard(result):
            fresh = self.store.sound(sound_id)
            self.set(working="")
            if fresh.knobs != values or not fresh.heard:
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

        self.later(self.host.call("hear_shaped", sound.chain, values, self.phrase[1]), heard, self._shaping_failed)

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
        from . import catalog
        from .sounds import Sound

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
            from .library import cached
        except ImportError:
            return None
        wanted = text.strip().casefold()
        found = [item for item in cached(self.folder) or [] if not item["slot"].get("is_factory")
                 and item["name"].strip().casefold() == wanted]
        return (found[0]["slot"], found[0]["name"]) if len(found) == 1 else None

    def _assets(self):
        from .assets import cached

        return cached(self.folder) or []

    def read_assets(self, done=None):
        """Read the IRs and captures on the unit (about ten seconds) and remember them."""
        self.set(working="Reading the IRs and captures on the Quad Cortex…")

        def read(records):
            self.set(working="", status=f"{len(records)} IRs and captures on the unit.")
            if done:
                done(records)

        self.later(self.host.call("lab.assets.listing", folder=str(self.folder)), read)

    def use_asset(self, asset, *, has_cab=None, version=None):
        """A copy of the sound on B with one of the player's IRs in place of its cab, or a capture in place of
        its amp (and cab, when the capture has one)."""
        from . import assets
        from .sounds import Sound

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
        from .profile import facts

        cached = getattr(self, "guitar_cache", None)
        if cached is None or cached[0] != self.state.instrument:
            cached = (self.state.instrument, self.guitar_profile()[0])
            self.guitar_cache = cached
        return facts(cached[1], self._setting()) if cached[1] else None

    def _listening(self):
        """What guitar music the player listens to most, if the listening history was analysed (personal; kept in
        the data folder)."""
        from .listening import prior

        try:
            record = json.loads((self.folder / "personal" / "listening.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return prior(record)

    def new_round(self, text, *, earlier=None, by="player", after=None):
        """Three designs for `text`. `after` is the round these follow, whose designs and what was heard of them
        (`earlier`) inform these."""
        quietly = by == "app"
        if not quietly:
            self.set(working="Designing three ways to get there. About a minute…")
        instrument = self._instrument()
        stop = self._request() if not quietly else None
        future = self.thinking.submit(design.round_of_three, self.assistant, text, instrument, earlier=earlier, by=by,
                                      cancel=stop, assets_known=self._assets(), guitar=self._guitar(),
                                      setting=self._setting(), listening=self._listening())
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
            self.set(working="", round=record["id"])
            if self.state.revision == revision:
                self.put("b", self.store.sound(record["candidates"][0]["sound"]))
                self.say(f"Aiming for {summary(aim)}. "
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
        if word in sound.marks:
            sound.marks.remove(word)
        else:
            opposite = {"keeper": "not this", "not this": "keeper"}.get(word)
            sound.marks = [item for item in sound.marks if item != opposite] + [word]
            self._choice(sound, word)
        self.store.save(sound)
        self.say(f"{sound.name}: " + (", ".join(sound.marks) if sound.marks else "no marks"))
        self._learn()

    def _choice(self, sound, word):
        """A keeper or a not-this given while A and B are both on show is a choice between them (blind when names
        are hidden), recorded for the taste model."""
        from . import taste

        side = "a" if self.state.a == sound.id else "b" if self.state.b == sound.id else None
        other = self.sound("b" if side == "a" else "a") if side else None
        if other is None or other.id == sound.id or word not in ("keeper", "not this"):
            return
        kept, over = (sound, other) if word == "keeper" else (other, sound)
        taste.record_choice(self.folder, kept.id, over.id, "blind" if self.state.blind else "a/b")

    def _learn(self):
        """Fit the taste model again from everything chosen so far; cheap, and quiet if it fails."""
        from . import taste

        try:
            self.taste = taste.learn(self.folder)
        except Exception:  # noqa: BLE001
            self.taste = None

    def taste_lines(self):
        """What the player's choices so far say, in measured quantities, or that there is not enough yet."""
        from . import taste

        model = getattr(self, "taste", None) or taste.load(self.folder)
        return taste.describe(model) if model else ["No choices yet: marks and A/B choices teach it."]

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
        future = self.thinking.submit(design.revise, self.assistant, sound, text, self._instrument(), cancel=stop,
                                      assets_known=self._assets(), guitar=self._guitar(), setting=self._setting())
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
            if sound.heard is None or "probe" not in sound.heard:     # never heard, or heard before test tones
                self.set(working=f"Listening to {sound.name}, silently…")

                def heard(result, sound=sound):
                    sound.heard = {"phrase": self.phrase[0], "digest": sound.chain.digest(), **result.to_record()}
                    self.store.save(sound)
                    if result.level_db is not None and result.level_db > -90:
                        sound.tags = [tag for tag in sound.tags if not tag.startswith("heard:")] + [
                            f"heard: {result.said()}"]
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
        chain = sound.chain

        def saved(result):
            slot, _ = result
            sound.saved = {"slot": slot, "name": name}
            self.store.save(sound)
            self._learn()
            self.set(working="", shown=None, status=f"Saved · Tone Lab {slot['position'] + 1} · {name}")
            self._show()

        self.later(self.host.call("save", chain, name, sound.knobs or None), saved)

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
            self.phrase_whole = (dry[:, :1], 0, len(dry))
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


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def knowledge_id():
    from .sounds import new_id

    return new_id()


NEW = ("make", "new sound", "build", "design", "give me", "something like", "i want a", "preset for", "tone for")


def _wants_new(text):
    lowered = text.casefold()
    return any(lowered.startswith(word) or f" {word} " in f" {lowered} " for word in NEW) or " - " in text
