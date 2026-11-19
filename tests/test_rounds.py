"""A request answered with an aim and several designs, checked by listening before anyone hears them."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_draft_forms import DELAY, GOVERNOR, PLEXI, RECTIFIER, V30, block  # noqa: E402
from tone_lab import advisor, preset_design, rounds, session  # noqa: E402

FUZZ, HIWATT = 22, 1063


def returned(name, chain, differs):
    """One design as the assistant returns it."""
    return {"name": name, "target": "Test", "explanation": "Test", "chain": chain, "scenes": [],
            "parallel_chain": [], "parallel_level_db": 0, "parallel_spread": 0, "sources": [],
            "excluded_stages": [], "owned_assets": [], "differs": differs}


def reply():
    return {"aim": {"drive": "saturated", "tone": "bright", "space": "ambient", "note": "A singing fuzz lead."},
            "designs": [
                returned("By the book", [block(FUZZ, fuzz=7.6), block(HIWATT), block(V30), block(DELAY, mix=20.)],
                         "The documented fuzz and amp."),
                returned("Measured", [block(RECTIFIER), block(V30)], "The amp that measures saturated."),
                returned("Another way", [block(GOVERNOR), block(PLEXI), block(V30)], "A pedal into a Plexi.")]}


def sound(drive, tone="balanced", space="dry"):
    return {"drive": drive, "tone": tone, "space": space, "squash": 0., "brightness": 0., "ambience_db": -60.}


class Ear:
    """Stands in for the unit: says what each design sounds like, and how its knobs change that."""

    def __init__(self):
        self.calls = []

    def __call__(self, draft, values):
        self.calls.append((draft["name"], dict(values)))
        if draft["name"] == "By the book":  # Tame and dark until Drive is at full and Warmth is turned down.
            drive = "saturated" if values.get("drive", 0) >= 1 else "crunch" if values.get("drive") else "edge of breakup"
            return sound(drive, "bright" if values.get("warmth", 0) <= -.8 else "dark", "ambient")
        if draft["name"] == "Measured":
            space = values.get("space", 0)
            return sound("saturated", "bright", "wet" if space > .5 else "ambient" if space > 0 else "dry")
        return sound("overdriven", "balanced", "dry")


class RoundTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        home = patch.object(session, "SESSIONS", Path(folder.name))
        home.start()
        self.addCleanup(home.stop)
        asked = patch.object(advisor, "request", return_value=reply())
        self.request = asked.start()
        self.addCleanup(asked.stop)

    def test_one_call_returns_an_aim_and_designs_that_say_how_they_differ(self):
        found = rounds.start("the solo of Time", "Charvel")
        self.request.assert_called_once()
        prompt, schema = self.request.call_args.args
        self.assertFalse(self.request.call_args.kwargs["web"])  # The first round does not wait for research.
        self.assertIn("an aim and three designs", prompt)
        self.assertEqual(schema["properties"]["aim"]["properties"]["drive"]["enum"], list(preset_design.AIM["drive"]))
        self.assertEqual(found["aim"]["drive"], "saturated")
        self.assertEqual([c["name"] for c in found["candidates"]], ["By the book", "Measured", "Another way"])
        self.assertEqual(found["candidates"][0]["differs"], "The documented fuzz and amp.")
        self.assertEqual(rounds.load(found["id"]), found)
        self.assertIn(RECTIFIER, [part["model_id"] for part in rounds.draft_of(found["candidates"][1])["chain"]])

    def test_a_design_that_cannot_be_used_does_not_sink_the_round(self):
        broken = reply()
        broken["designs"][1]["chain"] = [{"model_id": 999999, "settings": []}]
        self.request.return_value = broken
        self.assertEqual([c["name"] for c in rounds.start("x", "Charvel")["candidates"]], ["By the book", "Another way"])
        self.request.return_value = {**reply(), "aim": {"drive": "loud", "tone": "bright", "space": "dry", "note": ""}}
        with self.assertRaisesRegex(ValueError, "does not measure"):
            rounds.start("x", "Charvel")

    def test_listening_turns_the_apps_own_knobs_towards_the_aim_and_ranks_the_designs(self):
        ear = Ear()
        found = rounds.check(rounds.start("the solo of Time", "Charvel"), hear=ear)
        by_name = {c["name"]: c for c in found["candidates"]}
        book = by_name["By the book"]
        # As designed it was tame and dark. Drive at full and less Warmth brought it to the aim.
        self.assertEqual((book["heard"]["drive"], book["heard"]["tone"]), ("edge of breakup", "dark"))
        self.assertEqual(book["shape"], {"drive": 1., "warmth": -.8})
        self.assertEqual(book["tags"], ["saturated", "bright", "ambient", "on the aim"])
        # It was three words short on drive and two on tone, so the large move was tried first each time.
        self.assertEqual([call for call in ear.calls if call[0] == "By the book"],
                         [("By the book", {}), ("By the book", {"drive": 1.}),
                          ("By the book", {"drive": 1., "warmth": -.8}), ("By the book", {"drive": 1., "warmth": -.8})])
        self.assertEqual(by_name["Measured"]["shape"], {"space": .4})
        self.assertEqual(by_name["Another way"]["tags"][3:],
                         ["less driven than the aim", "darker than the aim", "drier than the aim"])
        self.assertEqual([c["name"] for c in found["candidates"]], ["By the book", "Measured", "Another way"])
        self.assertEqual([c["miss"] for c in found["candidates"]], [0, 0, 5])
        # Listening again costs nothing: what was heard is kept.
        again = Ear()
        rounds.check(found, hear=again)
        self.assertEqual(again.calls, [])

    def test_a_design_the_player_has_heard_keeps_its_place(self):
        found = rounds.check(rounds.start("the solo of Time", "Charvel"), hear=Ear())
        found["candidates"][2]["played"] = True
        self.assertEqual(rounds.check(found, hear=Ear())["candidates"][0]["name"], "Another way")

    def test_the_next_round_is_told_what_was_measured_and_what_the_player_said(self):
        found = rounds.check(rounds.start("the solo of Time", "Charvel"), hear=Ear())
        found["candidates"][0].update(played=True, review="right fuzz, still too dark", user_tags=["close"])
        rounds.save(found)
        second = rounds.again(found, "brighter, and more echo")
        prompt = self.request.call_args.args[0]
        self.assertTrue(self.request.call_args.kwargs["web"])  # Later rounds may take the time to look things up.
        self.assertIn("The player now says: brighter, and more echo", prompt)
        self.assertIn('"player_said": "right fuzz, still too dark"', prompt)
        self.assertIn('"knobs_moved": {"drive": 1.0, "warmth": -0.8}', prompt)
        self.assertIn('"measured_as_designed": {"drive": "edge of breakup", "tone": "dark", "space": "ambient"}', prompt)
        self.assertIn('"player_heard_it": false', prompt)
        self.assertEqual(second["parent"], found["id"])
        self.assertEqual(second["request"].splitlines()[0], "the solo of Time")

    def test_words_for_what_missed(self):
        aim = {"drive": "saturated", "tone": "balanced", "space": "ambient"}
        self.assertEqual(rounds.tags(aim, sound("clean", "bright", "wet")),
                         ["clean", "bright", "wet", "less driven than the aim", "brighter than the aim",
                          "wetter than the aim"])
        self.assertEqual(rounds.distance(rounds.miss(aim, sound("clean", "bright", "wet"))), 2 * 4 + 1 + 1)
        lines = []
        rounds.show(rounds.check(rounds.start("the solo of Time", "Charvel"), hear=Ear()), lines.append)
        self.assertIn("Aiming for: saturated, bright, ambient. A singing fuzz lead.", lines[1])


if __name__ == "__main__":
    unittest.main()
