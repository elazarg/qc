# Tone Lab for Quad Cortex

Tone Lab helps you make and improve presets for a Neural DSP Quad Cortex by playing and talking: two sounds side by side, your guitar or a recorded phrase through either, and one line to ask in. Designs come from an assistant that knows the unit's models and how players set them; the app builds them on the unit, listens to them silently, and puts them in front of you.

Start it with **Tone Lab.exe**, **Start Tone Lab.cmd**, or `.\.venv\Scripts\python.exe -m lab`. The earlier app is still here: `Tone Lab.exe --old`, or `{"desk": "old"}` in `launcher.json` in the data folder to make it the default again. `python -m lab --simulated` runs it with a simulated unit and no hardware. Opening it again brings the open window forward.

## The desk

Everything happens in the one window: no other windows, no dialogs, no pop-up menus, no keyboard shortcuts. (One exception: if the app cannot start, a message box says why, because there is no window yet to say it in.)

- **A** and **B** are two sounds; click one to hear it. **Guitar** and **Phrase** choose what feeds the selected one; press it again to stop. Switching between A and B moves only the input. **Copy B to A** and **Compare blind** sit beside them: blind hides the names and may swap A and B; **Show which was which** tells. **Undo** appears beside them once there is something to take back.
- **Ask** in the line at the bottom. A remark in players' words (“a bit tighter”, “muddy when I palm mute”, “fizzy”) moves the Shape knobs at once, with the remedy players usually try first that this chain can perform; say it again for the next remedy, and when the last one has been tried the line says so and nothing is sent to the assistant for it. A request that reads as a change to the sound on B (“more sustain from the fuzz, without more mud”, “make it heavier”) revises a copy of it and keeps everything the request does not change. Anything else (a sound, an artist, a song, “make …”) starts a round. The exact name of one of your presets brings it in. **Cancel** beside the working line drops a request in flight.
- **Songs** need no naming: when a request is for a song (“Comfortably Numb, the second solo”), the assistant says which song and part, the round shows it, and a sound you keep or save from it becomes that part of the song. History lists the songs and their parts.
- **Rounds.** A round is an aim (how distorted, how bright, how much tail) and three designs that reach it by different routes; the first goes on B and the chips step between them. Once all three designs of a round you asked for have been listened to, three more are prepared in the background from what was heard and what you marked, said and played; **› next three** brings them, **‹** goes back.
- **Listening.** While nothing plays and the unit has been left alone for two seconds, the app listens, silently, to what is on show and has not been heard as it is set now: the sounds on A and B first, then each design of the round. Pressing Guitar or Phrase stops a listen at once. A sound that could not be listened to says so on its tile, and is not tried again until it, the phrase or the scene changes.
- **Keep** and **Not this** on each tile are your marks; they go into the next round and teach what you prefer. The app's own tags (the aim, what it heard) are kept apart.
- **Scenes.** A sound whose scenes differ shows them under the tiles; the one chosen is heard on both sides.
- **Save to QC** asks for the name in place and writes the selected sound, with its knob positions, to the next free slot of the **Tone Lab** setlist.
- Reopening the app comes back to where it was left: A and B, the round, the scene, the phrase and its loop, and the open panel. A session that cannot be put back is set aside as `session.set-aside.json` in the data folder, and the desk opens empty and says so.

Panels open beside the tiles:

- **Shape**: the knobs this chain can move, and hearing it without the knobs. After a turn the app listens and says whether the knob did what it is for.
- **Chain**: the blocks, each with its model's named settings, and the Nano Cortex sheet.
- **Difference**: what listening heard differ between A and B; **Measure A and B now** plays the phrase silently through both and shows the difference in each octave band.
- **Phrase**: the phrases recorded, recording a new one with its pickup setting and, to compare settings, what was played, and the loop's edges to drag.
- **Sounds**: your presets on the unit, with a filter by words or by slot as the unit shows it; **Work on this** puts one on A as saved and a copy on B; **More with this preset** keeps it as a reference for Tone search, in the scene chosen, or leaves it selected on the unit. And your IRs and Neural Captures: an IR goes into B's cab, a capture in place of B's amp, or amp and cab. The designer also uses an IR of yours when a request names it.
- **History**: songs, what your choices say, and every sound, newest first.
- **Guitar**: the **Guitar Learner** (for an instrument it has a layout for) and **Teach a control**, which open in the panel's place with **‹ Back** to return; **Gestures on**, which listens for 20 s, and 20 s more after each gesture, and shows the seconds left; what each pickup setting of your instrument puts out, compared only on takes marked as the same gesture; and recording.
- **More**: the side tools, each shown in the panel's place with **‹ Back**: **Levels**, **Tone search**, **Tone field**, **Comparison workbench**, **Versions and the unit**, **Setup and sound check**, **Usage and feedback**, **POD Go presets**, **Songs you listen to**; the instrument; measuring the USB round trip.

A file (a WAV, a POD Go backup, a Spotify export) is chosen in place: from the list of those found where such files are kept (the data folder's `imports`, Downloads, Music, POD Go Edit's Backups), or by pasting its path and pressing Enter; a path that cannot be used says why.

While the Guitar Learner records or listens it has the audio input: Guitar and Phrase say so instead of starting. Beside gesture listening the guitar still plays, and Phrase stops the listening first. While the Levels tool's offset or mute is on, a line under A and B says so, with **Back to the desk's own level**.

