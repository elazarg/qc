# Tone shaping research agenda

Written 2026-11-18. This is the plan for the work beyond the interface: what Tone Lab should be able to know and prove about one player's tone. It builds on `QUAD_CORTEX_TONE_RESEARCH.md`, which surveys how to match a reference. This document asks a different question and sets the order of work.

## Status on 2026-11-18

Work that needed no playing was done first and is recorded in [TONE_KNOWLEDGE.md](TONE_KNOWLEDGE.md): the unit's own named settings, what each control does by name, the community's vocabulary and remedies, and gear knowledge for every amp. Two things bear on the chapters below.

- Chapter 0 has a harness: `tone_lab/probe.py` builds a chain once and replays a dry phrase per condition. A condition costs the phrase plus about three seconds, against 42 s for a full capture. Its first run returned silence because the unit stopped sending audio to USB. After a reboot it ran on 2026-11-19: 32 conditions in about five minutes, with a repeated condition matching to 0.00 dB. The results are in TONE_KNOWLEDGE.md under "Measured on the unit".
- Chapter 3 has a prior. The knob rules now follow control roles taken from manuals and checked against Neural DSP's named settings. Measuring them on the unit remains the chapter's work.

## The question

**Can a tone request be treated as an experiment on the player's own playing?**

A request such as "the low end gets muddy when I palm mute" contains a claim about the sound, a part of the playing it applies to, and a direction. Tone Lab can already replay the player's dry performance through any chain on the Quad Cortex and record the result silently. So each request can become: a measurable prediction, a change, a re-measurement, and then the player's verdict. Nothing commercial does this for an individual guitarist. Text-to-tone products generate a preset and stop. Tone-match products copy a spectrum and stop.

This is single-subject research. The claims are about this player, this Charvel, this unit. That is the point of it and also its limit.

## What the instrument can resolve

Measured from two independent renders of the same shaped tone on the same phrase (`20261118-092754-knobs-294716` and `20261118-093016-knobs-021def`):

| Quantity | Test–retest difference |
| --- | --- |
| Level | 0.00 dB |
| Any of the eight bands, 90 Hz to 9.8 kHz | at most 0.04 dB |
| Brightness | 0.001 octave |
| Residual after aligning and subtracting | −40.7 dB |
| Start offset between renders | 20 samples |

For scale, two different tones on the same phrase differ by 4 dB in a band and 13 dB in level variation. The loop resolves differences about a hundred times smaller than the ones that matter. This is one pair on one chain. It needs repeating across chains with modulation and delay, where free-running effects will raise the floor. (A second identical pair in the sessions was bit-identical and is probably a cached file, so it was not counted.)

The binding constraint is time, not precision. A capture took 42 s today for a 10 s phrase, most of it building the chain. That is about 85 renders an hour. Every chapter below depends on making this faster.

## Chapters

Each chapter is a question, what is already known, what Tone Lab has that others do not, a first experiment, and what the player gets.

### 0. The instrument

*How fast and how trustworthy is the measurement loop?*

- Repeat the test–retest on five chains, including one with tremolo and one with delay. Report the floor per descriptor.
- Build a **fast render**: keep the chain resident (the monitor now does this for live flips), change parameters in place, and replay a short probe. Target under 8 s per setting, about 450 an hour.
- Record a **diagnostic phrase set** on the Charvel, about 25 s in total: palm-muted low riff, open chords, a single-note line played soft and hard, sustained bends, one phrase in each pickup position the player uses. Keep one phrase out of all tuning as a held-out check.
- Calibrate: fixed loop latency, input level against the unit's own meter, and the level at which the dry signal clips.

Without this, nothing else has a budget or an error bar.

### 1. Tone depends on what is played

*Can the sound be described per playing technique instead of as one average?*

Today's descriptors (`tone_search.describe`) are long-term averages over a whole phrase: eight bands, brightness, fizz, level variation, width, tails. "Muddy when I palm mute" cannot be expressed in them.

- Segment the dry track, which is clean and easy to analyse: onsets, pitch, single note or chord, register, picking strength, palm-muted or ringing.
- Measure the processed track per class. Add what averages cannot see:
  - **Low-end tightness**: how long energy below 200 Hz lingers after a palm-mute onset.
  - **Saturation**: harmonic energy relative to the fundamental on single notes, roughness on chords.
  - **Dynamic response**: output level against input level across soft and hard notes. This is most of what players call feel.
  - **Attack**: rise time and transient-to-body ratio.
  - **Fizz**: noise-like energy above 5 kHz that does not follow the harmonics.
