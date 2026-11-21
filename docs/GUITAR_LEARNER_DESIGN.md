# Guitar Learner design

The Guitar Learner listens to the guitar's dry input and tells which pickup setting and control positions are in use. It has three parts, all in `lab/learner/`: the **Guitar Learner** window (tag recordings, see estimates), **Teach a control** (teach one control's settings, check a take), and **gestures** (a move of a guitar control away and back turns a Shape knob). This document says what each does, what its words mean, and what is not built.

## What is read from the sound

Everything starts from the same reading (`state._observations`): the dry signal in 120 ms frames, 60 ms apart. A frame is used only when it is a clear single note, not clipped and not near silence. Each used frame gives the note, the strength of its first twelve harmonics relative to each other, and its level.

Two things follow from this and are true of everything below:

- Notes are compared with the same notes. A setting can be told only on pitches that were taught.
- Volume is read from the level of the dry signal. Picking harder, or a different input gain on the unit, moves the level as much as the knob does. A volume reading holds only for picking as hard as in the examples, at the same input gain, and the app says so wherever it shows one ("by dry level").

## Record, check, tag

Record a clip, pick a saved take, or open a dry WAV. The learner compares the selected passage with what it has been taught and shows its estimate beside the recording; the guitar board always shows the settings you enter, so you can compare before tagging.

**Teach with this clip** saves the settings with a copy of the passage and fits again. **Keep as check** saves the actual settings for scoring. A saved check can later be used to teach, which takes it out of the checks. The dataset lists every recording and its tag; tags can be corrected or removed, and the audio and the tag's history are kept. This short loop follows work with musicians training and evaluating models interactively. [Fiebrink, Cook and Trueman, CHI 2011](https://ualresearchonline.arts.ac.uk/id/eprint/16686/1/FiebrinkCookTrueman_CHI2011.pdf)

The estimate (`configuration`) is a comparison with taught examples, not a trained classifier. For each taught pickup setting and each note it keeps the harmonic shape and level at every knob position taught, and fills in between taught knob positions. It names the pickup or the mini-toggle only when the clip is closer to one setting than to a taught alternative that differs in that control alone, on notes both were taught with. A knob comes back as the range of positions about as close as the best. Otherwise it says why it cannot tell.

The learner works on the instrument chosen on the desk, and is offered only for an instrument it has a control layout and photograph for (today the Charvel, with and without the mini-toggle). Pickup settings are named as the desk names them (`lab.sound.sounds.INSTRUMENTS`).

## Words, and what each stands for

- **Close / near / far match**: how near the notes are to the nearest taught example. It is a distance, not a chance of being right. Nothing is called strong or confident.
- **By dry level**: a volume reading; see above.
- **Check**: a recording that was never used to teach. One recording can never be both: the whole take counts, and so does the whole run of live listening a kept clip came from.
- **Independent check, from another recording session**: a check recorded on another day than every teaching example. The day is read from the take's name. Checks from the same day as the teaching are scored too, and the score line says how many of each there are.
- **The score line** counts a control as right or wrong only where it was estimated, and says how many checks were left uncertain.

## Listen as you play

**Listen live** opens the dry input in a process of its own and shows the recent waveform with an estimate from the latest usable notes. An estimate is shown once two looks half a second apart agree; during a pause the last one is shown as **Last estimate**. The audio stays in memory until you keep a clip.

