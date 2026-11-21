# Working on Tone Lab

Tone Lab drives a Neural DSP Quad Cortex from one desktop window. It is built for one player on his own Windows
machine, mostly by coding agents he directs. `README.md` says what the app does; `lab/__init__.py` lists the
packages; `docs/WHOLE_CODEBASE_REVIEW.md` and `docs/CODEBASE_REVIEW.md` hold the reviews and what was done
about them. This page holds the rules that are not in the code.

## Running things

- Use `.\.venv\Scripts\python.exe`, never a bare `python`.
- Tests: `.\.venv\Scripts\python.exe -m unittest discover -s tests`. One module: `-m unittest tests.test_lab_engine`.
- The app without hardware: `.\.venv\Scripts\python.exe -m lab --simulated`.
- Set `PYTHONIOENCODING=utf-8` when output holds non-ASCII names.

## What code and tests must never do

- **Never reach the real unit, the audio driver or the Codex CLI from a test.** Use `lab.qc.sim` (`SimUnit`,
  `SimAudio`), `Host(folder, simulated=True, local=True)` and `Assistant(folder, run=...)`. Every command-line
  call in a test passes `--simulated`.
- **Never put a window on the screen from a test.** A test that creates Tk imports `hidden_tk` first. No
  screenshots: the player may be at the computer.
- **Never read or write the player's data from a test.** Everything goes in a temporary folder; `TONE_LAB_HOME`
  points the app's data folder at it.
- **Do not drive the real unit from an agent session unless the player asked for it in that session.** A job
  that is killed leaves the unit on the app's build slot; give the player's preset back before anything else.

## Rules of the design

- **One window.** No dialogs, no pop-up windows or menus, no keyboard shortcuts. A feature opens as a panel of
  the desk, and the desk stays sparse: what is rarely needed is disclosed on request, in place.
- **Name what is measured.** A measurement is named for the quantity it measures. A conclusion drawn from it
  (such as "overdriven") is an inference and is labelled as one. Prefer a direct test signal to a proxy.
  The same holds for controls: a Shape knob is a quality that is heard, never one device control.
- **The unit's rules live in `lab/qc/unit.py`**, once. Nothing writes to the unit except through it: a level
  before an input, never above the ceiling, never to a preset the player selected, and the player's preset is
  given back.
- **Side features are carried, not dropped.** The Guitar Learner and the side tools stay; make them cheaper to
  keep.
- **The app is for one player.** Do not add configuration, compatibility layers or guards for cases that
  cannot happen here. Do keep data safe: write files whole (write beside, then replace), never overwrite a
  file that could not be read, and tolerate records written by an earlier version.

## The repository is public

Generic code and knowledge go in the repository, with made-up data in tests. Anything about one player (his
recordings, sounds, listening history, the unit's inventory, analyses of his data) stays in the data folder
(`%LOCALAPPDATA%\Tone Lab`) or in `personal/` at the top of the project, which git ignores. Check
`git check-ignore` before writing such a file inside the project.

## Conventions

- Plain `unittest`; test names are sentences that say the behaviour. A bug fix comes with a test that fails
  without it.
- Docstrings and comments say what the code does and why, not how it came to be.
- Text the player reads is plain and says what happened and what to do next.
- Commit finished work and push it; the working branch is `rewrite` until it is merged.
