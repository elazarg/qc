"""Play and compare several musical controls as a single gesture."""

from dataclasses import dataclass
from queue import Empty, SimpleQueue
from threading import Thread
import tkinter as tk

from . import diagnostics, listening, styles, tone_space


BG = "#14151a"
PANEL = "#20232c"
WELL = "#181a22"
TEXT = "#f0ede9"
MUTED = "#a5a6b5"
RULE = "#383c4a"
ACCENT = "#f7b889"
BLUE = "#8caef8"
FONT = "Segoe UI"


@dataclass(frozen=True)
class Mark:
    plane: str
    point: tuple[float, float]
    anchor: dict
    values: dict


class ToneSpacePanel(tk.Frame):
    """Maps whole-tone gestures to the desk's existing live and reamp controls."""

    def __init__(self, parent, *, read_values, apply_values, audition=None, read_sounds=None):
        super().__init__(parent, bg=BG, padx=10, pady=6)
        self.read_values = read_values
        self.apply_values = apply_values
        self.read_sounds = read_sounds
        self.anchor = dict(read_values())
        self.values = dict(self.anchor)
        self.available = None
        self.point = (0., 0.)
        self.marks = {}
        self.drag_job = self.sweep_job = self.measure_job = None
        self.result = self.measuring = None
        self.plane = tk.StringVar(master=self, value=next(iter(tone_space.PLANES)))
        self.description = tk.StringVar(master=self)
        self.controls_text = tk.StringVar(master=self)
        self.measure_text = tk.StringVar(master=self, value="Measure the two sounds after auditioning them.")
        self.build(audition)
        self.sync()
        self.bind("<Unmap>", self.unmap)
        self.bind("<Destroy>", self.destroy_jobs)

    def text(self, parent, title=None, *, variable=None, size=10, strong=False):
        return tk.Label(parent, text=title, textvariable=variable, background=parent["bg"],
                        foreground=TEXT if strong else MUTED, font=(FONT, size, "bold" if strong else "normal"),
                        justify="left", anchor="w")

    @staticmethod
    def button(parent, title, fn):
        return tk.Button(parent, text=title, command=fn, background=WELL, foreground=TEXT,
                         activebackground=RULE, activeforeground=TEXT, disabledforeground=MUTED,
                         font=(FONT, 10), padx=8, pady=5, borderwidth=0, relief="flat",
                         highlightthickness=1, highlightbackground=RULE, highlightcolor=ACCENT, takefocus=True)

    def build(self, audition):
        heading = tk.Frame(self, bg=BG)
        heading.pack(fill="x", pady=(0, 7))
        self.text(heading, "Tone field", size=15, strong=True).pack(side="left")
        plane_button = tk.Menubutton(heading, textvariable=self.plane, indicatoron=True,
                                    font=(FONT, 10, "bold"), bg=PANEL, fg=TEXT,
                                    activebackground=RULE, activeforeground=TEXT, padx=8, pady=5, takefocus=True)
        planes = tk.Menu(plane_button, tearoff=False, bg=PANEL, fg=TEXT, activebackground=RULE, activeforeground=TEXT)
        for name in tone_space.PLANES:
            planes.add_radiobutton(label=name, variable=self.plane, value=name, command=self.recenter)
        plane_button.configure(menu=planes)
        plane_button.pack(side="right")
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3, uniform="lab")
        body.grid_columnconfigure(1, weight=2, uniform="lab")
        body.grid_rowconfigure(0, weight=1)
        field = tk.Frame(body, background=PANEL, padx=10, pady=8)
        field.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        description = self.text(field, variable=self.description)
        description.pack(fill="x", pady=(0, 4))
        field.bind("<Configure>", lambda event: description.configure(wraplength=max(120, event.width - 21)))
        self.canvas = tk.Canvas(field, bg=WELL, height=230, width=380, highlightbackground=RULE,
                                highlightcolor=ACCENT, highlightthickness=1, takefocus=True, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self.paint())
        self.canvas.bind("<Button-1>", self.push)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.finish_drag)
        self.canvas.bind("<Home>", lambda _: self.set_point(0., 0., exact=True))
        for key, dx, dy in (("Left", -.12, 0.), ("Right", .12, 0.), ("Down", 0., -.12), ("Up", 0., .12)):
            self.canvas.bind(f"<{key}>", lambda _, x=dx, y=dy:
                             self.set_point(self.point[0] + x, self.point[1] + y))
        self.text(field, variable=self.controls_text, size=9).pack(fill="x", pady=(4, 0))

        compare = tk.Frame(body, background=PANEL, padx=10, pady=8)
        compare.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.text(compare, "Remember a contrast", size=11, strong=True).pack(fill="x", pady=(0, 6))
        actions = tk.Frame(compare, bg=PANEL)
        actions.pack(fill="x")
        self.recall_buttons = {}
        for slot in ("A", "B"):
            column = tk.Frame(actions, bg=PANEL)
            column.pack(side="left", padx=(0, 6))
            self.button(column, f"Mark {slot}", lambda name=slot: self.mark(name)).pack(fill="x")
            self.recall_buttons[slot] = self.button(column, f"Jump to {slot}", lambda name=slot: self.recall(name))
            self.recall_buttons[slot].pack(fill="x", pady=(4, 0))
        controls = tk.Frame(compare, bg=PANEL)
        controls.pack(fill="x", pady=(7, 3))
        self.travel = self.button(controls, "Sweep A → B", self.toggle_sweep)
        self.travel.pack(side="left", padx=(0, 5))
        self.button(controls, "Use current as center", self.recenter).pack(side="left")
        self.text(compare, "Snapshots of these controls", size=9).pack(fill="x")
        if audition:
            self.button(compare, "Render this position", audition).pack(anchor="w", pady=(5, 0))
        self.chart = None
        if self.read_sounds:
            measure = tk.Frame(compare, bg=PANEL)
            measure.pack(fill="x", pady=(10, 0))
            self.text(measure, "Recorded A/B", size=11, strong=True).pack(side="left")
            self.measure_button = self.button(measure, "Measure", self.measure)
            self.measure_button.pack(side="right")
            detail = self.text(compare, variable=self.measure_text, size=9)
            detail.pack(fill="x", pady=(4, 0))
            compare.bind("<Configure>", lambda event: detail.configure(wraplength=max(120, event.width - 21)))
            self.chart = tk.Canvas(compare, bg=WELL, height=107, width=235, highlightbackground=RULE,
                                   highlightthickness=1)
            self.chart.bind("<Configure>", lambda _: self.paint_chart())

    def sync(self, values=None, available=None):
        """Rebase after an edit elsewhere and refresh the chain's supported controls."""
        values = dict(self.read_values() if values is None else values)
        self.available = None if available is None else frozenset(available)
        if not tone_space.equivalent(values, self.values):
            self.cancel_drag()
            self.stop_sweep()
            self.anchor, self.values = dict(values), values
            self.point = (0., 0.)
        plane = tone_space.PLANES[self.plane.get()]
        self.description.set(plane.description)
        names = dict.fromkeys((*tone_space.moves(plane.horizontal, self.available),
                              *tone_space.moves(plane.vertical, self.available)))
        controls = " · ".join(styles.KNOBS[name][0] for name in names)
        caption = controls or "This chain has no controls for this field"
        self.controls_text.set(f"{caption}   |   Home returns to center")
        self.paint()
        self.mark_buttons()

    def recenter(self):
        self.cancel_drag()
        self.stop_sweep()
        self.anchor = dict(self.read_values())
        self.values = dict(self.anchor)
        self.point = (0., 0.)
        self.sync(self.values, self.available)

    def rect(self):
        width = max(240, self.canvas.winfo_width())
        height = max(140, self.canvas.winfo_height())
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
        for i in (-.75, -.5, -.25, .25, .5, .75):
            x, y = self.coords(i, i)
            canvas.create_line(x, top, x, bottom, fill="#292c38")
            canvas.create_line(left, y, right, y, fill="#292c38")
        plane = tone_space.PLANES[self.plane.get()]
        x_color = MUTED if tone_space.moves(plane.horizontal, self.available) else "#656876"
        y_color = MUTED if tone_space.moves(plane.vertical, self.available) else "#656876"
        canvas.create_line(left, cy, right, cy, fill=x_color, dash=(2, 3))
        canvas.create_line(cx, top, cx, bottom, fill=y_color, dash=(2, 3))
        font = (FONT, 10, "bold")
        canvas.create_text(left, bottom + 18, text=plane.horizontal.low, anchor="w", fill=x_color, font=font)
        canvas.create_text(right, bottom + 18, text=plane.horizontal.high, anchor="e", fill=x_color, font=font)
        canvas.create_text(cx, top - 13, text=plane.vertical.high, fill=y_color, font=font)
        canvas.create_text(cx, bottom + 18, text=plane.vertical.low, fill=y_color, font=font)
        for slot, mark in self.marks.items():
            if mark.plane == self.plane.get() and tone_space.equivalent(mark.anchor, self.anchor):
                x, y = self.coords(*mark.point)
                canvas.create_oval(x - 10, y - 10, x + 10, y + 10, outline=BLUE, width=2)
                canvas.create_text(x, y, text=slot, fill=BLUE, font=font)
        x, y = self.coords(*self.point)
        canvas.create_line(cx, cy, x, y, fill=ACCENT, width=2)
        canvas.create_oval(x - 8, y - 8, x + 8, y + 8, fill=ACCENT, outline=WELL, width=2)

    def normalize_point(self, x, y):
        plane = tone_space.PLANES[self.plane.get()]
        return (tone_space.bounded(x) if tone_space.moves(plane.horizontal, self.available) else 0.,
                tone_space.bounded(y) if tone_space.moves(plane.vertical, self.available) else 0.)

    def set_point(self, x, y, *, exact=False):
        self.cancel_drag()
        self.stop_sweep()
        self.point = self.normalize_point(x, y)
        self.apply_point(exact=exact)
        return "break"

    def push(self, event):
        self.canvas.focus_set()
        return self.set_point(*self.unproject(event))

    def drag(self, event):
        self.point = self.normalize_point(*self.unproject(event))
        self.paint()
        if self.drag_job is None:
            self.drag_job = self.after(110, self.apply_point)

    def cancel_drag(self):
        if self.drag_job is not None:
            self.after_cancel(self.drag_job)
            self.drag_job = None

    def finish_drag(self, _):
        self.cancel_drag()
        self.apply_point()

    def apply_point(self, *, exact=False):
        self.drag_job = None
        plane = tone_space.PLANES[self.plane.get()]
        self.publish(tone_space.position_values(self.anchor, plane, *self.point, self.available), exact=exact)
        self.paint()

    def publish(self, values, *, exact=False):
        changed = self.values != values if exact else not tone_space.equivalent(self.values, values)
        if changed:
            self.values = dict(values)
            self.apply_values(dict(values))

    def mark(self, slot):
        self.marks[slot] = Mark(self.plane.get(), self.point, dict(self.anchor), dict(self.read_values()))
        self.paint()
        self.mark_buttons()

    def recall(self, slot):
        mark = self.marks[slot]
        self.cancel_drag()
        self.stop_sweep()
        self.plane.set(mark.plane)
        self.anchor, self.point = dict(mark.anchor), mark.point
        self.publish(mark.values, exact=True)
        self.sync(self.values, self.available)

    def mark_buttons(self):
        for slot, control in self.recall_buttons.items():
            control.configure(state="normal" if slot in self.marks else "disabled")
        self.travel.configure(state="normal" if len(self.marks) == 2 else "disabled")

    def toggle_sweep(self):
        if self.sweep_job is not None:
            self.stop_sweep()
            return
        if len(self.marks) != 2:
            return
        self.recall("A")
        self.travel.configure(text="Stop sweep")
        self.sweep_job = self.after(120, lambda: self.sweep_step(1))

    def stop_sweep(self):
        if self.sweep_job is not None:
            self.after_cancel(self.sweep_job)
            self.sweep_job = None
        self.travel.configure(text="Sweep A → B")

    def sweep_step(self, index):
        self.sweep_job = None
        if index >= 16:
            self.recall("B")
            return
        a, b = self.marks["A"], self.marks["B"]
        ratio = index / 16
        values = tone_space.interpolate(a.values, b.values, ratio)
        if a.plane == b.plane and tone_space.equivalent(a.anchor, b.anchor):
            self.point = tuple(left + (right - left) * ratio for left, right in zip(a.point, b.point))
        else:
            self.anchor, self.point = values, (0., 0.)
        self.publish(values)
        self.paint()
        self.sweep_job = self.after(120, lambda: self.sweep_step(index + 1))

    def measure(self):
        if self.measuring is not None:
            return
        a, b = self.read_sounds()
        if not a or not b:
            self.measure_text.set("Choose two recorded sounds first.")
            return
        self.result = None
        self.chart.pack_forget()
        self.measure_text.set("Analyzing the recorded sounds…")
        self.measure_button.configure(state="disabled")
        result = self.measuring = SimpleQueue()
        self.measured_pair = (a, b)

        def work():
            try:
                measurements = tone_space.difference(listening.samples(a), listening.samples(b))
            except (OSError, ValueError, RuntimeError) as error:
                result.put((None, str(error)))
            except Exception:
                diagnostics.LOG.exception("Tone field analysis failed")
                result.put((None, "Could not analyze these recordings."))
            else:
                note = "Same performance" if a.paired_with(b) else "Differences include the playing"
                result.put((measurements, note))

        Thread(target=work, daemon=True, name="tone-field-analysis").start()
        self.measure_job = self.after(80, self.poll_measurement)

    def poll_measurement(self):
        try:
            self.result, note = self.measuring.get_nowait()
        except Empty:
            self.measure_job = self.after(80, self.poll_measurement)
            return
        self.measure_job = self.measuring = None
        self.measure_button.configure(state="normal")
        if self.read_sounds() != self.measured_pair:
            self.result = None
            self.measure_text.set("A/B changed. Measure the current pair.")
            return
        comparison = ""
        if self.result:
            a, b = self.measured_pair
            comparison = "\n" + "  ·  ".join(f"{name} {value:+.1f} {unit}"
                                               for name, value, unit in self.result.perceptual_delta)
            note = f"{a.name} → {b.name}\n{note} · B relative to A"
            self.chart.pack(fill="x", pady=(4, 0))
        self.measure_text.set(note + comparison)
        self.paint_chart()

    def paint_chart(self):
        chart = self.chart
        chart.delete("all")
        width = max(180, chart.winfo_width())
        height = max(80, chart.winfo_height())
        center = (height - 18) / 2
        chart.create_line(4, center, width - 4, center, fill=RULE)
        if not self.result:
            return
        values = self.result.spectrum_db
        scale = max(6., max(abs(value) for value in values))
        step = (width - 10) / len(values)
        for index, (value, label) in enumerate(zip(values, tone_space.BAND_NAMES)):
            x = 5 + step * (index + .5)
            y = center - value / scale * (center - 5)
            color = ACCENT if value > 0 else BLUE
            chart.create_rectangle(x - step * .28, center, x + step * .28, y, fill=color, outline="")
            chart.create_text(x, height - 8, text=label, font=(FONT, 8), fill=MUTED)
        chart.create_text(width - 4, 3, anchor="ne", text=f"±{scale:.0f} dB", font=(FONT, 8), fill=MUTED)

    def unmap(self, event):
        if event.widget == self:
            self.cancel_drag()
            self.stop_sweep()

    def destroy_jobs(self, event):
        if event.widget == self:
            for job in (self.drag_job, self.sweep_job, self.measure_job):
                if job is not None:
                    self.after_cancel(job)
