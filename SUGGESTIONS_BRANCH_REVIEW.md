# Tone Lab suggestions branch review

Reviewed `suggestions` at `5a7df28` against `main` at `3007bc6` on November 19, 2026. The new desk can present three designs, retain each design’s knob settings, and offer another round from the results. The branch should stay out of `main` until playback can be stopped reliably, automatic auditions respect control of the Quad Cortex, and every score in a round comes from the same phrase.

## Fix before merging

1. **Stop can be followed by Guitar starting anyway.** Ask for Guitar while an automatic audition holds the unit, then press Space or Esc while it says it is giving the unit back. I reproduced the queued start still running after Stop. Stop should clear any queued action that would start playback when the audition finishes. [Stop action](tone_lab/listening_room.py#L1214) · [Queued action](tone_lab/suggestions.py#L259)
2. **An audition can continue after the preset changes.** A simulated preset switch after the first playback sent the quieter second pass into the new preset’s USB-to-analog route. The code still accepted the result for ranking. Check the working slot, scene, routing, and cancellation before each pass and before accepting the return. Pass cancellation into the playback loop so the device can be released promptly. [Audition passes](tone_lab/hearing.py#L93) · [Draft audition](tone_lab/rounds.py#L101)
3. **The desk can take over a Quad Cortex that the player is using directly.** The timer checks whether Tone Lab is idle, then recalls its Preview preset. It also does this if the job was already cancelled before it started. Require an explicit audition or verified idle state on the unit, and check cancellation before acquiring it. Run the driver in a recoverable worker, as the live path does. The audition currently runs in the desktop process and has no preset recovery marker for a native ASIO crash. [Automatic audition](tone_lab/suggestions.py#L229) · [Device ownership](tone_lab/rounds.py#L101)
4. **Ranks can mix different performances and guitars.** Auditions choose the newest dry file that is long enough. They ignore the phrase selected on the desk. In an interrupted round, I verified that the first design could be measured on one clip and the other two on a newer clip. Hold one take, passage, and guitar fixed for the round and record them with the scores. [Round and take selection](tone_lab/rounds.py#L89) · [Dry audio selection](tone_lab/hearing.py#L107)

## Other findings

- **A deferred choice can load a design from the wrong round.** I selected design 2 while the app was releasing the unit, then browsed to another round. When the unit was ready, the app loaded design 2 from the round I had browsed to. Queue the selected draft ID with the action. [Deferred selection](tone_lab/suggestions.py#L102) · [Round browsing](tone_lab/suggestions.py#L120)
- **Scores can describe an earlier sound.** Turning a knob after ranking changes the stored shape while the score and tags still describe the previous one. A background result can also replace a newer manual setting on a design the player has yet to hear. Record the measured shape separately, show stale scores as such, and apply automatic changes only while the player’s shape still matches the snapshot. [Shape bookkeeping](tone_lab/suggestions.py#L137) · [Applying audition results](tone_lab/suggestions.py#L266)
- **Round writes can temporarily erase saved feedback.** When I tagged a design during an audition, the worker saved its older copy of the round and removed the tag from disk until the desk merged the result. A crash at that point can lose the tag. Give the desk a single writer for player state and merge completed measurements by draft ID. [Worker saves](tone_lab/rounds.py#L172) · [Tag saves](tone_lab/suggestions.py#L148)
- **Blind comparison reveals the selected design.** I opened Blind in the actual Tk window and all three names, the highlight, and the ranking marker remained visible. The strip still accepts changes to B. Hide it while Blind is active and block its shortcuts during the comparison. [Desk refresh](tone_lab/listening_room.py#L1019) · [Round controls](tone_lab/suggestions.py#L361)
- **New rounds lose the selected reference and can fail with an owned IR.** The desk gathers the reference and captured measurements but only passes them down the single-draft refinement path. Separately, starting the next round from a valid draft with a user IR fails because its loader ID is looked up in the stock-model table. [Reference context](tone_lab/listening_room.py#L1711) · [Next-round summary](tone_lab/rounds.py#L219)
- **Some tags overstate what the audio proves.** A clean signal that was dynamically compressed registered as saturated. Folding stereo to mono made opposite-phase audio rank dark or fail, and the tail window called a clearly audible 80 ms echo dry. Treat compression as evidence about dynamics, measure spectral energy per channel, and measure short and long decay from the end of the guitar signal. [Audio analysis](tone_lab/hearing.py#L67)
- **A queued follow-up can miss feedback the desk promises to use.** After a round with a miss queues the next three, a new remark is saved and confirmed with “The next three will use it.” The request already holds a snapshot without that remark. Mark the pending result as predating the feedback or regenerate from the updated review. [Feedback and follow-up](tone_lab/suggestions.py#L168)
- **Three design names can hide identical chains.** A simulated reply with three different labels and the same chain was accepted as three choices. A candidate that returns silence also stops the checks for the rest of its round. Verify that the returned designs differ and let the other candidates finish when one fails. [Design validation](tone_lab/preset_design.py#L740) · [Candidate checks](tone_lab/rounds.py#L172)

## Test results

- All **280 automated tests** passed. Python compilation and the Git whitespace check passed.
- A real Windows Tk test with a simulated Quad Cortex and assistant created three designs, switched between them, preserved knob settings, recorded feedback, ranked the designs, offered a follow-up, and browsed earlier rounds. The same test reproduced the Blind problem.
- Focused simulations reproduced the Stop restart, audition routing and cancellation failure, mixed-take ranking, stale or overwritten settings, and the owned IR failure. Audio analysis was checked with synthetic signals. The physical Quad Cortex and actual assistant replies were outside this test run.

## Response, 2026-11-19

Every finding was confirmed in the code. All are fixed except the reference context, which is left out on purpose. 296 automated tests pass, 16 of them new and named after the findings. The reworked listening was then run on the physical Quad Cortex, silently.

### Fixed before merging

1. **Stop followed by Guitar starting.** Stop now drops whatever was waiting for the unit (`Suggestions.forget`, called first in `Desk.stop`), and the reference that was waiting to play.
2. **An audition continuing after the preset changes.** `hearing.proof` checks the working slot, the scene and the USB routing before each pass and again before the result is accepted (`hearing.on_the_draft`). A replay can be cancelled within a twentieth of a second (`audio.reamp(cancel=)`). Either case ends the audition as stopped, and nothing measured is kept. The test switches the preset after the first pass: the quieter pass is never sent.
3. **Taking over a unit the player is using.** Listening now runs in a process of its own (`rounds.audition`). Before it touches anything it checks that it has not already been stopped, that the unit is on the preset Tone Lab last saw there, and that the dry guitar input is silent for 0.6 s. Otherwise it reports the unit as busy, leaves it alone, and the desk looks again 30 s later. It writes the same recovery marker the live worker does, and if the process dies the desk runs the live worker's recovery. **On the unit:** told to expect another preset, it reported busy and did nothing; stopped before it began, it returned in 1.5 s having heard nothing; stopped 9 s in, the unit was free 1.8 s later and no marker was left; left alone, it listened to all three and restored the playing preset. A crash of the child was not staged on the unit.
4. **Ranks mixing performances.** A round records the take it is listened to on: the phrase selected on the desk when the round is asked for, or when it is first listened to. Every later listen of that round uses it, and the next round inherits it.

### Other findings

- **Deferred choice from the wrong round.** The queued action carries the round and the draft, not a place (`put_chosen`).
- **Scores describing an earlier sound.** What was measured is stored with the knob positions it was measured at (`served_shape`). When the knobs have moved since, the strip says "heard before the knobs moved". The app's knobs replace a design's only if nobody has heard it and its knobs are still as they were sent.
- **Round writes erasing feedback.** The listening process writes nothing to a round. The desk is the only writer and merges measurements by draft.
- **Blind comparison.** The strip is hidden and its keys do nothing while a blind comparison is on, and the app does not start listening during one.
- **Owned impulse response.** The summary for the next round names a block by its asset or by the catalog, so a loader no longer fails the lookup. **The reference is deliberately not passed to rounds:** the player has said they have no recording to aim for, and the stale one that was set has been cleared.
- **Tags overstating.** Channels are no longer added together, so opposite-phase stereo is heard. A chain that swallows level while its peaks still stand 15 dB over the average is called at most "edge of breakup", so compression does not read as saturation. The tail is listened to from 40 ms after the phrase stops, in a short and a long window, so a short echo counts. The ambience thresholds were set again from nine chains measured on the unit.
- **Follow-up missing feedback.** A remark made while three more are being prepared is acknowledged as not included, and when they arrive the line says they were prepared before it and offers three more with it.
- **Identical chains and a silent design.** Designs with the same chain are counted once. A design that cannot be listened to is marked and the others are still listened to.

### Also changed

- The latest round is on show when the desk opens. Before, the markers stayed hidden until [ was pressed, which is why the player did not see them.

### Not done

- The idle check trusts the position the desk last read from the unit. If the desk has not read one, only the silence of the guitar is checked.
- Compression and clipping are told apart by one threshold on the peaks. A compressor in front of a distorting amp is still read as the amp's drive, which is what it sounds like.
