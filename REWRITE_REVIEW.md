# Tone Lab: review for a rewrite

Written 2026-11-19 on the branch `rewrite`, for the owner and for whoever does the rewrite. Nine readers went through the project in parallel, one aspect each, and their reports are in `review/reports/`. This document is what they add up to, with what I checked myself and what I measured. Where a claim comes from a reader and I did not re-check it, the report is named.

## The short version

**Keep the product, rewrite the implementation.** The screen, the flow and the rules it follows are right for one player with a guitar in hand, and they were hard to get right. The code under them is three days old, grew in three directions at once, and now does most things in several places.

**The biggest finding is about fit, not code.** You said the main use is creating presets and improving existing ones. Creating is well served. Improving an existing preset is the weakest thing in the app: it can turn its Shape knobs on a copy of the preset, and that is all. It cannot show the preset's chain, the assistant cannot see it, the app cannot listen to it, and nothing can tell you what is off about it. Your unit holds 68 presets of your own and 59 downloads, most named for a song or an artist, beside the factory library. That is where "something is off for me in many presets" lives, and the app's best parts do not reach it.

**So the rewrite should be built around the unit's own preset**, not around the designer's recipe. Today the core object is "a draft the assistant wrote": one amp, one cab, a fixed order. A saved preset, a POD Go preset, a bass chain and an acoustic chain all fail to fit it. If the core object is "a chain as the unit holds it", then a design, a saved preset and an imported preset are the same kind of thing, and everything the app can do applies to all of them.

**Four structural changes carry most of the benefit:**

1. One process owns the unit for the whole session. Today 27 places open their own connection.
2. Nothing is built twice. Today one design is built once to be listened to, again to be played, again after every restart, and again to be saved, at 11 to 17 seconds each.
3. The desk's state lives in one place. Today it is about 80 fields on one object of 2,300 lines, with several copies of the same fact.
4. Knowledge is data with a known origin. Today much of it was retyped by hand into prompts and tables.

**About 7,300 of the 20,000 lines are outside the flow you use** and can go (the Guitar Learner, guitar calibration and gestures, two older workbenches, the tone field and search, stage sound check).

**Decisions that are yours are at the end.** There are six.

## What the evidence says about use

Everything on disk dates from 17 to 19 November: 27 commits, all data.

- Nine distinct requests from you, sixteen drafts, two saved to the unit, eighteen recorded takes.
- The panels opened were Chain, Difference, Sounds, Shape and History. Nothing else was opened.
- The Guitar Learner was used in one sitting of twenty minutes. Guitar calibration, gestures and the tone field were never used. The older workbench was last started on the first night.
- The commonest failure in the log is "the selected preset changed on the Quad Cortex", which ends live control. In the two cases logged on the afternoon of 18 November, a draft had been playing for two minutes, and getting back cost a 17-second rebuild.
- My Presets holds 68 presets: "Pink Floyd Time", "Comfortaby Numb", "Still Got The Blues", "Sad But True", "Sultans Of Swing" and so on. A setlist of cloud downloads holds 59 more in the same vein.

Three days is not much evidence. It is enough to say what has not been used at all.

## State of the project, aspect by aspect

| Aspect | State | In the rewrite |
| --- | --- | --- |
| Fit to the main use | Creating: good. Improving an existing preset: knobs on a copy, nothing else | A saved preset becomes a chain the app can show, listen to, explain and revise |
| The screen and its rules | Right. A few faults of discoverability and naming | Kept. Faults listed below |
| Structure of the code | One job in many places; state in many copies | Four changes above |
| Safety of the unit | Rules are right and tested, but each entry point applies its own version | One place for each rule |
| Waiting | Dominated by rebuilding and by fixed pauses | Build once, recall after; rows of one preset for instant switching |
| Reliability | One error ends a live session; a crash in the app's own process can leave the unit on a working slot | Keep the session on a preset change; recovery at every start |
| Tone knowledge | Real, sourced, partly measured on your unit | Kept, as data with its origin recorded |
| Measurement | Five spectrum implementations, four definitions of crest factor | One module, one result type |
| Tests | 299, of which about 200 specify behaviour and survive a rewrite | Carried over; a simulated unit added so the whole app runs without hardware |
| Cost of the assistant | No record of calls; calls can start without your asking | One function makes calls, keeps a ledger, and refuses without a user action |
| Loudness | Several local limits, no ceiling | One ceiling where the unit is written |
| A friend's PC | Cannot be installed: one required file is written by nothing | First run that checks, measures and sets up |
| Your other gear | Charvel only, by name | Instruments as data |

