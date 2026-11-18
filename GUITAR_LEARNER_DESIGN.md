# Guitar Learner design

## Record, check, tag

Record a clip, pick a saved take, or open a dry WAV. The learner checks the selected passage automatically against what it has already learned. The guitar board always shows the settings you enter. The estimate appears beside the recording, so you can compare it with the actual controls before tagging.

**Teach with this clip** saves the settings and refreshes the model. **Keep as check** saves the actual settings for an independent score. You can later use a saved check to teach, which removes that performance from future independent scores. The dataset shows all available recordings and their current tags. Open a row to inspect its audio or correct the tag. Tag corrections and removals preserve the WAVs and label history. This short feedback loop draws on work with musicians training and evaluating models interactively. [Fiebrink, Cook and Trueman, CHI 2011](https://ualresearchonline.arts.ac.uk/id/eprint/16686/1/FiebrinkCookTrueman_CHI2011.pdf)

## Listen as you play

**Listen live** gives the learner the Quad Cortex’s dry input. It shows the recent waveform and estimates from a rolling window of usable evidence. During a pause the last usable result is marked **Last estimate**. The audio stays in memory until you keep an excerpt. Tone Lab uses one Quad Cortex audio stream at a time, so starting the learner releases any playback loop it was running.

The first detector looks for a sustained change in harmonics while pitch stays steady. It checks for a fresh pick attack or a gap and ignores changes explained by level alone. A change is shown as possible unless the before and after audio independently support different learned settings. **Keep before** and **Keep after** open the relevant excerpts in the ordinary tagging flow. This lets you provide examples around real switches during playing. There is evidence that pickup and pluck position both affect the harmonics, which makes the possibility of an acoustic change easier to establish than the precise physical cause. [Mohamad, Dixon and Harte, JASA 2017](https://webspace.eecs.qmul.ac.uk/s.e.dixon/pub/2017/MohamadDixonHarte-JASA2017.pdf)

## From a baseline to ordinary playing

1. Start with a short reusable phrase of clear notes across your usual register. Record it at your normal setup and again after moving one control. Keep the Quad Cortex input gain fixed. Compare the blade with the toggle held, and the toggle with the blade held.
2. Add actual riffs at known setups. Vary string, fret, picking position, and dynamics across examples for each setup. Repeat at some of the same notes and use distinct tone and volume settings. Reserve whole fresh performances as checks.
3. Verify performance by pickup, toggle, and knob ranges, with abstentions reported alongside correct estimates. Split evaluation by performance and recording session so similar excerpts do not appear on both sides. Include chords, bends, legato, muting, and normal playing with no configuration changes. Measure false change prompts per minute and time to a useful new estimate after a switch.
4. Keep the present note-matched estimator as a reference. For broader coverage, compare it with models that account for register and likely string, then test a multi-pitch estimator using only harmonic evidence where chord notes can be separated. Advance each approach only when it improves coverage on fresh normal playing at comparable error rates. Research on pickup and plucking-point estimates includes guitar chords, giving an experimental starting point. [Mohamad, Dixon and Harte, DAFx 2017](https://www.dafx17.eca.ed.ac.uk/papers/DAFx17_paper_43.pdf)

Volume and pick strength can produce similar changes in dry level. An estimate from ordinary playing can therefore return a range or leave a control unresolved even when the pickup is identifiable. The current implementation uses stable notes from whatever you play and reports partial evidence. Accuracy on arbitrary techniques and unseen pitches remains an evaluation target.

## Future recording annotations

A separate time-aligned annotation track could label technique and what happens throughout a clip. It could later support technique-specific evaluation and suggest which segments contain useful evidence for guitar configuration. The current tag describes the guitar controls for the selected passage.
