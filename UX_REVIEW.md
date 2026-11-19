# Tone Lab UX review and desk direction

Written 2026-11-18 from a read of the UI code and screenshots of every screen (taken with hardware access and state saving stubbed out). Nothing was tested with the Quad Cortex connected, so live A/B switching time and real assistant latency are unmeasured.

- Visual concept (four frames of the proposed desk): `design/desk-concept.html`, also at https://claude.ai/artifact/4nocsLB2Jyakye5k8rE5Jx
- Today's default screen, for comparison: `design/today.png`

## Status on 2026-11-18

The quiet desk is implemented in the Tk app on `main` (uncommitted when written). `pre-quiet-desk` points at the commit before it.

Done:

- Resting desk: A, B, the Guitar/Phrase input, one line, the request line, a status dot, five panel glyphs and a ⋯ menu (`tone_lab/listening_room.py`). The four tabs are gone. Setup and the tone field are separate windows.
- Tray with one open panel unless pinned, pins restored on launch (`tone_lab/desk_panels.py`): Shape, Difference, Chain, History, Sounds, Levels, Phrase.
- Requests: router no longer falls through to `create`; `undo`, `switch to A/B`, `save this`, `guitar`, `phrase` are commands (`listening.route`). Plain directions such as "a bit tighter" or "too bright" move the knob at once (`styles.direction`). Other free text goes to `advisor.read`, which also classifies `new_sound` and is then followed up as a create. The reading of each request is shown with Undo. Results are bound to the tone revision they were asked about and late results are offered, never applied. Cancel kills the `codex exec` process (`advisor.request`, `advisor.Cancelled`).
- The assistant receives measurements when captured audio exists: the selected tone against the reference and against the other tone, per band and as perceptual deltas (`listening_room.measured`).
- Undo for tone and shape changes, 40 steps, knob drags coalesced (`Desk.checkpoint`, `Desk.undo_last`).
- Keep works on the tone on air in either input mode and stores tone identity and shape (`listening.keep_tone`). Save to QC is separate and explains itself while live.
- Blind compare (`X`): tones shuffled and hidden, Keep reveals (`Desk.toggle_blind`, `Desk.reveal`).
- Shape shows what the chain can move, last-touched first, with the parameters each knob moves on hover. Availability is estimated from the draft before going live (`styles.draft_controls`) and replaced by the monitor's report once live. On the unit the estimate matched the report exactly for Stone Pulse (24 of 25 controls, no Gate).
- Phrase panel (`T`): waveform of the take with draggable loop edges, written to `selected_passage_seconds` in the take's `clip.json` (`Desk.trim_take`, `desk_panels.PhrasePanel`).
- Status by exception: amber dot and one sentence for an unreachable unit, Cortex Control open, a loudness limit, or a clipping USB return. Errors are red and stay until the next action. Levels shows the return peak while Phrase plays.
- Drafts stay resident on the unit: each is built once and stored in its side's working slot, then recalled (`monitor.resident_grids.select_draft`). Measured on the unit: first load about 20 s, first flip to the other draft about 11 s, every later flip 1.3–2.5 s (was 11–13 s every time).
- Layouts from use: when the same panels have been open together at least three times in a situation, the desk offers them on return (`Desk.offer_layout`, `usage.companions`).
- Bare keys, tooltips that name them, one shared palette for desk and Setup (`tone_lab/theme.py`), per-capture version and cabinet answers, usage log (`tone_lab/usage.py`).

Added later the same day, from research and the unit's own data (see `TONE_KNOWLEDGE.md`), none of it heard yet:

- Remarks in players' words are read at once with the remedy players try first, the reason, and **Another way** for the next remedy (`tone_lab/lore.py`, `Desk.instant_reading`, `Desk.another_way`).
- Chain panel: resting on a block says what it models and what to watch out for. Clicking a block lists its named settings, applied as a new version (`ChainPanel.voicings`, `Desk.voice_block`). Typing a setting's exact name does the same.
- Shape knobs follow what each control does on the model in use, including backwards controls and amps whose Volume is the gain (`styles.ROLES`). Low cut and Soft top act on the cab's filters in audible steps. Mic position is new. Its direction was measured the next day: up is toward the speaker's edge and darker.
- The designer can use every amp, drive and stock cab the unit will load, with notes and named settings for each, and can set a cab's microphones and a drive's Level.

