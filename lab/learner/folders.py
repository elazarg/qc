"""Where the Guitar Learner keeps its recordings and labels. Every path the learner uses starts here.

The learner's data was recorded before the new app had a home of its own
(`lab.knowledge.home()`, which is `tone_sessions/lab`). It stays where it
is, one level up in `tone_sessions`, so that nothing recorded or labelled
is lost: the labels in `tone_sessions/guitar_learner`, the takes it lists in
`tone_sessions/recordings` and `tone_sessions/guitar_learner/live_recordings`,
and the guitar-input calibrations in `tone_sessions/guitar_input`.
"""

from hashlib import sha256
import json
import logging
from pathlib import Path
import re
from uuid import uuid4

from ..knowledge import REPO

SESSIONS = REPO / "tone_sessions"
LOG = logging.getLogger("lab.learner")
LOG.addHandler(logging.NullHandler())   # Written only where the app sets up logging.


def learner():
    """Labelled excerpts, one folder per guitar and layout, and the remembered layout."""
    return SESSIONS / "guitar_learner"


def recordings():
    """Takes recorded with Record clip, here and in the earlier app."""
    return SESSIONS / "recordings"


def live_recordings():
    """Clips kept from live listening."""
    return learner() / "live_recordings"


def layout_choice():
    return learner() / "charvel-layout.json"


def calibration(instrument):
    """One guitar's one-control calibrations: examples, profiles and gesture mappings."""
    key = instrument.strip().casefold()
    prefix = re.sub(r"[^a-z0-9]+", "-", key).strip("-")[:35] or "guitar"
    return SESSIONS / "guitar_input" / f"{prefix}-{sha256(key.encode()).hexdigest()[:8]}"


def calibrated_instruments():
    return SESSIONS / "guitar_input"


def crash_log():
    return SESSIONS / "live-driver.log"


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)
