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
ACCENT = "#f7b889"
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
