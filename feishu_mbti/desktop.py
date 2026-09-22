"""Windows-only local OCR and transparent label placement. No screenshot uploads."""
from __future__ import annotations
import asyncio
import ctypes
import hashlib
import re
import time
from collections import defaultdict
from dataclasses import dataclass

_last_ocr = {'key': None, 'lines': []}


def dpi_aware():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass


def normalized(value):
    return ''.join(c.casefold() for c in value if c.isalnum())


def _compact(value):
    """Keep punctuation used as a visual boundary, dropping OCR whitespace only."""
    return ''.join(c.casefold() for c in value if not c.isspace())


def _prefix_right(line, target):
    """Return the visual right edge of a known text prefix from OCR word boxes."""
    if not line.words:
        return None
    wanted = _compact(target)
    consumed = ''
    for word, left, width in line.words:
        token = _compact(word)
        if not token:
            continue
        candidate = consumed + token
        if wanted.startswith(candidate):
            consumed = candidate
            if consumed == wanted:
                return left + width
            continue
        if candidate.startswith(wanted) and wanted.startswith(consumed):
            # OCR occasionally joins the name and status into one word. Chinese
            # glyphs are near-uniform here, so interpolate only inside that box.
            fraction = (len(wanted) - len(consumed)) / len(token)
            return left + width * fraction
        break
    return None


def _parenthetical_name_prefix(value):
    """Split ``name (group nickname) trailing status`` without guessing identity."""
    match = re.match(r'^(.*?[（(][^）)]*[）)])(.*)$', value)
    if match and match.group(2).strip():
        return match.group(1).strip()
    return value


@dataclass
class Line:
    text: str
    x: float
    y: float
    width: float
    height: float
    words: tuple = ()  # (text, left, width) in the same coordinate space


def resolve_people(lines, people, *, aliases=None, scale=1.5):
    """Require header geometry and exact identity evidence, never fuzzy-match names."""
    aliases = aliases or {}
    names = defaultdict(set)
    for uid, person in people.items():
        names[normalized(person['name'])].add(uid)
        for alias in aliases.get(uid, []):
            names[normalized(alias)].add(uid)
    result = []
    for index, line in enumerate(lines):
        # Feishu's sender column starts 60 DIP from the chat pane's left edge.
        if not (57 * scale <= line.x <= 69 * scale and
                120 * scale <= line.y and 7 * scale <= line.height <= 28 * scale):
            continue
        display_name = re.split(r'[|｜丨]', line.text, maxsplit=1)[0].strip()
        base = re.split(r'[（(]', display_name, maxsplit=1)[0].strip()
        display_matches = names.get(normalized(display_name), set())
        base_matches = names.get(normalized(base), set())
        matches = display_matches or base_matches
        matched_by_base = not display_matches and len(base_matches) == 1
        if len(matches) != 1:
            # A group nickname can differ from the directory name. Resolve it only
            # when the following full message text uniquely matches the API sample.
            following = []
            for item in lines[index + 1:index + 7]:
                if item.y - line.y >= 105 * scale:
                    break
                if 57 * scale <= item.x <= 69 * scale and 7 * scale <= item.height <= 28 * scale:
                    break
                if item.y > line.y and item.x >= line.x:
                    following.append(item)
            text_matches = set()
            for next_line in following:
                body = normalized(next_line.text)
                if len(body) < 10:
                    continue
                for uid, person in people.items():
                    if any(normalized(msg['text']) == body for msg in person['messages']):
                        text_matches.add(uid)
            matches = text_matches
        if len(matches) == 1:
            if matched_by_base:
                # Once the base name is already an exact identity match, a suffix
                # after the closing nickname parenthesis is UI status, not a name.
                display_name = _parenthetical_name_prefix(display_name)
            right = line.x + line.width
            word_right = _prefix_right(line, display_name)
            if word_right is not None:
                right = word_right
            same_row_status = [item for item in lines if
                item is not line
                and item.x >= right - 2 * scale
                and abs(item.y - line.y) <= max(4 * scale, min(item.height, line.height) / 2)
                and bool(normalized(item.text))
            ]
            # Preserve the name's center line; use space after the occupied header
            # when custom status text leaves no gap immediately after the name.
            row_right = max([right, line.x + line.width] + [item.x + item.width for item in same_row_status])
            result.append({'id': next(iter(matches)), 'display_name': display_name,
                           'x': row_right + 7 * scale, 'y': line.y,
                           'height': line.height, 'line': line})
    return result


