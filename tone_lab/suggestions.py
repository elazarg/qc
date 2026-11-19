"""The designs a request was answered with, on the desk: three at a time, stepped through on B.

A round arrives from the assistant with an aim and three designs. The first
goes on B at once. The app listens to the designs whenever the unit is free,
tags them, and has the next three prepared from what it measured and from what
the player said. Each design keeps its own knob positions, tags and review.
"""

from copy import deepcopy
from threading import Event
import tkinter as tk

from . import diagnostics, preset_design, rounds, theme
from .theme import DARK, DIM, FONT, INK, PAPER, SOFT, SUBTLE, TILE
from .tone_identity import Tone

MARKS = ("Closer", "Not it", "This is it")


def aim_words(found):
    aim = found["aim"]
    return f"{aim['drive']}, {aim['tone']}, {aim['space']}"


class Suggestions:
    """Which round is shown, which design is on B, and what happens to them over time."""

    AFTER_ARRIVAL = 25_000  # ms the unit is left alone when a round arrives, in case the player plays at once
    AFTER_PLAYING = 4_000   # ms after the input stops before the app takes the unit to listen
    WHEN_BUSY = 30_000      # ms before looking again when the unit was found in use

    def __init__(self, desk):
        self.desk = desk
        self.rounds = rounds.everything()
        self.shown = None
        self.pending = None      # The next round, being designed off the main thread.
        self.stop = None         # Set to take the unit back from a listening job.
        self.then = None         # What the player asked for while the app held the unit.
        self.timer = None
        self.reviewing = None    # (round id, place) while the request line is taking a review.
        self.sent = {}           # The knobs each design had when it was sent to be listened to.
        self.outdated = False    # The player said something after the next round was asked for.

    # ---- What is on show ----

    def found(self):
        return next((found for found in self.rounds if found["id"] == self.shown), None)

    def place(self):
        """The place in the shown round of the design that is on B, if it is one of them."""
        found, tone = self.found(), self.desk.tone_b
        if not found or not tone or not tone.draft_id:
            return None
        return next((place for place, candidate in enumerate(found["candidates"])
                     if candidate["draft"] == tone.draft_id), None)

    def restore(self, round_id):
        """Return to the round that was on show. Without one, the latest is shown, so that rounds are never hidden."""
        if any(found["id"] == round_id for found in self.rounds):
            self.shown = round_id
        elif self.shown is None and self.rounds:
            self.shown = self.rounds[-1]["id"]

    def adopt(self, found):
        """Take a round into the desk: its designs become drafts the desk knows."""
        for candidate in found["candidates"]:
            if candidate["draft"] not in self.desk.drafts:
                self.desk.drafts[candidate["draft"]] = rounds.draft_of(candidate)
        self.rounds = [other for other in self.rounds if other["id"] != found["id"]] + [found]

    # ---- A round arrives ----

    @staticmethod
    def design(text, guitar, take, cancel, phrase=None):
        """Runs off the main thread. `phrase` is the take the whole round will be listened to on."""
        return rounds.start(text, guitar, take, cancel=cancel, phrase=phrase)

    def arrived(self, found, current):
        desk = self.desk
        self.adopt(found)
        count = len(found["candidates"])
        busy = desk.job is not None and not desk.task_if_active("monitor")
        if busy or not current:
            why = "" if busy else " B changed while they were being designed, so it was left alone."
            desk.set_status(f"{count} designs are ready, aiming for {aim_words(found)}.{why}", kind="reading",
                            actions=(("Show them", lambda: self.show(found["id"])),))
        else:
            self.show(found["id"], announce=False)
            first = found["candidates"][0]
            desk.set_status(f"Aiming for {aim_words(found)}. 1 of {count} is on B · {first['name']}", kind="reading",
                            hint=found["aim"].get("note", ""),
                            actions=(("Undo", desk.undo_last), ("Why this sound", lambda: desk.tray.open("Chain"))))
        self.rest(self.AFTER_ARRIVAL)

    def show(self, round_id, *, announce=True):
        """Put a round on display with its first design on B."""
        self.shown = round_id
        self.put(0, announce=announce)

    # ---- Stepping ----

    def step(self, place):
        found = self.found()
        if self.desk.blind or not found or not 0 <= place < len(found["candidates"]) or place == self.place():
            return
        self.put(place)

    def put(self, place, *, announce=True):
        desk, found = self.desk, self.found()
        if desk.task_if_active("listen"):
            # The choice is of this design in this round, whatever is on show by the time the unit is back.
            chosen = (found["id"], found["candidates"][place]["draft"])
            self.interrupt(lambda: self.put_chosen(*chosen, announce=announce))
            return
        if desk.job is not None and not desk.task_if_active("monitor"):
            desk.set_status("Wait for the Quad Cortex to finish, then try again.")
            return
        self.sync()  # The design being left keeps the knobs it has now.
        candidate = found["candidates"][place]
        draft = desk.drafts[candidate["draft"]]
        desk.draft = draft
        desk.set_tone(Tone.from_draft(draft), shape=candidate["shape"], label=f"{candidate['name']} on B")
        if announce:
            # What sets it apart is on the strip already. The line only confirms the step.
            desk.set_status(f"{place + 1} of {len(found['candidates'])} · {candidate['name']} is on B.",
                            actions=(("Undo", desk.undo_last),))

    def put_chosen(self, round_id, draft_id, *, announce=True):
        found = next((found for found in self.rounds if found["id"] == round_id), None)
        place = next((place for place, candidate in enumerate(found["candidates"] if found else ())
                      if candidate["draft"] == draft_id), None)
        if place is not None:
            self.shown = round_id
            self.put(place, announce=announce)

    def browse(self, delta):
        """Look at an earlier or later round. B stays as it is until a design is chosen."""
        if not self.rounds or self.desk.blind:
            return
        ids = [found["id"] for found in self.rounds]
        at = ids.index(self.shown) if self.shown in ids else len(ids) - 1 if delta < 0 else -1
        target = min(len(ids) - 1, max(0, at + delta))
        if ids[target] != self.shown:
            self.shown = ids[target]
            found = self.found()
            self.desk.set_status(f"Round {target + 1} of {len(ids)} · {found['request'].splitlines()[0]} · "
                                 f"aiming for {aim_words(found)}")
            self.desk.remember()
            self.desk.update()

    # ---- What each design carries ----

    def sync(self):
        """Keep the knobs with the design they were turned on, and note that the player has heard it."""
        found, place, desk = self.found(), self.place(), self.desk
        if place is None:
            return
        candidate = found["candidates"][place]
        shape, played = dict(desk.shapes["b"]), candidate["played"] or desk.on_air() == "b"
        if (shape, played) != (candidate["shape"], candidate["played"]):
            candidate.update(shape=shape, played=played)
            rounds.save(found)

    def mark(self, place, word):
        found = self.found()
        tags = found["candidates"][place]["user_tags"]
        tags.remove(word) if word in tags else tags.append(word)
        rounds.save(found)
        self.outdated = self.pending is not None
        self.desk.update()
        self.consider()

    def review(self, place):
        found = self.found()
        candidate = found["candidates"][place]
        self.reviewing = (found["id"], place)
        self.desk.set_status(f"Say what you think of {place + 1} · {candidate['name']}, then Enter.", kind="progress",
                             actions=(("Cancel", self.cancel_review),))
        self.desk.focus_request()

    def cancel_review(self):
        self.reviewing = None
        self.desk.set_status("")

    def reviewed(self, text):
        round_id, place = self.reviewing
        self.reviewing = None
        found = next(found for found in self.rounds if found["id"] == round_id)
        found["candidates"][place]["review"] = text
        rounds.save(found)
        if self.pending is not None:
            # The three being prepared were asked for before this was said. They cannot know it.
            self.outdated = True
            self.desk.set_status("Noted. Three are already being prepared without it. Ask for three more "
                                 "afterwards to have it used.")
        else:
            self.desk.set_status("Noted. The next three will use it.", actions=(("Three more now", self.more),))
        self.desk.update()
        self.consider()

    # ---- The next three ----

    def consider(self):
        """Prepare the next round without being asked, once, when there is something to learn from."""
        found = self.found()
        if found and self.pending is None and rounds.wants_more(found):
            self.more(asked=False)

    def more(self, *, asked=True):
        found, desk = self.found(), self.desk
        if not found:
            return
        if self.pending is not None:
            if asked:
                desk.set_status("Three more are already being prepared.", kind="progress")
            return
        self.sync()
        found["followed"] = True
        rounds.save(found)
        self.outdated = False
        self.pending = desk.advisor.submit(rounds.again, deepcopy(found))
        if asked:
            desk.set_status("Preparing three more from what was measured and what you said. About a minute…",
                            kind="progress")
        desk.update()

    def poll(self):
        """Called on the desk's tick."""
        if self.pending is None or not self.pending.done():
            return
        finished, self.pending = self.pending, None
        try:
            found = finished.result()
        except Exception as error:  # noqa: BLE001
            diagnostics.LOG.exception("The next round could not be prepared")
            self.desk.set_status(f"Three more could not be prepared. {error}", kind="error",
                                 actions=(("Try again", self.more),))
            return
        self.adopt(found)
        late, self.outdated = self.outdated, False
        self.desk.set_status(f"Three more are ready, aiming for {aim_words(found)}."
                             + (" They were prepared before your last remark." if late else ""), kind="reading",
                             actions=(("Show them", lambda: self.show(found["id"])),
                                      *((("Three more, with the remark", self.more),) if late else ())))
        self.desk.update()

    # ---- Listening, whenever the unit is free ----

    def rest(self, wait=None):
        """The unit may be free soon. Look again after a pause, and listen if it is."""
        root = self.desk.root
        if self.timer is not None:
            root.after_cancel(self.timer)
        self.timer = root.after(self.AFTER_PLAYING if wait is None else wait, self.idle)

    def idle(self):
        self.timer = None
        desk = self.desk
        waiting = next((found for found in [self.found(), *self.rounds[::-1]] if found and rounds.unchecked(found)), None)
        if (waiting is None or desk.job is not None or desk.leaving is not None or desk.thinking is not None
                or not desk.take or desk.playing or desk.blind):
            return
        self.sync()
        if not waiting.get("phrase"):
            # Every design of a round is listened to on one phrase: the one selected now.
            waiting["phrase"] = desk.take.folder.name
            rounds.save(waiting)
        self.stop = Event()
        self.sent = {candidate["draft"]: dict(candidate["shape"]) for candidate in waiting["candidates"]}
        desk.submit("listen", "Listening to the designs, silently. Guitar or Phrase takes the unit back at once.",
                    self.listen, deepcopy(waiting), self.stop, desk.qc_position, desk.input_channel,
                    done=self.listened)
        if desk.task_if_active("listen"):
            desk.stop_signal = self.stop  # Space, and closing the desk, end it like any other job.

    @staticmethod
    def listen(found, stop, expect, physical_input):
        """Runs off the main thread, and does the listening in a process of its own."""
        try:
            return rounds.audition(found, stop, expect=expect, physical_input=physical_input)
        except Exception as error:  # noqa: BLE001
            diagnostics.LOG.exception("Listening to the designs failed")
            return found, "error", str(error)

    def forget(self):
        """The player pressed Stop. Whatever was waiting for the unit is dropped with it."""
        self.then = None

    def close(self):
        if self.timer is not None:
            self.desk.root.after_cancel(self.timer)
            self.timer = None
        if self.stop is not None:
            self.stop.set()

    def interrupt(self, then):
        """The player wants the unit. Stop listening and do what they asked as soon as it is back."""
        if self.stop is not None:
            self.stop.set()
        self.then = then
        self.desk.set_status("Giving the Quad Cortex back…", kind="progress")

    def listened(self, outcome):
        """Fold what the app heard into the round, leaving alone whatever the player changed meanwhile."""
        desk = self.desk
        result, status, message = outcome
        error = message if status == "error" else None
        stopped = status == "stopped" or self.stop is not None and self.stop.is_set()
        self.stop = None
        if error is not None:
            desk.set_status(f"The designs could not be listened to. {error}", kind="error")
        elif status == "busy":
            # The unit is in use away from Tone Lab. It was not touched. Look again later, quietly.
            desk.set_status("")
            then, self.then = self.then, None
            desk.update()
            if then:
                desk.root.after_idle(then)
            else:
                self.rest(self.WHEN_BUSY)
            return
        found = next((found for found in self.rounds if found["id"] == result["id"]), None)
        if found:
            on_b = self.place() if found["id"] == self.shown else None
            found["phrase"] = result.get("phrase") or found.get("phrase")
            for place, candidate in enumerate(found["candidates"]):
                heard = next((other for other in result["candidates"] if other["draft"] == candidate["draft"]), None)
                if heard and heard.get("failed"):
                    candidate["failed"] = heard["failed"]
                if not heard or heard["heard"] is None:
                    continue
                candidate.update({key: heard[key] for key in ("heard", "served", "served_shape", "tags", "miss")})
                untouched = candidate["shape"] == self.sent.get(candidate["draft"])
                if not candidate["played"] and untouched and heard["shape"] != candidate["shape"]:
                    # Nobody has heard this one and its knobs are as they were sent, so the knobs the app
                    # found for it become its own. Knobs the player has turned since are left alone.
                    candidate["shape"] = dict(heard["shape"])
                    if place == on_b:
                        desk.set_shape(candidate["shape"], side="b", checkpoint=False)
            rounds.save(found)
            if not rounds.unchecked(found) and rounds.closest(found) is not None:
                best = rounds.closest(found)
                winner = found["candidates"][best]
                desk.set_status(f"Listened to all {len(found['candidates'])}. Closest to the aim: {best + 1} · "
                                f"{winner['name']} ({', '.join(winner['tags'][3:])}).", kind="reading")
        then, self.then = self.then, None
        desk.update()
        if then:
            desk.root.after_idle(then)
        elif error is None and not stopped:
            self.consider()
            self.rest()  # Another round may still be waiting.


