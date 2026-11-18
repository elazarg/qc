"""Saved-tone search, reference matching, and optional rig research."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import filedialog
from urllib.parse import urlsplit
import webbrowser

from . import advisor, diagnostics
from .widgets import FONT, LINE, MUTED, PANEL, RECESS, TEXT, add_scrollbar, button, card, entry, label, slider


class LibraryPanel:
    def __init__(self, review, parent, worker: ThreadPoolExecutor, *, matching=False):
        self.review, self.parent, self.worker = review, parent, worker
        self.matching = matching
        self.future = None
        self.reference = None
        self.status = tk.StringVar()
        self.query = tk.StringVar()
        self.same_take = tk.BooleanVar()
        self.what = tk.StringVar(value="Choose a WAV or use the wet recording for this take.")

        controls = card(parent)
        controls.pack(fill="x", pady=(0, 11))
        title = "Match a reference" if matching else "Find a tone"
        label(controls, title, size=16, strong=True).pack(anchor="w")
        if matching:
            self.build_match(controls)
        else:
            self.build_search(controls)
        options = tk.Frame(controls, bg=PANEL)
        options.pack(fill="x", pady=(7, 0))
        self.only_this = tk.Checkbutton(
            options, text="This take only", variable=self.same_take, font=(FONT, 10),
            background=PANEL, foreground=MUTED, activebackground=PANEL, activeforeground=TEXT,
            selectcolor=RECESS, relief="flat", highlightthickness=0, takefocus=True,
        )
        self.only_this.pack(side="left")
        label(controls, variable=self.status, color=MUTED, wrap=520).pack(anchor="w", pady=(4, 0))

        results = card(parent, padding=12)
        results.pack(fill="both", expand=True)
        self.result_heading = tk.StringVar(value="Saved tones")
        label(results, variable=self.result_heading, size=13, strong=True).pack(anchor="w", pady=(0, 6))
        result_bounds = tk.Frame(results, background=PANEL)
        result_bounds.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(result_bounds, background=PANEL, height=180, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        add_scrollbar(result_bounds, self.canvas)
        self.body = tk.Frame(self.canvas, background=PANEL)
        self.body_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.body_id, width=event.width))
        self.clear("Rank saved reamps against your reference." if matching else
                   "Search the tones you have recorded or reamped.")

    def build_search(self, panel):
        label(panel, "Describe a sound, name an artist, or browse saved takes.",
              color=MUTED).pack(anchor="w", pady=(4, 7))
        row = tk.Frame(panel, bg=PANEL)
        row.pack(fill="x")
        field = entry(row, self.query)
        field.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 7))
        field.bind("<Return>", lambda _: self.find())
        self.action = button(row, "Search", self.find, accent=True, compact=True)
        self.action.pack(side="left")
        self.research_button = button(row, "Research rig", self.research, compact=True)
        self.research_button.pack(side="left", padx=(6, 0))
        self.secondary_actions = [self.research_button]

    def build_match(self, panel):
        label(panel, variable=self.what, color=MUTED, wrap=520).pack(anchor="w", pady=(4, 7))
        row = tk.Frame(panel, bg=PANEL)
        row.pack(fill="x")
        choose = button(row, "Choose WAV", self.choose, compact=True)
        choose.pack(side="left", padx=(0, 5))
        use_take = button(row, "Use take's wet audio", self.use_take, compact=True)
        use_take.pack(side="left", padx=(0, 5))
        self.action = button(row, "Find closest tones", self.find, accent=True, compact=True)
        self.action.pack(side="left")
        self.secondary_actions = [choose, use_take]
        priorities = tk.Frame(panel, background=PANEL)
        priorities.pack(fill="x", pady=(5, 0))
        self.weights = {}
        for i, (key, title) in enumerate((("tone", "Tone"), ("dynamics", "Dynamics"), ("space", "Ambience"))):
            priorities.columnconfigure(i, weight=1, uniform="weights")
            cell = tk.Frame(priorities, bg=PANEL)
            cell.grid(row=0, column=i, sticky="ew", padx=(0, 8))
            self.weights[key] = tk.DoubleVar(value=1)
            weight = self.weights[key]
            caption = tk.StringVar(value=f"{title} · 100%")
            label(cell, variable=caption, color=MUTED, size=9).pack(anchor="w")
            weight.trace_add("write", lambda *_, name=title, value=weight, text=caption:
                             text.set(f"{name} · {value.get():.0%}"))
            slider(cell, weight).pack(fill="x")

    def choose(self):
        choice = filedialog.askopenfilename(parent=self.review.root, title="Choose a guitar reference",
                                            filetypes=(("WAV audio", "*.wav"),))
        if choice:
            self.reference = Path(choice)
            self.what.set(self.reference.name)

    def use_take(self):
        source = self.review.take_info.get("wet")
        if not source:
            self.status.set("This take does not have a wet recording.")
            return
        self.reference = Path(source)
        self.what.set("Wet recording from this take")

    def clear(self, message=None):
        for child in self.body.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)
        if message:
            label(self.body, message, color=MUTED, wrap=520).pack(anchor="w", padx=4, pady=8)
        self.canvas.update_idletasks()

    def bind_wheel(self, widget):
        widget.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units"))
        for child in widget.winfo_children():
            self.bind_wheel(child)

    def find(self):
        from . import tone_search

        if self.matching and self.reference is None:
            self.status.set("Choose a reference WAV first.")
            return
        if self.same_take.get() and not self.review.take:
            self.status.set("There is no current take to search.")
            return
        priorities = {key: value.get() for key, value in self.weights.items()} if self.matching else None
        if priorities is not None and not any(priorities.values()):
            self.status.set("Give at least one matching dimension some weight.")
            return
        take_id = self.review.take.name if self.review.take else None
        self.run("Analyzing saved tones…" if self.matching else "Searching saved tones…",
                 self.show_results, tone_search.search, self.query.get().strip(),
                 reference=self.reference, priorities=priorities, take_id=take_id,
                 same_take_only=self.same_take.get())

    def research(self):
        text = self.query.get().strip()
        if not text:
            self.status.set("Enter an artist, song, or rig to research.")
            return
        self.run("Researching the rig on the web…", self.show_research,
                 advisor.research, text, self.review.take_info)

    def run(self, status, on_success, work, *args, **kwargs):
        if self.future is not None:
            return
        self.status.set(status)
        self.action.configure(state="disabled")
        for control in self.secondary_actions:
            control.configure(state="disabled")
        self.future = self.worker.submit(work, *args, **kwargs)
        self.review.root.after(110, lambda: self.poll(on_success))

    def poll(self, on_success):
        if not self.future.done():
            self.review.root.after(110, lambda: self.poll(on_success))
            return
        future, self.future = self.future, None
        self.action.configure(state="normal")
        for control in self.secondary_actions:
            control.configure(state="normal")
        try:
            result = future.result()
        except Exception as error:
            diagnostics.LOG.exception("Tone library operation failed")
            self.status.set(str(error))
        else:
            self.status.set("")
            on_success(result)

    def show_research(self, result):
        reply, sources = result
        self.result_heading.set("Rig research")
        self.clear()
        label(self.body, reply, color=TEXT, wrap=525).pack(fill="x", padx=4, pady=(3, 8))
        for source in sources:
            url = source.get("url", "")
            if urlsplit(url).scheme != "https":
                continue
            control = button(self.body, source["title"][:60], lambda link=url: webbrowser.open(link), compact=True)
            control.pack(anchor="w", pady=(0, 4))
        self.bind_wheel(self.body)

    def show_results(self, results):
        self.result_heading.set("Closest saved tones" if self.matching else "Saved tones")
        self.clear()
        if not results:
            self.clear("No tones matched. Try broader wording or include other takes.")
            return
        for result in results:
            row = tk.Frame(self.body, bg=RECESS, padx=9, pady=8, highlightbackground=LINE,
                           highlightthickness=1)
            row.pack(fill="x", pady=(0, 5))
            action = button(row, "Play", lambda selected=result: self.preview(selected), compact=True)
            action.pack(side="right")
            if result.get("session") and result.get("same_take"):
                button(row, "Compare", lambda selected=result: self.review.select_comparison(selected["session"]),
                       compact=True).pack(side="right", padx=(0, 5))
            left = tk.Frame(row, bg=RECESS)
            left.pack(side="left", fill="x", expand=True)
            label(left, result["title"], size=11, strong=True, wrap=350).pack(fill="x")
            score = result.get("similarity" if self.matching else "score")
            bits = []
            if score is not None:
                bits.append(f"{score:.0f}/100 " + ("similarity" if self.matching else "relevance"))
            if result.get("same_take"):
                bits.append("This take")
            bits.extend(result.get("traits", [])[:3])
            if bits:
                label(left, " · ".join(bits), color=MUTED, wrap=350, size=9).pack(fill="x", pady=(2, 0))
        self.bind_wheel(self.body)

    def preview(self, result):
        from . import tone_search

        if self.review.busy():
            self.status.set("Stop the live session or wait for the current recording first.")
            return
        self.run("Preparing audition…", self.play_preview, tone_search.audition, result)

    def play_preview(self, path):
        try:
            self.review.play_file(path)
        except RuntimeError as error:
            self.status.set(str(error))