## The main findings

### 1. Improving an existing preset is the least served journey

From `review/reports/product-fit.md`, and I confirmed the central point in the code. With a saved preset on B you can turn the Shape knobs, speak a remark that moves a knob, flip against A, capture and compare, and save the result as a new preset. You cannot:

- see its chain. The Chain panel says "Open Cortex Control to see or edit this preset's blocks";
- have the assistant reason about it. It is sent no gear at all for a saved preset;
- have the app listen to it. Listening takes drafts only;
- change a block, a cab, a microphone or an impulse response;
- turn it into something the designer can revise. No code does this.

The unit hands over every saved preset in full: blocks, settings, scenes, routing. The app already reads that to verify its own work. Turning it into a chain the rest of the app understands is the single most valuable thing the rewrite can add, and it is why the core object has to change.

### 2. The same design is built over and over

From `review/reports/latency-and-failures.md`, which computed the time for each step from the code and checked it against the log.

- A built copy of a design lives only in the memory of the process that built it. Listening builds it, playing builds it again, each restart builds it again, saving builds it a third time.
- Starting Guitar on a draft took 16 to 20 seconds on ten logged starts. About 7 of those are fixed pauses.
- Stepping to another suggestion while playing is a full rebuild: 15 to 17 seconds of silence.
- Every pause between writes (0.05 to 0.7 s) is a guess that was never measured. Asking the unit anything costs a fixed 0.1 s because the library sleeps through its whole polling window.
- The desk reads the unit for about 3 seconds at start, after every stop, and whenever the window gets focus, and that read can delay Guitar by 3 to 6 seconds.

What the unit allows, measured this week: adding or removing a block mutes all audio for 0.2 s; changing settings, switching scenes and saving do not; moving the guitar from one row to another costs 20 ms; recalling a stored preset takes 1 to 3 seconds.

So: store each built chain in a slot keyed by its content, and never build the same chain twice. Put the designs you are comparing on separate rows of one preset, and switching between them takes 20 ms. Offer settings-only variations as scenes, which switch with no gap at all.

### 3. One job, many implementations

From `review/reports/hardware-layer.md`.

| Job | Implementations |
| --- | --- |
| Open the connection | 27 sites |
| Put a chain on the unit | 5 |
| Restore the preset you were playing | 8, each with its own test of what is ours |
| Decide that a slot is ours to write | 4 |
| Write routing | 3, with 4 ways of computing it |
| Verify what was written | 6, with three different tolerances |
| Apply output level | 3 |

The safety rules are sound: never write a slot that is not ours, restore what was playing, silence before connecting, check the selected preset before writing. But each entry point lists the reserved slots differently, and a crash is only recovered for work done in the two child processes. Capturing, saving and rendering run inside the desk's own process with no recovery marker, and recovery never runs when the app starts. The reader's summary of what must not be lost is the most useful page in the reports: port numbers, block ids, the order of operations for scenes, the fault in the library that needs its queue cleared.

One loss is already real. Every silent measurement depends on `tone_sessions/calibration.json`, the delay of the USB round trip. No code writes that file. I checked: the only code that ever mentioned its contents was in the first commit. The procedure that produced it is gone, and a firmware update that changes the delay would break listening, capture and every measurement with no way to redo it.

### 4. The desk is one object