def title_matches(lines, title, scale=1.5):
    expected = normalized(title)
    candidates = [normalized(line.text) for line in lines if line.y < 55 * scale and line.x < 400 * scale]
    return any(value == expected or re.fullmatch(re.escape(expected) + r'\d+(?:公开|私有)?', value) for value in candidates)


def label_regions_stable(before, after, lines, anchors, title, scale):
    """Ignore body animations while rejecting stale title and sender positions."""
    if before.size != after.size:
        return False
    regions = [(line, line.x + line.width) for line in lines
               if title_matches([line], title, scale)]
    if not regions:
        return False
    regions.extend((anchor['line'], anchor['x']) for anchor in anchors)
    for line, right in regions:
        box = (max(0, int(line.x) - 1), max(0, int(line.y) - 1),
               min(before.width, int(right + 2)),
               min(before.height, int(line.y + line.height + 2)))
        if box[2] <= box[0] or box[3] <= box[1]:
            return False
        if before.crop(box).tobytes() != after.crop(box).tobytes():
            return False
    return True


def foreground_feishu():
    import win32gui
    import win32process
    import win32api
    import win32con
    from ctypes import wintypes
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd or win32gui.IsIconic(hwnd):
        return None
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    try:
        process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            query = ctypes.windll.kernel32.QueryFullProcessImageNameW
            query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            query.restype = wintypes.BOOL
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if not query(wintypes.HANDLE(int(process)), 0, buffer, ctypes.byref(size)):
                return None
            path = buffer.value
        finally:
            process.Close()
    except Exception:
        return None
    if path.replace('\\', '/').rsplit('/', 1)[-1].lower() not in ('feishu.exe', 'lark.exe'):
        return None
    return hwnd


def chat_rectangle(hwnd):
    import uiautomation as auto
    queue = [auto.ControlFromHandle(hwnd)]
    count = 0
    deadline = time.monotonic() + 1.5
    while queue and count < 180 and time.monotonic() < deadline:
        item = queue.pop(0)
        count += 1
        if item.ClassName == 'MultiWebView' and 'messenger-chat:' in item.Name:
            rect = item.BoundingRectangle
            if rect.width() > 150 and rect.height() > 150:
                return (rect.left, rect.top, rect.right, rect.bottom)
        queue.extend(item.GetChildren())
    return None


async def recognize(image):
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    from winrt.windows.storage.streams import DataWriter
    from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
    if not OcrEngine.is_language_supported(Language('zh-Hans-CN')):
        raise RuntimeError('Windows 未安装中文 OCR。请在 Windows 设置 → 语言 → 中文 → 语言选项中安装文字识别。')
    engine = OcrEngine.try_create_from_language(Language('zh-Hans-CN'))
    rgba = image.convert('RGBA')
    writer = DataWriter()
    try:
        writer.write_bytes(rgba.tobytes())
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.RGBA8, rgba.width, rgba.height)
        try:
            result = await engine.recognize_async(bitmap)
            lines = []
            for line in result.lines:
                words = list(line.words)
                if not words:
                    continue
                left = min(w.bounding_rect.x for w in words)
                top = min(w.bounding_rect.y for w in words)
                right = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
                bottom = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
                lines.append(Line(line.text, left, top, right-left, bottom-top,
                                  tuple((w.text, w.bounding_rect.x, w.bounding_rect.width) for w in words)))
            return sorted(lines, key=lambda item: (item.y, item.x))
        finally:
            bitmap.close()
    finally:
        writer.close()


