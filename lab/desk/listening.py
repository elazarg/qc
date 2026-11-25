"""Listening while the unit is free: what on show has not been heard as it plays now is listened to, silently,
and knobs that were turned are checked by listening.

What a measurement holds for. A sound is measured on a take and a part of it, with its chain as it is, at one
scene (which matters only when the sound's scenes differ). `playing_as` says what that is for a sound now, and
every question of whether a measurement holds is answered by comparing with it: `heard` (the record without the
knobs), `evidence` (the record as the sound plays now, knobs included), `unheard` (why a listen failed).

What is listened to, and when (`use_free_unit`, called from the studio's `poll`). While nothing plays, or the
guitar plays and its strings are quiet, nobody else has the audio input and the unit has nothing to do (a note
played during a listen ends it, and the guitar is heard again at once):

1. knobs turned on a sound, once they have stood still for `settle_seconds`, are checked (`check_shaping`);
2. once the unit has been left alone (or the strings quiet) for `idle_seconds`: the sounds on A and B, then the designs of the round on
   show, each that has not been heard as it plays now; then those heard with an earlier layout of the test
   signals;
3. A and B are made ready again if listening used the unit, and three more designs are prepared.

Before the unit is borrowed it is asked what it is on (`look_first`): a preset the player chose there by hand is
left alone until they come back to the desk's sounds. A listen that failed says so on the sound (`heard` with
"failed") and is not tried again until the chain, the phrase or the scene changes; one that was stopped, or
that the unit turned away, says nothing about the sound and is made again later.
"""

import time

from lab.sound import knobs

SETTLE_SECONDS = 4.     # a turn is checked by listening once no knob has moved for this long
# Silent listening starts once the unit has been left alone for this long: nothing playing, or the guitar playing
# and its strings quiet.
IDLE_SECONDS = 3.
# The one correction a check may make to a knob, as a factor on its position: further when it did too little,
# back when it did the opposite of what it is for.
CORRECTION = {"too small": 1.6, "opposite": .5}
# What a check found of a knob, as the status line says it.
VERDICT = {"as intended": "did what it says", "too small": "did little here", "opposite": "went the other way"}
CORRECTION_LIMIT = .4   # never more than this much of a knob's travel
# Why a silent listen can end without saying anything about the sound it was listening to: it was stopped, or the
# unit was not the app's to use just then.
NOT_THE_SOUND = frozenset({"Stopped", "Played", "Moved", "Unsaved", "NotOurs", "EditorActive", "WorkerGone"})
STOPPED = frozenset({"Stopped", "Played"})      # stopped by the player: by pressing something, or by playing


