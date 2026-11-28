"""One colour and type vocabulary for the desk, the side tools and the Guitar Learner."""

PAPER = "#14151a"
DARK = "#1e2028"
WHITE = "#252834"
TILE = "#2c303c"
RULE = "#383c4a"
INK = "#f0ede9"
SOFT = "#bcbcc9"
SUBTLE = "#a5a6b5"
DIM = "#8b8fa5"                  # the quietest text: still 4.5:1 or more on the panels
ACCENT = "#c9b2ff"                # what can be clicked to go somewhere: a colour no sound or state has
ACCENT_INK = "#1a1718"
CHOSEN = "#4a4e5e"               # behind a choice that is chosen, with INK on it: one look on every screen
FONT = "Segoe UI"

# Colour carries identity and state. It is never used for emphasis alone.
TONE_A = "#8fb2ff"
TONE_B = "#f7b889"
LIVE = "#6fdd9f"
FAULT = "#ff9e8f"                # something went wrong, or could not be done
RECORDING = "#ff5a5f"            # the input is being recorded
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
    "search": "",                          # a magnifying glass, before a line that filters a list
    "heard": "", "muted": "",    # the guitar heard through the selected sound, or muted
    "once": "",                         # play the phrase once through
}


# A block of a chain as a symbol of what it does, by the start of its category in the unit's catalogue; its
# name is said on hover. Anything not listed (routing, utilities, loops) is a gear.
BLOCKS = (
    ("Guitar Overdrive", ""), ("Bass Overdrive", ""),    # a bolt
    ("Guitar Amplifier", ""), ("Bass Amplifier", ""),    # an amp's head
    ("Cabsim", ""), ("IRLoader", ""),                    # a speaker cabinet
    ("Neural Capture", ""),                                    # a chip
    ("Delay", ""),                                             # a clock turning back
    ("Reverb", ""),                                            # a cloud: the room around the sound
    ("Modulation", ""), ("Morph", ""),                   # a moving wave
    ("Compressor", ""),                                        # brackets closing in
    ("Equalizer", ""),                                         # sliders
    ("Wah", ""), ("Filter", ""),                         # a funnel
    ("Pitch", ""), ("Synth", ""),                        # a note
)
GEAR = ""


def block_glyph(block):
    """The symbol of a block (anything with `.info.category`, or none)."""
    category = getattr(getattr(block, "info", None), "category", "") or ""
    return next((glyph for start, glyph in BLOCKS if category.startswith(start)), GEAR)


_icons = []


def icon_font(size=13):
    """The icon font at a size, falling back to the older one where the newer is not installed. (Which is
    installed is asked once: a Tk window must exist by then.)"""
    if not _icons:
        import tkinter.font

        _icons.append(ICONS if ICONS in tkinter.font.families() else "Segoe MDL2 Assets")
    return (_icons[0], size)
