"""One colour and type vocabulary for the desk, the side tools and the Guitar Learner."""

PAPER = "#14151a"
DARK = "#1e2028"
WHITE = "#252834"
TILE = "#2c303c"
RULE = "#383c4a"
INK = "#f0ede9"
SOFT = "#bcbcc9"
SUBTLE = "#a5a6b5"
DIM = "#71758a"
ACCENT = "#c9b2ff"                # what can be clicked to go somewhere: a colour no sound or state has
ACCENT_INK = "#1a1718"
FONT = "Segoe UI"

# Colour carries identity and state. It is never used for emphasis alone.
TONE_A = "#8fb2ff"
TONE_B = "#f7b889"
LIVE = "#6fdd9f"
FAULT = "#ff9e8f"                # something went wrong, or could not be done
SIDE = {"a": TONE_A, "b": TONE_B}


def mix(color, base, amount):
    """Blend `amount` of color into base."""
    front = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
    back = [int(base[index:index + 2], 16) for index in (1, 3, 5)]
    return "#" + "".join(f"{round(b + (f - b) * amount):02x}" for f, b in zip(front, back))

# Symbols, from the icon font Windows ships (Segoe Fluent Icons on Windows 11, Segoe MDL2 Assets before it; the
# two share these code points). Only symbols everybody reads without words: each control that shows one also
# says its name while the pointer rests on it (`lab.desk.hints.named`).
ICONS = "Segoe Fluent Icons"
GLYPH = {
    "keep": "", "kept": "",         # a heart, and the heart filled
    "not this": "",                        # a cross
    "copy": "",                            # an arrow to the left: B onto A
    "blind": "", "shown": "",       # an eye, and the eye struck through while names are hidden
    "undo": "",
    "loop": "",
    "record": "",
}


_icons = []


def icon_font(size=13):
    """The icon font at a size, falling back to the older one where the newer is not installed. (Which is
    installed is asked once: a Tk window must exist by then.)"""
    if not _icons:
        import tkinter.font

        _icons.append(ICONS if ICONS in tkinter.font.families() else "Segoe MDL2 Assets")
    return (_icons[0], size)
