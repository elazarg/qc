# Reader's report: the hardware layer

One of nine reports behind `REWRITE_REVIEW.md`, written by a reader that only read the code on 2026-11-19. Its claims were not all re-checked. Line numbers are as of commit `11f5382`. Paths are under `tone_lab/`.

## 1. Map

Sizes are lines.

- `device.py` (220): connect with Cortex Control priority, read helpers; lines 154-220 serve only the oldest CLI sweep.
- `iterate.py` (306): SCRATCH/LIVE_B constants, `recall`, route calculators, copy-a-preset-into-our-slot (`prepare_fixture`), two legacy renders. It imports `capture`, which imports tkinter.
- `draft_render.py` (566): WORK_SLOT, draft→`Chain`, block-by-block builder, verifier, `hear`, `save`.
- `monitor.py` (603): the desk's live session (A/B slots, input switch, knob loop), plus `render_variant`/`save_variant`.
- `live.py` (151): `update` (knob diff writer, used everywhere) and a legacy loop.
- `live_worker.py` (129), `live_client.py` (162): child entry point and supervisor.
- `live_levels.py` (354): lane volume/mute and loudness matching.
- `presets.py` (38) local archive; `qc_library.py` (250) directory, free-slot chooser, assets; `preset_library.py` (148) browse/load/reference render; `device_presets.py` (194) factory-settings harvest; `budget.py` (102); `probe.py` (149) sweeps; `hearing.py` (201) and `rounds.py` (423) silent listening; `audio.py` (301) ASIO; `capture.py` (255) recorder mixed with a Tk window; `stage_check.py` (107) is offline only.
- `session.py:95-219` also holds the unit.

Cycles, hidden by lazy imports: iterate↔live; draft_render→preset_design→device_presets→draft_render; rounds→live_worker→live→iterate.

One concept, several implementations:

- **Connection:** 27 `device.connect` sites.
- **Chain on the unit (5):** `draft_render.build_grid:294`, `iterate.prepare_fixture:107` (copy by save), `monitor.py:243` (build, then save to the side slot), `device_presets.py:103`, `probe.py:111`.
- **Routing (3 writers, 4 calculators):** `iterate.py:81`, `draft_render.py:365`, `monitor.py:92`; `iterate.py:27,39`, `monitor.py:84`, `draft_render.py:359`.
- **Restore (8):** `draft_render.py:99`, `monitor.py:60,307`, `live.py:145`, `iterate.py:222,290`, `preset_library.py:75`, `live_worker.py:21`. Each uses a different ownership test and equality check.
- **Slot-is-ours (4):** `iterate.py:131`, `draft_render.py:94`, `monitor.py:273,283`.
- **Verify:** `assert_grid`/`assert_scenes`, inline in `build_grid:343,352` and `_build_scenes:275`, `verify_fixture`, `apply_changes`, `device.write_parameter`. Tolerances are .002, .005 and .006.
- **Clean check (3):** `device.py:75`, `draft_render.py:85`, `preset_library.py:64`.
- **Level (3):** gain block 16005 (`draft_render.py:138`), lane output control (`live_levels.py:115`), software mute (`audio.PhraseCursor.muted`).

## 2. Invariants

- **Cortex Control has priority** (`device.py:39-58`). It is detected from the process list, so there is a window while it starts.
- **Start from a saved, clean preset outside our slots.** The reserved set differs per entry: `live.py:58` and `iterate.py:207` exclude SCRATCH only, `draft_render.py:86` WORK_SLOT only, `monitor.py:28` omits LIVE_B, `monitor.py:190` has all three.
- **Write only to our slots:** whitelist (`iterate.py:61`) plus blank-or-our-name. Only LIVE_B's blankness is confirmed against the directory (`iterate.py:90`). `qc_library.destination` reserves My Presets 254/255 but not Tone Lab 255 (`qc_library.py:164`).
- **Archive before overwriting:** by convention at each site.
- **Check the selected slot before writing:** `still_working` per block, `live.update` per batch, a 0.8 s poll. Raw writes in `draft_render.py:464`, probe and the harvest skip it.
- **Restore preset and scene:** `finally` blocks. The crash marker exists only for the two child processes. In-process jobs (`hear`, `save`, `render_reference`, `render_variant`, `save_variant`) have none. `--recover` runs only after a supervised failure (`live_client.py:144`, `rounds.py:353`), never at app start.
- **Silence:** build on USB routes (`draft_render.py:300`), set trim and mute before connecting (`monitor.py:465-472`), disconnect the input before changing the output (`draft_render.py:369`). Legacy `try_guitar` and `live.run` connect unmuted.
- **Save to a fresh slot:** `draft_render.save` recomputes the destination just before writing (553); `save_variant` does not (`monitor.py:589`).
- **Listen only when idle:** `rounds.py:121`.

## 3. Knowledge to carry over

