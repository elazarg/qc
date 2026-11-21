"""The first run on a new computer: what is in place, what is missing, and how to put it right.

Each check returns a plain finding, `{"check", "ok", "say"}`, where `say` is
one or two sentences for the player: what was found, and when something is
missing, what to do about it. Nothing here touches the Quad Cortex beyond
asking Windows which audio devices exist; no stream is opened.

- `packages`: every package in requirements.txt imports.
- `tables`: the tables of gear knowledge that ship with the app are there.
- `audio`: the audio driver lists the Quad Cortex.
- `assistant`: the `codex` program is found, and signed in.
- `home`: the data folder can be written.

The checks run once for a data folder, at the first start (`first_run`), and
what they found is recorded there; `python -m lab doctor` runs them again
whenever asked. A start does not repeat them: they take seconds, and a unit
that is switched off says so itself when the desk opens. The first run also
brings the earlier app's history in (`migrate`); if that fails it is tried
again at the next start, and the checks are not.

`move_old_home` copies what an earlier version kept inside the program
folder into the data folder in the user's profile, once, and leaves a note
where it was. Nothing is deleted.
"""

from datetime import datetime, timezone
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[1]
REQUIREMENTS = REPO / "requirements.txt"
MARKER = "setup.json"
MOVED_NOTE = "MOVED.txt"
DEVICE_NAMES = ("quad cortex", "neuraldsp usb audio", "neural dsp")
# The module that shows a package is installed, where it is not the package's own name.
IMPORT_NAMES = {"pywin32": "pythoncom"}
INSTALL_CODEX = ("Install Codex: either the Codex app from the Microsoft Store, or in a terminal "
                 "`npm install -g @openai/codex` (needs Node.js). Then run `codex login` once and sign in "
                 "with your ChatGPT account.")


def finding(check, ok, say):
    return {"check": check, "ok": bool(ok), "say": say}


# ---- Packages ----

def requirements(path=REQUIREMENTS):
    """(distribution name, module to import) for each line of requirements.txt."""
    found = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~;@\s\[]", line, maxsplit=1)[0].strip()
        if name:
            found.append((name, IMPORT_NAMES.get(name.lower(), name.replace("-", "_").lower())))
    return found


def check_packages(path=REQUIREMENTS, *, load=None):
    """Every package the app needs imports. `load` imports a module by name (replaced in tests)."""
    load = load or _import
    wanted = requirements(path)
    if not wanted:
        return finding("packages", False, f"Could not read the list of packages the app needs ({Path(path).name}).")
    missing = []
    for name, module in wanted:
        try:
            load(module)
        except Exception as error:  # noqa: BLE001  an import can fail in many ways (a missing DLL, a bad build)
            missing.append(f"{name} ({type(error).__name__}: {str(error)[:80]})")
    if missing:
        return finding("packages", False,
                       "Some packages the app needs do not load: " + "; ".join(missing) + ". In the program folder, "
                       "run `.venv\\Scripts\\python.exe -m pip install -r requirements.txt`.")
    return finding("packages", True, f"All {len(wanted)} packages the app needs are installed.")


def _import(module):
    if module == "sounddevice":
        os.environ.setdefault("SD_ENABLE_ASIO", "1")    # must be set before the first import, or ASIO is not listed
    importlib.import_module(module)


# ---- The tables that ship with the app ----

def check_tables():
    """The tables of gear knowledge are in the program folder and can be read. Without one nothing is designed."""
    from lab import knowledge

    missing = knowledge.missing()
    if missing:
        return finding("tables", False, f"The app's tables are missing or cannot be read: {', '.join(missing)} in "
                                        f"{knowledge.SHARED}. Restore them from the repository "
                                        "(`git checkout -- lab/tables`).")
    return finding("tables", True, f"The app's {len(knowledge.SHIPPED)} tables of gear knowledge are in place.")


# ---- Audio ----

def audio_devices():
    """Every audio device Windows lists, as (name, driver kind, inputs, outputs). Opens nothing."""
    os.environ.setdefault("SD_ENABLE_ASIO", "1")
    import sounddevice

    found = []
    for device in sounddevice.query_devices():
        api = sounddevice.query_hostapis(device["hostapi"])["name"]
        found.append((device["name"], api, device["max_input_channels"], device["max_output_channels"]))
    return found


def check_audio(devices=None):
    """The Quad Cortex is listed by its ASIO driver with eight channels each way. `devices` as `audio_devices`."""
    try:
        listed = audio_devices() if devices is None else devices
    except Exception as error:  # noqa: BLE001
        return finding("audio", False, f"Windows' list of audio devices could not be read: {error}. "
                                       "Reinstall the packages (see the first check).")
    ours = [item for item in listed if any(name in item[0].lower() for name in DEVICE_NAMES)]
    asio = [item for item in ours if item[1] == "ASIO" and min(item[2], item[3]) >= 8]
    if asio:
        return finding("audio", True, f"The Quad Cortex is connected: {asio[0][0]} (ASIO).")
    if ours:
        return finding("audio", False, "Windows sees the Quad Cortex, but not through its ASIO driver. Install "
                                       "Cortex Control from neuraldsp.com, which brings the driver, then restart "
                                       "the app.")
    return finding("audio", False, "The Quad Cortex is not connected, or its driver is not installed. Connect it "
                                   "by USB and switch it on. If it still does not show, install Cortex Control from "
                                   "neuraldsp.com, which brings the driver.")


