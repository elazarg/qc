# Tone knowledge in Tone Lab

Written 2026-11-18. This records what Tone Lab now knows about guitar tone and the Quad Cortex, where each piece came from, how far it was checked, and where it lives in the code. It was gathered without anyone playing: from the unit itself, from presets already archived, and from published sources.

Three kinds of evidence appear below, and they are not equal:

- **Read from the unit**: values the connected Quad Cortex (CorOS 4.1.1) returned. Treat as fact for this unit and firmware.
- **Published**: manuals, circuit analyses and engineering articles. Each research file in `research/` carries the URL for every claim.
- **Community practice**: what players and engineers repeat. Most numbers here are rules of thumb. None of them has been measured on this player's guitar.

A fourth kind was added on 2026-11-19: **measured on the unit**, by replaying one recorded dry phrase of the Charvel through a built chain and comparing third-octave spectra. These are facts for the one chain measured. See "Measured on the unit".

## Where it lives

| What | File |
| --- | --- |
| Gear knowledge the app loads: 45 amp families covering all 129 amps, 46 drives, 87 cabs, 135 effects, microphones, 15 recipes, the two guitars | `tone_lab/knowledge/gear.json` |
| Lookups over it, the tone vocabulary and its remedies | `tone_lab/lore.py` |
| The catalog as the app reads it, including the cab layout and plugin ownership | `tone_lab/models.py` |
| The unit's own named settings, read and served | `tone_lab/device_presets.py`, data in `tone_sessions/device-presets.json` |
| Control roles behind the Shape knobs | `tone_lab/styles.py` (`ROLES`, `KNOBS`) |
| Silent measurement harness | `tone_lab/probe.py`, `research/measure_controls.py` |
| What each model costs in processing and delay memory, and whether a chain fits | `tone_lab/budget.py`, data in `tone_lab/knowledge/costs.json` (`python -m tone_lab.budget` rereads it from the unit) |
| What every amp control was measured to do, and which models invert the signal | `tone_lab/measured.py`, data in `tone_lab/knowledge/controls.json` and `polarity.json` |
| The sweeps behind those tables | `research/measure_amps.py`, `research/analyse_amps.py`, `research/measure_polarity.py`, report in `research/amp-controls-report.md` |
| What a built draft sounds like, measured before anyone hears it | `tone_lab/hearing.py` (`python -m tone_lab.hearing <draft id>`) |
| Scenes and a second path in a draft | `tone_lab/preset_design.py` (`layout`, `FORM`), `tone_lab/draft_render.py` (`Chain`, `build_grid`) |
| Raw research with a source per claim | `research/*.json`, `research/qc-internals.md` |
| Rebuild the knowledge file from the research | `python research/build_knowledge.py` |

`gear.json` is generated. Edit the research files or the build script, then rebuild. The build checks every model id, parameter name and setting against the catalog and reports what it drops (currently nothing).

## Read from the unit

### Every cab block has 22 parameters, and the catalog lists 2

The bundled catalog gives a stock cab only its two microphone selectors. Presets archived from the unit show every stock cab stored in the layout of the Default Cabsim: two microphones, each with bypass, selector, level, pan, distance and position, then the block's high-pass, low-pass and output. Five different cabs in 23 archived presets all have this shape, and the harvest read it back for the mono guitar cabs it loaded. The stereo layout is taken from the same catalog and has not been seen in an archived preset.

Consequences, all fixed in `tone_lab/models.py` and its users:

- Tone Lab could not move a cab microphone or a cab filter. Every draft played through the default pair (Dynamic 57 and Ribbon 160) at position 4.0, distance 0, with the cab's filters wide open.
- The **Low cut** and **Soft top** knobs matched no parameter on a stock cab. They now move one filter per row: the one doing the work (the lowest low-pass or the highest high-pass), whether it sits on the cab or on an equalizer.
- Drafts can set a cab's `POSITION`, `DISTANCE`, `HPF` and `LPF`. One setting moves both microphones.
- The microphone names follow `<prefix>_<cab>_<mic>`, for example `ML_412 Brit 60B GB 71_Dynamic 57`. Choosing a microphone is not implemented.

### 2,374 named settings from Neural DSP

The unit holds named settings for each model ("device presets"). The index is one read. The values come back only when a setting is loaded onto a block, so `python -m tone_lab.device_presets` loads each one in the working slot with USB-only routing, reads it back, and restores the preset that was selected. It makes no sound and takes about half an hour. It resumes if interrupted.

It recorded 450 models (amps, drives, cabs, equalizers, compressors, gates, delays, reverbs, modulation, wah, filter, pitch) and 2,374 settings, 1,932 of them named beyond the default. The names are in players' own words, which makes this the manufacturer's statement of what those words mean on each model:

| Model | Setting | Values |
| --- | --- | --- |
| Rectifier, Modern | Tight Modern | Gain 6, Bass 1.0, Mid 4, Treble 6, Presence 3, Master 1.0 |
| Rectifier, Modern | Boost Me | Gain 4, Bass 1.6, Mid 7, Treble 6.3, Presence 3, Master 2 |
| Green 808 | Boost | Overdrive 0, Tone 5, Level 10 |
| RAT | Brighten | Distortion 0.5, Filter 0, Volume 10 |
| AC30 Top Boost | Bright Clean / Warm Crunch | Tone Cut 1.4 / Tone Cut 6.0 |
| Low-High Cut | Space For Bass | High-pass 130 Hz, low-pass 10.3 kHz |
| Parametric-3 | No Honky | a cut centred on 624 Hz |
| Simple Gate | Chug Cutter | Threshold −35 dB |

The app uses them in three places: as named starting points given to the preset designer, as one-click settings on a block in the Chain panel, and by name in the request line (typing `tight modern` applies it when the selected draft has that amp).

Models that share a name and controls share settings (`device_presets.settings`). The catalog keeps both the original and the updated id for several models, and only one of each pair carries the named settings.

### What the names say each control does

`python research/analyse_factory_settings.py` contrasts settings by the words in their names, within each model, and pools the result by parameter. The findings that matter:

- **Which control is the gain.** The rank of a name from clean to lead follows `INPUT GAIN` (ρ = 0.95), `GAIN 1` (0.94), `VOLUME` (0.89 over 16 amps), `GAIN` (0.88 over 35 amps), `VOLUME BRIGHT` (0.81) and `DRIVE` (0.80). So on an amp with a Volume and no Gain, Volume is the drive. The Drive knob previously ignored eight such amps, including the Dumble, the JC-120 and the Friedman clean channel. It also ignored a Tube Screamer's `OVERDRIVE` control.
- **Backwards controls.** "Bright" settings have a lower Vox `TONE CUT` than "dark" ones (−0.17 of the range), and the RAT's "Brighten" has `FILTER` at 0. Warmth and Presence now turn these the right way.
- **Mids.** "Mid" settings sit 0.45 of the range above "scoop" settings on `MID`.
- **Bass falls as gain rises.** Across 60 amps the bass setting falls as the name moves from clean to lead (ρ = −0.53). Neural DSP's own designers follow the rule the amp makers print in their manuals. Tone Lab's Drive knob does not do this yet.

Too few settings are named for tightness to test the low-end controls this way (two models).

### Plugin models this unit refuses, and plugin models that do nothing

The unit refused to add the cabs of three plugins: Nolly, Cory Wong and Fortin Nameless. It accepted cabs from Plini, Gojira, Soldano, Petrucci, John Mayer, Rabea, Misha and Tim Henson. Tone Lab treats every model of a refused plugin as unavailable (45 models) and does not offer them to the designer. This is an inference: the unit did not answer a licence query, and it accepted the amps of those three plugins as blocks. The player confirmed on 2026-11-19 that none of the three is owned.

**Accepting a block does not mean it works.** The amp sweep of 2026-11-19 found that the 18 amps of six of the accepted plugins (Gojira, Soldano, Petrucci, Rabea, Misha and Tim Henson) load and then pass the signal through untouched. Every one returned the same sound, the guitar through the cab alone, and no knob or switch changed it. Their cabs behaved alike in the polarity measurement: twelve of them returned one identical sound. The amps of Plini and John Mayer work. The player confirmed on 2026-11-19 that none of those six is owned. So this is what the unit does with a plugin it holds no licence for: sometimes it refuses the block, and sometimes it loads the block and leaves it doing nothing. Only a measurement tells the two apart from a model that works.

Tone Lab withholds every model of a plugin whose amp did nothing (`preset_design.locked_models`, from the `inert` list in `controls.json`): 143 models in all with the refused ones. Before this, a draft built on a Petrucci or Soldano amp would have been a clean guitar through a cab. After buying a plugin, `python research/measure_amps.py inert` measures those 18 amps again and `python research/analyse_amps.py` rebuilds the list, so the plugin's models are offered once its amps respond.

### The pinned device library duplicates queued messages

`qc_mcp`'s `request()` re-queues every retained packet each time a reply matches. A polled request therefore doubles the queue on each call. During the harvest this grew one process past 10 GB and stalled it. `device_presets._loaded` now clears the queue around each poll, as `device.live_position` already did. Any new polling code needs the same care.

### Measured on the unit

`research/measure_controls.py` ran on 2026-11-19, after the unit was rebooted. The numbers are in `research/measurements/`. One 7 s phrase, 32 conditions, no sound in the room. A repeated condition matched the first to 0.00 dB in every band, so differences below are the controls and not noise. Band changes are relative to the rest of the spectrum, with the overall level change given separately.