From `review/reports/desk-ui.md`. `Desk` has 138 methods. "What is on B" is held as the tone, its shape, its revision, a separate "current draft", a captured sound, and now a round's own copy of the knobs, and these can disagree. "When the unit is free, do this" exists three times. The text of the output line is tested to decide what the screen shows.

That report found ten faults by reading. I confirmed and fixed four on `main` while writing this, because that is the code you will run in the meantime:

- "Stop and play R" never played (my fault, from my own fixes earlier the same day).
- A design was marked as heard while it was still being built, which then stopped the app from applying its own knobs to it.
- One unexpected error in the desk's clock stopped the desk for good: no job could finish and the window could not close.
- The request line gave no sign that it was taking a remark on a design, and stayed in that mode.

The reader's judgement on the toolkit is worth repeating: **Tkinter is not the problem.** Every fault found was a fault of state. Tk limits only what has not been built: meters, motion, high-DPI rendering.

### 5. Knowledge is real, but much of it was retyped

From `review/reports/knowledge-and-design.md`.

- A block has five representations between the assistant's reply and the unit.
- Knob positions are called values, shape, moves, knobs and directions.
- "Words to knob moves" exists five times.
- The instructions to the designer have accreted contradictions: "avoid maximum gain or mix" beside "run a fuzz at or near maximum"; "reverb under 25 percent" beside an aim of "wet"; a requirement to cite web sources in a call that runs with the web off.
- Some things the designer is told are not true of the code. It is told the notes say what a switch's positions do, and it is given no such notes.
- 95 descriptors, the research on paths and scenes, and two of the measurement files are consumed by no script. I transcribed them by hand into prompts and tables.
- Nothing compares the firmware a table was measured on with the unit's firmware. A model with no measured cost is treated as free.

And one error of mine, corrected on `main`: I told the designer that the amps were measured "with this player's bridge humbucker". The phrase used for every measurement has no pickup recorded. See the section on your guitar.

### 6. About a third of the code is outside the flow

From `review/reports/around-the-main-flow.md`, with line counts I took myself.

| Part | Lines | Evidence of use | Proposal |
| --- | --- | --- | --- |
| Guitar Learner, calibration, gestures | 3,945 | One 20-minute sitting; calibration never | Drop. Keep the harmonic analysis for the guitar profile |
| Two older workbenches | 1,994 | Last started on the first night | Drop |
| Tone field, search, "match" | 831 | No opens; one failed use | Drop. Keep the one function Difference uses |
| Setup, sound check, stage EQ | 540 | One preview | Fold dry level and headroom into every take |
| POD Go and Spotify readers | 59 | Command line only | Keep and connect (see "Other gear") |

The storage is over-built for one player. One new render writes seven files and 19 MB. Three days produced 528 MB. Re-trimming a phrase orphans every earlier comparison made on it.

### 7. No ceiling on loudness, no record of calls

From `review/reports/security-and-cost.md`.

- Loudness is bounded by several local limits (a −12 dB trim block, ±6 dB on some outputs, a +6 dB cap on matching). None is a ceiling. With the guitar live, a legal but extreme chain is bounded only by the unit's full scale and your monitor knob. Drive at full now puts every gain control at its top.
- The assistant's knob suggestions are applied to the live tone at once, although its instructions say they are proposals.
- One sentence can cost three calls: reading it, a round if it is judged a request for a new sound, and an automatic second round with web search once the app has listened. There is no record of calls anywhere, and nothing stops a script from triggering them. That is how a screenshot script of mine made a real call earlier the same day.
- What is sent to the assistant is modest and contains no audio or file paths. Replies are validated before anything reaches the unit.
- Names of presets and impulse responses downloaded from the cloud, and web search results, reach the prompt. The worst outcome is a legal but extreme chain, which is the loudness point again.

### 8. A friend cannot install it

From `review/reports/product-fit.md`. Nothing in the source names this machine, but:

- the calibration file above is required and nothing can create it;
- a setlist named "Tone Lab" must be made by hand, and three particular slots must be free;
- the tables of costs, control strengths and polarity are your unit at firmware 4.1.1, loaded without a check;
- the list of plugins that load and do nothing is yours, and locks those plugins for everyone;
- data is written inside the program folder.

