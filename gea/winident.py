# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""winident - the control panel's window as GEA-Program on the Windows taskbar, not as Microsoft Edge.

Edge's app window (msedge --app=) shares Edge's taskbar identity, so the taskbar showed Edge's logo for it and
grouped it with Edge. Windows decides a window's taskbar button by its AppUserModelID; a window's own property store
can be given one (SHGetPropertyStoreForWindow), with the icon, the command and the name to use when it is pinned. The
launcher finds the control panel's window once it has opened and gives it GEA-Program's: ENRGYONE.GEAProgram, the
program's gea.ico, and `pythonw -m gea launch` - the same identity the installer gives the Start-menu shortcuts, so
the window, its taskbar button and a pinned shortcut are one program with one icon.

Standard library only (ctypes); nothing happens anywhere but Windows.
"""

from __future__ import annotations

import os
import time
from typing import Callable, List, Optional, Tuple

AUMID = 'ENRGYONE.GEAProgram'
TITLE_PREFIX = 'GEA-Program'


def _guid(s: str):
    import ctypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16), ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_ubyte * 8)]
    u = uuid.UUID(s)
    g = GUID()
    g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
    g.Data4[:] = u.bytes[8:]
    return g, GUID


def windows_with_title(prefix: str = TITLE_PREFIX, exe_name: str = 'msedge.exe') -> List[Tuple[int, str]]:
    """[(hwnd, title)] of visible top-level windows whose title starts with the prefix and whose process is exe_name."""
    import ctypes
    from ctypes import wintypes as W
    u, k = ctypes.WinDLL('user32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
    found: List[Tuple[int, str]] = []
    PROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    u.GetWindowTextLengthW.argtypes = [W.HWND]
    u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    u.IsWindowVisible.argtypes = [W.HWND]
    u.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    k.OpenProcess.restype = W.HANDLE
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    k.CloseHandle.argtypes = [W.HANDLE]

    def cb(hwnd, _lp):
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            n = u.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if not buf.value.startswith(prefix):
                return True
            pid = W.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h = k.OpenProcess(0x1000, False, pid.value)             # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                try:
                    size = W.DWORD(1024)
                    path = ctypes.create_unicode_buffer(1024)
                    if k.QueryFullProcessImageNameW(h, 0, path, ctypes.byref(size)) and os.path.basename(path.value).lower() == exe_name.lower():
                        found.append((int(hwnd), buf.value))
                finally:
                    k.CloseHandle(h)
        except Exception:
            pass
        return True
    u.EnumWindows(PROC(cb), 0)
    return found


def set_identity(hwnd: int, aumid: str, icon: str, command: str, name: str) -> bool:
    """Give one window GEA-Program's taskbar identity (AppUserModel ID, relaunch icon, command and name)."""
    import ctypes
    from ctypes import wintypes as W
    ole32, shell32 = ctypes.WinDLL('ole32'), ctypes.WinDLL('shell32')
    ole32.CoInitialize(None)
    iid, _ = _guid('886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99')                 # IID_IPropertyStore
    fmt, GUID = _guid('9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3')               # PKEY_AppUserModel_*

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [('fmtid', GUID), ('pid', W.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [('vt', ctypes.c_ushort), ('r1', ctypes.c_ushort), ('r2', ctypes.c_ushort), ('r3', ctypes.c_ushort),
                    ('pwszVal', ctypes.c_wchar_p), ('pad', ctypes.c_void_p)]

    store = ctypes.c_void_p()
    shell32.SHGetPropertyStoreForWindow.argtypes = [W.HWND, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    hr = shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid), ctypes.byref(store))
    if hr != 0 or not store:
        return False
    vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    SetValue = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(vtbl[6])
    Commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[7])
    Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
    ok = True
    try:
        for pid, value in ((2, command), (3, icon), (4, name), (5, aumid)):   # RelaunchCommand, RelaunchIconResource, RelaunchDisplayName, ID last
            if not value:
                continue
            key = PROPERTYKEY(fmt, pid)
            pv = PROPVARIANT(31, 0, 0, 0, value, None)                         # VT_LPWSTR; the store copies it
            if SetValue(store, ctypes.byref(key), ctypes.byref(pv)) != 0:
                ok = False
        Commit(store)
    finally:
        Release(store)
    return ok


def claim_window(kit_dir: Optional[str], timeout_s: float = 20.0, sleep: Callable = time.sleep, find: Callable = windows_with_title,
                 setter: Callable = set_identity, done: Optional[set] = None, exe_name: str = 'msedge.exe',
                 aumid: str = AUMID, title_prefix: str = TITLE_PREFIX, name: str = 'GEA-Program', icon_file: str = 'gea.ico',
                 command_args: str = '-m gea launch') -> int:
    """Wait for the program's window (Edge, its title starting with title_prefix) and give it the program's identity:
    GEA-Program's by default, GEA Reader's when the reader passes its own. Returns how many windows were claimed.
    Windows only; elsewhere 0. Stays self-contained: GEA Reader's kit carries this file on its own."""
    if os.name != 'nt' and find is windows_with_title:
        return 0
    import ntpath                                  # Windows paths, whatever the platform the check runs on
    icon = ntpath.join(kit_dir, icon_file) + ',0' if kit_dir else ''
    pyw = ntpath.join(kit_dir, 'python', 'pythonw.exe') if kit_dir else ''
    command = f'"{pyw}" {command_args}' if pyw else ''
    done = set() if done is None else done
    deadline = time.monotonic() + timeout_s
    while True:
        for hwnd, _title in find(title_prefix, exe_name):
            if hwnd not in done and setter(hwnd, aumid, icon, command, name):
                done.add(hwnd)
        if done or time.monotonic() > deadline:
            return len(done)
        sleep(0.5)
