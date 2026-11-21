"""Import this in a test that opens Tk windows: nothing appears on the screen of whoever runs the suite, the
keyboard stays with them, and `update` always comes back.

Tests build real windows to check them. A window stays mapped, so that sizes, layout, events and `update`
behave exactly as on screen (some tests measure how panels fit a window), and is kept from being seen.

**Not seen.** Every window is made far off the screen (20,000 points up and to the left of it), is kept off the
taskbar, and is cloaked as soon as Windows has made it: a cloaked window is laid out and drawn like any other
and shown nowhere. A place on the screen asked for later (`geometry("...+0+0")`) is not taken; the size is.
Should cloaking fail, the window is made fully transparent instead.
(Until now every window was made transparent. That hides it as well, but Windows draws a transparent window by
another, slower route: a change of size of the desk's window took 150 ms that way and takes 35 ms this way,
and the tests that look at how things fit spend most of their time there.)

**The keyboard.** Being unseen does not keep a window from taking it. Tk activates the first window a process
makes (measured: a transparent, a disabled and a withdrawn root each became the foreground window), and the
keys typed after that go to a window nobody can see. So:

- While a window is being made, Windows is told to refuse its activation (a hook of this thread, there only
  for that moment), so the window the person is typing in stays the foreground window throughout.
- `focus_force` does nothing, and `deiconify` shows a window without raising it or forcing the focus to it
  (`wm state normal`, which is what Tk offers for that).
- Should a window of this process hold the foreground all the same after a `Tk()` or an `update`, it is handed
  straight back to the window that had it, and counted (`handed_back`): `tests/test_isolation.py` expects none.
- `focus_set`, `focus` and `lift` are left as they are: Tk's `focus` without `-force` only notes where the focus
  goes once the application has it, and `lift` restacks a window.

**`update` coming back.** For a while `root.update()` sometimes never returned (no processor time used; about
half the runs of the tests that show tools in the desk's window). Tcl's event loop on Windows asks whether a
message is waiting (`PeekMessage`) and, told yes, fetches it with `GetMessage`, which waits until one comes.
What was seen each time it stopped (a hook noting every message, in the scratch folder of the review) was a run
of paint messages for child windows, and then nothing; one posted message set it going again. It happened only
in runs where the first window had taken the foreground and handed it back, as this module did then (3 stops
in 3 runs); with the activation refused, so that nothing is taken or given, it did not happen once, and the
timer below never had to end a wait. How the two are connected inside Windows was not found. Because the loop
is Tcl's and cannot be changed from here, the wait is made finite all the same: while `update` runs, a timer
of this thread ticks every 50 ms. A tick is a message like any other to `GetMessage`, and Tk does nothing with
it. (The app itself is not at risk: its `mainloop` waits there on purpose.)

**The home folder.** tkinter reads and runs `.Tk.tcl`, `.Tk.py` and the like from the home folder (or, without
a HOME, from the folder the tests were started in) whenever a root is made. In a test it does not.

**Closing.** `destroy` of a root first destroys everything in it in one step, as Tk does, and only then lets
tkinter take its widgets apart one by one, which otherwise lays the window out again after each (0.15 s for the
desk against 0.04 s).
"""

import atexit
from contextlib import contextmanager
import os
import re
import sys
import tkinter as tk

_toplevel_init = tk.Toplevel.__init__
_tk_init, _tk_destroy = tk.Tk.__init__, tk.Tk.destroy
_update, _update_idletasks = tk.Misc.update, tk.Misc.update_idletasks
_geometry = tk.Wm.wm_geometry

TICK_MS = 50
FAR = "+-20000+-20000"  # where every window is made: off any screen
handed_back = 0         # times a window of this process held the foreground and it was given back
refused = 0             # times an activation was refused while a window was being made
transparent = 0         # windows that could not be cloaked and were made transparent instead

try:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32")
    _dwm = ctypes.WinDLL("dwmapi")
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
    _user32.SetTimer.restype = ctypes.c_size_t
    _user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
    _Hook = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    _user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _Hook, wintypes.HINSTANCE, wintypes.DWORD]
    _user32.SetWindowsHookExW.restype = wintypes.HHOOK
    _user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    _user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    _user32.CallNextHookEx.restype = ctypes.c_ssize_t
    _dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _this_thread = ctypes.WinDLL("kernel32").GetCurrentThreadId
except (AttributeError, OSError, ImportError):     # not Windows: no foreground window, and Tcl waits another way
    _user32 = _dwm = None

_elsewhere = [None]     # the foreground window last seen that is not one of this process's
_ACTIVATE, _SET_FOCUS = 5, 9        # what a computer-based-training hook (WH_CBT, 5) is asked about
_CLOAK, _CLOAKED = 13, 14           # the window attributes that cloak a window and say whether it is cloaked


# ---- the keyboard ----