## Your guitar's raw signal

You said something is off in many presets, and that you want to understand what your guitar puts out. Here is what can be said today, and what five minutes of recording would settle.

**The level is normal.** By figures Neural DSP's support gave a forum user, full scale at the unit's input is about 14 to 15 dBu. Your dry takes peak around −18 dBFS on the louder ones, which is roughly 0.75 V peak. Seymour Duncan's own output figures put the Full Shred within 0.2 dB of a JB, the pickup preset makers most often assume. So the bridge pickup is neither weak nor hot. Sources are in `review/reports/gear-research.md`; the forum figures are second-hand.

**The noise is not the problem.** With nobody playing, the input sat at −90 dBFS with hum harmonics below −98. That is 55 to 60 dB under your playing.

**The unit's input looks like the defaults.** Read from the unit: both inputs at instrument level with level 0, as I read the raw values. Those are the settings the community recommends leaving alone.

**What I did not expect:** of the nine takes you labelled in the Guitar Learner, five are neck pickup with the mini-toggle on **parallel**, one is neck in series, and positions 2, 3 and 4 have one take each. There is no labelled take of the bridge pickup at all. The phrase that every measurement on your unit was made with (all 121 amps, the thresholds for "driven", the "Time" comparison) has no label. It was recorded two minutes after the last neck-parallel take.

That matters in two ways.

- **A hypothesis about "off".** A humbucker wired in parallel puts out roughly half the voltage of the same pickup in series, and sounds thinner and brighter. Your neck pickup is also about 4 dB below your bridge to begin with. Nearly every preset on your unit was built on a series bridge humbucker or something like it. If the toggle sits on parallel much of the time, those presets receive 6 to 10 dB less than they were built for: less drive, less sustain, a thinner sound. I cannot tell from the recordings whether that is how you usually play. It is cheap to find out.
- **A caution about my measurements.** How driven an amp gets depends on how hard it is hit. The table that says a Hiwatt stays clean and a Rectifier is saturated at 5 was measured with one phrase at one unknown pickup setting. It compares amps with each other fairly. It does not say what any of them does with your bridge pickup in series.

**Five minutes when you are back.** One short phrase, the same each time, recorded in each setting: bridge series, bridge parallel, neck series, neck parallel, and positions 2, 3 and 4. Then the same phrase played soft and played hard on the bridge, and the volume knob at 10, 7 and 4. From that the app can state, in decibels and in words, what each setting does to level and to brightness, and it can listen to any preset once per setting and say which settings it suits. That is the "guitar profile" below, and it is what would let the app say "this preset expects 8 dB more than your neck-parallel gives it" where today it can say nothing.

## The shape of the rewrite

### The core model

Named once, used everywhere. Today the same thing is called a tone, sound, draft, design, version and preset.

- **Chain**: blocks on rows, their settings, scenes, splits, routing. What the unit holds. Everything below is, or has, a chain.
- **Sound**: a chain with a name and a history: where it came from (designed, read from the unit, imported), what it was derived from, what was measured of it, what you said about it, where it is saved.
- **Round**: an aim and three sounds, as now.
- **Song**: a name, an instrument, a phrase, and ordered **parts** (rhythm, lead, clean). A part is a sound, or a scene of a sound. Rounds belong to a part. This is the "collection of presets for a single song": one preset with scenes when the parts share their blocks, several presets in adjacent slots when they do not.
- **Instrument**: Charvel, Les Paul, bass, acoustic, classical. Its input, its notes for the designer, its own phrases and its measured profile per pickup setting. Chain templates per kind, so that a bass or an acoustic chain is not forced into "one amp and one cab".
- **Phrase**: a recorded take with the instrument and the pickup setting it was played on, written down at the time of recording.
- **Measurement**: one result type for level, loudness, peaks, spectrum, and the "heard" words.
- **Knob**: a function from a chain to the settings it would move. Today six mechanisms are overlaid in one 100-line function, and whether a knob is available is computed two ways that disagree.