class RoundStrip(tk.Frame):
    """Three markers for the designs of the round on show, and one line about the one on B."""

    def __init__(self, parent, suggestions, **place):
        super().__init__(parent, bg=PAPER)
        self.suggestions, self.placing, self.shown = suggestions, place, None
        row = tk.Frame(self, bg=PAPER)
        row.pack(fill="x")
        self.chips = tk.Frame(row, bg=PAPER)
        self.chips.pack(side="left")
        self.pager = tk.Frame(row, bg=PAPER)
        self.pager.pack(side="right")
        self.earlier = self.arrow("‹", -1, "The round before  ·  [")
        self.where = tk.Label(self.pager, bg=PAPER, fg=DIM, font=(FONT, 9))
        self.where.pack(side="left", padx=4)
        self.later = self.arrow("›", 1, "The round after  ·  ]")
        self.caption = tk.Label(self, bg=PAPER, fg=SUBTLE, font=(FONT, 9), justify="left", anchor="w")
        self.caption.pack(fill="x", pady=(5, 0))
        self.bind("<Configure>", lambda event: self.caption.configure(wraplength=max(200, event.width - 8)))
        for widget in (self, row, self.chips, self.caption, self.where):
            widget.bind("<MouseWheel>", self.wheel)

    def arrow(self, glyph, delta, tip):
        label = tk.Label(self.pager, text=glyph, bg=PAPER, fg=SUBTLE, font=(FONT, 12), cursor="hand2", padx=4)
        label.pack(side="left")
        label.bind("<ButtonRelease-1>", lambda _: self.suggestions.browse(delta))
        theme.Tooltip(label, tip)
        return label

    def wheel(self, event):
        self.suggestions.browse(-1 if event.delta > 0 else 1)
        return "break"

    def menu(self, event, place):
        suggestions, found = self.suggestions, self.suggestions.found()
        candidate = found["candidates"][place]
        options = tk.Menu(self, tearoff=False, bg=TILE, fg=INK, activebackground=theme.RULE, activeforeground=INK)
        options.add_command(label="Say what you think…", command=lambda: suggestions.review(place))
        options.add_separator()
        for word in MARKS:
            options.add_command(label=("✓ " if word in candidate["user_tags"] else "    ") + word,
                                command=lambda word=word: suggestions.mark(place, word))
        options.add_separator()
        options.add_command(label="Three more, from what you said", command=suggestions.more)
        options.tk_popup(event.x_root, event.y_root)

    @staticmethod
    def about(found, candidate):
        """One line on a design: how it differs, what the app heard of it, and what the player said."""
        parts = [candidate["differs"]] if candidate["differs"] else []
        if candidate["served"] is not None:
            # What was heard describes the knobs it was heard with. Once they move it is the past.
            moved = candidate.get("served_shape") is not None and candidate["served_shape"] != candidate["shape"]
            parts.append(("heard before the knobs moved: " if moved else "heard: ") + ", ".join(candidate["tags"]))
        elif candidate.get("failed"):
            parts.append("could not be listened to")
        else:
            parts.append("not listened to yet")
        yours = [*candidate["user_tags"], *([f"“{candidate['review']}”"] if candidate["review"] else [])]
        if yours:
            parts.append("you: " + ", ".join(yours))
        return "  ·  ".join(parts)

    def sync(self):
        suggestions = self.suggestions
        found, place = suggestions.found(), suggestions.place()
        key = (found and (found["id"], place, len(suggestions.rounds), suggestions.pending is not None,
                          [(c["name"], c["played"], c["served"] is not None, tuple(c["tags"]), tuple(c["user_tags"]),
                            c["review"], c.get("failed"), c.get("served_shape") == c["shape"])
                           for c in found["candidates"]]))
        if suggestions.desk.blind:
            found = None  # A blind comparison must not be given away by a name, a highlight or a mark.
            key = None
        if key == self.shown:
            return
        self.shown = key
        if not found:
            self.pack_forget()
            return
        for child in self.chips.winfo_children():
            child.destroy()
        best = rounds.closest(found)
        for index, candidate in enumerate(found["candidates"]):
            chosen = index == place
            mark = "  ◆" if index == best else ""  # The one that measured closest to the aim.
            chip = tk.Label(self.chips, text=f"{index + 1}  {candidate['name']}{mark}", cursor="hand2", padx=10, pady=5,
                            font=(FONT, 9, "bold" if chosen else "normal"),
                            bg=theme.mix(theme.TONE_B, DARK, .3) if chosen else TILE,
                            fg=INK if chosen or candidate["played"] else SOFT)
            chip.pack(side="left", padx=(0, 5))
            chip.bind("<ButtonRelease-1>", lambda _, index=index: suggestions.step(index))
            chip.bind("<Button-3>", lambda event, index=index: self.menu(event, index))
            chip.bind("<MouseWheel>", self.wheel)
            theme.Tooltip(chip, lambda found=found, candidate=candidate, index=index: (
                f"{self.about(found, candidate)}\n{preset_design.chain_text(rounds.draft_of(candidate))}\n"
                f"Put it on B  ·  {index + 1}     Right-click to review"), wrap=440)
        ids = [other["id"] for other in suggestions.rounds]
        at = ids.index(found["id"])
        self.where.configure(text=f"{at + 1} of {len(ids)}" + ("  ·  three more coming" if suggestions.pending else ""))
        self.earlier.configure(fg=SUBTLE if at else PAPER)
        self.later.configure(fg=SUBTLE if at < len(ids) - 1 else PAPER)
        if place is None:
            self.caption.configure(text=f"Aiming for {aim_words(found)}  ·  {found['request'].splitlines()[0]}")
        else:
            self.caption.configure(text=self.about(found, found["candidates"][place]))
        if not self.winfo_manager():
            self.pack(**self.placing)
