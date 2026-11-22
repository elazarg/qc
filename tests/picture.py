"""A picture of a window that is not on the screen: what a hidden window (`hidden_tk`) would show, as a PNG file.

Tests measure how things fit; a picture lets somebody look, without anything appearing on the player's screen.

    import isolated, hidden_tk, picture
    picture.save(root, "desk.png")

Windows draws a window into memory when asked (`PrintWindow`), also one that is cloaked and far off the screen.
The PNG is written here, with the standard library only.
"""

import ctypes
from ctypes import wintypes
import struct
import zlib

_user32 = ctypes.WinDLL("user32")
_gdi32 = ctypes.WinDLL("gdi32")
_dwm = ctypes.WinDLL("dwmapi")
CLOAK = 13                              # DwmSetWindowAttribute: shown nowhere while set
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetWindowDC.argtypes = [wintypes.HWND]
_user32.GetWindowDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 wintypes.UINT]
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.UINT]

CLIENT_ONLY, WHOLE_CONTENT = 1, 2      # PrintWindow: the inside of the window, drawn as it is composed
INVALIDATE, ERASE, ALL_CHILDREN, FRAME = 0x1, 0x4, 0x80, 0x400      # RedrawWindow
QUIETLY = 0x1 | 0x4 | 0x10              # SetWindowPos: size and order kept, not activated


class _Header(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
                ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                ("image", wintypes.DWORD), ("x", wintypes.LONG), ("y", wintypes.LONG), ("used", wintypes.DWORD),
                ("important", wintypes.DWORD)]


def pixels(window):
    """(width, height, rows of RGB bytes from the top) of the inside of a Tk window."""
    handle = _user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
    # A window nobody sees is not asked to draw the parts a visible one would be: ask for all of it, and let Tk
    # answer, before the picture is taken.
    # Windows asks a window to draw only what lies on a screen, and a hidden window lies far off every screen
    # (`hidden_tk`): it has drawn its texts and nothing behind them. For the picture it is cloaked (shown
    # nowhere), put where a screen is so that it is asked to draw all of itself, and put back afterwards.
    cloak, place = ctypes.c_int(1), wintypes.RECT()
    if _dwm.DwmSetWindowAttribute(handle, CLOAK, ctypes.byref(cloak), ctypes.sizeof(cloak)) != 0:
        raise RuntimeError("The window could not be cloaked, so no picture is taken of it.")
    _user32.GetWindowRect(handle, ctypes.byref(place))
    _user32.SetWindowPos(handle, None, 0, 0, 0, 0, QUIETLY)
    _user32.RedrawWindow(handle, None, None, INVALIDATE | ERASE | ALL_CHILDREN | FRAME)
    window.update()
    window.update_idletasks()
    rect = wintypes.RECT()
    _user32.GetClientRect(handle, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError("The window has no size yet.")
    screen = _user32.GetWindowDC(handle)
    memory = _gdi32.CreateCompatibleDC(screen)
    bitmap = _gdi32.CreateCompatibleBitmap(screen, width, height)
    before = _gdi32.SelectObject(memory, bitmap)
    try:
        if not _user32.PrintWindow(handle, memory, CLIENT_ONLY | WHOLE_CONTENT):
            raise RuntimeError("Windows did not draw the window.")
        header = _Header(ctypes.sizeof(_Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        raw = ctypes.create_string_buffer(width * height * 4)
        _gdi32.SelectObject(memory, before)
        if not _gdi32.GetDIBits(memory, bitmap, 0, height, raw, ctypes.byref(header), 0):
            raise RuntimeError("The picture could not be read.")
    finally:
        _user32.SetWindowPos(handle, None, place.left, place.top, 0, 0, QUIETLY)
        _gdi32.SelectObject(memory, before)
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(memory)
        _user32.ReleaseDC(handle, screen)
    data = raw.raw
    rows = []
    for y in range(height):
        line = data[y * width * 4:(y + 1) * width * 4]
        rows.append(bytes(b for x in range(0, len(line), 4) for b in (line[x + 2], line[x + 1], line[x])))
    return width, height, rows


def save(window, path):
    """Write what `window` shows to `path` as a PNG. Returns (width, height)."""
    width, height, rows = pixels(window)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)

    body = zlib.compress(b"".join(b"\x00" + row for row in rows), 6)
    with open(path, "wb") as out:
        out.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                  + chunk(b"IDAT", body) + chunk(b"IEND", b""))
    return width, height
