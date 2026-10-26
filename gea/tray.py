# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""tray - the program's icon in the Windows notification area.

    gea tray                     the icon (the launcher starts it; one per port)
    gea tray --selftest          make the window, the icon and the menu, show the icon for a moment, take it away

The icon says whether the control panel is running (in colour) or not (grey), in its tooltip and, when the panel
stops without being asked to from here, in a notification. Its menu: Open the control panel (also a double
click), Start or Stop it, Open the site workspace folder, Help, Close this icon. It reads one thing to know how
the panel stands - whether its port answers on this computer - and changes nothing in the workspace.

Standard library only: the Win32 calls through ctypes (Shell_NotifyIconW, a hidden window for its messages, a
popup menu). The icon is drawn in code (gea.icon), so there is no image file to lose.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from . import launcher as L
from .icon import dib, ico_bytes, read_ico

POLL_MS = 3000
TIP_MAX = 127
CMD_STATUS, CMD_OPEN, CMD_START, CMD_STOP, CMD_FOLDER, CMD_HELP, CMD_LOG, CMD_OPERATOR, CMD_CLOSE = 100, 101, 102, 103, 104, 105, 106, 107, 109

# ---------------------------------------------------------------------------- what the icon says (any platform)

def menu_items(running: bool, kit: Optional[str] = None) -> list:
    """[(id, text, enabled)]: the menu as it reads for the state; None is a separator."""
    items = [(CMD_STATUS, 'GEA-Program: the control panel is running' if running else 'GEA-Program: the control panel is stopped', False), None,
             (CMD_OPEN, 'Open the control panel' if running else 'Start and open the control panel', True)]
    if running:
        items.append((CMD_STOP, 'Stop the control panel', kit is not None))
    else:
        items.append((CMD_START, 'Start the control panel (no window)', True))
    items += [(CMD_OPERATOR, 'Open the operator window', True), None, (CMD_FOLDER, 'Open the site workspace folder', True), (CMD_LOG, "The control panel's log", True), (CMD_HELP, 'Help', True), None,
              (CMD_CLOSE, 'Close this icon (the control panel keeps running)' if running else 'Close this icon', True)]
    return items


def tooltip(running: bool, port: int, ws: str) -> str:
    t = f"GEA-Program - {'running' if running else 'stopped'} (port {int(port)})\n{os.path.basename(ws.rstrip(chr(92) + '/')) or ws}"
    return t[:TIP_MAX]


def notice(was: Optional[bool], now: bool, asked: bool) -> Optional[tuple]:
    """(title, text, warning) to show when the state changes, or None. A stop asked for from the icon is not news;
    the first reading is not a change."""
    if was is None or was == now:
        return None
    if now:
        return ('GEA-Program', 'The control panel is running.', False)
    if asked:
        return None
    return ('GEA-Program', 'The control panel has stopped. Its window says why; the icon\'s menu starts it again.', True)


def help_url(base: str) -> str:
    return base.rstrip('/') + '/#/help/install'


# ---------------------------------------------------------------------------- Win32

