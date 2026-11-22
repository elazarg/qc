"""Ways to add a quiet stereo effects return without changing the direct voice."""

from dataclasses import replace
import re

from lab import knowledge
from lab.qc import assets
from lab.sound import catalog
from lab.sound.chain import Block, COLUMNS, INTERNAL

DUAL_DELAY, PLATE = 6012, 8012
MODELS = (DUAL_DELAY, PLATE, 7022)


def wants_layers(text):
    if re.search(r"\b(?:no|without|omit)\s+(?:any\s+)?(?:delay|echo|reverb|effects|layers)\b", text,
                 re.IGNORECASE) or re.search(r"\b(?:dry only|stripped-down|keep it simple)\b", text, re.IGNORECASE):
        return False
    return bool(re.search(r"\b(?:andy timmons|comfortably numb|wide|lush|layered|spacious|parallel|bigger|fuller|"
                          r"ambient|cinematic|epic)\b", text, re.IGNORECASE))


def _effect(model, settings):
    info = catalog.model(model)
    block = Block(model)
    for name, value in settings.items():
        block = block.set_display(info.index(name), value)
    return block


def parallel_space(chain):
    """Split after the last cab/amp, leave the main line alone, and add a filtered stereo return."""
    feeds = [(index, row) for index, row in enumerate(chain.rows) if row.inlet not in INTERNAL and row.outlet not in INTERNAL]
    if len(feeds) != 1:
        raise ValueError("Parallel space needs one direct path. Use a text request to arrange this grid.")
    r, main = feeds[0]
    if r % 2:
        raise ValueError("The direct path needs the first row of a processor pair for a split.")
    child = chain.rows[r + 1]
    if main.split or child.blocks or child.inlet or child.outlet or child.split:
        raise ValueError("This grid already uses the space for a parallel layer. Ask for a change to its existing paths.")
    core = [column for column, block in main.blocks if assets.is_amp(block) or assets.is_cab(block)]
    start, join = max(core, default=-1) + 1, COLUMNS - 1
    if start + 2 > join or main.cells[join] is not None:
        raise ValueError("There is no room after the cab for this layer. Ask to rearrange the grid.")
    if any(model in knowledge.locked() for model in (DUAL_DELAY, PLATE)) or not knowledge.weigh(
            [*(block.model for _, block in main.blocks), DUAL_DELAY, PLATE]).fits:
        raise ValueError("This sound has too little processing room for another delay and plate.")
    echoes = _effect(DUAL_DELAY, {"MIX": 100, "SYNC L": 0, "SYNC R": 0, "DELAY TIME L": 345,
                                  "DELAY TIME R": 475, "FEEDBACK L": 21, "FEEDBACK R": 23,
                                  "MOD DEPTH": 18, "HIGH PASS": 180, "LOW PASS": 4600, "STEREO WIDTH": 82})
    room = _effect(PLATE, {"MIX": 28, "PRE DELAY": 36, "DECAY": 50, "HIGH PASS": 190, "LOW PASS": 4300})
    mixer = {index: catalog.to_norm(catalog.MIXER, index, value) for index, value in ((1, 5), (2, -12), (3, 5))}
    return (chain.with_row(r, replace(main, split=(start, join), mixer=tuple(mixer.items())))
            .with_row(r + 1, replace(child, cells=(None,) * start + (echoes, room))))


def has_layers(chain):
    if any(row.split for row in chain.rows):
        return True
    kinds = [block.info.category for _, _, block in chain.blocks if block.info]
    return kinds.count("Delay") > 1 or len(set(kinds) & {"Delay", "Reverb", "Modulation"}) == 3


def references(text, stored):
    """Compact routing and effect roles from explicitly named local presets."""
    requested = set(re.findall(r"[a-z0-9]+", text.casefold()))
    found = []
    for sound in reversed(stored):
        if sound.origin.get("kind") != "preset" and "keeper" not in sound.marks:
            continue
        words = set(re.findall(r"[a-z0-9]+", sound.name.casefold()))
        if len(words) < 2 or not words <= requested or any(ref["name"] == sound.name for ref in found):
            continue
        chain = sound.chain
        found.append({"name": sound.name, "scenes": list(chain.labels[:chain.scenes_in_use()]),
                      "rows": [{"row": r, "split": row.split, "input": row.inlet not in INTERNAL,
                                "output": row.outlet not in INTERNAL,
                                "blocks": [{"column": column, "name": block.name,
                                            "off": block.bypass} for column, block in row.blocks]}
                               for r, row in enumerate(chain.rows) if row.blocks]})
        if len(found) == 2:
            break
    return found