def _foreground():
    """The foreground window, and whether it is one of this process's."""
    window = _user32.GetForegroundWindow()
    if not window:
        return None, False
    process = wintypes.DWORD()
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
    global handed_back
    if _user32 and _elsewhere[0] and _foreground()[1] and _user32.IsWindow(_elsewhere[0]):
        handed_back += 1
        _user32.SetForegroundWindow(_elsewhere[0])


if _user32:
    @_Hook
    def _refuse(code, wparam, lparam):
        global refused
        if code in (_ACTIVATE, _SET_FOCUS):
            refused += 1
            return 1
        return _user32.CallNextHookEx(None, code, wparam, lparam)


@contextmanager
def _never_active():
    """While this lasts, no window of this thread is activated or given the focus."""
    hook = _user32.SetWindowsHookExW(5, _refuse, None, _this_thread()) if _user32 else None
    try:
        yield
    finally:
        if hook:
            _user32.UnhookWindowsHookEx(hook)


def _keep_focus(self, *args, **options):
    return None


def _shown_quietly(self):
    return self.wm_state("normal")


# ---- not seen ----

def is_cloaked(window):
    """Whether Windows shows a window nowhere although it is mapped. For the test of this module."""
    if not _dwm:
        return False
    cloaked = wintypes.DWORD()
    _dwm.DwmGetWindowAttribute(_user32.GetAncestor(window.winfo_id(), 2), _CLOAKED, ctypes.byref(cloaked), 4)
    return bool(cloaked.value)


def _far_away(window):
    """Before a window is first shown: off the screen and off the taskbar."""
    try:
        window.attributes("-toolwindow", True)
        _geometry(window, FAR)
    except tk.TclError:
        pass            # an interpreter without a window (tk.Tcl()) has nothing to hide


def _cloak(window):
    """Once Windows has made a window: shown nowhere, wherever it is put. Transparent if that cannot be done."""
    global transparent
    try:
        if _dwm:
            _dwm.DwmSetWindowAttribute(_user32.GetAncestor(window.winfo_id(), 2), _CLOAK,
                                       ctypes.byref(wintypes.BOOL(True)), 4)
        if not is_cloaked(window):
            transparent += 1
            window.attributes("-alpha", 0.0)
    except tk.TclError:
        pass


def _size_only(self, newGeometry=None):
    """`geometry` that takes a size and leaves the window where it is, far off the screen."""
    if newGeometry is None:
        return _geometry(self)
    size = re.match(r"=?(\d+x\d+)?", str(newGeometry)).group(1)
    return _geometry(self, size) if size else ""


def _hidden_toplevel(self, *args, **options):
    with _never_active():
        _toplevel_init(self, *args, **options)
        _far_away(self)
    # Tk makes the window when it is first shown, which is when it can be cloaked.
    self.bind("<Map>", lambda event: event.widget is self and _cloak(self), add="+")


def _hidden_tk(self, *args, **options):
    _note()
    with _never_active():
        _tk_init(self, *args, **options)
        _far_away(self)
        try:
            _update_idletasks(self)     # the window is made here, while its activation is refused
        except tk.TclError:
            pass
        _cloak(self)
    _give_back()


def _no_profile(self, baseName, className):
    return None


def _destroyed_at_once(self):
    try:
        inside = [child._w for child in self.winfo_children()]
        if inside:
            self.tk.call("destroy", *inside)
    except tk.TclError:
        pass            # already gone
    _tk_destroy(self)


# ---- `update` coming back ----

@contextmanager
def _ticking():
    """While this lasts, a message comes to this thread every TICK_MS, so that a wait for one always ends."""
    timer = _user32.SetTimer(None, 0, TICK_MS, None) if _user32 else 0
    try:
        yield
    finally:
        if timer:
            _user32.KillTimer(None, timer)


def _quiet_update(self):
    _note()
    try:
        with _ticking():
            return _update(self)
    finally:
        _give_back()


def _quiet_update_idletasks(self):
    _note()
    try:
        return _update_idletasks(self)      # idle work only: Tcl does not wait for a message here
    finally:
        _give_back()


def _say_what_went_wrong():
    if handed_back:
        print(f"hidden_tk: a window of the tests held the keyboard {handed_back} time(s) and gave it back.",
              file=sys.stderr)
    if transparent:
        print(f"hidden_tk: {transparent} window(s) could not be cloaked and were made transparent.", file=sys.stderr)


atexit.register(_say_what_went_wrong)
tk.Toplevel.__init__ = _hidden_toplevel
tk.Tk.__init__ = _hidden_tk
tk.Tk.destroy = _destroyed_at_once
tk.Tk.readprofile = _no_profile
tk.Misc.focus_force = _keep_focus
tk.Wm.wm_deiconify = tk.Wm.deiconify = _shown_quietly
tk.Wm.wm_geometry = tk.Wm.geometry = _size_only
tk.Misc.update = _quiet_update
tk.Misc.update_idletasks = _quiet_update_idletasks
