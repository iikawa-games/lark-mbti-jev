"""Notification-area icon. Runs its own Win32 message loop; commands go to a callback."""
from __future__ import annotations
import threading

from .store import DATA

CLASS_NAME = 'FeishuMbtiTray'
WM_TRAY = 0x8000 + 1      # WM_APP + 1: icon mouse events
WM_SHOW_PANEL = 0x8000 + 2  # sent by a second launch
THEME_BASE = 100  # Menu ids for the theme submenu
MENU = [(1, 'show', '打开面板'), (2, 'toggle', None), (3, 'refresh', '刷新聊天'), (0, None, None), (9, 'quit', '退出')]


def _icon_file():
    path = DATA / 'tray.ico'
    if not path.exists():
        from PIL import Image, ImageDraw, ImageFont
        DATA.mkdir(exist_ok=True)
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, 62, 62), 14, fill=(51, 112, 255))
        try:
            font = ImageFont.truetype('segoeuib.ttf', 40)
        except OSError:
            font = ImageFont.load_default()
        draw.text((32, 33), 'M', fill='white', font=font, anchor='mm')
        image.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
    return str(path)


def show_running_panel():
    """Ask an already running instance to open its panel. Returns True if found."""
    import win32gui
    hwnd = win32gui.FindWindow(CLASS_NAME, None)
    if hwnd:
        win32gui.PostMessage(hwnd, WM_SHOW_PANEL, 0, 0)
    return bool(hwnd)


class Tray:
    def __init__(self, on_command, tooltip='飞书 MBTI'):
        self.on_command = on_command
        self.tooltip = tooltip
        self.enabled = False
        self.themes = []  # [(theme id, name)], set by the app
        self.theme = ''
        self.hwnd = None
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True, name='tray').start()
        self._ready.wait(3)

    def _notify_data(self, flags):
        return (self.hwnd, 0, flags, WM_TRAY, self.icon, self.tooltip[:127])

    def _run(self):
        import win32api
        import win32con
        import win32gui
        instance = win32api.GetModuleHandle(None)
        self.taskbar_created = win32gui.RegisterWindowMessage('TaskbarCreated')
        wc = win32gui.WNDCLASS()
        wc.hInstance = instance
        wc.lpszClassName = CLASS_NAME
        wc.lpfnWndProc = {WM_TRAY: self._on_tray, WM_SHOW_PANEL: self._on_show,
                          win32con.WM_COMMAND: self._on_menu, win32con.WM_DESTROY: self._on_destroy,
                          self.taskbar_created: self._on_taskbar}
        try:
            win32gui.RegisterClass(wc)
        except win32gui.error:
            pass  # Already registered in this process.
        self.hwnd = win32gui.CreateWindow(CLASS_NAME, 'Feishu MBTI', 0, 0, 0, 0, 0, 0, 0, instance, None)
        try:
            self.icon = win32gui.LoadImage(instance, _icon_file(), win32con.IMAGE_ICON, 0, 0,
                                           win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
        except Exception:
            self.icon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        self._add()
        self._ready.set()
        win32gui.PumpMessages()

    def _add(self):
        import win32gui
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, self._notify_data(flags))
        except win32gui.error:
            pass  # Explorer not ready yet; TaskbarCreated will add it.

    def _on_taskbar(self, hwnd, msg, wparam, lparam):
        self._add()
        return 0

    def _on_show(self, hwnd, msg, wparam, lparam):
        self.on_command('show')
        return 0

    def _on_tray(self, hwnd, msg, wparam, lparam):
        import win32con
        if lparam in (win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK):
            self.on_command('show')
        elif lparam == win32con.WM_RBUTTONUP:
            self._menu()
        return 0

    def _menu(self):
        import win32con
        import win32gui
        menu = win32gui.CreatePopupMenu()
        themes = win32gui.CreatePopupMenu()
        for index, (key, name) in enumerate(self.themes):
            flags = win32con.MF_STRING | (win32con.MF_CHECKED if key == self.theme else 0)
            win32gui.AppendMenu(themes, flags, THEME_BASE + index, name)
        for command_id, _, text in MENU:
            if not command_id:
                win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, '')
                continue
            if text is None:
                text = '暂停标签' if self.enabled else '开启标签'
            win32gui.AppendMenu(menu, win32con.MF_STRING, command_id, text)
            if command_id == 2 and self.themes:
                win32gui.AppendMenu(menu, win32con.MF_POPUP, themes, '标签显示')
        win32gui.SetMenuDefaultItem(menu, 1, False)
        x, y = win32gui.GetCursorPos()
        # Required so the menu closes when the user clicks elsewhere.
        win32gui.SetForegroundWindow(self.hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_RIGHTBUTTON, x, y, 0, self.hwnd, None)
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)
        win32gui.DestroyMenu(menu)

    def _on_menu(self, hwnd, msg, wparam, lparam):
        command_id = wparam & 0xFFFF
        if THEME_BASE <= command_id < THEME_BASE + len(self.themes):
            self.on_command('theme:' + self.themes[command_id - THEME_BASE][0])
            return 0
        for item_id, name, _ in MENU:
            if item_id == command_id and name:
                self.on_command(name)
        return 0

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        import win32gui
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.hwnd, 0))
        except win32gui.error:
            pass
        win32gui.PostQuitMessage(0)
        return 0

    def set_tooltip(self, text):
        import win32gui
        self.tooltip = text or '飞书 MBTI'
        if self.hwnd:
            try:
                win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, self._notify_data(win32gui.NIF_TIP))
            except win32gui.error:
                pass

    def close(self):
        import win32con
        import win32gui
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