- **Queue duplication:** `request()` duplicates retained packets on every match (`transport.py:449`, an identity test against a fresh tuple). The workaround is clearing `_pending` under `_io_lock` (`device.py:83`, `device_presets.py:55`). 14 plain `get_setlist_position` calls and every `read_state` remain unguarded.
- **Settle times:**
  - recall: poll 4×0.25 s
  - after `write_preset_file`: 0.55–0.7 s, then recall and compare
  - routing: 0.10 s per row, then 0.35 s
  - delete 0.07, add 0.10, parameter 0.07, final readback after 0.40
  - split: 0.25, set before row 2 is filled
  - scene select: poll 3×0.25; live knob writes 0.012, parameters before bypasses
- **Scenes:** assign, switch, confirm, then write. Bypass index is `len(params)`.
- **Blank slots:** a vacant slot recalls as ""/"Unsaved" with no blocks.
- **Ports:** inputs 1/2, USB in 12, USB out 14, Multi Out 19, internal 16-18. ASIO channels [4,5]. The dry/wet return channels depend on `IOSettings.usb_port.dry_wet` (`device.py:97-106`).
- **Ids:** gain 16005; mixer 11000 (pan A 1, level B 2, pan B 3, phase 4); IR 29001; captures 14000/14001, key is the first 64 characters; lane VOLUME 0, MUTE 2.
- **Reamp alignment:** every silent render uses `tone_sessions/calibration.json` (7750 samples, firmware 4.1.1). No code in the repo or its history writes that file, so the calibration procedure is lost and a firmware update strands it.
- **ASIO and process quirks:** one stream at a time (`monitor.py:142`); open on the PortAudio thread (`capture.py:133`); the worker needs `python.exe` and raw `os.read` on stdin (`live_worker.py:38`).
- **Constants:** budget (91, 87.96, 85); PREVIEW_GAIN −12 with a live clamp of −6; playing threshold −50 dBFS; watchdogs 18/6/6/45 s.

## 4. Accidental complexity

- **Too long:** `monitor.run` (317-557) has nine closures and about 25 nonlocals. `resident_grids` (185-314) passes state through a `staging` nonlocal and tuples in `prepared`.
- **Fragile type:** `Chain` is a list subclass with class-level mutable defaults; `getattr` guards exist because probe and the harvest pass plain lists.
- **Dead:** `draft_render.new_slot` (tests only); `--capture-return` (never sent).
- **CLI or research only:** `hearing.listen` (duplicated by `rounds.unit`); the `_attachments` fallback (`draft_render.py:162-170`).
- **Legacy, reachable only via `--workbench`, `ui` or old CLI verbs:** `live.run`, `live_client.run`, `try_guitar`, `iterate.render`/`render_knobs`/`apply_changes`, `session.compare_qc`/`apply_winner` with `device.py:154-220`, the `LEGACY_WORK_NAME` rename.
- **Third path:** `monitor.grid` is the fallback when LIVE_B is unavailable.

## 5. Process model

- **Tk process:** one worker thread (`listening_room.py:160`) serialises every job and `read_qc`. That serialisation is the only device lock. Renders, saves, loads and library reads hold HID and ASIO in-process. Recording opens a second, shared HID handle on another thread (`capture.py:66`).
- **`live_worker` child:** runs `monitor.run`. JSON lines go over stdin (knobs, source, tone, level, stop) and stdout (events). The client coalesces commands and, on failure, kills the child and spawns `--recover`.
- **`rounds --audition` child:** job, result and stop files. Its stderr pipe is not drained while polling (`rounds.py:339`).
- **Weak points:** three stop mechanisms, two IPC styles, one marker file shared by both children. `expect` is the desk's last-seen position. A UI crash during an in-process job leaves the unit on a working slot.

## 6. Proposal

1. **One device-owner process**, started with the app, holding HID and ASIO, with typed commands (ensure side, knobs, input, level, listen, save, status, library, stop) and events. It runs recovery at startup. Replaces the 27 connect sites, the worker modes, the audition child, file IPC and in-process jobs.
2. **Slot registry:** one table of address, name and role; one directory-confirmed `claim`; one reserved set used by guards, browser and destination.
3. **Borrow scope:** one context for preconditions, marker, archive and restore. Replaces the eight restores.
4. **Grid reconciler:** desired state (blocks, parameters, split, mixer, scenes, routes, lane level) diffed against a readback, applied as ordered operations with the settle times above, then verified. Replaces `build_grid`, `_build_scenes`, `live.update`, the routing writers, level writes, the probe and harvest swaps, and all verifiers. Copy-by-save stays as one operation.
5. **Guarded transport:** clears the queue, checks the position before each write batch, paces writes.
6. **Audio service:** reamp, loop, record, plus a calibration command.
7. **Delete the legacy paths** listed in section 4.

**Unverified:** whether the Tone Lab setlist's folder key is the path `iterate.py:15` hardcodes; the stereo cab layout.
