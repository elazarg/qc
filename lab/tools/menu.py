"""The side tools the desk lists in its More panel, in the order they are listed.

Each module has `open(desk)`, which shows the tool in the desk's window, in the place of the side panel
(`lab.tools.common.Tool`). What the Library tool did is in the desk's Sounds panel (`lab.tools.library`
keeps its operations), and measuring how B differs from A is in the Difference panel (`lab.tools.field`).
"""

from importlib import import_module

TOOLS = [
    ("Levels", "levels"),
    ("Tone search", "search"),
    ("Tone field", "field"),
    ("Comparison workbench", "workbench"),
    ("Versions and the unit", "controls"),
    ("Setup and sound check", "soundcheck"),
    ("Usage and feedback", "usage"),
    ("POD Go presets", "pod"),
    ("Songs you listen to", "listening"),
]


def opener(name):
    """The `open(desk)` of a tool, imported when it is first wanted."""
    return import_module(f"lab.tools.{name}").open