Cab microphones, on the 412 Brit 60B GB 71 behind a Plexi at gain 6, both microphones moved together:

| Setting (default: position 4, distance 0) | Level | Below 200 Hz | 200–800 Hz | 3–6 kHz | Above 6 kHz |
| --- | --- | --- | --- | --- | --- |
| Position 0 | −0.8 dB | −1.1 | −1.0 | +1.2 | +2.8 |
| Position 2 | −0.4 dB | −0.5 | −0.5 | +0.4 | +1.4 |
| Position 6 | +0.7 dB | +0.7 | +0.6 | 0.0 | −2.1 |
| Position 8 | +2.0 dB | +1.6 | +1.6 | −0.2 | −4.5 |
| Position 10 | +4.2 dB | +2.7 | +2.6 | −1.6 | −5.5 |
| Distance 3 | −1.2 dB | −1.4 | −0.7 | +1.8 | +1.6 |
| Distance 6 | −2.2 dB | −3.3 | −1.2 | +3.9 | +3.5 |
| Distance 10 | −2.5 dB | −5.9 | −1.2 | +5.9 | +5.1 |

- **Position 0 is the centre of the speaker and 10 is the edge.** A higher position is darker, fuller and louder, in even steps. It acts mostly above 6 kHz and hardly at 3–6 kHz until the last step, so it treats fizz and not harshness.
- **Distance 0 is against the speaker.** Raising it removes lows and adds upper mids and treble: thinner, brighter and more peaky (crest factor rises from 11.9 to 15.2 dB). It is the strongest low-end control measured, stronger than the amp's Bass.
- The cab filters do what they say: the high-pass at 100 Hz took 3.2 dB from the band below 200 Hz, and the low-pass at 6 kHz took 7.2 dB from the band above it.

One microphone alone on that cab, at the default position, compared with the Dynamic 57:

| Microphone | Below 200 Hz | 200–800 Hz | 800 Hz–3 kHz | 3–6 kHz | Above 6 kHz |
| --- | --- | --- | --- | --- | --- |
| Dynamic 421 | +0.9 | −1.1 | −1.2 | +1.7 | +0.2 |
| Ribbon 121 | +6.7 | −0.4 | −2.3 | −3.1 | −5.9 |
| Ribbon 160 | +1.9 | +1.7 | −0.2 | −1.2 | −4.5 |
| Condenser 414 | +0.5 | +0.3 | −0.1 | +2.3 | −2.9 |
| Condenser 184 | +3.9 | +0.5 | −1.1 | −3.4 | −3.4 |

This agrees with the published characters: the 421 is scooped with more bite, both ribbons are dark, and the 121 carries far more low end. The Condenser 184 came out dark and full here, which the published notes did not predict. Other cabs have their own captures and were not measured.

Amp controls, on the US Rectifier at gain 5 into a V30 cab (defaults: Master 3, the rest 5):

- **Output is clean level.** −6 dB and +6 dB changed the level by exactly 6.00 dB and no band by more than 0.01 dB.
- **Master is power-amp drive.** From 3 to 9 the level rose only 1.0 dB while the crest factor fell from 11.5 to 10.4 dB, with about 1 dB more below 200 Hz and above 6 kHz and 1.6 dB less at 800 Hz–3 kHz: more compressed and slightly scooped. At 1 it was 8.6 dB quieter and thinner.
- **Bass and Presence are gentle on this amp.** Bass from 2 to 8 moved the band below 200 Hz by 2.9 dB in total. Presence from 2 to 8 moved the band above 6 kHz by 1.5 dB. A cab filter or the microphone distance moves the low end several times further.
- Gain at 2 was 22.8 dB quieter than at 5, and at 8 only 1.6 dB louder: by 5 this amp is already saturated.

### What one sound can carry

The unit answers `ModuleStats` with the cost of every model: processor share, four kinds of memory, delay memory and latency. `tone_lab/knowledge/costs.json` holds it for 411 models. What the unit does with those numbers is not documented, so it was found by filling the grid until blocks were refused (2026-11-19, 17 cases):

- Rows 1 and 2 of the grid share two processors. A block runs whole on one of them. The two took 91.0% and 88.0% before refusing more.
- One kind of memory (`pmda`) holds 8,192 per processor. Two amps the size of a Rectifier cannot share a processor, so a third such amp is refused whatever the processor share says.
- Delay memory is one pool of 85 for both processors.
- **A block that does not fit is refused silently.** The grid simply comes back without it.
- A stock cab costs what the Default Cabsim costs.
- The unit did not stream its live processor meter (`CPULoad`) to this connection, idle or after a change. Cortex Control shows one, so a capture of its USB traffic would say how to ask.

