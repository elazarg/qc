# Reader's report: the desk UI

One of nine reports behind `REWRITE_REVIEW.md`, written by a reader that only read the code on 2026-11-19. Its claims were not all re-checked. Line numbers are as of commit `11f5382`.

LR = `tone_lab/listening_room.py`, SG = `suggestions.py`, DP = `desk_panels.py`. Desk has 138 methods; `update()` has 32 call sites, `set_status` 89.

## 1. Map of Desk

- **Tone slots**: `tone_a/tone_b`, `shapes`, `tone_revisions`, `side`, `drafts`, `draft` (LR:834-930, 1319-1427, 1857-1888). Only `set_tone` bumps the revision, yet identity is also written by captured audio (1289, 1307), `matched` (1456), monitor rollback (2285-2289), guitar gestures (2263) and SG:121.
- **Unit job slot**: `worker`, `job`, `task`, `outcome`, `stop_signal`, `ready`, `check`, `leaving`, `editor_after`, `qc_*`; `submit`, `tick`, `stop`, `close` (2056-2212). `stop_signal` and `ready` are assigned from outside: SG:274, setup_ui.py:71-74, guitar_learner_ui.py:709-711, 850.
- **Live monitor**: `control_mail`, `live_messages`, `requested_source`, `active_source/side/revision`, `confirmed_tones`, `tone_requests`, `monitor_controls`, `base_bypassed`, `level_feedback` (880-893, 1993-2054, 2214-2294). Every group reads it through `on_air()`.
- **Captured audio through Windows**: `player`, `play_job`, `playing`, `last_heard`, `baseline`, `alternative`, `audition_values` (813-832, 1225-1317).
- **Requests**: `advisor`, `thinking`, `asked`, `sent`, `cancel_signal`, `followup` (1614-1855). SG borrows `advisor`.
- **Undo** (1172-1203), **Reference** (587-604, 1429-1580), **Blind** (499-527; also cleared in `set_tone`, 1322), **Keep/Save** (1890-1991), **Phrase** (746-811), **Line and dot** (688-704, 1130-1166), **Levels** (941-994), **desk.json** (709-744).
- **Shell**: `build`, `key`, menus, and `update` (1011-1092), which renders everything and also saves rounds (1034 → SG:153).

## 2. State model

- On A/B: `tone_a/tone_b`, `shapes[side]`, `tone_revisions[side]`. Copies that can disagree: `draft` against `tone_b.draft_id` (1802); `baseline/alternative` against `tone.capture` against `tone_asset()` (kept on a shape change, dropped by any `resolve_audio`: 813, 1337); a round's `candidate["shape"]` against `shapes["b"]`; `presets` against `browser.library`.
- Sounding now: `active_side`, `active_revision`, `active_source`, `base_bypassed`; through Windows, `playing`. The `output` text is state too: `sync_air` tests `startswith("Switching", "Loading")` (1139).
- Confirmed by the unit: `confirmed_tones[side]` (tone exact, shape assumed on send: 921-923), `monitor_controls`, `level_feedback`, `qc_*`.
- Holds the unit: `job` and `task`, plus `check` and `play_job` on the same single worker, plus Cortex Control.
- Machines: job (idle, running[task], done in `tick`; monitor adds preparing, live, switching, stopping); request (`thinking`, then answered, failed or cancelled; `followup` re-asks as create); requested against confirmed (`requested_source`/`active_source`, `tone_revisions`/`active_revision`, rollback on failure); listening (timer, job, interrupted with `then`); next round (`pending`, `outdated`); `reviewing`; blind; close (`leaving` waits for `tick`). "When the unit is free" exists three times: `reference_waiting`, `editor_after`, SG `then`.

## 3. Bugs and fragile spots