# ---- The assistant ----

def check_codex(*, locate=None, run=None):
    """The `codex` program is found and signed in. `locate` returns its path or raises; `run` runs a command."""
    from lab.sound.assistant import codex

    locate = locate or codex
    run = run or _run
    try:
        path = locate()
    except Exception:  # noqa: BLE001
        return finding("assistant", False, "The assistant (Codex) is not installed. " + INSTALL_CODEX)
    try:
        result = run([path, "login", "status"])
    except Exception:  # noqa: BLE001
        return finding("assistant", True, f"Codex is installed ({path}). Whether it is signed in could not be "
                                          "checked; if requests fail, run `codex login`.")
    text = ((result.stdout or "") + (result.stderr or "")).lower()
    if result.returncode == 0 and "not logged in" not in text:
        return finding("assistant", True, "Codex is installed and signed in.")
    return finding("assistant", False, "Codex is installed but not signed in. In a terminal, run `codex login` and "
                                       "sign in with your ChatGPT account.")


def _run(command):
    return subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=15,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# ---- The data folder ----

def check_home(home):
    folder = Path(home)
    probe = folder / ".write-test"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        return finding("home", False, f"The app cannot write its data folder {folder}: {error.strerror or error}. "
                                      "Set TONE_LAB_HOME to a folder you can write, or fix the folder's permissions.")
    return finding("home", True, f"Your sounds and recordings are kept in {folder}.")


# ---- All of them ----

def checks(home, *, again=False, packages=None, tables=None, audio=None, assistant=None):
    """What the checks find, in the order a player would fix them.

    They are made once for a data folder: when their outcome is recorded there, that is what is returned,
    unless `again` asks for them to be made now (as `doctor` does). The other keyword arguments replace a
    check (for tests).
    """
    if not again:
        found = recorded(home)
        if found is not None:
            return found
    return [(packages or check_packages)(), (tables or check_tables)(), (audio or check_audio)(),
            (assistant or check_codex)(), check_home(home)]


def recorded(home):
    """The findings recorded for this data folder, or None when the checks have not been made for it."""
    try:
        record_ = json.loads((Path(home) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    found = record_.get("findings") if isinstance(record_, dict) else None
    return found if isinstance(found, list) else None


def first_run_needed(home):
    """Whether something of the first run is still to do for this data folder: the checks have never been made,
    or the earlier app's history has not been brought in (it is tried again until it has)."""
    from lab import migrate

    return recorded(home) is None or not migrate.done(home)


def first_run(home):
    """What the first start does, once: check what the app needs and record it, and bring the earlier app's
    history in. Returns what to say about it, "" when there is nothing to say.

    A later start does only what is still to do: nothing, or bringing the history in again if that failed.
    """
    from lab import migrate

    said = []
    if recorded(home) is None:
        findings = checks(home, again=True)
        record(home, findings)
        said += [item["say"] for item in findings if not item["ok"]]
    if not migrate.done(home):
        try:
            said.append(migrate.summary(migrate.migrate(home))[0])
        except Exception as error:  # noqa: BLE001  whatever went wrong, the app opens; it is tried again
            said.append(f"The earlier app's history could not be brought in ({error}). It is tried again at the "
                        "next start.")
    return " ".join(text for text in said if text)


def record(home, findings):
    """Note the outcome of the checks for this data folder. Returns whether every one passed."""
    passed = all(item["ok"] for item in findings)
    path = Path(home) / MARKER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"passed": passed, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "findings": findings, "versions": _versions()}, indent=1), encoding="utf-8")
    return passed


def _versions():
    found = {}
    for name, _ in requirements():
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = None
    return found


# ---- Moving data out of the program folder ----

def move_old_home(old, new):
    """Copy the player's data from where an earlier version kept it (`old`) to `new`, once.

    Done only when `new` does not exist yet and `old` holds something. The copy goes to a temporary folder
    beside `new` and is renamed into place when complete, so an interrupted copy is never mistaken for the
    data. A note is left in `old` saying where the data went. `old` itself is kept as it was.
    Returns True when data was copied.
    """
    old, new = Path(old), Path(new)
    if new.exists() or not old.is_dir() or (old / MOVED_NOTE).exists() or not any(old.iterdir()):
        return False
    new.parent.mkdir(parents=True, exist_ok=True)
    staging = new.with_name(new.name + ".copying")
    if staging.exists():
        shutil.rmtree(staging)          # left by an interrupted copy; it is ours, and `old` is still intact
    shutil.copytree(old, staging)
    staging.replace(new)
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        (old / MOVED_NOTE).write_text(
            f"Tone Lab's data moved to {new} on {when}.\n"
            "This folder is a copy kept for safety. The app no longer reads or writes it, and it can be deleted "
            "once you have checked that your sounds are all there.\n", encoding="utf-8")
    except OSError:
        pass
    return True