- A tone becomes a table of technique against descriptor.

First experiment: take ten remarks the player has actually made about a sound and check whether each maps to a cell in the table that moved in the stated direction between the two versions being compared.

The player gets a Difference panel that can say "on palm mutes B carries 5 dB more below 200 Hz for 80 ms longer", and an assistant that is given those numbers.

### 2. Requests with evidence

*Can language be grounded in this player's own meanings and checked?*

Known: crowdsourced maps from words to settings exist for EQ and reverb (Social-EQ, Audealize). Text2FX steers effects from open text through a text–audio embedding. These learn what a population means by "warm". They do not verify the result on the listener's own material, and they cover linear effects far better than an amp chain.

Tone Lab's `styles.direction` table is a guess at what "tighter" or "too bright" mean. Replace the guess with data:

- Every request already has an outcome: kept, undone, or followed by a correction. Log the request text, the technique-by-descriptor table before and after, and the outcome.
- Before applying a change, state its prediction ("less energy below 200 Hz on palm mutes, chords unchanged"). After a silent re-render, report whether it held.
- Learn the player's vocabulary: which descriptor cells move when they say "bite", "jazz", "tight". Personal meanings will differ from the crowd's.

First experiment: fifty logged requests. Measure how often the predicted cell moved as predicted, and how often the player kept the result.

The player gets changes that come with a one-line proof, and a vocabulary that becomes theirs.

### 3. Knobs that do what they say

*What does each macro knob actually do on this chain, and can the side effects be removed?*

The 25 macro knobs are hand-written weights (`styles.KNOBS`). "Warmth" lowers treble and presence by fixed amounts whatever the amp. Nobody has measured what that does to loudness, saturation or attack.

- For the loaded chain, render each knob at two positions. With the fast render that is about 50 renders, under ten minutes.
- This gives each knob's measured effect in descriptor space: a local sensitivity map.
- Solve for combinations that move one quality and hold the others. "Brighter" that does not get louder or grittier. "Tighter" that leaves open chords full.
- Flag knobs that do nothing on this chain or have hit a limit.

Known: gradient estimates for non-differentiable effects by stochastic perturbation (Martínez Ramírez et al.). Here the number of controls is small enough to measure directly.

First experiment: for one chain, compare the hand-written "Warmth" with the solved one. Measure collateral change in level and saturation. Then ask the player to choose blind.

The player gets a Shape panel whose hover text is a measurement, and knobs that stay out of each other's way.

### 4. An atlas of the unit through this guitar

*What does every amp, cabinet and capture on the unit sound like with this player's hands?*

The unit holds 2,078 captures in a flat alphabetical list, plus the stock amps and cabinets. Their names say little.

- Render each one once through the diagnostic phrases in a fixed neutral chain, overnight and silently. At 8 s each this is under five hours. The job must resume after interruption.
- Store the technique-by-descriptor table and a short audio thumbnail for each.
- The Sounds panel becomes a map: neighbours of the current tone, "like this but tighter", "the cleanest three with this much body".

This also gives reference matching its first stage: choosing the amp and cabinet by retrieval instead of by name.

First experiment: fifty captures. Check that neighbours in the map sound like neighbours in a blind test.

### 5. Matching with the unit in the loop

*How close can a search get to a target, and how many renders does it take?*

`QUAD_CORTEX_TONE_RESEARCH.md` covers the approaches. What is missing is a benchmark with a known answer.

- **Hidden-target test**: pick a preset the search has never seen, render it on the player's phrase, and hide its settings. Search for it: atlas retrieval for amp and cabinet, a gradient-free optimiser over at most six continuous controls, then a fitted post-EQ. Stop when the distance is below the test–retest floor times a margin, or the budget ends.
- Score by measured distance on a held-out phrase and by whether the player can tell the result from the target blind.
- Only then move to targets from records, where the performance differs and the objective must ignore the notes (ST-ITO is the closest published method).

First experiment: five hidden targets, a budget of 100 renders each.

### 6. The guitar as the first block

*How do the guitar's own controls interact with the chain?*

Guitar Learner estimates pickup, volume and tone settings from the dry signal. Pickup position along the string and plucking point can be estimated from the spectrum of a note (Mohamad, Dixon and Harte).

