# Reader's report: the tests, and the audio measurement code

One of nine reports behind `REWRITE_REVIEW.md`, written by a reader that read the code and ran the test suite once on 2026-11-19. Its claims were not all re-checked. Line numbers are as of commit `11f5382`. Bare module names are under `tone_lab/`.

## 1. Classification of tests/*.py

296 tests, 8.1 s, no warnings: (a) pure logic 201, (b) hand-written fake 26, (c) internals mocked 65, (d) real Tk window 4.

- (a) 20 whole files (126 tests): seven guitar_* logic files, knowledge, level_plan, live_levels, lore, measured, models, qc_library, roles, stage_check, styles, tone_identity, tone_search, tone_space. Parts: draft_forms 16/20, preset_design 15/17, listening 11/12, rounds 10/12, quiet_desk 9/10, hearing 7/8, desk_safety 3/16, guitar_live 2/4, setup_ui 2/3.
- (b) draft_forms 4, desk_safety 3 (`FakeUnit`); suggestions 19 (fake `Desk`, tests/test_suggestions.py:19).
- (c) desk_transport 19 (`Desk.__new__` with about 20 `Mock` attributes, tests/test_desk_transport.py:18-64), desk_safety 10, monitor_guitar 7, guitar_calibration_ui 7, iterate 5, preset_library 4, desktop_startup 3, plus 10: guitar_live 2, preset_design 2, rounds 2, hearing 1, listening 1, quiet_desk 1, setup_ui 1.
- (d) tray 4.

(a) and (b) survive with import renames (seams: `session.SESSIONS`, `advisor.request`, `measured.table`); (c) must be rewritten.

## 2. Rules held only by mock-heavy tests, or by none

- `monitor.run` (monitor.py:317): three calls, each behind 8-11 patches of its helpers (tests/test_desk_safety.py:194, tests/test_monitor_guitar.py:80, 219). Level-before-connect order and phrase continuity live only there.
- Crash recovery (live_worker.py:21-32, live_client.py:144): no test.
- Restoring the player's preset (monitor.py:310-314): untested (unverified).
- Desk events: no test builds a real `Desk`; live_ui, knob_panel, draft_ui, library_ui, ui are never named.
- Saving to the unit: one test behind 13 patches (tests/test_preset_design.py:160-193).
- Untested: `draft_render.hear`, `hearing.listen`, `probe.sweep`, `audio.reamp`, settle delays (`time.sleep` always patched).

## 3. Rules the tests pin down

Unit safety

1. Never write an occupied slot, or a saved preset that looks empty.
2. A preset switch mid-build stops it within one block.
3. Never open the unit while Cortex Control runs.
4. A stored draft stays on USB until connected; trim and mute are written first.
5. Guitar copies are stored without input, so recall is silent.
6. Loading refuses to discard unsaved edits.
7. Listening stops once the unit leaves the draft.
8. Reference capture restores preset and scene even if audio fails.

Building drafts

9. A draft too heavy for the unit is refused by name.
10. Drafts cannot set levels, routing or out-of-range values.
11. Captures need version and cabinet status; owned IRs are verified exactly.
12. Saving verifies the readback and uses a free slot and name.

Scenes and two paths

13. Scene changes are validated like settings.
14. A shape on one scene leaves the others confirmed untouched.
15. A second amp needs its own cab.
16. Opposite polarity is corrected at the mixer.
17. Save and play use the chosen scene.

Knobs and remarks

18. A plain direction moves a bounded knob at once, without the assistant.
19. A remark applies the first feasible remedy and offers the next.
20. "No fizz" means less; "no bite" means missing.
21. An observation never requests a new sound; a proper name does.
22. One gesture is one undo.
23. A late answer never changes a tone chosen since.

Rounds and listening

24. A request returns an aim and three designs that say how they differ.
25. The first goes on B; a late round is offered, not applied.
26. The app listens only when unit and assistant are idle.
27. Its knobs go only to designs nobody heard.
28. A failed listen is said once; Stop drops queued work.
29. Clipping is "saturated" whatever the EQ; compression is not distortion.
30. Blind comparison hides identities until Keep.
31. Keep records what is sounding.

Levels

32. Matching only cuts; a requested lift is bounded by true peak, lane headroom and +6 dB.
33. No boost for live guitar from phrase measurements.
34. Matching waits for both exact shapes from one phrase.
35. Trim preserves scene baseline, parallel balance and mute.
36. Switching A/B keeps the playhead.

Desk

37. R plays when free, else says why and offers to stop and play.
38. Panels fold to titles in a small window.

## 4. The fakes

`FakeUnit` (tests/test_draft_forms.py:34-126) models grid edits faithfully: real protobuf grid, per-scene values and bypass, split, mixer, ignored writes to empty cells, silent refusal of listed models. `Wanderer` (tests/test_desk_safety.py:26) adds a player who leaves after N writes. It is a sound core for the simulator, covering 14 of about 27 unit calls the app makes. Missing: `write_preset_file`, `recall`, a directory, `set_param_typed`, output lanes, the editor-running state, time, budget-driven refusal (wire in `budget.weigh`), audio. Also needed: saved versus edited slots; a virtual clock with stale readbacks; scripted player actions; a fake eight-channel ASIO device returning a crude render of the grid (drive, tone, tail, level, mute, polarity) at fixed latency; a canned assistant.

## 5. Measurement map

- LUFS: audio.py:63 (whole file), level_plan.py:19 (passage). qc_mcp/loudness.py:177 is never called; only lane constants and dB conversion are used (live_levels.py:76-118).
- RMS: audio.py:69, hearing.py:84 (all channels); probe.py:32 (mono fold-down, cancels opposite-phase stereo); tone_search.py:98 (gated).
- True peak: only level_plan.py:40. Crest: four definitions (audio.py:72, hearing.py:89, probe.py:37, tone_search.py:98).
- Spectrum: five Welch versions that disagree: audio.py:64 (4096, eight octave bands, shares); tone_search.py:82 (gated); hearing.py:97 (8192, centroid); probe.py:22 (8192, 25 third-octaves, absolute dB); guitar_compensation.py:14 (ten bands). Brightness has three scales: Hz (probe.py:36), octaves against dry (hearing.py:118), log2 of band centres (tone_search.py:89).
- Difference: audio.py:76, tone_space.py:99, tone_search.py:212, probe.py:88, rounds.py:56.
- Matching: `audio.loudness_plan` (audio.py:81); a second algorithm at level_plan.py:99; a third inline at draft_render.py:458.
- Key names differ (audio.py:70-73, level_plan.py:41-42); audio.py:89 silently falls back to sample peak.
- Heard: hearing.py:47-119; sole consumer rounds.py:171.

## 6. Proposal

One module, one frozen `Measurement`: lufs, rms, sample and true peak, crest, absolute third-octave bands, centroid, width, passage. Channels are power-summed, never folded. Functions: `measure(samples, passage)`; `compare(a, b)` (level-free band gap plus dynamics, width, tails); `match(measurements, target, offset, ceiling, boost)`; `heard(dry, loud, soft, tail)`. Later: the same `Measurement` of the dry guitar, and a per-band monitor curve (weighting and playback EQ), which needs absolute bands. Port as is: `audio.loudness_plan`, `level_plan.measure`, `hearing.describe` with its unit-calibrated thresholds (hearing.py:33-40), `probe.spectrum/gap`, `listening.Loop`, `GainRamp`, `live_levels.terminal_lanes/lane_targets`. Retire `audio.describe/distance`, `comparison_plan`, the inline trim.
