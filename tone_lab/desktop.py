"""Launch a single Tone Lab workspace with a native Windows taskbar identity."""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic

APP_ID = "ToneLab.QuadCortex.Desktop"
ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "ToneLab.ico"
LAUNCHER = ROOT / "Tone Lab.exe"
WINDOW_MARK = APP_ID + ".Workspace"


def workspace_window():
    import win32gui

    get_property = ctypes.windll.user32.GetPropW
    get_property.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
    get_property.restype = wintypes.HANDLE
    windows = []

    def collect(hwnd, _):
        if get_property(hwnd, WINDOW_MARK):
            windows.append(hwnd)
        return True

    win32gui.EnumWindows(collect, None)
    return next(iter(windows), None)


def bring_forward(hwnd):
    show_window = ctypes.windll.user32.ShowWindowAsync
    show_window.argtypes = (wintypes.HWND, ctypes.c_int)
    user32 = ctypes.windll.user32
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.FlashWindow.argtypes = (wintypes.HWND, wintypes.BOOL)
    show_window(hwnd, 9 if user32.IsIconic(hwnd) else 5)  # SW_RESTORE or SW_SHOW
    if not user32.SetForegroundWindow(hwnd):
        user32.FlashWindow(hwnd, True)


@contextmanager
def claim_workspace():
    import win32event

    mutex = win32event.CreateMutex(None, False, "Local\\" + APP_ID)
    acquired = False
    try:
        deadline = monotonic() + 10
        while monotonic() < deadline:
            result = win32event.WaitForSingleObject(mutex, 100)
            if result in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
                acquired = True
                yield True
                return
            if hwnd := workspace_window():
                bring_forward(hwnd)
                yield False
                return
        raise RuntimeError("Tone Lab is already starting or closing. Try opening it again in a moment.")
    finally:
        if acquired:
            win32event.ReleaseMutex(mutex)
        mutex.Close()


def open_desk(input_channel=1, *, workbench=False):
    with claim_workspace() as acquired:
        if acquired:
            from . import capture

            capture.launch(input_channel, workbench=workbench)


def identify_process():
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def window_handle(root):
    root.update_idletasks()
    user32 = ctypes.windll.user32
    ancestor = user32.GetAncestor
    ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
    ancestor.restype = wintypes.HWND
    return ancestor(root.winfo_id(), 2)  # Tk's native top-level wrapper.


def _set_dark_title(hwnd):
    enabled = wintypes.BOOL(True)
    dark_mode = ctypes.windll.dwmapi.DwmSetWindowAttribute
    dark_mode.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    dark_mode(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))


def dark_title(root):
    if sys.platform == "win32":
        _set_dark_title(window_handle(root))


def show_in_taskbar(root):
    import tkinter as tk
    from win32com.propsys import propsys, pscon

    from . import diagnostics

    try:
        root.iconbitmap(default=str(ICON))
    except tk.TclError:
        diagnostics.LOG.warning("Could not apply the Tone Lab window icon", exc_info=True)
    hwnd = window_handle(root)
    _set_dark_title(hwnd)
    user32 = ctypes.windll.user32
    get_style = user32.GetWindowLongW
    set_style = user32.SetWindowLongW
    get_style.argtypes = (wintypes.HWND, ctypes.c_int)
    set_style.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
    index = -20  # GWL_EXSTYLE
    flags = (get_style(hwnd, index) | 0x00040000) & ~0x00000080  # APPWINDOW, without TOOLWINDOW.
    set_style(hwnd, index, flags)
    try:
        store = propsys.SHGetPropertyStoreForWindow(hwnd, propsys.IID_IPropertyStore)
        store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
        store.Commit()
    except Exception:
        diagnostics.LOG.warning("Could not apply the Tone Lab taskbar identity", exc_info=True)
    root.deiconify()
    root.update_idletasks()
    mark = user32.SetPropW
    mark.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE)
    if not mark(hwnd, WINDOW_MARK, 1):
        diagnostics.LOG.warning("Could not mark the window for reopening")
    bring_forward(hwnd)


def open_cortex_control():
    user32 = ctypes.windll.user32
    find = user32.FindWindowW
    find.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    find.restype = wintypes.HWND
    hwnd = find(None, "Cortex Control")
    if hwnd:
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        return
    app = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Neural DSP/Cortex Control/Cortex Control.exe"
    if not app.is_file():
        raise RuntimeError("Windows couldn't find Cortex Control in its standard installation folder.")
    os.startfile(app)


def build_launcher():
    source = ROOT / "desktop_launcher" / "Program.cs"
    if LAUNCHER.is_file() and LAUNCHER.stat().st_mtime >= max(source.stat().st_mtime, ICON.stat().st_mtime):
        return
    framework = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Microsoft.NET"
    candidates = [framework / arch / "v4.0.30319" / "csc.exe" for arch in ("Framework64", "Framework")]
    compiler = next((path for path in candidates if path.is_file()), None)
    if compiler is None:
        raise RuntimeError("Windows could not find the .NET Framework compiler to build the Tone Lab launcher.")
    with TemporaryDirectory(dir=ROOT) as temporary:
        staged = Path(temporary) / LAUNCHER.name
        result = subprocess.run([str(compiler), "/nologo", "/target:winexe", "/optimize+",
                                 f"/win32icon:{ICON}", f"/out:{staged}", str(source)],
                                capture_output=True, text=True, errors="replace")
        if result.returncode:
            raise RuntimeError((result.stdout + result.stderr).strip() or "Could not build the Tone Lab launcher.")
        staged.replace(LAUNCHER)


def repair_taskbar_links(file, appdata):
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shell, shellcon

    pins = appdata / "Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar"
    for path in pins.glob("*.lnk"):
        try:
            store = propsys.SHGetPropertyStoreFromParsingName(str(path))
            app_id = store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue()
        except pythoncom.com_error:
            continue
        if app_id == APP_ID:
            file.Save(str(path), False)
            shell.SHChangeNotify(shellcon.SHCNE_UPDATEITEM, shellcon.SHCNF_PATHW, str(path), None)


def shortcut():
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shell, shellcon

    build_launcher()
    link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                     shell.IID_IShellLink)
    link.SetPath(str(LAUNCHER))
    link.SetWorkingDirectory(str(ROOT))
    link.SetDescription("Tone Lab for Quad Cortex")
    link.SetIconLocation(str(ICON), 0)
    store = link.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
    store.Commit()
    programs = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAMS, 0, 0))
    path = ROOT / "Tone Lab.lnk"
    file = link.QueryInterface(pythoncom.IID_IPersistFile)
    file.Save(str(path), True)
    file.Save(str(programs / path.name), True)
    repair_taskbar_links(file, Path(shell.SHGetFolderPath(0, shellcon.CSIDL_APPDATA, 0, 0)))
    return path
