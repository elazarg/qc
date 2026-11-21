# Tone Lab: whole-codebase review

November 21, 2026, branch `rewrite` at `f702f03`. This review covers everything in `lab/`, the launcher, the tests, the docs and the repository itself. It judges the app as what it is: a tool for one player on his own machine, built mostly by coding agents he directs. Product concerns (other platforms, strangers, installers) are left out on purpose.

[CODEBASE_REVIEW.md](CODEBASE_REVIEW.md) holds three earlier rounds on the desk and the unit layer. Nothing here repeats them. This review looks at what those rounds did not: the main flow in ordinary use, the measurements, the assistant, the side features, the tests, and whether the code is in proportion to its one user.

## The short version

The foundation is good and should be kept: the chain model, the guarded unit, the engine in a process of its own, small JSON files written atomically, and a fast suite on a simulated unit. The three earlier rounds made the unit layer hard to hurt.

The problems are now elsewhere:

1. **The main flow does not do what the README says in three everyday cases.** A song request typed while B holds a sound revises B instead of starting a round. Sounds brought in with "Work on this", and revisions, are never listened to, so Difference and loudness matching have nothing to go on. One design that cannot be measured stops the whole round from being heard.
2. **The taste model states preferences that are not there.** One press of Keep is counted as two decisions, and with random choices the model calls something "strong" in most runs at 8 to 20 decisions. Its sentences reach the designer's prompt.
3. **Several measurements do not measure what their name says**, by the rule this project set itself: the brightness word, the tail word, the decay time, and distortion and movement read behind a delay.
4. **Revisions lose things.** A reply that moves a block to another column drops its bypass state and its bound IR or capture; a second path added in a revision gets no mixer; the assistant cannot switch a block on or off; a bass request is offered no bass amp or cab.
5. **`tone_lab/` cannot be deleted yet.** The new app reads its knowledge tables from inside it and fails silently without them.
6. **The side features were ported but not proven on the unit.** The Guitar Learner very likely cannot reach the unit while the desk holds it, and live gestures mostly do not fire.
7. **The safety checks now sit on the hottest path.** One A/B click costs 4 writes and 17 reads in the simulation. Nobody has timed it on the unit.
8. **"One window" holds for the desk only.** Twelve other windows open from it, with file dialogs, pop-up menus and shortcuts, and four status lines tell the player to press keys that no longer do anything.

I would not merge to `main` before items 1, 2 and 5 and a session on the unit (next section).

How to read the findings: **reproduced** means a script showed it on the simulated unit or on synthetic signals; **read** means it follows from the code and was not run; **rig** means only the real unit can settle it. No part of this review touched the unit, the audio driver, the Codex CLI or the data folder.

## A session on the unit settles the open questions

Everything since the rewrite was verified on the simulator. These are the things only the unit can answer, in the order I would check them:

1. **Time one A/B click and one first build.** `Unit.run` asks the unit where it is before every write ([lab/qc/unit.py:203](../lab/qc/unit.py#L203)). Counted on the simulator: a first build of A and B is 45 writes with 50 position reads and 9 full preset reads; an engine-level switch of side is 4 writes, 7 position reads and 4 preset reads; one click on the desk sends `level`, `level`, `play` and totals 17 reads. The code's own figure is about 0.1 s per position read, which would put a click over a second before any preset read, against the README's "under a second" and the engine docstring's "about 20 ms". If the measured time is long, see [Proportion](#proportion-the-guards-and-the-hot-path).
2. **Guitar Learner → Record clip, with the desk open.** The engine holds the unit with an exclusive handle; the learner opens a second one from another process ([lab/learner/inputs.py:50](../lab/learner/inputs.py#L50)). I expect "sharing violation" and a take saved as "routing needs checking".
3. **The three items carried from the earlier review**: Stop and the ownership check during real playback, the level read-back, recording through ASIO.
4. **The probe behind a delay.** Put a single delay (500 ms, mix about −6 dB) after a clean amp and listen silently. On synthetic signals this reads as intermodulation −45 dB and "movement: level 28%, pitch 6 cents" where the true answer is none.
5. **Whether the unit rounds stored values.** `TOLERANCE = .006` assumes it does; the simulator stores exact floats, so the second pass of `plan_values` and the cache comparison have never met a rounded value.
6. **The taskbar.** The new desk's process never sets the app identity the launcher sets, so the window probably shows as a separate Python button.

## Do first

### The main flow

- **A song or artist request revises B.** `_wants_new` knows nine lead-in words and " - " ([lab/desk/studio.py:1291](../lab/desk/studio.py#L1291), used at [:667](../lab/desk/studio.py#L667)). With any sound on B, which is always after first use, "Comfortably Numb, the second solo" (the README's own example) becomes "Revising…": no round, no song. Let the assistant decide between revising and a round in one reply, or start a round unless the text reads as a change to B. Add a test with the README's sentence. *Reproduced.*
- **Silent listening covers only the designs of the round on show, and starts only when A and B have just been made ready** ([lab/desk/studio.py:1056](../lab/desk/studio.py#L1056), called from [:290](../lab/desk/studio.py#L290)). A preset brought in with "Work on this", its copy on B, and every revision are never measured, while the Difference panel says "That happens by itself while the unit is free." A round that arrives while Guitar plays is not heard after Stop either. Make one function that says what needs hearing (A, B, then the round's designs) and call it from `poll` when the unit is idle. *Reproduced.*
- **One design that cannot be measured starves the round** ([:1062](../lab/desk/studio.py#L1062)). The failing design is retried on every step, the other two are never measured, "three more" is never prepared, and each retry logs a trace. Record the failure on the sound and move on. *Reproduced.*
- **A play that fails leaves the desk saying it plays** ([:322](../lab/desk/studio.py#L322) has no failure handler). The tile stays lit, `watch` runs every 1.5 s, and listening stays blocked until the button is pressed twice. *Reproduced.*
- **The working line is used as a lock** ([:280](../lab/desk/studio.py#L280), [:296](../lab/desk/studio.py#L296), [:461](../lab/desk/studio.py#L461)). Stepping to another design while a revision is in flight blanks it and hides Cancel while the request still runs. Keep a set of activities and derive the text and Cancel from it. *Reproduced.*
- **A remark, Undo, the same remark again applies the inverse of the first remedy** ([:634](../lab/desk/studio.py#L634)): "muddy" gives tightness +0.3, Undo, "muddy" gives tightness −0.3 and drive −0.2. `self.remark` survives Undo and manual turns. *Reproduced.*
- **The song sticks to later rounds** ([:779](../lab/desk/studio.py#L779); `song` is in `KEPT`). After one song round, "make a clean funk sound" produced designs tagged with the earlier song, across restarts. *Reproduced.*
- **Songs whose names have no Latin letters or digits all share `untitled.json`** ([lab/sound/sounds.py:144](../lab/sound/sounds.py#L144)). Two Hebrew titles give the same slug, and the second song's part replaces the first's. Keep Unicode word characters, or add a short hash. *Reproduced.*

### Revisions and designs

- **A revision that moves a block loses what the assistant never sees.** Kept values are matched by row and column ([lab/sound/design.py:339](../lab/sound/design.py#L339)). "Add a boost in front" shifts everything right: a bypassed fuzz comes back on, a cab loses its microphones, a capture block loses its file. Match kept blocks by model in row order, and report a problem when a capture or IR block ends up unbound. *Reproduced.*
- **A second path added or changed in a revision gets no mixer** ([:347](../lab/sound/design.py#L347)): the mixer, `blend` and the polarity flip run only for new designs. Two amps that `knowledge.opposed` says cancel are then blended out of polarity. *Reproduced.*
- **The assistant cannot switch a block on or off.** The schema has no `off` ([:240](../lab/sound/design.py#L240)) though `as_form` shows it; "turn the delay on" returns the same chain after a full call. *Reproduced.*
- **With the Bass instrument no bass amp, cab or drive is offered** ([:83](../lab/sound/design.py#L83)). `offered("Ampeg SVT rock bass", "bass")` returns ten guitar overdrives, modulation, delay, reverb, a compressor, an EQ and a utility; the catalog has 37 bass-category models and none of them has a `gear` entry. *Reproduced.*
- **On a scene-switched preset the Shape knobs act as if scene A's on/off states held in every scene** ([lab/sound/knobs.py:371](../lab/sound/knobs.py#L371), [:465](../lab/sound/knobs.py#L465)). A fuzz that is off in A and on in the lead scene never answers Distortion, Brightness or Texture, because the desk never names a scene. *Reproduced.*
- **Turning a knob down on a parked effect brings the effect in** ([lab/sound/knobs.py:260](../lab/sound/knobs.py#L260)): "shorter tails" on a reverb at MIX 0 raises MIX to 4.8%. Audition only for positive values. *Reproduced.*
- **The prompt and the validator disagree.** `designer.md` allows two amps into one shared cab and points at an amp's OUTPUT for scene level; the code rejects both, each costing a repair call from the day's 80. The code accepts a split with nothing on the second row (dry guitar beside the amp) and offers a stock cab's internal switches and an unclamped −96 to +12 dB output. *Reproduced.*

### The taste model

- **One press of Keep is two decisions.** `mark()` records a choice with its audition context, and `comparisons()` derives the same pair again from the round's marks with no context, so the keys differ and both stay ([lab/player/taste.py:197](../lab/player/taste.py#L197), [lab/desk/studio.py:881](../lab/desk/studio.py#L881)). Four presses gave eight decisions and `enough = True`. *Reproduced.*
- **The strength words are not valid at small n** ([lab/player/taste.py:298](../lab/player/taste.py#L298), [:350](../lab/player/taste.py#L350)). Strength is a normal p-value from weight over bootstrap spread; with few decisions and about 20 collinear quantities every resample is separable. With random choices, the share of runs in which at least one quantity is called strong:

  | Decisions | 20 realistic quantities | 5 independent quantities |
  |---|---|---|
  | 8 | 78% | 33% |
  | 12 | 55% | 22% |
  | 20 | 45% | 5% |
  | 40 | 14% | 0% |

  The repo's own test passes because it uses 5 quantities, 30 decisions and 5 seeds. An exact sign test with a correction for the number of quantities fired in at most 3% of the same runs. A planted preference is found with the right sign, but the sentence usually names a neighbour of the real quantity. *Reproduced, 100 runs per cell.*
- **What to do.** Drop a marks-derived pair when a recorded choice exists for it. Replace the bootstrap z with a sign-flip permutation test or the exact sign test. Fit on about five axes (amount of distortion, order balance, compression, brightness, tail) and name the axis in the sentence. Until then, keep the model's sentences out of the designer's prompt.

### Before deleting `tone_lab/`

- **The new app's tables live in the old package**: `SHARED = REPO / "tone_lab" / "knowledge"` ([lab/knowledge.py:26](../lab/knowledge.py#L26)). Without them nothing raises: locked models drop from 143 to 45, `cost()` is None, and designs that do not fit pass the budget. `lab/tools/budget.py:27` writes `costs.json` there. Move the four JSON files under `lab/`, and make a missing table an error that `doctor` reports.
- **Three more ties to the old layout**: the unit's named settings in `tone_sessions/device-presets.json` ([lab/tools/named.py:52](../lab/tools/named.py#L52)), the startup log in `tone_sessions/` ([desktop_launcher/start.pyw:30](../desktop_launcher/start.pyw#L30)), and the Guitar Learner's whole dataset in `tone_sessions/` ([lab/learner/folders.py:20](../lab/learner/folders.py#L20)). The last one ignores `TONE_LAB_HOME`, is wiped by `git clean -fdx`, and is why `--simulated` still records into the real dataset. Move all three to the data folder with a one-time copy, as `setup.move_old_home` does.
- **`knowledge.SOURCES` still tells the player to run `python -m tone_lab.budget`** ([lab/knowledge.py:72](../lab/knowledge.py#L72)), and six `research/*.py` scripts import `tone_lab`.
- **Tests worth porting first.** Sixteen old test files have near-identical `test_lab_*` copies and can go. Twelve cover behaviour the new tests do not; the ones that matter: `knowledge.weigh`/`cost` against the real `costs.json`, `gear.json` and `controls.json` integrity against the catalog, `knowledge.locked` and `opposed`, `design.to_chain` refusals, killing Codex on cancel, `Loop` wrap-around, and listening stopping when the player switches preset. (This table came from a read-and-grep comparison and was not re-verified line by line.)
- **Then remove the scaffolding**: `--old`/`--new`, `TONE_LAB_DESK`, `launcher.json`, and, once History is confirmed complete, `lab/migrate.py` (540 lines) and `setup.move_old_home`. Until then `first_run` reruns its checks, about 3.5 s before the window appears, on every start until all four pass.

### Faults you cannot see

- **The engine's traces are thrown away.** The worker prints them ([lab/qc/host.py:86](../lab/qc/host.py#L86)); under `pythonw` there is nowhere to print, and only the message crosses to the desk. `errors.log` then holds a trace of the desk receiving the error, not of the fault. Send the formatted trace with the reply and log that. *Reproduced.*
- **A fault in a click logs `<traceback object at 0x…>`**: `Desk.failed` passes the traceback object straight to the log ([lab/desk/window.py:875](../lab/desk/window.py#L875)). *Read; reproduced by the desk reviewer.*
- **Expected outcomes are logged as faults.** Stop, Cancel and the retry every five seconds while the unit has unsaved edits each write a trace, because errors cross the process boundary as bare strings and the desk cannot tell `Stopped` or `Moved` from a crash. Send the exception's type name too.
- **Learner faults are written nowhere**: its logger has only a `NullHandler` ([lab/learner/folders.py:22](../lab/learner/folders.py#L22)) and the worker's stderr is discarded.
- **A bad file named by `session.json` stops the app from starting, every time** ([lab/desk/window.py:109](../lab/desk/window.py#L109)): a missing design of the round on show, a sound with a field this version does not know, or a JSON list. Guard `restore` and the first draw; on failure set the session aside and start empty. *Reproduced.*
- **Closing while the app's own "three more" is with the assistant keeps the process alive** for the length of the call (up to 180 s), with the single-instance port still bound, so relaunching does nothing. Background requests have no cancel signal ([lab/desk/studio.py:760](../lab/desk/studio.py#L760)). *Reproduced.*
- **Anything else listening on port 47231 makes Tone Lab exit silently** ([lab/desk/window.py:1011](../lab/desk/window.py#L1011)). Have the desk answer with a token. *Reproduced on a private port.*

## Proportion: the guards and the hot path

Three review rounds each added checks to the unit layer, and each was right about the fault it closed. Together they changed the cost of the action the app exists for. Comparing two sounds by ear depends on switching fast, and one click now reads the unit 17 times.

The fault the per-write check prevents is a parameter landing on a preset the player selected by hand between two writes. That edit is unsaved and visible on the unit. For a build of 45 writes over many seconds the check is cheap insurance. For a switch of side, which is four writes inside a few tens of milliseconds, it is most of the cost.

What I would do, after timing it on the unit:

- Give the switch of side its own path in the engine: one position check, the level and mute writes, the route writes, one read-back. That is 2 reads against 11.
- Stop reading the preset twice in a row: `_play` reads it and `apply` reads it again ([lab/qc/engine.py:172](../lab/qc/engine.py#L172), [lab/qc/unit.py:214](../lab/qc/unit.py#L214)).
- Stop the desk sending `level` twice before every `play` when the levels have not changed ([lab/desk/studio.py:324](../lab/desk/studio.py#L324)).
- Keep the per-write check on builds, saves and everything that runs in the background.

The same question applies to the desk. `studio.py` grew by about 200 lines of guards across the rounds, and "does this measurement hold for what plays now" is now written four times with different fields ([lab/desk/studio.py:356](../lab/desk/studio.py#L356), [:477](../lab/desk/studio.py#L477), [:496](../lab/desk/studio.py#L496), [:1066](../lab/desk/studio.py#L1066); the last one omits the scene). One function, used by all four, would be shorter and would have prevented two of the findings above.

## Measurements against the naming rule

The rule: a measurement is named for the quantity measured, an inference is labelled as one, and a direct test signal is preferred to a proxy. The numbers themselves are mostly right (harmonics, LUFS, peaks and the third-octave spectrum are exact on known inputs). The words and a few derived measures are not.

| Name | What is computed | Verdict |
|---|---|---|
| Brightness word, `brightness_oct` | log ratio of the spectral centre to the dry's ([lab/hearing/measure.py:401](../lab/hearing/measure.py#L401)) | A low shelf of +6 dB with the highs untouched reads "dark"; +12 dB above 3 kHz reads "balanced". The module's docstring says brightness is never from the centre alone. Use band levels above 1 kHz against 200 Hz to 1 kHz. |
| Tail word, `tail_db` | level after the phrase ends against the phrase | One reverb reads "wet", "a touch of room" or "dry" depending on how the phrase ends. Take it from the click. |
| `decay_s` | 3 × the time from −5 to −25 dB of a curve that includes the direct click ([lab/hearing/probe.py:258](../lab/hearing/probe.py#L258)) | Right only when the wet energy is at least the direct. At wet −20 dB it reads a quarter of the true time; a single echo reads three times its delay. 6.0 is a sentinel fed to the taste model as a number. |
| `distortion_db` and its word | harmonics of one 196 Hz tone at the output | The number fits. The word is an inference from one frequency: a drive behind a 720 Hz low cut reads "edge of breakup" at 196 Hz and −18.5 dB at 784 Hz. Add a second tone. |
| Soft-tone distortion | the same, on the softer tone | Measured inside the louder tone's echoes: behind a 400 ms delay "crunch" becomes "overdriven". Play soft before loud and lengthen the gaps. |
| `intermod_db`, level and pitch wobble | products and envelope swing over 0.5 s | A linear delay alone reads as both. Wobble is blind below about 3 Hz, which is where chorus and phaser rates are. |
| `attack_ms` | 10 to 90% time to the highest peak within 250 ms | It is time to peak: an in-phase echo reads 148 ms where the dry reads 35. |
| `compression` | 1 − (loud − soft)/12, clipped to 0..1 | Fits, but an expander or gate reads 0.00, and a gate that closes on the soft pass raises a routing error. |
| `even_odd_db` | even over odd harmonic power | Unbounded (+116 dB for a second harmonic alone) and 0.0 when there are no harmonics. |
| Guitar profile | band levels and the single largest sample over all takes ([lab/hearing/profile.py:113](../lab/hearing/profile.py#L113)) | It reads the playing as the pickup: the same phrase an octave up comes out "brighter and harsher", one hard note adds 5 dB of "hotter". The designer is told these as facts. |
| "Volume" in the Guitar Learner | dry level in dBFS against taught levels | Picking 4 dB softer moves the knob estimate off the true value while it is headed "Strong estimate". |
| "Guitar input calibration" | a classifier fitted from labelled examples | Nothing is calibrated. The app has a real calibration (the USB round trip). |
| "Confidence: strong / good / tentative" | closeness to a taught template | Call it a close, near or far match. |
| "chosen" in listening history | started by a click with shuffle mode off | A track clicked 20 times with shuffle on reads 0; the standard export has no such field, so every line says 0. |
| `knowledge.drive_curve`, `crest_by_gain` | crest factor by gain setting | The card key carries distortion words ("GAIN 5 crunch") for a crest-factor measure. |

The guitar profile matters most to the stated wish to understand the guitar's raw output. It needs one fixed gesture per pickup setting, level as percentiles and LUFS, the per-band gap against a reference setting (`measure.gap` exists and has no caller), and the noise floor in the silences.

Calibration of the USB round trip is keyed by firmware only ([lab/hearing/listen.py:39](../lab/hearing/listen.py#L39)). A change of ASIO buffer size moves it and nothing notices; 10 ms early and the click is missed. Check the click's arrival on every listen. *Read.*

## One window

The desk's own window keeps the rule and has a test for it. Everything around it does not:

- **Twelve other windows**: the Guitar Learner, calibration and ten tools, all `tk.Toplevel` ([lab/tools/common.py:218](../lab/tools/common.py#L218), [lab/learner/window.py:162](../lab/learner/window.py#L162), [lab/learner/calibration.py:1053](../lab/learner/calibration.py#L1053)).
- **Six file dialogs**: learner 577, calibration 652, `tools/listening.py:77`, `pod.py:57`, `search.py:464`, `workbench.py:323`.
- **Pop-up menus**: `common.menu_choice` ([lab/tools/common.py:173](../lab/tools/common.py#L173)), used in six tools, and four `tk.Menu` of their own.
- **Shortcuts**: Escape, Ctrl-R (starts a recording) and Ctrl-O in the learner ([lab/learner/window.py:167](../lab/learner/window.py#L167)); arrows and Home in the Tone field.
- **Status lines that name keys that do nothing**: "press N" ([lab/desk/studio.py:798](../lab/desk/studio.py#L798)), "Z undoes it" ([:583](../lab/desk/studio.py#L583), [:722](../lab/desk/studio.py#L722)), "press X" ([:995](../lab/desk/studio.py#L995)).
- **A symptom**: the Controls tool's "Save to QC" opens the name row on the desk behind the tool's own window.

Moving the tools into the desk needs no engine change, because every tool already builds into `self.body` and talks through `self.call`. `Window.__init__` takes a parent frame; the desk hosts the frame beside the tiles; chips replace the menus; a path line or a list of known folders replaces each dialog.

Several tools duplicate a desk panel, and folding is cheaper than porting:

| Tool | Desk feature | Keep |
|---|---|---|
| Library | Sounds panel | The panel; move "Use as reference" and "Leave it on the unit" into it |
| Levels, A and B mode | the desk's loudness matching | The desk's. The tool's Apply is undone at the next play (*reproduced*) |
| Field, "Measure A and B" | Difference | Difference |
| Controls: every knob, base, reset, save | Shape | Shape; keep Controls' versions and read-back |
| Soundcheck's blind comparison | Workbench | Workbench |
| `cli record` | Phrase panel | The panel. The CLI writes three channels as the "dry" take (*reproduced*) |

## The Guitar Learner

- **Two learners answer one question.** The Guitar Learner proper (dataset, template matching, live tracker) and "Input calibration" with gestures (about 1,800 lines) have two example stores, two profile formats, two estimators, two live listeners and two vocabularies for pickup settings. Keeping both features on one store and one vocabulary, with gestures reading the live tracker's estimate, is the largest saving in the package.
- **A cleanly played gesture is mostly missed live.** The live window and poll (.48 s, .14 s) leave gaps longer than the detector's `max_gap` of .4 s ([lab/learner/gestures.py:38](../lab/learner/gestures.py#L38), [:116](../lab/learner/gestures.py#L116)). Of 32 ideal gestures, the rehearsal cadence caught 32 and the live cadence 12. *Reproduced.*
- **Gestures work for 20 s after "Gestures on" and never again**, while the worker keeps the input open and the button still reads as on ([:115](../lab/learner/gestures.py#L115)). *Reproduced.*
- **Starting gestures a second time returns the first session**, and the calibration panel then stays disabled until restart ([:469](../lab/learner/gestures.py#L469)). *Reproduced.*
- **Who holds the audio driver is settled by convention.** `LearnerSeat.submit` queues `quiet` and starts its job at once; background listening, the Shape check and every tool can open the driver while the learner records. One audio-owner token on the studio would settle it. *Read.*
- **The learner is hard-wired to one guitar** ([lab/learner/window.py:27](../lab/learner/window.py#L27)): with another instrument chosen on the desk, takes are still taught into the first one's dataset.
- **Calibration examples are absolute paths to WAVs, not copies** ([lab/learner/calibration.py:685](../lab/learner/calibration.py#L685)); moving the repo breaks "Learn settings" for that control.
- The fix for item 2 of the rig session also deletes code: ask the engine for the channels (it already records dry and wet, [lab/qc/engine.py:430](../lab/qc/engine.py#L430)), then remove `inputs.connect`, `channels_from` and `editor_running`, which repeat `lab/qc/device.py`.

Good and worth keeping: abstention is real (20 of 20 untaught spectra got no answer), the dataset copies and stages its writes, the worker is a separate process with a heartbeat, and a gesture can only propose a quarter-turn of one Shape knob, never a save.

## Side tools, gear and the command line

- **Tone search cannot finish if one sound cannot be built**, and discards every render made before it; closing the window mid-run does the same ([lab/tools/search.py:329](../lab/tools/search.py#L329)). The default scope is every sound, so one click queues a build for each, with no Stop. *Reproduced.*
- **"Leave it on the unit" recalls a preset without the unsaved-edits check** that every other path makes ([lab/tools/library.py:76](../lab/tools/library.py#L76)). *Read.*
- **Any tool render silences the desk while it still shows playing** ([lab/tools/common.py:39](../lab/tools/common.py#L39)). *Reproduced.*
- **The Tone field floods Undo**: each drag tick is a checkpoint, so 45 moves push the earlier sound out of reach ([lab/tools/field.py:293](../lab/tools/field.py#L293)). *Reproduced.*
- **The search cache ignores the Shape knobs** though the render includes them ([lab/tools/search.py:223](../lab/tools/search.py#L223)). *Reproduced.*
- **POD Go**: one odd preset or a damaged archive fails the whole backup with a bare Python error and no preset name ([lab/gear/podgo.py:154](../lab/gear/podgo.py#L154)); a block dropped for want of room is still listed in the explanation; an amp whose description names no channel gets the lowest-numbered model, so a high-gain amp becomes its clean channel labelled "same gear". *Reproduced on made-up backups.*
- **Named settings**: a model once recorded as unavailable is never read again on the same firmware, so buying a plugin leaves its models locked until the file is deleted by hand ([lab/tools/named.py:209](../lab/tools/named.py#L209)). *Reproduced.*
- **Cancel and the timeout do not stop an npm-installed Codex**: `kill()` reaches only `cmd.exe` ([lab/sound/assistant.py:139](../lab/sound/assistant.py#L139)). `setup.py` recommends that install. Kill the process tree. Also `codex()` is looked up with PowerShell on every call, about 0.8 s each time. *Reproduced with a stand-in.*
- **The README's list of what leaves the computer omits one thing**: the Songs tool's "Ask about the artists" sends the 30 most-listened artists and 40 song titles ([lab/tools/listening.py:155](../lab/tools/listening.py#L155)); the README says "no hours, songs or dates".

Checked and clean: all 23 dotted operations and every engine method named in a `host.call` exist, and every operation accepts `stop`.

## Structure

- **`Studio` is 72 methods over 1,170 lines** and holds about fourteen jobs. Four cuts are cheap: a `Rounds` store beside `Store` (round files are written non-atomically at [lab/desk/studio.py:1040](../lab/desk/studio.py#L1040)), a `Requests` object for the freshness and cancel rules, the Shape check as a class with a named record for its inputs, and `difference()` as a pure function.
- **"All state is in `Studio.state`" is not true.** Thirteen attributes live outside it, nine of them created lazily and read with `getattr`. The window writes `studio.phrase` directly in three places. The engine does the same with `visited` and `chosen_scene` ([lab/qc/engine.py:94](../lab/qc/engine.py#L94), [:268](../lab/qc/engine.py#L268)). Declare them in `__init__`.
- **Two parsers for the player's words**: `knobs.direction` ([lab/sound/knobs.py:658](../lab/sound/knobs.py#L658)) is shadowed by `words.read` for all but two of its phrases, and the two disagree on "heavier", "too boomy" and "more attack". Intensifiers flip a reading against the code's own comment: "really dark" is read as a wish for darker ([lab/sound/words.py:310](../lab/sound/words.py#L310)). One table.
- **Three loudness matchers and two `measure` functions** (`hearing.measure`, `tools/levels.py`, `Studio._match_levels`), where the package docstring promises one measurement for everything.
- **Four widget kits and three window scaffolds** across desk, tools and learner; six hand-rolled `after` loops in the learner where one `later(future, done)` would do.
- **Dead code**, all confirmed by grep: about 150 of the 185 lines of `lab/desk/theme.py` (`Tooltip`, `IconButton`, `GLYPHS`, `Line`), `window.phrases` and `loudest`, `Studio.add_part` and `chain_text`, `words.brief`, `taste.rank`, `measure.gap`, `levels.loudness_plan` and its four helpers, `search.audition`, the learner seat's four unread members and its `Bench` and `Recorder` adapters, and `Unit.position`'s queue clearing, which `Device` already does.
- **Guards for things that always exist**: about ten `getattr(studio, …)`, `hasattr` and `except ImportError` in `lab/tools/common.py` and elsewhere. They hide real breakage.
- **`Store` records are brittle outside `save`**: one unknown field makes a sound unreadable, and a mistyped `instruments.json` is silently overwritten with the defaults ([lab/sound/sounds.py:110](../lab/sound/sounds.py#L110)). Filter records to known fields and never overwrite a file that could not be read.
- **Stale words in code**: `lab/__init__.py` lists `qc` below `sound` though each imports the other; `chain.py:10` describes a `plan` function that does not exist; `designer.md:3`, `folders.py:3`, `menu.py:1` and `Program.cs:22` describe earlier layouts.

## Tests

827 tests pass in 56 seconds with no warnings, and left the repo and the data folder unchanged. Under an audit hook no test reached the unit, the audio driver, Codex or the network.

That safety rests on habit, not on a guard:

- **Two CLI tests omit `--simulated`** and are saved only because an argument check is the first statement of the command ([tests/test_lab_cli.py:248](../tests/test_lab_cli.py#L248)). If that check moves, the suite opens the real unit for writing.
- **Six test helpers build a `Studio` whose assistant is the real Codex CLI** with automatic calls on ([tests/test_lab_tools_support.py:46](../tests/test_lab_tools_support.py#L46) and five more). No test happens to ask for a round today.
- **The data folder is not redirected for the run**: six design tests read the real `assets.json`, and one desk test opens the Guitar Learner on the real recordings ([tests/test_lab_desk_pad.py:145](../tests/test_lab_desk_pad.py#L145)).
- **One fix for all three**: set `TONE_LAB_HOME` to a temporary folder once for the run, and a flag that makes `device._library`, `Audio.__init__` and `Assistant._codex` raise under test.

Other points:

- **The core safety test cannot fail**: "nothing lands on a preset the player switched to" asserts at most 40 stray writes where a whole apply is 29 and the measured worst is 0 ([tests/test_lab_unit.py:114](../tests/test_lab_unit.py#L114)). Assert none.
- **One test takes 10 of the 56 seconds** waiting out a gate it sets too late ([tests/test_lab_rereview_faults.py:278](../tests/test_lab_rereview_faults.py#L278)).
- **Nothing tests Stop ending a build between two writes**, the real `Loop`, `_codex`, or the window's ask and save wiring. There is no test that goes ask → round → heard → A/B → shape → save and reads the unit back; the pieces exist and it is about ten lines on top of `test_lab_studio.py`.
- **The simulator has no contract with the unit.** It is synchronous, exact and never fails. A first step needs no hardware: a local test, skipped when the files are absent, that reads the preset files already saved under `tone_sessions/`, applies each to the simulator and checks it reads back the same. A second: an `inspect.signature` comparison of the simulator with the library's device, for when the pinned commit moves.
- **The fault tests are the best in the suite and the hardest to find**: three files named by review round. Fold them into the modules they test (engine, host, unit, studio).

## Repository and documents

- **There is no `AGENTS.md` or `CLAUDE.md`.** The standing rules of this project live in conversation: the venv Python only, no window on screen in tests, no hardware or Codex in tests, one window, name what is measured, personal data out of the repo. An agent starting cold knows none of them. One page at the root would save every session from relearning them, and is the cheapest item in this review.
- **`qc-mcp` is a pinned commit of someone else's repository**, and the app reaches into its private `_io_lock`, `_pending` and `_XML`. If that repository goes, a fresh install fails. Fork it or keep a built wheel.
- **`pywin32` is used by `python -m lab shortcut` and not listed**; it arrives through `qc-mcp`. The README's prerequisites omit the Codex CLI. How `Tone Lab.exe` is built is said only in `--help`.
- **No lint.** A pass with pyflakes-style rules finds six unused imports in `lab/` and would have caught the stale ones this review lists. One of 1,326 functions is annotated; that is fine for this code.
- **Personal data.** No name, path, address or serial is tracked, and no personal file was ever committed. Some tracked tests and two documents use what read as real preset names, and `sounds.INSTRUMENTS` ships one player's guitars as everyone's defaults. Whether that matters is the owner's call.
- **Documents.** `TONE_KNOWLEDGE.md` is valid but its code map names `tone_lab` modules as current. `QUAD_CORTEX_TONE_RESEARCH.md` predates the build and belongs in the archive. `CODEBASE_REVIEW.md` opens with a link to "current findings" that its own last section records as fixed. `design/desk-concept.html` and its screenshots show the old app and nothing refers to them.
- **Branches**: `pre-quiet-desk`, `quiet-desk-and-tone-knowledge` and `suggestions` are ancestors of `rewrite` and can go. `rewrite` is 56 commits ahead of `main`.

## What is good and should stay as it is

- **One type for everything.** A design, a read-back and a saved preset are the same frozen `Chain`, and `differences` is the one way to compare them. `to_chain` is the single place a reply becomes a chain: a sweep of all 348 usable models with every control at both ends stored nothing out of range.
- **The unit's rules in one file.** `lab/qc/unit.py` reads clearly, states each rule once, and every write goes through it. The digest is only a cache key and every recall is verified, so a stale cache costs a rebuild and never a wrong sound.
- **The seams for testing**: `connect(opener=, editor=)`, `Assistant(run=)`, `Host(simulated=True, local=True)`, `Unit(settle=, sleep=)`. They let the tests run real code with almost no mocking.
- **Operations by dotted name.** Tools never touch the unit themselves, which is why they can move into the desk without engine work.
- **Small files, atomic writes, restore that drops what no longer exists.**
- **The assistant behind one `ask`**: no shell, a read-only sandbox, a schema-bound reply, a ledger, a daily limit.
- **Status lines in plain words** that say what happened and what to do next.
- **Compression and distortion as separate measures**, with the words documented as inferences from a ladder measured on the unit. The rule is right; this review only holds the rest of the code to it.

## How this was done

Seven parts were read in full: the unit layer by the reviewer who wrote this document, and the desk, the sound package, hearing and player, the Guitar Learner, the tools with the command line and launcher, and the tests with the repository, each by a separate reviewer. Reproductions ran on the simulated unit, a hidden window, scripted replies and synthetic audio. I re-checked the items under "Do first" and "A session on the unit" against the code, and re-ran the write counts, the slug collision, the bass offer and the lost engine trace myself; the rest stand on the slice reviewers' scripts. The tests reviewer ran the suite three times, once under an audit hook that blocked the real unit and Codex; the repo and the data folder were unchanged afterwards.
