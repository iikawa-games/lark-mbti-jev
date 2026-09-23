"""Observe scrolling in the current chat without consuming mouse input."""
import ctypes
from collections import deque
from ctypes import wintypes
import threading
import time


class ScrollMonitor:
    def __init__(self, *, clock=time.monotonic, settle_seconds=.25):
        self.clock = clock
        self.settle_seconds = settle_seconds
        self._lock = threading.Lock()
        self._revision = 0
        self._last_motion = float('-inf')
        self._target = (None, None)
        self._dragging = False
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._error = None
        self._wheels = deque(maxlen=128)  # (time, signed delta) inside the chat

    def watch(self, hwnd, rect):
        with self._lock:
            self._target = (hwnd, rect)

    def state(self):
        with self._lock:
            return self._revision, not self._dragging and self.clock() - self._last_motion >= self.settle_seconds

    def wheels_since(self, since):
        with self._lock:
            return [item for item in self._wheels if item[0] >= since]

    def accepts(self, revision):
        current, settled = self.state()
        return settled and current == revision

    def observe(self, message, x, y, foreground, data=0):
        # Called by the native hook: only update a few fields, never OCR or Tk.
        with self._lock:
            hwnd, rect = self._target
            was_dragging = self._dragging
            if message == 0x0202:  # left button up, including outside the pane
                self._dragging = False
                if was_dragging:
                    self._revision += 1
                    self._last_motion = self.clock()
            if not hwnd or hwnd != foreground or not rect:
                return
            if not (rect[0] <= x < rect[2] and rect[1] <= y < rect[3]):
                return
            if message == 0x0201:
                self._dragging = True
            if message in (0x020A, 0x020E, 0x0201) or (message == 0x0200 and self._dragging):
                self._revision += 1
                self._last_motion = self.clock()
            if message == 0x020A:
                delta = (data >> 16) & 0xFFFF
                self._wheels.append((self._last_motion, delta - 0x10000 if delta & 0x8000 else delta))

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name='chat-scroll', daemon=True)
        self._thread.start()
        if not self._ready.wait(2) or self._error:
            raise RuntimeError('鼠标滚动监听启动失败。') from self._error

    def _run(self):
        hook = None
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        class MouseData(ctypes.Structure):
            _fields_ = [('pt', wintypes.POINT), ('mouseData', wintypes.DWORD),
                        ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]

        user.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD]
        user.SetWindowsHookExW.restype = wintypes.HANDLE
        user.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user.CallNextHookEx.restype = ctypes.c_ssize_t
        user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        kernel.GetCurrentThreadId.restype = wintypes.DWORD

        @callback_type
        def callback(code, message, data):
            if code >= 0 and message in (0x0200, 0x0201, 0x0202, 0x020A, 0x020E):
                contents = ctypes.cast(data, ctypes.POINTER(MouseData)).contents
                self.observe(message, contents.pt.x, contents.pt.y, user.GetForegroundWindow(), contents.mouseData)
            return user.CallNextHookEx(None, code, message, data)

        try:
            self._thread_id = kernel.GetCurrentThreadId()
            message = wintypes.MSG()
            user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            hook = user.SetWindowsHookExW(14, callback, kernel.GetModuleHandleW(None), 0)
            if not hook:
                raise ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user.TranslateMessage(ctypes.byref(message))
                user.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self._error = exc
            self._ready.set()
        finally:
            if hook:
                user.UnhookWindowsHookEx(hook)

    def close(self):
        if self._thread and self._thread.is_alive() and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
            self._thread.join(timeout=2)
