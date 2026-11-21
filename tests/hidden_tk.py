"""Import this in a test that opens Tk windows: nothing appears on the screen of whoever runs the suite.

Tests build real windows to check them. Every window is made fully transparent and kept off the taskbar as soon
as it is created, so it is never seen, while it stays mapped: sizes, layout, events and `update` behave exactly as
on screen (some tests measure how panels fit a window).

Nothing in a test may take the keyboard away from what the person at the computer is doing either. Being unseen
does not do that: Windows makes the first window of a process the foreground window when it is made, seen or
not (measured: a transparent, a disabled and a withdrawn root all took it), and the keys typed after that go to
a window nobody can see. So:

- `focus_force` does nothing, and `deiconify` shows a window without raising it or forcing the focus to it
  (`wm state normal`, which is what Tk offers for that).
- The first window is made inside `Tk()` itself, and the foreground is handed straight back to the window that
  had it. The same is done after every `update`, in case a window took it meanwhile. The keyboard is away for
  the instant in between, not for the rest of the run.
- `focus_set`, `focus` and `lift` are left as they are. Tk's `focus` without `-force` only notes where the
  focus goes once the application has it, and `lift` restacks a window. Neither could be measured apart from
  the rest (a process that has given the foreground away is not allowed to take it again), so if one of them
  does take it, the next `update` gives it back.
"""

import os
import tkinter as tk

_toplevel_init = tk.Toplevel.__init__
_tk_init = tk.Tk.__init__
_update, _update_idletasks = tk.Misc.update, tk.Misc.update_idletasks

try:
    import ctypes

    _user32 = ctypes.windll.user32
    _user32.GetForegroundWindow.restype = ctypes.c_void_p
    _user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    _user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    _user32.IsWindow.argtypes = [ctypes.c_void_p]
except (AttributeError, OSError):     # not Windows: no foreground window to give back
    _user32 = None

_elsewhere = [None]     # the foreground window last seen that is not one of this process's


def _foreground():
    """The foreground window, and whether it is one of this process's."""
    window = _user32.GetForegroundWindow()
    if not window:
        return None, False
    process = ctypes.c_ulong()
    _user32.GetWindowThreadProcessId(window, ctypes.byref(process))
    return window, process.value == os.getpid()


def foreground_is_ours():
    """Whether a window of this process holds the keyboard. For the test of this module."""
    return bool(_user32) and _foreground()[1]


def _note():
    if _user32:
        window, ours = _foreground()
        if window and not ours:
            _elsewhere[0] = window


def _give_back():
    if _user32 and _elsewhere[0] and _foreground()[1] and _user32.IsWindow(_elsewhere[0]):
        _user32.SetForegroundWindow(_elsewhere[0])


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
    _note()
    _tk_init(self, *args, **options)
    _unseen(self)
    try:
        _update_idletasks(self)     # the window is made here, where the foreground it takes can be given back
    except tk.TclError:
        pass
    _give_back()


def _keep_focus(self, *args, **options):
    return None


def _shown_quietly(self):
    return self.wm_state("normal")


def _quiet_update(self):
    _note()
    try:
        return _update(self)
    finally:
        _give_back()


def _quiet_update_idletasks(self):
    _note()
    try:
        return _update_idletasks(self)
    finally:
        _give_back()


tk.Toplevel.__init__ = _hidden_toplevel
tk.Tk.__init__ = _hidden_tk
tk.Misc.focus_force = _keep_focus
tk.Wm.wm_deiconify = tk.Wm.deiconify = _shown_quietly
tk.Misc.update = _quiet_update
tk.Misc.update_idletasks = _quiet_update_idletasks
