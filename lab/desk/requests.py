"""Requests to the assistant: rounds of three designs and revisions of the sound on B, whose answer goes on show
only while it is still what was asked for; the rounds kept on disk; and telling a request for a new sound from
a change to the one on B.

A request runs on the assistant's thread (`Studio.thinking`) and is handled on the window's thread by the
studio's `poll`. Each of the player's requests has a cancel signal of its own (`requests`), and only the answer
of the newest (`latest`) goes on show: an older one is kept in History and said so. The app's own requests,
three more for a round whose designs have all been listened to (`prepare_next`), have signals too
(`background`), set only when the app closes.
"""

import json
from pathlib import Path
import re
import threading

from lab.sound import design, knobs, words
from lab.hearing.measure import summary
from lab.sound.sounds import describe, new_id, now, write_whole


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


class Requests:
    def __init__(self, studio):
        self.studio = studio
        self.requests = set()             # the player's requests in flight, each its own cancel signal
        self.background = set()           # the app's own requests in flight, cancelled only when the app closes
        self.latest = None                # the newest of the player's
        self.preparing = None             # the round whose next three are being designed

    # ---- the requests in flight ----

    def signal(self, quietly=False):
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
        studio = self.studio
        active = [signal for signal in self.requests if not signal.is_set()]
        if not active:
            return False
        for signal in active:
            signal.set()
            studio.doing.pop(signal, None)
        self.requests.clear()
        studio._set(status="Request cancelled.")
        return True

    def failed(self, stop, error):
        """A request that failed. The working line and Cancel stay while a newer one is still on its way: each
        request is an activity of its own."""
        studio = self.studio
        if stop is self.latest:
            studio.fault(str(error))
        else:
            studio.fault(f"An earlier request failed ({error}); the latest is still on its way.")

    # ---- asking ----

    def design(self, function, *args, **options):
        """Run a call to the designer on the assistant's thread. What the instrument puts out (`profile.facts`) is
        worked out there first, from the takes as they are now: reading them takes a second or more."""
        studio = self.studio
        from lab.hearing.profile import facts

        instrument, setting = studio.state.instrument, studio.guitar.setting()
        takes = studio.guitar.takes(instrument)
        source = studio.phrase[1] if studio.phrase is not None and options.get("by") != "app" else None

        def work():
            engineering = None
            if source is not None:
                import numpy as np

                from lab.hearing.audio import db

                peak = float(np.max(np.abs(source), initial=0.))
                if peak > 0:
                    engineering = {"dry_peak_dbfs": round(db(peak), 1), "dry_reached_full_scale": peak >= .999}
            found = studio.guitar.profile(instrument, takes)[0] if takes else None
            from lab.sound.layers import references

            text = args[1] if function is design.round_of_three else args[2]
            examples = references(text, studio.store.sounds()) if options.get("by") != "app" else None
            return function(*args, guitar=facts(found, setting) if found else None, setting=setting,
                            engineering=engineering, references=examples, **options)

        return studio.thinking.submit(work)

    def new_round(self, text, *, earlier=None, by="player", after=None, saying=None):
        """Three designs for `text`. `after` is the round these follow, whose designs and what was heard of them
        (`earlier`) inform these. `saying` is the working line, when it is not the usual one."""
        studio = self.studio
        quietly = by == "app"
        stop = self.signal(quietly)
        if not quietly:
            studio.begin(stop, saying or f"New round for “{text[:60]}” · about a minute…")
        future = self.design(design.round_of_three, studio.assistant, text, studio._instrument(), earlier=earlier,
                              by=by, cancel=stop, assets_known=studio._assets(),
                              listening=None if quietly else studio._listening())
        revision = studio.state.revision
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
            record = {"id": new_id(), "request": text, "aim": aim, "created": now(),
                      "previous": after, "by": by, "candidates": []}
            # The song is the one this request is for, or the one the round it follows was for: never one left
            # over from an earlier, unrelated request.
            song = aim.get("song") or (studio.rounds.get(after) or {}).get("aim", {}).get("song")
            if song:
                record["aim"]["song"] = song
            for sound, differs in designs:
                sound.tags = [f"aim: {summary(aim)}"]
                if song:
                    sound.origin = {**sound.origin, "song": song_name(song)}
                studio.store.save(sound)
                record["candidates"].append({"sound": sound.id, "differs": differs})
            studio.rounds.save(record)
            if after:
                studio.rounds.follow(after, record["id"])
            if quietly:
                studio.say("Three more designs are ready, informed by what was heard: click › next three.")
                return
            if stop is not self.latest:
                # A newer request is on its way or already answered: this one is kept, not put on show.
                studio.say(f"The designs for an earlier request ({text[:40]}) are in History; the latest request's "
                         "are the ones shown.")
                return
            on_show = {"round": record["id"], "song": song_name(song) if song else None}
            if studio.state.revision == revision:
                studio.put("b", studio.store.sound(record["candidates"][0]["sound"]), **on_show)
                studio.say(f"1 of {len(designs)} is on B · {designs[0][0].name}")
            else:
                studio._set(**on_show)
                studio.say(f"{len(designs)} designs are ready. B changed meanwhile, so it was left alone.")

        def failed(error):
            over()
            if not stop.is_set() and not quietly:
                self.failed(stop, error)

        studio.later(future, arrived, failed, doing=stop)

    def more(self, *, by="player"):
        """Three more for the round on show, informed by its designs, what listening heard and the player's marks."""
        studio = self.studio
        found = studio.round()
        if found is None:
            studio.say("Ask for a sound first.")
            return
        if found.get("next"):
            studio.go_round(found["next"])
            return
        if self.preparing == found["id"]:
            studio.say("Three more are on their way.")
            return
        earlier = []
        for candidate in found["candidates"]:
            sound = studio.store.sound(candidate["sound"])
            earlier.append({"name": sound.name, "differs": candidate["differs"], "chain": describe(sound.chain),
                            "heard": {key: sound.heard.get(key) for key in ("distortion", "compressed",
                                                                               "brightness", "tail")}
                            if sound.heard and "failed" not in sound.heard else None,
                            "player_marks": sound.marks, "player_said": [note["text"] for note in sound.notes],
                            "played": sound.played})
        self.new_round(found["request"], earlier=earlier, by=by, after=found["id"],
                       saying="Designing three more, from what was heard and what you marked…")

    def revise(self, sound, text, side="b"):
        """A copy of `sound` changed as `text` asks, put on `side` (the side it was on) when it comes, if that
        side has not changed meanwhile."""
        studio = self.studio
        stop = self.signal()
        studio.begin(stop, f"Revising {side.upper()} · “{text[:60]}”…")
        future = self.design(design.revise, studio.assistant, sound, text, studio._instrument(), cancel=stop,
                              assets_known=studio._assets())
        revision = studio.state.revision

        def revised(result):
            self.requests.discard(stop)
            if stop.is_set():
                return
            result.notes.append({"at": now(), "text": text})
            studio.store.save(result)
            if stop is not self.latest:
                studio.say(f"The revision for an earlier request ({text[:40]}) is in History; the latest request's "
                         "answer is the one shown.")
            elif studio.state.revision == revision:
                studio.put(side, result)
                studio.say(f"{result.name}, revised: {text}")
            else:
                studio.say(f"The revision of {sound.name} is ready in History. {side.upper()} changed meanwhile.")

        def failed(error):
            self.requests.discard(stop)
            if not stop.is_set():
                self.failed(stop, error)

        studio.later(future, revised, failed, doing=stop)

    # ---- the app's own ----

    def prepare_next(self):
        """When every design of the round on show has been listened to, prepare three more in the background, if
        the assistant may be called without asking. Only for a round the player asked for: the app's own rounds
        lead to no further call."""
        studio = self.studio
        found = studio.round()
        if found is None or found.get("next") or self.preparing or not studio.assistant.automatic:
            return
        if found.get("by") == "app":
            return
        try:
            heard = [studio.store.sound(candidate["sound"]).heard for candidate in found["candidates"]]
        except Exception:  # noqa: BLE001
            return
        if all(heard):
            self.more(by="app")


def song_name(song):
    """"Artist - Title" for a song the assistant named."""
    return " - ".join(part for part in (song.get("artist"), song.get("title")) if part)


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
