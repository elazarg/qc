"""Record or select a reusable idle reference in the Guitar Learner."""

import tkinter as tk

from lab.learner import dataset, idle
from lab.learner.board import Dial, GuitarBoard, PANEL
from lab.tools.common import Chips, button, listbox, room, said, section, send, sized
from lab.desk.theme import PAPER


class IdleReference:
    def __init__(self, seat, parent, current):
        self.seat = seat
        self.current = current
        self.frame = tk.Frame(parent, bg=PAPER)
        self.header = None
        self.guitar = None
        self.pending = False
        self.pickup = tk.StringVar(master=self.frame)

    def shown(self):
        if self.guitar == self.seat.guitar:
            return
        self.guitar = self.seat.guitar
        for widget in self.frame.winfo_children():
            widget.destroy()
        layouts = dataset.layouts_for(self.guitar)
        if not layouts:
            return
        studio = self.seat.studio
        picked = (idle.selected(studio.folder, self.guitar) or self.current(self.guitar)
                  or idle.for_sound(studio.folder, self.guitar, studio.sound(studio.state.side)))
        self.layout = picked["layout"] if picked else layouts[0]
        self.board = None
        workspace = tk.Frame(self.frame, bg=PAPER)
        workspace.pack(fill="both", expand=True)
        panel = tk.Frame(workspace, bg=PANEL, padx=14, pady=12, width=350)
        panel.pack(side="right", fill="y")
        panel.pack_propagate(False)
        card = section(panel, f"{self.guitar} idle reference",
                       "Match the controls to the guitar and mute the strings while recording. Tone Lab reuses "
                       "the dry clip for any preset you check on that setup. Input gain, the cable and the room "
                       "can change the noise. Record again if that changes.")
        if self.layout.startswith("dk24"):
            self.board = GuitarBoard(workspace, self.change_layout, lambda: None)
            self.board.pack(side="left", fill="both", expand=True, padx=(0, 12))
            self.board.show(self.layout, picked["values"] if picked else None)
        else:
            self.controls = tk.Frame(workspace, bg=PANEL, padx=14, pady=12)
            self.controls.pack(side="left", fill="both", expand=True, padx=(0, 12))
            self.controls_for(picked["values"] if picked else {})
        actions = tk.Frame(card, bg=card["bg"])
        actions.pack(fill="x", pady=(10, 0))
        button(actions, "Use setup", self.select).pack(side="left")
        self.record_button = button(actions, "Record muted strings", self.record)
        self.record_button.pack(side="left", padx=(6, 0))
        self.line = said(card, pady=(6, 0))
        self.saved_area = room(panel)
        saved = section(self.saved_area, "Recorded setups")
        self.saved = listbox(saved, height=4)
        self.saved.bind("<<ListboxSelect>>", self.load_recorded)
        self.refresh_recorded()
        chosen = idle.selected(studio.folder, self.guitar)
        self.line.configure(text=f"In use · {dataset.suggested_setup(chosen, self.guitar)}" if chosen else "")

    def hidden(self):
        pass

    def change_layout(self, layout):
        if not self.pending and layout != self.layout:
            self.layout = layout
            self.board.show(layout)

    def controls_for(self, values):
        for widget in self.controls.winfo_children():
            widget.destroy()
        layout = dataset.LAYOUTS[self.layout]
        pickups = layout["pickups"]
        self.pickup.set(pickups[values.get("pickup", len(pickups)) - 1])
        Chips(self.controls, self.pickup, pickups).pack(anchor="w")
        self.knobs = {}
        row = tk.Frame(self.controls, bg=self.controls["bg"])
        row.pack(anchor="w", pady=(6, 0))
        for key in layout["knobs"]:
            dial = self.knobs[key] = Dial(row, key.replace("_", " "), lambda value, key=key: self.turn(key, value))
            dial.paint(values.get(key, 10.))
            dial.pack(side="left", padx=(0, 6))

    def turn(self, key, value):
        self.knobs[key].paint(max(0., min(10., round(value * 2) / 2)))

    def refresh_recorded(self):
        self.known = idle.recorded(self.seat.studio.folder, self.guitar)
        self.saved.delete(0, "end")
        for item in self.known:
            self.saved.insert("end", dataset.suggested_setup(item, self.guitar))
        if self.known:
            sized(self.saved, 5)
            self.saved.pack(fill="x")
        self.saved_area.show(bool(self.known))

    def load_recorded(self, _):
        position = self.saved.curselection()
        if not position or self.pending:
            return
        item = self.known[position[0]]
        self.layout = item["layout"]
        if self.board:
            self.board.show(self.layout, item["values"])
        else:
            for key, dial in self.knobs.items():
                dial.paint(item["values"][key])
            self.pickup.set(dataset.LAYOUTS[self.layout]["pickups"][item["values"]["pickup"] - 1])

    def read_setup(self):
        try:
            if self.board:
                values = self.board.values
            else:
                values = {key: dial.value for key, dial in self.knobs.items()}
                values["pickup"] = dataset.LAYOUTS[self.layout]["pickups"].index(self.pickup.get()) + 1
            return idle.setup(self.guitar, self.layout, values)
        except ValueError as error:
            self.line.configure(text=f"Check the setup. {error or 'Use numbers from 0 to 10.'}")
            return None

    def select(self):
        if self.pending or not (item := self.read_setup()):
            return
        studio = self.seat.studio
        try:
            idle.choose(studio.folder, item)
        except OSError as error:
            self.line.configure(text=str(error))
            return

        def found(result):
            if self.guitar == item["instrument"] and item == idle.selected(studio.folder, self.guitar):
                label = dataset.suggested_setup(item, self.guitar)
                self.line.configure(text=f"In use · {label}\n" + (f"Using {result[1]}." if result else
                                    "No idle reference for this setup. Record with the strings muted."))

        studio.later(studio.reading.submit(idle.reference, studio.folder, item), found)

    def record(self):
        if self.pending or not (item := self.read_setup()):
            return
        studio = self.seat.studio

        def finished(audio):
            self.pending = False
            self.record_button.configure(state="normal")
            if studio.state.instrument != item["instrument"]:
                self.line.configure(text="The guitar changed during recording. Try again on this guitar.")
                return
            try:
                idle.keep(studio.folder, item, audio)
            except (OSError, ValueError) as error:
                failed(error)
                return
            if self.guitar == item["instrument"]:
                self.refresh_recorded()
                self.line.configure(text=f"Idle reference saved · {dataset.suggested_setup(item, self.guitar)}")

        def failed(error):
            self.pending = False
            self.record_button.configure(state="normal")
            self.line.configure(text=str(error))

        if send(studio, "record", 3., done=finished, failed=failed, working="Recording muted strings…", renders=True):
            self.pending = True
            self.record_button.configure(state="disabled")
            self.line.configure(text="Keep the strings muted and leave the guitar controls in place.")
        else:
            self.line.configure(text=f"{studio.held_words()} Stop it there first.")
