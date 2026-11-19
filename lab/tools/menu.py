"""The side tools for the desk's ⋯ menu, in the order they are listed. Each module has `open(desk)`.

    for title, name in TOOLS:
        menu.add_command(label=title, command=lambda name=name: opener(name)(desk))
"""

from importlib import import_module

TOOLS = [
    ("Library", "library"),
    ("Levels", "levels"),
    ("Tone search", "search"),
    ("Tone field", "field"),
    ("Comparison workbench", "workbench"),
    ("Controls workbench", "controls"),
    ("Setup and sound check", "soundcheck"),
    ("Usage and feedback", "usage"),
    ("POD Go presets", "pod"),
    ("Songs you listen to", "listening"),
]


def opener(name):
    """The `open(desk)` of a tool, imported when it is first wanted."""
    return import_module(f"lab.tools.{name}").open
