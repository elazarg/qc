"""The tone field: several Shape knobs played as one gesture on a plane, and a measured A/B contrast.

Carried over from the old Tone field (`tone_space`, `tone_space_ui`). A plane names two directions, each a
blend of knobs ("Cleaner ↔ Saturated" is drive with a little sustain). A point on it is an offset from
where the knobs stood when the field was centred; the centre is exactly that position again. Points can be
marked A and B, jumped between, and swept from one to the other. Only knobs the selected sound has blocks
for move; a direction with none is greyed.

The knobs are the desk's own (`lab.sound.knobs`), turned on the selected side through the studio, so the field and
the Shape panel always agree. A gesture (a drag from press to release, a sweep from A to B) is heard on the
unit while it lasts and becomes the sound's knob positions once, at its end: one step for the desk's Undo,
one write of the sound, one check by listening. "Measure A and B" plays the phrase silently through both
sounds on the unit and says how B differs from A, level aside: octave-band balance, brightness, how much the
level moves, stereo sides and what rings on. It is asked for in the desk's Difference panel, which shows
what `contrast` found with `contrast_lines` and `draw_bands`; the field itself is the plane.
"""

from dataclasses import dataclass
import math
import tkinter as tk

from lab.sound.knobs import KNOBS
from lab.desk.theme import ACCENT, DARK, DIM, FONT, PAPER, RULE, SUBTLE, TONE_A
from lab.tools.common import WIDTH, Chips, Tool, button, disclosure, label, render_many
from lab.tools.search import BANDS, describe


@dataclass(frozen=True)
class Dimension:
    low: str
    high: str
    moves: tuple


@dataclass(frozen=True)
class Plane:
    horizontal: Dimension
    vertical: Dimension
    description: str


PLANES = {
    "Gain & color": Plane(
        Dimension("Cleaner", "Saturated", (("drive", .9), ("sustain", .18))),
        Dimension("Rounded", "Cutting", (("brightness", .72), ("presence", .63))),
        "Find the edge of breakup, then move the bite without moving the gain."),
    "Under the fingers": Plane(
        Dimension("Giving", "Immediate", (("attack", .78), ("sustain", -.48))),
        Dimension("Loose lows", "Tight lows", (("tightness", .9),)),
        "Play the same figure softly and firmly while you explore response and low-end control."),
    "Depth & width": Plane(
        Dimension("Close", "Distant", (("space", .72), ("brightness", -.23))),
        Dimension("Focused", "Wide", (("width", .92), ("modulation", .12))),
        "Move the sound back into a room, or spread it to the sides."),
    "Inside a mix": Plane(
        Dimension("Tucked in", "Forward", (("mids", .7), ("presence", .41), ("space", -.34))),
        Dimension("Lean", "Weighty", (("body", .83), ("tightness", -.1))),
        "Listen for where the guitar sits against a backing part at a steady playback level."),
    "Note shape": Plane(
        Dimension("Brief", "Held", (("sustain", .77), ("tail", .24), ("reverb", .15))),
        Dimension("Soft front", "Clear front", (("attack", .74), ("presence", .24))),
        "Compare how notes start and how much sound carries into the next note."),
    "Atmosphere": Plane(
        Dimension("Dry", "Bloom", (("reverb", .87), ("space", .18))),
        Dimension("Still", "In motion", (("modulation", .72), ("tail", .32), ("delay", .22))),
        "Move from direct sound to wash, then add motion when the chain supports it."),
}
BAND_NAMES = tuple(f"{(low * high) ** .5 / 1000:.1f}k" if (low * high) ** .5 >= 1000 else str(round((low * high) ** .5))
                   for low, high in zip(BANDS, BANDS[1:]))


def bounded(value, minimum=-1., maximum=1.):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Tone positions must be finite.")
    return min(maximum, max(minimum, value))


def moves(dimension, available=None):
    return {key: amount for key, amount in dimension.moves if available is None or key in available}


def position_values(anchor, plane, x, y, available=None):
    """One point as absolute knob positions, within each knob's range; the centre is the anchor exactly."""
    changes = {}
    for dimension, position in ((plane.horizontal, bounded(x)), (plane.vertical, bounded(y))):
        for key, amount in moves(dimension, available).items():
            changes[key] = changes.get(key, 0.) + position * amount
    values = dict(anchor)
    for key, delta in changes.items():
        if delta:
            values[key] = bounded(values.get(key, 0.) + delta, KNOBS[key][1])
    return values


