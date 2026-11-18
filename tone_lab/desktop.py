"""Give Tone Lab a native Windows taskbar identity and shortcut."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys

APP_ID = "ToneLab.QuadCortex.Desktop"
ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "ToneLab.ico"


def identify_process():
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def show_in_taskbar(root):
    root.iconbitmap(default=str(ICON))
    root.update_idletasks()
    user32 = ctypes.windll.user32
    ancestor = user32.GetAncestor
    ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
    ancestor.restype = wintypes.HWND
    hwnd = ancestor(root.winfo_id(), 2)  # Tk's native top-level wrapper.
    dark_title = wintypes.BOOL(True)
    dark_mode = ctypes.windll.dwmapi.DwmSetWindowAttribute
    dark_mode.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    dark_mode(hwnd, 20, ctypes.byref(dark_title), ctypes.sizeof(dark_title))
    get_style = user32.GetWindowLongW
    set_style = user32.SetWindowLongW
    get_style.argtypes = (wintypes.HWND, ctypes.c_int)
    set_style.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
    index = -20  # GWL_EXSTYLE
    flags = (get_style(hwnd, index) | 0x00040000) & ~0x00000080  # APPWINDOW, without TOOLWINDOW.
    set_style(hwnd, index, flags)
    root.withdraw()
    root.after_idle(root.deiconify)


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


def shortcut():
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shell

    path = ROOT / "Tone Lab.lnk"
    link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                     shell.IID_IShellLink)
    link.SetPath(str(Path(sys.executable).with_name("pythonw.exe")))
    link.SetArguments("-m tone_lab clip")
    link.SetWorkingDirectory(str(ROOT))
    link.SetDescription("Tone Lab for Quad Cortex")
    link.SetIconLocation(str(ICON), 0)
    store = link.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
    store.Commit()
    link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path), True)
    return path
