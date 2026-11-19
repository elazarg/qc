# Reader's report: tone knowledge and the designer

One of nine reports behind `REWRITE_REVIEW.md`, written by a reader that only read the code on 2026-11-19. Its claims were not all re-checked. Line numbers are as of commit `11f5382`.

## 1. Data model as it is

- **Draft**: one dict in `tone_sessions/drafts/<id>.json`. The designer returns `name, target, explanation, chain, scenes, parallel_chain, parallel_level_db, parallel_spread, sources, excluded_stages, owned_assets` (preset_design.py:604). `_recipe` renames to `parallel` and `blend{level,spread}` (672). `validate` builds a new dict and keeps `parallel/blend/scenes` only when non-empty (462-470). Block: `model_id, settings[{name,value}]`, optional `scene_settings, off_in_scenes, reserved, asset`. `_with_shaping` appends neutral blocks (481). `_persist` adds `id, created_utc, request, guitar, parent` (553).
- `validate` drops those five keys, so callers re-merge `{**data, **validate(data)}` (771, rounds.py:103). It also doubles as loading: a saved draft whose model becomes locked silently leaves the history (772).
- **Round** (rounds.py:113): `id, created_utc, request, guitar, aim{drive,tone,space,note}, parent, phrase, followed?, candidates[]`. Candidate: `draft, name, differs, heard, shape, served, served_shape, tags, user_tags, review, played, miss?, failed?`. State lives in None and absent keys; the one round on disk lacks `phrase` and `served_shape`.
- **choices** (141): `role, name, controls{name:(index,lo,hi)}, twins, switches`. **Card** (174): `id, role, name, display_ranges, switches, about, watch_out, cpu, memory, named_settings, measured_drive`.
- **Knowledge**: gear.json keys amps by family slug with `models[int]`, drives/cabs/effects by string id, plus mics, speakers, irs, recipes, guitars. controls.json: `typical{role}, inert[int], amps{str id:{knobs{name:{role,sign,low,high}},switches,default}}`. polarity.json: int lists. costs.json: `core, models{str id}`.
- **Duplicates**:
  - A block has five forms: designer JSON, recipe dict, `choices` tuples, `Chain(list)` of `(model_id,[(index,value)])` with class-level mutable defaults (draft_render.py:25-38), protobuf.
  - Knob positions are called values, shape, moves, knobs and directions.
  - Knob edits use 1-based row/column (styles.py:480), re-keyed 0-based in live.py:14.
  - The five drive words are defined twice by different measures (`hearing.DRIVE`, `measured.BREAKUP`).
- **Dict traps**: `budget.cost(id) or {}` makes an unknown model free (budget.py:61); `KNOBS[key][1]`; `rounds.miss` raises if a stored word leaves `AIM` (rounds.py:58); `previous_chain` sends the whole persisted draft (658).

## 2. The designer prompt

- **Assembly**: one string, `[ROUND]+DESIGN+CRAFT+FORM+json(facts)` (690, 735). Prose is about 7,900 characters (9,400 in a round). TONE_KNOWLEDGE gives about 35,000 in total, so most is cards. The 51 everyday cards repeat on every request; only candidate cards, recipe, guitar notes and `earlier` vary.
- **Conflicts**:
  - "Avoid maximum gain or mix" against "fuzz at or near maximum" and "set each MIX to 100".
  - "Less amp gain than seems needed" against "make sure the chain reaches that".
  - "Reverb under 25 percent" against an aim of "wet".
  - Rounds still carry DESIGN's sentences on `previous_chain` and reserved devices.
  - DESIGN asks for cited web sources while the first round runs with search off (737) and `sources` is required.
- **Schema** enforces shape only: `model_id` is any integer, setting names are free strings, `designs` has no count. Ids, names, ranges, switch steps, one block per role, row width and budget are checked afterwards.
- **Told but not true**:
  - "Only the listed models": any model in `choices()` passes (328).
  - "The model's notes say what its positions do" and "controls that run backwards": the card carries no control notes (180-185); only `lore.brief` does, for the assistant.
  - Scene and delay advice is unchecked; blend is clamped silently (674).
  - The designer sees cpu and delay memory; the validator also limits four memories per processor (budget.py:26).
  - A block whose role is in `excluded_stages` is dropped silently (455).
- **Failure**: no retry or repair. One bad setting name voids a design. In a round it is dropped unlogged (752), so a round may hold one design. Codex stderr is discarded (advisor.py:183).

## 3. Shape knobs

There is no single model. `apply_rules` (styles.py:392-495) overlays six:

1. A rule table `(category, parameter name, max move)` summed over knobs; `ROLES` expands into it and the role is lost.
2. Drive, outside the table (`KNOBS["drive"]` is empty): `_drive_delta`, pedal level, bypass at −0.98, a 15% gain floor, and `_hold_level`, which covers Drive's gain but not Sustain's or Bloom's.
3. Filters in octaves with one owner per row.
4. "Previews" that raise a parked MIX.
5. Scalings: range, two unit special cases (448-451), `measured.scale` for guitar amps only.
6. Aliases and exclusions: GAIN variants, Volume-as-drive, gate 16003, SYNC, scenes, expression.

- Availability has two implementations, `available_knobs` (266) and `draft_controls` (271), which differ on bass amps (301 against 420).
- Signs come from parameter names: controls.json measured the Matchmore Ch2 TONE running opposite, and gear.json's `brighter`/`tighter` flags are unread.
- Words-to-knobs exists five times: `lore.LEXICON`, `styles.direction` (fallback, listening_room.py:1749), `rounds.REMEDY`, advisor prose (advisor.py:96), `tone_space.PLANES`.
- Tests need a protobuf preset, the global catalog and cached tables.

## 4. Provenance and regeneration

- gear.json comes from `research/build_knowledge.py`, reading amps-classic, amps-highgain, drives-effects, cabs-mics, and vocabulary (recipes and two hard-coded guitars only).
- controls.json comes from `analyse_amps.py` over `measurements/amps` and `amps-low`. Its firmware stamp is read from calibration.json at analysis time (156), not from the sweep.
- polarity.json comes from `measure_polarity.py`, costs.json from `python -m tone_lab.budget`, `tone_sessions/device-presets.json` from the harvest.
- No script consumes vocabulary's 95 descriptors, paths-scenes.json, qc-internals.json, the `rules` lists, or measure_controls' two result files. They were transcribed by hand into LEXICON, CRAFT, FORM, KNOB_HINTS and the mic knob.
- Hand-written that should be data: `MODELS`, `SHAPING_STAGES`, `CONTROLS`, `ROLES`, `KNOBS`, LEXICON, hearing thresholds, `BREAKUP`, CPU limits, `CAB_LAYOUTS`, ids 16003/16005/11000.
- Silently stale:
  - Nothing compares a file's firmware with the unit at runtime.
  - A new model without costs weighs nothing; without a gear entry it is never offered.
  - A formerly refused plugin stays locked, because the harvest skips recorded models (device_presets.py:92).
  - An inert plugin needs two manual scripts.

## 5. Dead code and overlaps

- No caller: `lore.control_note`. Own CLI only: `hearing.listen`. Tests only: `measured.strength`.
- Old Review window or CLI only: `styles.apply`, `styles.intent`, `advisor.ask`, `advisor.speak`, `advisor.research`, and tone_search.py (`library_ui`, review.py:219). The Desk reaches only `tone_search.describe`, through tone_space.
- tone_space.py is live (`tone_space_ui`, `desk_panels`, listening_room.py:73). It is a second feature extractor beside `hearing.Heard`.
- level_plan.py is live (listening.py, live_levels.py, setup_ui.py) and is not knowledge.
- gear.json fields never read: `gain_range, drive_control, poor_at, pairs_with, good_for, mics, speakers, basis, type`.
- `CONTROLS` still lists amp and drive names that line 115 no longer consults.

## 6. Proposal

- **Typed core**, frozen dataclasses with one versioned parse/dump:
  - `Control(name, indices, range, kind, role, sign, strength)`, with role and sign resolved once per model: name rule, then gear flag, then measurement.
  - `Model`, `Block(model, settings, per_scene, off_in, asset, neutral)`, `Chain(paths, blend, scenes)`, `Design`, `Draft`.
  - `Aim` and `Heard` sharing one enum module; `Candidate` with an explicit state; `Round`.
  - Loading never depends on availability.
- **Knob**: a pure function from `Chain` to targets `(block, control, value)`. Drive, filters and previews implement the same interface. Availability means "targets non-empty". One diff step produces device edits.
- **Knowledge**: one loader returning typed tables keyed by int id. Every file carries schema, firmware, generator and inputs, checked at startup. The lexicon is generated from vocabulary.json, and measured numbers are rendered into the prompt, not retyped.
- **Prompt**: sections with one owner each, cards rendered from `Model` including control notes, static part first. The schema is generated per request with a model-id enum and exactly three designs. A validation error gets one repair turn.
- **Instruments**: `Instrument(kind, notes, phrase)` selects a chain template. Today role order and "amp plus one cab" are hard-wired (`MODELS` order, 457), and `EXTENDED_ROLES`, `ROLES` and the harvest are guitar-only. Bass and acoustic need templates and categories, with measurements keyed by instrument.
- **Songs**: a `Song` holds parts. A part is a scene of a shared chain or its own preset, and a round belongs to a part.
