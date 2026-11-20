"""Import this in a test that opens Tk windows: nothing appears on the screen of whoever runs the suite.

Tests build real windows to check them. Every window is made fully transparent and kept off the taskbar as soon
as it is created, so it is never seen, while it stays mapped: sizes, layout, events and `update` behave exactly as
on screen (some tests measure how panels fit a window). Nothing in a test may take the keyboard focus away from
what the person at the computer is doing, so `focus_force` does nothing.
"""

import tkinter as tk

_toplevel_init = tk.Toplevel.__init__
_tk_init = tk.Tk.__init__


def _unseen(window):
    try:
        window.attributes("-alpha", 0.0)
        window.attributes("-toolwindow", True)
    except tk.TclError:
        pass            # an interpreter without a window (tk.Tcl()) has nothing to hide


def _hidden_toplevel(self, *args, **options):
    _toplevel_init(self, *args, **options)
    _unseen(self)


def _hidden_tk(self, *args, **options):
    _tk_init(self, *args, **options)
    _unseen(self)


def _keep_focus(self, *args, **options):
    return None


tk.Toplevel.__init__ = _hidden_toplevel
tk.Tk.__init__ = _hidden_tk
tk.Misc.focus_force = _keep_focus
