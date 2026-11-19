# Suggestions: several checked designs, and learning from what you say

Written 2026-11-19 on the branch `suggestions`. This is a proposal. Only the first part, listening, is built. The rest is here to be corrected before any of it is.

## What this is for

Today a request gives one design, written by an assistant that never hears it. The request for the solo of "Time" showed what that costs: the right gear on paper, a tame sound, and knobs that could not rescue it. The player then has to do the judging and the repair.

The aim is the other way round:

- A request gives a short series of designs that differ on purpose.
- The app has listened to each one before you do, and has repaired or dropped the ones that missed.
- You react to the ones you hear, in a few words or by turning knobs.
- The next designs use those reactions.
- A design you have not heard is the system's to replace. A design you have heard is yours.

None of this needs a recording of the sound to aim for. The target is described, not recorded. Evidence from real songs is a later, separate step (see the end).

## Listening, which is built

`tone_lab/hearing.py` builds a draft in the working slot with nothing connected to the outputs, replays your recorded phrase through it twice, and says what came out. The second pass is 12 dB quieter. A chain that adds no distortion returns it 12 dB quieter too. A saturated chain returns it almost as loud. That measure does not depend on the phrase, on equalization, or on delay and reverb.

It takes 26 seconds per draft, measured, of which about 11 are the build. Your saved drafts, listened to on the unit on 2026-11-19:

| Draft | Asked for | Heard |
| --- | --- | --- |
| Time Solo | Gilmour's solo on "Time" | edge of breakup, dark, ambient |
| Time Solo with Drive at full | | saturated, balanced, wet |
| Europa Bloom | Santana, "Europa" | saturated, balanced, dry |
| Timmons Bloom | Andy Timmons, "Electric Gypsy" | overdriven, balanced, wet |
| Steel Chug | Pantera, "Walk" | saturated, bright, dry |
| Goldtop Bite | the solo of "Are You Gonna Go My Way" | crunch, balanced, dry |
| Stone Pulse | "Like a Stone", verse | edge of breakup, balanced, a touch of room |
| Dry Chime | "High and Dry" | edge of breakup, bright, a touch of room |
| Matinee Glass | Plini-style clean arpeggios | clean, balanced, ambient |
| Mellow Wide | Pat Metheny, clean | clean, balanced, a touch of room |

The first row is the point. Had the app listened, it would have known that a fuzz solo came out at the edge of breakup and dark, before you heard a note.

What listening can say: how driven a sound is, how bright, how much low end, how much ambience, and how loud. What it cannot say: whether the sound is good, whether it is like the record, or how it feels under the fingers. Those stay with you.

`python -m tone_lab.hearing <draft id>` runs it. Nothing in the desk uses it yet.

## The proposed flow

**1. Ask.** One call to the assistant returns a brief and three designs.

- The brief says what the sound is in the words listening uses: how driven (clean to saturated), how bright, how much space, and one sentence on what matters most about it. The assistant commits to it before it designs anything. The desk shows it in one line, for example "Aiming for: saturated, bright, some echo", so that a misunderstanding costs you a word and not a minute.
- The three designs differ on purpose: one from the documented gear, one from whatever on this unit measures closest to the brief, and one by another route, such as another family of amp. Each carries one line on how it differs from the others.

**2. Check.** The app builds each design silently and listens.

- A design that misses the brief on drive is first corrected with the app's own Drive knob and listened to again. That costs 15 seconds and no call.
- A design that still misses, or misses on tone, is dropped, or sent back once with the measurements. At most one repair call per round.

**3. Serve.** The first design that passes is offered as soon as it is ready. The others arrive on the line as they pass. You step through them on B. Each shows its one-line difference and what was heard of it. A stays where it is, as the sound to compare against.

**4. React.** Keep stays as it is. Review is new: right-click a suggestion and say a few words ("right amount of fuzz, too dark"), or use one of three marks: closer, not it, this is it. What you did with the knobs on a suggestion is recorded with the review. Drive turned to full is a statement.

**5. Again, when you ask.** "More like the second but brighter", or just "try again". The assistant receives the brief, each design in one line, what was heard of each, your reviews and your knob positions. Designs from the earlier round that you never heard are dropped or revised without asking.

## Rules

- A design you have heard is never replaced or removed by the system. A design you have not heard can be.
- One call per round, plus at most one repair. No calls happen unless you asked for something.
- Everything the app builds for checking is silent. The unit can only hold one chain at a time, so nothing is checked while you are playing through it.
- Rounds are kept: what was asked, what was served, what was heard, what you said. That record is what later rounds, and later requests for similar sounds, learn from.

## What it would cost in time

Measured: a single design takes the assistant about 22 seconds, and one listen takes 26. Not measured: a call that returns a brief and three designs, which I would expect to take 40 to 60 seconds.

So the first suggestion would be ready a little over a minute after asking, against about 45 seconds today for one unchecked design (22 s for the call and 20 s for the first build). The second and third would follow at about 26 seconds each, as long as you are not playing.

## Decisions that are yours

1. **How many per round.** Three is proposed. Two is quicker and gives less to choose from.
2. **Serve the first when ready, or wait for all.** Serving the first means you can start playing after about a minute, and the checking of the others then waits until you stop. Waiting for all three means about two minutes of silence and then no further waiting.
3. **More working slots.** Tone Lab keeps a stored copy of one draft per side, so stepping between A and B takes two to three seconds. Stepping to a third suggestion means a rebuild of about 17 seconds. If the app may use three or four more slots at the end of the Tone Lab setlist, every suggestion of a round steps in two to three seconds.
4. **Knobs per suggestion.** Proposed: each suggestion has its own knob positions, starting neutral. Stepping to another suggestion shows that one's knobs. Your moves are evidence for the next round and are not copied onto the other suggestions. The alternative is knobs that stay where you put them as you step, which compares designs less fairly and feels more like one instrument.
5. **The brief before building.** Showing "Aiming for…" and waiting a moment for a correction adds a step. It could also be shown without waiting, and corrected afterwards at the cost of a round.

## Later: evidence from real songs

You said a target can only come from the song itself, and that Studio Pro's stem separation is lossy but something. A separated guitar stem would not be matched note for note. It would be listened to for the same few facts as a draft, and those facts would become the brief's numbers in place of the assistant's opinion. Two limits are already clear. The 12 dB test cannot be run on a recording, so drive would have to be judged from how flat the stem's peaks and sustain are, which the "Time" measurements showed to be a weaker measure. And separation changes brightness and ambience, by an amount nobody has measured here. This is not designed yet.

## Not proposed

- The assistant calling itself in a loop until a score improves. The checks here are a gate with a fixed, small cost.
- Any score for how good a sound is.
- Removing the single-design request. A plain "make it tighter" should stay as quick as it is.
