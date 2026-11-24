# UX review of the desk

Written 2026-11-24 at commit `0046f6b`. Every screen was pictured hidden on the simulated unit with made-up
sounds (`tests/picture.py`), at 1100×680 and at 980×620: the desk empty, with a round, playing, blind, saving,
each of the eight panels, each of the nine side tools, and the three views of the learning screen. The flows
were then read in `lab/desk/window.py`, `lab/desk/studio.py` and `lab/desk/requests.py`. Nothing was tried on
the real unit, so timing and anything audible are not judged here. The pictures named below are in
`docs/ux-review/`.

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

The last one is why the first four keep coming back. Section 10 is about that.

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

## Order of work

1. **The faults** F1–F9. Hours each; F1, F3 and F4 block things outright.
2. **The request loop** R1–R5 and R7: the placeholder, the request kept on show, the reading said at once,
   the working line and Cancel above the ask line, the seconds, three looks for the line.
3. **A and B** A1–A4: one rule for what a change acts on, the side named in every panel that changes a
   sound, blind comparison putting things back, the line of what differs, the aim against what was heard.
4. **The process** P2–P4, before any regrouping, so that the regrouping is looked at whole and argued from
   use.
5. **The merges that are plainly duplication** I1–I4, then words (W1–W5) and looks (V1–V6, L1–L6).
6. **History and the other lists** S1–S3.

What should not change: the resting desk, one window, hints behind a `?`, disclosure in place, the unit's
rules, the side features themselves.
