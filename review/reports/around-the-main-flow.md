# Reader's report: everything around the main flow

One of nine reports behind `REWRITE_REVIEW.md`, written by a reader that only read the code and the data on disk on 2026-11-19. Its claims were not all re-checked. Line numbers are as of commit `11f5382`.

All 27 commits and all data date from 17 to 19 November 2026. `ui-usage.jsonl` (23 rows, 19 November) shows opens of Chain 6, Difference 6, Sounds 2, Shape 2, History 2 and nothing else. It does not log the Learner.

## 1. Sub-features

- **Guitar Learner** (about 2,500 lines in six `guitar_*` modules). Opened from the desk menu (`listening_room.py:651`); the desk reads none of its output. One sitting: 18 November 13:30-13:51, 14 takes, 9 labelled.
- **Calibration and gestures** (about 1,370 lines: `guitar_ui`, `guitar_state`, `guitar_gestures`, `guitar_runtime`). Wired into the live path (`monitor.py:146-173`, `listening_room.py:2041,2259`). Never used: `tone_sessions/guitar_input/` does not exist.
- **Workbench A**, `python -m tone_lab ui` (`ui.py`, 308). Left 5 demo and 1 import session on 17 November; no `winner.json`.
- **Workbench B**, `clip --workbench` (about 1,800 lines: `review`, `live_ui`, `draft_ui`, `library_ui`, `preset_library_ui`, `capture.py:126-225`). The desk cannot open it; last started 18 November 03:46. The desk still imports `Knob` from it (`knob_panel.py:6`).
- **CLI** (`__main__.py`, 22 commands). Only `clip` is on the launch path. Use of `doctor`, `inspect`, `pod`, `history` is unverified.
- **Setup and sound check** (about 610 lines: `setup_ui`, `soundcheck_ui`, `stage_check`, `guitar_compensation`). One stage-EQ preview, 18 November 03:09. The desk also uses `level_plan` (`listening.py:329`).
- **POD Go and Spotify**: `sources.py`, 59 lines, CLI only, no tests.
- **Reference capture and match**: capture (`preset_library.py:100-148`) used once, 18 November. Match (`listening.py:184-202` over `tone_search.search`) tried once and failed (log, 18 November 12:06).
- **Tone field** (`tone_space`, `tone_space_ui`, 490 lines): no recorded opens. `tone_space.difference` feeds the Difference panel (`desk_panels.py:472`).
- **Recording** (`capture.py:61-123`): 18 takes, 4 from the workbench night, 14 from the Learner sitting, none since.
- **Sessions and feedback** (`session.py`, `feedback.py`): 20 folders, 1 feedback note, 0 winners, no `listening-decisions` folder (Keep never wrote one), 2 `.kept.json` (saved to the unit).

## 2. What the Learner knows about the guitar

- **Features**: per 120 ms frame, a pitch snapped to a note, 12 harmonic energy shares, and frame level (`guitar_state.py:44-95`). Only sustained single notes count; chords, bends and mutes are discarded.
- **Model**: per-note median templates for each pickup and wiring (`guitar_configuration.py:78-121`), refit on every open.
- **Data**: Charvel only, 9 clips, 113 s. Six examples: neck/parallel ×4, position 4/parallel, neck/series. Three checks: positions 2, 3 and neck. No bridge clip. Volume and tone always 10. No technique labels. A Les Paul layout exists (`guitar_dataset.py:32`) but the guitar is hard-coded (`guitar_learner_ui.py:18`).
- It classifies control positions; it does not describe the signal.
- **Reusable**: the harmonic extractor, the 18 dry takes, and the spec-sheet pickup notes the designer already receives (`research/vocabulary.json:10602`).
- **Missing**: absolute level. Dry peaks run from −16.6 to −53.4 dBFS across takes, and the labelled clips measure −36 to −45 LUFS. Nothing relates that to the unit's input meter or to what an amp model expects (undone per `RESEARCH_AGENDA.md:47`). Also missing: spectrum per pickup, soft-to-hard range, noise floor, attack, sustain, a scripted diagnostic phrase set, any Les Paul recording.

