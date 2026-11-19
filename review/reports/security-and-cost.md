# Reader's report: security, privacy, cost and trust

One of nine reports behind `REWRITE_REVIEW.md`, written by a reader that only read the code on 2026-11-19. Its claims were not all re-checked. Line numbers are as of commit `11f5382`. Paths are under `tone_lab/` unless stated.

## 1. The assistant boundary

- All calls go through `advisor.request` (advisor.py:148-185). Flags (161-163): `-a never exec --skip-git-repo-check --ephemeral --ignore-user-config --disable shell_tool --sandbox read-only -c web_search=live|disabled -c model_reasoning_effort=low --output-schema`; the working directory is an empty temporary folder. No model is pinned, the environment is inherited, stderr is discarded. Unverified against the installed Codex.
- `read`/`ask` (advisor.py:76-106): request, guitar, pickup note, song, preset and tone names, knob values, band measurements, gear notes; 8-11k characters (2-3k tokens), no web. `research` (120-131): request, guitar, song, preset name; web on.
- `design`, `design_round`, `again` (preset_design.py:650-662, 690, 735; rounds.py:284-318): 7.7-9.2k characters of instructions, 51-75 model cards, up to 16 user IR names, the previous chain or three earlier rounds with the player's remarks; 50-70k characters, 13-18k tokens (estimate). No audio or paths (error strings excepted, rounds.py:303).
- Validation: knobs are kept only if known, finite and in range (advisor.py:110-115). `validate` (preset_design.py:426-478): listed models only, settings inside the catalogue range or the design is rejected, block and CPU limits; names sanitised (78-81); links https only (advisor.py:135-145). Drafts are revalidated at build (draft_render.py:110); writes are read back (186-228).
- Gaps: `read` knobs reach the live tone immediately (listening_room.py:1836-1838), contradicting the prompt (advisor.py:99); Drive at 1.0 is every gain control at its top (styles.py:315-323). `target`, `explanation`, `differs` and link titles are unbounded and return in later prompts.
- Injection paths: web results; preset, tone and IR names from the unit, cloud downloads included; stored assistant text; take notes. POD Go files reach only a CLI command (__main__.py:189-200). Worst case: a legal but extreme chain or knob set, a misleading explanation, a clicked https link, prompt contents leaving in search queries.

## 2. Loudness

No single ceiling; local limits:

- Trim block at −12 dB (draft_render.py:19, 138); `min(gain, −6)` written three times (draft_render.py:383; monitor.py:36, 256).
- `hear`: trim −30..0 dB, peak −3 dBFS, one scene (draft_render.py:458-470).
- `validate`: compressor makeup and EQ output ±6 dB, feedback ≤70, blend −20..+6 (preset_design.py:35, 133-139). Drive-pedal OUTPUT and amp MASTER keep full range (115).
- Lane volume and mute: boost ≤6 dB, phrase only, after measurement (live_levels.py:98-112, 277-279).
- Routing: `route_to_guitar` (draft_render.py:365-374), `iterate.set_routes` (iterate.py:81-87; callers monitor.py:95, 132, 592; live.py:72-76). Trim and mute precede connection (monitor.py:465-472).
- Shape knobs stop at catalogue maxima; repeats, tail and space stack on feedback (styles.py:60-90, 466).

All are relative trims: with live guitar, an extreme legal chain is bounded only by the unit's full scale and the monitor knob.

## 3. Cost

- A recognised remark: 0 calls (listening_room.py:1705-1710). Other text: one `read` (50 s). If it answers `new_sound`, a round starts unasked (2187-2189; 180 s). After the app's own audition, any miss or remark starts `again` with web (suggestions.py:203-207; rounds.py:273-281; 240 s). One sentence can cost three calls.
- Revision 1 (50 s); "Three more" 1 each, uncapped; research 1 (85 s). No retries.
- Unasked triggers: `mark`, `reviewed`, `listened`, unit release (listening_room.py:2051). Only the round's `followed` flag guards them; nothing switches the assistant off for tests or scripts.
- Record: none.
- Proposal: `ledger.jsonl`, one line per call (time, kind, user or automatic, web, size, seconds, outcome), written by the single assistant function, which requires a user-action token and enforces a daily cap.

## 4. Supply chain

- `qc_mcp` is pinned by commit to one person's repository (requirements.txt:5); a fresh install fails if it vanishes. Used from `QuadCortex`: open/close, firmware, protocol_version_verified, get_current_preset, get_setlist_position, read_state/read_message, list_directory, list_model_presets/load_model_preset/select_model_slot, recall, write_preset_file, add_block/delete_block, set_param/set_param_typed, set_block_bypass, set_routing, set_split_points, set_lane_param, set_scene/set_scene_label, assign_param_to_scenes, set_ir/set_capture, private `_io_lock`/`_pending` (device.py:89-94). Also `catalog` (lookup, to_norm, to_display, private `_catalog`, `_XML`; models.py:39, 57), `protocol.ACTION`, `preset.describe`, `leveling`, three `autolevel` constants. Its protobuf objects are read directly in about ten modules.
- Firmware: `connect` refuses an unverified protocol (device.py:54-55); calibration and costs are firmware-keyed (iterate.py:190); ports, model ids and slot paths are literals.
- Codex: a renamed flag yields "check the sign-in" (advisor.py:183-184).
- ASIO: device matched by name, channels literal (audio.py:112-126).

## 5. Data at rest

- `git ls-files` (371): no session data, logs, audio, user paths or emails. Tracked but personal: this unit's measurements, `GUITARS` (session.py:15), "this player's bridge humbucker" in the prompt (preset_design.py:219; corrected on `main` after this report). design/*.png not inspected.
- `tone_sessions/` (528 MB, inside the repository; session.py:16): recordings, take notes, requests and reviews, preset snapshots, the unit's directory with `author` and `cloud_id` on 14.7k entries, licences, learner dataset.
- Logs: tone-lab.log (preset names, tracebacks with user paths, unrotated); ui-usage.jsonl (view events); startup.log (launch times).
- Absolute paths: desk.json, guitar_learner records, soundcheck session, listening decisions (listening.py:211-212).

## 6. Recommendations

1. One output ceiling in the device adapter; an unmeasured chain stays at a low trim.
2. One typed boundary for assistant output, strings capped; knob suggestions are proposals.
3. Ledger and gate; automatic calls opt-in.
4. Adapters for device, assistant (`complete(prompt, schema, web, timeout, cancel)`) and audio; qc_mcp vendored; firmware check at start.
5. Data under %LOCALAPPDATA%, relative paths, per-player profile apart from shared knowledge.

Not worth doing: encryption, accounts, extra sandboxing, hash-locked dependencies, exact token accounting, several providers.
