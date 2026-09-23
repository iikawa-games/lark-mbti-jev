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

    def text(self, chat, *, max_nodes=4000):
        from .desktop import Line
        root, child_id, rect = chat
        # Also read names just outside the view, so their labels are ready to
        # scroll in with the text instead of waiting for the next full read.
        margin = (rect[3] - rect[1]) // 2
        clip = (rect[0], rect[1] - margin, rect[2], rect[3] + margin)
        queue = deque([(root, child_id, None, -1)])
        # A maximized window or a busy client can exceed a tight budget; a
        # partial read is discarded, so leave room for a complete one.
        deadline = time.monotonic() + 2
        lines, references, seen, viewports = [], {}, set(), []
        visited = 0
        while queue and visited < max_nodes and time.monotonic() < deadline:
            obj, child_id, parent, list_id = queue.popleft()
            visited += 1
            try:
                box = tuple(obj.accLocation(child_id))
                # Content taller than its container is a scrolling list; the
                # container is the visible band. Text inside belongs to the list.
                if parent and box[3] > parent[3] + 2 and box[1] <= parent[1] and intersects(parent, rect):
                    known = [item[0] for item in viewports]
                    if parent not in known:
                        viewports.append((parent, box))
                    list_id = known.index(parent) if parent in known else len(viewports) - 1
                # Far-offscreen messages can remain in the virtual accessibility tree.
                if box[2] > 0 and box[3] > 0 and not intersects(box, clip):
                    continue
                role = obj.accRole[child_id]
                if role in (40, 41, 42, 43):
                    name = obj.accName[child_id] or ''
                    if name and len(name) <= 2000 and intersects(box, clip):
                        key = (name, box)
                        if key not in seen:
                            seen.add(key)
                            x, y, width, height = box
                            line = Line(name, x-rect[0], y-rect[1], width, height, (), list_id)
                            lines.append(line)
                            references[id(line)] = (obj, child_id, name, box)
                queue.extend((child, child_child, box, list_id) for child, child_child in self.children(obj, child_id))
            except Exception:
                continue
        if queue:
            return None  # A partial tree must not be mistaken for a valid layout.
        return sorted(lines, key=lambda line: (line.y, line.x)), references, visited, viewports


def message_viewport(viewports, lines, rect, scale):
    """The message list: visible band (top, bottom), content (top, bottom), list id."""
    if not viewports:
        # Older layouts: below the chat header, above the compose box.
        return (rect[1] + 100 * scale, rect[3] - 60 * scale), None, None
    counts = {}
    for line in lines:
        counts[line.list_id] = counts.get(line.list_id, 0) + 1
    index = max(range(len(viewports)), key=lambda i: (counts.get(i, 0), viewports[i][0][3]))
    box, content = viewports[index]
    return (box[1], box[1] + box[3]), (content[1], content[1] + content[3]), index


def track_positions(wheels_since=None, predictor=None):
    """Label positions for this frame; cheap enough to run per frame.

    Re-reads only the label anchors and, while the wheel is turning, predicts
    the motion the accessibility tree has not reported yet. Runs on the
    scanner thread, which owns the COM references.
    """
    from .motion import WheelPredictor, predicted_positions
    tracked = getattr(_thread, 'tracked', None)
    if not tracked:
        return None
    current = []
    for obj, child_id, dx, dy in tracked['refs']:
        try:
            x, y, width, height = obj.accLocation(child_id)
            current.append((x + dx, y + dy, height) if width > 0 and height > 0 else None)
        except Exception:
            current.append(None)
    now = time.monotonic()
    wheels = wheels_since(tracked['since']) if wheels_since else []
    return predicted_positions(tracked, current, wheels, now, predictor or WheelPredictor(tracked['scale']))


def scan_accessible(title, people, aliases, hwnd, monitor=None):
    import uiautomation as auto
    import win32gui
    from .desktop import resolve_people, title_matches
    from .typography import accessible_font_pixels

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
        # A read without the scrolling list (e.g. just after Feishu is restored,
        # before layout) disables scroll following, so it is kept only briefly.
        ttl = 5 if cached and cached['result'][3] else 1
        reused = bool(cached and cached['key'] == key and
                      started-cached['at'] < ttl and references_unchanged(cached['result'][1]))
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
        lines, references, visited, viewports = result
        if not title_matches(lines, title, scale):
            return {'state': 'other_chat', 'anchors': [], 'backend': 'msaa'}
        band, content, list_id = message_viewport(viewports, lines, rect, scale)
        if monitor and content is not None:
            monitor.watch(hwnd, rect, (rect[0], band[0], rect[2], band[1]))
        height = rect[3] - rect[1]
        # Senders come only from the scrolling list, never the fixed header or
        # pinned bar; names beyond the view are kept for scrolling in.
        row_ok = ((lambda line: line.list_id == list_id) if list_id is not None
                  else (lambda line: 120 * scale <= line.y <= height))
        anchors = resolve_people(lines, people, aliases=aliases, scale=scale, row_ok=row_ok)
        # Recheck only text supporting the labels. This catches inertial motion
        # or a group switch during the read without comparing screen pixels.
        checked = [line for line in lines if title_matches([line], title, scale)]
        checked.extend(anchor['line'] for anchor in anchors)
        for line in checked:
            obj, child_id, name, box = references[id(line)]
            if tuple(obj.accLocation(child_id)) != box or obj.accName[child_id] != name:
                return {'state': 'moving', 'anchors': [], 'backend': 'msaa'}
        refs = []
        for anchor in anchors:
            line = anchor.pop('line')
            anchor['font_pixels'] = accessible_font_pixels(anchor['height'], scale)
            anchor['x'] += rect[0]
            anchor['y'] += rect[1]
            anchor['right_limit'] = rect[2] - 10*scale
            # Names under the header, compose box or beyond the view wait parked.
            anchor['visible'] = band[0] <= anchor['y'] and anchor['y'] + anchor['height'] <= band[1]
            obj, child_id, _, box = references[id(line)]
            anchor['track'] = len(refs)
            refs.append((obj, child_id, anchor['x'] - box[0], anchor['y'] - box[1]))
    if (win32gui.GetForegroundWindow() != hwnd or win32gui.IsIconic(hwnd)
            or win32gui.GetWindowRect(hwnd) != outer):
        return {'state': 'background', 'anchors': [], 'backend': 'msaa'}
    _thread.tracked = {'refs': refs, 'band': band, 'content': content, 'scale': scale, 'since': started,
                       'base': [(anchor['x'], anchor['y'], anchor['height']) for anchor in anchors]}
    return {'state': 'ready', 'backend': 'msaa', 'anchors': anchors, 'hwnd': hwnd,
            'rect': rect, 'window_rect': outer, 'scale': scale,
            'captured_at': time.monotonic(), 'scan_seconds': time.monotonic()-started,
            'nodes': visited, 'reused_layout':reused}
