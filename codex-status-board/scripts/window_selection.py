"""Read the current Codex sidebar task using Windows UI Automation.

No UI actions are performed. Missing, ambiguous, or inaccessible state returns
None. Run in a disposable subprocess: a foreign UIA provider can block a call.
"""

from __future__ import annotations

import ctypes
import re
import sys
import uuid
from ctypes import wintypes


def selected_title() -> str | None:
    """Return one visible Codex window's aria-current sidebar title, if known."""
    if sys.platform != "win32":
        return None
    try:
        return _selected_title_windows()
    except (OSError, ValueError, RuntimeError):
        return None


def _selected_title_windows() -> str | None:
    ole = ctypes.OleDLL("ole32")
    automation = ctypes.OleDLL("oleaut32")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    ptr = ctypes.c_void_p
    hresult = ctypes.c_long

    class Guid(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

        def __init__(self, value):
            super().__init__()
            self.data[:] = uuid.UUID(value).bytes_le

    class VariantData(ctypes.Union):
        _fields_ = [
            ("bstr", ptr),
            ("integer", ctypes.c_long),
            ("boolean", ctypes.c_short),
            ("storage", ctypes.c_ubyte * (16 if ctypes.sizeof(ptr) == 8 else 8)),
        ]

    class Variant(ctypes.Structure):
        _fields_ = [
            ("vt", ctypes.c_ushort),
            ("reserved1", ctypes.c_ushort),
            ("reserved2", ctypes.c_ushort),
            ("reserved3", ctypes.c_ushort),
            ("value", VariantData),
        ]

    ole.CoInitializeEx.argtypes = [ptr, wintypes.DWORD]
    ole.CoInitializeEx.restype = hresult
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(Guid), ptr, wintypes.DWORD,
                                    ctypes.POINTER(Guid), ctypes.POINTER(ptr)]
    ole.CoCreateInstance.restype = hresult
    ole.CoUninitialize.argtypes = []
    automation.VariantClear.argtypes = [ctypes.POINTER(Variant)]
    automation.VariantClear.restype = hresult
    automation.SysStringLen.argtypes = [ptr]
    automation.SysStringLen.restype = wintypes.UINT
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                 wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    user.IsIconic.argtypes = [wintypes.HWND]
    user.IsIconic.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL

    def call(interface, slot, argtypes, *args):
        table = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ptr))).contents
        method = ctypes.WINFUNCTYPE(hresult, ptr, *argtypes)(table[slot])
        result = method(interface, *args)
        if result < 0:
            raise OSError(f"UI Automation HRESULT {result & 0xffffffff:08x}")
        return result

    def release(interface):
        if interface:
            table = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ptr))).contents
            ctypes.WINFUNCTYPE(wintypes.ULONG, ptr)(table[2])(interface)

    def property_value(element, property_id):
        value = Variant()
        try:
            call(element, 10, [ctypes.c_int, ctypes.POINTER(Variant)], property_id,
                 ctypes.byref(value))
            if value.vt == 8:  # VT_BSTR; length avoids embedded-NUL assumptions.
                return (ctypes.wstring_at(value.value.bstr,
                                         automation.SysStringLen(value.value.bstr))
                        if value.value.bstr else "")
            if value.vt == 3:
                return value.value.integer
            if value.vt == 11:
                return bool(value.value.boolean)
            return None
        finally:
            automation.VariantClear(ctypes.byref(value))

    windows = []
    process_matches = {}

    def is_codex_process(pid):
        if pid in process_matches:
            return process_matches[pid]
        handle = kernel.OpenProcess(0x1000, False, pid)
        matches = False
        if handle:
            try:
                size = wintypes.DWORD(32768)
                path = ctypes.create_unicode_buffer(size.value)
                if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    normalized = path.value.replace("/", "\\").lower()
                    parts = normalized.split("\\")
                    matches = (parts[-1] in {"codex.exe", "chatgpt.exe"}
                               and any(part.startswith("openai.codex_") or part == "codex"
                                       for part in parts[:-1]))
            finally:
                kernel.CloseHandle(handle)
        process_matches[pid] = matches
        return matches

    @callback_type
    def collect(hwnd, _):
        if user.IsWindowVisible(hwnd) and not user.IsIconic(hwnd):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if is_codex_process(pid.value):
                windows.append(hwnd)
        return True

    if not user.EnumWindows(collect, 0) or not windows:
        return None
    initialized = ole.CoInitializeEx(None, 0)
    if initialized < 0:
        return None
    client, condition = ptr(), ptr()
    try:
        clsid = Guid("FF48DBA4-60EF-4201-AA87-54103EEF594E")
        iid = Guid("30CBE57D-D9D0-452A-AB13-7AC5AC4825EE")
        if ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid),
                                ctypes.byref(client)) < 0:
            return None
        call(client, 21, [ctypes.POINTER(ptr)], ctypes.byref(condition))
        candidates = []
        for hwnd in windows:
            root, elements = ptr(), ptr()
            try:
                call(client, 6, [wintypes.HWND, ctypes.POINTER(ptr)], hwnd,
                     ctypes.byref(root))
                call(root, 6, [ctypes.c_int, ptr, ctypes.POINTER(ptr)], 4, condition,
                     ctypes.byref(elements))  # TreeScope_Descendants
                count = ctypes.c_int()
                call(elements, 3, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(count))
                for index in range(count.value):
                    element = ptr()
                    try:
                        call(elements, 4, [ctypes.c_int, ctypes.POINTER(ptr)], index,
                             ctypes.byref(element))
                        class_name = property_value(element, 30012)
                        if not isinstance(class_name, str) or "sidebar-item" not in class_name.split():
                            continue
                        if property_value(element, 30003) != 50000:  # Button
                            continue
                        aria = property_value(element, 30102)
                        if not isinstance(aria, str) or not re.search(
                                r"(?:^|;)\s*(?:aria-)?current\s*=\s*page\s*(?:;|$)", aria):
                            continue
                        name = property_value(element, 30005)
                        if not isinstance(name, str) or not name.strip():
                            return None
                        candidates.append(name.strip())
                    finally:
                        release(element)
            finally:
                release(elements)
                release(root)
        # Even equal titles in separate windows are ambiguous.
        return candidates[0] if len(candidates) == 1 else None
    finally:
        release(condition)
        release(client)
        ole.CoUninitialize()


if __name__ == "__main__":
    import json
    print(json.dumps(selected_title(), ensure_ascii=True))