A fault is said on the line and written with its trace to `errors.log` in the data folder; stopping, cancelling and a unit busy with your own edits are said and not logged.

## What the app does on the unit

The app holds the unit for the whole session in a process of its own and gives your preset back when it closes, or when it next starts after a crash. Sounds are built once and kept in the **Tone Lab** setlist, slots 222 to 253, named `TL …`; after that they are recalled in half a second. A and B sit on one preset (rows 1–2 and 3–4) when they fit; a larger preset is compared by recalling it. Nothing is connected until its level is set, and nothing plays louder than 6 dB above unity. Saves go to the lowest free slot of the **Tone Lab** setlist below the cache. My Presets 255, “Tone Lab Preview”, is the build slot.

The app never recalls over a preset with unsaved edits: it waits, and says so, until you save or discard them on the unit. A preset you choose by hand on the unit is left alone: the app listens to nothing and sends no sound until you press Guitar or Phrase. Before every write it asks the unit which preset is selected; `python -m lab time-switch` times what that costs when switching between A and B.

Only models found to work on this unit are offered: plugins you do not own (Gojira, Soldano, Petrucci, Rabea, Misha, Tim Henson and others) are refused or pass the guitar through untouched, and are left out. The processing each model needs, and the unit's named settings, were read from this unit (CorOS 4.1.1); after a firmware update, `python -m lab budget` and `python -m lab named-settings` read them again.

## Your data

Sounds, rounds, songs, phrases, the Guitar Learner's recordings and labels (`learner`), the assistant's ledger, the unit's directory and its named settings (`device-presets.json`), and the start-up log live in `%LOCALAPPDATA%\Tone Lab` (`TONE_LAB_HOME` overrides it). The first start checks what the app needs, once (`python -m lab doctor` repeats the checks), and brings in the earlier app's history; `python -m lab migrate` does that again and adds only what is new. What the earlier app kept in `tone_sessions` in the program folder (the Guitar Learner's data, the named settings) is copied to the data folder the first time it is needed; the originals are left as they were.

## The assistant

Designs and revisions are made by the Codex CLI (`codex login` once). Every call is written to `ledger.jsonl` with its kind, who asked (you, or the app preparing three more), its length and outcome, and no more than 80 calls are made in a day by your clock, yours and the app's together. A round takes about a minute; listening to its three designs about another minute and a quarter.

## Command line

```powershell
$lab = '.\.venv\Scripts\python.exe'
& $lab -m lab doctor            # what the app needs, and whether it is there
& $lab -m lab migrate --dry-run # what would be brought in from the earlier app
& $lab -m lab directory         # the presets on the unit
& $lab -m lab time-switch       # how long switching between A and B takes on the unit
& $lab -m lab pod "$env:USERPROFILE\Documents\Line 6\POD Go\Backups\<backup>.pgb"
& $lab -m lab history .\StreamingHistory_music_0.json   # songs by how often you chose them
& $lab -m lab shortcut          # build Tone Lab.exe and the Windows shortcut
```

`python -m lab --help` lists the rest. Every command that uses the unit takes `--simulated`.

## Installation

Windows, Python 3.13, the Quad Cortex's ASIO driver (installed with Cortex Control), Git, and the Codex CLI for the assistant.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m lab shortcut
```

The tests use a simulated unit and a temporary data folder; they cannot reach the unit, the audio driver, the assistant or your data. Close Cortex Control and any audio program holding the driver before starting the app. The earlier app (`tone_lab/`, `python -m tone_lab`) is still here, reachable with `--old`. See [what Tone Lab knows about tone](docs/TONE_KNOWLEDGE.md), [what tone research says](docs/TONE_RESEARCH.md), [the Guitar Learner's design](docs/GUITAR_LEARNER_DESIGN.md), and the reviews of the code ([the desk and the unit layer](docs/CODEBASE_REVIEW.md), [everything else](docs/WHOLE_CODEBASE_REVIEW.md)). [AGENTS.md](AGENTS.md) holds the rules for working on it.

## Personal data

This repository is public and holds only generic code and knowledge. Everything about a particular player stays on their computer: recordings, sounds, rounds, the assistant's ledger, the guitar profile, the taste model and the listening-history analysis live in `%LOCALAPPDATA%\Tone Lab` (its `personal` folder for analyses), and documents about one player's unit, usage or gear go in `personal/` at the top of the project, which git ignores. Account exports (`my_spotify_data*.zip`, streaming-history files) are ignored too. Tests use made-up data.

What leaves the computer is what goes to the assistant (your own Codex sign-in) with a request: the request's words, the instrument and pickup setting, a few numbers from the guitar profile (output level and tone of each pickup setting), the names of the IRs a request names or the sound being revised holds, the sound being revised or the earlier designs of a round with your marks and remarks on them, and, with requests you make for a new round, the ten guitar-led artists you listen to most, with their guitarists and tone families (from the listening-history analysis, once it has been made; no hours, songs or dates). Background rounds the app prepares by itself do not include the listening history. Separately, **Ask about the artists** in the Songs tool sends the names of your 30 most-listened artists and the artist and title of your 40 most-listened songs, once each time you press it, to learn which make guitar-led music; nothing of the listening history is sent until you press it. Recordings, measurements, the raw account exports and the taste model are never sent.
