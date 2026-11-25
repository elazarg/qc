"""What the instrument puts out, drawn: one row per pickup setting, a bar to the peak of its firmly held notes on a
scale of dBFS, a dashed line where the humbucker most presets assume peaks, a tick for the largest single peak
and a short mark for the noise in the silences. The figures behind it are `lab.hearing.profile`'s, and the
sentences that say them are a click away in the Guitar panel.

The bar is named for what it measures, the held-note peak in the takes recorded; that a setting is hotter or
weaker than presets assume is read from the gap to the dashed line, not stated.
"""

from lab.desk.theme import DIM, FONT, INK, RULE, SOFT, SUBTLE
from lab.hearing.profile import TYPICAL_PEAK_DB

LOWEST, HIGHEST = -48., 0.      # the dBFS the scale shows
ROW = 22
TOP = 16                        # room above the rows for the dashed line's name
BOTTOM = 16                     # room under them for the scale
NAME = 150                      # pixels for a setting's name
FIGURES = 58                    # pixels for its peak in figures


def height_for(summary):
    return TOP + ROW * len(summary) + BOTTOM


def x_of(value, width):
    span = width - NAME - FIGURES - 8
    clipped = max(LOWEST, min(HIGHEST, value))
    return NAME + (clipped - LOWEST) / (HIGHEST - LOWEST) * span


def draw(canvas, summary, *, width, chosen=None):
    """Draw `summary` ({setting: profile summary}) on `canvas`; the setting `chosen` (that of the phrase in use)
    is named in the brightest ink."""
    canvas.delete("all")
    height = height_for(summary)
    canvas.configure(height=height)
    typical = x_of(TYPICAL_PEAK_DB, width)
    canvas.create_line(typical, TOP - 2, typical, height - BOTTOM + 2, fill=DIM, dash=(3, 3), tags="typical")
    canvas.create_text(typical, 2, text="the humbucker presets assume", anchor="n", fill=DIM, font=(FONT, 9))
    for row, (setting, item) in enumerate(summary.items()):
        y = TOP + row * ROW + ROW / 2
        canvas.create_text(0, y, text=setting, anchor="w", fill=INK if setting == chosen else SUBTLE, font=(FONT, 9))
        canvas.create_line(NAME, y, x_of(HIGHEST, width), y, fill=RULE, width=1)
        held = item.get("held_peak_db")
        level = held if held is not None else item.get("peak_db")
        if level is None:
            continue
        canvas.create_rectangle(NAME, y - 5, x_of(level, width), y + 5, fill=SOFT, width=0, tags="held")
        if item.get("peak_db") is not None and held is not None:
            x = x_of(item["peak_db"], width)
            canvas.create_line(x, y - 7, x, y + 7, fill=SOFT, width=2, tags="peak")
        noise = item.get("noise_db")
        if noise is not None and noise >= LOWEST:
            x = x_of(noise, width)
            canvas.create_line(x, y + 3, x, y + 9, fill=DIM, width=2, tags="noise")
        canvas.create_text(width, y, text=f"{level:g} dBFS", anchor="e", fill=SUBTLE, font=(FONT, 9))
    for value in (LOWEST, -36., -24., -12., HIGHEST):
        canvas.create_text(x_of(value, width), height - 2, text=f"{value:g}", anchor="s", fill=DIM, font=(FONT, 9))


def words(summary):
    """What the chart shows, for the pointer resting on it."""
    return ("Bars: the peak of firmly held notes in the takes recorded on each setting. Dashed line: where a "
            f"medium-hot humbucker peaks ({TYPICAL_PEAK_DB:g} dBFS), which most presets assume. Tick: the largest "
            "single peak. Short mark under the bar: the noise in the silences, when it is on the scale.")
