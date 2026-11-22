# Tone Lab for Quad Cortex

Tone Lab helps you make and improve presets for a Neural DSP Quad Cortex by playing and talking: two sounds side by side, your guitar or a recorded phrase through either, and one line to ask in. Designs come from an assistant that knows the unit's models and how players set them; the app builds them on the unit, listens to them silently, and puts them in front of you.

Start it with **Tone Lab.exe**, **Start Tone Lab.cmd**, or `.\.venv\Scripts\python.exe -m lab`. `python -m lab --simulated` runs it with a simulated unit and no hardware. Opening it again brings the open window forward.

## The desk

Everything happens in the one window: no other windows, no dialogs, no keyboard shortcuts. A list that drops down and the words of a **?** are drawn inside the window. (One exception: if the app cannot start, a message box says why, because there is no window yet to say it in.)

**The top row** is there on every screen. At its left, the instrument you are playing: a tile with its picture and name. Click it and choose another from the list beneath; the whole app follows at once (the Guitar and Phrase panels, what the designer is told, gestures, and the phrase, when one was recorded on that instrument). Left of it, the learning symbol.

**Learning the guitar** is a screen of its own: the learning symbol puts it in the place of everything below the top row, and brings the desk back as it was when pressed again. It has two views. The **Guitar Learner** (for a guitar it has a photograph of: the Charvel) records or opens a dry clip, shows what it makes of it, and tags it with the controls set on the photograph. **Teach a control** (any instrument) teaches two settings of one control, checks a fresh take, and maps a return gesture to a Shape knob.

The desk shows what is done with a thing, not a paragraph about it: what is worth knowing once is behind a **?** beside it, shown while the pointer rests there, or after a click until the next.

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
- **Sounds** lets you find presets by name or slot. **Work on this** puts one on A and an editable copy on B. **More with this preset** keeps it as a Tone search reference in the chosen scene or leaves it selected on the unit. Auditioning and saving copies preserve splitter settings, scene assignments, expression ranges and stomp assignments. Sounds with performance assignments use independent audition slots. **Put in B's cab** puts your IR in a stock cab's first mic slot and keeps its other mic and filters. Captures can replace B's amp alone or its amp and cab.
- **History**: songs, what your choices say, and every sound, newest first.
- **Guitar**: **Gestures on**, which listens for 20 s, and 20 s more after each gesture, and shows the seconds left; what each pickup setting of your instrument puts out, compared only on takes marked as the same gesture; and recording a phrase with its pickup setting.
- **More**: the side tools, each shown in the panel's place with **‹ Back**: **Levels**, **Tone search**, **Tone field**, **Comparison workbench**, **Versions and the unit**, **Setup and sound check**, **Usage and feedback**, **POD Go presets**, **Songs you listen to**; and measuring the USB round trip.

New tone requests have access to a compact catalog of eligible Quad Cortex blocks, local IRs and captures with known versions. The designer gets detailed starting points for familiar gear and can choose other entries or use rows 2 and 3. It can look up gear for requests where a source could change the sound, such as a specific song. You can ask it to research further in the same request box.

**Levels → Match a song’s parts** measures the chosen scenes of the song's parts and verifies the proposed trims with the same phrase before keeping matched copies. Add a target offset to a part name, such as `Solo +2 dB`, to keep an intentional level difference. **Setup and sound check** reports input headroom, a suggested input-gain cut if needed, USB output peaks and mono compatibility. The guitar and phrase audition controls still work before measurements are ready.

New designs show suggested pickup, wiring and guitar knob positions for the selected Charvel or Les Paul. A recorded phrase still uses the settings it was played on. For play-along volume, set the Windows default output to Quad Cortex Out 1/2, play a typical section at your usual level, and choose **Levels → Measure music**. The reference and listening offset apply across Tone Lab and survive a restart. **Fit selected** checks the active scene against that reference and prepares a copy for **Save to QC**. Re-measure after changing the backing track's app or Windows playback level. The Quad Cortex master knob sets listening volume when both sources use the same outputs. Only the music's loudness and output name are saved locally.

**Guitar Learner → Idle reference** keeps one muted-string recording per guitar configuration, including pickup, wiring and knob positions. Tone Lab can also use a stable gap in an exactly labeled dry take. **Setup and sound check → Noise while idle** reuses that dry audio through the selected preset and compares it with a silent input. It offers a preset copy with a gate when it measures a quieter idle and the soft replay still comes through. Re-record a reference if the input gain or the noise in the setup changes.

A file (a WAV, a POD Go backup, a Spotify export) is chosen in place: from the list of those found where such files are kept (the data folder's `imports`, Downloads, Music, POD Go Edit's Backups), or by pasting its path and pressing Enter; a path that cannot be used says why.

While the learning screen records or listens it has the audio input: Guitar and Phrase say so instead of starting. Beside gesture listening the guitar still plays, and Phrase stops the listening first. While the Levels tool's offset or mute is on, a line under A and B says so, with **Back to the desk's own level**.

A fault is said on the line and written with its trace to `errors.log` in the data folder; stopping, cancelling and a unit busy with your own edits are said and not logged.

