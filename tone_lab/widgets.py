"""Shared Windows widgets for the Tone Lab workspace."""

import tkinter as tk
from tkinter import ttk

from . import theme as palette

BG = palette.PAPER
PANEL = palette.DARK
RECESS = "#181a22"
LINE = palette.RULE
TEXT = palette.INK
MUTED = palette.SUBTLE
ACCENT = palette.ACCENT
INK = palette.ACCENT_INK
FONT = palette.FONT


def button(parent, label, command, *, accent=False, compact=False):
    return tk.Button(
        parent, text=label, command=command, font=(FONT, 10, "bold"), relief="flat",
        borderwidth=0, padx=10 if compact else 14, pady=6 if compact else 9,
        background=ACCENT if accent else palette.TILE, foreground=INK if accent else TEXT,
        activebackground="#ffd7b7" if accent else palette.RULE, activeforeground=INK if accent else TEXT,
        disabledforeground="#8a8b9a" if not accent else "#7a5a43", takefocus=True,
        highlightthickness=1, highlightcolor=ACCENT, highlightbackground=parent.cget("bg"),
    )


def label(parent, text=None, *, variable=None, size=10, strong=False, color=TEXT, wrap=0):
    options = {"textvariable": variable} if variable is not None else {"text": text}
    return tk.Label(parent, **options, font=(FONT, size, "bold" if strong else "normal"),
                    background=parent.cget("bg"), foreground=color, wraplength=wrap,
                    justify="left", anchor="w")


def card(parent, *, padding=16):
    return tk.Frame(parent, background=PANEL, padx=padding, pady=padding,
                    highlightbackground=LINE, highlightthickness=1)


def entry(parent, variable, *, width=24):
    return tk.Entry(parent, textvariable=variable, width=width, font=(FONT, 11),
                    background=RECESS, foreground=TEXT, insertbackground=TEXT, relief="flat",
                    highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                    selectbackground="#3e4c68", selectforeground=TEXT)


def theme(parent):
    style = ttk.Style(parent)
    style.theme_use("clam")
    style.configure("ToneLab.Vertical.TScrollbar", background=LINE, troughcolor=BG, bordercolor=BG,
                    darkcolor=LINE, lightcolor=LINE, arrowcolor=MUTED, arrowsize=11)
    style.configure("ToneLab.Horizontal.TScale", background=ACCENT, troughcolor=LINE, bordercolor=RECESS,
                    darkcolor=ACCENT, lightcolor=ACCENT, sliderlength=14)


def add_scrollbar(parent, canvas):
    bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview, style="ToneLab.Vertical.TScrollbar")

    def show(first, last):
        bar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            bar.pack_forget()
        elif not bar.winfo_manager():
            bar.pack(side="right", fill="y", before=canvas)

    canvas.configure(yscrollcommand=show)


def slider(parent, variable):
    return ttk.Scale(parent, variable=variable, from_=0, to=1, orient="horizontal",
                     style="ToneLab.Horizontal.TScale", takefocus=True)