- **Clean-up curve**: replay the same phrase at dry levels from −12 dB to +6 dB through each amp. Plot saturation and output level against input. This shows which amps clean up with the volume knob and which do not.
- **Pickup interaction**: the same phrase in each pickup position through the same chain. Which positions need a different pre-EQ, gain or cabinet?
- **Second guitar**: record the diagnostic phrases on the Les Paul. Fit the level and EQ difference between the two dry signals. Port a tone and check it on the held-out phrase.

The player gets tones that survive a pickup change, and a preset that carries to the other guitar with a measured correction.

### 7. Learning the player's taste

*Can past choices predict the next one?*

Every Keep now stores the tone and its shape. Blind comparisons give clean pairwise choices.

- Fit a pairwise preference model over the descriptor table once there are enough choices. Expect to need dozens before it beats chance.
- Use it to pick the next comparison. Sequential line search (Koyama et al.) asks the listener to choose a point on one slider, which is what the tone field's sweep already is.
- Hold out choices to test it. If it cannot predict held-out choices, it should not steer anything.

### 8. Other rooms

*Does a tone chosen at home survive rehearsal?*

- Hearing changes with level: the balance of lows and highs heard at home volume is not what is heard on stage. Measure the playback level once with a phone meter and offer a compensated variant.
- Fit in a mix: measure the guitar against a backing track and report where it is masked.
- Validate at rehearsal. This chapter cannot be finished at a desk.

## Hypotheses that could fail

| | Claim | Fails if |
| --- | --- | --- |
| H1 | Per-technique descriptors explain the player's remarks better than averages do | fewer than 7 of 10 real remarks map to a cell that moved the right way |
| H2 | Solved knobs cut collateral change by half against the hand-written ones | measured side effects are not halved, or the player prefers the old knob blind |
| H3 | A hidden target can be reached within the blind-indistinguishable range in 100 renders | the player still picks out the target in a blind test |
| H4 | Neighbours in the atlas sound alike | blind similarity judgments disagree with the map |
| H5 | The preference model predicts held-out choices | accuracy below 65% after 100 choices |

## Order

1. **Chapter 0**: fast render, diagnostic phrases, floor per descriptor. Everything depends on it.
2. **Chapter 1**: per-technique measurement. It upgrades the Difference panel and the assistant at once and defines the objective for everything after.
3. **Chapter 3**: measured knobs. Cheap once renders are fast, and visible to the player the same day.
4. **Chapter 4**: the atlas, as an overnight job.
5. **Chapter 2** runs alongside from the start, because it needs logged requests and those only accumulate with use. Start logging now.
6. **Chapters 5, 6, 7, 8** as data and interest allow.

If only one thread is pursued, it should be chapters 1 to 3 together: describe the tone by what is played, ground requests in that description, and make the controls honest.

## Sources

- Martínez Ramírez, Wang, Smaragdis, Bryan. Differentiable Signal Processing With Black-Box Audio Effects. ICASSP 2021. https://arxiv.org/abs/2105.04752
- Chu, O'Reilly, Barnett, Pardo. Text2FX: Harnessing CLAP Embeddings for Text-Guided Audio Effects. ICASSP 2025. https://arxiv.org/abs/2409.18847
- Cartwright, Pardo. Social-EQ: Crowdsourcing an Equalization Descriptor Map. ISMIR 2013. https://dblp.org/rec/conf/ismir/CartwrightP13.html
- Seetharaman, Pardo. Audealize: Crowdsourced Audio Production Tools. JAES. https://interactiveaudiolab.github.io/assets/papers/seetharaman_pardo_audealize_jaes.pdf
- Steinmetz et al. ST-ITO: Controlling Audio Effects for Style Transfer with Inference-Time Optimization. ISMIR 2024. https://arxiv.org/abs/2410.21233
- Koyama, Sato, Sakamoto, Igarashi. Sequential Line Search for Efficient Visual Design Optimization by Crowds. ACM TOG 36(4), 2017. https://dblp.org/rec/journals/tog/KoyamaSSI17.html
- Mohamad, Dixon, Harte. Pickup position and plucking point estimation on an electric guitar via autocorrelation. JASA 142(6), 2017. https://pubs.aip.org/asa/jasa/article-abstract/142/6/3530/694989/Pickup-position-and-plucking-point-estimation-on

Found while checking these and not yet read: "EG-VAE: A Unified Framework for Electric Guitar Tone Transfer and Removal" (arXiv 2608.05513), "InstructFX2FX: A Multi-Turn Text-to-Effect System for Sequential Audio Effect Refinement" (arXiv 2606.22005), "Do Joint Language-Audio Embeddings Encode Perceptual Timbre Semantics?" (arXiv 2510.14249).
