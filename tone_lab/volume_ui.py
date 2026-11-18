"""Transport volume and comparison loudness controls."""

import math
import tkinter as tk


def bounded_level(value, high):
    try:
        level = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return round(max(-40, min(high, level))) if math.isfinite(level) else 0


def saved_settings(value):
    value = value if isinstance(value, dict) else {}
    master = bounded_level(value.get("master_db", 0), 0)
    match = value.get("match") is not False
    return {"master_db": master, "loudness_db": bounded_level(value.get("loudness_db", master if match else 0), 6),
            "muted": value.get("muted") is True, "match": match}


def listening_command(settings, command):
    if command in ("mute", "unmute"):
        return {**settings, "muted": command == "mute"}
    key = "loudness_db" if settings["match"] else "master_db"
    level = settings[key] + {"quieter": -3, "louder": 3, "slightly quieter": -1,
                             "slightly louder": 1, "normal": -settings[key]}[command]
    return saved_settings({**settings, key: level})


class VolumePanel:
    def __init__(self, deck, transport, settings, change):
        from .listening_room import ACCENT, ACCENT_INK, DARK, LIGHT, SOFT, TILE, control, text

        self.change = change
        self.transport = transport
        self.button = control(transport, "", self.toggle, dark=True)
        self.panel = tk.Frame(deck, bg=TILE, padx=8, pady=3)
        self.level = tk.IntVar(master=deck)
        self.mute = tk.BooleanVar(master=deck, value=settings["muted"])
        self.manual = tk.BooleanVar(master=deck, value=not settings["match"])
        self.summary = tk.StringVar(master=deck)
        self.settings = settings
        self.syncing = False

        control(self.panel, "Quieter", lambda: self.command("quieter"), dark=True).pack(side="left")
        self.slider = tk.Scale(self.panel, from_=-40, to=6, orient=tk.HORIZONTAL, length=120, resolution=1,
                               variable=self.level, showvalue=False, command=self.changed,
                               bg=TILE, fg=LIGHT, activebackground=ACCENT, troughcolor=DARK,
                               relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=TILE,
                               highlightcolor=ACCENT, takefocus=True)
        self.slider.pack(side="left")
        for caption, action in (("Louder", "louder"), ("Reset", "normal")):
            control(self.panel, caption, lambda choice=action: self.command(choice), dark=True).pack(side="left")
        for caption, value in (("Mute", self.mute), ("Manual trim", self.manual)):
            toggle = tk.Checkbutton(self.panel, text=caption, variable=value, command=self.changed,
                                    bg=TILE, fg=LIGHT, activebackground=TILE, activeforeground=ACCENT,
                                    selectcolor=DARK, relief="flat", borderwidth=0, padx=5,
                                    highlightcolor=ACCENT_INK)
            toggle.pack(side="left", padx=(7, 0))
        text(self.panel, variable=self.summary, color=SOFT, size=9).pack(side="left", fill="x", expand=True, padx=8)
        self.sync(settings, "")

    def changed(self, _=None):
        if self.syncing:
            return
        match = not self.manual.get()
        settings = {**self.settings, "muted": self.mute.get(), "match": match}
        if match == self.settings["match"]:
            settings["loudness_db" if match else "master_db"] = self.level.get()
        if settings != self.settings:
            self.change(settings)

    def command(self, action):
        self.change(listening_command(self.settings, action))

    def toggle(self):
        if self.panel.winfo_manager():
            self.panel.pack_forget()
        else:
            self.panel.pack(after=self.transport, fill="x", pady=(4, 0))

    def sync(self, settings, status):
        self.syncing = True
        self.settings = settings
        match = settings["match"]
        offset = settings["loudness_db"] if match else settings["master_db"]
        self.slider.configure(to=6 if match else 0)
        self.level.set(offset)
        self.mute.set(settings["muted"])
        self.manual.set(not match)
        self.syncing = False
        if settings["muted"]:
            level = "Muted"
        elif not match:
            level = f"{offset} dB"
        elif offset:
            level = "Quieter" if offset < 0 else "Louder"
        else:
            level = "Normal"
        self.button.configure(text=f"Volume · {level}")
        self.summary.set(status)
