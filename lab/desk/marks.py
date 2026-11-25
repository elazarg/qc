"""The player's marks on the sounds, the choices they amount to for the taste model, and the songs a kept sound
becomes a part of."""

from lab.desk.requests import song_name


class Marks:
    def __init__(self, studio):
        self.studio = studio
        self.taste = None                 # the taste model, once fitted

    def mark(self, word, side=None):
        """Put a mark on the sound on a side, or take it off. "keeper" and "not this" exclude each other."""
        studio = self.studio
        sound = studio.sound(side or studio.state.side)
        if sound is None:
            return
        word = word.strip()[:24]
        if not word:
            return
        blind_choice = studio.state.blind and word in ("keeper", "not this")
        if word in sound.marks and not blind_choice:
            sound.marks.remove(word)
        else:
            # A choice made blind is a choice even when that sound already had the mark: it is kept, and counted.
            opposite = {"keeper": "not this", "not this": "keeper"}.get(word)
            sound.marks = [item for item in sound.marks if item not in (opposite, word)] + [word]
            self.choice(sound, word)
        studio.store.save(sound)
        if blind_choice:
            # The choice is made: now say which was which, and put them back where they were.
            side = (side or studio.state.side).upper()
            studio.reveal(f"{side}: {word}. ")
        else:
            studio.say(f"{sound.name}: " + (", ".join(sound.marks) if sound.marks else "no marks"))
        if word == "keeper" and word in sound.marks:
            self.into_song(sound)
        self.learn()

    def choice(self, sound, word):
        """A keeper or a not-this given while A and B are both on show is a choice between them (blind when names
        are hidden), recorded for the taste model."""
        studio = self.studio
        from lab.player import taste

        side = "a" if studio.state.a == sound.id else "b" if studio.state.b == sound.id else None
        other = studio.sound("b" if side == "a" else "a") if side else None
        if other is None or other.id == sound.id or word not in ("keeper", "not this"):
            return
        kept, over = (sound, other) if word == "keeper" else (other, sound)
        context = {"phrase": studio.phrase_key(), "scene": studio.state.scene,
                   "knobs": {kept.id: dict(kept.knobs), over.id: dict(over.knobs)}}
        taste.record_choice(studio.folder, kept.id, over.id, "blind" if studio.state.blind else "a/b",
                            round_id=studio.state.round, context=context, event=word)

    def learn(self):
        """Fit the taste model again from everything chosen so far; cheap, and quiet if it fails."""
        studio = self.studio
        from lab.player import taste

        try:
            self.taste = taste.learn(studio.folder)
        except Exception:  # noqa: BLE001
            self.taste = None

    def taste_lines(self):
        """What the player's choices so far say, in measured quantities, or how far they are from saying
        anything; nothing before the first choice."""
        studio = self.studio
        from lab.player import taste

        model = self.taste or taste.load(studio.folder)
        return taste.describe(model) if model else []

    # ---- songs ----

    def into_song(self, sound):
        """A sound kept or saved from a round that was for a song becomes that song's part: the request said
        which song and part, so nothing has to be named by hand."""
        studio = self.studio
        song = (studio.rounds.of(sound.id) or {}).get("aim", {}).get("song")
        if not song and sound.parent:
            song = (studio.rounds.of(sound.parent) or {}).get("aim", {}).get("song")
        if not song:
            return
        name = song_name(song)
        record = studio.store.song(name)
        record.update(artist=song.get("artist"), instrument=studio.state.instrument)
        part = (song.get("part") or "main").capitalize()
        record["parts"] = [item for item in record["parts"] if item["name"] != part] + [{"name": part, "sound": sound.id}]
        if studio.phrase and not record.get("phrase"):
            record["phrase"] = studio.phrase[0]
        studio.store.save_song(record)
        studio._set(song=name)

    def add_part(self, part, scene=None):
        """Make the sound on show (or one scene of it, 0 to 7) a part of the song being worked on."""
        studio = self.studio
        if not studio.state.song:
            studio.say("Name the song first.")
            return
        sound = studio.sound(studio.state.side)
        if sound is None:
            return
        song = studio.store.song(studio.state.song)
        song["instrument"] = studio.state.instrument
        entry = {"name": part, "sound": sound.id, **({"scene": scene} if scene is not None else {})}
        song["parts"] = [item for item in song["parts"] if item["name"] != part] + [entry]
        if studio.phrase and not song.get("phrase"):
            song["phrase"] = studio.phrase[0]
        studio.store.save_song(song)
        where = f", scene {'ABCDEFGH'[scene]}" if scene is not None else ""
        studio.say(f"{sound.name}{where} is the {part} of {song['name']}.")
