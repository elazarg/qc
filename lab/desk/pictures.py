"""Small pictures drawn from tables of shapes: one for each kind of instrument, and the symbol for learning.

    from lab.desk.pictures import instrument_picture, learning_symbol
    image = instrument_picture(widget, {"name": "Les Paul", "kind": "electric"}, 56, 24, paper=TILE)

There are no picture files and no image library: a picture is a few outlines (`SHAPES`), filled on a grid four
times as fine as the picture and averaged down, so that its edges are smooth, and handed to Tk as a
`PhotoImage`. All of them are drawn in one style (a light body, a darker neck, the details cut out), lying with
the head to the left. An instrument has the picture of its kind; an electric whose name says Les Paul has the
single-cut one. An instrument the player adds has its kind's picture.
"""

import tkinter as tk

import numpy as np

from lab.desk.theme import DIM, SOFT, mix

FINE = 4                # the grid a picture is filled on is this many times finer than the picture
ACROSS, DOWN = 112, 48  # the units an instrument's shapes are written in: head at the left, body at the right
SYMBOL = 48, 40         # and the learning symbol's


def S(x, y):
    """A point of an outline that stays a corner; the others are rounded."""
    return (x, y, True)


def both_sides(top):
    """An outline that is the same above and below the middle line, from its upper half, left to right."""
    return [*top, *[(point[0], DOWN - point[1], *point[2:]) for point in reversed(top)]]


def box(left, top, right, bottom):
    return [S(left, top), S(right, top), S(right, bottom), S(left, bottom)]


# kind -> what is drawn, in order: (tone, an outline) or (tone, (x, y, radius)) for a disc. Tones: "body", "neck"
# and "cut" (the colour behind the picture, for what is cut out of it).
SHAPES = {
    "superstrat": [
        ("neck", box(20, 21.3, 62, 26.7)),
        ("neck", [(2, 24), (5, 17.5), (13, 16.2), S(21, 19.5), S(24, 21.3), S(24, 26.7), (19, 28.5), (10, 29),
                  (4.5, 29.5)]),
        ("body", [S(50, 5), (59, 7.5), (72, 5.5), (90, 5), (103, 10), (110, 19), (110, 30), (103, 39.5), (90, 44),
                  (74, 43), (64, 42.5), S(55.5, 44), (62.5, 36.5), (62, 31), S(61, 27.5), S(61, 20.5), (62.5, 17),
                  (60.5, 12.5)]),
        ("cut", box(70, 18, 74.4, 30)), ("cut", box(82, 18, 86.4, 30)), ("cut", box(96, 18.5, 98.8, 29.5)),
    ],
    "single-cut": [
        ("neck", box(20, 21.3, 60, 26.7)),
        ("neck", [S(3, 17.5), (9, 16.8), S(21, 19.5), S(24, 21.3), S(24, 26.7), S(21, 28.5), (9, 31.2), S(3, 30.5),
                  S(6, 24)]),
        ("body", [S(59, 20.5), (60, 12), (67, 7), (76, 7), (83, 8.8), (92, 4.5), (104, 8.5), (110.5, 19),
                  (110.5, 29), (104, 39.5), (92, 43.5), (83, 42), (75, 43.5), (69, 44.5), S(58, 45), (64.5, 36.5),
                  (62.5, 31), S(59, 27.5)]),
        ("cut", box(70, 18, 74.4, 30)), ("cut", box(82, 18, 86.4, 30)), ("cut", box(94, 18.5, 96.2, 29.5)),
        ("cut", box(100, 19.5, 102.2, 28.5)),
    ],
    "bass": [
        ("neck", box(21, 21.8, 70, 26.2)),
        ("neck", [(1.5, 26.5), (3.5, 18.5), (12, 16), S(22, 19.8), S(25, 21.8), S(25, 26.2), (20, 28), (8, 30)]),
        *[("neck", (x, 14.2, 2.1)) for x in (6.5, 11.3, 16.1, 20.9)],
        ("body", [S(56, 5), (67, 8.5), (80, 6.5), (94, 6.5), (105, 12), (110.5, 21), (110, 31), (103, 40), (90, 44),
                  (79, 42.5), S(65.5, 43.5), (72.5, 36), (71, 30.5), S(69.5, 27), S(69.5, 21), (71, 17.5), (69, 13)]),
        ("cut", box(84, 17.5, 88.4, 30.5)), ("cut", box(99, 18.5, 102.5, 29.5)),
    ],
    "acoustic": [
        ("neck", box(20, 21, 58, 27)),
        ("neck", [S(4, 18), S(21, 19.3), S(24, 21), S(24, 27), S(21, 28.7), S(4, 30)]),
        *[("neck", (x, y, 1.5)) for x in (7.5, 12.5, 17.5) for y in (16.6, 31.4)],
        ("body", both_sides([(54.5, 24), (55.5, 14.5), (60, 8), (69, 5.5), (77, 7.8), (83, 10.5), (92, 3.2),
                             (104, 6), (110.5, 15.5), (111.7, 24)])[:-1]),
        ("cut", (76, 24, 5.4)), ("cut", box(96.5, 16, 99.3, 32)),
    ],
    "classical": [
        ("neck", box(20, 20.4, 59, 27.6)),
        ("neck", [S(3, 17.6), S(21, 18.6), S(24, 20.4), S(24, 27.6), S(21, 29.4), S(3, 30.4)]),
        ("cut", box(6.5, 20.3, 19, 22.4)), ("cut", box(6.5, 25.6, 19, 27.7)),
        ("body", both_sides([(56, 24), (57, 15.5), (61, 9.5), (69, 7), (76.5, 9), (82.5, 12.5), (90.5, 5.5),
                             (102, 7), (109, 15.5), (110.5, 24)])[:-1]),
        ("cut", (77, 24, 7.6)), ("body", (77, 24, 6.2)), ("cut", (77, 24, 4.6)),
        ("cut", box(96, 18.5, 99, 29.5)),
    ],
}


