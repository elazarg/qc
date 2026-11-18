"""Choose a saved preset for the Quad Cortex or for Tone Match."""

import tkinter as tk

from . import device, preset_library
from .widgets import ACCENT, FONT, INK, LINE, MUTED, PANEL, RECESS, TEXT, add_scrollbar, button, card, entry, label


class PresetLibraryPanel:
    def __init__(self, review, parent):
        self.review = review
        self.rows = None
        self.visible = []
        self.requested = None
        self.query = tk.StringVar()
        self.scene = tk.StringVar(value="Default scene")
        self.status = tk.StringVar()
        self.hint = tk.StringVar()

        panel = card(parent)
        panel.pack(fill="both", expand=True)
        heading = tk.Frame(panel, bg=PANEL)
        heading.pack(fill="x", pady=(0, 10))
        label(heading, "Saved QC presets", size=16, strong=True).pack(side="left")
        self.refresh_button = button(heading, "Refresh", self.refresh, compact=True)
        self.refresh_button.pack(side="right")
        query = entry(panel, self.query)
        query.pack(fill="x", ipady=5)
        label(panel, "Filter by preset, setlist, or slot", color=MUTED, size=9).pack(anchor="w", pady=(3, 8))

        results = tk.Frame(panel, bg=PANEL)
        results.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(results, height=8, font=(FONT, 11), relief="flat", background=RECESS,
                                  foreground=TEXT, selectbackground=ACCENT, selectforeground=INK,
                                  highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                                  activestyle="none", exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)
        add_scrollbar(results, self.listbox)
        self.listbox.bind("<<ListboxSelect>>", lambda _: self.update_buttons())
        query.bind("<Down>", lambda _: self.focus_results())

        actions = tk.Frame(panel, bg=PANEL)
        actions.pack(fill="x", pady=(10, 0))
        self.load_button = button(actions, "Load on QC", self.load, compact=True)
        self.load_button.pack(side="left", padx=(0, 7))
        self.reference_button = button(actions, "Use as reference", self.reference, accent=True, compact=True)
        self.reference_button.pack(side="left", padx=(0, 7))
        self.scene_menu = tk.Menubutton(actions, textvariable=self.scene, indicatoron=True, takefocus=True,
                                         font=(FONT, 10), background=PANEL, foreground=TEXT,
                                         activebackground=LINE, activeforeground=TEXT, padx=6, pady=6)
        scene_menu = tk.Menu(self.scene_menu, tearoff=False, font=(FONT, 10))
        self.scene_menu.configure(menu=scene_menu)
        for option in ("Default scene", *(f"Scene {value}" for value in device.SCENES)):
            scene_menu.add_radiobutton(label=option, value=option, variable=self.scene)
        self.scene_menu.pack(side="left")
        label(panel, variable=self.hint, color=MUTED, wrap=480, size=9).pack(anchor="w", pady=(6, 0))
        label(panel, variable=self.status, color=TEXT, wrap=480).pack(anchor="w", pady=(5, 0))

        self.query.trace_add("write", lambda *_: self.filter())
        self.update_buttons()

    def show(self):
        if self.rows is None and not self.review.busy():
            self.refresh()
        else:
            self.update_buttons()

    def find_named(self, query, action):
        self.query.set(query)
        self.requested = (query, action) if action else None
        self.review.show_view("presets")
        if self.rows is not None:
            self.follow_request()
        elif self.requested and self.review.live_active():
            self.refresh()

    def follow_request(self):
        if self.requested is None:
            return
        query, action = self.requested
        self.requested = None
        if query != self.query.get():
            return
        pick = [(i, row) for i, row in enumerate(self.visible) if row.name.casefold() == query.casefold()]
        if len(pick) != 1:
            self.status.set("Choose the saved preset you meant.")
            return
        index, _ = pick[0]
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(index)
        self.listbox.see(index)
        self.update_buttons()
        control = self.reference_button if action == "reference" else self.load_button
        if control.cget("state") == "normal":
            control.invoke()

    def focus_results(self):
        if self.visible and not self.listbox.curselection():
            self.listbox.selection_set(0)
            self.update_buttons()
        self.listbox.focus_set()
        return "break"

    def filter(self):
        selected = self.selected()
        search = self.query.get().casefold().split()
        self.visible = [row for row in self.rows or [] if
                        all(term in f"{row.name} {row.setlist} {row.slot}".casefold() for term in search)]
        self.listbox.delete(0, tk.END)
        for row in self.visible:
            self.listbox.insert(tk.END, f"{row.name}  ·  {row.setlist} {row.slot}")
        if selected in self.visible:
            index = self.visible.index(selected)
            self.listbox.selection_set(index)
            self.listbox.see(index)
        self.update_buttons()

    def selected(self):
        selection = self.listbox.curselection()
        return self.visible[selection[0]] if selection else None

    def update_buttons(self):
        live = self.review.live_active()
        busy = self.review.busy() and (not live or self.review.live_panel.stop_signal.is_set())
        selected = self.selected() is not None
        take_available = bool(self.review.take and self.review.take_info.get("dry") and self.review.take_info.get("wet"))
        self.refresh_button.configure(text="Stop and refresh" if live else "Refresh",
                                      state="disabled" if busy else "normal")
        self.load_button.configure(state="normal" if selected and not busy else "disabled")
        reference_available = selected and take_available and not busy
        for control in (self.reference_button, self.scene_menu):
            control.configure(state="normal" if reference_available else "disabled")
        if live:
            hint = "Loading or capturing first stops live control and restores its starting preset."
        elif self.rows is None:
            hint = "Refresh to read your saved setlists from the Quad Cortex."
        elif not take_available:
            hint = "Record a clip to use a saved preset as a tone reference."
        else:
            hint = "A reference uses this take's dry guitar and restores the preset and scene afterward."
        self.hint.set(hint)

    def run(self, work, status, on_success, *args, **kwargs):
        if self.review.live_active():
            if self.review.live_panel.stop_for(lambda: self.run(work, status, on_success, *args, **kwargs)):
                self.status.set("Stopping live control…")
            return
        if self.review.render(work, *args, status=status, on_success=on_success,
                              on_error=lambda error: self.status.set(str(error)), **kwargs):
            self.status.set(status)

    def refresh(self):
        self.run(preset_library.inventory, "Reading saved Quad Cortex presets…", self.show_inventory)

    def show_inventory(self, rows):
        self.rows = rows
        self.filter()
        self.status.set("" if rows else "No saved presets found in user setlists.")
        self.follow_request()

    def load(self):
        choice = self.selected()
        if choice:
            self.run(preset_library.load_preset, f"Loading {choice.name}…", self.loaded, choice)

    def loaded(self, choice):
        status = f"Loaded on QC · {choice.name} · {choice.setlist} {choice.slot}"
        self.status.set(status)
        self.review.recorder.status.set(status)

    def reference(self):
        choice, take = self.selected(), self.review.take
        if not choice or not take:
            return
        scene = self.scene.get()
        number = None if scene == "Default scene" else device.SCENES.index(scene[-1])
        self.run(preset_library.render_reference, f"Capturing {choice.name} over USB…", self.reference_ready,
                 choice, take, scene=number)

    def reference_ready(self, reference):
        display = f"{reference.name} · Scene {reference.scene}"
        status = f"Reference ready · {display}"
        self.status.set(status)
        self.review.recorder.status.set(status)
        self.review.match.use_preset_reference(reference.path, display)
        if self.review.active_view == "presets":
            self.review.show_view("match")
        if self.review.active_view == "match":
            self.review.match.find()
