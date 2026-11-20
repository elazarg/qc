# Suggestions: several checked designs, and learning from what you say

Branch `suggestions`. Updated 2026-11-19. Listening, the round and its place in the desk are built. Nobody has yet played through a round.

## What this is for

A request used to give one design, written by an assistant that never hears it. The request for the solo of "Time" showed what that costs: the right gear on paper, a tame sound, and knobs that could not rescue it.

The aim now:

- A request gives three designs that differ on purpose. You can play the first as soon as it is built.
- The app listens to each design by itself, tags it, and ranks the three.
- What it measured, and what you say and do, shape the next three, which are prepared while you play.
- You see three at a time. Earlier ones are a scroll away.
- Each design carries what the app heard of it, your own tags, and its own knob positions.
- A design you have not heard is the system's to replace. A design you have heard is yours.

No recording of a target is needed. The target is described, not recorded.

## Decided

| Question | Answer |
| --- | --- |
| How many at a time | Three. |
| Serve the first when ready, or wait for all | Serve as soon as it is built. Listening, ranking and the next three come after. |
| Use more slots | Yes. The Tone Lab setlist is all Tone Lab's. |
| Knobs | Each design has its own state: the app's tags, your tags, your review, its knob positions. |
| Show the aim and wait | No waiting. See below. |

**About the aim.** Before it designs anything, the assistant now says what it thinks the sound is, in three words the app can measure: how driven, how bright, how much space. For "Time" it said "overdriven, balanced, ambient". The question was whether the desk should pause there so that you can say "no, far more distorted" before anything is built. It should not: it shows the aim on the line and carries on, and a correction from you goes into the next three.

## What the unit allows, measured

"Built in the background" depends on what the Quad Cortex can do while it is sounding. A steady tone was run through an amp on row 1 while the grid was edited (2026-11-19, silent):

| While a sound plays | What happens to it |
| --- | --- |
| A block is added or removed, on any row | All audio stops for about 0.2 s |
| A setting is changed | Nothing |
| The preset is read, or saved to a slot | Nothing |
| A scene is switched | Nothing |
| The input is moved from one row to another | A dip of about 20 ms |

Two things follow.

- **A new chain cannot be built while you play without stutter.** Eight blocks are eight holes of a fifth of a second. Building has to happen when you are not playing: before a design is served, when the input is stopped, or in the pauses between phrases if the app watches the guitar's level, which it does not do yet.
- **Designs that sit on different rows of one preset switch instantly.** Three single-path designs fit on three rows. Stepping between them would take 20 ms, where recalling another preset takes two to three seconds. So a round can be one preset in one slot, and "more slots" means a slot per round.

Designing costs no unit time. The assistant can prepare the next three while you play.

## What is built

**Listening** (`tone_lab/hearing.py`). Builds a draft silently, replays your phrase at its own level and 12 dB quieter, and says how driven, how bright and how ambient the result is. 26 s per draft.

**A round** (`tone_lab/rounds.py`, `preset_design.design_round`). One call returns the aim and three designs with a line each on how they differ. The app then listens to each, and where one misses the aim it tries its own Shape knobs (Drive, Warmth, Space), at most three extra listens per design. It keeps the knob positions that came closest as that design's own, tags it, and puts the closest first. A later round is told the earlier aim, each design, what was measured, which knobs moved, whether you heard it, and what you said.

The first real round, for "David Gilmour - Time (Solo)":

| | Design | As designed | After the app's knobs | Verdict |
| --- | --- | --- | --- | --- |
| Aim | | overdriven, balanced, ambient | | |
| 1 | RAT into a clean Twin | overdriven, bright, ambient | Warmth +0.8: overdriven, balanced, ambient | on the aim |
| 2 | Fuzz Face into the Hiwatt | saturated, balanced, wet | Space −0.4: saturated, balanced, ambient | more driven than the aim |
| 3 | Solo 100 lead channel | saturated, bright, ambient | Drive −0.4: saturated, balanced, ambient | more driven than the aim |

The call took 54 s and listening to all three took 164 s. The Fuzz Face design, which came out at the edge of breakup the day before, now measures saturated as designed. Whether "overdriven" or "saturated" is the truer word for that solo is exactly what your reaction should settle. Nobody has heard these three.

`python -m tone_lab.rounds "<request>"` runs a round. `python -m tone_lab.rounds --again <round id> "<what you think>"` runs the next.

## In the desk

Built on 2026-11-19 (`tone_lab/suggestions.py`), checked in the real window and, for listening, on the unit:

- A request for a new sound returns a round. The aim is said on the line and the first design goes on B.
- Three markers under the tones step between the designs (keys 1, 2, 3). One line says how the design on B differs, what the app heard of it and what you said. ◆ marks the one that measured closest to the aim.
- Each design keeps its own knob positions. Stepping away stores them and stepping back restores them. A design is marked as heard once it has been on air.
- The app listens when the unit has been free for 4 seconds after playing, or 25 seconds after a round arrives. Guitar, Phrase, Space or a step takes the unit back: measured, 3.9 s after asking. The knobs the app finds for a design become that design's own only if you have not heard it yet.
- Right-click a marker: say what you think, or mark it Closer, Not it or This is it.
- The next three are prepared once without being asked, when the round has been listened to and a design missed the aim or you said something. "Three more" asks again at any time. They are offered on the line when ready and nothing moves until you choose.
- Earlier rounds: mouse wheel over the markers, [ and ], or the arrows. B stays as it is until a design is chosen.

## What is left to build, in order

1. **Quick stepping.** Stepping to a design while you play rebuilds it, about 17 s. Storing each design in its own slot at the end of the Tone Lab setlist when the app listens to it would make a step a recall, two to three seconds.
2. **A round as one preset.** Three designs on separate rows of one preset step in 20 ms. This needs the builder and the Shape knobs to work on one row of a preset.
3. **Build in the pauses.** Watch the guitar's level and add blocks only while it is silent, so that designs are built and listened to without your stopping.
4. **Words in the request line.** "More like the second but brighter" should be read as a review and a request for three more. Today that takes a right-click.

## Rules

- A design you have heard is never replaced or removed by the system. A design you have not heard can be.
- One call for the first three and one for the next three. After that, a call only when you ask.
- Everything built for checking is silent.
- Rounds are kept on disk: the request, the aim, the designs, what was measured, what you said and did.

## Later: evidence from real songs

A guitar stem from Studio Pro's stem separation could be listened to for the same three facts and replace the assistant's opinion of the aim. The 12 dB test cannot be run on a recording, so drive would have to be judged from how flat the stem's peaks and sustain are, which is a weaker measure, and separation changes brightness and ambience by an amount nobody has measured here. Not designed yet.

## Not proposed

- The assistant calling itself in a loop until a score improves.
- Any score for how good a sound is. The app measures how driven, how bright and how ambient. Whether it is right stays with you.
- Removing the single-design request. A plain "make it tighter" should stay as quick as it is.