# The symbol for learning: a mortarboard with its tassel.
LEARNING = [
    ("body", [S(24, 5), S(45, 14.5), S(24, 24), S(3, 14.5)]),
    ("body", [S(11.5, 21.5), S(24, 27.3), S(36.5, 21.5), (36.5, 29.5), (24, 34.5), (11.5, 29.5)]),
    ("body", box(40.4, 15.5, 42, 27.5)), ("body", (41.2, 29.3, 2.6)),
]


def kind_of(instrument):
    """Which picture an instrument has: its kind's, and the single-cut one for an electric called a Les Paul."""
    name = str((instrument or {}).get("name", "")).casefold()
    kind = str((instrument or {}).get("kind", "electric")).casefold()
    if kind in ("bass", "acoustic", "classical"):
        return kind
    return "single-cut" if "les paul" in name or name.startswith("lp") else "superstrat"


def rounded(outline, times=3):
    """An outline with its corners cut, again and again, except the points marked as corners (`S`)."""
    points = [(float(point[0]), float(point[1]), len(point) > 2 and bool(point[2])) for point in outline]
    for _ in range(times):
        made = []
        for index, (x, y, sharp) in enumerate(points):
            next_x, next_y, next_sharp = points[(index + 1) % len(points)]
            made.append((x, y, True) if sharp else (.75 * x + .25 * next_x, .75 * y + .25 * next_y, False))
            if not next_sharp:
                made.append((.25 * x + .75 * next_x, .25 * y + .75 * next_y, False))
        points = made
    return [(x, y) for x, y, _ in points]


def covered(shape, xs, ys):
    """Which points of a grid lie inside a shape: a disc (x, y, radius) or an outline."""
    if len(shape) == 3 and not isinstance(shape[0], (tuple, list)):
        x, y, radius = shape
        return (xs - x) ** 2 + (ys - y) ** 2 <= radius ** 2
    inside = np.zeros(xs.shape, dtype=bool)
    points = rounded(shape)
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
        if y0 == y1:
            continue
        crosses = (y0 > ys) != (y1 > ys)
        inside ^= crosses & (xs < x0 + (ys - y0) * (x1 - x0) / (y1 - y0))
    return inside


def rgb(color):
    return np.array([int(color[index:index + 2], 16) for index in (1, 3, 5)], dtype=float)


def rows(shapes, units, width, height, tones):
    """A picture as rows of colours ("#rrggbb"): `shapes` (written in `units` across and down) filled on a fine
    grid in the colours of `tones`, and averaged down to `width` by `height`."""
    across, down = units
    fine_x, fine_y = width * FINE, height * FINE
    xs, ys = np.meshgrid((np.arange(fine_x) + .5) * across / fine_x, (np.arange(fine_y) + .5) * down / fine_y)
    image = np.empty((fine_y, fine_x, 3))
    image[:] = rgb(tones["cut"])
    for tone, shape in shapes:
        image[covered(shape, xs, ys)] = rgb(tones[tone])
    small = image.reshape(height, FINE, width, FINE, 3).mean(axis=(1, 3)).round().astype(int)
    return [["#%02x%02x%02x" % tuple(pixel) for pixel in row] for row in small]


_drawn = {}     # (which picture, size, tones) -> its rows: the same picture is worked out once


def image(master, key, shapes, units, width, height, tones):
    found = _drawn.get((key, width, height, tuple(sorted(tones.items()))))
    if found is None:
        found = _drawn[(key, width, height, tuple(sorted(tones.items())))] = rows(shapes, units, width, height, tones)
    picture = tk.PhotoImage(master=master, width=width, height=height)
    picture.put(" ".join("{" + " ".join(row) + "}" for row in found))
    return picture


def instrument_picture(master, instrument, width, height, *, paper, body=SOFT, neck=None):
    """The picture of an instrument (its record from the store, or just {"name", "kind"}), on `paper`."""
    kind = kind_of(instrument)
    tones = {"body": body, "neck": neck or mix(DIM, body, .55), "cut": paper}
    return image(master, kind, SHAPES[kind], (ACROSS, DOWN), width, height, tones)


def learning_symbol(master, width, height, *, paper, ink=SOFT):
    return image(master, "learning", LEARNING, SYMBOL, width, height, {"body": ink, "cut": paper})