def interpolate(start, end, fraction):
    """Between two knob snapshots, by one amount from 0 to 1."""
    fraction = bounded(fraction, 0., 1.)
    if fraction == 0.:
        return dict(start)
    if fraction == 1.:
        return dict(end)
    values = {}
    for key in start.keys() | end.keys():
        before, after = start.get(key, 0.), end.get(key, 0.)
        values[key] = bounded(before + (after - before) * fraction, KNOBS[key][1])
    return values


def equivalent(first, second):
    return all(abs(first.get(name, 0.) - second.get(name, 0.)) <= .005 for name in first.keys() | second.keys())


@dataclass(frozen=True)
class Fingerprint:
    spectrum_db: tuple
    perceptual_delta: tuple


def difference(before, after):
    """How `after` differs from `before`, whatever their levels."""
    a, b = describe(before), describe(after)
    return Fingerprint(
        tuple(right - left for left, right in zip(a.spectrum, b.spectrum)),
        (("Brightness", b.brightness - a.brightness, "oct"),
         ("Variation", b.dynamics - a.dynamics, "dB"),
         ("Stereo sides", (b.width - a.width) * 100, "pp"),
         ("Tail continuity", (b.tails - a.tails) * 100, "pp")),
    )


# ---- operations, run where the unit is held ----

def contrast(engine, a, b, dry, *, stop=None):
    """The phrase through A and through B, silently, and how B differs."""
    first, second = render_many(engine, [(a, None), (b, None)], dry, tail=1., stop=stop)
    found = difference(first, second)
    return {"spectrum_db": list(found.spectrum_db), "perceptual_delta": [list(item) for item in found.perceptual_delta]}


def contrast_lines(found):
    """What `contrast` found, in two lines of words."""
    return ["B against A, level aside:",
            "  ·  ".join(f"{name} {value:+.1f} {unit}" for name, value, unit in found["perceptual_delta"])]


def draw_bands(chart, values, *, width, height):
    """Draw the difference in each octave band (`contrast`'s "spectrum_db") as bars up and down from a line."""
    chart.delete("all")
    centre = (height - 18) / 2
    chart.create_line(4, centre, width - 4, centre, fill=RULE)
    scale = max(6., max(abs(value) for value in values))
    step = (width - 10) / len(values)
    for index, (value, name) in enumerate(zip(values, BAND_NAMES)):
        x = 5 + step * (index + .5)
        y = centre - value / scale * (centre - 5)
        chart.create_rectangle(x - step * .28, centre, x + step * .28, y, fill=ACCENT if value > 0 else TONE_A,
                               outline="")
        chart.create_text(x, height - 8, text=name, font=(FONT, 8), fill=SUBTLE)
    chart.create_text(width - 4, 3, anchor="ne", text=f"±{scale:.0f} dB", font=(FONT, 8), fill=SUBTLE)


# ---- the tool ----

@dataclass(frozen=True)
class Mark:
    plane: str
    point: tuple
    anchor: dict
    values: dict