### The unit

- **One owner process**, started with the app, holding the USB connection and the audio driver for the whole session. The desk sends it typed commands (make this sound available, connect it, set these knobs, listen, save, stop) and receives events. It runs recovery every time it starts.
- **A reconciler.** Given the chain wanted and the chain read back, it works out the writes, in the right order, with the checks. One implementation replaces the five builders, three routing writers and six verifiers.
- **A slot registry.** One table of which slots are ours and what each holds, confirmed against the unit's directory. One reserved set used by every guard.
- **A cache of built chains**, keyed by content, in slots at the end of the Tone Lab setlist. Build once, recall after.
- **Rows for instant switching.** The sounds being compared sit on separate rows of one preset. Whether three full chains always fit one preset's processing has not been measured.
- **Pauses replaced by acknowledgement.** Write, then read until the value is there, with a limit. The reader estimates a build about 5 seconds shorter and a flip under a second. Those are estimates.
- **A preset change is not a failure.** If you select another preset on the unit, the app stops writing, keeps the session, and offers to return or to work on the one you chose.
- **One loudness ceiling**, at the point where the unit is written. A chain nobody has measured stays at a low trim.
- **A calibration command** that measures the USB delay, so the file nothing can create is created by the app.

### The desk

The same screen. State in one immutable structure, changed only by a function from (state, event) to (state, things to do). The Tk widgets draw the state and send events. One scheduler for everything that waits for the unit, which Stop clears. Undo becomes a stack of earlier states. Saving the desk is a subscriber.

### Knowledge and the designer

- Every knowledge file carries what generated it, from what, on which firmware. One loader, typed tables, a check against the unit at start.
- The lexicon, the craft notes and the measured numbers are generated from the research files into the prompt. No retyping.
- The prompt is built from sections with one owner each, and the schema lists the legal model ids, so that a reply cannot name a model that is not offered. A reply that fails validation gets one repair turn before it is dropped.
- One function calls the assistant. It keeps a ledger (time, kind, asked by you or automatic, web, size, seconds, outcome), refuses a call that no user action stands behind, and has a daily cap.

### Tests

About 200 of the 299 tests state behaviour on real data and survive with renamed imports. The 65 that patch internals have to be rewritten against the new seams, and the rules only they hold are listed in `review/reports/tests-and-measurement.md` so that none is lost: the order of level before connection, phrase continuity across a flip, crash recovery (which has no test at all).

The test fake of the unit is good enough to grow into a simulator: saved and edited slots, a directory, recall, a clock, refusal of blocks that do not fit, a player who switches presets, and a crude audio return. With that, the whole app runs end to end without hardware, which is also what a friend's first look and every future review need.

## Faults in the screen and its words

You asked for the interface to stay unless issues were found. These were, and they are small.

1. **A request that matches a preset's name loads that preset** and never reaches the designer. You name presets after songs, so "Pink Floyd Time" typed as a request will load your preset of that name. Offer both readings.
2. **A revised design leaves its round.** "Rework the chain", a named setting or an impulse response makes a new draft outside the round and loses its marks and reviews.
3. **Keep writes a file that nothing reads.** "This is it" neither keeps nor saves. History's star means saved to the unit, not kept.
4. **Things reachable only by a key or a right-click**: blind comparison, copy B to A, reviews, marks, "Three more".
5. **Controls that silently do nothing** while a job runs, in six places.
6. **Words.** Tone, sound, draft, design, version and preset for one thing. "Capture" for a Neural Capture, a recording and the act. "Guitar" for the input and the instrument. Phrase, take, loop and clip.
7. **The app takes the unit to listen four seconds after you stop**, and the guitar is silent while it does. With built chains cached and listening done at build time, this goes away.

## Other gear