class Listening:
    def __init__(self, studio):
        self.studio = studio
        self.shaping = None               # (sound id, knobs turned, when) waiting to be checked by listening
        self.settle_seconds = SETTLE_SECONDS
        self.idle_seconds = IDLE_SECONDS
        self.free_since = None          # since when the unit has been left alone
        self.hearing = None               # the sound a silent listen in flight is for
        self.listen_after = 0.            # no silent listen before this (after one the unit turned away)
        self.looking = False              # asking the unit what it is on, before borrowing it
        self.looked = False               # ... and it was on one of the app's own slots, in this quiet stretch
        self.away = False                 # the player chose a preset of their own on the unit: it is left alone
        self.borrowed = False             # something used the unit: A and B are made ready again afterwards
        self.settled = None               # what was on show when nothing was left to hear or prepare
        self.quick_levels = {}            # sound id -> short measurement or failure, for its exact setting

    # ---- what the studio tells it ----

    def leave_guitar(self):
        """A silent listen takes the unit while the guitar is live and the strings are quiet: the guitar is
        disconnected for it, and comes back after it (`Studio.guitar_back`), or at the first note played."""
        if self.studio.state.mode == "guitar":
            self.studio._set(mode=None)

    def lent(self):
        """Something is using the unit that takes A and B off it (a listen, a recording, a measurement)."""
        self.borrowed = True

    def made_ready(self):
        """A and B are being made ready again: the unit is the desk's, and nothing is owed."""
        self.borrowed = self.away = False
        self.listen_after = 0.

    def came_back(self):
        """The player played, or clicked a tile: the unit is theirs to play the desk's sounds on again."""
        self.away, self.listen_after = False, 0.

    def left_alone(self):
        """The player chose another preset on the unit: nothing is listened to until they come back."""
        self.away = True

    # ---- measurements, and whether they hold ----

    def playing_as(self, sound):
        """What a measurement of `sound` must have been made on to hold for what plays now: this take and this
        part of it, this chain, and the scene heard (any scene, when the sound's scenes do not differ). Every
        question of whether a measurement holds is answered by comparing with this."""
        studio = self.studio
        return (studio.phrase_key(), sound.chain.digest(),
                studio.state.scene if sound.chain.scenes_in_use() > 1 else 0)

    @staticmethod
    def made_on(record):
        return record.get("phrase"), record.get("digest"), record.get("scene", 0)

    def heard(self, sound):
        """The record of listening to `sound` without its knobs, when it holds for what plays now, or None. It is
        the record of a listen that failed when it has "failed"."""
        heard = sound.heard
        return heard if heard and self.made_on(heard) == self.playing_as(sound) else None

    def evidence(self, sound):
        """What was measured of `sound` as it plays now (this phrase, this chain, this scene, these knobs), or
        None."""
        heard = self.heard(sound)
        if heard is None or "failed" in heard:
            return None
        if not sound.knobs:
            return heard
        shaped = heard.get("shaped") or {}
        # The shaped measurement is kept inside the one it was compared with; one written before that was so says
        # itself what it was made on.
        made_on = self.made_on({**heard, **{key: shaped[key] for key in ("phrase", "scene") if key in shaped}})
        return shaped if shaped.get("knobs") == sound.knobs and made_on == self.playing_as(sound) else None

    def level_evidence(self, sound):
        """Use full measurement when present, otherwise a short audition or verified saved trim."""
        full = self.evidence(sound)
        if full is not None and full.get("true_peak_db") is not None:
            return full
        for record in (self.quick_levels.get(sound.id), sound.origin.get("music_fit")):
            if (isinstance(record, dict) and record.get("knobs") == sound.knobs
                    and self.made_on(record) == self.playing_as(sound) and "failed" not in record):
                return record
        return full

    def unheard(self, sound):
        """Why `sound` could not be listened to as it plays now, or None when it could or has not been tried."""
        heard = self.heard(sound) if sound is not None else None
        return heard.get("failed") if heard else None

    def current(self, inputs):
        """Whether the take, part, scene and sound (its chain unchanged) a check was started on are still what is
        on show. `inputs` is (phrase key, the audio, scene, chain digest, sound id)."""
        studio = self.studio
        key, _, scene, digest, sound_id = inputs
        if sound_id not in (studio.state.a, studio.state.b):
            return False
        try:
            sound = studio.store.sound(sound_id)
        except (OSError, ValueError):
            return False
        return self.playing_as(sound) == (key, digest, scene if sound.chain.scenes_in_use() > 1 else 0)

    def not_measured(self, side, sound):
        """Why there is no measurement of the sound on a side as it plays now, and what will bring one; None when
        there is one."""
        why = self.unheard(sound)
        if why:
            return f"{side} could not be listened to: {why}"
        heard = self.heard(sound)
        if heard is None or "probe" not in heard:
            return f"{side}: not measured as it is set now"
        if self.evidence(sound) is not None:
            return None
        # Turned since, corrected after they were measured, or measured on another take, part or scene: the
        # Measure symbol beside it, or a turn, measures them where they stand.
        return f"{side}: Shape knobs not measured where they stand"

    # ---- listening while the unit is free ----

    def use_free_unit(self, now):
        """While nothing plays, nobody else has the input and the unit has nothing to do: check knobs that were
        turned, then listen to what is on show and has not been heard as it plays now, then make A and B ready
        again, then prepare three more. Listening starts only once the unit has been left alone for a moment."""
        studio = self.studio
        if self.hearing is not None or self.looking:
            return                                  # the studio's own listening: the quiet stretch goes on
        quiet = studio.strings_quiet(now)
        if not (studio.opened and (not studio.state.mode or quiet) and studio.audio_held_by is None
                and studio.phrase is not None and not studio.host.busy and studio.host.failed is None):
            self.free_since, self.looked = None, False
            return
        if self.free_since is None:
            self.free_since = now
        if quiet:                                   # the guitar plays: left alone since the last note
            self.free_since = max(self.free_since, now - quiet)
        if self.away or now < self.listen_after:
            return
        if studio.media_reference is not None and now - self.free_since >= min(self.idle_seconds, .25):
            sound = self.needs_level()
            if sound is not None:
                if self.looked:
                    self.measure_level(sound)
                else:
                    self.look_first()
                return
        if self.shaping is not None and now - self.shaping[2] >= self.settle_seconds:
            if not self.looked:
                self.look_first()
                return
            if self.check_shaping(now):
                return
        on_show = (studio.state.a, studio.state.b, studio.state.round, studio.phrase_key(), studio.state.scene)
        if now - self.free_since < self.idle_seconds or self.settled == on_show:
            return
        sound = self.next_to_hear()
        if sound is not None and not self.looked:
            self.look_first()
        elif sound is not None:
            self.listen(sound)
        elif self.borrowed:
            self.borrowed = False
            studio._show()                            # listening used the unit: A and B go back, knobs as they stand
        else:
            self.settled = on_show
            studio.asking.prepare_next()

    def look_first(self):
        """Before the unit is borrowed to listen: ask what it is on. When the player has chosen a preset of their
        own there, by hand, it is left alone until they come back to the desk's sounds."""
        studio = self.studio
        self.looking = True

        def found(result):
            self.looking = False
            if result.get("ours"):          # on a slot the app works in or put up itself
                self.looked = True
            else:
                self.turned_away("Moved", "Another preset is selected on the Quad Cortex, so Tone Lab leaves it "
                                           "alone and listens to nothing. Click A or B to come back.")

        def failed(error):
            self.looking = False
            self.turned_away(getattr(error, "kind", ""), error, wait=60)

        studio.later(studio.host.call("inputs"), found, failed)

    def needs_level(self):
        studio = self.studio
        side = studio.state.side
        for key in (side, "a" if side == "b" else "b"):
            sound = studio.sound(key)
            if sound is None:
                continue
            evidence = self.level_evidence(sound)
            if evidence and evidence.get("true_peak_db") is not None:
                continue
            tried = self.quick_levels.get(sound.id)
            if tried and self.made_on(tried) == self.playing_as(sound) and tried["knobs"] == sound.knobs:
                continue
            return sound
        return None

    def measure_level(self, sound):
        """Get audition level from one pass. The player can interrupt it by pressing Play."""
        studio = self.studio
        phrase_key, digest, scene = self.playing_as(sound)
        record = {"phrase": phrase_key, "digest": digest, "scene": scene, "knobs": dict(sound.knobs), "short": True}
        self.hearing, self.borrowed = sound.id, True
        self.leave_guitar()
        studio.begin("level measurement", "Checking audition level…")

        def received(result):
            self.hearing = None
            self.quick_levels[sound.id] = {**record, **result}
            studio._set()
            if not studio.state.mode and not result.get("failed"):
                planned = studio._audition(studio.sound("a"), studio.sound("b"))
                if (planned != studio.prepared_audition and planned != studio.refused_audition
                        and any(boost for _, boost, _ in planned)):
                    studio._show()

        def failed(error):
            self.hearing = None
            kind = getattr(error, "kind", "")
            if kind not in NOT_THE_SOUND:
                received({"failed": True})
            elif kind not in STOPPED:
                self.turned_away(kind, error)

        studio.later(studio.host.call("lab.tools.levels.audition_reading", sound.chain, sound.knobs, studio.phrase[1], scene),
                     received, failed, doing="level measurement")

    def turned_away(self, kind, error, wait=15):
        """The unit was not the app's to listen with. After the player chose another preset there, nothing is
        listened to until they come back; otherwise it is tried again after `wait` seconds."""
        studio = self.studio
        changes = {}
        if kind == "Moved":
            self.away = True
            changes["mode"] = None          # nothing of the desk's plays on a preset the player chose
        self.listen_after = time.monotonic() + wait
        studio._set(status=str(error), **changes)

    def next_to_hear(self):
        """The next sound to listen to: the sounds on A and B first, then the designs of the round on show. One
        that could not be heard is not tried again until its chain, the phrase or the scene changes. A sound whose
        chain was already listened to on another sound takes what came of it. When nothing is left that was never
        heard, a sound heard with an earlier layout of the test signals is listened to again."""
        studio = self.studio
        ids = [sound_id for sound_id in (studio.state.a, studio.state.b) if sound_id]
        ids += [item["sound"] for item in (studio.round() or {}).get("candidates", [])]
        sounds, known = [], {}
        for sound_id in dict.fromkeys(ids):
            try:
                sound = studio.store.sound(sound_id)
            except Exception:  # noqa: BLE001  a file that is gone or unreadable is not listened to
                continue
            sounds.append(sound)
            heard = self.heard(sound)
            if heard is not None and ("failed" in heard or ("probe" in heard and not _earlier_tests(heard))):
                known.setdefault(self.playing_as(sound), heard)
        again = None
        for sound in sounds:
            heard = self.heard(sound)
            if heard is not None and ("probe" in heard or "failed" in heard):
                if again is None and _earlier_tests(heard):
                    again = sound
                continue
            twin = known.get(self.playing_as(sound))
            if twin is None:
                return sound
            sound.heard = {key: value for key, value in twin.items() if key != "shaped"}
            studio.store.save(sound)
        return again

    def listen(self, sound, then=None, dropped=None):
        """Listen to `sound` without its knobs, silently, as it plays now, and keep what was measured with what it
        was measured on. `then(sound)` follows a measurement; `dropped()` a listen that was stopped."""
        studio = self.studio
        phrase_key, digest, scene = self.playing_as(sound)
        sound_id, name = sound.id, "a sound" if studio.state.blind else sound.name
        self.hearing, self.borrowed = sound_id, True
        self.leave_guitar()
        studio.begin("listen", f"Listening to {name}, silently…")

        def heard(result):
            self.hearing = None
            fresh = studio.store.sound(sound_id)        # knobs or marks may have changed meanwhile
            # "probe" marks a record as made with the current tests: without it the sound would be listened to again.
            fresh.heard = {"probe": None, "phrase": phrase_key, "digest": digest, "scene": scene, **result.to_record()}
            if result.level_db is not None and result.level_db > -90:
                fresh.tags = [tag for tag in fresh.tags if not tag.startswith("heard:")] + [
                    f"heard: {result.said()}"]
            studio.store.save(fresh)
            studio._set()
            if then is not None:
                then(fresh)

        def failed(error):
            self.hearing = None
            kind = getattr(error, "kind", "")
            if kind in NOT_THE_SOUND:
                # Nothing was learned about the sound: it is listened to again later.
                if kind not in STOPPED:
                    self.turned_away(kind, error)
                if dropped is not None:
                    dropped()
                return
            fresh = studio.store.sound(sound_id)
            fresh.heard = {"failed": str(error), "phrase": phrase_key, "digest": digest, "scene": scene}
            fresh.tags = [tag for tag in fresh.tags if not tag.startswith("heard:")]
            studio.store.save(fresh)
            studio.fault(f"Could not listen to {name}: {error}")

        studio.later(studio.host.call("hear", sound.chain, studio.phrase[1], scene, interrupt=studio.live), heard,
                     failed, doing="listen")

    # ---- knobs that check themselves ----

    def check_later(self, sound_id, turned):
        """Have knobs that were turned on a sound checked by listening, once the turning has stopped and the unit
        is free (`_check_shaping`)."""
        earlier = self.shaping[1] if self.shaping and self.shaping[0] == sound_id else set()
        self.shaping = (sound_id, set(turned) | earlier, time.monotonic())

    def check_shaping(self, now):
        """Once the knobs of a sound have not been turned for a few seconds (and the unit is free), listen to the
        sound as shaped, silently, and compare it with the sound heard without its knobs. True when a listen was
        started.

        A knob can do something else on this amp than the table says (amp controls interact: docs/TONE_RESEARCH.md,
        finding 8). One knob that measured the wrong way, or too little, is corrected once; nothing is measured
        again for that correction, so this never loops."""
        studio = self.studio
        if self.shaping is None:
            return False
        sound_id, turned, at = self.shaping
        if now - at < self.settle_seconds:
            return False
        self.shaping = None
        if sound_id not in (studio.state.a, studio.state.b):
            return False                            # no longer on show
        try:
            sound = studio.store.sound(sound_id)
        except (OSError, ValueError):
            return False
        if not sound.knobs:
            return False
        # The take, the part of it, the scene and the chain are fixed for the whole check: both measurements are
        # made on them, and a correction is made only while they are still what is on show.
        inputs = (studio.phrase_key(), studio.phrase[1], studio.state.scene, sound.chain.digest(), sound.id)
        heard = self.heard(sound)
        if heard is not None and "failed" in heard:
            return False                            # it cannot be listened to: nothing to compare with
        if heard is not None and "probe" in heard:
            self.hear_shaped(sound, turned, inputs)
            return True

        # The sound without its knobs has not been heard as it plays now: that first. A listen the player's own
        # playing stopped is made again later.
        self.listen(sound, then=lambda fresh: self.current(inputs) and self.hear_shaped(fresh, turned, inputs),
                     dropped=lambda: self.check_later(sound_id, turned))
        return True

    def hear_shaped(self, sound, turned, inputs):
        studio = self.studio
        sound_id, values = sound.id, dict(sound.knobs)
        phrase_key, _, scene = self.playing_as(sound)
        name = "a sound" if studio.state.blind else sound.name
        self.hearing, self.borrowed = sound_id, True
        self.leave_guitar()
        studio.begin("listen", f"Listening to {name} as shaped, silently…")

        def heard(result):
            self.hearing = None
            fresh = studio.store.sound(sound_id)
            if fresh.knobs != values or self.heard(fresh) is None or not self.current(inputs):
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
                if studio.remark and studio.remark["sound"] == sound_id and studio.remark["after"] == values:
                    studio.remark["after"] = dict(fresh.knobs)     # the remedy as corrected is still the remedy
            studio.store.save(fresh)
            if correction and studio.state.mode and studio.state.shown:
                # Playing began meanwhile, which made A and B ready again: the corrected knobs go on as it plays.
                for side in ("a", "b"):
                    if getattr(studio.state, side) == sound_id:
                        studio.later(studio.host.call("turn", side, fresh.knobs), lambda moves: None)
            studio.say(_checked_words(found, turned, correction))

        def failed(error):
            self.hearing = None
            kind = getattr(error, "kind", "")
            if kind in STOPPED:
                self.check_later(sound_id, turned)     # the player's own playing stopped it: checked afterwards
                return
            if kind in NOT_THE_SOUND:
                self.turned_away(kind, error)
            else:
                studio.fault(f"Could not listen to the shaped sound: {error}")

        studio.later(studio.host.call("hear_shaped", sound.chain, values, inputs[1], scene, interrupt=studio.live),
                     heard, failed, doing="listen")


