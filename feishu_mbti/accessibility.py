"""Read Feishu's desktop text and coordinates through Windows MSAA.

No screenshots, OCR, selection changes or client injection are used here.
COM objects stay on the scanner thread and are released before its apartment.
"""
from collections import deque
import ctypes
import time
import threading

_thread = threading.local()


def clear_thread_cache():
    _thread.__dict__.clear()


def references_unchanged(references):
    try:
        for obj, child_id, name, box in references.values():
            if tuple(obj.accLocation(child_id)) != box or obj.accName[child_id] != name:
                return False
    except Exception:
        # A virtualized message can disappear between two COM property reads.
        return False
    return True


def intersects(box, clip):
    x, y, width, height = box
    return width > 0 and height > 0 and x < clip[2] and y < clip[3] and x + width > clip[0] and y + height > clip[1]


class NativeReader:
    def __init__(self):
        import comtypes
        import comtypes.client
        from comtypes.automation import VARIANT
        self.interface = comtypes.client.GetModule('oleacc.dll').IAccessible
        self.variant = VARIANT
        self.api = ctypes.OleDLL('oleacc')
        self.api.AccessibleObjectFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(comtypes.GUID), ctypes.POINTER(ctypes.POINTER(self.interface))]
        self.api.AccessibleChildren.argtypes = [ctypes.POINTER(self.interface), ctypes.c_long,
            ctypes.c_long, ctypes.POINTER(VARIANT), ctypes.POINTER(ctypes.c_long)]

    def window(self, hwnd):
        obj = ctypes.POINTER(self.interface)()
        self.api.AccessibleObjectFromWindow(hwnd, 0xFFFFFFFC,
                                           ctypes.byref(self.interface._iid_), ctypes.byref(obj))
        return obj

    def children(self, obj, child_id=0):
        if child_id:
            return []
        count = min(obj.accChildCount, 256)
        if count <= 0:
            return []
        values = (self.variant * count)()
        obtained = ctypes.c_long()
        self.api.AccessibleChildren(obj, 0, count, values, ctypes.byref(obtained))
        result = []
        for index in range(obtained.value):
            value = values[index].value
            if isinstance(value, int):
                result.append((obj, value))
            elif value:
                result.append((value.QueryInterface(self.interface), 0))
        return result

    def chat(self, hwnd):
        queue = deque([(self.window(hwnd), 0)])
        deadline = time.monotonic() + .75
        visited = 0
        while queue and visited < 240 and time.monotonic() < deadline:
            obj, child_id = queue.popleft()
            visited += 1
            try:
                if 'messenger-chat:' in (obj.accName[child_id] or ''):
                    x, y, width, height = obj.accLocation(child_id)
                    return obj, child_id, (x, y, x + width, y + height)
                queue.extend(self.children(obj, child_id))
            except Exception:
                continue
        return None

    def text(self, chat, *, max_nodes=1200):
        from .desktop import Line
        root, child_id, rect = chat
        queue = deque([(root, child_id)])
        deadline = time.monotonic() + .8
        lines, references, seen = [], {}, set()
        visited = 0
        while queue and visited < max_nodes and time.monotonic() < deadline:
            obj, child_id = queue.popleft()
            visited += 1
            try:
                box = tuple(obj.accLocation(child_id))
                # Offscreen messages can remain in the virtual accessibility tree.
                if box[2] > 0 and box[3] > 0 and not intersects(box, rect):
                    continue
                role = obj.accRole[child_id]
                if role in (40, 41, 42, 43):
                    name = obj.accName[child_id] or ''
                    if name and len(name) <= 2000 and intersects(box, rect):
                        key = (name, box)
                        if key not in seen:
                            seen.add(key)
                            x, y, width, height = box
                            line = Line(name, x-rect[0], y-rect[1], width, height)
                            lines.append(line)
                            references[id(line)] = (obj, child_id, name, box)
                queue.extend(self.children(obj, child_id))
            except Exception:
                continue
        if queue:
            return None  # A partial tree must not be mistaken for a valid layout.
        return sorted(lines, key=lambda line: (line.y, line.x)), references, visited


def scan_accessible(title, people, aliases, hwnd, monitor=None):
    import uiautomation as auto
    import win32gui
    from .desktop import resolve_people, title_matches

    started = time.monotonic()
    outer = win32gui.GetWindowRect(hwnd)
    scale = ctypes.windll.user32.GetDpiForWindow(hwnd) / 96 or 1
    with auto.UIAutomationInitializerInThread():
        reader = getattr(_thread, 'reader', None)
        if reader is None:
            reader = _thread.reader = NativeReader()
        revision = monitor.state()[0] if monitor else None
        key = (hwnd, outer, title, revision)
        cached = getattr(_thread, 'layout', None)
        reused = bool(cached and cached['key'] == key and
                      started-cached['at'] < 5 and references_unchanged(cached['result'][1]))
        if reused:
            chat, result = cached['chat'], cached['result']
        else:
            _thread.layout = None
            chat = reader.chat(hwnd)
            if not chat:
                return None
            result = reader.text(chat)
            if (result is None and cached and cached['key'] == key
                    and references_unchanged(cached['result'][1])):
                # A busy client must not blink previously verified labels.
                chat, result, reused = cached['chat'], cached['result'], True
            if result is not None:
                _thread.layout = {'key':key, 'at':started, 'chat':chat, 'result':result}
        rect = chat[2]
        if monitor:
            monitor.watch(hwnd, rect)
        if result is None:
            return {'state': 'accessibility_busy', 'anchors': [], 'backend': 'msaa'}
        lines, references, visited = result
        if not title_matches(lines, title, scale):
            return {'state': 'other_chat', 'anchors': [], 'backend': 'msaa'}
        anchors = resolve_people(lines, people, aliases=aliases, scale=scale)
        # Recheck only text supporting the labels. This catches inertial motion
        # or a group switch during the read without comparing screen pixels.
        checked = [line for line in lines if title_matches([line], title, scale)]
        checked.extend(anchor['line'] for anchor in anchors)
        for line in checked:
            obj, child_id, name, box = references[id(line)]
            if tuple(obj.accLocation(child_id)) != box or obj.accName[child_id] != name:
                return {'state': 'moving', 'anchors': [], 'backend': 'msaa'}
        for anchor in anchors:
            anchor['font_pixels'] = max(round(9*scale), round(anchor['height']/1.2))
            anchor['x'] += rect[0]
            anchor['y'] += rect[1]
            anchor['right_limit'] = rect[2] - 10*scale
            anchor.pop('line', None)
    if (win32gui.GetForegroundWindow() != hwnd or win32gui.IsIconic(hwnd)
            or win32gui.GetWindowRect(hwnd) != outer):
        return {'state': 'background', 'anchors': [], 'backend': 'msaa'}
    return {'state': 'ready', 'backend': 'msaa', 'anchors': anchors, 'hwnd': hwnd,
            'rect': rect, 'window_rect': outer, 'scale': scale,
            'captured_at': time.monotonic(), 'scan_seconds': time.monotonic()-started,
            'nodes': visited, 'reused_layout':reused}