- **Les Paul, bass, acoustic, classical.** Instruments as above. The Les Paul needs a phrase and five minutes of profile. Bass and acoustic need chain templates, and bass models are currently filtered out of what the designer may set. Classical and acoustic through the unit are mostly EQ, compression and reverb, with no amp, which today's model cannot express.
- **POD Go.** The app already reads your backup completely: 128 user presets with every block, its settings, four snapshots each. Your names there already split by instrument and song ("LP", "CH", bass, "LOVE OF GOD", "Time"). Nothing translates them. Line 6 and Neural DSP each publish which real amp or pedal every model is based on, so a POD Go preset can become a starting chain through the real gear. That makes 128 sounds you already like into requests the designer can start from.
- **Nano Cortex.** It runs one Neural Capture, one impulse response and a fixed set of effect slots. It has no amp models and cannot load a Quad Cortex preset. Captures made on the Quad Cortex play on it. So a sound travels as a capture of the amp-and-drive part plus a note of the cab and effects to set by hand. The app could prepare exactly that: which blocks to capture, and a short sheet for the rest. Loading it from a computer is not documented.
- **Spotify.** The reader exists and ranks tracks by listening time. Its natural use is to propose songs to build collections for.
- **Monitors, headphones, room.** Nothing exists. The app plays captured audio through the Windows default output. The honest version of this is a measured correction per listening device, applied to what the app plays and taken into account when it compares. It needs the measurement module to keep absolute spectrum bands, which is why that is in the core now. It also needs a measurement microphone, or it is guesswork.
- **Stems from real songs.** Studio Pro separates into vocals, drums, bass and "other" locally, so the guitar arrives mixed with keys and everything else. Its cloud option offers more instruments. Not designed here.

## Order of work

Each stage ends with something that runs. The current app stays usable until the new one replaces it: the new code goes in a new package beside the old one.

1. **Core, simulator, knowledge.** The typed model, the loader with provenance, the simulated unit, the measurement module. The 200 behaviour tests ported. No hardware and no screen.
2. **The unit process.** Owner, reconciler, slot registry, cache, recovery at start, calibration, ceiling. Checked against the simulator and then silently on your unit.
3. **Existing presets as chains.** Read a saved preset into a sound. Show its chain, listen to it, let the designer revise a copy. This comes before the desk is moved because it is the largest gain and it tests whether the model is right.
4. **The desk on the new core.** The same screen on the new state. Rounds, listening, A/B on rows.
5. **Songs, parts, instruments, the guitar profile.**
6. **First run for a friend.** Data in the user profile, checks, setup of slots.
7. **POD Go as starting points, the Nano sheet, the assistant ledger's daily view.**

Stages 1 and 2 need nothing from you. Stage 3 onwards is better with you playing.

## Decisions that are yours

1. **Rewrite beside the old code, or change the old code in place?** I recommend beside it. The old app keeps working, and the new one is compared against it stage by stage.
2. **Is "improve a preset I already have" the first new capability?** I recommend yes, at stage 3, ahead of moving the desk.
3. **The drop list**: the Guitar Learner, guitar calibration and gestures, both workbenches, the tone field and search, stage sound check. About 7,300 lines. Anything there you want kept?
4. **Songs with parts** as the way to hold a collection for one song. Is that how you think of it, or do you think in setlists?
5. **Automatic calls.** Today a second round is prepared without asking when a design missed. With a ledger in place, do you want that on by default, or only on request?
6. **Stay on Tkinter.** I recommend yes. The evidence says the toolkit is not what has been going wrong.

## What was done while reviewing

- `suggestions` merged into `main`.
- Four desk faults fixed on `main`, with tests (299 pass).
- The false statement to the designer about the bridge humbucker corrected on `main`.
- Nothing else in the code was changed. No part of the rewrite has been started.

## What this review did not check

- The readers read code. Except where this document says "I confirmed" or "measured", their findings were not re-checked line by line.
- Nobody has listened to anything.
- The level figures for the unit's input are second-hand from a forum.
- Whether three chains fit on three rows of one preset, and how much faster writes can go without fixed pauses, are untested.
- The security reader could not verify the assistant's command-line flags against the installed version.
