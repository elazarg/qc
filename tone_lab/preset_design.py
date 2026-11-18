"""Turn a player's request into a versioned draft using stock Quad Cortex models."""

from datetime import datetime, timezone
from functools import lru_cache
import json
import math
import re
import unicodedata
from uuid import uuid4

from qc_mcp import catalog

from . import advisor, session


MODELS = {
    "Drive": (1, 4, 5, 10, 11, 15, 18, 22, 25, 29),
    "Amp": (1011, 1018, 1045, 1079, 1081, 1084, 1093, 1095, 1100, 1120, 1126, 1155),
    "Cab": (12001, 12004, 12006, 12009, 12013, 12017, 12018, 12022, 12026, 12032, 12044, 12048),
    "Modulation": (7000, 7002, 7004, 7006, 7008),
    "Delay": (6004, 6005, 6007),
    "Reverb": (8000, 8001, 8012, 8013),
}
CONTROLS = frozenset({
    "BASS", "MID", "MIDDLE", "TREBLE", "PRESENCE", "CUT", "TONE",
    "GAIN", "VOLUME", "VOLUME BRIGHT", "VOLUME NORMAL", "MASTER",
    "OVERDRIVE", "DRIVE", "DISTORTION", "FUZZ", "SUSTAIN", "FILTER",
    "RATE", "DEPTH", "WIDTH", "MIX", "FEEDBACK", "DELAY TIME", "WOW", "FLUTTER",
    "DECAY", "PRE DELAY", "HIGH PASS", "LOW PASS", "DAMPING",
})


def is_request(text):
    words = set(re.findall(r"[\w']+", text.casefold()))
    if words & {"preset", "patch", "rig"} and words & {
        "make", "build", "create", "design", "draft", "generate", "give", "need", "want",
    } and not words & {"current", "existing"}:
        return True
    if re.match(r"(?i)^(?:preset|patch|rig)\s+for\s+\S", text):
        return True
    return bool(re.match(r"(?i)^(?:make|build|create|design|draft|generate)\b.{0,70}\b(?:tone|sound)\b", text)
                and not re.search(r"(?i)\b(this|current|warmer|brighter|less|more)\b", text))


def preset_name(text):
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", errors="ignore").decode()
    name = re.sub(r"[^a-zA-Z0-9 _&+'-]", " ", ascii_text)
    return " ".join(name.split())[:24].rstrip() or "Tone Lab Draft"


@lru_cache(maxsize=1)
def choices():
    available = {}
    for role, ids in MODELS.items():
        for model_id in ids:
            model = catalog.lookup(model_id)
            if not model:
                continue
            controls = {}
            for index, param in enumerate(model["params"]):
                name = param["name"]
                if param["type"] != "float" or name not in CONTROLS:
                    continue
                try:
                    lo, hi = float(param["min"]), float(param["max"])
                except (TypeError, ValueError):
                    continue
                if name == "FEEDBACK":
                    hi = min(hi, 70)
                controls[name] = (index, lo, hi)
            available[model_id] = {"role": role, "name": model["name"], "controls": controls}
    return available


def validate(recipe):
    if not isinstance(recipe, dict) or not isinstance(recipe.get("chain"), list):
        raise ValueError("The draft did not contain a signal chain.")
    available = choices()
    blocks, seen = [], set()
    for block in recipe["chain"]:
        model_id = block.get("model_id")
        if type(model_id) is not int or model_id not in available:
            raise ValueError("The draft picked a block outside the available stock models.")
        model = available[model_id]
        if model["role"] in seen:
            raise ValueError("Choose at most one block of each type in a draft.")
        seen.add(model["role"])
        settings = {}
        for setting in block["settings"]:
            key, value = setting["name"], setting["value"]
            control = model["controls"].get(key)
            if (not control or key in settings or type(value) not in (float, int)
                    or not math.isfinite(value) or not control[1] <= value <= control[2]):
                raise ValueError(f"The draft gave {model['name']} an unsupported {key} setting.")
            settings[key] = value
        blocks.append({"model_id": model_id, "settings": [
            {"name": key, "value": value} for key, value in settings.items()]})
    if not {"Amp", "Cab"} <= seen:
        raise ValueError("The draft needs both an amp and a cabinet.")
    order = tuple(MODELS)
    blocks.sort(key=lambda block: order.index(available[block["model_id"]]["role"]))
    return {"name": preset_name(recipe["name"]), "target": recipe["target"].strip(),
            "explanation": recipe["explanation"].strip(), "chain": blocks,
            "sources": advisor.source_links(recipe.get("sources", []))}


def design(text, guitar, take, previous=None, listening_note=""):
    def fields(**properties):
        return {"type": "object", "additionalProperties": False, "required": list(properties),
                "properties": properties}

    string = {"type": "string"}
    schema = fields(
        name=string, target=string, explanation=string,
        chain={"type": "array", "items": fields(
            model_id={"type": "integer"},
            settings={"type": "array", "items": fields(name=string, value={"type": "number"})})},
        sources={"type": "array", "items": fields(title=string, url=string)},
    )
    models = [{"id": model_id, "role": model["role"], "name": model["name"],
               "display_ranges": {key: [lo, hi] for key, (_, lo, hi) in model["controls"].items()}}
              for model_id, model in choices().items()]
    prompt = (
        "Design a practical, expressive Quad Cortex guitar preset. Return a short preset name, the chosen artist/track/"
        "passage or tonal goal, an explanation of the specific starting point and how it should feel (at most two "
        "sentences), and a signal chain. An artist-only request is an invitation to pick a useful starting point and name "
        "the assumption. For a named artist, song or historical rig, use web search if available and prefer artist/"
        "session participant sources. Cite only consulted pages supported by search. Separate documented gear from "
        "the proposed practical substitute and never claim an exact recording match. Requests can be in any language. "
        "You cannot hear the player's take. The take is an input performance, not a request to copy its wet preset. "
        "Revise previous_chain if the request refers to the previous draft. Use short, meaningful changes. "
        "Choose one amp and one cabinet plus at most one of each other role, only the listed models. Use the smallest "
        "chain that gets the sound. Omit settings that should stay default. Settings are display values, never normalized. "
        "Avoid maximum gain or mix without an explicit musical reason. Consider the guitar when known. "
        "The application handles routing and safe final level. No hardware commands and no local files.\n"
        + json.dumps({"request": text, "selected_guitar": guitar,
                      "take": {key: take[key] for key in ("guitar", "pickup_note") if key in take},
                      "previous_chain": previous, "listening_note": listening_note,
                      "available_models": models}, ensure_ascii=False)
    )
    result = advisor.request(prompt, schema, web=previous is None)
    if previous and not result["sources"]:
        result["sources"] = previous.get("sources", [])
    try:
        recipe = validate(result)
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise ValueError(f"The AI returned an unusable draft. Try the request again. {error}") from error
    draft = {**recipe, "id": uuid4().hex[:12], "created_utc": datetime.now(timezone.utc).isoformat(),
             "request": text, "guitar": guitar, "parent": previous.get("id") if previous else None}
    root = session.SESSIONS / "drafts"
    root.mkdir(parents=True, exist_ok=True)
    session.write_json(root / f"{draft['id']}.json", draft)
    return draft


def history():
    drafts = []
    for path in (session.SESSIONS / "drafts").glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["id"] == path.stem:
                drafts.append({**data, **validate(data)})
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return sorted(drafts, key=lambda draft: draft["created_utc"], reverse=True)


def chain_text(draft):
    return "  →  ".join(choices()[block["model_id"]]["name"] for block in draft["chain"])
