# Tone Lab for Quad Cortex

Tone Lab helps you make and improve presets for a Neural DSP Quad Cortex by playing and talking: two sounds side by side, your guitar or a recorded phrase through either, and one line to ask in. Designs come from an assistant that knows the unit's models and how players set them; the app builds them on the unit, listens to them silently, and puts them in front of you.

Start it with **Start Tone Lab (new).cmd**, `.\.venv\Scripts\python.exe -m lab`, or `Tone Lab.exe --new` (the earlier app still starts by default; `launcher.json` in the data folder with `{"desk": "new"}` makes the new one the default). `python -m lab --simulated` runs it with a simulated unit and no hardware. Opening it again brings the open window forward.

## The desk

- **A** and **B** are two sounds. Click one, or press **A** / **B**. **Guitar** (**G**) and **Phrase** (**P**) choose what feeds the selected one; **Space** stops. Switching between A and B moves only the input, so it takes under a second.
- **Ask** in the line at the bottom (**/**). A remark in players' words (“a bit tighter”, “muddy when I palm mute”, “fizzy”) moves the Shape knobs at once, with the remedy players usually try first that this chain can perform; say it again for the next remedy. A request for the sound on B (“more sustain from the fuzz, without more mud”) revises a copy of it and keeps everything the request does not change. “make …”, an artist or a song starts a round. The exact name of one of your presets brings it in. **Esc** cancels a request in flight.
- **Rounds.** A round is an aim (how driven, how bright, how much space) and three designs that reach it by different routes; the first goes on B and **1**, **2**, **3** step between them. Whenever the unit is free the app builds each design silently, plays your phrase through it and says what it heard. Once all three are heard, three more are prepared in the background from what was heard and what you marked, said and played; **N** or **›** brings them, **[** and **]** page between rounds.
- **Marks.** Right-click a sound, or press **K** (keeper), **D** (not this), **T** (any word). Marks are yours and go into the next round; the app's own tags (the aim, what it heard) are kept apart.
- **X** compares blind: the names are hidden and A and B may have been swapped; **X** again says which was which. **Copy B to A** is on the right-click menu. **Z** undoes. **0** hears the sound without its Shape knobs.
- **Scenes.** A sound whose scenes differ shows them under the tiles; the one chosen is heard on both sides.
- **Save to QC** writes the selected sound, with its knob positions, to the next free slot of the **Tone Lab** setlist.

Panels open beside the tiles: **Shape** (the knobs this chain can move), **Chain** (the blocks, each with a ▾ of its model's named settings), **Difference** (what listening heard differ between A and B), **Phrase** (drag the loop's edges), **Sounds** (your presets on the unit, with search; **Work on this** puts one on A as saved and a copy on B), **History**, **Song** (parts of one song, each a sound or one scene of it) and **Guitar** (what each pickup setting of your instrument puts out, from phrases recorded with the setting noted).

The **⋯** menu: **Guitar Learner**, **Guitar input calibration**, **Gestures on / off**, **Instrument** (Charvel, Les Paul, bass, acoustic, classical; the designer has its own guidance for bass, acoustic and classical), the side tools (**Library**, **Levels**, **Tone search**, **Tone field**, the **Comparison** and **Controls** workbenches, **Setup and sound check**, **Usage and feedback** with the assistant's calls by day), and **Measure the USB round trip**.

## What the app does on the unit

The app holds the unit for the whole session in a process of its own and gives your preset back when it closes, or when it next starts after a crash. Sounds are built once and kept in the **Tone Lab** setlist, slots 222 to 253, named `TL …`; after that they are recalled in half a second. A and B sit on one preset (rows 1–2 and 3–4) when they fit; a larger preset is compared by recalling it. Nothing is connected until its level is set, and nothing plays louder than 6 dB above unity. Saves go to the lowest free slot of the **Tone Lab** setlist below the cache. My Presets 255, “Tone Lab Preview”, is the build slot.

Only models found to work on this unit are offered: plugins you do not own (Gojira, Soldano, Petrucci, Rabea, Misha, Tim Henson and others) are refused or pass the guitar through untouched, and are left out. The processing each model needs, and the unit's named settings, were read from this unit (CorOS 4.1.1); after a firmware update, `python -m lab budget` and `python -m lab named-settings` read them again.

## Your data

Sounds, rounds, songs, phrases, the assistant's ledger and the unit's directory live in `%LOCALAPPDATA%\Tone Lab` (`TONE_LAB_HOME` overrides it). The first start checks what the app needs and brings in the earlier app's history; `python -m lab migrate` does that again and adds only what is new. The Guitar Learner keeps its labels and takes in `tone_sessions`.

## The assistant

Designs and revisions are made by the Codex CLI (`codex login` once). Every call is written to `ledger.jsonl` with its kind, who asked (you, or the app preparing three more), its length and outcome, and the app stops making calls of its own after 80 a day. A round takes about a minute; listening to its three designs about another minute.

## Command line

```powershell
$lab = '.\.venv\Scripts\python.exe'
& $lab -m lab doctor            # what the app needs, and whether it is there
& $lab -m lab migrate --dry-run # what would be brought in from the earlier app
& $lab -m lab directory         # the presets on the unit
& $lab -m lab pod "$env:USERPROFILE\Documents\Line 6\POD Go\Backups\<backup>.pgb"
& $lab -m lab history .\StreamingHistory_music_0.json
```

`python -m lab --help` lists the rest.

## Installation

Windows, Python 3.13, the Quad Cortex's ASIO driver (installed with Cortex Control), and Git.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Close Cortex Control and any audio program holding the driver before starting. The earlier app (`tone_lab/`, `python -m tone_lab`) is still here while the new one is tried; see [the rewrite review](REWRITE_REVIEW.md) for why it was rewritten and how, and [what Tone Lab knows about tone](TONE_KNOWLEDGE.md).
