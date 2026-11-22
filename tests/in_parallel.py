"""Run the whole suite in several processes at once: the same tests as
`python -m unittest discover -s tests`, in about a third of the time.

    .\\.venv\\Scripts\\python.exe tests\\in_parallel.py        (four processes, or as many as there are processors)
    .\\.venv\\Scripts\\python.exe tests\\in_parallel.py 2      (two)

Each process runs whole test modules, so nothing a test shares within its module is split. Every process has a
data folder of its own (`isolated`), and its windows are hidden like any other test's. The modules that knock on
the desk's door, a port of this computer, are kept in one process so that they cannot meet each other there.

It prints one line for each process as it ends, then what failed, and ends with 1 if anything did. For the
output of one module as unittest prints it, run that module by itself.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

TESTS = Path(__file__).resolve().parent
SAME_DOOR = ("test_lab_desk_window", "test_lab_cli", "test_lab_launcher", "test_lab_setup", "test_desktop_startup")
# Roughly how long each of the long modules takes, in seconds; the others count by their size.
LONG = {"test_lab_studio_flow": 14, "test_lab_one_window": 13, "test_lab_tools_windows": 11, "test_lab_desk_window": 11,
        "test_lab_studio": 8, "test_lab_probe": 6, "test_lab_learner_gestures": 6, "test_lab_measure": 5,
        "test_lab_learner_teach": 4, "test_lab_learner_window": 4, "test_isolation": 3, "test_lab_cli": 3,
        "test_lab_tools_sweeps": 6}


def shares(count):
    """The test modules in `count` lists of about the same running time."""
    weight = {path.stem: LONG.get(path.stem, path.stat().st_size / 12000) for path in TESTS.glob("test_*.py")}
    found = [[] for _ in range(count)]
    load = [0.] * count
    found[0] = [name for name in SAME_DOOR if name in weight]
    load[0] = sum(weight.pop(name) for name in found[0])
    for name in sorted(weight, key=weight.get, reverse=True):
        lightest = load.index(min(load))
        found[lightest].append(name)
        load[lightest] += weight[name]
    return [sorted(names) for names in found if names]


def main(arguments):
    count = int(arguments[0]) if arguments else min(4, os.cpu_count() or 2)
    environment = {**os.environ, "PYTHONPATH": str(TESTS.parent), "PYTHONIOENCODING": "utf-8"}
    started, running = time.monotonic(), []
    for names in shares(count):
        output = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
        running.append((names, output, subprocess.Popen([sys.executable, "-m", "unittest", *names], cwd=TESTS,
                                                         env=environment, stdout=output, stderr=subprocess.STDOUT)))
    failed = False
    for number, (names, output, process) in enumerate(running, start=1):
        code = process.wait()
        output.seek(0)
        lines = output.read().splitlines()
        counted = next((line for line in reversed(lines) if line.startswith("Ran ")), "did not finish")
        verdict = next((line for line in reversed(lines) if line.startswith(("OK", "FAILED"))), f"ended with {code}")
        print(f"{number}: {len(names)} modules. {counted}. {verdict}", flush=True)
        if code:
            failed = True
            print("\n".join(line for line in lines if not set(line) <= set(".sxEF")), flush=True)
    print(f"{'FAILED' if failed else 'OK'} in {time.monotonic() - started:.0f} s, {len(running)} processes")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