Added on 2026-11-19, built and read back on the unit, none of it heard (see `TONE_KNOWLEDGE.md`):

- Drafts can hold scenes and a second path (`preset_design.layout`, `draft_render.Chain`). The Chain panel shows scene buttons, the second path under **∥**, per-scene differences on each block and the share of the unit's processing in use. The scene chosen there is the one that plays, live and in captures (`Desk.choose_scene`).
- A draft that will not fit the unit is rejected before building, naming the block (`budget.weigh`).
- Shape knob moves on an amp are scaled by how strong that amp's control measured against the typical amp (`measured.scale`).
- When the two paths of a draft hold amps of opposite polarity, the builder sets the mixer's phase switch (`measured.opposed`). Checked on twelve real blends.
- The designer is no longer offered the 18 plugin amps that load and do nothing, or anything else from their six plugins (`preset_design.locked_models`). No saved draft used one.

Fixed on 2026-11-19 after the review in `CLAUDE_WORK_REVIEW.md`, which also records how each was checked:

- A draft is stored and recalled on USB, and its output trim and mute are written before it is connected (`monitor.resident_grids.select_draft`, `monitor.run`). Measured on the unit: connecting adds about one second to each flip between drafts, so a flip takes two to three seconds.
- The builder confirms the working slot is still selected before each block, the split, the mixer and each scene (`draft_render.still_working`). Each check takes the unit about 0.1 s.
- Crash recovery returns from the Preview slot as well as the two side slots.
- A draft is saved on the scene chosen in the Chain panel, every scene is confirmed after saving, and an unsaved draft reopens on its scene.
- Keep records the tone and shape last confirmed as sounding (`Desk.heard`).
- "No fizz" asks for less. "No bite" still says bite is missing.
- Panels that do not fit fold to their titles (`desk_panels.Tray.fit`).

Changed on 2026-11-19 after the player asked for Pink Floyd's "Time" and got a tame fuzz (measurements in `TONE_KNOWLEDGE.md`, "A fuzz lead that came out too tame"):

- Drive at full right reaches the top of every gain control, and holds the level by trimming the amp's Output.
- R answers a click at any time. While the unit is on air it says that it is a recording that plays through Windows, and offers to stop and play it. Before, it was dead with no explanation.
- The designer is told how distorted each amp measured, and to run a fuzz near maximum.

Verified on the Quad Cortex (CorOS as installed on 2026-11-18, output muted through the lane mute, return peak −80 dBFS): reading the unit, loading a draft, Phrase and Guitar input, switching input without changing tones, A/B flips, a knob change, an instant direction, undo, Keep, blind flips, stop and restore to the original preset and scene, and a silent capture followed by a measured difference against the reference.

Not verified on the unit: recording a new phrase, Save to QC, trimming the loop while it plays (the `reload` path in `monitor.run`), saved presets as tones (only drafts were on A and B), and anything audible, since every run was muted.

Not done:

- Rolling record buffer and "keep that". The audio driver is only open while the monitor runs, and nothing retains the dry input. It needs a ring buffer in the live worker.
- Input and output meters. Only the USB return peak is read, and only in Phrase mode.
- True gapless A/B. A recall still takes over a second and interrupts the output. Building A and B on two rows of one preset and switching by lane mute would remove the gap for drafts, at the cost of reworking levels and the macro knobs to be row-aware.
- Guitar Learner still has its own palette.
- Startup reads the unit for about six seconds before the dot settles.
- Anything that needs a different toolkit: motion, smooth scaling, DPI awareness.

## Direction, set by the player

The first concept put everything on screen at once: meters, waveform, chains, spectrum, parameter lists, knobs, history. The player rejected it:

> That's a very busy interface, lots of text, all appears at the same time. 95% of the time I wouldn't be interested in 95% of what's there. Only from experience will I know what are the combinations that might be needed simultaneously, but we can be flexible.

So the problem "the app hides what it knows" is **not** solved by showing it permanently. It is solved by making each piece arrive when there is a reason and leave afterwards. Treat this as the governing constraint. When a finding below says something is hidden, the fix is "available at the right moment", never "add it to the default screen".

### Rules

