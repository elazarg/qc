"""The comparison workbench: several versions of one sound, rendered with the phrase, heard blind.

Each version is a chain made from the selected sound, played silently through the unit with the desk's
phrase:

- **Scenes**: each scene the sound names (up to six).
- **Strengths**: a request such as "darker" or "more jazz" as Shape knob moves, at 40 % and 85 %, next to the
  sound as it is.
- **Sweep**: one control of one block at several values, in the units the unit shows.
- **WAV files**: two or more recordings from elsewhere, chosen in place from where recordings are kept or by
  their paths; no unit needed.

The versions are level-matched (one loudness, only cuts, peaks under −3 dBTP), shuffled under letters and
played on the computer. "Keep" notes the last one played with a remark; "Which was which?" reveals the
order; "Put the kept one on B" brings that version to the desk as a sound of its own. Each comparison is
kept under `comparisons/` in the data folder with its notes (the old `feedback` was tied to a comparison
the same way).

`comparison` and `auditions` are the one blind comparison of rendered audio in the tools: the sound check's
stage EQ versions go through them too. `with_control` is the one way a control of a block is set to a value
in display units and checked against its range; the command line's `sweep` uses it.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import random
import tkinter as tk
from uuid import uuid4

from lab.sound import knobs, words
from lab.hearing.audio import read_wave
from lab.hearing.measure import match, measure
from lab.sound.sounds import Sound
from lab.desk.theme import FONT, INK, SUBTLE, TILE
from lab.tools.common import (SCENE_LETTERS, Chips, FileChoice, Tool, button, caption, disclosure, flow, gained,
                              heard_chain, label, listbox, phrase, play, room, said, section, silence, sized,
                              usual_folders, with_knobs, write_pcm16)

STRENGTHS = (.4, .85)
KINDS = ("Scenes", "Strengths", "Sweep", "WAV files")
# What each kind compares, said behind the "?" beside the heading; and how the versions are made and heard.
KIND_NOTES = {
    "Scenes": "Each scene the selected sound names, up to six.",
    "Strengths": "A request in words, such as darker, more jazz or less delay, at 40 % and 85 % next to the sound as "
                 "it is.",
    "Sweep": "One control of the selected sound at each of the values typed: choose the control in the list, then "
             "type two values or more.",
    "WAV files": "Two or more recordings from elsewhere: click each in the list, or paste a path. No unit needed.",
}
HOW_RENDERED = "They are rendered silently with the desk's phrase, then level-matched and shuffled."


# ---- versions of a sound ----

def scene_versions(sound):
    chain = heard_chain(sound)
    count = min(chain.scenes or 1, 6)
    return [{"name": f"Scene {SCENE_LETTERS[scene]}" + (f" · {chain.labels[scene]}" if chain.labels[scene] else ""),
             "chain": chain, "scene": scene, "knobs": dict(sound.knobs)} for scene in range(count)]


def request_moves(text, chain):
    """A request as knob moves the chain can make, scaled so the largest is 1, or None."""
    found = None
    reading = words.read(text)
    if reading:
        ways = words.usable(reading, set(knobs.available(chain)))
        if ways:
            found = dict(ways[0].moves)
    if found is None:
        found = knobs.direction(text)
    if not found:
        return None
    found = {key: value for key, value in found.items() if key in knobs.available(chain)}
    largest = max((abs(value) for value in found.values()), default=0.)
    return {key: value / largest for key, value in found.items()} if largest else None


def strength_versions(sound, text, strengths=STRENGTHS):
    moves = request_moves(text, sound.chain)
    if moves is None:
        raise ValueError(f"The selected sound has nothing that answers “{text}”. Try darker, more jazz, "
                         "less delay, a bit tighter.")
    versions = [{"name": "As it is", "chain": heard_chain(sound), "scene": None, "knobs": dict(sound.knobs)}]
    for amount in strengths:
        values = dict(sound.knobs)
        for key, step in moves.items():
            low = knobs.KNOBS[key][1]
            values[key] = round(max(low, min(1., values.get(key, 0.) + step * amount)), 2)
        versions.append({"name": f"{text} · {amount:.0%}", "chain": with_knobs(sound.chain, values), "scene": None,
                         "knobs": values, "request": text, "amount": amount})
    return versions


def controls(chain):
    """[(label, row, column, index)] for the numeric controls of every block."""
    found = []
    for row, column, block in chain.blocks:
        info = block.info
        if not info:
            continue
        for param in info.params:
            if param.kind in ("float", "fader", "int") and param.low is not None and param.high is not None:
                found.append((f"{block.name} · {param.name}", row, column, param.index))
    return found


def with_control(chain, row, column, index, value):
    """The chain with one control of one block at `value`, in the units the unit shows (rows and columns from 0).
    Returns (chain, block, parameter); a block that is not there, a control without a range and a value out of
    range are refused with the reason."""
    block = chain.block(row, column)
    if block is None:
        raise ValueError(f"Row {row + 1}, column {column + 1} holds no block.")
    param = next((param for param in block.info.params if param.index == index), None) if block.info else None
    if param is None or param.kind not in ("float", "fader", "int") or param.low is None or param.high is None:
        raise ValueError(f"{block.name} has no control {index} with a range.")
    if not param.low <= value <= param.high:
        raise ValueError(f"{block.name} {param.name} goes from {param.low:g} to {param.high:g}.")
    return chain.with_block(row, column, block.set_display(index, value)), block, param


def sweep_versions(sound, row, column, index, values):
    chain = heard_chain(sound)
    versions = []
    for value in values:
        changed, block, param = with_control(chain, row, column, index, value)
        versions.append({"name": f"{block.name} {param.name} {value:g}{param.units and ' ' + param.units}",
                         "chain": changed, "scene": None, "knobs": dict(sound.knobs),
                         "change": {"row": row, "column": column, "index": index, "to": value}})
    return versions


# ---- a comparison ----

def matched(audios, *, ceiling_db=-3.):
    """Each audio at one loudness; returns (audios, trims, measurements)."""
    found = [measure(audio) for audio in audios]
    if any(item.lufs is None for item in found):
        raise ValueError("A version returned silence. Check that it passes sound.")
    if any(item.clipped for item in found):
        raise ValueError("A version reached full scale. Lower the level inside it first.")
    trims = match(found, ceiling_db=ceiling_db)
    return [gained(audio, trim) for audio, trim in zip(audios, trims)], trims, found


def comparison(folder, versions, audios, *, sound=None, phrase_name=None, kind="", seed=None, about=None):
    """Level-match the versions, shuffle them under letters, and keep it all under `comparisons/`.
    `about` is anything else to keep with the comparison (what it was made for, notes on judging it)."""
    if len(versions) < 2:
        raise ValueError("Compare at least two versions.")
    leveled, trims, found = matched(audios)
    order = list(range(len(versions)))
    random.Random(seed).shuffle(order)
    stamp = datetime.now(timezone.utc)
    target = Path(folder) / "comparisons" / f"{stamp:%Y%m%d-%H%M%S}-{kind.lower().replace(' ', '-')}-{uuid4().hex[:6]}"
    target.mkdir(parents=True)
    record = {"created": stamp.isoformat(timespec="seconds"), "kind": kind, "phrase": phrase_name,
              "sound": {"id": sound.id, "name": sound.name} if sound else None, "auditions": {}, "choice": None,
              **({"about": about} if about else {})}
    for place, index in enumerate(order):
        letter = SCENE_LETTERS[place]
        version = versions[index]
        write_pcm16(target / f"audition-{letter}.wav", leveled[index])
        record["auditions"][letter] = {
            "name": version["name"], "file": f"audition-{letter}.wav", "trim_db": round(trims[index], 2),
            "lufs": found[index].lufs, **{key: version[key] for key in ("scene", "knobs", "request", "amount",
                                                                        "change", "source", "post_eq",
                                                                        "post_eq_trim_db") if key in version}}
        if "chain" in version:
            record["auditions"][letter]["chain"] = version["chain"].to_record()
    (target / "comparison.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return target, record


def load(target):
    return json.loads((Path(target) / "comparison.json").read_text(encoding="utf-8"))


def auditions(target, record):
    """{letter: the level-matched audio} of a comparison, read from where it is kept."""
    return {letter: read_wave(Path(target) / row["file"]) for letter, row in record["auditions"].items()}


def earlier(folder, limit=30):
    """[(folder, record)] of the comparisons kept, newest first. One that cannot be read is left out."""
    found = []
    for path in sorted((Path(folder) / "comparisons").glob("*/comparison.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("auditions"), dict):
            found.append((path.parent, record))
        if len(found) >= limit:
            break
    return found


def keep(target, letter, notes=""):
    """Note the preferred letter and a remark in the comparison."""
    record = load(target)
    if letter not in record["auditions"]:
        raise ValueError("Play a version before keeping it.")
    record["choice"] = {"letter": letter, "name": record["auditions"][letter]["name"], "notes": notes.strip(),
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (Path(target) / "comparison.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


def feedback(target, text, *, audition=None):
    """A remark about the comparison, kept with it under `feedback/`, without changing anything else."""
    text = (text or "").strip()
    record = load(target)
    if not text or not record.get("auditions"):
        raise ValueError("Write a note about a completed comparison.")
    stamp = datetime.now(timezone.utc)
    directory = Path(target) / "feedback"
    directory.mkdir(exist_ok=True)
    path = directory / f"{stamp:%Y%m%d-%H%M%S}-{uuid4().hex[:6]}.json"
    heard = record["auditions"].get(audition, {})
    path.write_text(json.dumps({"at": stamp.isoformat(timespec="seconds"), "text": text, "audition": audition,
                                "audition_name": heard.get("name"),
                                "auditions": {letter: row["name"] for letter, row in record["auditions"].items()}},
                               indent=1), encoding="utf-8")
    return path


def as_sound(record, letter, parent):
    """A kept version as a sound of its own, revised from the one it was made from."""
    from lab.sound.chain import Chain

    audition = record["auditions"][letter]
    if "chain" not in audition:
        raise ValueError("A recording from elsewhere has no chain to bring to the desk.")
    chain = Chain.from_record(audition["chain"])
    origin = {"kind": "revised", "from": parent.id, "request": audition["name"]}
    # The chain was rendered with the knobs written in; the copy starts with them at rest.
    sound = Sound(f"{parent.name} · {audition['name']}"[:60], chain, origin, parent=parent.id,
                  explanation=parent.explanation)
    if audition.get("scene") is not None:
        sound.tags = [f"scene {SCENE_LETTERS[audition['scene']]}"]
    return sound


# ---- the tool ----

class WorkbenchTool(Tool):
    TITLE = "Comparison workbench"

    def build(self):
        self.target = self.record = self.last = None
        self.auditions = {}
        self.control_rows = []          # the controls listed for a sweep: (label, row, column, index)
        make = section(self.body, "Versions of the selected sound", "Versions.")
        self.help = make.hint           # what the kind chosen compares, behind the "?": `kind_changed` says it
        self.kind = tk.StringVar(master=self.frame, value=KINDS[0])
        Chips(make, self.kind, KINDS, command=self.kind_changed).pack(fill="x", pady=(0, 4))
        # What a kind needs is shown for that kind alone: the words or values, the control, the recordings.
        self.typed = tk.Frame(make, bg=make["bg"])
        self.asked = label(self.typed, size=9, color=SUBTLE)        # the name of what is typed
        self.asked.pack(fill="x")
        self.argument = tk.Entry(self.typed, font=(FONT, 10), relief="flat", bg=TILE, fg=INK, insertbackground=INK)
        self.argument.pack(fill="x", ipady=4)
        self.argument.bind("<Return>", lambda _: self.render())
        self.control_list = listbox(make, height=5)
        self.files = FileChoice(make, folders=lambda: usual_folders("wav", self.folder), patterns=("*.wav",),
                                choose=self.imported, several=True, action="Compare these")
        self.render_button = button(make, "Render", self.render)

        # The audition is shown once there is a comparison to hear.
        self.hear = section(self.body, "Blind audition",
                            "The versions at one loudness, in an order that is not the one they were made in. Play "
                            "them, keep the one you prefer, then ask which was which.")
        self.players = tk.Frame(self.hear, bg=self.hear["bg"])
        self.players.pack(fill="x")
        self.about = said(self.hear, pady=(6, 0))
        caption(self.hear, "Remark", "Kept with the version last played (Keep last played), or with the comparison "
                                     "as a whole (Note on this comparison).").pack(fill="x", pady=(6, 0))
        self.remark = tk.Entry(self.hear, font=(FONT, 10), relief="flat", bg=TILE, fg=INK, insertbackground=INK)
        self.remark.pack(fill="x", ipady=4)
        row = tk.Frame(self.hear, bg=self.hear["bg"])
        row.pack(fill="x", pady=(6, 0))
        button(row, "Keep last played", self.keep).pack(side="left")
        button(row, "Put the kept one on B", self.put_kept, quiet=True).pack(side="left", padx=4)
        row = tk.Frame(self.hear, bg=self.hear["bg"])
        row.pack(fill="x", pady=(4, 0))
        button(row, "Which was which?", self.reveal, quiet=True).pack(side="left")
        button(row, "Note on this comparison", self.note, quiet=True).pack(side="left", padx=4)
        self.hear.pack_forget()

        earlier_ones = disclosure(self.body, "Earlier comparisons")
        self.none_earlier = said(earlier_ones, color=SUBTLE, pady=0)
        self.earlier_part = room(earlier_ones)
        self.earlier = listbox(self.earlier_part, height=5)
        self.earlier.pack(fill="x")
        button(self.earlier_part, "Open", self.open_earlier, quiet=True).pack(anchor="w", pady=(4, 0))
        self.earlier.bind("<Double-Button-1>", lambda _: self.open_earlier())     # the same as Open
        self.kind_changed()
        self.list_earlier()

    def kind_changed(self):
        kind = self.kind.get()
        for part in (self.typed, self.control_list, self.files, self.render_button):
            part.pack_forget()
        self.argument.delete(0, "end")
        self.help.say(f"{KIND_NOTES[kind]} {HOW_RENDERED if kind != 'WAV files' else ''}".strip())
        if kind == "Strengths":
            self.asked.configure(text="A request, such as darker")
            self.typed.pack(fill="x")
        elif kind == "Sweep":
            self.asked.configure(text="Values of the control chosen, such as 3 5 7")
            self.list_controls()
            self.control_list.pack(fill="x", pady=(0, 4))
            self.typed.pack(fill="x")
        elif kind == "WAV files":
            self.files.look()
            self.files.pack(fill="x")
            return
        self.render_button.pack(anchor="w", pady=(6, 0))

    def shown(self):
        super().shown()
        if self.kind.get() == "Sweep":
            self.list_controls()        # of the sound selected now

    def list_controls(self):
        sound = self.studio.sound(self.studio.state.side)
        self.control_rows = controls(sound.chain) if sound else []
        self.control_list.delete(0, "end")
        for name, *_ in self.control_rows:
            self.control_list.insert("end", name)
        sized(self.control_list, 5)

    def versions(self, sound):
        kind, text = self.kind.get(), self.argument.get().strip()
        if kind == "Scenes":
            return scene_versions(sound)
        if kind == "Strengths":
            if not text:
                raise ValueError("Type a request first, such as darker.")
            return strength_versions(sound, text)
        picked = self.control_list.curselection()
        if not picked:
            raise ValueError("Choose a control in the list first.")
        _, row, column, index = self.control_rows[picked[0]]
        try:
            values = [float(value) for value in text.replace(",", " ").split()]
        except ValueError:
            raise ValueError("Type the values as numbers, such as 3 5 7.") from None
        if len(values) < 2:
            raise ValueError("Type at least two values.")
        return sweep_versions(sound, int(row), int(column), int(index), values)

    def render(self):
        sound, taken = self.studio.sound(self.studio.state.side), phrase(self.studio)
        if sound is None or taken is None:
            self.say("Put a sound on the desk and choose a phrase first.")
            return
        try:
            versions = self.versions(sound)
        except ValueError as error:
            self.say(str(error))
            return
        if len(versions) < 2:
            self.say("The sound names only one scene. Choose another kind of comparison.")
            return

        def rendered(audios):
            self.made(versions, audios, sound, taken[0])

        self.call("lab.tools.common.render_many", [(version["chain"], version["scene"]) for version in versions],
                  taken[1], done=rendered, working=f"Rendering {len(versions)} versions silently on the Quad Cortex…",
                  renders=True)

    def imported(self, paths):
        """Compare recordings from elsewhere: the files chosen in the list, or the one whose path was typed added
        to them."""
        chosen = [self.files.found[index] for index in self.files.list.curselection()]
        paths = list(dict.fromkeys([*chosen, *paths]))
        if len(paths) < 2:
            self.say("Choose at least two recordings: click each in the list, then Compare these.")
            return
        try:
            audios = [read_wave(path) for path in paths]
        except (OSError, ValueError) as error:
            self.say(str(error))
            return
        self.made([{"name": Path(path).stem, "source": str(path)} for path in paths], audios, None, None)

    def made(self, versions, audios, sound, phrase_name):
        try:
            self.target, self.record = comparison(self.folder, versions, audios, sound=sound, phrase_name=phrase_name,
                                                  kind=self.kind.get())
        except ValueError as error:
            self.say(str(error))
            return
        self.show()
        self.list_earlier()

    def show(self):
        self.auditions = auditions(self.target, self.record)
        self.last = None
        for child in self.players.winfo_children():
            child.destroy()
        flow(self.players, [button(self.players, f"Play {letter}", lambda letter=letter: self.play(letter))
                            for letter in self.auditions] + [button(self.players, "Stop", self.silence, quiet=True)])
        if not self.hear.winfo_manager():
            self.hear.pack(fill="x", pady=(0, 10), after=self.body.winfo_children()[0])
        chosen = self.record.get("choice")
        self.about.configure(text=f"{len(self.auditions)} level-matched versions"
                                  + (f" of {self.record['sound']['name']}" if self.record.get("sound") else "")
                                  + (f" · kept {chosen['letter']}" if chosen else ""))

    def play(self, letter):
        try:
            play(self.auditions[letter], self.folder / "previews")
        except (RuntimeError, ImportError) as error:
            self.say(str(error))
            return
        self.last = letter
        self.say(f"Playing {letter}")

    def silence(self):
        silence()

    def keep(self):
        if not self.target or not self.last:
            self.say("Play a version before keeping it.")
            return
        self.record = keep(self.target, self.last, self.remark.get())
        self.remark.delete(0, "end")
        self.say(f"Kept {self.last}.")
        self.show()

    def note(self):
        if not self.target:
            return
        try:
            feedback(self.target, self.remark.get(), audition=self.last)
        except ValueError as error:
            self.say(str(error))
            return
        self.remark.delete(0, "end")
        self.say("The note is kept with this comparison.")

    def reveal(self):
        if self.record:
            self.about.configure(text="  ·  ".join(f"{letter}: {row['name']}"
                                                   for letter, row in self.record["auditions"].items()))

    def put_kept(self):
        if not self.record or not self.record.get("choice"):
            self.say("Keep one first.")
            return
        sound = self.record.get("sound")
        if not sound:
            self.say("A recording from elsewhere has no chain to bring to the desk.")
            return
        made = as_sound(self.record, self.record["choice"]["letter"], self.studio.store.sound(sound["id"]))
        self.studio.put("b", made)

    def list_earlier(self):
        self.found = earlier(self.folder)       # the rows of the list, one for one
        self.earlier_part.show(bool(self.found))
        self.none_earlier.configure(text="" if self.found else "None yet.")
        self.earlier.delete(0, "end")
        for _, record in self.found:
            chosen = record.get("choice")
            self.earlier.insert("end", f"{str(record.get('created', ''))[:16].replace('T', ' ')}  "
                                       f"{record.get('kind', '')}"
                                       + (f" · {record['sound']['name']}" if record.get("sound") else "")
                                       + (f" · kept {chosen['name']}" if chosen else ""))
        sized(self.earlier, 5)

    def open_earlier(self):
        chosen = self.earlier.curselection()
        if chosen:
            self.target, self.record = self.found[chosen[0]]
            self.show()


def open(desk):  # noqa: A001
    return WorkbenchTool.open(desk)