class _Win32:
    """The handful of Win32 calls the icon needs, with their argument types declared (64-bit handles)."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as W
        self.c, self.W = ctypes, W
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.shell32 = ctypes.WinDLL('shell32', use_last_error=True)
        self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.LRESULT = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(self.LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [('style', W.UINT), ('lpfnWndProc', self.WNDPROC), ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                        ('hInstance', W.HINSTANCE), ('hIcon', W.HICON), ('hCursor', W.HANDLE), ('hbrBackground', W.HBRUSH),
                        ('lpszMenuName', W.LPCWSTR), ('lpszClassName', W.LPCWSTR)]

        class GUID(ctypes.Structure):
            _fields_ = [('Data1', W.DWORD), ('Data2', W.WORD), ('Data3', W.WORD), ('Data4', W.BYTE * 8)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [('cbSize', W.DWORD), ('hWnd', W.HWND), ('uID', W.UINT), ('uFlags', W.UINT), ('uCallbackMessage', W.UINT),
                        ('hIcon', W.HICON), ('szTip', W.WCHAR * 128), ('dwState', W.DWORD), ('dwStateMask', W.DWORD),
                        ('szInfo', W.WCHAR * 256), ('uVersion', W.UINT), ('szInfoTitle', W.WCHAR * 64), ('dwInfoFlags', W.DWORD),
                        ('guidItem', GUID), ('hBalloonIcon', W.HICON)]

        self.WNDCLASSW, self.NOTIFYICONDATAW = WNDCLASSW, NOTIFYICONDATAW
        u, s, k = self.user32, self.shell32, self.kernel32
        P = ctypes.POINTER
        sig = [
            (u, 'DefWindowProcW', [W.HWND, W.UINT, W.WPARAM, W.LPARAM], self.LRESULT),
            (u, 'RegisterClassW', [P(WNDCLASSW)], W.ATOM),
            (u, 'UnregisterClassW', [W.LPCWSTR, W.HINSTANCE], W.BOOL),
            (u, 'CreateWindowExW', [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID], W.HWND),
            (u, 'DestroyWindow', [W.HWND], W.BOOL),
            (u, 'CreateIconFromResourceEx', [ctypes.c_void_p, W.DWORD, W.BOOL, W.DWORD, ctypes.c_int, ctypes.c_int, W.UINT], W.HICON),
            (u, 'DestroyIcon', [W.HICON], W.BOOL),
            (u, 'CreatePopupMenu', [], W.HMENU),
            (u, 'AppendMenuW', [W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR], W.BOOL),
            (u, 'SetMenuDefaultItem', [W.HMENU, W.UINT, W.UINT], W.BOOL),
            (u, 'TrackPopupMenu', [W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, ctypes.c_void_p], ctypes.c_int),
            (u, 'DestroyMenu', [W.HMENU], W.BOOL),
            (u, 'GetCursorPos', [P(W.POINT)], W.BOOL),
            (u, 'SetForegroundWindow', [W.HWND], W.BOOL),
            (u, 'PostMessageW', [W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
            (u, 'SetTimer', [W.HWND, ctypes.c_size_t, W.UINT, ctypes.c_void_p], ctypes.c_size_t),
            (u, 'KillTimer', [W.HWND, ctypes.c_size_t], W.BOOL),
            (u, 'GetMessageW', [P(W.MSG), W.HWND, W.UINT, W.UINT], W.BOOL),
            (u, 'TranslateMessage', [P(W.MSG)], W.BOOL),
            (u, 'DispatchMessageW', [P(W.MSG)], self.LRESULT),
            (u, 'PostQuitMessage', [ctypes.c_int], None),
            (u, 'RegisterWindowMessageW', [W.LPCWSTR], W.UINT),
            (u, 'GetSystemMetrics', [ctypes.c_int], ctypes.c_int),
            (u, 'MessageBoxW', [W.HWND, W.LPCWSTR, W.LPCWSTR, W.UINT], ctypes.c_int),
            (s, 'Shell_NotifyIconW', [W.DWORD, P(NOTIFYICONDATAW)], W.BOOL),
            (k, 'GetModuleHandleW', [W.LPCWSTR], W.HMODULE),
            (k, 'CreateMutexW', [ctypes.c_void_p, W.BOOL, W.LPCWSTR], W.HANDLE),
            (k, 'CloseHandle', [W.HANDLE], W.BOOL),
        ]
        for dll, name, args, res in sig:
            f = getattr(dll, name)
            f.argtypes, f.restype = args, res
        try:
            u.SetProcessDPIAware()
        except Exception:
            pass

    def hicon(self, size: int, running: bool):
        data = dib(size, running)
        buf = (self.c.c_char * len(data)).from_buffer_copy(data)
        h = self.user32.CreateIconFromResourceEx(self.c.cast(buf, self.c.c_void_p), len(data), True, 0x00030000, size, size, 0)
        if not h:
            raise OSError(f'CreateIconFromResourceEx failed ({self.c.get_last_error()})')
        return h


WM_DESTROY, WM_CLOSE, WM_TIMER, WM_COMMAND, WM_NULL = 0x0002, 0x0010, 0x0113, 0x0111, 0x0000
WM_LBUTTONDBLCLK, WM_RBUTTONUP, WM_CONTEXTMENU, WM_APP = 0x0203, 0x0205, 0x007B, 0x8000
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING = 0x1, 0x2
MF_STRING, MF_GRAYED, MF_SEPARATOR = 0x0, 0x1, 0x800
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
SM_CXSMICON = 49
ERROR_ALREADY_EXISTS = 183
WM_TRAY = WM_APP + 1


class Tray:
    def __init__(self, ws: str, port: int, kit=None, api: Optional[_Win32] = None):
        self.ws, self.port, self.kit = ws, int(port), kit
        self.api = api or _Win32()
        self.running: Optional[bool] = None
        self.asked_stop = False
        self.icons = {}
        self.hwnd = None
        self._proc = None
        self._added = False

    # -- window and icon
    def create(self) -> None:
        a = self.api
        self.hinst = a.kernel32.GetModuleHandleW(None)
        self._proc = a.WNDPROC(self._wndproc)
        self.cls = f'GEAProgramTray{self.port}'
        wc = a.WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = self.hinst
        wc.lpszClassName = self.cls
        if not a.user32.RegisterClassW(a.c.byref(wc)):
            raise OSError(f'RegisterClassW failed ({a.c.get_last_error()})')
        self.hwnd = a.user32.CreateWindowExW(0, self.cls, 'GEA-Program', 0, 0, 0, 0, 0, None, None, self.hinst, None)
        if not self.hwnd:
            raise OSError(f'CreateWindowExW failed ({a.c.get_last_error()})')
        self.wm_taskbar = a.user32.RegisterWindowMessageW('TaskbarCreated')
        size = a.user32.GetSystemMetrics(SM_CXSMICON) or 16
        self.icons = {True: a.hicon(size, True), False: a.hicon(size, False)}

    def _nid(self, flags: int):
        a = self.api
        n = a.NOTIFYICONDATAW()
        n.cbSize = a.c.sizeof(a.NOTIFYICONDATAW)
        n.hWnd, n.uID, n.uFlags, n.uCallbackMessage = self.hwnd, 1, flags, WM_TRAY
        n.hIcon = self.icons[bool(self.running)]
        n.szTip = tooltip(bool(self.running), self.port, self.ws)
        return n

    def add(self) -> bool:
        n = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        self._added = bool(self.api.shell32.Shell_NotifyIconW(NIM_ADD, self.api.c.byref(n)))
        return self._added

    def update(self, info: Optional[tuple] = None) -> None:
        n = self._nid(NIF_ICON | NIF_TIP | (NIF_INFO if info else 0))
        if info:
            n.szInfoTitle, n.szInfo = info[0][:63], info[1][:255]
            n.dwInfoFlags = NIIF_WARNING if info[2] else NIIF_INFO
        self.api.shell32.Shell_NotifyIconW(NIM_MODIFY, self.api.c.byref(n))

    def remove(self) -> None:
        if self._added:
            n = self._nid(0)
            self.api.shell32.Shell_NotifyIconW(NIM_DELETE, self.api.c.byref(n))
            self._added = False

    def destroy(self) -> None:
        a = self.api
        self.remove()
        for h in self.icons.values():
            a.user32.DestroyIcon(h)
        self.icons = {}
        if self.hwnd:
            a.user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        a.user32.UnregisterClassW(self.cls, self.hinst)

    def menu(self):
        a = self.api
        m = a.user32.CreatePopupMenu()
        for it in menu_items(bool(self.running), self.kit):
            if it is None:
                a.user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
            else:
                a.user32.AppendMenuW(m, MF_STRING | (0 if it[2] else MF_GRAYED), it[0], it[1])
        a.user32.SetMenuDefaultItem(m, CMD_OPEN, 0)
        return m

    # -- what it does
    def poll(self) -> None:
        now = L.port_open(self.port)
        info = notice(self.running, now, self.asked_stop)
        changed = now != self.running
        self.running = now
        if now:
            self.asked_stop = False
        if changed and self._added:
            self.update(info)

    def _spawn_launch(self, background: bool) -> None:
        import subprocess
        cmd = [L.gui_python(), '-m', 'gea', 'launch', '--workspace', self.ws, '--port', str(self.port), '--no-tray'] + (['--background'] if background else [])
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=L.DETACHED_PROCESS | L.CREATE_NEW_PROCESS_GROUP)

    def command(self, cid: int) -> None:
        if cid == CMD_OPEN:
            if self.running:
                threading.Thread(target=L.open_window, args=(L.dashboard_url(self.ws, self.port),), daemon=True).start()
            else:
                self._spawn_launch(background=False)
        elif cid == CMD_START:
            self._spawn_launch(background=True)
        elif cid == CMD_STOP:
            self.asked_stop = True
            threading.Thread(target=L.stop_service, args=(self.kit,), daemon=True).start()
        elif cid == CMD_FOLDER:
            if os.path.isdir(self.ws):
                os.startfile(self.ws)
        elif cid == CMD_LOG:
            p = L.console_log_path(self.ws)
            if os.path.isfile(p):
                os.startfile(p)
        elif cid == CMD_OPERATOR:
            import subprocess
            subprocess.Popen([L.gui_python(), '-m', 'gea', 'operator'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True, creationflags=L.DETACHED_PROCESS | L.CREATE_NEW_PROCESS_GROUP)
        elif cid == CMD_HELP:
            if self.running:
                threading.Thread(target=L.open_window, args=(help_url(L.dashboard_url(self.ws, self.port)),), daemon=True).start()
            elif self.kit:
                os.startfile(os.path.join(str(self.kit), 'README.txt'))
        elif cid == CMD_CLOSE:
            self.api.user32.DestroyWindow(self.hwnd)

    def _show_menu(self) -> None:
        a = self.api
        pt = a.W.POINT()
        a.user32.GetCursorPos(a.c.byref(pt))
        m = self.menu()
        a.user32.SetForegroundWindow(self.hwnd)
        cid = a.user32.TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, 0, self.hwnd, None)
        a.user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        a.user32.DestroyMenu(m)
        if cid:
            self.command(cid)

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_TRAY:
                if lp in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                elif lp == WM_LBUTTONDBLCLK:
                    self.command(CMD_OPEN)
                return 0
            if msg == WM_TIMER:
                self.poll()
                return 0
            if msg and msg == getattr(self, 'wm_taskbar', 0):
                self.add()                         # Explorer started again: the icon is put back
                return 0
            if msg == WM_DESTROY:
                self.remove()
                self.api.user32.PostQuitMessage(0)
                return 0
        except Exception as e:                     # an error in one click never takes the icon down
            _log(f'{type(e).__name__}: {e}')
        return self.api.user32.DefWindowProcW(hwnd, msg, wp, lp)

    def run(self) -> int:
        a = self.api
        self.create()
        self.running = L.port_open(self.port)
        self.add()
        threading.Thread(target=promote_icon, daemon=True).start()
        a.user32.SetTimer(self.hwnd, 1, POLL_MS, None)
        msg = a.W.MSG()
        while a.user32.GetMessageW(a.c.byref(msg), None, 0, 0) > 0:
            a.user32.TranslateMessage(a.c.byref(msg))
            a.user32.DispatchMessageW(a.c.byref(msg))
        for h in self.icons.values():
            a.user32.DestroyIcon(h)
        return 0


KNOWN_FOLDERS = {'{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}': 'LOCALAPPDATA', '{6D809377-6AF0-444B-8957-A3773F02200E}': 'ProgramW6432',
                 '{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}': 'ProgramFiles(x86)', '{F38BF404-1D43-42F2-9305-67DE0B28FC23}': 'SystemRoot'}


def _known_folder_path(path: str, env: Optional[dict] = None) -> str:
    """Windows writes a notification icon's program path with a known folder's GUID in front
    ({F1B32785-...}\\\\Programs\\\\GEA-Program\\\\...) - the same path, normalised for comparing."""
    env = os.environ if env is None else env
    p = path.strip().strip('"')
    for guid, var in KNOWN_FOLDERS.items():
        if p.upper().startswith(guid) and env.get(var):
            p = env[var] + p[len(guid):]
            break
    p = os.path.expandvars(p)
    return os.path.normcase(os.path.normpath(p)) if os.name == 'nt' else os.path.normpath(p.replace('\\', '/'))


def promote_icon(executable: Optional[str] = None, wait_s: float = 10.0, registry=None) -> int:
    """Windows 11 puts a new program's notification-area icon behind the ^ (hidden icons) until the person drags it
    out; the GEA icon is the program's way back to its window, so it is shown on the taskbar the first time. Windows
    keeps one entry per icon under HKCU\\\\Control Panel\\\\NotifyIconSettings with the program's path; the entry for this
    pythonw.exe is marked IsPromoted=1 only where no choice has been made yet (a person who hid it keeps it hidden).
    Returns the number of entries promoted."""
    import time as _t
    if registry is None:
        try:
            import winreg as registry
        except ImportError:
            return 0
    exe = _known_folder_path(executable or L.gui_python())
    n = 0
    deadline = _t.monotonic() + wait_s
    while True:
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, r'Control Panel\NotifyIconSettings') as root:
                i = 0
                while True:
                    try:
                        sub = registry.EnumKey(root, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with registry.OpenKey(root, sub, 0, registry.KEY_READ | registry.KEY_SET_VALUE) as k:
                            path, _ = registry.QueryValueEx(k, 'ExecutablePath')
                            if _known_folder_path(str(path)) != exe:
                                continue
                            try:
                                registry.QueryValueEx(k, 'IsPromoted')
                                continue                       # a choice was made already: left as it is
                            except OSError:
                                registry.SetValueEx(k, 'IsPromoted', 0, registry.REG_DWORD, 1)
                                n += 1
                    except OSError:
                        continue
        except OSError:
            pass
        if n or _t.monotonic() > deadline:
            return n
        _t.sleep(1.0)


def _log(line: str) -> None:
    try:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        p = os.path.join(base, 'GEA-Program', 'tray.log')
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'a', encoding='utf-8') as f:
            f.write(L._utc_now() + '  ' + line + '\n')
    except OSError:
        pass


def selftest(ws: str, port: int, kit=None, show: bool = True) -> tuple:
    """(ok, lines): the drawing read back as an icon file, the menu for both states; on Windows the window, the
    icon handles and the menu made for real, and the icon shown and taken away again."""
    lines = []
    imgs = read_ico(ico_bytes((16, 32)))
    lines.append(f'icon: {len(imgs)} images ({", ".join(f"{w}x{h} {k}" for w, h, _n, k in imgs)})')
    for r in (True, False):
        lines.append(f"menu ({'running' if r else 'stopped'}): " + ' | '.join(it[1] for it in menu_items(r, kit) if it))
    if not L.is_windows():
        lines.append('the notification area is Windows\'; checked the drawing and the menu only')
        return True, lines
    t = Tray(ws, port, kit)
    try:
        t.create()
        t.running = L.port_open(port)
        m = t.menu()
        t.api.user32.DestroyMenu(m)
        lines.append(f'window: made; icon handles: {len(t.icons)}; menu: made')
        if show:
            shown = t.add()
            lines.append('notification area: icon shown and taken away' if shown else
                         'notification area: no taskbar answered here (a service session) - the icon was not shown')
    finally:
        t.destroy()
    return True, lines


def main(a) -> int:
    kit = L.kit_dir()
    ws, _ = L.resolve_workspace(a.workspace, kit=kit)
    if a.selftest:
        ok, lines = selftest(ws, a.port, str(kit) if kit else None)
        print('\n'.join(lines))
        return 0 if ok else 1
    if not L.is_windows():
        print('gea tray: the notification-area icon is for Windows; the control panel is gea serve (or gea launch)')
        return 2
    api = _Win32()
    mutex = api.kernel32.CreateMutexW(None, False, f'Local\\GEA-Program-Tray-{int(a.port)}')
    if api.c.get_last_error() == ERROR_ALREADY_EXISTS:
        return 0                                   # one icon per port: this one is already there
    try:
        return Tray(ws, a.port, str(kit) if kit else None, api).run()
    finally:
        api.kernel32.CloseHandle(mutex)