async def read_chat_image(image, scale):
    from PIL import Image
    # The Windows OCR engine is substantially more accurate on 2x chat text.
    factor = min(2.0, 3900 / max(image.size))
    enlarged = image.resize((int(image.width * factor), int(image.height * factor)), Image.Resampling.LANCZOS)
    raw = await recognize(enlarged)
    lines = [Line(x.text, x.x/factor, x.y/factor, x.width/factor, x.height/factor,
                  tuple((text, left/factor, width/factor) for text, left, width in x.words)) for x in raw]
    # Isolate sender column and increase contrast to remove pale tenant watermarks.
    left, top = int(56 * scale), int(120 * scale)
    right = min(image.width, int(440 * scale))
    if right > left and image.height > top:
        strip = image.crop((left, top, right, image.height)).convert('L')
        strip = strip.point(lambda value: 255 if value > 200 else 0).convert('RGB')
        raw_names = await recognize(strip.resize((int(strip.width * factor), int(strip.height * factor)), Image.Resampling.LANCZOS))
        for item in raw_names:
            item = Line(item.text, item.x/factor + left, item.y/factor + top, item.width/factor, item.height/factor,
                        tuple((text, word_left/factor + left, width/factor) for text, word_left, width in item.words))
            if 49 * scale <= item.x <= 69 * scale and item.height <= 28 * scale:
                lines = [old for old in lines if not (abs(old.y-item.y) < 6 * scale and abs(old.x-item.x) < 10 * scale)]
                lines.append(item)
    return sorted(lines, key=lambda item: (item.y, item.x))


def scan_chat(title, people, aliases=None, monitor=None):
    from PIL import ImageGrab
    import win32gui
    import uiautomation as auto
    hwnd = foreground_feishu()
    if not hwnd:
        return {'state': 'background', 'anchors': []}
    window_rect = win32gui.GetWindowRect(hwnd)
    with auto.UIAutomationInitializerInThread():
        rect = chat_rectangle(hwnd)
    if not rect:
        return {'state': 'no_chat', 'anchors': []}
    if monitor:
        monitor.watch(hwnd, rect)
    scale = ctypes.windll.user32.GetDpiForWindow(hwnd) / 96 or 1
    image = ImageGrab.grab(bbox=rect, all_screens=True)
    # The compose box has no sender labels; leave it out of OCR and change detection.
    if image.height > 300 * scale:
        image = image.crop((0, 0, image.width, int(image.height - 90 * scale)))
    key = (hwnd, image.size, scale, hashlib.blake2b(image.tobytes(), digest_size=16).digest())
    if _last_ocr['key'] == key:
        lines = _last_ocr['lines']
    else:
        lines = asyncio.run(read_chat_image(image, scale))
        _last_ocr.update(key=key, lines=lines)
    if not title_matches(lines, title, scale):
        return {'state': 'other_chat', 'anchors': []}
    anchors = resolve_people(lines, people, aliases=aliases, scale=scale)
    # Inertial scrolling can outlast the last wheel event. Check the pixels
    # supporting label placement, including cache hits. GIFs, avatars and other
    # animations elsewhere in the chat must not suppress stationary labels.
    time.sleep(.08)
    captured_at = time.monotonic()
    latest = ImageGrab.grab(bbox=rect, all_screens=True).crop((0, 0, image.width, image.height))
    if not label_regions_stable(image, latest, lines, anchors, title, scale):
        return {'state': 'moving', 'anchors': []}
    # Reject snapshots from a moved, minimized or switched window.
    if hwnd != win32gui.GetForegroundWindow() or win32gui.IsIconic(hwnd) or win32gui.GetWindowRect(hwnd) != window_rect:
        return {'state': 'background', 'anchors': []}
    for item in anchors:
        item['x'] += rect[0]
        item['y'] += rect[1]
        item['right_limit'] = rect[2] - 10 * scale
        item.pop('line', None)
    return {'state': 'ready', 'anchors': anchors, 'rect': rect, 'hwnd': hwnd,
            'scale': scale, 'window_rect': window_rect, 'captured_at': captured_at}