## 3. Storage model

- **Take**: `recordings/clip-<local time>-<id>/` with `dry.wav`, `wet.wav`, `clip.json`.
- **Passage**: one `selected_passage_seconds` in `clip.json` (`listening_room.py:799-801`).
- **Session, comparison, shortlist** (one thing): `<UTC time>-<kind>-<id>/` from `session.py:27-83`, holding `session.json`, `render-N.wav`, `reference.wav`, `audition-A/B/C.wav`, `listen.wav`, and `feedback/`.
- **Also**: `drafts/`, `rounds/`, `presets/<sha>/`, `desk.json` with absolute paths.
- A comparison belongs to a take only when `source_clip` and the exact passage match (`listening.py:115-118`), so re-trimming orphans earlier comparisons.

Over-built:

- A draft comparison is 7 files and 19.4 MB for one new render. Only the legacy UIs read the audition and `listen.wav` files.
- 528 MB in three days; 12 of 14 take-based sessions use the same take.
- `song`, passage name and `pickup_note` are read (`advisor.py:76`) but never written by the app; three `clip.json` files were hand-edited.

## 4. Dead, duplicate, valuable

- **Dead**: every Drop row in section 6, plus `session.py:86-219`.
- **Duplicates of desk features**: LivePanel (Shape, ask box), LibraryPanel (closest sound), PresetLibraryPanel (Sounds), Review (Phrase, History). `DeskReview` (`setup_ui.py:113-145`) only hosts old panels.
- **Valuable**: the Keep and Fold-in rows in section 6, plus `level_plan` and the guitar notes.

## 5. Other gear

- **POD Go**: `sources.py:10-43` reads a `.pgb` completely. The newest of six local backups (4 March 2025) holds 128 User and 122 Factory presets and 52 IRs. Each preset carries every block's model id, order, bypass and parameter values, four snapshots and IR hashes. Nothing translates them to Quad Cortex models; IR audio is not extracted. The names already split by instrument and song: 5 "LP", 7 "CH", 9 bass, "LOVE OF GOD", "Time".
- **Nano Cortex**: nothing.
- **Spotify**: `sources.py:46-59` ranks the top 30 tracks by minutes. Unconnected.
- **Per guitar**: `GUITARS = ("Charvel", "Les Paul")` (`session.py:15`) is a label plus prose notes (`lore.py:525-531`). No bass, acoustic or classical; no per-guitar level or EQ.
- **Monitoring**: nothing; playback uses the Windows default output (`listening.py:427-431`).

## 6. Recommendation

| Feature | Verdict | Reason |
|---|---|---|
| Learner UI, classifier | Drop | One sitting; reports a pickup he already knows |
| Harmonic extractor, dry takes | Fold in | Basis for a per-pickup signal profile |
| Calibration, gestures, stage EQ, compensation | Drop | Never trained; one preview |
| Workbenches | Drop | Superseded; move `Knob` |
| CLI | Keep launch, `doctor` | Rest unused |
| Sound check | Fold in | Show dry peak and headroom on every take |
| POD parser | Fold in | 128 liked presets as starting points |
| Spotify | Fold in, small | Ranks songs for collections |
| Reference capture | Keep | Main flow |
| Match, search, tone field | Drop | One failed use, no opens; keep `describe` (`tone_search.py:58`) |
| Recording | Keep | Add name, pickup, controls at record time |
| Sessions, feedback | Replace | Below |

Minimal storage:

- `takes/<id>/`: `dry.wav`, `wet.wav`, `take.json` (guitar, pickup, controls, name, passage, preset heard, levels).
- `guitars/<name>.json`: measured level and spectrum per pickup.
- `tones/<id>.json`: recipe, request, parent, notes, unit location.
- `renders/<tone>-<take>.wav`: a deletable cache.
- `songs/<name>.json`: ordered tone ids.
- `journal.jsonl`: kept, rejected, remarks.
- `presets/<sha>/` snapshots; `desk.json` holding ids.