1. **One thing at a time.** The resting desk holds two tones, a phrase and a request line. A second thing appears only because the player called it or something happened.
2. **Quiet while fine.** Meters, routing, device state and loudness matching are checked constantly and shown only when they fail.
3. **Arrive on cause, leave on the next action.** The reading of a request, progress and confirmations are single lines that retire into history by themselves.
4. **Summon, don't navigate.** No tabs. Any panel is one key, one phrase or one glyph away. One panel is open at a time unless pinned.
5. **Marks before words.** Colour says which tone (A blue, B peach, reference lilac, everywhere). Light says on air. A dot says healthy. Labels appear on hover and focus.
6. **The layout is learned.** Nobody knows yet which panels are needed together. Log what is opened and pinned in each situation, then offer that set next time. Do not hard-code combinations.

A useful test for any new element: would the player want this on screen during the 95% of the time they are just playing and flipping A/B? If not, it is a summoned panel or a by-exception line.

## The proposed desk

### Resting state (about eleven controls; today's default screen has about forty)

- **A and B** as two large fields with the tone's name. The lit one is on air. Click or press `A` / `B` to flip.
- **Input: Guitar and Phrase**, both always visible and equally direct (`G`, `P`; `Space` stops). A thin progress bar with the loop length shows while Phrase is the input. Input and tone are independent axes: switching input never changes either tone or its shape, and flipping A/B never changes the input.
- **Request line**: placeholder "Ask for a change, a sound or a song". `/` focuses it.
- **Status dot**: green while everything is fine. No text beyond "On air".
- **Rail of five glyphs**: Shape, Difference, Chain, History, Sounds.

Nothing else. No reference row until a reference exists. No Keep until something is on air. No Save until there is something unsaved.

**Keep and Save are different actions.** Keep (`K`) records a preference for the tone actually being heard, in either input mode, and stores the tone's identity and shape values. Save to QC writes a preset to the unit and is always a separate, explicit step.

### Lines that appear and retire

| Line | Appears when | Content | Leaves when |
| --- | --- | --- | --- |
| Reading of a request | After every request | "Tightened the low end on B" (always names the tone) · Undo `Z` · "What changed" | Next action; moves to history |
| Late result | A reply arrives for a tone that has since been replaced or re-shaped | "Ready for the earlier Stone Pulse · Put on B" | Accepted or dismissed. Never applied by itself. |
| Progress | A request or hardware job takes over a second | One phrase and a cancel | Job ends |
| Exception | Clipping, silent return, loudness mismatch, device lost, Cortex Control open | One sentence and one link to look closer | Condition clears |
| New-sound note | A sound was built from a request | Target and assumption, e.g. "Audioslave, Like a Stone, verse · assumed" | Next action; stays reachable on the Chain panel |

### Panels (summoned, one open unless pinned)

| Panel | Key | Opens by itself when | Contents |
| --- | --- | --- | --- |
| Shape | `S` | A request moved a knob | Knobs for the lit tone. Last-touched first, three to five visible, "N more for this chain" expands. Only controls the chain can move. Each knob shows the real parameters it moves on hover. |
| Difference | `D` | A reference is set | A/B/reference spectrum on one scale and one sentence naming the largest gap. |
| Chain | `C` | A new sound was built (once) | Blocks in order, blocks changed by the last request marked, the rationale and sources. |
| History | `H` | Never | Versions of each tone, kept ones starred. Any two can go on A and B. |
| Levels | `L` | An exception line links to it | Input and output meters, match status, trims. |
| Sounds | type a name | Never | Search over presets, captures, IRs. Recents and favourites first. |

Setup, calibration and Guitar Learner stay off the desk, behind a menu.

### Bare keys (when the request line is unfocused)

`A` `B` flip · `G` guitar · `P` phrase · `R` reference · `Space` stop · `Z` undo · `K` keep what is on air · `X` blind · `/` request line · panel keys above. The same actions should later map to footswitches and push-to-talk voice.

### Learning the layout

Add a local usage log now, before any redesign, so decisions come from use:

- File: `tone_sessions/ui-usage.jsonl`, one JSON object per event.
- Events: view or panel shown/hidden/pinned, with timestamp and situation flags (`live`, `source`, `has_reference`, `has_draft`, `recording`).
- Derived later: which views are visible together and for how long, per situation. Offer the most common pinned set when that situation recurs. Offer, don't impose.

In the current Tk app the "views" are the four tabs, the Shape sub-groups, the volume panel, the Details menu items and the Guitar Learner window. Logging those already answers most of the question.

## Findings in today's app

Each was verified by reading the code or running it. Line numbers are as of commit `7dfa69a` plus the uncommitted changes present on 2026-11-18.

