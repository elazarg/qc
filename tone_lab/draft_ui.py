"""The draft and audition card shown below the tone conversation."""

from pathlib import Path
import json
import tkinter as tk
import webbrowser

from . import draft_render, feedback, preset_design, session
from .widgets import ACCENT, FONT, MUTED, PANEL, TEXT, button, card, label


class DraftCard:
    def __init__(self, panel, parent):
        self.panel, self.review = panel, panel.review
        self.versions = preset_design.history()
        self.draft = None
        self.take = None
        self.proof = None
        self.saved = {}
        for draft in self.versions:
            try:
                receipt = session.SESSIONS / "drafts" / f"{draft['id']}.kept.json"
                self.saved[draft["id"]] = json.loads(receipt.read_text(encoding="utf-8"))["label"]
            except (OSError, ValueError, KeyError):
                pass
        self.name, self.target, self.chain, self.note = (tk.StringVar() for _ in range(4))
        self.box = card(parent, padding=12)
        heading = tk.Frame(self.box, bg=PANEL)
        heading.pack(fill="x")
        label(heading, variable=self.name, size=14, strong=True).pack(side="left")
        self.older = button(heading, "Older", lambda: self.move(1), compact=True)
        self.newer = button(heading, "Newer", lambda: self.move(-1), compact=True)
        target_line = label(self.box, variable=self.target, color=ACCENT, wrap=505)
        target_line.pack(anchor="w", pady=(3, 0))
        chain_line = label(self.box, variable=self.chain, color=MUTED, wrap=505)
        chain_line.pack(anchor="w", pady=(3, 0))
        controls = self.controls = tk.Frame(self.box, bg=PANEL)
        controls.pack(fill="x", pady=(8, 0))
        self.hear = button(controls, "Render A/B", self.render, accent=True, compact=True)
        self.baseline = button(controls, "Original", lambda: self.play("original"), compact=True)
        self.listen = button(controls, "Play draft", lambda: self.play("draft"), accent=True, compact=True)
        self.try_live = button(controls, "Try live", self.try_on_guitar, compact=True)
        self.try_live.pack(side="left", padx=(0, 5))
        self.save = button(controls, "Save on QC", self.keep, compact=True)
        self.save.pack(side="left")
        self.sources = tk.Menubutton(controls, text="Details", indicatoron=True, takefocus=True,
                                     font=(FONT, 10), background=PANEL, foreground=TEXT, padx=5)
        self.source_menu = tk.Menu(self.sources, tearoff=False, font=(FONT, 10))
        self.sources.configure(menu=self.source_menu)
        self.sources.pack(side="right")
        note_line = label(self.box, variable=self.note, color=MUTED, wrap=505, size=9)
        note_line.pack(anchor="w", pady=(6, 0))
        self.explanation = label(self.box, color=MUTED, wrap=505)
        self.box.bind("<Configure>", lambda event: self.wrap(event.width - 27,
                                                            target_line, chain_line, note_line, self.explanation))

    @staticmethod
    def wrap(width, *lines):
        if width > 60:
            for line in lines:
                line.configure(wraplength=width)

    def show(self, draft, *, add=False):
        if add:
            self.versions.insert(0, draft)
        self.draft = draft
        if not self.box.winfo_manager():
            self.box.pack(fill="x", pady=(0, 12), before=self.panel.controls)
        self.name.set(draft["name"])
        self.target.set(f"{draft['guitar']} · {draft['target']}")
        self.chain.set(preset_design.chain_text(draft))
        self.explanation.configure(text=draft["explanation"])
        self.explanation.pack_forget()
        self.source_menu.delete(0, tk.END)
        self.source_menu.add_command(label="Why this approach?", command=self.toggle_explanation)
        for row in draft["sources"]:
            self.source_menu.add_command(label=row["title"][:90],
                                         command=lambda url=row["url"]: webbrowser.open(url))
        if len(self.versions) > 1 and not self.newer.winfo_manager():
            self.newer.pack(side="right")
            self.older.pack(side="right", padx=(0, 5))
        self.take = None
        self.update()

    def toggle_explanation(self):
        if self.explanation.winfo_manager():
            self.explanation.pack_forget()
        else:
            self.explanation.pack(anchor="w", pady=(6, 0))

    def move(self, direction):
        draft = self.versions[self.index() + direction]
        self.show(draft)
        self.panel.last_reply = draft["explanation"]
        self.panel.reply.set("")
        self.panel.target_draft = True
        self.panel.designing = True
        self.panel.update_buttons()

    def index(self):
        return next((i for i, value in enumerate(self.versions) if value["id"] == self.draft["id"]), 0)

    def update(self):
        if not self.draft:
            return
        if self.review.take != self.take:
            self.take = self.review.take
            self.proof = next(
                ((path, info) for path, info in feedback.comparisons(self.take.name)
                 if info.get("draft_id") == self.draft["id"]), None) if self.take else None
        busy = self.review.busy()
        take_ready = bool(self.take and self.review.take_info.get("dry") and self.review.take_info.get("wet"))
        usable = bool(self.proof and not busy)
        saved = self.saved.get(self.draft["id"])
        self.hear.configure(state="normal" if take_ready and not busy and not self.proof else "disabled")
        self.baseline.configure(state="normal" if usable and self.path("original") else "disabled")
        self.listen.configure(state="normal" if usable and self.path("draft") else "disabled")
        if self.proof:
            self.hear.pack_forget()
            order = (self.listen, self.baseline)
        else:
            self.listen.pack_forget()
            self.baseline.pack_forget()
            order = (self.hear,)
        for control in order:
            if not control.winfo_manager():
                control.pack(side="left", padx=(0, 5), before=self.try_live)
        self.save.configure(state="normal" if not busy and not saved else "disabled")
        self.try_live.configure(state="disabled" if busy else "normal")
        locked = busy or self.panel.ai_future is not None
        self.older.configure(state="normal" if not locked and self.index() + 1 < len(self.versions) else "disabled")
        self.newer.configure(state="normal" if not locked and self.index() else "disabled")
        if busy:
            return
        if saved:
            message = f"Saved · {saved}"
            if self.proof and (guitar := self.proof[1].get("guitar")):
                message += f" · Audition from {guitar}"
        elif self.proof:
            guitar = self.proof[1].get("guitar")
            message = f"Level-matched on the same {guitar + ' ' if guitar else ''}take."
            if guitar and guitar != self.draft["guitar"]:
                message += f" Record a {self.draft['guitar']} take to hear it with that guitar."
        elif take_ready:
            message = "Render the current dry take through this draft, then compare it with the original."
        else:
            message = "Record a clip to audition this draft."
        self.note.set(message)

    def render(self):
        if self.review.render(draft_render.hear, self.draft, self.take,
                              status="Rendering the draft on your Quad Cortex…",
                              on_success=self.rendered, on_error=self.error):
            self.note.set("Rendering the draft on your Quad Cortex…")

    def rendered(self, folder):
        self.proof = Path(folder), session.load_session(folder)
        self.take = self.review.take
        self.update()

    def path(self, role):
        if not self.proof:
            return None
        path, manifest = self.proof
        candidate = next((row["file"] for row in manifest["candidates"] if row.get("audition_role") == role), None)
        sound = next((row["file"] for row in manifest["auditions"].values() if row["render"] == candidate), None)
        return path / sound if sound else None

    def play(self, role):
        if path := self.path(role):
            try:
                self.review.mark_audition(path)
                self.review.play_file(path)
            except RuntimeError as error:
                self.error(error)

    def try_on_guitar(self):
        gain = self.proof[1].get("output_gain_db") if self.proof else None
        self.panel.start_draft(self.draft, gain)

    def keep(self):
        proof = self.proof[0] if self.proof else None
        if self.review.render(draft_render.save, self.draft, proof, physical_input=self.review.recorder.input_channel,
                              status="Saving in an empty My Presets slot…",
                              on_success=self.kept, on_error=self.error):
            self.note.set("Saving in an empty My Presets slot…")

    def kept(self, address):
        self.saved[self.draft["id"]] = address
        self.review.presets.rows = None
        self.update()

    def error(self, error):
        self.panel.reply.set(str(error))