## What the app does on the unit

The app holds the unit for the whole session in a process of its own and gives your preset back when it closes, or when it next starts after a crash. Sounds are built once and kept in the **Tone Lab** setlist, slots 222 to 253, named `TL …`; after that they are recalled in half a second. A and B sit on one preset (rows 1–2 and 3–4) when they fit; a larger preset is compared by recalling it. Nothing is connected until its level is set, and nothing plays louder than 6 dB above unity. Saves go to the lowest free slot of the **Tone Lab** setlist below the cache. My Presets 255, “Tone Lab Preview”, is the build slot.

The app never recalls over a preset with unsaved edits: it waits, and says so, until you save or discard them on the unit. A preset you choose by hand on the unit is left alone: the app listens to nothing and sends no sound until you press Guitar or Phrase. Before every write it asks the unit which preset is selected; `python -m lab time-switch` times what that costs when switching between A and B.

Only models found to work on this unit are offered: plugins you do not own (Gojira, Soldano, Petrucci, Rabea, Misha, Tim Henson and others) are refused or pass the guitar through untouched, and are left out. The processing each model needs, and the unit's named settings, were read from this unit (CorOS 4.1.1); after a firmware update, `python -m lab budget` and `python -m lab named-settings` read them again.

## Your data

Sounds, rounds, songs, phrases, the Guitar Learner's recordings and labels (`learner`), the assistant's ledger, the unit's directory and its named settings (`device-presets.json`), and the start-up log live in `%LOCALAPPDATA%\Tone Lab` (`TONE_LAB_HOME` overrides it). The first start checks what the app needs, once (`python -m lab doctor` repeats the checks), and brings in the history an earlier version of the app left in `tone_sessions` in the program folder; `python -m lab migrate` does that again and adds only what is new. The Guitar Learner's data and the named settings kept there are copied to the data folder the first time they are needed; the originals are left as they were.

## The assistant

Designs and revisions are made by the Codex CLI (`codex login` once). Every call is written to `ledger.jsonl` with its kind, who asked (you, or the app preparing three more), its length and outcome, and no more than 80 calls are made in a day by your clock, yours and the app's together. A round takes about a minute; listening to its three designs about another minute and a quarter.

## Command line

```powershell
$lab = '.\.venv\Scripts\python.exe'
& $lab -m lab doctor            # what the app needs, and whether it is there
& $lab -m lab migrate --dry-run # what would be brought in from an earlier version's history
& $lab -m lab directory         # the presets on the unit
& $lab -m lab time-switch       # how long switching between A and B takes on the unit
& $lab -m lab pod "$env:USERPROFILE\Documents\Line 6\POD Go\Backups\<backup>.pgb"
& $lab -m lab history .\StreamingHistory_music_0.json   # songs by how often you chose them
& $lab -m lab shortcut          # build Tone Lab.exe and the Windows shortcut
```

`python -m lab --help` lists the rest. Every command that uses the unit takes `--simulated`.

`python -m lab qc-api --help` lists the API discovery commands. See [Quad Cortex API discovery](docs/QC_API_DISCOVERY.md) for hardware results, offline tools, and MCP setup.

## Installation

Windows, Python 3.13, the Quad Cortex's ASIO driver (installed with Cortex Control), Git, and the Codex CLI for the assistant.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m lab shortcut
```

The tests use a simulated unit and a temporary data folder; they cannot reach the unit, the audio driver, the assistant or your data. Close Cortex Control and any audio program holding the driver before starting the app. The earlier app, `tone_lab/`, was removed on 2026-11-22; the tag `with-tone_lab` is the last commit that has it. See [what Tone Lab knows about tone](docs/TONE_KNOWLEDGE.md), [what tone research says](docs/TONE_RESEARCH.md), [the Guitar Learner's design](docs/GUITAR_LEARNER_DESIGN.md), and the reviews of the code ([the desk and the unit layer](docs/CODEBASE_REVIEW.md), [everything else](docs/WHOLE_CODEBASE_REVIEW.md)). [AGENTS.md](AGENTS.md) holds the rules for working on it.

## Personal data

This repository is public and holds only generic code and knowledge. Everything about a particular player stays on their computer: recordings, sounds, rounds, the assistant's ledger, the guitar profile, the taste model and the listening-history analysis live in `%LOCALAPPDATA%\Tone Lab` (its `personal` folder for analyses), and documents about one player's unit, usage or gear go in `personal/` at the top of the project, which git ignores. Account exports (`my_spotify_data*.zip`, streaming-history files) are ignored too. Tests use made-up data.

Tone requests send the request text, instrument and pickup setting, guitar profile summaries, and the names of eligible local IRs and captures with temporary design IDs. Direct requests can include the measured peak of the current dry phrase. Revisions and follow-up rounds can include the chain, earlier candidate summaries, measurements, and your remarks. A new round you request can include the ten guitar-led artists from your listening analysis. Background rounds omit the listening history and phrase peak. **Ask about the artists** separately sends the artist and title summary shown in that tool. Recordings, capture file keys, raw account exports, and the taste model stay on the computer.