### Requests are misread, slow, and cannot be undone

1. **Unrecognised text creates a new preset when a draft is on B.** `listening.route` falls through to `"create"` whenever `has_draft` is true (`tone_lab/listening.py:261-263`). `Desk.ask` then calls `preset_design.design(..., prior=None, ...)` with web search (`tone_lab/listening_room.py:1042-1048`) and `answered` installs the result on B (`:1070`). Measured with the router:

   | Typed, with a draft on B | Routed to |
   | --- | --- |
   | the low end gets muddy when I palm mute | create |
   | it sounds harsh on the high strings | create |
   | undo / go back / undo that | create |
   | switch to A / play A | create |
   | save this | create |
   | needs more sustain for the solo | create |
   | brighter / a bit warmer / less gain | refine |
   | more jazz | instant knob move |
   | more jazzy | refine (subprocess) |

   Without a draft, everything including "John Mayer Gravity" routes to `adjust-live`, so a bare artist and song is treated as a knob tweak.
2. **No cancel and no preview.** The request text is cleared, the Ask button is disabled until the future returns (`listening_room.py:595`), and B is replaced without showing how the request was read.
   **The result is not bound to the tone it was asked about.** `answered` calls `install_draft`, which always does `set_tone(..., side="b")` and selects B (`listening_room.py:784-786`, `:1070`), whatever B has become during the wait. Knob suggestions go to `intent_side` (`:1075`) without comparing `tone_revisions`, so they can land on a different tone that now occupies that side. Capture `tone_revisions[side]` when the request is sent and apply only if it still matches; otherwise show the "late result" line.
3. **Three phrases are instant** ("more jazz", "less delay", "darker", `tone_lab/styles.py:9-18`). Everything else starts a `codex exec` subprocess with a 50 s or 85 s timeout (`tone_lab/advisor.py:122`).
4. **The assistant gets text only.** The prompt says "You receive text only and have not heard this guitar" (`advisor.py:58`), although `tone_space.difference` already computes spectrum, dynamics, stereo and tail deltas. "Match" searches previously saved renders for the nearest one (`listening.py:184`); it does not shape toward the reference.
5. **No undo, no reachable history.** Drafts are loaded from disk at startup (`listening_room.py:113`; nine valid drafts on this machine) but only the latest is offered, under Details. After B is replaced, the previous B cannot be reached from the window.

### The main listening mode cannot record a decision

6. **"Keep this sound" is disabled while the Quad Cortex is live.** It requires `idle and self.selected_sound()` (`listening_room.py:598`), and `selected_sound` is only set by captured Windows playback started from the Details menu (`:317`, `:689`).
7. **Two parallel A/B systems**: live on the unit (tiles, Guitar, Phrase) and captured through Windows (Details menu). The second is where Keep, level-matched same-cursor switching and measurement live.
8. **Blind compare exists only in the old workbench** (`tone_lab/review.py:168`).
9. **Shortcuts are Ctrl chords shown nowhere in the window** (`listening_room.py:171-177`). Voice is Windows dictation into the focused field followed by Enter.

### What the app knows is discarded

