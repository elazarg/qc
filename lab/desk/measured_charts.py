"""What listening measured of a sound, drawn small for the Chain panel: the harmonics of a tone played through it,
each order against the tone, and its level in each band against the dry guitar. The figures are those of
`lab.hearing.probe` ("harmonics_db") and `lab.hearing.measure` ("band_levels_db"); the sentences that say every
measurement are a click away in the panel, and the exact figures of a chart are said while the pointer rests on it.
"""

from lab.desk.theme import DIM, FONT, RULE, SUBTLE

FLOOR = -70.        # the lowest a harmonic is measured to, in dB against the tone
HEIGHT = 92
FOOT = 14           # room under the bars for their names
HEAD = 14           # room above them for the chart's name


def draw_harmonics(canvas, harmonics, *, width, color):
    """One bar per harmonic, the 2nd to the 10th, rising from the floor to its level against the tone."""
    canvas.delete("all")
    canvas.configure(height=HEIGHT)
    canvas.create_text(0, 0, text="Harmonics against the tone", anchor="nw", fill=SUBTLE, font=(FONT, 8))
    bottom = HEIGHT - FOOT
    canvas.create_line(0, bottom, width, bottom, fill=RULE)
    step = width / max(len(harmonics), 1)
    for index, value in enumerate(harmonics):
        x = step * (index + .5)
        top = bottom - (max(FLOOR, min(0., value)) - FLOOR) / -FLOOR * (bottom - HEAD)
        if top < bottom - .5:
            canvas.create_rectangle(x - step * .3, top, x + step * .3, bottom, fill=color, width=0, tags="harmonic")
        canvas.create_text(x, HEIGHT, text=str(index + 2), anchor="s", fill=DIM, font=(FONT, 8))
    canvas.create_text(width, 0, text=f"{FLOOR:g} to 0 dB", anchor="ne", fill=DIM, font=(FONT, 8))


def harmonics_words(harmonics):
    return "Each harmonic against the tone: " + ", ".join(
        f"{index + 2}{_order(index + 2)} {value:.0f} dB" for index, value in enumerate(harmonics))


def draw_bands(canvas, bands, *, width, color):
    """One bar per band, up where the sound has more of it than the dry guitar and down where it has less, with
    the 200 Hz to 1 kHz band as the reference."""
    canvas.delete("all")
    canvas.configure(height=HEIGHT)
    canvas.create_text(0, 0, text="Level by band against your dry guitar", anchor="nw", fill=SUBTLE,
                       font=(FONT, 8))
    centre = HEAD + (HEIGHT - HEAD - FOOT) / 2
    reach = max(12., max((abs(value) for value in bands.values()), default=0.))
    canvas.create_line(0, centre, width, centre, fill=RULE)
    step = width / max(len(bands), 1)
    for index, (name, value) in enumerate(bands.items()):
        x = step * (index + .5)
        y = centre - value / reach * (centre - HEAD)
        if abs(y - centre) >= .5:
            canvas.create_rectangle(x - step * .3, centre, x + step * .3, y, fill=color, width=0, tags="band")
        canvas.create_text(x, HEIGHT, text=name, anchor="s", fill=DIM, font=(FONT, 8))
    canvas.create_text(width, 0, text=f"±{reach:.0f} dB", anchor="ne", fill=DIM, font=(FONT, 8))


def bands_words(bands):
    return "Against your dry guitar: " + ", ".join(f"{name} {value:+.1f} dB" for name, value in bands.items())


def _order(number):
    return "nd" if number == 2 else "rd" if number == 3 else "th"
