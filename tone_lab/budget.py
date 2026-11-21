"""What a chain costs the Quad Cortex, from the unit's own table of costs per model.

Each pair of rows runs on two processors. A block lives on one of them and
cannot be split, so the question is whether the blocks can be shared out, not
whether their total is small enough. Delay memory is one pool for the pair.
docs/TONE_KNOWLEDGE.md records how the limits were found on the unit.

Run `python -m tone_lab.budget` with the unit connected to read the table again
after a firmware update.
"""

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

from . import models

PATH = Path(__file__).resolve().parents[1] / "lab" / "tables" / "costs.json"
# The unit states about 92 per processor. Filling rows 1 and 2 with graded blocks showed one processor
# taking 90.7 and refusing 91.5, and the other taking 87.96 and refusing 87.97.
CPU = (91., 87.96)
MEMORY = 85.  # Delay memory shared by both processors: three Room reverbs fit and a fourth is refused.
FIELDS = {"cpu": "cpu_percent", "dm": "dm_heap_bytes", "pm": "pm_heap_bytes", "dmda": "dmda_bytes",
          "pmda": "pmda_bytes", "memory": "edma_percent", "latency": "latency"}
PER_CORE = ("cpu", "dm", "pm", "dmda", "pmda")


@lru_cache(maxsize=1)
def table():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"core": {}, "models": {}}


def cost(model_id):
    """What one block needs, or None when the unit lists no cost for it."""
    known = table()["models"]
    found = known.get(str(model_id))
    if found is None and models.is_stock_cab(model_id):
        # A stock cab is the default cab block with its microphones chosen.
        layout = models.CAB_LAYOUTS.get((models.lookup(model_id) or {}).get("category"))
        found = known.get(str(layout))
    return found


@dataclass(frozen=True)
class Load:
    fits: bool
    percent: int  # of the processing one sound can use
    memory: int  # percent of the delay memory
    refused: int | None = None  # the first model that found no room


def weigh(model_ids):
    """Share the blocks between two processors, largest first, the way the unit must."""
    core = table()["core"]
    limits = [{**{key: core.get(key, float("inf")) for key in PER_CORE}, "cpu": cpu} for cpu in CPU]
    used = [dict.fromkeys(PER_CORE, 0.), dict.fromkeys(PER_CORE, 0.)]
    costs = [(model_id, cost(model_id) or {}) for model_id in model_ids if model_id]
    memory = sum(block.get("memory", 0.) for _, block in costs)
    refused = None
    for model_id, block in sorted(costs, key=lambda item: -item[1].get("cpu", 0.)):
        room = [side for side, limit in sorted(zip(used, limits), key=lambda pair: pair[0]["cpu"])
                if all(side[key] + block.get(key, 0.) <= limit[key] for key in PER_CORE)]
        if not room:
            refused = refused or model_id
            continue
        for key in PER_CORE:
            room[0][key] += block.get(key, 0.)
    if memory > MEMORY and refused is None:
        refused = max(costs, key=lambda item: item[1].get("memory", 0.))[0]
    total = sum(block.get("cpu", 0.) for _, block in costs)
    return Load(refused is None, round(100 * total / sum(CPU)), round(100 * memory / MEMORY), refused)


def refresh(qc):
    """Read the cost of every model from the connected unit."""
    stats = qc.read_message("ModuleStats", timeout_ms=6000)
    if stats is None or not stats.stats or not stats.core_stats:
        raise RuntimeError("The Quad Cortex did not return its table of processing costs.")

    def row(item):
        return {key: round(getattr(item, field), 3) for key, field in FIELDS.items() if getattr(item, field)}

    cores = [row(item) for item in stats.core_stats]
    record = {"firmware": qc.firmware,
              "core": {key: min(core.get(key, 0) for core in cores) for key in FIELDS},
              "models": {str(item.id): row(item) for item in sorted(stats.stats, key=lambda item: item.id)
                         if item.cpu_percent}}
    PATH.write_text(json.dumps(record, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    table.cache_clear()
    return record


if __name__ == "__main__":
    from . import device

    with device.connect() as unit:
        found = refresh(unit)
    print(f"{len(found['models'])} models, firmware {found['firmware']}, saved to {PATH}")
