# Quad Cortex internals for Tone Lab (CorOS 4.1.1) — researched 2026-11-18

Confidence labels: **official** = Neural DSP manual / release notes / knowledge base / device list / staff post. **firmware** = value read from the device catalog `ModelRepo.xml` (CorOS 4.1 snapshot published in qc-mcp; same family as the app's bundled catalog) — hard data for ranges, defaults, step names, tooltips; meaning is sometimes my reading. **community** = repeated by several independent users/moderators. **anecdote** = one post or one project's measurement. **inference** = my deduction, verify by measurement.

Source shorthand: [M411] https://neuraldsp.com/manual/quad-cortex · [M300] https://downloads.neuraldsp.com/file/quad-cortex/Quad%20Cortex%20User%20Manual%20v3.0.0.pdf · [M200] https://downloads.neuraldsp.com/file/quad-cortex/Quad%20Cortex%20User%20Manual%202_0_0.pdf · [M102] https://downloads.neuraldsp.com/file/quad-cortex/Quad%20Cortex%20User%20Manual%201_0_2.pdf · [DL] https://neuraldsp.com/device-list · [FW] https://raw.githubusercontent.com/lexasoft123/qc-mcp/HEAD/proto/ModelRepo.xml · [QCPU] https://raw.githubusercontent.com/lexasoft123/qc-mcp/HEAD/docs/CPU.md · [QLVL] https://raw.githubusercontent.com/lexasoft123/qc-mcp/HEAD/docs/LEVELING.md · [QANAT] https://raw.githubusercontent.com/lexasoft123/qc-mcp/HEAD/docs/PRESET-ANATOMY.md · [Q41] https://raw.githubusercontent.com/lexasoft123/qc-mcp/HEAD/docs/COROS-4.1.md · [QCL] https://raw.githubusercontent.com/lexasoft123/qc-mcp/HEAD/CLAUDE.md · [R4.1.1] https://neuraldsp.com/quad-cortex-updates/coros-4-1-1-is-now-available · [R4.1.0] https://neuraldsp.com/quad-cortex-updates/coros-and-cortex-control-4-1-0-are-now-available · [R4.0.1] https://neuraldsp.com/quad-cortex-updates/coros-4-0-1-and-cortex-control-4-0-1-are-now-available · [R3.3.1] https://neuraldsp.com/quad-cortex-updates/coros-3-3-1-and-cortex-control-1-4-1-are-now-available · [R3.3.0] https://neuraldsp.com/quad-cortex-updates/coros-3-3-0-and-cortex-control-1-4-0-are-now-available · [R3.0.0] https://neuraldsp.com/quad-cortex-updates/coros-3-0-0-and-cortex-control-1-1-0-are-now-available · [R2.1.0] https://neuraldsp.com/quad-cortex-updates/coros-2-1-0-is-now-available · [R2.0.0] https://neuraldsp.com/quad-cortex-updates/coros-2-0-0-is-now-available · [R1.4.0] https://neuraldsp.com/quad-cortex-updates/coros-1-4-0-is-now-available · [NCV2] https://neuraldsp.com/news/introducing-neural-capture-version-2 · [NCV21] https://neuraldsp.com/news/neural-capture-v2-1-is-now-available · [USB] https://neuraldsp.com/getting-started/using-quad-cortex-as-an-audio-interface · [TIPS] https://neuraldsp.com/getting-started/tips-for-using-your-plugin · [PCOM] https://neuraldsp.com/pcom · [DU58] https://neuraldsp.com/quad-cortex-updates/quad-cortex-development-update-58 · [WGS] https://quadcortex.wiki/Gain_Staging · [WFAQ] https://quadcortex.wiki/Frequently_Asked_Questions · [U:n] https://unity.neuraldsp.com/t/n (Neural DSP forum topic n, read via /raw/n).

## 1. Input stage

- Inputs 1/2: TS impedance 10 kΩ–10 MΩ, XLR 9.4 kΩ, max input gain +60 dB; Returns 1 MΩ. (official) [M411]
- IMPEDANCE default is 1 MΩ; disabled when TYPE = Mic. First-gen units have a TYPE switch; "ESS Codec" units detect type automatically. (official) [M411]
- Impedance and IN LEVEL are global I/O settings, not stored in a preset or scene. Since 4.1.0 they can be saved as I/O Settings presets (32 user), with no "set as default". (official) [M411] [R4.1.0]
- Loading an I/O Settings preset rewrites hardware input level, impedance and type; it is not undone by reloading a preset. (anecdote, project note) [Q41]
- No Neural DSP recommendation specific to passive humbuckers exists in the manual or knowledge base. Community practice: leave 1 MΩ (matches a typical amp input); lower values (toward 10 kΩ) only to imitate the pickup loading of a fuzz; several users report little audible change across the range. (community) [U:7401] [U:3759]
- IN LEVEL 0 dB is unity. A pass-through lane with input and output at 0 dB measured unity within 0.1 dB, "same for the USB interface levels". (anecdote) [U:5253]
- The input is designed to take a hard-strummed humbucker at 0 dB without clipping; "0 dB input gain would be the reference standard". (community) [WGS] [U:7401]
- IN LEVEL sits before every model, so raising it makes all amps, drives and captures hotter and noisier; several users cured over-driven cleans and noise by going back to 0 dB. A second camp raises it until hard strums peak about −12 dB. Moderator advice: leave I/O at default and adjust per preset. (community, split) [U:7401] [U:20769]
- Neural DSP's plugin guide asks for DI peaks between −14 and −9 dB at the plugin input (plugins, not the QC meter). (official) [TIPS]
- I/O display range for IN 1 LEVEL measured −12..+60 dB, with normalized 0.166667 = 0.00 dB. (anecdote, project measurement on 4.1.0) [QCL]
- Input and output blocks turn red when their I/O clips. (official) [M411]
- **Input Gate Control** lives in every input block (tap-hold), added in 1.4.0, bypassed by default. Parameters: NOISE REDUCTION 0–100 % (default 30), BYPASS (default bypassed), INPUT GAIN −24..+24 dB (default 0), a gain-reduction meter −40..0 dB. Scene- and expression-assignable. (official + firmware) [R1.4.0] [M300] [M411] [FW]
- INPUT GAIN in the input block is the only per-preset (and per-scene) input trim. (official: "provides an additional INPUT GAIN control") [M411]
- The Input Gate is an adaptive type like the Adaptive Gate, not a threshold gate; it can stutter on decaying notes with the guitar volume rolled back. (anecdote) [U:4731]
- At NOISE REDUCTION 0 % the Input Gate still acts while enabled; bypass it to remove it. (anecdote) [U:16865]
- Input Gate and Global EQ are switched off automatically when a preset exceeds processing resources. (official) [M411] They share the rows 1–2 budget, and were seen disabled at 48 % total CPU. (community) [U:22701]
- **Adaptive Gate** (id 16001): one knob, NOISE REDUCTION 0–100 %, default 30. **Adaptive Gate (S/C)** (16007, since 3.0.0): adds SOURCE, any input or earlier block. (firmware + official) [FW] [R3.0.0]
- Neural DSP's own sidechain example: key the Adaptive Gate from the input but place it at the end of the chain. (official) [R3.0.0]
- **Simple Gate** (16003): THRESHOLD −90..0 dB, default −60. **Utility Gate** (16002): THRESHOLD (default −80), ATTACK 0.5–1000 ms (1), HOLD 1–2000 ms (10), RELEASE 2–5000 ms (40), RANGE −90..−6 dB (default −90, shown OFF). **Plugin Gate** (16009): THRESHOLD −96..0 dB. **Fortin Zuul** (16010): THRESHOLD 0–10, needs Fortin Nameless Suite X. (firmware) [FW] [DL]
- CorOS 2.0.0 moved every factory preset from the Utility Gate to the Input Gate. (official) [R2.0.0]
- High-gain consensus: Adaptive Gate (S/C) after the amp (before the cab), SOURCE = Input 1; add the Input Gate or a second gate before the amp only for hand noise; switch gates off in clean scenes. (community) [U:18774] [U:14715] [U:16865]
- Players who ride the guitar volume prefer a threshold gate after the amp, set as low as possible. (anecdote) [U:18774]
- An enabled Input Gate plus a block gate interact; check the input block before adding another gate. (anecdote) [U:16865]

## 2. Amp blocks

- Official parameter meanings: GAIN = "amount of gain in the preamp section"; VOLUME = "amount of volume in the preamp section"; BASS/MID/TREBLE = tonestack; MASTER = "Adjusts the amount of power amp gain. This parameter is highly interactive with all other amp parameters. The lower the Master is set, the less effect the other controls will have"; PRESENCE = boosts upper mids and treble; DEPTH = boosts lower mids and bass; RESONANCE = boosts bass; BRIGHT = extra treble; OUTPUT = "Controls the overall volume of the amp". (official) [M300]
- So MASTER is modelled power-amp drive and changes tone; OUTPUT is the level control after the model. OUTPUT is −60..+12 dB, default 0.0 dB on almost every amp, with skew 3.8018 (0 dB at normalized 0.5). (official + firmware) [M300] [FW] [QLVL]
- Staff answer on master vs level (plugin amp, 2020): Master gives "more drive coming from the amp" but "the actual sound you hear wouldn't be louder"; Level is the output volume. (official, staff post) [U:1901]
- Level-match with amp OUTPUT, a Gain block or the lane output, never MASTER. (community) [U:20554] [QLVL] https://thegearforum.com/threads/nmd-ndsp-quad-cortex.3847/page-6
- Defaults: MASTER 3 on 66 amp models, 5 on 25 (mostly plugin amps), GAIN 5 on most. (firmware) [FW]
- 46 amp models have no MASTER: Brit Plexi 50/100, Brit TM45 and Brit Bass 50 (GAIN, or GAIN 1 + GAIN 2 on "Patch" models), US DLX / TWN / SPR / Prince, UK C30 / C15, Rols Jazz, Freeman 100 Clean (VOLUME), US Tweed Basslad and US HP Tweed TWN (VOLUME NORMAL + VOLUME BRIGHT), plus Gojira CLN, Tim Henson Crunch Ch1/Ch2 and others. (firmware) [FW]
- On those non-master designs the single GAIN/VOLUME knob is the real amp's volume, which drives phase inverter and power amp too; treat it as whole-amp drive, not preamp-only. (inference from amp topology)
- "Patch" = jumpered channels (GAIN 1 / GAIN 2 are the two channel volumes); the device list labels the equivalent bass model "Jumpered". (official, by analogy) [DL]
- Brit UBL (Silver Jubilee): INPUT GAIN, LEAD MASTER, OUTPUT MASTER, then OUTPUT. Fortin Nameless: MVC is the master; GAIN I, GAIN II, INPUT [LOW, HIGH], PULL GAIN. (firmware) [FW]
- Soldano SLO-100 (plugin): knobs run 0–11; GAIN NORMAL/MASTER NORMAL apply on CHANNEL = Normal, GAIN OD/MASTER OD on CHANNEL = OD; CRUNCH [Clean, Crunch]. (firmware) [FW]
- Dumbbell ODS tooltips: OD CONTENT "Adjust the gain of the Overdrive Channel"; EQ [Jazz, Rock] — "Rock: Mid-forward and aggressive. Jazz: Smoother and more scooped"; PREAMP, BRIGHT, MID are [Off, Boost]; CHANNEL [Clean, OD]. (firmware) [FW]
- Switch step orders that invert the obvious reading: UK C30/C15 TopBoost BOOST = [HOT, COOL] (0 = HOT, default 1 = COOL); Victor Squid FOCUS = [ON, OFF] (0 = ON, default 1 = OFF). (firmware) [FW]
- Other step names: Victor Squid and Rabea Crunch/Lead TUBE = [6L6, EL34]; Freeman 100 Clean BRIGHT = [Normal, Bright 1, Bright 2]; CA 1Star EQ = [Normal, Thick, Thicker]; D-Cell Hisbert Ch2 MODE = [-, +], MIDCUT [OFF, ON] with INTENSITY and LEVEL knobs; US DLX 64/65 INPUT [Low, High], MODE [Normal, Vibrato, Vibrato Bright Off]; Headroom Hero INPUT [NORMAL, FET]; Misha Clean VOICE [Normal, Thicker], Misha Rhythm VOICE [Low, High]. (firmware) [FW]
- CA John's 2C Ch2/Ch3: GAIN PULL, SHRED, PRES PULL switches plus a 5-band graphic EQ (80, 240, 750, 2200, 6600 Hz, each 0–10). (firmware) [FW]
- Hidden parameters: Victor Squid MOD, H, B; Rabea MOD; Misha Lead TREBLE SHIFT; plugin amps carry hidden MUTE TRIG and DELAY (int 0–64). (firmware) [FW]
- Freeman 100 Rhythm/Lead FAT, C45, VOICE, SAT have no step names. Real BE-100 manual: FAT "fattens up the tone, ideal for single coil pickups"; SAT adds gain, compression and saturation; VOICE left = darker with more mids, right = brighter with bigger bass; C45 = alternate custom voicing. (official for the real amp, not QC) https://www.manualslib.com/manual/880520/Friedman-Be-100.html https://quadcortex.wiki/Freeman_100
- 50 parameters carry a firmware tooltip saying that changing them "will cause a brief mute" (full list in qc-internals.json, `brief_mute_parameters`). Amp ones: UK C15 Normal BRIGHT, UK C15 TopBoost BOOST, John's 2C Ch2/Ch3 GAIN PULL + SHRED, Victor Squid Ch1/Ch2 TUBE, Fortin Nameless INPUT + PULL GAIN, US DLX 64/65 MODE, SLO-100 BRIGHT + CHANNEL + CRUNCH, Amp Snob BRIGHT + DRIVE, Dumbbell ODS CHANNEL + PREAMP + BRIGHT + MID + EQ, Petrucci Piezo AIR, Petrucci Rhythm BITE + MID BOOST, Smooth Operator BRIGHT, Signature 83 BRIGHT + EQ, Headroom Hero INPUT + BRIGHT + MID, Rabea Crunch/Lead TUBE. Non-amp: Plini Drive MODE, Justa Boost BOOST, Tealbreaker MODEL, Colossus Fuzz MODE, Paragon MODE, Legendary 87 RATIO (all three), Douglas Shining Comp RATIO, Horizon Compressor INTENSITY, Flanger Engine WAVEFORM, Chorus Engine VOICE MODE, Plini Chorus MODE, Misha Modulator MODE, Cory Wong Wah AUTO-WAH, Spring Reverb Engine TANK TYPE. (firmware) [FW]
- Scene-switching such a switch delays the scene change by up to about a second; staff and moderators call it expected and advise two copies of the block toggled by scenes. (anecdote + moderator) [U:23332] [U:17511]
- Device Versioning (2.0.0): an updated device and its legacy version coexist; a green icon marks an available update; the version is chosen in the block's contextual menu. (official) [R2.0.0]
- Same-name id pairs: 1142–1147 replace 1109–1114 (D-Cell H4), 1148/1149 replace 1017/1098 (EV101IIIS EL34), 1150–1152 replace 1045/1099/1100 (Solo 100), 1167 (US DLX 65 Reissue) replaces 1094 + 1095, 27 replaces 5 (Green 808), 4005→4002, 7029→7007, 7031→7006, 9018→9017, 24006/24007→24003/24004. (firmware) [FW] [DL]
- Plugin (PCOM) devices "behave in exactly the same way as native Quad Cortex devices". (official) [PCOM] No official statement on engine or sound differences from legacy models.
- Fixes that matter for automation: 3.3.0 Fortin Nameless PULL GAIN/INPUT now follow scenes; 3.3.1 Dumbbell ODS no longer toggles channel or mutes when other devices are auditioned; 4.1.0 Nameless no longer mutes on scene switch and ODS no longer drops volume on certain parameter combinations. (official) [R3.3.0] [R3.3.1] [R4.1.0]
- Recto model: "try the master volume below 1 … it sounds and responds basically just like my Rev G. You have to add a lot of level afterwards". (anecdote) https://thegearforum.com/threads/nmd-ndsp-quad-cortex.3847/page-3
- Real Recto lore repeated on the community wiki (general amp knowledge, not a QC measurement): Modern mode has no negative feedback, the power amp saturates at very low master, raising master muffles highs — "reduce the Master Volume. Compensate with the Output control". (community, non-QC source) https://quadcortex.wiki/CA_Duo_Ch3

## 3. Cab blocks and IR loader

- A cab block holds two mic/IR slots. Per slot: power (bypass), `ir selector` (mic or IR), LEVEL, PAN 0–10 (L/C/R, default 5), DISTANCE 0–10, POSITION 0–10, `phi` (phase invert), GRID MODE. Block level: HPF 20–500 Hz (20 = OFF), LPF 500–20000 Hz (20000 = OFF), OUTPUT VOLUME −96..+12 dB. (firmware) [FW]
- Defaults: POSITION 4.0, DISTANCE 0.0, LEVEL 0 dB, phase off, GRID MODE on, HPF and LPF off. (firmware) [FW]
- POSITION: "where the microphone is, from the center of the cone to the edge of the cone". DISTANCE: "between close to the cab and far towards the room". Both are disabled when an external IR is loaded. Dragging the white circle sets both. (official) [M200] [M300] [TIPS]
- Numeric direction is not documented. Reading: POSITION 0 = centre (brightest), 10 = edge (darker); DISTANCE 0 = closest. Basis: wording order and the close-mic default of 0.0. (inference — measure the extremes)
- Plugin cabs use 0–1 ranges for POSITION and DISTANCE (Plini default 0.5/0.0, Petrucci 0.3/0.0; Gojira, SLO, Rabea, Misha, Tim Henson declare DISTANCE 0–1), native cabs 0–10. John Mayer cabs add ROOM SIZE (0–2), ROOM L LEVEL, ROOM R LEVEL (−40..+6 dB, default −14). (firmware) [FW]
- 4.1.0 changed the "EQ+ROOM parameter layout" for all plugin cab blocks. (official) [R4.1.0]
- Factory mic strings are `<prefix>_<cab>_<mic>`, e.g. `ML_412 Cali Stand OS S V30 90s_Dynamic 57`; prefixes seen: ML_, NG_, ME_, JP_. (firmware) [FW]
- Default slot pairs: Dynamic 57 + Ribbon 160 on 72 guitar cabs; Dynamic 57 + Ribbon 121 on 9 (Gojira 1/2, SLO-100, Nameless, Rabea 1–3, Tim Henson); Dynamic 57 + Condenser 414 on 4 (Gojira 3, Misha 1–3); Dynamic 57 + Ribbon 10 on 412 CA Trad S H30 15 and most bass cabs; Dynamic 421 + Ribbon 353 on the Petrucci cab. (firmware) [FW]
- Mic names seen: Dynamic 57, Dynamic 421, Ribbon 160, Ribbon 121, Ribbon 10, Condenser 414, Condenser 184, Ribbon 353 (Petrucci only). One user reports "Dynamic 7B" on one cab. (firmware + anecdote) [FW] [M102] [U:12314]
- The mic list differs per cab; a user counted six on most guitar cabs, and staff confirmed "some of the cabs have a slightly different selection". (official staff + anecdote) [U:12314]
- Neural DSP does not publish what each mic is modelled on. By naming: 57 = Shure SM57, 421 = Sennheiser MD 421, 160 = Beyerdynamic M 160, 121 = Royer R-121, 10 = Royer R-10, 414 = AKG C414, 184 = Neumann KM 184, 7B = Shure SM7B. (inference)
- Stereo: since 2.1.0 every cab has an (ST) version, "negating the need to add two cab blocks". 2.1.0 also redesigned the cab editor and added HPF/LPF to all cabs. (official) [R2.1.0]
- Global Bypass (device setting) bypasses Cab and IR Loader blocks on chosen rows in every preset; the 3.0 manual adds cab-type Neural Captures. (official) [M411] [M300]
- A cab slot can load a user IR ("Load IR"). (official) [M300] The IR or mic chosen in a cab slot can differ per scene. (anecdote, 2021; string parameters hold per-scene values in the preset format) [U:3869] [QANAT]
- IR Loader variants: Single/Dual × (M)/(ST), plus Lite versions since 2.1.0 — "Room reverb has been removed from these versions to reduce CPU consumption". (official) [R2.0.0] [R2.1.0]
- IR Loader parameters: MUTE, INVERT, LEVEL −96..+12 dB, HI PASS 20–800 Hz, LOW PASS 1000–20000 Hz, PAN (BALANCE on Dual ST), DELAY 0–25 ms; room section ROOM MIX 0–100 %, PRE DELAY 1–100 ms, REV HI PASS, REV LOW PASS, SIZE; GLOBAL OUTPUT. (firmware + official) [FW] [M300]
- "IR Loader blocks use less CPU than Cab blocks." (official) [M300]
- IR length: files are "automatically resized to 1024 samples (approximately 21 ms)" on upload. (official) [M411] Community reading: any WAV rate is converted to 48 kHz and truncated; use 48 kHz/24-bit; room or reverb IRs lose their low end. (community) [U:4642] [WFAQ]
- Storage: 2048 IRs, 30 folders. (official) [M411]
- Bug report on 4.0.1: a non-Lite IR Loader set to Mute passes signal as if bypassed. (anecdote) [U:22729]

## 4. Neural Capture blocks

- Parameters, identical for both engines: GAIN −24..+24 dB, BASS/MID/TREBLE −6..+6 dB, VOLUME −24..+24 dB, all default 0; hidden `file_name`. V1 = model 14000 ("Eltron 30"), V2 = model 14001 ("Capture 2"). (firmware) [FW]
- V1 and V2 share one parameter editor; V2 blocks show a double-layered outline. (official) [M411]
- GAIN is not the captured device's gain knob: "it is best to create a new Capture with higher gain settings on the original device rather than boosting the block parameters (for example, setting its GAIN to +24dB)". (official) [M411]
- Moderator: capture GAIN "shouldn't be any different than putting a Gain block in front of the capture"; the EQ centres are "120, 750 and 1000hz" (as written). (anecdote, moderator) [U:20769]
- Whether the 3-band EQ is before or after the model is undocumented; users infer post because drive captures respond like a post EQ. (anecdote) [U:11947] [U:7401]
- The knobs do not reproduce the captured device's controls; a capture is one setting. (community) [U:20769] [WFAQ]
- Capture drive tracks the level hitting the block, so IN LEVEL, input-block INPUT GAIN and guitar output all change it. (community) [U:3718] [U:7401]
- Calibration: IN 1 LEVEL "is for monitoring purposes only and does not affect the CAPTURE OUT training signal level"; aim IN 2 at about −12 dB peak. (official) [M411]
- CAPTURE OUT measured fixed at 1.774 V peak whatever IN 1 LEVEL is. (anecdote) [U:7401]
- Gain metadata 1–10 is computed from the device's saturation. (official) [M411]
- V1: trained on the unit in minutes, offline, "ideal for most amplifiers, cabinets, and overdrives" and for CPU-tight presets. V2: trained in Cortex Cloud through Cortex Control, about 10 minutes, 40 per day, better at fuzz clean-up, power-amp sag, compressors and blend knobs; "the resulting Capture V2 blocks use more CPU power". (official) [NCV2] [R3.3.0]
- V2.1 (2026-11-18) is a cloud-side training change; captures still appear as V2 and need no firmware update. (official) [NCV21]
- Capture types: Default, Amp, Amp + Cab, Combo Amp, Cab, and for V2 Overdrive, Fuzz, Compressor. (official) [NCV2] [R3.3.0]
- V1 under-captures cranked power-amp distortion (about 4 dB less apparent gain); Neural DSP support acknowledged the limit in 2022. (anecdote quoting support) [U:3718]
- The 669 factory V2 amp captures are mostly very hot and some include a drive pedal; that is stated only in the Cortex Cloud description. (community) [U:20180]
- Captures are not supported by Device Presets. (official) [M411]
- The capture file cannot change per scene; use two capture blocks and bypass them by scene. (anecdote, 2021) [U:3869]
- Usual shaping: boost or Gain block in front for drive, EQ block after for tone. (community) [U:20769]

## 5. DSP / CPU budget

- Neural DSP publishes no per-block CPU percentages. (official sources checked: manuals, device list, release notes)
- "Bypassing or disabling blocks does not reduce CPU consumption." (official) [M411]
- "The maximum load is up to 90%"; "Rows 1-2 and Rows 3-4 on The Grid utilise different processor cores"; reverbs and delays cost most; EQ, compressor, wah and FX loop cost least; amps vary widely within the category. (official) [M300]
- Each row pair has its own budget: presets fail at 33–48 % total when everything sits on rows 1–2, and moving blocks to rows 3–4 fixes it. (community) [U:4339] [U:16132] [U:22701]
- Live telemetry: per-block relative weights amp 0.5–0.6, capture about the same, stereo spring reverb 0.38, mono cab 0.18, drive/comp/EQ 0.08–0.16; stereo roughly twice mono; blocks are balanced across two cores per block rather than strictly per row. (anecdote, one project's measurement) [QCPU]
- Hardware: two Analog Devices ADSP-SC589. (anecdote) [QCPU] [U:22571]
- Cheaper choices named officially: IR Loader instead of Cab, Lite IR Loader instead of full, V1 capture instead of V2. (official) [M300] [R2.1.0] [NCV2]
- At the limit: Global EQ and Input Gate are disabled; the unit only offers device swaps "where there is enough CPU available"; an "Out of Resources" pop-up appears on load (4.1.1 fixed false ones). (official) [M411] [R2.0.0] [R4.1.1]
- Cortex Control computes CPU live for each slot while a device is dragged. (official) [R3.3.0]
- Grid limits: 4 rows × 8 slots; at most two side-chain devices per row pair. (official) [M411]
- Three Plini amps plus one cab reached about 85 %. (anecdote) [U:14706]
- The catalog `Padding cpu` attribute (0.15–3.5 per model) does not track cost (Dual (M) IR Loader 1.0 vs Dual (ST) 0.5). (firmware + inference) [FW]

## 6. Switching

- A preset change always has an audible gap because the chain is reloaded. (community, moderators) [U:7375] [U:8430] [U:17511]
- Gap measured 0.2–0.4 s in a DAW across small and large presets; another user reports about 1 s. (anecdote) [U:7375] [U:8430]
- Preset changes cut delay and reverb tails; there is no spillover setting. (community; manual has none) [U:17511] https://quadcortex.wiki/Spillover
- A Looper X loop survives a preset change if the next preset also has a Looper. (official) [M300]
- Eight scenes per preset; any parameter is assigned by tap-hold and then stored per scene; bypass state per scene; the scene active at save becomes the default. (official) [M411]
- Scene changes are gapless, with a very short crossfade when amps swap. (community) [U:17511] [U:6148]
- Scene-assignable beyond blocks: Input Gate Control, Lane Output Control, splitter/mixer parameters, tempo. (official) [M300] [QANAT]
- "When a parameter is assigned to an expression pedal, its values are excluded from Scene data." (official) [M411]
- A scene cannot swap the model in a slot. (community) [U:3869]
- SCENE BYPASS BEHAVIOR is a global setting with three modes; default saves every bypass change into the scene. (official) [M411]
- Two expression inputs; most parameters assignable with min/max; expression bypass modes Heel-Toe, Switch, Stop with up to 5000 ms delay; pedal position is not stored in presets. MIDI CC1/CC2 set EXP 1/2 position. (official) [M411]
- TRAILS switch on delays, reverbs and FX Loop blocks lets tails decay after bypass (inside one preset). (official) [M300] [FW]
- MIDI: CC43 values 0–7 recall scenes A–H; PC with CC0/CC32 recalls presets; Ignore Duplicate PC is CC62. (official) [M411]
- A bypassed amp and cab still pass dry signal down their row; with one amp per row, dry guitar reaches the output unless handled. (anecdote) [U:21595]
- 4.1.1 report: a brief loud spike when a scene swaps a parallel capture path for the Rols Jazz CH120; moderator advice is to keep bypassed devices' settings identical across scenes. (anecdote) [U:23568]
- 3.3.1 fixed scene changes that muted output or crashed in some user presets. (official) [R3.3.1]

## 7. Levels and USB

- Chain order: input → A/D → lane input → blocks → lane output → limiter → I/O output level → D/A → volume wheel → jack. (community) [WGS]
- Lane Output Control (tap-hold an output block): VOLUME, PAN, MUTE, SOLO; stored in the preset; scene- and expression-assignable. VOLUME range −40..+12 dB. (official; range from project notes) [M411] [QANAT]
- Neural DSP support's answer for matching presets is the lane output level. (anecdote quoting support) [U:5526]
- I/O output levels and Global EQ are global. The volume wheel is an attenuator: maximum is unity. (community; "official word" per moderator) [U:9098] [WGS]
- Each output has a limiter with a gain-reduction meter: "Allows you to bring up the level without allowing the peaks to clip the output." (official) [M102] [M200]
- Red on the meter warns before clipping; the limiter engages later. (community) [U:13709]
- Maximum output: XLR 1/2 +9.5 dBu; TRS 3/4 +15.5 dBu balanced, +9.5 dBu unbalanced; sends +9.5 dBu; headphones 300 mW. (official) [M411]
- No official loudness or peak target for presets exists. Community targets: 12 dB headroom for clean and 6 dB for distorted at the lane output; factory presets are roughly uniform and serve as a reference. (community / anecdote) [WGS] [U:5526]
- Equal meter level is not equal loudness: high-gain scenes read the same and sound far louder at stage volume; cut around 6–8 kHz before trimming. (anecdote) [U:20554] [QLVL]
- Parallel lanes sum; N amps at unity clip. (anecdote, project note) https://raw.githubusercontent.com/lexasoft123/qc-mcp/HEAD/.claude/skills/review-preset/SKILL.md
- Global EQ: five parametric bands, ±12 dB, 20 Hz–20 kHz, assignable to Out 1/2, Out 3/4 or both, with its own output level. (official) [M411] [R2.0.0]
- 4.0.1 fixed Global EQ not being recalled after a power cycle. (official) [R4.0.1]
- Headphones: recommended up to 300 Ω; hiss is reduced by HP LEVEL −5 dB with the wheel up; headphone-only setups hum because the unit takes its ground from connected gear. (community wiki, Neural DSP support text) [WFAQ] https://quadcortex.wiki/Resolving_noise_problems
- USB audio: class 2.0, 24-bit, fixed 48 kHz, 8 in / 8 out. Host inputs 1/2 = dry DI of analog In 1/2; 3/4 = analog Out 1/L, 2/R; 5–8 = grid USB Outputs 5–8. Host outputs 1/2 → Out 1/2; 3/4 → Out 3/4; 5–8 → grid USB Inputs 5–8. (official) [M411]
- Reamp: route the DI track to host Output 5, set the grid input block to USB Input 5, record host Inputs 3/4. (official) [M411] [USB]
- A DRY/WET switch in I/O Settings swaps what USB 1/2 and 3/4 carry; host channel names update only after reconnect or power cycle. (official) [M411]
- Host inputs 3/4 follow the analog output stage, so master volume changes the reading; measure on grid USB outputs 5–8. Host outputs 1–4 go straight to the jacks, bypassing the grid. (anecdote, project) [QLVL]
- 4.1.0 fixed persistent artifacts after dropped USB packets. (official) [R4.1.0]

## 8. Plugin Compatibility (PCOM)

- Required plugin per device family (official) [DL] [R4.1.0], with catalog `plugin_id` (firmware) [FW]:
  - Archetype: Plini X (3.0.0, id 3): Plini Clean/Crunch/Lead, Plini Cab, Drive, Comp, Octaver, Pre Delay, Delay, Chorus, Reverb.
  - Archetype: Gojira X (3.0.0, id 10): Gojira CLN/RST/HOT, Cab 1–3, OD, DRT, PHSR, CHR, DLY, REV, REV Shimmer, WOW, OCT.
  - Soldano SLO-100 X (3.1.0, id 11): Soldano SLO-100, SLO-100 Cab, Overdrive-1/2, Compressor, Chorus, Delay, Reverb.
  - Fortin Nameless Suite X (3.1.0, id 1): Fortin Nameless, Nameless Cab, Grind, Hexdrive, Zuul, Nameless Delay, Reverb.
  - Archetype: Cory Wong X (3.2.0, id 9): D.I. Funk Console, The Clean Machine, The Amp Snob, Cab 1–3, The Tuber, The Big Rig Overdrive, 4th Position Compressor, Wah, The Postal Service, The 80s, Delay-y-y, The Wash, The Wash Shimmer.
  - Archetype: Nolly X (3.2.0, id 5): Nolly Clean/Crunch/Rhythm/Lead, Cab 1–4, Overdrive-1/2, Compressor, Graphic-9, Delay-1, Delay-2, Reverb.
  - Parallax X (3.2.0, id 4): Parallax, Parallax Cab.
  - Archetype: John Mayer X (4.1.0, id 21): Smooth Operator, Headroom Hero, Signature 83, Three-In-One, three cabs, Justa Boost, Halfman OD, Tealbreaker, Antelope Filter, Compressor, Millipede Delay, Dream Delay, Harmonic Tremolo, Spring Reverb, Studio Verb Hall/Plate.
  - Archetype: Petrucci X (4.1.0, id 13): Piezo, Clean, Rhythm, Lead, Cab, Overdrive, Compressor, Wah, Volume, Phaser, Chorus 1/2, Flanger, Delay, Reverb, Reverb Shimmer.
  - Archetype: Misha Mansoor X (4.1.0, id 20): Clean/Rhythm/Lead Amp, Cab 1–3, Precision Drive, Chaos, Horizon Compressor, Laser, Dual Octaver, Modulator, Tape Echo, Stereo Delay, Reverb, Reverb Shimmer.
  - Archetype: Rabea X (4.1.0, id 15): Clean/Crunch/Lead Amp, Cab 1–3, Colossus Fuzz, Paragon Overdrive, Twin Blade Dual Compressor, Chaos Bed Octaver, Atlas Delay, Aeons Reverb.
  - Archetype: Tim Henson X (4.1.0, id 12): Acoustic Amp, Crunch Amp Ch1/Ch2, Lead Amp, Cab 1–2, Boost, Overdrive, Compressor, Chorus, three delays, Reverb, Reverb Shimmer.
- Catalog names differ from device-list names (catalog "Petrucci Rhythm", "Misha Clean"; device list "Rhythm", "Clean Amp"). Every licensed model carries a `plugin_id` attribute; use it, not the name. (firmware) [FW] [DL]
- Plugin-derived devices free for everyone (no `plugin_id`): Multivoicer, Glitch, Overlord Synth (was Mono Synth), Doubler, Plugin Doubler, Plugin Blend, Transparent Blend, Plugin Gate, Plugin Graphic-9, Plugin Parametric-4, Transpose. (official + firmware) [DL] [R4.1.0] [R3.0.0] [FW]
- Licences are checked against iLok when the unit logs in to the Neural DSP account; one internet check is enough and they persist offline until another account is linked; 14-day trials do not count. (official) [M411]
- "Plugins you do not own will remain locked in the Virtual Device List and cannot be loaded onto The Grid." (official) [M411]
- A licence covers three Quad Cortex and three mini units; on a fourth, plugin blocks "will appear locked in the Virtual Device List and will be bypassed". (official) [M411]
- Plugin Presets also need the perpetual licence, on the unit and in Cortex Control. (official) [M411]
- Plugin models are in the catalog on every unit; a block without its licence loads but stays silent. (anecdote, project note) [Q41]
- All non-plugin devices are released "for all Quad Cortex users"; legacy versions stay selectable through Device Versioning. (official) [R3.3.0] [R4.1.0] [R2.0.0]
- "Every currently available X-updated plugin is now compatible." Darkglass Ultimate, Fortin NTS, Tom Morello and Mantra will not come to the unit; Tone King Imperial MKII and Morgan Amps Suite are next for X updates. (official) [PCOM] [DU58]

## 9. Other unit-specific tips and CorOS 4.x surprises

- Delete unused blocks; orphaned or bypassed amps still draw CPU. (official + project note) [M411] [QCL]
- 4.1 Device Presets: more than 2,000 factory presets; loading one replaces every parameter of the block, keeps scene assignments, and recalls bypass and expression assignments. A save is refused when the values match an existing preset. (official + anecdote) [R4.1.0] [U:23098] [Q41]
- Earlier custom defaults were kept as a user Device Preset named "My Default"; each device also has a "Neural DSP Default". (official) [R4.1.0]
- "Dynamic Latency Compensation: Disabling this feature may help prevent phase issues when bypassing devices." (official) [M411]
- For a lead boost, build the base tone with a −3 dB cut and let the boost remove it, so the output keeps headroom. (community) [WGS]
- FX Loop sends default to line level (unity); lower by about 5 dB for pedals. (community wiki quoting Neural DSP staff) [WFAQ]
- Outputs 3/4 need TRS cables for unity; a TS cable gives 6 dB less. (community) [WGS]
- Realism tips collected by the community: high-pass 50–100 Hz and low-pass 6–10 kHz, compressor last for "sag", a power-amp capture after the amp for power-stage saturation. (community) https://quadcortex.wiki/Realistic_amp_feel/tone
- 4.1.1 fixes: false "Out of Resources" pop-ups, artifacts when loading certain Device Presets, Minivoicer level drop at high MIX. (official) [R4.1.1]
- 4.1.0 added parameters to existing devices: Doubler/Plugin Doubler INPUT (three-way, left/right channel of a stereo source) and Plugin Blend/Transparent Blend BLEND LEVEL; in the catalog both are appended after the older parameters. (official + firmware) [R4.1.0] [FW]
- "Save As…" in Cortex Control 4.1.0 selects the nearest empty slot instead of the loaded one. (official) [R4.1.0]
- Open 4.1 reports: Freeze (block and Vintage Digital) fading instead of holding; wet-only output through the headphone jack into a PA; USB phone audio stopping. (anecdote each; 23435 and 23135 seen only as forum search excerpts) [U:23435] [U:23681] [U:23135]
- The Gear Page thread could not be read (redirects to a paid crawler gateway); r/QuadCortex is blocked to the fetcher.

## Unverified

- Numeric direction of POSITION and DISTANCE (which end is centre / close).
- What GRID MODE on a cab slot does, and the meaning of the mic-string prefixes ML_/NG_/ME_/JP_.
- Which real microphones the cab mics model (naming convention only); what "Ribbon 353" is; the complete mic list per cab.
- Whether the capture EQ is pre or post the model, and its true centre frequencies (one moderator post gives 120/750/1000 Hz).
- Per-block CPU percentages; the meaning of the catalog `Padding cpu` attribute; any hard cap on reverbs per row pair in 4.x.
- Any engine difference between legacy and PCOM amps; whether there is a newer "cab engine" beyond the 2.1.0 redesign.
- Which numeric value maps to which position of Freeman FAT/C45/VOICE/SAT and other unlabelled switches.
- How a normal user preset behaves on a unit lacking the plugin licence (official text covers only the device list and the fourth-unit case).
- Whether Global EQ affects the USB wet channels and the headphone output.
- Preset recall time on 4.1.1 for large presets (only 2022–2023 anecdotes).
- Discrete steps of the impedance control between 10 kΩ and 10 MΩ.
- r/QuadCortex could not be read (blocked to the fetcher); The Gear Page was not read directly. PCOM page FAQ answers are not in the static page.
