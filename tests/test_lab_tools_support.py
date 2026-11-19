"""What the side tools' tests share: a simulated host, sounds, a phrase, and a desk to open windows on."""

from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np

from lab import catalog
from lab.chain import Block, Chain, Row
from lab.engine import MULTI_OUT
from lab.host import Host

PLEXI, HIWATT, V30, GREENBACK, DELAY = 1081, 1063, 12001, 12006, 6011


def chain(*models, gain_db=None, **settings):
    """A one-row chain from the guitar to Multi Out, with a Gain block at the end when `gain_db` is given."""
    blocks = []
    for model_id in models:
        block = Block(model_id)
        for name, value in settings.get(str(model_id), {}).items():
            block = block.set_display(catalog.model(model_id).index(name), value)
        blocks.append(block)
    if gain_db is not None:
        blocks.append(Block(catalog.GAIN_BLOCK).set_display(0, gain_db))
    return Chain((Row(tuple(blocks), inlet=1, outlet=MULTI_OUT),))


def phrase(seconds=1.5, level=.2):
    time_ = np.arange(round(48000 * seconds)) / 48000
    note = level * np.sin(2 * np.pi * 196 * time_) * np.exp(-1.5 * (time_ % .75))
    return note[:, None].astype(np.float32)


def host(folder):
    found = Host(Path(folder), simulated=True, local=True)
    found.ready.result(10)
    return found


def desk(folder, root=None):
    """A desk as a tool sees it: a root window and a studio over a simulated host."""
    from lab.studio import Studio

    studio = Studio(host(folder), folder)
    return SimpleNamespace(root=root, studio=studio)


def pump(studio, root=None, seconds=30):
    """Run the studio's clock until every job sent to the engine is back."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        studio.poll()
        if root is not None:
            root.update()
        if not studio.jobs:
            return
        time.sleep(.02)
    raise TimeoutError("The engine did not answer.")
