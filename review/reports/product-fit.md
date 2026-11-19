# Reader's report: fit of the product to its user, and what a friend would need

One of nine reports behind `REWRITE_REVIEW.md`, written by a reader that only read the code and documents on 2026-11-19. Its claims were not all re-checked. Line numbers are as of commit `11f5382`. Timings are the documents' figures.

Files are in `tone_lab/` (LR = listening_room.py, SG = suggestions.py, DP = desk_panels.py, QB = qc_browser.py).

## 1. The two journeys

**(a) New sound, about 7 actions:** `/`, type, Enter, about a minute (LR:1722); `G`, 10–20 s first build; `2`/`3`, about 17 s each (SUGGESTIONS.md:84); a remark; Space; Save to QC. Not shown on screen:

- A title equal to one preset's name loads that preset (LR:1634); otherwise it starts a round (listening.py:292). This owner names presets after songs.
- Rework the chain, a named setting or an IR makes a new draft outside its round, losing marks and reviews (SG:49-55, LR:1400-1427).
- Four seconds after the input stops, the app takes the unit to listen, 26 s per design (SG:29, 256-272).
- Keep writes a file nothing reads (listening.py:205-229). "This is it" neither keeps nor saves (SG:164).

**(b) Improve an existing preset, about 8 actions:** `F`, filter, Use as B, Use as A, `G`, remark or knob, `0` for before/after, Space, Save. It offers Shape knobs on a working copy, instant remarks, live A/B, capture with Difference, and a save as a new preset "<name> Shape" in the Tone Lab setlist (monitor.py:582-603). It cannot:

- show the chain: "Open Cortex Control to see or edit this preset's blocks" (DP:255-259);
- give the assistant the chain (`gear=()`, LR:1732); "Rework the chain" needs a draft (LR:1822);
- turn a preset into a draft: no such code exists;
- swap an IR or capture: that replaces a preset on B with a "Clean platform" draft (LR:1414-1419);
- say what is off: listening takes drafts only (hearing.py:168).

Plainly: (b) is macro knobs on a copy. The designer, the gear knowledge and the rounds do not reach saved presets.

## 2. Collections for one song

Exists: scenes inside one draft, written by the designer in one call (preset_design.py:246-258), chosen in Chain (DP:262-272), saved together in one preset (draft_render.py:519-566).

Missing: no song on drafts, rounds, phrases or receipts; one global `desk.json` (LR:204); every phrase reads "Recorded phrase · guitar · HHMMSS" (listening.py:69, LR:623); History is a flat dated list (DP:574-582); saves take the next free slot under the designer's name (qc_library.py:169); two designs cannot become scenes of one preset; a saved preset's scenes are letters in Sounds (QB:83-86).

Smallest concept: a **Song** holding instrument, phrase, reference, rounds, and ordered **parts** (rhythm, lead, clean), each a draft or saved preset plus scene. On screen: one song name beside the phrase menu, filtering History and rounds. Save names "<Song> <Part>" in adjacent slots, or one preset with scenes when parts share blocks.

## 3. Discoverability and vocabulary

- Key only: `X` (LR:479). Right-click only: Copy B to A (LR:663), reviews, marks and "Three more" (SG:388-399).
- Review mode makes the request line a note field until Enter or Cancel, placeholder unchanged (LR:1619, SG:173-183). (Fixed on `main` after this report: the line now says so and Escape leaves it.)
- The round strip never leaves once a round exists, and 1/2/3 replace B (SG:57-62, 431).
- Silent refusals during a job: LR:865, 761, 1226, 1258, 1586, 2090.
- Messages assuming knowledge: "Run the USB bypass calibration" (iterate.py:191), "My Presets 32H is in use" (draft_render.py:97).

Vocabulary: tone, sound, draft, design, version and preset name one thing (LR:1105, 1717, 1722, 1812, 1955). "Capture" is a Neural Capture, rendered audio and the act (QB:123, LR:668, 1104). Keep is a preference, but ★ in History means saved to the unit (DP:557-580). "Aim" is the round's three words and the reference (SG:90, LR:317). "Guitar" is input and instrument (LR:322, 635). Phrase, take, loop, clip (LR:1110, DP:625).

## 4. The one guitar

`GUITARS = ("Charvel", "Les Paul")` (session.py:15). `knowledge/gear.json` describes the Charvel fully and the Les Paul as differences. The designer was told amps were measured "with this player's bridge humbucker" (preset_design.py:219; corrected on `main` after this report). `controls.json` is one take, one cab, 3.5 s; breakup thresholds come from that phrase (measured.py:45-46). Choosing a guitar changes only the designer's text; listening uses whichever phrase is selected (SG:264-267). The launcher fixes input 1 (desktop.py:74). A draft needs one amp and one cab (preset_design.py:457), so acoustic and classical chains cannot be expressed; bass settings are filtered out (preset_design.py:164).

Needed: an Instrument (kind, input, notes, own phrases), tables labelled with their instrument, amp-less chains.

## 5. A friend installs it

No `C:\Users\elaza` in source. Assumptions:

- Windows; the exe expects `.venv` beside it (desktop_launcher/Program.cs:28) and is built locally (desktop.py:158-174); data sits in `<repo>\tone_sessions` (session.py:16). pywin32 is undeclared and arrives only through qc-mcp.
- `calibration.json` is required and firmware-pinned (iterate.py:187-192), and nothing in the repo or its history writes it. A friend cannot capture, listen or set a reference.
- Codex signed in, unchecked (advisor.py:18-44); the Neural ASIO driver (audio.py:112-118).
- A hand-made "Tone Lab" setlist, learned at first save (qc_library.py:159); its slot 32H and My Presets 32G and 32H free (iterate.py:13-15, draft_render.py:15).
- `costs.json`, `controls.json`, `polarity.json` are the owner's unit at 4.1.1, loaded without a firmware check (budget.py:31-36).
- Licences: the inert list in `controls.json` (18 models, six plugins) locks them for everyone (preset_design.py:84-89). `read_licences` exists (device_presets.py:164) but the owner's `device-presets.json` records none.

First run: check unit, driver and assistant; read library and licences; choose slots; measure the USB offset; add an instrument; record a phrase.

## 6. Recommendations

1. "Work on a copy of X": read a saved preset's chain into a draft, so Chain, the designer and listening apply. Highest value.
2. When a request matches a preset name, offer both readings.
3. Song with parts.
4. Keep a revised design in its round.
5. Let the round strip leave; mark review mode; give Blind and Three more a visible home.
6. Show kept sounds in History, apart from saved ones.
7. Say why a control is refused.

Name once: Song, Part, Instrument, Phrase, Sound (design, kept, saved), Round, Shape, Reference, Neural Capture versus recording.

Friend minimum: declared dependencies, data in the user profile, the first run above, instruments as data.