class FieldTool(Tool):
    TITLE = "Tone field"

    def build(self):
        self.side = self.studio.state.side
        self.anchor = self.read_values()
        self.values = dict(self.anchor)
        self.point = (0., 0.)
        self.marks = {}
        self.drag_job = self.sweep_job = None
        self.plane = tk.StringVar(master=self.frame, value=next(iter(PLANES)))
        Chips(self.body, self.plane, tuple(PLANES), command=self.recenter).pack(fill="x")
        self.description = label(self.body, size=9, color=SUBTLE, wrap=WIDTH)
        self.description.pack(fill="x", pady=(2, 4))
        self.canvas = tk.Canvas(self.body, bg=PAPER, width=WIDTH, height=220, highlightthickness=1,
                                highlightbackground=RULE, highlightcolor=RULE, cursor="crosshair")
        self.canvas.pack(fill="x")
        self.canvas.bind("<Configure>", lambda _: self.paint())
        self.canvas.bind("<Button-1>", self.push)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _: self.apply_point())
        self.controls = label(self.body, size=9, color=SUBTLE, wrap=WIDTH)
        self.controls.pack(fill="x", pady=(4, 6))
        row = tk.Frame(self.body, bg=self.body["bg"])
        row.pack(fill="x")
        button(row, "Back to the centre", lambda: self.set_point(0., 0.), quiet=True).pack(side="left")
        button(row, "Use current as centre", self.recenter, quiet=True).pack(side="left", padx=4)

        # Marking two points and going between them is the rarer use: shown on request.
        marks = disclosure(self.body, "Remember a contrast")
        row = tk.Frame(marks, bg=marks["bg"])
        row.pack(fill="x")
        self.jump = {}
        for slot in ("A", "B"):
            button(row, f"Mark {slot}", lambda slot=slot: self.mark(slot)).pack(side="left", padx=(0, 4))
            self.jump[slot] = button(row, f"Jump to {slot}", lambda slot=slot: self.recall(slot), quiet=True)
            self.jump[slot].pack(side="left", padx=(0, 6))
        self.sweep_button = button(marks, "Sweep A → B", self.toggle_sweep, quiet=True)
        self.sweep_button.pack(anchor="w", pady=(4, 0))
        label(self.body, "How B differs from A as they are set now: Difference, Measure A and B now.", size=9,
              color=SUBTLE, wrap=WIDTH).pack(fill="x", pady=(8, 0))
        self.sync()

    def shown(self):
        """On show again: on the side selected now, from where its knobs stand."""
        super().shown()
        if self.side != self.studio.state.side or self.read_values() != self.values:
            self.recenter()

    def hidden(self):
        """Left: a sweep ends where it is, and is kept there."""
        self.stop_sweep()
        super().hidden()

    # what the desk holds

    def sound(self):
        return self.studio.sound(self.side)

    def read_values(self):
        sound = self.studio.sound(self.studio.state.side)
        return dict(sound.knobs) if sound else {}

    def available(self):
        return frozenset(self.studio.available(self.side)) if self.sound() else frozenset()

    def hear(self, values, *, exact=False):
        """Let these knob positions be heard without keeping them: sent to the unit, not written to the sound.
        Every step of a gesture is heard this way."""
        if (self.values != values) if exact else not equivalent(self.values, values):
            self.values = dict(values)
            if self.sound() is not None and self.studio.state.shown:
                self.studio.later(self.studio.host.call("turn", self.side,
                                                        {key: value for key, value in values.items() if value}),
                                  lambda moves: None)

    def keep(self):
        """The end of a gesture: what is heard becomes the sound's knob positions, in one step."""
        sound = self.sound()
        if sound is not None and {key: value for key, value in self.values.items() if value} != sound.knobs:
            self.studio.turn(dict(self.values), self.side)

    def sync(self):
        plane = PLANES[self.plane.get()]
        self.description.configure(text=plane.description)
        available = self.available()
        names = dict.fromkeys((*moves(plane.horizontal, available), *moves(plane.vertical, available)))
        caption = " · ".join(KNOBS[name][0] for name in names) or "The selected sound has nothing this field moves"
        self.controls.configure(text=caption)
        for slot, control in self.jump.items():
            control.configure(state="normal" if slot in self.marks else "disabled")
        self.sweep_button.configure(state="normal" if len(self.marks) == 2 else "disabled")
        self.paint()

    def recenter(self):
        self.stop_sweep()
        self.side = self.studio.state.side
        self.anchor = self.read_values()
        self.values, self.point = dict(self.anchor), (0., 0.)
        self.sync()

    # drawing

    def rect(self):
        width, height = max(240, self.canvas.winfo_width()), max(140, self.canvas.winfo_height())
        return 30, 28, width - 30, height - 34

    def coords(self, x, y):
        left, top, right, bottom = self.rect()
        return left + (right - left) * (x + 1) / 2, bottom - (bottom - top) * (y + 1) / 2

    def unproject(self, event):
        left, top, right, bottom = self.rect()
        return 2 * (event.x - left) / (right - left) - 1, 2 * (bottom - event.y) / (bottom - top) - 1

    def paint(self):
        canvas = self.canvas
        canvas.delete("all")
        left, top, right, bottom = self.rect()
        cx, cy = self.coords(0., 0.)
        canvas.create_rectangle(left, top, right, bottom, outline=RULE)
        plane = PLANES[self.plane.get()]
        available = self.available()
        x_color = SUBTLE if moves(plane.horizontal, available) else DIM
        y_color = SUBTLE if moves(plane.vertical, available) else DIM
        canvas.create_line(left, cy, right, cy, fill=x_color, dash=(2, 3))
        canvas.create_line(cx, top, cx, bottom, fill=y_color, dash=(2, 3))
        font = (FONT, 10, "bold")
        canvas.create_text(left, bottom + 18, text=plane.horizontal.low, anchor="w", fill=x_color, font=font)
        canvas.create_text(right, bottom + 18, text=plane.horizontal.high, anchor="e", fill=x_color, font=font)
        canvas.create_text(cx, top - 13, text=plane.vertical.high, fill=y_color, font=font)
        canvas.create_text(cx, bottom + 18, text=plane.vertical.low, fill=y_color, font=font)
        for slot, mark in self.marks.items():
            if mark.plane == self.plane.get() and equivalent(mark.anchor, self.anchor):
                x, y = self.coords(*mark.point)
                canvas.create_oval(x - 10, y - 10, x + 10, y + 10, outline=TONE_A, width=2)
                canvas.create_text(x, y, text=slot, fill=TONE_A, font=font)
        x, y = self.coords(*self.point)
        canvas.create_line(cx, cy, x, y, fill=ACCENT, width=2)
        canvas.create_oval(x - 8, y - 8, x + 8, y + 8, fill=ACCENT, outline=DARK, width=2)

    # moving

    def normal(self, x, y):
        plane, available = PLANES[self.plane.get()], self.available()
        return (bounded(x) if moves(plane.horizontal, available) else 0.,
                bounded(y) if moves(plane.vertical, available) else 0.)

    def set_point(self, x, y):
        """A move made in one step: heard and kept."""
        self.stop_sweep()
        self.point = self.normal(x, y)
        self.apply_point()

    def push(self, event):
        """The start of a drag: heard at once, kept when the button is let go."""
        self.stop_sweep()
        self.point = self.normal(*self.unproject(event))
        self.hear_point()

    def drag(self, event):
        self.point = self.normal(*self.unproject(event))
        self.paint()
        if self.drag_job is None:
            self.drag_job = self.after(160, self.hear_point)

    def hear_point(self):
        if self.drag_job is not None:
            self.after_cancel(self.drag_job)
        self.drag_job = None
        plane = PLANES[self.plane.get()]
        self.hear(position_values(self.anchor, plane, *self.point, self.available()))
        self.paint()

    def apply_point(self):
        """The end of a drag, or a move in one step: the point is heard, and kept."""
        self.hear_point()
        self.keep()

    def mark(self, slot):
        self.marks[slot] = Mark(self.plane.get(), self.point, dict(self.anchor), dict(self.values))
        self.sync()

    def recall(self, slot, *, keep=True):
        mark = self.marks[slot]
        self.stop_sweep()
        self.plane.set(mark.plane)
        self.anchor, self.point = dict(mark.anchor), mark.point
        self.hear(mark.values, exact=True)
        if keep:
            self.keep()
        self.sync()

    def toggle_sweep(self):
        if self.sweep_job is not None:
            self.stop_sweep()
            return
        if len(self.marks) == 2:
            self.recall("A", keep=False)        # the whole sweep is one gesture: kept where it ends
            self.sweep_button.configure(text="Stop sweep")
            self.sweep_job = self.after(400, lambda: self.sweep_step(1))

    def stop_sweep(self):
        if self.sweep_job is not None:
            self.after_cancel(self.sweep_job)
            self.sweep_job = None
            self.keep()                         # stopped on the way: kept where it stopped
        if self.alive():
            self.sweep_button.configure(text="Sweep A → B")

    def sweep_step(self, index, steps=8):
        """A sweep in a few steps: each is a write to the unit, so it moves slower than the old one did."""
        self.sweep_job = None
        if index >= steps:
            self.recall("B")                    # the end of the gesture: kept
            return
        a, b = self.marks["A"], self.marks["B"]
        ratio = index / steps
        values = interpolate(a.values, b.values, ratio)
        if a.plane == b.plane and equivalent(a.anchor, b.anchor):
            self.point = tuple(left + (right - left) * ratio for left, right in zip(a.point, b.point))
        else:
            self.anchor, self.point = values, (0., 0.)
        self.hear(values)
        self.paint()
        self.sweep_job = self.after(400, lambda: self.sweep_step(index + 1, steps))

def open(desk):  # noqa: A001
    return FieldTool.open(desk)
