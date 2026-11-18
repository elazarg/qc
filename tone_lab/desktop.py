"""Give Tone Lab a native Windows taskbar identity and shortcut."""

import ctypes
from ctypes import wintypes
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