`budget.weigh` places the largest blocks first on the emptier processor and predicts all 17 cases. A draft that would not fit is rejected with the name of the block that did not, the designer is told each model's cost, and the Chain panel says how much of the unit a draft uses once it passes 60%. The app's own level block counts.

### Scenes and a second path

Both were built through the app's own code and read back from the unit on 2026-11-19. Nothing here was heard.

- A control becomes per-scene with `assign_param_to_scenes`. After that a plain write lands on the active scene only, so the builder switches scene, confirms the switch and writes. A block's on/off is the parameter one past its last control. The readback holds eight values per control.
- A scene name of 24 characters read back whole. Tone Lab keeps names to 12, which is a guess at what the footswitch display shows.
- A draft with two amps on two paths, two scenes and a per-scene delay was built and confirmed in 11.5 s. Replaying a phrase gave a silent tail in the scene with the delay off (−127 dB) and a tail at −64 dB in the scene with it on.
- `set_split_points(row, split, mix)` splits the first row at one column and joins it at another. The cells of the second row between them are the second path. The mixer's **A is the first row** and **B is the second**: with the paths spread 60%, silencing one amp left the other 8 dB to one side.
- **The unit lines the two paths up in time.** Its settings report dynamic delay compensation on. A click through a Rectifier (16 samples of latency in the table) arrived on the same sample as a dry path beside it, and a drive and an amp (40 samples) arrived within 5 samples of an amp alone, which is the resolution of the test. So blocks on one path only do not smear the blend.

### Every amp's controls

`research/measure_amps.py` replayed one 3.5 s phrase through each of the 121 guitar amps the unit will load, into the same V30 cab, moving one control at a time: three points below and above its default for a knob, every position for a switch. 1,672 conditions, about two hours, silent. `research/analyse_amps.py` turns the results into `controls.json` and `research/amp-controls-report.md`. 103 amps process the signal and are in the table. The other 18 do nothing (see "Plugin models this unit refuses, and plugin models that do nothing").

