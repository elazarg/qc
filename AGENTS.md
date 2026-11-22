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

## Writing tests

- Every `tests/test_lab_*.py` starts with `import isolated`, before anything of `lab`: it gives the run a
  temporary data folder and closes the doors to the real unit, the audio driver and the Codex CLI. A file that
  uses tkinter, or imports an app module that does, imports `hidden_tk` next. `tests/test_isolation.py`
  enforces both, with no exceptions.
- Shared helpers are in `tests/support.py`: `EngineCase`, `StudioCase`, `DeskCase`; `settle`, `settle_until`
  and `until` in place of sleep loops; `free_now()` in place of `gc.collect()`.
- A `Studio` in a test always gets `assistant=Assistant(folder, automatic=False, run=...)`.
- Windows in tests are hidden by `hidden_tk`: do not set alpha or positions, and never call `mainloop` or
  `wait_window`.
- `tests/test_lab_one_window.py` and `tests/test_lab_code.py` read the source: no second window, dialog, menu
  or key binding; nothing defined twice; no unused import.
- An `expectedFailure` carries the fault in its comment; remove the marker with the fix.
- A faster run of the same tests: `.\.venv\Scripts\python.exe tests\in_parallel.py`.
- A scratch script that starts the suite needs `if __name__ == "__main__":`; the engine's own process imports
  the main script again.

## Checking the simulated unit against the real one

The tests run on `lab/qc/sim.py`. Two checks hold it to the real unit, both skipped unless asked for, and both
fed with files that hold the player's presets (keep them in the data folder or `personal/`):

- `TONE_LAB_TRACE=<file>` makes the app write every call on the unit and its reply to that file
  (`lab/qc/trace.py`). `TONE_LAB_CONTRACT_TRACE=<file>` then replays it on the simulated unit in
  `tests/test_lab_sim_contract.py` and says where they differ.
- `TONE_LAB_CONTRACT_PRESETS=<folder>` checks every `*.pb` preset read under it against what the simulated
  unit takes a preset to be.

## What code and tests must never do

- **Never reach the real unit, the audio driver or the Codex CLI from a test.** Use `lab.qc.sim` (`SimUnit`,
  `SimAudio`), `Host(folder, simulated=True, local=True)` and `Assistant(folder, run=...)`. Every command-line
  call in a test passes `--simulated`.
- **Never put a window on the screen from a test.** A test that creates Tk imports `hidden_tk` first. To look
  at what you built, take a picture of the hidden window: `tests/picture.py` (`picture.save(root, path)`)
  writes a PNG without showing anything. Look at every screen you change, at 1100x680 and at 980x620.
- **Never read or write the player's data from a test.** Everything goes in a temporary folder; `TONE_LAB_HOME`
  points the app's data folder at it.
- **Do not drive the real unit from an agent session unless the player asked for it in that session.** A job
  that is killed leaves the unit on the app's build slot; give the player's preset back before anything else.

## Rules of the design

- **One window.** No dialogs, no pop-up windows, no keyboard shortcuts. A feature opens as a panel of the desk,
  and the desk stays sparse: what is rarely needed is disclosed on request, in place. A list that drops down
  (the instrument chooser) and the words of a hint are frames placed over the window's own content, never a
  window or a `tk.Menu`. Learning the guitar is the one thing that takes the whole screen below the top row.
- **Few words on the screen.** A sentence that explains how something works, and does not change with what
  is happening, is left out when the control's name says it, and otherwise goes behind a `hint`
  (`lab/desk/hints.py`). Names, values, results and the status line stay in the open.
- **The instrument is global.** It is chosen in the top row and everything follows it at once; nothing keeps
  an instrument of its own.
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
- Commit finished work and push it, on `main`.
