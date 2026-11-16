"""Give the Takebloom app window its own taskbar button and logo.

Takebloom's window is a Microsoft Edge app window. Windows groups it with Edge and shows a plain globe on the
taskbar, whatever icon the page offers. Windows lets a program give a window its own identity: an
AppUserModelID plus a relaunch command, name and icon (the documented System.AppUserModel window properties).
With those set, the window gets its own taskbar button with the Takebloom logo, and pinning it pins Takebloom,
not Edge. The title-bar and Alt+Tab icon are set with WM_SETICON.

Windows only; everything is best effort and never stops the app from opening.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

APP_ID = "Takebloom.VideoStudio"
TITLE = "Takebloom"


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "GUID":
        import uuid
        u = uuid.UUID(text)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b
        return g


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):  # only the VT_LPWSTR form is used
    _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.USHORT), ("r2", wintypes.USHORT), ("r3", wintypes.USHORT),
                ("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]


_FMTID = "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"
PKEY = {"id": 5, "relaunch_command": 2, "relaunch_icon": 3, "relaunch_name": 4}
IID_IPropertyStore = "886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"
VT_LPWSTR = 31
WM_SETICON = 0x0080


def icon_path() -> Path | None:
    from video_studio.util import resource_path
    path = Path(resource_path("assets", "app-icon.ico"))
    return path if path.is_file() else None


def launch_command() -> str:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).with_name("VideoStudio.exe")
        return f'"{exe}"'
    return f'"{sys.executable}" -m video_studio studio'


def find_windows(title: str = TITLE) -> list[int]:
    """Visible top-level Edge windows whose title is the app's title."""
    if os.name != "nt":
        return []
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value != title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            size = wintypes.DWORD(1024)
            path = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)) and path.value.lower().endswith("msedge.exe"):
                found.append(hwnd)
            kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows(visit, 0)
    return found


def _store(hwnd: int):
    shell32 = ctypes.windll.shell32
    ptr = ctypes.c_void_p()
    iid = GUID.parse(IID_IPropertyStore)
    hr = shell32.SHGetPropertyStoreForWindow(wintypes.HWND(hwnd), ctypes.byref(iid), ctypes.byref(ptr))
    if hr != 0 or not ptr.value:
        raise OSError(f"SHGetPropertyStoreForWindow failed ({hr & 0xFFFFFFFF:#x})")
    return ptr


def _call(ptr, index: int, *argtypes):
    vtable = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])


def set_identity(hwnd: int, icon: Path | None = None) -> bool:
    """Give one window Takebloom's taskbar identity. Returns True when Windows accepted it."""
    icon = icon or icon_path()
    ptr = _store(hwnd)
    set_value = _call(ptr, 6, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))
    commit = _call(ptr, 7)
    release = _call(ptr, 2)
    fmtid = GUID.parse(_FMTID)
    values = [("relaunch_command", launch_command()), ("relaunch_name", "Takebloom")]
    if icon:
        values.append(("relaunch_icon", f"{icon},0"))
    values.append(("id", APP_ID))  # the ID last, so Windows regroups the button with the relaunch details in place
    try:
        ok = True
        for key, text in values:
            pk = PROPERTYKEY(fmtid, PKEY[key])
            pv = PROPVARIANT(VT_LPWSTR, 0, 0, 0, text, None)
            ok = set_value(ptr, ctypes.byref(pk), ctypes.byref(pv)) == 0 and ok
        commit(ptr)
        return ok
    finally:
        release(ptr)


_ICONS: dict[int, int] = {}


def set_icon(hwnd: int, icon: Path | None = None) -> None:
    """Title-bar and Alt+Tab icon. The icon handles must outlive the window, so this process keeps them."""
    icon = icon or icon_path()
    if not icon:
        return
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = wintypes.HANDLE
    for kind, size in ((0, 16), (1, 32)):  # ICON_SMALL, ICON_BIG
        if size not in _ICONS:
            _ICONS[size] = user32.LoadImageW(None, str(icon), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if _ICONS[size]:
            user32.SendMessageTimeoutW(wintypes.HWND(hwnd), WM_SETICON, kind, _ICONS[size], 0x0002, 500, None)


def icon_kept(hwnd: int) -> bool:
    """True when the window still shows our icon (Edge may swap in the page's own icon after a reload)."""
    if not _ICONS.get(16):
        return True
    user32 = ctypes.windll.user32
    user32.SendMessageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    return user32.SendMessageW(hwnd, 0x007F, 0, 0) == _ICONS[16]  # WM_GETICON, ICON_SMALL


def brand(hwnd: int) -> bool:
    try:
        ok = set_identity(hwnd)
    except OSError:
        ok = False
    try:
        set_icon(hwnd)
    except OSError:
        pass
    return ok


def watch(first_wait: float = 25.0, poll: float = 1.5) -> int:
    """Brand the app window as soon as it appears, then keep branding new ones until none is left open.
    Returns how many windows were branded. Runs in the small desktop launcher process."""
    if os.name != "nt":
        return 0
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (OSError, AttributeError):
        pass
    done: set[int] = set()
    deadline = time.monotonic() + first_wait
    while True:
        windows = find_windows()
        for hwnd in windows:
            if hwnd not in done:
                brand(hwnd)
                done.add(hwnd)
            else:
                try:
                    if not icon_kept(hwnd):
                        set_icon(hwnd)
                except OSError:
                    pass
        if windows:
            deadline = time.monotonic() + 4 * poll  # keep watching while the window is open (reloads, new windows)
        elif time.monotonic() > deadline:
            return len(done)
        time.sleep(poll)
