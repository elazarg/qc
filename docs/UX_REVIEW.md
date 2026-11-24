# UX review of the desk

Written 2026-11-24 at commit `0046f6b`. Every screen was pictured hidden on the simulated unit with made-up
sounds (`tests/picture.py`), at 1100×680 and at 980×620: the desk empty, with a round, playing, blind, saving,
each of the eight panels, each of the nine side tools, and the three views of the learning screen. The flows
were then read in `lab/desk/window.py`, `lab/desk/studio.py` and `lab/desk/requests.py`. Nothing was tried on
the real unit, so timing and anything audible are not judged here. The pictures named below are in
`docs/ux-review/`.

## Status on 2026-11-25

Done the same day, each with tests (`tests/test_lab_desk_faults.py`, `test_lab_desk_asking.py`,
`test_lab_desk_sides.py`, `test_lab_desk_symbols.py`) and looked at in `tests/gallery.py`'s pictures at both
sizes. Nothing was tried on the real unit.

- **Faults:** F1–F9, all.
- **Request loop:** R1–R7. The working line and Cancel sit above the ask line with the seconds; the ask line
  has a placeholder and keeps the focus; the next remedy is a link; faults have a colour and old lines dim.
- **A and B:** A1 (the rule chosen: a change acts on the selected side and goes back there; Shape, Chain and
  Sounds name the side), A2, A3 (B's tile lists the knobs that differ; blocks that differ are outlined), A4
  (scales with aim and heard; the misses named under the chips), A5, A6, A7.
- **Where things live:** I4 (the open panel's name is lit). **Words:** W2 (Chain's measurements behind
  "Measured ▾"), W1 in part ("Volume" for how loud the player hears it; the word list is in `AGENTS.md`), W3
  (a knob's check says whether it did what it says, with the measure in brackets, and the correction in the
  same clause), W4 (the contrast's four measures renamed for what they measure and drawn as signed bars; the
  band chart in the sides' colours, its bands in Hz, with a key), W5 (the record control is a dot, dimmed with
  its reason).
- **Looks:** V1 (the accent is lilac, no longer B's peach), V3 (the dimmest text is 5:1 on the panels), V4 (a
  one-pixel rule round the tiles and the buttons), V6 (Shape's knobs are tracks with a mark where each rests,
  a bar to where it stands in the side's colour, and the position), L3
  (the rows under the tiles keep their room; the tiles give way), L6 through D4.
- **Lists:** S1 (History grouped under the request each sound came from, newest first, with ♥, ✕ and ★, and a
  line to narrow it; the lists that filter carry a magnifying glass), S2.
- **Process:** P1–P5: `tests/gallery.py`, a fit test of every panel with real content, the usage log sees the
  desk, and the placement rule and word list in `AGENTS.md`.
- **Section 11:** N1 (in `AGENTS.md`), D4, D5, G1 (tiles, the round's chips, History rows and IRs; not
  presets or captures, whose drop would need a choice), the scales, the chain's blocks, the pips, the heart
  and the cross, the three symbols between the tiles, the phrase strip with its playhead and record dot, and
  the lines that restated a click.

Waiting, and why:

- **On the unit, with the player:** D1 (the guitar live by default), D2 (play once, alternate A and B), G3
  (flipping A and B with no hand), D3 (A as "before" by itself, which changes what A means). These change
  what the engine does or what the player is used to.
- **The player's call:** I1–I3 and I5 (merging Phrase and Guitar, one place for the guitar's setting,
  renaming Sounds and the tools). The usage log now records the desk, so these can be argued from use.
- **Not started:** R8, W6, L1–L2, L4–L5, V2, V5, S3, T1–T3, G2, N2–N4 and the rest of the table of marks
  (rings on what is being worked on, a recording lamp, charts in Guitar and Chain).

Findings are numbered so that they can be picked up one at a time. Each says what was seen, where, and what
would mend it inside the rules of the design in `AGENTS.md` (one window, few words, no shortcuts, side
features carried).

## The short of it

The quiet desk holds. At rest it shows two sounds, two inputs, one line and one place to ask, and the side
tools are sparse when they open. The palette is one palette now, hints are behind a `?`, and every panel and
tool is in the one window. None of that needs redoing.

What falls behind is everything that was added since:

1. **The main loop gives the least feedback where the player waits longest.** A request is typed into an
   unlabelled box, vanishes on Enter, and for about a minute the only sign of it is a grey line in the top
   row whose Cancel is cut off by the panel names. The status line beside the box still says "Ready."
2. **Nothing on the screen says what an action will act on.** Some actions change the selected side, some
   always change B, and one reads the selected side and writes to B.
3. **Features are filed by when they were built, not by what the player is doing.** Recording a phrase has
   five entry points and three ways to state the pickup setting; comparing two sounds has seven places;
   "listening" names four different things.
4. **Panels grew past their frame.** Chain and Guitar are cut off at the bottom with no way to scroll, so
   the Record button cannot be reached at the smallest window, and the status line is clipped instead of
   wrapped whenever a panel is open.
5. **There is no step in the work that looks at the whole.** Each feature was checked where it landed. No
   command shows every screen, no test checks that a panel fits its height, and the usage log records the
   side tools only.

6. **The app says in sentences what a state, a symbol, a mark or a default would say.** The desk can say
   about 166 different things on its lines; nearly a quarter of them are "do something else first", for a
   control that should not have looked pressable. Guitar is a button the player presses again after every
   save, recording and measurement. Section 11, added after the owner read the first ten, is about that.

The fifth is why the first four keep coming back. Section 10 is about that.

## 1. Faults, verified in pictures and code

These are defects, not matters of taste. Each was seen in a picture and found in the code.

| # | What | Where it shows | Code |
| --- | --- | --- | --- |
| F1 | While a request is with the assistant, **Cancel is cut to "Canc" at 1100 wide and is gone at 980**; the working line also covers the names Shape and Chain. The working line is packed before the panel names, so it takes the room first and Cancel gets what is left. A longer working line ("Designing three more, from what was heard and what you marked…") covers more. | `request-in-flight-1100.png`, `request-in-flight-980.png` | `window.py:202-210`, `:342-345` |
| F2 | **The status line is clipped, not wrapped**, when a panel is open: it wraps at a fixed 640 px while the column is 616 px at 1100 and 496 px at 980. The same holds for the note under the round's chips (600 px) and for the aim at their right, which is cut mid-word. The tiles' name and caption follow the width; these three do not. | `shape-980.png` ("…for a firmer atta"), `guitar-980.png` | `window.py:308`, `:275`, `:495` |
| F3 | **The Chain panel does not scroll.** A sound that has been listened to has thirteen lines under "Measured"; the last are cut off at both sizes, and with them the Nano Cortex sheet and the player's own remarks, which come after. | `chain-980.png` | `window.py:731-792` |
| F4 | **The Guitar panel does not scroll.** At 1100×680 the Record button is cut in half; at 980×620 it is off the panel, so a phrase cannot be recorded from there. | `guitar-980.png` | `window.py:1247-1267`, `:1196-1235` |
| F5 | **The tile's letter keeps a dark box behind it while the tile is on air**: the badge is left out when the tile's background changes. Keep and Not this are painted before the tile is, so they lag one draw behind (seen as lighter chips after playing stops). | `on-air-1100.png` | `window.py:354-366` |
| F6 | **The phrase list shows the newest eight takes and no more**, without saying so. An older take cannot be chosen again from the panel. The captures list stops at 400 rows the same way. | Phrase panel, Sounds › Captures | `window.py:853`, `:1049` |
| F7 | **Add parallel space reads the selected side and writes to B.** With A selected, pressing it in Shape replaces B with A's sound plus the layer. Trying an IR or a capture takes B, or A when B is empty, and writes to B. | Shape panel, Sounds › IRs | `studio.py:988-1004`, `:966-986` |
| F8 | **Teach a control offers a classical guitar a pickup selector, a volume knob and a tone knob**, with Neck, Bridge and Middle as its settings. | `teach-classical-1100.png` | `learner/taught.py:39` |
| F9 | **The status line goes stale and nothing marks it as old.** "Ready." stays for the whole minute of a request. "5 IRs and captures on the unit." followed from the Sounds panel through nine tools to the learning screen. | `request-in-flight-1100.png` | `studio.py:180`, `window.py:368` |

F3 and F4 would have been caught by a test of height like the one the tools have for width
(`tests/test_lab_one_window.py:225-287`, `support.DeskCase.assert_fits`); the panels have none.

## 2. The request loop

Asking is the main thing the app is for, and the ask line is the least finished control on the desk.

- **R1. The ask line has no name.** It is an empty dark box at the bottom with no label, no placeholder and
  no button (`window.py:310-314`). The empty tiles say "Ask for a sound, or bring in a preset from Sounds",
  twice, but do not say where. A placeholder in the box, in the dim colour, gone on the first key, costs no
  permanent words: "Ask for a sound, a song, or a change".
- **R2. The request disappears on Enter.** The text is cleared and the focus leaves the box
  (`window.py:1166-1170`). Nothing on the screen says what was asked until the answer comes, and "say it
  again for the next remedy" means clicking back in and typing the same words again. Keep the last request
  on show, dimmed, beside the working line; keep the focus in the box; and after a remark, show the next
  remedy as something to click ("Next: less gain"), which is a name and a value, not an explanation.
- **R3. How a request was read is not shown before it costs anything.** The same box turns a knob at once,
  revises B (a call to the assistant), starts a round (a call, about a minute, and B and the round are
  replaced), or brings in a preset by its exact name. Which of the four happens is decided by word lists the
  player cannot see (`requests.py:284-338`, `studio.py:869-928`). A misread is recoverable (Undo, Cancel) but
  the player finds out by what happens. Say the reading at once, as the working line: "New round for
  “Comfortably Numb, the second solo” · about a minute" or "Revising B: more sustain from the fuzz".
- **R4. The working line is in the wrong place.** It is in the top row, in soft grey, between the instrument
  and the panel names; the player's eyes are at the bottom, where they typed. Put what is being worked on,
  and Cancel, directly above the ask line where the status line is, and let the status line show the last
  result only when nothing is in flight. That also ends F1.
- **R5. A minute's wait shows no time.** The working line says "About a minute…" and does not change. The
  seconds elapsed, counted beside it, are a value, and tell a stuck request from a slow one.
- **R6. "Three more" spends a call with one click.** While the next three are not ready, the chip reads
  "Three more" in grey and starts a round on the assistant (`studio.py:1040-1045`); when they are ready it
  reads "› next three" in the accent. The difference between "this is free" and "this takes a minute and one
  of today's eighty calls" is a change of weight in a 9-point label.
- **R7. Messages have one look.** A fault ("Something went wrong…"), a refusal ("Nothing is on B to copy"),
  a result ("Saved · Tone Lab 3 · …") and a tip are all the same white 10-point line
  (`window.py:308`, `:368`). `theme.py` reserves colour for identity and state; a fault is a state. Give
  the line three looks: plain for results, the dim colour once the message is older than the last action,
  and one colour for faults and refusals.
- **R8. There are four places where the app speaks.** The working line (top row), the status line (above the
  ask line), each tool's own line (bottom of the tool, `tools/common.py:588`), and the learning screen's line
  (`learner/screen.py:45`). A tool's "Working on the Quad Cortex…" is said in two of them at once. One place
  for what is going on and one for what happened is enough; the tools can use the desk's.

## 3. A and B: what an action acts on

The desk's words imply a model: A is what you compare against, B is what you work on ("Plexi Crunch is on A
too, to compare the next change on B against"; a preset "is on A as saved, and on B to work on"). The
controls do not all follow it.

| Action | Acts on |
| --- | --- |
| Guitar, Phrase, Save to QC, Chain panel, scenes | the selected side |
| Shape knobs and pad, a named setting, "Hear it without the knobs" | the selected side |
| A remark ("a bit tighter") | the selected side |
| A change in words ("more sustain from the fuzz") | B, always |
| The round's chips, a new round | B, always |
| An IR or capture from Sounds | reads B (or A), writes B |
| Add parallel space | reads the selected side, writes B |
| Copy B to A | B to A |

- **A1. Choose one rule and show it.** Either every change goes to B and selecting A only changes what is
  heard, or every change goes to the selected side. The first matches the app's own sentences and most of
  the code; then the Shape panel is B's, and says so. Whichever it is, the panel that changes a sound should
  carry the side's letter in its colour in its title, as Chain and the Versions tool already do
  ("B · Plexi Crunch") and Shape, Sounds and the other tools do not (`shape-980.png`).
- **A2. Comparing blind leaves A and B swapped.** Half the time the two are exchanged, and they stay so after
  "Show which was which" (`studio.py:1066-1081`). The sound being worked on is then on A, the round's chips
  light none of their three, and the next request in words revises what used to be the reference. Put them
  back when the names are shown.
- **A3. A and B often have the same name.** A copy, a revision by a named setting, an IR or capture swap and
  an added layer all keep the name (`studio.py:846`, `:983`, `:1000`, `:1060`). After Copy B to A and one
  change, both tiles read "Plexi Crunch" and the only difference on the desk is a grey caption. The app can
  already say how two chains differ (`lab.sound.chain.differences`) and which knobs stand where. One line on
  B's tile, "against A: Tightness +0.15" or "against A: + delay", is a result, not an explanation, and is
  the thing the player is listening for.
- **A4. A design that missed its aim is not marked.** In `desk-second-design-1100.png` the aim is "crunch,
  balanced, dry"; the second design measured "saturated, bright, dry". The two phrases are 400 px apart in
  two greys, and the player is left to compare them. When a round arrives (`desk-round-1100.png`) the same
  three words are on the tile, under the chips, at the chips' right and in the status line: four places at
  once. Say it once, under the chips, and mark the words that miss ("aim crunch, balanced, dry · measured
  **saturated, bright**, dry").
- **A5. The caption calls an inference a measurement.** "measured: crunch, balanced, dry" are the app's
  words for what it measured, which is the project's own distinction (`AGENTS.md`, "Name what is measured").
  The Chain panel does it properly ("harmonics 15 dB below the tone …: crunch"). On the tile, "heard as
  crunch, balanced, dry" would be honest in as many words.
- **A6. Keep and Not this look like captions.** They are 9-point grey words with no edge, at the bottom of
  the largest object on the screen (`window.py:244-249`). In a blind comparison they are the answer the
  whole screen is waiting for, and the status line has to explain that they are buttons. Give them the look
  of the other buttons.
- **A7. Undo does not say what it takes back, and there are two of them** (beside Compare blind, and in
  Shape). It covers what is on A and B and the knobs; it does not cover a mark, a scene, the loop or a save.
  "Undo Tightness" or "Undo: Hiwatt Push on B" names it; one Undo, by the tiles, is enough.

## 4. Where things live

The top row has eight panel names of equal weight. They are five things (Chain, Phrase, Sounds, History,
Guitar), one activity (Shape), one result (Difference) and one drawer (More, with nine tools named after how
they work). A new feature has gone wherever there was room, and it shows:

- **Recording the guitar** can be started in five places: the Phrase panel, the Guitar panel (the same form,
  `window.py:861` and `:1267`), the Guitar Learner ("Record clip"), Teach a control ("Record Neck") and Idle
  reference ("Record muted strings").
- **The guitar's setting** is stated three ways: ten chips "Bridge · Series" … in Phrase and Guitar; a
  photograph with a blade, a toggle and two knobs in the Guitar Learner; five words "Neck, Bridge, Outer
  coils …" in Teach a control. The instrument is global; how it is set right now is asked for again at each
  recording, in a different form each time.
- **Comparing two sounds** is in seven places: the tiles, Compare blind, the Difference panel, "Measure A and
  B now" inside it, the Comparison workbench, "Remember a contrast" in Tone field, and "Hear it without the
  knobs" in Shape.
- **Changing a sound** is in seven: the ask line, Shape's pad and knobs, Tone field's six pads, the named
  settings in Chain, IRs and captures in Sounds, Add parallel space, and Tone search.
- **Levels** are in five: the matching the desk does by itself and never shows, the Levels tool, Setup and
  sound check, the line under the tiles while an offset is on, and "about 4 dB below music target" in the
  status line.
- **Going back** is in five: Undo, History, "Versions and the unit" (Older, Newer), the round's ‹ and ›, and
  "Earlier comparisons" in the workbench.

None of these is wrong by itself, and the side features stay. What is missing is a rule for where the next
one goes. Three merges are plainly duplication and can be made now; the rest should wait for the usage log
(P4 below), as the owner said of layouts: only use will tell.

- **I1. One place to record.** Phrase and Guitar are one panel's worth: the takes, recording one, what the
  instrument puts out, gestures. The phrase's name beside Save to QC already opens it.
- **I2. One statement of how the guitar is set.** Beside the instrument in the top row, or in that one
  panel: the pickup setting last chosen, kept until changed, used by every recording and by the suggested
  setup on the tiles. Each recording screen shows it and lets it be changed in place; none asks from nothing.
- **I3. "Sounds" does not hold the app's sounds.** The Sounds panel lists what is on the unit (presets, IRs,
  captures); the sounds the app made are in History. Call the first by what it is (the unit's), and let the
  second be the place for sounds.
- **I4. Mark the open panel.** The panel names never change (`window.py:207-210`): nothing in the top row
  says which panel is open, and the way to close one, clicking its name again, is not shown anywhere. Light
  the open panel's name as the round's chosen chip is lit.
- **I5. Name tools for what the player gets.** "Tone field", "Comparison workbench", "Versions and the unit"
  and "Usage and feedback" name mechanisms. "Measure the USB round trip" is a lone button in More at the
  weight of a main action; it belongs in Setup and sound check.
- **I6. A placement rule, written down.** See P1.

## 5. What the screen says

- **W1. One word, several meanings.**

  | Word | Means |
  | --- | --- |
  | listen | the app measuring a sound silently ("could not be listened to"); the player's monitoring volume ("Listening level"); the Spotify history ("Songs you listen to"); gestures ("listens for 20 s") |
  | reference | a preset kept for Tone search; the idle recording of a guitar; the music level; the sound on A |
  | setting | a pickup position; a model's named settings; "Choose the setting first" |
  | keep | a mark on a sound ("Keep", stored and said back as "keeper"); "Keep this note"; a reference "kept" |
  | versions | earlier states of a sound; a capture's V1 or V2 |
  | phrase, take, clip, recording, example | the same thing, a stretch of dry guitar, in Phrase, the Guitar Learner and Teach a control |

  A short list of the app's nouns, one meaning each, kept beside `AGENTS.md`, would settle these as they
  come up. "Measured silently" for the first sense of listen and "volume" for the second would remove the
  worst.
- **W2. The Chain panel leads with the laboratory.** Under two lines of chain come thirteen lines such as
  "Harmonic spread: weighted by order (n²/4) −11 dB against the tone, 0.2% of the power in the 5th–10th,
  centre at harmonic 1.07: texture warm." (`chain-980.png`). They are exact and correctly named, and they
  are what the rule about few words exists for. Show the three words that are drawn from them and put the
  lines behind "Measured ▾", as the Nano Cortex sheet already is. That also gives the chain itself, which is
  what the panel is named for, its room back.
- **W3. The app reports on itself in its own terms.** "Measured with the knobs, against without: Tightness:
  low end +0.0 dB, too small. Tightness turned further, from +0.15 to +0.24, once; not measured again."
  "B's Shape knobs were corrected after they were measured, and are not measured again by themselves: turn
  one to have them measured where they stand." (`difference-1100.png`). The player asked for "a bit
  tighter". What happened, in their terms: "Tighter did little here, so it was turned further (+0.24)."
- **W4. Units and words nobody says.** "Stereo sides +0.0 pp", "Tail continuity −7.9 pp", "Variation
  +11.7 dB" in Difference; the band chart's labels have no Hz, and its two colours (B's for more in B, A's
  for more in A) are a good idea that no key explains.
- **W5. A button that is a sentence.** With no pickup setting chosen, the record button reads "Choose the
  setting first" and looks ready to press (`window.py:1232`). Elsewhere a control that cannot act yet is
  dimmed ("Learn settings", "Newer"). With I2 the case goes away.
- **W6. Things only the README tells.** Typing a preset's exact name brings it in. Typing the same remark
  again tries the next remedy. `Solo +2 dB` in a part's name sets a level offset. Typing "add parallel
  space" presses a button. Each is a convention with no trace on the screen. Where a convention has a result
  the screen can show (R2's "Next: less gain"), show that; where it does not, it is a hint beside the
  control it belongs to.

## 6. Layout and hierarchy

- **L1. The largest things say the least.** With no panel open the two tiles take about 60% of the window
  and hold a letter, a name and one grey line; about 300 px of each is empty (`desk-round-1100.png`). With a
  panel open the same tiles are 300 px wide (240 at 980) and the panel beside them is cut off. Large tiles
  are right as targets. They can also carry the two lines that are now a panel away: the chain in one line
  ("Brit Plexi 50 → 412 Greenback") and A3's line of what differs.
- **L2. The panel is one width for everything.** 430 px for nine chips and a button (More) and for a column
  of text that needs scrolling (Chain). The tiles pay for it either way.
- **L3. The desk jumps.** A status of two lines makes the tiles 18 px shorter; the save row pushes them up
  again; the level row and the scene row come and go. Each is small; together the things being clicked move
  under the pointer. Reserve the height of two status lines, and put Save's name in place of the row it was
  called from instead of under it.
- **L4. The prime corner holds the rarest thing.** Top left is the learning symbol, then the instrument; the
  ask line, used in every session, is at the bottom edge. The instrument earns its place. The learning
  screen is a side feature that could open from the guitar's own panel.
- **L5. Small targets.** The `?` is one 8-point character in the dimmest colour. The round's ‹ is a
  single 10-point character. Scene chips, Keep and Not this are 9-point words with 2–3 px of padding.
- **L6. Controls with nothing to act on.** Copy B to A, Compare blind and Save to QC are on show on an empty
  desk and answer with a sentence. Undo already appears only when there is something to undo; these three
  can follow it.

## 7. Colour, type, states

- **V1. The accent is B's colour.** `ACCENT` and `TONE_B` are both `#f7b889` (`theme.py:12`, `:18`). So
  Cancel, ‹ Back, every disclosure line in the tools, the pad's dot and "› next three" wear B's identity,
  and on the learning screen the same colour fills both the selected choice ("Blade + toggle", "5",
  "Parallel") and the main action ("Listen live", "Record Neck"), so that "Listen live" looks as if it were
  on. `theme.py` says colour carries identity and state and is never emphasis alone; give the accent a
  colour of its own and keep peach for B.
- **V2. Selection has three looks.** On the desk and in the tools a chosen chip is a lighter tile with bold
  text; a round's chosen design is tinted with B's colour; on the learning screen it is a solid peach
  block. One look for "chosen" and another for "do this" would cover all three screens.
- **V3. The dimmest text is too dim.** `DIM` (`#71758a`) is 3.6:1 on the panels and 2.9:1 on a tile, at 8
  and 9 points; 4.5:1 is the usual floor for text of that size. It is used for the `?`, "Row 1", "Measured",
  "Scene", the aim, the pad's axis words and a disabled Newer. `SUBTLE` (6.7:1) is fine and could take its
  place for anything meant to be read.
- **V4. Surfaces barely part.** Page to panel is 1.12:1, panel to tile 1.23:1. An empty A tile and the page
  are nearly one surface, and a button is told from the panel behind it by its bold label more than by its
  edge. A one-pixel rule in `RULE` round the tiles and buttons would do it without more colour.
- **V5. Too many sizes, too small.** The desk, tools and learner name eight font sizes from 7 to 15 points
  outright; half of those places set 9-point and a sixth set 8. A scale of four (9, 10, 12, 14, with 34 for
  the letters) and nothing under 9 would read better from where a player with a guitar on sits.
- **V6. The sliders have no zero and no value.** Shape's knobs are bare tracks (`shape-980.png`): the ones
  that go both ways rest in the middle and the ones that go one way rest at the left, with no mark for
  either and no number. The pad says "Brightness +0.00 · Texture +0.00"; the knobs under it say nothing.

## 8. Lists at scale

The lists were built for a handful of rows and the data grows by a round of three every few minutes.

- **S1. History is one flat list** of "name · designed", newest first (`history-1100.png`,
  `window.py:1110-1143`). Names repeat (A3), there is no date, no round or song to group by, no sign of Keep
  or Not this (only ★ for saved), and no search, though the search of the Sounds panel is a few lines away.
  Above the list, in the same panel, are the songs and what the choices say: three different things under
  one title. Group the sounds by the request they came from, newest request first, with the marks shown;
  that is the round the player remembers.
- **S2. Lists that stop without saying so** (F6): eight phrases, 400 captures.
- **S3. A take is "Charvel · Neck · 11-20 10:00".** Not what was played, not how long, and no way to drop a
  bad one.

## 9. The learning screen and the side tools

- **T1. The learning screen is the best-finished screen in the app** and the most different from the rest:
  its own selection colour (V1), its own tabs, its own words for a recording (W1), its own way to set the
  guitar (I2), and the desk's last status line carried along at the bottom whatever it said (F9).
- **T2. The side tools are sparse at rest** and use sections, hints and disclosures the same way. Their
  weak points are names (I5), the second status line (R8) and that no tool says which side it is about
  except Versions (A1).
- **T3. Two pads.** Shape has a Brightness × Texture pad; Tone field has six pads over the same knobs with
  other axis names ("Cleaner, Saturated, Rounded, Cutting"). They are one idea at two depths; Tone field
  could open from Shape's pad instead of from More.

## 10. Keeping the screen in step with the features

The findings above are the cost of adding features one at a time with every check local to the feature.
Four changes to how work is done would keep them from coming back. They are small.

- **P1. A placement rule in `AGENTS.md`.** Before a control is added: name what the player is doing when
  they need it, and find the control that already serves that. A new feature extends that place or replaces
  something in it. A new panel name, a new entry in More, or a second way to do something that exists needs
  a sentence in the change saying why the existing place could not hold it.
- **P2. One command that pictures every screen.** `AGENTS.md` already says to look at every screen that
  changes, at both sizes. This review needed a script of about 200 lines to do that once. Kept in `tests/`
  as a gallery (the states above, both sizes, into a folder git ignores), it makes "look at the whole desk"
  a single run for every agent session, and the pictures of two commits can be laid side by side.
- **P3. A height test for the panels.** The tools are tested for width at both sizes. The same for each
  panel, with a sound that has been listened to: the last control lies inside the panel or the panel
  scrolls. F3, F4 and the Cancel of F1 are of this kind.
- **P4. Let the usage log see the desk.** It records the side tools opening and closing
  (`tools/common.py:616`) and nothing else: not which panel was opened, not Guitar or Phrase, not how a
  request was read, not Undo straight after one. Those four answer most of sections 3 and 4 from use
  instead of from argument: which panels are opened together, which requests are taken back, which tools
  are never reached. Usage is a reason to move a thing nearer or further, never to drop it.
- **P5. A word list** (W1): the app's nouns, one meaning each, checked when text is written.

## 11. Words where something else would say it

The first ten sections judge the words. This one asks which of them should be words at all. The player has a
guitar on: both hands are busy, the eyes are often on the fretboard, and the screen is at arm's length or
further. A 9-point sentence is the most expensive thing to give that player, and a click is the second most
expensive, because a hand has to leave the strings for the mouse.

### What the count says

Words and controls on show, counted from the widgets of the hidden window (labels, buttons, list rows, text
drawn on a canvas), with the made-up round of section 1 on the desk:

| Screen | Words | Controls |
| --- | --- | --- |
| The desk, empty | 51 | 16 |
| The desk, a round on show, A and B | 97 | 25 |
| Chain panel | 251 | 0 |
| Guitar panel | 204 | 17 |
| Phrase panel | 88 | 18 |
| Teach a control | 108 | 22 |
| Guitar Learner | 101 | 19 |
| Every other panel and tool | 10 to 54 | 2 to 18 |

The resting desk nearly doubles its words when there is something on it, and two panels are pages of prose
with nothing to press.

The lines can say about 166 different things (every `say`, `status=` and working line in `lab/desk`,
`lab/tools` and `lab/learner`; a count from the source, so near enough). Of those:

- **About 38 are "not yet".** 27 end in "first." ("Choose a preset in the list first.", "Record or choose a
  phrase first.", "Measure music first.") and the rest begin "Nothing …" or say what is needed. Each is the
  answer to a press on a control that looked ready.
- **About 33 are progress** ("Playing the phrase silently through each part…", "Getting A and B ready on
  the Quad Cortex…"): a sentence for the fact that time is passing.
- **About 15 of the desk's own restate the click that caused them**: "Stopped.", "Undone.", "Scene B",
  "B · Hiwatt Push · phrase", "Gestures off.", "Plexi Crunch: keeper", "Plexi Crunch is on A too, to compare
  the next change on B against."

That leaves about half that carry something the screen does not already show: results, faults, and what the
assistant decided.

### N1. A rule for what gets a word

A word is kept for what only a word can say:

- a name somebody gave: the sound, the request, the designer's line on how a design differs;
- an abstract quality: the Shape knobs ("Jazz", "Band fit" have no picture);
- an action that is rare and has consequences: Save to QC;
- a fault, and what to do about it.

Everything else has a cheaper form: a **state** shown on the thing itself, a **symbol** for an action
everybody knows, a **mark** on a scale for a value, **motion** for time, a **default** so that the control
is not needed, or a **drag** so that the target need not be named. A symbol is used alone only where it is
universal (play, loop, record, stop, undo, heart, ✕, eye, pin, search, warning); `lab/desk/hints.py:named`
already gives a drawn symbol its name while the pointer rests on it, and `lab/desk/pictures.py` already
draws the app's own. Windows ships an icon font (Segoe MDL2 Assets, Segoe Fluent Icons), so the common
symbols need no image files; how they look at 1100×680 is a thing to check in a picture, not to assume.

### Controls a default would remove

- **D1. Guitar is the resting state, not a button.** The player came to play. Today the guitar is connected
  only after Guitar is pressed, and the app takes it away in seven places and gives it back in two: Stop;
  Save to QC (`studio.py:1112`); recording a phrase (`:1215`); every measurement a tool or panel makes
  (`tools/common.py:546`); the Guitar Learner taking the input (`studio.py:758`); a preset chosen by hand
  on the unit (`:440`); a failure (`:560`, `:623`). It comes back by itself only when A and B are made
  ready while it was still on (`:547`) and beside gesture listening (`:754`). So Guitar is pressed over and
  over to put back what the app removed. With the guitar live whenever A or B is ready, the button becomes
  a lamp (the instrument at the top left, lit in the live colour) and one control to pause.

  Three things have to hold, and none was tried on the unit:
  1. *Silent listening needs the unit.* It disconnects the input and builds the sound somewhere else
     (`engine.py:361-367`), and today it starts once nothing has played for two seconds. With the guitar
     always on, "nothing plays" has to be heard, and in Guitar mode the app opens no audio driver: the route
     is made on the unit (`engine.py:173-215`). Gesture listening does watch the dry input while the guitar
     plays (`studio.py:66-69`), so the means exist. The listen would start when the strings have been quiet
     for a few seconds and stop at the first note, at the cost of the half second a recall takes.
  2. *A preset chosen by hand on the unit is left alone*, as now. Clicking a tile is the way back; that
     replaces "Press Guitar or Phrase to come back."
  3. *A pause stays*, for tuning, talking, or using the unit by hand.
- **D2. Phrase is a transport, not a mode.** The phrase can only loop (`hearing/audio.py:204`, "round and
  round"). Three symbols say more than the one word: ▶ plays it once through the selected sound and gives
  the guitar back; ⟳ loops it; ⇄ loops it and changes side at each pass, which is an A/B comparison with no
  hand on the mouse and is what a blind comparison wants too. ● beside them records (D5). The phrase itself
  is the strip to their right: its shape, the part that loops, the place it is at. That strip replaces the
  words "Phrase · Neck", is the loop editor of the Phrase panel moved to where the phrase is played, and
  gives the row a playhead, which is the one piece of motion the desk most lacks.
- **D3. A is "before" unless it is pinned.** Copy B to A exists so that a change to B can be compared with
  what B was; the app's own sentence says so. The app knows when a change is about to be made. If the
  sound B had goes to A by itself whenever B is revised (and never when A is pinned or was put there by
  hand), the usual use of the control is gone, and "is this better than before" is always one click on A.
- **D4. A control that cannot act is dimmed.** That is the 38 sentences. The app already dims in places
  ("Learn settings", "Newer", Guitar and Phrase while the input is held); the reason goes on the dimmed
  control's hover, where it is read only by somebody who wonders.
- **D5. Recording takes one press.** With the guitar's setting kept (I2), ● records on the setting last
  used. "Choose the setting first" (W5) and the form of ten chips before every take go away; the setting
  can be corrected on the take afterwards.

### Dragging, and no hands

- **G1. The tiles take whatever is dropped on them.** B onto A is Copy B to A. A chip of the round onto A
  keeps that design to compare the next against, which today takes three clicks (the chip, Copy B to A, the
  next chip). A row of History, a preset, an IR or a capture onto a tile puts it there. That removes six
  buttons (Copy B to A, Put on A, Put on B, Work on this, Put in B's cab, Instead of B's amp) and it answers
  section 3 without a rule: what a thing acts on is where it was dropped. Dragging is easy to miss, so three
  things go with it: the pointer changes over what can be dragged, both tiles light as targets the moment a
  drag starts, and one click path stays (the arrow between the tiles; a double click on a row, as the
  presets list already has). In this toolkit it is a press, a motion and a frame that follows the pointer
  inside the one window, like the list of the instrument chooser; the pad and the loop's edges are already
  dragged.
- **G2. The wheel steps through the round** while the pointer is over its chips or over B. It is a pointer
  gesture, not a key.
- **G3. The comparison that matters most is made with no free hand.** Flipping between A and B while
  playing is the app's central act and needs the mouse each time. D2's ⇄ covers it for the phrase. For the
  guitar the app already listens for taught gestures, but only to turn one Shape knob, and only for 20
  seconds after a button in a panel. Whether A and B can be flipped from a footswitch of the unit, or by a
  gesture on the muted strings, is worth finding out on the unit before anything on the screen is polished:
  nothing here is known about it.

### Marks instead of sentences

| Now, in words | Instead |
| --- | --- |
| "measured: crunch, balanced, dry" on the tile, again under the chips, "aiming for crunch, balanced, dry" at the right, "Aiming for …" on the status line | Three short scales on each tile (gain, bright, tail): a ring where the aim is, a dot in the side's colour where the sound was heard. A miss is a gap between ring and dot, seen without reading (A4). The words stay on hover. |
| "Brit Plexi 50 Normal", "412 Brit 60B GB 71 (M)" as lines in a panel | The chain as a row of block symbols on the tile, as the unit itself draws it; a block that differs from the other side is outlined (A3). A click on a block opens its settings. |
| " · not listened to yet", " · could not be listened to", "Listening to Plexi Slap, silently…" | A pip on the design's chip: hollow, filling while it is listened to, full, or a warning sign with the reason on hover. |
| "● guitar", "B · Hiwatt Push · phrase", "Stopped." | The lit tile and the transport already say it. |
| Keep, Not this | A heart and a ✕ at the tile's corner, filled when set (A6). |
| Copy B to A, Compare blind / Show which was which, Undo | Three symbols between the tiles: an arrow, an eye that is struck through while names are hidden, a turned-back arrow whose hover says what it undoes (A7). |
| "Revised: a copy to compare against", "shaped", "saved as Lead 2" | Small marks on the tile: a knob when the knobs are off zero, the slot ("3A") when saved. The first says nothing the player did not just do. |
| "Getting A and B ready on the Quad Cortex…" | The tiles drawn hatched until they are ready. |
| Thirty-three working sentences | A ring on the thing being worked on: B's tile for a request, the pip for a listen, the list for a read of the unit. One short line stays for which. |
| "Opening the input… wait for “play now”.", "Recording 8 s · play now (you hear B)…" | A lamp that turns red when the input is open, a ring that drains in eight seconds, the input's level as a bar. A sentence cannot be read while counting in; a colour can. |
| "Gestures off · armed 17 s", rewritten every second | A ring that drains. |
| Four paragraphs under "What the Charvel puts out" | One bar per pickup setting against a line where "the humbucker most presets assume" is, and a tick for the noise. The paragraphs are the numbers of that chart written out. |
| Thirteen lines under Measured in Chain (W2) | Two small charts the app half has: harmonics by order, level by band (`tools/field.py:draw_bands`). The exact numbers on hover. |
| "Brightness −1.0 oct · Variation +11.7 dB · Stereo sides +0.0 pp · Tail continuity −7.9 pp" | Four signed bars from a centre line, in B's colour to the right and A's to the left, as the band chart beside it already does. |
| Scene chips "A  Rhythm", "B  Lead" | The unit's row of eight footswitches, the chosen one lit, with the scene's name. |

- **N2. Scenes and sides share their letters.** Scenes are drawn as A to H (`window.py:445`) directly under
  two tiles called A and B, and the status line says "Scene B" while B is also a side. Drawing scenes as
  the unit's footswitches ends it.
- **N3. Time is shown nowhere.** The phrase has no playhead, a listen of 25 seconds and a request of a
  minute have no progress, a recording has no count, gestures count down in rewritten text. The window's
  clock already ticks every 80 ms (`window.py:1339`); a ring or a bar drawn on a canvas at that rate is
  within the toolkit. Fades and slides are not, and are not needed.
- **N4. Colour says three things and could say six.** Blue and peach are the sides and green is on air.
  Free to take: red for recording, one colour for a fault or a refusal (R7), and a quiet grey for "the unit
  is yours, the app is leaving it alone". Each colour goes with a change of shape as well, so that it
  never stands alone.

### The desk this adds up to

```
[● guitar picture  Charvel  ●○○○○]                         Shape   Sounds   Unit   More
+-------------------------------+  <-  +-------------------------------+
| A                    pin ♥ ✕  |      | B                        ♥ ✕  |
| Plexi Crunch                  |  eye | Hiwatt Push                   |
| [amp] > [cab]                 |      | [drive] > [amp] > [cab]       |
| gain   ---(o)-----            | undo | gain   ---( )------o--        |
| bright -----(o)---            |      | bright -----( )-----o-        |
| tail   (o)--------            |      | tail   (o)--------            |
+-------------------------------+      +-------------------------------+
[● 1 Plexi Crunch] [● 2 Hiwatt Push] [○ 3 Plexi Slap]  [› 3 more · 1 min]
[live] [▶] [⟳] [⇄] [●]  [ phrase: shape, loop, playhead ............ ]  [Save to QC]
[ Ask for a sound, a song, or a change                                             ]
```

About thirty words where there are 97, and every one of them a name. Chain and Difference are no longer
panels to open, since the tile shows the chain and the two tiles show the difference; Phrase and Guitar are
the strip and the instrument; that leaves four names at the top right where there are eight. This is one
frame of a direction to argue with, not a specification. It keeps the resting desk's rule: nothing in it
explains anything.

### What this cannot settle

Nothing in this section was heard or tried on the unit. D1 depends on hearing that the strings are quiet,
D2 on a phrase that ends, G3 on what the unit's footswitches can be given to do. Symbols and marks have to
be looked at in pictures at both sizes before they are believed. And a mark that replaces a sentence must
still name what is measured: the three scales show what the app took the sound to be, and their hover
keeps saying so.

## Order of work

1. **The faults** F1–F9. Hours each; F1, F3 and F4 block things outright.
2. **The request loop** R1–R5 and R7: the placeholder, the request kept on show, the reading said at once,
   the working line and Cancel above the ask line, the seconds, three looks for the line.
3. **Fewer sentences without new machinery**: dim what cannot act (D4), the pips on the chips, the heart
   and the ✕, the three symbols between the tiles, the transport's symbols over what exists (loop and
   stop), drop the lines that restate a click.
4. **A and B** A1–A4, with D3 and the drops of G1: what a change acts on, blind comparison putting things
   back, the scales with aim and heard, the chain on the tile.
5. **The process** P2–P4, before any regrouping, so that the regrouping is looked at whole and argued from
   use.
6. **On the unit, with the player**: guitar live by default (D1), play once and alternate (D2), flipping
   A and B with no hand (G3). These change what the engine does and need hearing.
7. **The merges that are plainly duplication** I1–I4, then words (W1–W5) and looks (V1–V6, L1–L6).
8. **History and the other lists** S1–S3.

What should not change: the resting desk, one window, hints behind a `?`, disclosure in place, the unit's
rules, the side features themselves.
