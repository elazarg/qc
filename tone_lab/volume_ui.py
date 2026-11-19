"""Listening level and comparison loudness controls."""

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
    """The Levels card. It stays closed unless the player opens it or a level needs attention."""

    def __init__(self, parent, settings, change):
        from .listening_room import ACCENT, ACCENT_INK, DARK, LIGHT, SOFT, TILE, control, text

        self.change = change
        self.panel = tk.Frame(parent, bg=parent["bg"])
        self.level = tk.IntVar(master=parent)
        self.mute = tk.BooleanVar(master=parent, value=settings["muted"])
        self.manual = tk.BooleanVar(master=parent, value=not settings["match"])
        self.summary = tk.StringVar(master=parent)
        self.reading = tk.StringVar(master=parent)
        self.settings = settings
        self.syncing = False
        self.label = ""

        row = tk.Frame(self.panel, bg=self.panel["bg"])
        row.pack(fill="x")
        control(row, "Quieter", lambda: self.command("quieter"), dark=True).pack(side="left")
        self.slider = tk.Scale(row, from_=-40, to=6, orient=tk.HORIZONTAL, length=120, resolution=1,
                               variable=self.level, showvalue=False, command=self.changed,
                               bg=self.panel["bg"], fg=LIGHT, activebackground=ACCENT, troughcolor="#14151a",
                               relief="flat", borderwidth=0, highlightthickness=1,
                               highlightbackground=self.panel["bg"], highlightcolor=ACCENT, takefocus=True)
        self.slider.pack(side="left", fill="x", expand=True, padx=4)
        control(row, "Louder", lambda: self.command("louder"), dark=True).pack(side="left")
        options = tk.Frame(self.panel, bg=self.panel["bg"])
        options.pack(fill="x", pady=(6, 0))
        text(options, variable=self.reading, color=LIGHT, size=10, bold=True).pack(side="left")
        control(options, "Reset", lambda: self.command("normal"), dark=True).pack(side="right")
        for caption, value in (("Manual trim", self.manual), ("Mute", self.mute)):
            toggle = tk.Checkbutton(options, text=caption, variable=value, command=self.changed,
                                    bg=self.panel["bg"], fg=LIGHT, activebackground=self.panel["bg"],
                                    activeforeground=ACCENT, selectcolor=TILE, relief="flat", borderwidth=0, padx=5,
                                    highlightcolor=ACCENT_INK)
            toggle.pack(side="right", padx=(0, 4))
        note = text(self.panel, variable=self.summary, color=SOFT, size=9)
        note.pack(fill="x", pady=(6, 0))
        self.panel.bind("<Configure>", lambda event: note.configure(wraplength=max(160, event.width - 8)))
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
            level = f"{offset:+d} dB from matched"
        else:
            level = "Matched level"
        self.label = level
        self.reading.set(level)
        self.summary.set(status)
