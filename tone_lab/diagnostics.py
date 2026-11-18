"""Local desktop diagnostics without recorded audio."""

import logging

from .session import SESSIONS

LOG = logging.getLogger("tone_lab")
LOG_PATH = SESSIONS / "tone-lab.log"


def configure():
    if LOG.handlers:
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(process)d/%(threadName)s] %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
