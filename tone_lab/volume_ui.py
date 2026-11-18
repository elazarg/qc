"""Transport volume and comparison loudness controls."""

import math
import tkinter as tk


def saved_settings(value):
    value = value if isinstance(value, dict) else {}
    try:
        level = float(value.get("master_db", 0))
    except (TypeError, ValueError):
        level = 0
    return {"master_db": round(max(-40, min(0, level))) if math.isfinite(level) else 0,
            "muted": value.get("muted") is True, "match": value.get("match") is not False}


class VolumePanel:
    def __init__(self, deck, transport, settings, change):
        from .listening_room import ACCENT, ACCENT_INK, DARK, LIGHT, SOFT, TILE, control, text

        self.change = change
        self.transport = transport
        self.button = control(transport, "", self.toggle, dark=True)
        self.panel = tk.Frame(deck, bg=TILE, padx=8, pady=3)
        self.master = tk.IntVar(master=deck, value=settings["master_db"])
        self.mute = tk.BooleanVar(master=deck, value=settings["muted"])
        self.match = tk.BooleanVar(master=deck, value=settings["match"])
        self.summary = tk.StringVar(master=deck)

        slider = tk.Scale(self.panel, from_=-40, to=0, orient=tk.HORIZONTAL, length=150, resolution=1,
                          variable=self.master, showvalue=False, command=lambda _: self.changed(),
                          bg=TILE, fg=LIGHT, activebackground=ACCENT, troughcolor=DARK,
                          relief="flat", borderwidth=0, highlightthickness=0, takefocus=True)
        slider.pack(side="left")
        for caption, value in (("Mute", self.mute), ("Match loudness", self.match)):
            toggle = tk.Checkbutton(self.panel, text=caption, variable=value, command=self.changed,
                                    bg=TILE, fg=LIGHT, activebackground=TILE, activeforeground=ACCENT,
                                    selectcolor=DARK, relief="flat", borderwidth=0, padx=5,
                                    highlightcolor=ACCENT_INK)
            toggle.pack(side="left", padx=(7, 0))
        text(self.panel, variable=self.summary, color=SOFT, size=9).pack(side="left", fill="x", expand=True, padx=8)
        self.sync(settings, "Applies while Tone Lab is playing.")

    def changed(self):
        self.change({"master_db": self.master.get(), "muted": self.mute.get(), "match": self.match.get()})

    def toggle(self):
        if self.panel.winfo_manager():
            self.panel.pack_forget()
        else:
            self.panel.pack(after=self.transport, fill="x", pady=(4, 0))

    def sync(self, settings, status):
        level = "muted" if settings["muted"] else f"{settings['master_db']} dB"
        self.button.configure(text=f"Volume · {level}")
        self.summary.set(status)