10. **One status label carries progress, errors, assistant replies, "Why this sound?" and the signal chain** (`listening_room.py:1101-1102`). Each message erases the last. Errors look like hints.
11. **Tone tiles say "Draft".** The target, the assumption ("like a stone" read as the Audioslave verse) and the chain are not on the tile.
12. **Macro knobs read "0%".** `styles.apply_rules` returns the before and after value of every real parameter (`styles.py:251`); the UI never shows them.
13. **Which knobs do anything is unknown until the unit is live** (`available` arrives with the monitor's `controls` event). Before that all 25 look usable.
14. **No meters and no waveform on the desk.** The playhead is a 3 px canvas (`listening_room.py:264`). The loop region cannot be seen or edited there.
15. **Recording has no pre-roll, count-in or level.** Guitar Learner has "Keep recent clip"; the desk does not.

### Too much is permanent

16. **About forty controls on the default screen.** The reference row (Match, play, Reference menu) shows with no reference. Keep and Save show with nothing to keep or save. "Guitar Learner" appears twice in the header (`listening_room.py:193` and `:196`).
17. **Up to four levels of look-alike tabs**: Shape/Sounds/Lab/Setup, then Essentials/Response/EQ/Effects/Character, then Guitar calibration/Sound check, then Pickup/Volume/Tone.
18. **Sounds is a flat list**: 2,078 captures alphabetically with "Imported" as the only detail. Presets open on bass factory presets after the Tone Lab setlist. A capture needs two dropdown answers before "Try capture" enables, and those answers are shared widget state, so they carry to the next capture selected (`tone_lab/qc_browser.py:20-22`, `:124-138`).

### Visual consistency

19. **Four palettes**: desk peach (`listening_room.py:21-31`), Setup lime (`tone_lab/widgets.py:6-14`), Guitar Learner mint with a Georgia title (`tone_lab/guitar_board.py:10-19`, `guitar_learner_ui.py:195`), workbench (`tone_lab/ui.py:15-19`). 75 distinct colour values across the UI modules.
20. **The accent means five things**: primary action, selected tab, selected sub-tab, selected tone, knob value. Nothing is reserved for live, recording or error.
21. **Clipping at minimum size.** At 940 × 700 the Shape footer (hint, Hear base, Reset shape) and the lower row of knob values are cut off.
22. **Vocabulary.** "Capture" means a Neural Capture, a rendered clip ("Capture A for playback") and the act of recording. A/B means the desk's two tones and also the Lab's two marks ("Mark A", "Sweep A → B"). Take, phrase, clip and recording are used for the same thing.
23. **Toolkit ceiling.** No hover states, tooltips, motion or icons; native white dropdowns and scrollbars in Sounds and Setup; no DPI-awareness call (not observed as a problem on this 100% display).

## Order of work

### Days, inside the current Tk app: make it safe to try things

Order revised on 2026-11-18 after Codex's review, which independently reproduced finding 1 and added the late-result problem in finding 2. The first three items are the priority, in this order.

1. **Make requests safe to try.**
   - Router: never fall through to `create`. Require a creation verb or an artist/song pattern; send everything else to adjust or answer. Handle `undo`, `switch to A/B`, `save this` as commands.
   - Show how each request was read, naming the tone, before or as it applies.
   - Bind the result to the tone and revision it was asked about. A late result is offered, never applied by itself.
   - One-step undo for shape and tone changes, and a cancel for requests in flight.
2. **Keep Guitar, Phrase and A/B independent.**
   - Guitar and Phrase equally direct. Switching input preserves both tones and their shapes (the live monitor already does this through its `source` event; keep it true for every path, including when nothing is running yet).
   - Keep works on the tone actually being heard, live included, storing tone identity and shape values.
   - Save to QC stays a separate action.
3. **Make the knobs easy to summon and pin.**
   - Shape opens with a few controls that work on the selected chain, last-touched first. Resolve availability from the draft or preset before going live where possible.
   - A pin keeps it open while exploring.
   - Chain details, reference difference and meters stay one step away. Within reach means summoned, not permanently on screen.
4. A History menu listing saved drafts.
5. Hide what has no use yet: the reference row until a reference exists, Keep until something is on air, Save until something is unsaved, the duplicate Guitar Learner button.
6. Make capture version and cabinet answers per capture and remember them.
7. Fix the minimum-size clipping.
8. One shared token module for colour and type; point desk, Setup and Learner at it.
9. Start the usage log.

### Weeks: the quiet desk

1. Replace the four tabs with the resting state and summoned panels with pin.
2. Levels and device state by exception.
3. Bare keys.
4. Rolling record buffer so "keep that" works after the fact.
5. Difference panel on the desk; pass the same measurements to the assistant.
6. A local vocabulary so common descriptors respond without a subprocess.

### Then: a surface that can carry it

1. Web front end in a native shell with the Python engine unchanged. Panels that slide, fade and scale, smooth meters and real typography need it. `uvicorn` and `starlette` are already present in `.venv`.
2. Pinned sets offered per situation, from the usage log.
3. Footswitch, MIDI and push-to-talk for the same actions.
4. Auto-loop: when playing stops, the last phrase loops through the lit tone.
5. Listening contexts (home, rehearsal, stage) and guitar profiles driven by the Learner.

## Keep

- The listening-desk contract in `INTENT_AND_LANDSCAPE.md`: one place to ask, one focal comparison, one way back.
- Same-cursor A/B with crossfade and loudness matching (`listening.Loop`, `listening.Player`).
- Hardware safety: scratch slots, restore on stop, the Cortex Control handoff.
- The macro-knob vocabulary and the tone field.
- Drafts that cite sources and name their assumption.
- Guitar Learner's board as the bar for visual finish.
