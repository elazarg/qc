"""A record of everything asked of the unit and what it answered, for checking the simulated unit against the real one.

Start the app (or any command) with the environment variable TONE_LAB_TRACE set to a file, use it for a minute
(make a round, switch between A and B, turn a knob, save), and close it. `tests/test_lab_sim_contract.py` replays
such a file on the simulated unit when TONE_LAB_CONTRACT_TRACE names it, and says where the two differ: values
the unit rounded, changes it showed late, replies shaped otherwise. The file holds the player's presets, so it
belongs in the data folder or in `personal/`, never in the repository.
"""

import base64
import json
import time


def _plain(value):
    return json.loads(json.dumps(value, default=lambda other: float(other) if hasattr(other, "__float__")
                                 else str(other)))


def _told(reply):
    if type(reply).__name__ == "BinaryPreset":
        return {"preset": base64.b64encode(reply.SerializeToString()).decode("ascii")}
    if hasattr(reply, "DESCRIPTOR"):         # a message of the unit's: its plain fields
        return {"message": _plain({field.name: getattr(reply, field.name) for field in reply.DESCRIPTOR.fields
                                   if field.message_type is None and not field.is_repeated})}
    return {"message": _plain(vars(reply))} if hasattr(reply, "__dict__") else {"value": _plain(reply)}


def recording(device, path):
    """`device` with every call on it written to `path` as it is made: one JSON line for each call, with its
    arguments, what came back (a preset as its bytes in base64, a message as its fields, anything else as it
    is) or what was raised, and how long it took. The first line says what the file is."""

    class Recorded:
        def __getattr__(self, name):
            found = getattr(device, name)
            if not callable(found):
                return found

            def call(*args, **options):
                entry, started = {"call": name, "args": _plain(args), "options": _plain(options)}, time.monotonic()
                try:
                    reply = found(*args, **options)
                    entry.update(_told(reply))
                    return reply
                except Exception as error:
                    entry["raised"] = f"{type(error).__name__}: {error}"
                    raise
                finally:
                    entry["seconds"] = round(time.monotonic() - started, 4)
                    with open(path, "a", encoding="utf-8") as out:
                        out.write(json.dumps(entry) + "\n")
            return call

        def __setattr__(self, name, value):
            setattr(device, name, value)

    with open(path, "w", encoding="utf-8") as out:
        out.write(json.dumps({"trace": 1, "of": "Quad Cortex device calls",
                              "firmware": getattr(device, "firmware", None),
                              "recorded": time.strftime("%Y-%m-%d %H:%M")}) + "\n")
    return Recorded()
