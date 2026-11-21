"""What the tests of the unit, the engine, the host and the studio share: sounds, a simulated unit with an
engine on it, a studio over a simulated host, and waiting for work to finish.

`tests/test_lab_tools_support.py` holds the same kind of thing for the side tools' tests (`host`, `desk`,
`pump`, `chain`, `phrase`); the two belong in one module.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)

from pathlib import Path  # noqa: E402
from tempfile import TemporaryDirectory  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402

import numpy as np  # noqa: E402

from lab.qc.engine import MULTI_OUT, Engine  # noqa: E402
from lab.qc.sim import MY_PRESETS, SimAudio, SimUnit  # noqa: E402
from lab.qc.slots import Slots  # noqa: E402
from lab.qc.unit import Unit  # noqa: E402
from lab.sound.chain import Block, Chain, Row  # noqa: E402

PLEXI, HIWATT, V30, GREENBACK = 1081, 1063, 12001, 12006


def one_row(*models, output=None):
    """A sound on one row, from the guitar to Multi Out."""
    return Chain((Row(tuple(Block(model) for model in models), inlet=1, outlet=MULTI_OUT, output=output or {}),))


def inputs(device):
    """The external inputs connected on the simulated unit's working preset."""
    return [lane.in_portid for lane in device.working.chains if lane.in_portid not in (0, 16, 17, 18)]


def fast(device):
    """The guarded unit over a device, without the waits a real unit needs."""
    return Unit(device, settle=.05, sleep=lambda seconds: None)


def engine_on(device, folder):
    """An engine on a device, keeping its records in `folder`. Not yet opened."""
    unit = fast(device)
    return Engine(unit, Slots(unit, Path(folder)), SimAudio(device))


def note(seconds=.5, level=.2):
    """A steady G as a dry phrase: one column of samples."""
    return (np.sin(np.arange(round(48000 * seconds)) / 48000 * 2 * np.pi * 196) * level)[:, None].astype(np.float32)


def until(test, seconds=10, what="what was waited for"):
    """Wait for `test()` to be true and return what it gave. Fails the test when it does not come."""
    deadline = time.monotonic() + seconds
    while True:
        found = test()
        if found:
            return found
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} did not happen within {seconds} s")
        time.sleep(.002)


def settle(studio, seconds=15):
    """Run the studio's clock until every job it started is back and the unit's worker is idle."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        studio.poll()
        if not studio.jobs and not studio.host.busy:
            studio.poll()
            if not studio.jobs:
                return
        time.sleep(.002)
    raise AssertionError("work did not finish")


def settle_until(studio, test, seconds=15, what="what was waited for"):
    """Run the studio's clock until `test()` is true, whatever else is still on its way."""
    deadline = time.monotonic() + seconds
    while not test():
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} did not come within {seconds} s")
        studio.poll()
        time.sleep(.002)


class EngineCase(unittest.TestCase):
    """A simulated unit with a preset of the player's selected, and an engine opened on it."""

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.device = SimUnit()
        self.device.save_slot(MY_PRESETS, 5, "Mine", [(PLEXI, {}), (V30, {})])
        self.device.recall(MY_PRESETS, 5)
        self.unit = fast(self.device)
        self.slots = Slots(self.unit, Path(folder.name))
        self.engine = Engine(self.unit, self.slots, SimAudio(self.device))
        self.engine.open()


class StudioCase(unittest.TestCase):
    """A studio over a simulated host, with a phrase and an assistant that answers from `script`.

    The desk is imported here and not at the top, so that the tests of the unit and the engine, which share this
    module, do not depend on it."""

    script = None

    def setUp(self):
        from lab.desk.studio import Studio
        from lab.qc.host import Host
        from lab.sound.assistant import Assistant

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.host = Host(self.folder / "unit", simulated=True, local=True)
        self.host.ready.result(10)
        self.studio = Studio(self.host, self.folder,
                             assistant=Assistant(self.folder, automatic=False, run=self.script or (lambda *a, **k: {})),
                             phrase=("take", note().astype(np.float64)))
        self.addCleanup(self.studio.close)
        self.studio.open()
        self.settle()

    def settle(self, seconds=15):
        settle(self.studio, seconds)

    def put_two(self):
        from lab.sound.sounds import Sound

        self.studio.put("a", Sound("Plexi", one_row(PLEXI, V30), {"kind": "designed"}))
        self.studio.put("b", Sound("Hiwatt", one_row(HIWATT, GREENBACK), {"kind": "designed"}))
        self.settle()