A change of setup on a held note is looked for: a step in the harmonics while the pitch stays, with no fresh pick attack or gap, and not explained by level alone. It is shown as possible unless the audio before and after supports two different taught settings. **Keep before** and **Keep after** open those excerpts for tagging. Pickup and plucking position both shape the harmonics, which is why a change is easier to establish than its cause. [Mohamad, Dixon and Harte, JASA 2017](https://webspace.eecs.qmul.ac.uk/s.e.dixon/pub/2017/MohamadDixonHarte-JASA2017.pdf)

## Teach a control

One control at a time (the pickup selector, the volume knob or the tone knob) is taught from takes at two or more of its settings, with the other controls held fixed (`taught`, `state`). A fresh take is then checked: the setting heard for the whole take, and a timeline of the settings heard window by window. The window was called "Guitar input calibration"; nothing is calibrated there.

Examples are kept as copies in the same form as the Guitar Learner's labels, so a take that is later moved or deleted takes nothing with it. An estimate is the taught setting the notes are closest to, with a closeness from 0 to 1 that is said as close, near or far.

## Gestures

A gesture is a deliberate move of one taught control from a starting setting to another and back. Its mapping names one Shape knob and a step of at most a quarter of its range. The listening only proposes; the desk turns the knob on the sound on show, and only if that knob does something on its chain. No gesture can save to the unit.

- **Timing.** An estimate is made from 0.48 s of audio every 0.14 s, live and in the rehearsal on a recorded take, so the rehearsal shows what listening live would do. Each setting must be held for 0.2 s of close readings. While the control is moving, no reading is close for about half a second; the detector waits up to 0.76 s before it gives up. A gesture needs between about 0.6 and 1.2 s at the excursion setting, and at most 1.5 s away.
- **Arming.** Listening is armed for 20 s from its start, and every gesture arms it for 20 s more. When that time passes with no gesture, listening started from the desk ends and lets go of the audio input, and says so. Listening started from Teach a control, to show the estimate, goes on observing.
- **What could set one off by accident.** A decaying note can read as a lower volume, or as a rolled-back tone, and a new pick as the return. In checks with made-up notes the dwell and gap rules kept that from being taken for a gesture, but it has not been measured on real playing. The pickup selector is the safest control to map.

## Who has the audio driver

The driver has one owner at a time. The engine holds the unit for the whole session, so the learner never opens the unit: it asks the engine which USB channel carries the dry guitar. Before a recording, live listening or gesture listening opens the driver, it takes the desk's audio hold, which stops what the desk plays and has the engine close its stream; it starts only when the engine has done so, and gives the hold back when it ends, however it ends. While the hold is taken the desk does not listen silently in the background.

When the engine cannot say which input is dry, a take is still kept: the dry input is found from the signal where that is unambiguous, and otherwise all four inputs are saved for the routing to be settled later. A dropout reported by the driver is noted in the take and does not end it.

## Where the data is

Everything is in `learner` inside the app's data folder (`%LOCALAPPDATA%\Tone Lab`; a simulated desk uses its own folder): takes, labelled excerpts, what was taught, and the gesture mappings. Faults are written with their trace to `errors.log` there. Until the app had a data folder this lived in `tone_sessions` in the program folder; the first time the learner is opened it is copied over, and the old folder is left as it was with a note. Paths written into records are relative to the data folder.

## Not built, and the plan

- **One estimator.** There are two: the Guitar Learner's (`configuration`, all controls together) and the one-control profiles (`state`) that gestures use. They now share the example store's form, the names of pickup settings, the profile files and the reading of the sound, but not the model. Making gestures read the Guitar Learner's live estimate is the next step, in this order: (1) have the live tracker report an estimate every 0.14 s from 0.48 s windows, without its two-look delay; (2) run the audio-to-gesture tests (`tests/test_lab_learner_gestures.py`, `AudioToGestureTest`) against it; (3) only if they pass, map gestures to pickup settings by name and retire the one-control profiles for the pickup, keeping volume and tone as they are until they pass too.
- **How good it is on ordinary playing.** Not measured. The plan: baseline phrases across the usual register at known setups; then real riffs, varying string, fret, picking position and dynamics; whole fresh performances kept as checks, on other days; scores by pickup, toggle and knob range with the uncertain counted beside the right; false change prompts per minute and time to a useful estimate after a switch. Chords, bends, legato and muting are outside what the note-matched estimator can use, and an estimator that accounts for register and string, or separates the notes of a chord, would be compared against it on those. [Mohamad, Dixon and Harte, DAFx 2017](https://www.dafx17.eca.ed.ac.uk/papers/DAFx17_paper_43.pdf)
- **A recording session is a calendar day.** Two sittings on one day count as one session, which is the cautious side.
- **The windows.** The learner and Teach a control are still windows of their own, with a file chooser, one drop-down menu and three keyboard shortcuts; they are to become panels of the desk's one window.
