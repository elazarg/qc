"""Reading what each model costs the Quad Cortex, from the unit's own table.

The unit answers one read (ModuleStats) with the processing and memory every
model it can load needs, and what each of its processors offers. This writes
that table where `knowledge.table("costs")` reads it (`costs.json`, beside the
shared knowledge), with the firmware it was read on, so that `knowledge.stale`
notices a firmware update. `knowledge.weigh` turns it into whether a chain
fits; docs/TONE_KNOWLEDGE.md records how the limits per processor were found.

Carried over from `tone_lab.budget.refresh`. It is an operation run where the
unit is held: `host.call("lab.tools.budget.measure")`, or
`python -m lab budget`. Nothing is written to the unit and nothing is loaded.
"""

import json
import os
from pathlib import Path

from .. import knowledge

FIELDS = {"cpu": "cpu_percent", "dm": "dm_heap_bytes", "pm": "pm_heap_bytes", "dmda": "dmda_bytes",
          "pmda": "pmda_bytes", "memory": "edma_percent", "latency": "latency"}


def default_path():
    """Where `knowledge` reads the costs from, decided when asked (tests move it)."""
    return knowledge.SHARED / "costs.json"


def table_of(stats, firmware):
    """The unit's ModuleStats reply as the costs table: {"firmware", "core", "models": {id: {...}}}.

    A model is listed only when it costs processing; a value of zero is left out, as the old table did.
    For the processors, the smaller of the two is kept for each measure.
    """
    if stats is None or not getattr(stats, "stats", None) or not getattr(stats, "core_stats", None):
        raise RuntimeError("The Quad Cortex did not return its table of processing costs.")

    def row(item):
        return {key: round(getattr(item, field), 3) for key, field in FIELDS.items() if getattr(item, field, 0)}

    cores = [row(item) for item in stats.core_stats]
    return {"firmware": firmware,
            "core": {key: min(core.get(key, 0) for core in cores) for key in FIELDS},
            "models": {str(item.id): row(item) for item in sorted(stats.stats, key=lambda item: item.id)
                       if item.cpu_percent}}


def measure(engine, *, stop=None, path=None):
    """Read every model's cost from the unit and write the costs table. Returns where, how many, which firmware."""
    if stop is not None and stop.is_set():
        raise RuntimeError("Stopped.")
    engine.quiet()
    reply = engine.unit.device.read_message("ModuleStats", timeout_ms=6000)
    record = table_of(reply, engine.unit.firmware)
    target = Path(path) if path is not None else default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    spare = target.with_name(target.name + ".part")
    spare.write_text(json.dumps(record, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(spare, target)
    knowledge.table.cache_clear()
    return {"path": str(target), "firmware": record["firmware"], "models": len(record["models"]),
            "core": record["core"]}