1. "Stop and play R" never plays: `stop_for_reference` sets `reference_waiting` and `stop()` clears it (LR:603-604, 1216, since e739f24). The test stubs `stop` (tests/test_desk_safety.py:270).
2. A design is marked heard before it sounds: SG:159 uses `on_air()`, which ignores `active_revision` (LR:568). Stepping while live marks it during its build, blocking the app's knobs (SG:334).
3. `tick` re-arms only on its last line (2212). An exception in `monitor_event` (2175), `poll` → `adopt` (SG:239), the follow-up `ask` (2189) or `update` is logged (capture.py:237) and the desk stops for good: jobs never finish, the window cannot close.
4. Review mode is invisible and sticky (SG:176-187, LR:1619): after Esc or any other message, the next request is stored as a review.
5. A draft tone restored from desk.json is not checked against `drafts`; `monitor_config` indexes it (2004), so Guitar and Phrase would do nothing. Occurrence unverified.
6. Stop cannot stop render, save or reference jobs: their `stop_signal` is None (1218).
7. Enabled but dead: tiles while the app listens (1033, 865); Difference's "Closest saved sound" and "Reference on A" while live (DP:443-444).
8. `pick` calls `retire_line` without cancelling the timer (578, 701), which later wipes a reading.
9. Blind leaks: an applied request names the side and opens Shape (1852-1855); the tile menu names B (664).
10. `matched` changes A before its checkpoint, without a revision (1456). Closing does not cancel the assistant; `rounds.again` takes no cancel (SG:221).

No method is defined twice (scripted check). No Tk call from a worker thread found.

## 4. Dead code and leftovers

- Unused (grepped): `self.step` (LR:223), `intent`, `intent_side` (1694, 1699), `instant_moves` (tests only), `show_guitar_calibration` (433), imports `webbrowser`, `qc_library`.
- Older flow: `draft` with `restore_draft_comparison` (1297); `shape_dirty`, `audition_values`, `state["shape"]`, all B only; Ctrl+1/2/G/L/0 (261-265); the `choose_preset` dialog with "Play on Quad Cortex" (1510-1565), duplicating Sounds; `close(destination)`, always None (257).
- ⋯ only: the Tone field's "Recorded A/B" duplicates Difference (tone_space_ui.py:298-367); its "Render this position" starts the input (LR:895). Sound check runs through `DeskReview`, a shim for "the old review contract" (setup_ui.py:113).

## 5. Carry over unchanged

- Results bound to (side, revision); late ones offered (1796-1840, 1764).
- Stop drops everything waiting (1215). The player preempts listening; the choice is kept as (round, draft) (2016, SG:110-134).
- App knobs adopted only if unplayed and untouched since sent; the caller merges (SG:333-339).
- Keep records what `sounding()` confirms (1890). Save refuses while live.
- A failed load rolls back to the confirmed tone (2278-2294).
- Undo covers both tones; drags coalesce (1172).
- Cancel returns the text (1788); "Another way" restarts from the earlier shape (1769).
- Auto-open never displaces a chosen panel (DP:127-134).
- Close waits for the restore. Single instance (desktop.py:50).

## 6. Proposed structure

- `DeskState`, frozen: two slots (tone, shape, revision, capture), selection, input wanted, reference, take, round shown, blind, panels. `UnitState`, written only from unit events. A pure `reduce(state, event)` returning state and effects. Undo is a stack of slot pairs, desk.json a debounced subscriber.
- One `UnitScheduler` owning the unit: typed, cancellable jobs and one queue of deferred intents that Stop clears. Setup and the Learner ask it.
- One `Assistant` service issuing (side, revision) tickets, cancellable, next round included.
- One event queue drained by one guarded pump, replacing six polling loops.
- Views render selectors and dispatch intents; no widget state as a guard (DP:602, qc_browser.py:205).

Tkinter is not the problem: every bug above is state. Its work-arounds are contained: the fold solver (DP:101-217), nine wraplength binds, hand-made tooltip, glyphs and placeholder (theme.py:36-139, LR:358), DWM title (desktop.py:95), and `update_idletasks` inside every `set_status` (theme.py:171), a re-entrancy hazard (no failure traced). It limits only what is unbuilt: meters, motion, DPI (UX_REVIEW.md:70).