def _earlier_tests(heard):
    """Whether a record was made with an earlier layout of the test signals (`lab.hearing.probe.VERSION`): its
    distortion and echo figures are not those of the current tests, so it is listened to again when there is time."""
    probe = heard.get("probe")
    return isinstance(probe, dict) and probe.get("version", 1) < 2


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
    """What listening measured of the knobs just turned, in one line for the status, in the player's terms:
    whether each knob did what its name says, with the measure it was judged by in brackets, and the one
    correction made."""
    parts, unmeasured = [], []
    for key in sorted(turned, key=lambda key: list(knobs.KNOBS).index(key) if key in knobs.KNOBS else 99):
        entry = found.get(key)
        if entry is None:
            continue
        title = knobs.KNOBS[key][0]
        if entry["verdict"] == "not measurable":
            unmeasured.append(title)
            continue
        # A knob without a measure of its own is judged by another quality's, and the line says so.
        proxy = f"; judged by the measure of another quality, {knobs.PROXY[key]}" if entry.get("proxy") else ""
        part = f"{title} {VERDICT.get(entry['verdict'], entry['verdict'])} ({_measured(entry)}{proxy})"
        if correction and correction[0] == key:
            _, old, new = correction
            part += (f", so it was turned {'further' if abs(new) > abs(old) else 'back'}, from {old:+.2f} to "
                     f"{new:+.2f}, once")
        parts.append(part)
    line = ("; ".join(parts) + ".") if parts else "Listening measures none of the knobs just turned."
    if unmeasured:
        line += f" Listening cannot measure {', '.join(unmeasured)}."
    return line
