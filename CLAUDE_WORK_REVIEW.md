# Tone Lab review of Claude’s changes

I reviewed the quiet desk, tone knowledge, and draft scene and routing changes in commit `a094593` against `3279783`. The quieter desk launched successfully and the main controls worked in testing. I would keep the changes on the review branch until the three Quad Cortex issues below are fixed.

## Fix before merging

1. **Switching presets during a draft build can affect the newly selected preset.** In a simulated switch just after the final parameter write on the first row, the app sent a splitter write to the new preset before it noticed the switch. Check that the working slot is still selected before writing splitter, mixer, and scene settings. [Draft building](tone_lab/draft_render.py#L299)
2. **Starting a fresh draft can briefly bypass the app’s mute or listening level.** The monitor connects the physical guitar to Multi Out before applying the requested output trim and mute. Keep the draft on USB during staging and apply those settings before connecting the physical route. [Draft staging](tone_lab/monitor.py#L283)
3. **Crash recovery can leave the Quad Cortex on the Preview slot.** In a simulated crash during draft staging, recovery skipped restoring the playing preset and cleared its recovery marker. Include Preview among the working slots recorded for recovery. [Recovery setup](tone_lab/monitor.py#L189)

## Other findings

4. **Saving or reopening a draft can lose the selected scene.** Saving a live-selected Lead scene without a matching recording applies its shape to scene A. Reopening an unsaved draft also loses its saved scene selection. Pass the selected scene explicitly and verify the saved settings across scenes. [Save action](tone_lab/listening_room.py#L1896) · [Draft state](tone_lab/tone_identity.py#L183)
5. **Keep can record a different sound from the one you heard.** During a same-side recall, Keep can record the incoming preset while the old preset is still sounding. With Hear base enabled, it records the shaped version. Use the last confirmed playing tone and shape for the decision. [Keep action](tone_lab/listening_room.py#L1854)
6. **“No fizz” moves the tone in the wrong direction.** The local interpreter reads it as “More fizzy” and moves Soft top in the opposite direction from “less fizz.” [Request interpretation](tone_lab/lore.py#L332)
7. **Pinned panels can disappear at the minimum window size.** At 980 × 620, expanding and pinning Shape, pinning Phrase, and opening History left both Phrase and History invisible. The tray needs a way to reach panels that exceed its height. [Panel layout](tone_lab/desk_panels.py#L128)

## What was tested

- All 231 automated tests passed. The Windows shortcut opened the new desk.
- An isolated desk test with simulated hardware passed Guitar and Phrase switching, A/B, instant shaping, Undo, blind Keep, panel pinning, loop trimming, and loudness control.
- A saved recording pair matched in measured loudness while retaining peak headroom.
- The physical Quad Cortex and its ASIO driver passed read-only checks. Hardware writes, crash recovery, and output routing were tested in simulation. The physical unit stayed on Brit 2203, scene A.

## Response, 2026-11-19

All seven findings were confirmed in the code and fixed. 246 automated tests pass, 15 of them new (`tests/test_desk_safety.py`, `tests/test_tray.py`). Two things were checked beyond simulation and are marked below.

### Fixed before merging

1. **Switching presets during a build.** `draft_render.still_working` now runs before each block, before the split, before the mixer, before the scene assignments, before and after each scene switch, and before the old grid is cleared. Asking the unit which preset is selected takes about 0.1 s (measured), so one check guards each short run of writes and not every write. A switch in the middle of a block can still let the rest of that block's settings land. The test moves the switch to every write of a two-path, two-scene build: no split, mixer or scene write follows a switch made just before it, and the build always stops within one block.
2. **Mute and level before the guitar.** A draft is now built, stored and recalled on USB routing. `monitor.run` writes the output trim and mute for the incoming tone and only then connects the guitar or the phrase, both at the start and on every change of tone. **Checked on the unit, silently:** after staging the route was USB, the mute was then written, the guitar was then connected with the lane muted, and a second recall came back on USB. At no step did an unmuted lane reach an output. The cost is the connect step, 0.95 s measured, on every flip between drafts.
3. **Crash recovery.** The Preview slot is among the slots recovery returns from. On the unit, the restore notice that the worker saves as its recovery marker listed all three. A crash itself was not staged on the unit.

### Other findings

4. **Scenes on save and reopen.** `draft_render.save` takes the scene explicitly and the desk passes the one chosen in the Chain panel. After saving, `assert_scenes` confirms every scene: the shaped scene with its edits, and the others without the edits that belong to that scene alone. An unsaved draft now reopens on the scene it was left on.
5. **Keep.** `Desk.heard` gives the tone, shape and revision last confirmed by the unit, with an empty shape while the base is switched in. Keep and the Kept mark both use it.
6. **"No fizz".** "No" before a fault (fizz, mud, boom) now asks for less. Before a quality (bite, sustain, punch, clarity) it still says the quality is missing.
7. **Panels out of reach.** When the open panels need more height than the tray has, the older ones fold to their title, marked ▸. A click on a folded title, or the panel's key, brings it forward and folds another. **Checked in the real desk window at 980 × 620** with the case reported: Shape expanded and pinned, Phrase pinned, History opened. Every title stayed in reach through each step. A front panel taller than the room left by the titles is cut short at its bottom edge, and the titles below it stay visible.

### Left as it was

- ~~Saved presets are still recalled already connected.~~ **Fixed later the same day.** A saved preset's working copy for Guitar now keeps the preset's own outputs and has no input connected (`iterate.held_routes`), so recalling it is silent and the guitar is connected after the trim and mute, as for drafts. **Checked on the unit, silently,** with the playing preset: no input after staging and after a recall, the mute written to the row that leaves the preset, then the guitar connected in 0.96 s. A flip between saved presets in Guitar now also takes about a second longer.
- **Saving a draft connects the guitar** for the few seconds the save takes, at the draft's own level, because the saved preset has to carry the guitar routing.
- The workbench's **Play draft** (`draft_render.try_guitar`) connects a draft as soon as it is built. It has no mute or level control.
