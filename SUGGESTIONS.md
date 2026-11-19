# Suggestions: several checked designs, and learning from what you say

Branch `suggestions`. Updated 2026-11-19 after the player's answers. Listening and the round itself are built and run from the command line. Nothing here is in the desk yet.

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

## What is left to build, in order

1. **Serve a round in the desk.** Ask, show the aim on the line, build the first design and put it on B (about 65 s after asking: 54 s for the call and 11 s for the build). Three small markers under B step between the designs of the round. Each shows its one-line difference and its tags. Scrolling there goes back through earlier rounds.
2. **A round as one preset.** Put the three designs on separate rows of one preset in a Tone Lab slot, so that stepping is instant. This needs the builder and the Shape knobs to work on one row of a preset, which they do not yet. A design with a second path takes two rows, so a round with one of those holds two designs in the preset and the third is a recall away.
3. **Listen after serving.** The design you are playing cannot be replayed over USB at the same time. It is listened to when the input next stops, and the other two as they are built.
4. **Review.** Right-click a design: a few words, or a tag. Your knob moves on a design are kept with it. All of it goes to the next round.
5. **The next three, prepared while you play.** Once the first round has been listened to, the assistant designs the next round with what was measured and anything you have said, and may search the web this time. That is a second call per request. If all three were on the aim and you have said nothing, it waits for you. Unheard designs of the earlier round give way to the new ones.
6. **Build in the pauses.** Watch the guitar's level and add blocks only while it is silent, so that the next designs are ready without your stopping. Until this exists, they are built when the input stops, or when you step to one, which is a wait of about 11 s.

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