- **Tone controls differ widely between amps.** Per point of travel, Treble moves the 3–6 kHz band by 0.7 dB on the typical amp, by 0.2 dB on a Rectifier and by more than 2 dB on the John Mayer Signature 83. Presence is typically 0.6 dB per point and Bass 0.5.
- The Shape knobs now even this out. A move of an amp's bass, treble, presence or low-end control is multiplied by the typical strength over that amp's, between a half and double (`measured.scale`, used in `styles.apply_rules`). Mids are left alone: they are most of a guitar's spectrum, so a mid control shows up as level and its strength could not be separated from it.
- **Most amps are close to saturated at their default gain.** Above the default, a point of gain adds 0.35 dB of level on the typical amp. Below it, a point takes away 0.8 dB.
- No control was assigned to the wrong role in `styles.ROLES`. A few run against it on one side of their travel (the Uberschall lead Presence, the Matchless Ch2 Tone), and the report lists them.
- **Marshall-type Mids act on the top.** On the Plexi and JTM45 models the Mid control hardly moves the mids against the rest and adds up to 1 dB per point above 6 kHz.
- **Bright switches move more than three points of a tone knob.** The typical Bright switch moves one band by 4.8 dB against the rest, and the largest by 10.7 dB.
- One switch that works on a working amp still measured as doing nothing in every position (the John Mayer Three-In-One's Room Size, at its default settings). Such switches are not offered to the designer (`measured.idle_switch`).
- The bass and low-end controls of the 99 working amps that have one were measured again on the stretch of a take with the most energy below 200 Hz (`python research/measure_amps.py low`, into `research/measurements/amps-low/`), because the first phrase has little there. The table uses that pass for those controls. Bass moves the band below 200 Hz by 0.5 dB per point on the typical amp, by 0.2 dB on a JCM900 clean channel or a Plexi 50, and by 2.6 dB on the John Mayer Signature 83.

### Polarity

Two amps blended on parallel paths cancel where they overlap when one turns the signal upside down and the other does not. The first two-amp draft built on the unit did exactly that: a Rectifier beside a Plexi came out 3.4 dB louder, and 2.3 dB fuller below 400 Hz, with the mixer's phase switch flipped.

`research/measure_polarity.py` measured every amp against every other. The amp under test sits on the first row and a clean reference amp on the second, panned hard apart, so one pass records both, already aligned by the unit. The reference channel then lines every pass up with every other.

- **The amps fall into two camps**, 49 and 52 of the working amps that could be judged. 92% of the 6,347 pairs that interact at all are explained by one sign per amp. The camps do not follow families, and channels of one amp can differ: the Rectifier modes, the 2203 and the Plexi 50 Bright are in one camp, and the Plexi 50 Normal, the 5150-type Lead, the AC30 Top Boost and the JTM45s are in the other. So the table is needed, and no rule of thumb replaces it. Which camp is the upright one was not measured, and for blending it does not matter.
- **Stock cabs agree with each other.** All 69 that could be judged have the polarity of the reference cab.
- Checked on twelve real blends through the mixer: wherever the table had an opinion and the switch changed the level by 1 dB or the low end by 2 dB, the table chose the fuller setting. The low end below 400 Hz moved by up to 6.6 dB between the two settings.
- Three amps are left out because they hardly correlate with anything (both Uberschall-type leads and the Gojira HOT), and so are the sixteen cabs that are empty or belong to the plugins that do nothing.

The builder now sets the mixer's phase switch whenever the two paths of a draft hold amps of opposite camps (`measured.opposed`, used in `draft_render.compile_chain`). It compares paths only when both hold an amp. An amp beside a dry path, drives, captures, impulse responses and the choice of microphone were not measured.

Not measured, because the run was stopped: whether a cab's other microphones share its polarity. Drives could not be measured this way. A path with no mono block on it carries the USB input on its left channel only, so a dry second path, panned right, was silent.

### A fuzz lead that came out too tame

The player asked for the solo of Pink Floyd's "Time" and got the right gear on paper: a Fuzz Face-type into a Hiwatt and its 4×12, tape echo and plate. It sounded nowhere near as distorted as the record, with the Drive knob at maximum too. Measured on the unit on 2026-11-19, the Charvel phrase through the draft with its delay and reverb off. "Loud to quiet" is how far the quiet quarter of the phrase sits under its loudest moments. Saturation closes that gap, which is what sustain is. The dry guitar measures 14.2 dB.

| Setting | Loud to quiet | Peaks over average | Brightness (centroid) | Level |
| --- | --- | --- | --- | --- |
| As drafted: Fuzz 7.6, pedal Volume 5.3, amp Gain 5.4 | 5.5 dB | 10.4 dB | 586 Hz | −38.4 dB |
| Drive at maximum as it then was: Fuzz 9.1, Gain 6.7 | 3.3 dB | 9.9 dB | 627 Hz | −34.6 dB |
| Fuzz 10 alone | 1.7 dB | 9.5 dB | 966 Hz | −35.6 dB |
| Fuzz 10, with Treble 6 and the cab's low-pass at 10 kHz | 1.4 dB | 10.4 dB | 1,204 Hz | −36.5 dB |
| For scale: a Rectifier at its defaults in the same chain | 1.8 dB | 8.0 dB | 766 Hz | −34.7 dB |

What went wrong, in order of weight:

- **A Fuzz Face does most of its work in its last point.** From 7.6 to 9.1 the sustain gap closed from 5.5 to 3.3 dB. From 9.1 to 10 it closed to 1.7 dB and the sound became half again as bright. The designer's own notes said the pedal is run at or near maximum, and it chose 7.6, under an instruction of mine to use less gain than seems needed. That instruction is right for rhythm parts and wrong for a fuzz lead.
- **The Drive knob stopped short.** Full right added a fixed 1.5 points to each gain control, which left the fuzz at 9.1. Full left already went to the floor of each control.
- **The tone was filtered dark, and dark distortion does not sound distorted.** Treble 3.5, a low-pass at 7.8 kHz and the microphones moved towards the edge left the result darker than the dry guitar (586 against 824 Hz). Each choice has a source. Together they hid the fuzz.
- **The Hiwatt adds nothing by itself.** In the amp sweep both of its channels measured clean at gain 2, 5 and 8. The designer was not told.
- **Nothing ever listens to a draft.** The designer writes settings and the app builds them. No step compares the result with how distorted the request implies it should be.

What changed:

- Drive at full right now takes every gain control to its top, on a square-law taper so that small turns stay small (`styles._drive_delta`). On this draft it measures 2.7 dB loud to quiet.
- Drive takes back, at the amp's clean Output, the level the amp's gain was measured to add (`styles._hold_level`). Without it, full right on this draft was 10.5 dB louder, because a clean amp's gain is mostly volume. With it, 3.2 dB.
- Each amp's card now carries `measured_drive`: how distorted the amp was at three settings of its gain, in words, from the sweep (`measured.drive_curve`). For the Hiwatt: "GAIN 2 clean, 5 clean, 8 clean". For a Rectifier: "GAIN 2 clean, 5 saturated, 8 saturated".
- The designer is told to decide how distorted the sound is and make the chain reach it, to run a fuzz at or near maximum, and to keep a lead bright enough to cut. "Less gain than seems needed" now applies to rhythm parts.

Also measured: the Facial Fuzz's undocumented `PICKUP LEVEL` behaves like the guitar's volume (at 5 the sustain gap opened from 1.8 to 4.9 dB), and its `PICKUP` switch changed almost nothing. With the fuzz at 0 the Hiwatt at Gain 10 and Master 8 did saturate (2.4 dB), driven by the pedal's output.

Drives have not been swept the way amps were.

### Listening to a draft

`tone_lab/hearing.py` builds a draft silently and says what came out of it: how driven, how bright, how much ambience. It replays the phrase at its own level and again 12 dB quieter. The share of that 12 dB step the chain swallows is the measure of drive: 0 for a chain that only follows the playing, near 1 for one that is flat out. Equalization, delay and reverb do not move it. One listen takes 26 s on the unit, about 11 of them the build.

Measured on 2026-11-19 on the saved drafts: the clean Twin drafts 0.00, a Plexi at gain 2.3 0.18, the "Time" draft 0.35 as written and 0.99 with Drive at full, a crunch solo 0.50, the Timmons lead 0.80, the Rectifier rhythm 0.87 and the Santana lead 0.92. The words change at 0.18 (edge of breakup), 0.40 (crunch), 0.62 (overdriven) and 0.82 (saturated).

A chain whose peaks still stand 15 dB over its average is called at most the edge of breakup, so that a compressor is not read as distortion. Ambience is what sounds from 40 ms after the phrase stops, against the phrase: chains with no delay or reverb measured −45 to −72 dB, a room at 9 to 11% −35 to −37, a lead delay at 21% −15 to −17. `SUGGESTIONS.md` describes how the desk uses it.

## What each control does

`styles.ROLES` names controls by what they do, wherever a model has them, with a sign for controls that work the other way round:

| Role | Raises it | Lowers it |
| --- | --- | --- |
| Amp treble | Treble, Brilliance, Tone, Highs | |
| Amp presence | Presence | Tone Cut (Vox), Cut (Matchless) |
| Amp bass | Bass, Lows | |
| Amp low-end resonance | Depth, Resonance, Deep | Tight |
| Amp mids | Mid, Middle, Mids | |
| Pedal treble | Tone, Treble, High, Spectrum, Presence | Filter (RAT, Gojira DRT), Cut (1981 DRV) |
| Pedal bass | Bass, Low | Tight (BE-OD) |
| Pedal gain | Gain, Drive, Distortion, Overdrive, Dist, Drv, Fuzz, Sustain | |

Sources: the maker's manuals and circuit analyses for the backwards controls (`research/amps-classic.json`, `research/drives-effects.json`), checked against the factory settings where the names allow. Moves written for a 0–10 knob scale to the control's own range.

Known gaps:

- The Shape knobs do not move switches. The designer can set them (see "What the designer and the assistant are told"). Bright switches, the Friedman's Fat and Sat, the JP-2C's pulls and channel selectors matter, and the research files describe them.
- Model-specific behaviour is described to the designer and the assistant but is not in the knob rules. Examples: the Rectifier's Presence has a much lower useful range in Modern mode, the JP-2C's Bass sits before the distortion, the Dumble gets darker as its Volume rises, and a Top Boost's Treble also cuts bass.
- The Low cut and Soft top knobs move in octaves and start from the audible span (45 Hz and 14 kHz). Before, Soft top at full moved a filter from 20 kHz to 16.5 kHz, which is inaudible.
- **Mic position** is a new knob. Up moves both microphones toward the edge of the speaker, which is darker and fuller. The direction was measured on 2026-11-19 (see "Measured on the unit"). A full turn is five points of position, about 5 dB above 6 kHz.
- The Body and Presence knobs move an amp's Bass and Presence by two to two and a half points on the typical amp. Since 2026-11-19 the move is scaled per amp from the sweep of all 121 (see "Every amp's controls"), so a Rectifier's weak Treble is turned up to twice as far and a John Mayer amp's strong one half as far.

## Words and remedies

`lore.LEXICON` holds 41 words with 174 ways of saying them. Each word has its remedies in the order players and engineers try them. The request line applies the first remedy the selected chain can perform, says why in one clause, and offers **Another way**, which swaps it for the next remedy instead of stacking them. A fix that no knob here performs, such as changing pickup, is one hover away on the line, marked ⓘ. A remark can name a way of playing ("when I palm mute", "on chords", "on the high strings", "when I dig in"), which changes the order. Questions and longer requests go to the assistant, which is given the same remedies and the notes on the loaded gear.

The order comes from `research/vocabulary.json` (95 descriptors, 701 remedies, 9 ordering rules with sources). The rules most often broken:

| Complaint | Usual wrong first move | What is tried first |
| --- | --- | --- |
| Mud or flub under gain | Cutting bass after the distortion | Less gain, then a low cut or a Tube Screamer-type boost **before** the amp, then the amp's bass and resonance |
| Fizz | Turning Treble down | Less gain, move the mic off the cap, a darker mic, a high cut, Presence. Treble last |
| Thin leads | More gain, bass or reverb | Neck pickup or series wiring, more mids, a mid-hump drive |
| Lost note definition | More gain | Less gain, mids, a boost for attack |
| Lost in a band | Turning up, more bass and treble | More mids, less bass, less gain, less reverb, then level |
| Dull and thin | Bass and treble up | Check the listening level first: quiet monitoring makes any tone sound dull and thin |
| Harsh high notes | A high cut for the whole preset | Neck pickup or guitar tone at 7, then mids, Presence, mic position |

Where Tone Lab departs from that order, and why:

- The app cannot change a pickup, so guitar-side fixes appear as a tip on the line, shown on hover.
- "Less gain" is usually the community's first move. Tone Lab applies a tonal remedy first for plain "muddy" and "fizzy" and offers less gain as the next way, because gain changes how the guitar feels under the fingers. For "mushy", "clearer" and muddy chords, less gain is first.
- A boost in front needs a block the chain may not have. The reading line says so, and **Rework the chain** hands the request to the designer, which now knows the recipe.
- Mic position is now a remedy, since its direction was measured: second for "fizzy", after the cab's low-pass, and last for "bright" and "dark" in both directions. It is not offered for "harsh", because it measured as nearly flat at 3–6 kHz.

Frequency regions quoted in the lexicon (mud 150–400 Hz, boxiness 300–600 Hz, honk 700 Hz–1.2 kHz, harshness 2–5 kHz, fizz 5–10 kHz) are rules of thumb from engineering articles. Neural DSP's "No Honky" setting cuts at 624 Hz and "Space For Bass" high-passes at 130 Hz, which fit.

## What the designer and the assistant are told

The preset designer (`preset_design.design`) used a hand-picked set of 12 amps, 10 drives and 12 cabs. It now:

- can use any amp, drive or stock cab the knowledge base describes and the unit can load and that does something there (194 models: 103 amps, 29 drives and 62 cabs). The everyday set is always offered. Models a request names by maker, model, artist or genre are added (`lore.candidates`): "the edge" adds the AC30s and a treble booster, "rectifier wall of sound" adds the three Rectifier modes, a Tube Screamer and V30 4×12s.
- receives for each model what it is, how it behaves and what to watch out for, and up to seven named settings (the community's, then Neural DSP's).
- can set every knob of an amp or drive, including a drive's Level, which the classic boost needs. It could not set Level before. The amp's Output stays with the app, which sets final level itself.
- receives the community recipe for the sound named (15 recipes, from jazz clean to Steve Vai lead) and notes on the selected guitar: for the Charvel, the pickups (Full Shred bridge, Alnico II Pro neck) and what each blade position suits.
- follows a short statement of practice: less amp gain than seems needed, the tightening boost, cab filters at 70–110 Hz and 6–10 kHz on driven sounds, lead delay and reverb amounts.

The assistant (`advisor.read`) receives notes on the gear in the selected draft and the ordered remedies for the words in the request.

Added on 2026-11-19:

- **Scenes.** A draft can name up to eight scenes. A block's control can take another value in one scene, and a block can be off in a scene. The designer is told what the community does with them (`research/paths-scenes.json`, 31 entries with sources): a lead scene is the rhythm sound with 2–4 dB more clean level after the amp, a small lift around 1 kHz and delay, not more gain. It is told not to switch an amp off in a scene, because the unit crossfades and players report a dropout, and to keep a delay's time and feedback the same across scenes, because a ringing tail takes on the new scene's values. To make the level lift possible it can set an equalizer's Output, within ±6 dB.
- **A second path.** A draft can put blocks on a parallel path: a second amp and cab, or delay and reverb beside a dry path, with the second path's level and how far the two are panned apart. The path runs beside the main chain from the earliest type of block it holds to the latest. The designer is told to blend amps that do different jobs, with less gain on each (29 entries on dual amps in the same file).
- **Switches.** The designer can set an amp's or drive's switches, in whole steps. 52 models offer at least one. Switches that measured as doing nothing are withheld (see "Not done").
- **Cost.** Each model's card carries its processor share and delay memory, and the prompt says what one sound can carry.

Two design requests were sent on 2026-11-19 to check the new forms, and both returned valid drafts in about 22 s: a rhythm and lead pair with two scenes, and a Plexi blended with an AC30. Neither was kept in the history and neither was heard.

Nothing here has been listened to. The prompts were checked for content and size (about 35,000 characters for a typical request).

## Quad Cortex facts that change what software should do

From `research/qc-internals.md`, which gives a source and a confidence for each. Official unless marked.

- An amp's **Master** is power-amp gain and changes tone. **Output** is the clean level after the model. Level-match with Output, a Gain block or the lane volume. Tone Lab uses a Gain block.
- 46 amp models have no Master. Their Gain or Volume is the whole amp.
- A capture's **Gain** behaves like a gain block in front of it (moderator statement). Its Bass, Mid and Treble are a generic ±6 dB equalizer.
- Cab `POSITION` and `DISTANCE` are microphone placement. The manual does not say which end is which. Measured: position 0 is the centre and distance 0 is closest. Both are disabled when a slot holds a user impulse response. User impulse responses are shortened to 1,024 samples.
- Microphones differ per cab. Dynamic 57 and Ribbon 160 are the default pair on 72 guitar cabs.
- Input level and impedance are global, not per preset. 1 MΩ and 0 dB are the defaults the community keeps for passive pickups. The per-preset trim is the input block's Input Gain.
- Bypassing a block frees no processing. The manual gives a ceiling of 90%, and rows 1–2 and 3–4 run on different processors. Measured: each pair of rows has two processors that took 91% and 88%, with separate limits on memory (see "What one sound can carry"). On overload the Global EQ and the Input Gate switch off silently (community report).
- Parallel paths are aligned in time by the unit when dynamic delay compensation is on, which it is on this unit (measured). Nothing aligns their polarity (see "Polarity").
- Measured on 2026-11-19 with a steady tone through row 1: adding or removing a block on any row stops all audio for about 0.2 s. Changing a setting, reading or saving the preset and switching scene do not interrupt it, and moving the input from one row to another costs a dip of about 20 ms. So a chain cannot be built while its neighbour plays without stutter, and two chains on different rows of one preset can be switched instantly.
- A preset change has a gap and cuts tails. A scene change does not. Fifty switch parameters mute briefly when changed, which delays a scene change that uses them.
- For high gain the community places an Adaptive Gate (S/C) after the amp, keyed from the input. Tone Lab's drafts use a Simple Gate at the front.
- Models with the same name can have different ids. Address models by id.

## Not done

- **More measurements.** Every amp's controls and polarity are measured, on one phrase of the Charvel through one cab. Not measured: cabs other than one, mic position on a single microphone, the stereo cab layout, drives, and any chain with the Les Paul's phrase.
- **The live processor meter and the licence list.** The unit did not send either to this connection. A capture of Cortex Control's USB traffic would show how to ask. Until then, which plugins work is known only from the amp sweep: Plini and John Mayer.
- **Rows 3 and 4.** Drafts use the first two rows only. The second pair of processors is unused.
- **Why the unit went silent on 2026-11-18.** The first run of the measurement returned digital silence from every condition, although the preset, routing and I/O settings read back correct. The next day the unit reported an unspecified issue and was rebooted, after which audio passed and the user's presets were all present. It followed about 2,000 block loads and one force-stopped read in the working slot, so a long harvest may be what upset it. That link is a guess.
- **Listening.** No change in this document has been heard. The measurements say what a control does to a spectrum, not whether the result sounds better.
- **Microphone choice**, switches on the Shape knobs, and the model-specific behaviours listed above.
- **Bass against gain.** The Drive knob does not lower bass as it adds gain.
- **Gate type and placement** for high gain.
- **Reddit, The Gear Page and the Fractal wiki** could not be read by the research tools. Fractal's amp notes were used only through Yek's guide and search summaries.
- Artist chains for Plini, Santana's "Europa" and Vai's Passion and Warfare era are reconstructions. `research/vocabulary.json` says which parts are documented.

## Sources

Each research file lists the source for every entry. The ones the conclusions lean on most:

- Neural DSP, Quad Cortex manual: https://neuraldsp.com/manual/quad-cortex
- Neural DSP, device list: https://neuraldsp.com/device-list
- Neural DSP, how to use an overdrive pedal: https://neuraldsp.com/articles/how-to-use-an-overdrive-pedal
- Tube Screamer circuit analysis (mirror of ElectroSmash): https://electrosmash.mas-effects.com/tube-screamer-analysis
- Mesa/Boogie Rectifier manual: https://mesa-boogie.imgix.net/media/User%20Manuals/3chRecto_multiWatt.pdf
- Mesa/Boogie JP-2C manual: https://mesa-boogie.imgix.net/media/User%20Manuals/070494-JP2C-160317-download.pdf
- Sound On Sound, guitar amp recording: https://www.soundonsound.com/techniques/guitar-amp-recording
- Recording Magazine, capturing the cab: https://www.recordingmag.com/resources/recording-info/mics-miking/capturing-the-cab/
- Adam "Nolly" Getgood on high-gain myths, Guitar World: https://www.guitarworld.com/gear/guitars/adam-nolly-getgood-on-the-myths-surrounding-high-gain-guitar-tone
- Line 6, studio tone, live tone and equal-loudness curves: https://kb.line6.com/studio-tone-vs-live-tone-and-the-fletcher-munson-curve
- York Audio MES 212 V30: https://www.yorkaudio.co/product-page/mes-212-v30
- Steve Vai on his DS-1 settings: https://www.vai.com/answers-45/
- qc-mcp project notes: https://github.com/lexasoft123/qc-mcp
