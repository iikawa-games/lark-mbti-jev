"""Desktop control panel and foreground-only, click-through labels."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime, timedelta, timezone

from .desktop import dpi_aware, foreground_feishu, scan_chat, screen_overlay
from .lark import search_chats, fetch_history, fetch_person_history, cancel_reads, LarkError
from .store import read_json, write_json, ROOT
from .classifier import classify
from .presentation import format_profile, is_current_profile
from .message_cache import MessageCache
from .typography import badge_font_pixels, badge_top
from .scroll import ScrollMonitor
from .tray import Tray, show_running_panel
from .themes import character, ensure_custom_folder, load_themes

DEFAULT_CHAT = {'chat_id': '', 'name': ''}
TITLE = '飞书 MBTI · 本机标签'
WORKERS = 4
PREWARM = 30  # most recent speakers analyzed before they scroll into view
RETRY_FAILED_SECONDS = 60
# Bumped when history parsing keeps more message kinds; triggers one full re-read.
HISTORY_FORMAT = 2
PARKED = (-32000, -32000)  # Off-screen spot for labels whose names are out of view
BG, FG, MUTED, BLUE = '#f6f8fb', '#202633', '#677286', '#3370ff'


class Badges:
    def __init__(self, root):
        self.root = root
        self.windows = []
        self.signature = None
        self.pool = {}
        self.slots = {}  # scan anchor 'track' index -> pool entry, for scrolling
        self.native = {}  # track index -> (hwnd, label height); read by the scanner thread
        self.formatter = format_profile  # The app swaps in its theme-aware formatter.

    def clear(self):
        for win in self.windows:
            win.withdraw()
        self.windows.clear()
        self.signature = None
        self.slots = {}
        self.native = {}

    def _place(self, entry, position):
        import win32gui
        import win32con
        win = entry['window']
        if entry.get('position') != position or win not in self.windows:
            win.geometry(f'+{position[0]}+{position[1]}')
            entry['position'] = position
        hwnd = entry['hwnd'] = win32gui.GetParent(win.winfo_id()) or win.winfo_id()
        if not entry.get('native_ready'):
            styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, styles | win32con.WS_EX_LAYERED |
                                  win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOOLWINDOW | 0x08000000)
            entry['native_ready'] = True
        if win not in self.windows:
            win.deiconify()
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                 win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
            self.windows.append(win)

    def move_native(self, positions):
        """Scanner thread: move label windows the moment new positions are read.

        Asynchronous Win32 calls never wait for the Tk thread. Tk's own state is
        reconciled afterwards by move() on the Tk thread.
        """
        user32 = ctypes.windll.user32
        native = self.native
        for item in positions:
            slot = native.get(item['index'])
            if slot is None:
                continue
            hwnd, height = slot
            # Park hidden labels off-screen instead of hiding them: show state
            # stays owned by Tk, so a skipped frame can never strand a label.
            x, y = (int(item['x']), badge_top(item, height)) if item['visible'] else (-32000, -32000)
            # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
            user32.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x4015)

    def move(self, positions):
        """Tk thread: record tracked positions; labels off the list stay parked."""
        self.signature = None  # The next full scan re-applies everything.
        for item in positions:
            entry = self.slots.get(item['index'])
            if entry is None:
                continue
            position = ((int(item['x']), badge_top(item, entry['label'].winfo_reqheight()))
                        if item['visible'] else PARKED)
            if entry['window'] in self.windows:
                entry['position'] = position  # Already moved natively.
            else:
                self._place(entry, position)

    def show(self, anchors, profiles, scale):
        signature = tuple((a['id'], round(a['x']), round(a['y']), round(a['height']),
                           a.get('right_limit'), a.get('font_pixels'), scale,
                           self.formatter(profiles.get(a['id']))) for a in anchors)
        if self.signature == signature:
            return
        self.signature = signature
        previous = self.windows
        self.windows = []
        self.slots = {}
        used = set()
        occurrences = {}
        for anchor in anchors:
            occurrence = occurrences.get(anchor['id'], 0)
            occurrences[anchor['id']] = occurrence + 1
            key = (anchor['id'], occurrence)
            used.add(key)
            profile = profiles.get(anchor['id'])
            text = self.formatter(profile)
            pixels = anchor.get('font_pixels') or badge_font_pixels(anchor['height'], scale, anchor.get('display_name', ''))
            entry = self.pool.get(key)
            if entry is None:
                win = tk.Toplevel(self.root)
                win.withdraw()
                win.overrideredirect(True)
                win.configure(bg='#010203')
                win.attributes('-transparentcolor', '#010203')
                win.attributes('-topmost', True)
                win.wm_attributes('-toolwindow', True)
                label_widget = tk.Label(win, bg='#f2f4f7', fg='#646a73',
                                        anchor='center', justify='center', bd=0,
                                        highlightthickness=0, pady=0)
                label_widget.pack()
                entry = self.pool[key] = {'window': win, 'label': label_widget}
            win, label_widget = entry['window'], entry['label']
            if entry.get('style') != (text, pixels):
                label_widget.configure(text=text, font=('Segoe UI', -pixels),
                                       padx=max(2, round(pixels * .2)))
                win.update_idletasks()
                entry['style'] = (text, pixels)
            if anchor['x'] + label_widget.winfo_reqwidth() > anchor.get('right_limit', float('inf')):
                win.withdraw()
                continue
            if win in previous:
                self.windows.append(win)  # Already shown: _place only moves it.
            # Off-view names keep a mapped label parked off-screen, so the
            # scanner can slide it in without waiting for the Tk thread.
            self._place(entry, (int(anchor['x']), badge_top(anchor, label_widget.winfo_reqheight(), scale))
                        if anchor.get('visible', True) else PARKED)
            if 'track' in anchor:
                self.slots[anchor['track']] = entry
        for win in previous:
            if win not in self.windows:
                win.withdraw()
        self.native = {index: (entry['hwnd'], entry['label'].winfo_reqheight())
                       for index, entry in self.slots.items()}
        # Bound native resources even after browsing many different senders.
        for key in list(self.pool):
            if len(self.pool) <= max(32, len(used)):
                break
            if key not in used:
                self.pool.pop(key)['window'].destroy()


class App:
    def __init__(self, root, *, auto=False):
        self.root = root
        self.config = read_json('settings.json', {'chat': DEFAULT_CHAT, 'aliases': {}})
        self.chat = self.config.get('chat', DEFAULT_CHAT)
        self.cache = read_json('profiles.json', {})
        self.profiles = self.cache.get(self.chat['chat_id'], {})
        self.messages = MessageCache()
        self.people = self.cached_people()
        self.cache_lock = threading.RLock()
        self.events = queue.Queue()
        self.jobs = queue.PriorityQueue()
        self.job_seq = 0
        self.pending_jobs = {}
        self.running = set()
        self.commands = queue.Queue()
        self.pending = {}  # uid -> queued priority (0 = on screen, 1 = prewarm)
        self.failed = {}  # uid -> monotonic time of the failure
        self.hydrated = set()
        self.generation = 0
        self.enabled = False
        self.loading = False
        self.closed = threading.Event()
        self.scan_request = threading.Event()
        self.scan_state = None
        self.scroll = ScrollMonitor()
        self.scroll_revision = 0
        self.last_snapshot = 0
        self.last_fetch = 0
        self.poll_since = self.config.get('poll_cursors', {}).get(self.chat['chat_id']) if self.people else None
        self.badges = Badges(root)
        self.themes = load_themes()
        self.theme_id = self.config.get('theme', '') if self.config.get('theme') in self.themes else ''
        self.badges.formatter = self.format
        self.build_ui()
        self.tray = Tray(self.commands.put, TITLE)
        self.sync_tray_themes()
        self.status.trace_add('write', lambda *_: self.tray.set_tooltip('飞书 MBTI · ' + self.status.get()))
        threading.Thread(target=self.scanner, daemon=True, name='desktop-ocr').start()
        # Parallel workers: each new member needs a lark-cli read and a Jev call.
        for index in range(WORKERS):
            threading.Thread(target=self.classifier_worker, daemon=True, name=f'jev-classifier-{index}').start()
        self.root.after(100, self.tick)
        # Closing the panel only hides it; the tray menu quits.
        self.root.protocol('WM_DELETE_WINDOW', self.hide_panel)
        if auto and self.chat.get('chat_id'):
            # Start quietly in the tray once a group has been chosen.
            self.root.withdraw()
            self.root.after(200, self.enable)

    def cached_people(self):
        if not self.chat.get('chat_id'):
            return {}
        people = self.messages.people(self.chat['chat_id'])
        # Profiles survive a history-window change. Their exact saved identity
        # can still anchor a label while the person's older text is filled in.
        for uid, profile in self.profiles.items():
            people.setdefault(uid, {'id': uid, 'name': profile.get('name', uid), 'messages': []})
        return people

    def build_ui(self):
        self.root.title(TITLE)
        self.root.geometry('640x560')
        self.root.minsize(520, 460)
        self.root.configure(bg=BG)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background=BG)
        style.configure('TLabel', background=BG, foreground=FG, font=('Microsoft YaHei UI', 10))
        style.configure('TButton', font=('Microsoft YaHei UI', 10), padding=(12, 7))
        style.configure('Treeview', rowheight=26, font=('Microsoft YaHei UI', 10), background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'), padding=7)
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='姓名旁显示 MBTI + 模型概率；双击成员查看四个维度。关闭窗口后在托盘继续运行。',
                  foreground=MUTED, wraplength=600).pack(anchor='w', pady=(0, 10))
        search = ttk.Frame(outer)
        search.pack(fill='x')
        self.query = tk.StringVar(value=self.chat['name'])
        entry = ttk.Entry(search, textvariable=self.query, font=('Microsoft YaHei UI', 11))
        entry.pack(side='left', fill='x', expand=True, ipady=5)
        entry.bind('<Return>', lambda _: self.search())
        ttk.Button(search, text='选择群聊', command=self.search).pack(side='left', padx=(8, 0))
        self.chat_label = ttk.Label(outer, text='当前群：' + (self.chat['name'] or '尚未选择'), foreground=MUTED)
        self.chat_label.pack(anchor='w', pady=(8, 6))
        self.capture_label = ttk.Label(outer, text='姓名定位：等待打开群聊', foreground=MUTED)
        self.capture_label.pack(anchor='w', pady=(0, 8))
        theme_row = ttk.Frame(outer)
        theme_row.pack(fill='x', pady=(0, 8))
        ttk.Label(theme_row, text='标签显示：').pack(side='left')
        self.theme_choice = ttk.Combobox(theme_row, state='readonly', width=24, postcommand=self.reload_themes)
        self.theme_choice.pack(side='left')
        self.theme_choice.bind('<<ComboboxSelected>>', lambda _: self.set_theme(
            self.theme_ids[self.theme_choice.current()]))
        ttk.Button(theme_row, text='自定义主题…', command=self.open_theme_folder).pack(side='left', padx=8)
        actions = ttk.Frame(outer)
        actions.pack(fill='x')
        self.toggle_button = ttk.Button(actions, text='开启标签', command=self.toggle)
        self.toggle_button.pack(side='left')
        ttk.Button(actions, text='刷新聊天', command=self.refresh).pack(side='left', padx=8)
        ttk.Button(actions, text='在飞书打开', command=self.open_chat).pack(side='left')
        ttk.Button(actions, text='清除缓存', command=self.clear).pack(side='right')
        self.status = tk.StringVar(value='输入群名并点击“选择群聊”，选择后再开启标签。' if not self.chat.get('chat_id')
                                  else '准备就绪。开启后切换到飞书群聊即可看到标签。')
        ttk.Label(outer, textvariable=self.status, foreground=BLUE, wraplength=600).pack(anchor='w', pady=10)
        columns = ('name', 'label', 'sample', 'updated')
        self.table = ttk.Treeview(outer, columns=columns, show='headings', selectmode='browse', height=5)
        for key, title, width in [('name', '成员', 180), ('label', 'MBTI 推测', 100), ('sample', '缓存文本', 70), ('updated', '更新', 100)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=60)
        self.table.pack(fill='both', expand=True)
        self.table.bind('<Double-1>', lambda _: self.detail())
        self.fill_theme_choice()
        bottom = ttk.Frame(outer)
        bottom.pack(fill='x', pady=(10, 0))
        ttk.Button(bottom, text='查看维度', command=self.detail).pack(side='left')
        ttk.Button(bottom, text='补充群昵称', command=self.add_alias).pack(side='left', padx=8)
        ttk.Button(bottom, text='退出程序', command=self.close).pack(side='right')
        ttk.Label(outer, text='结果自动缓存 · 同一成员新增超过 10 条文本才重新判定\n每人保留最多 1,000 条文本，仅你可见；判定样本发往已配置的 Jev。', foreground=MUTED,
                  font=('Microsoft YaHei UI', 9), wraplength=600).pack(anchor='w', pady=(10, 0))
        self.render_table()

    def background(self, fn, event, generation=None):
        generation = self.generation if generation is None else generation
        def run():
            try:
                value = fn()
                self.events.put((event, generation, value))
            except Exception as exc:
                # Domain exceptions deliberately contain no raw request/response data.
                detail = str(exc) if isinstance(exc, (LarkError, ValueError)) else type(exc).__name__
                self.events.put((event + '_error', generation, detail[:220]))
        threading.Thread(target=run, daemon=True).start()

    def search(self):
        self.status.set('正在查找群聊…')
        query = self.query.get()
        self.background(lambda: search_chats(query), 'search')

    def choose_chat(self, chats):
        if not chats:
            self.status.set('没有找到群聊，请检查名称。')
            return
        popup = tk.Toplevel(self.root)
        popup.title('选择群聊')
        popup.geometry('520x290')
        listing = tk.Listbox(popup, font=('Microsoft YaHei UI', 11))
        listing.pack(fill='both', expand=True, padx=14, pady=14)
        for chat in chats:
            listing.insert('end', chat['name'])
        def select():
            indices = listing.curselection()
            if not indices:
                return
            chat = chats[indices[0]]
            with self.cache_lock:
                self.generation += 1
                self.chat = {'chat_id': chat['chat_id'], 'name': chat['name']}
                self.profiles = self.cache.get(chat['chat_id'], {})
                self.people = self.cached_people()
                self.poll_since = self.config.get('poll_cursors', {}).get(chat['chat_id']) if self.people else None
            self.badges.clear()
            self.pending.clear()
            self.failed.clear()
            self.hydrated.clear()
            self.loading = False
            self.config['chat'] = self.chat
            write_json('settings.json', self.config)
            self.chat_label.config(text='当前群：' + chat['name'])
            self.render_table()
            popup.destroy()
            self.refresh(incremental=True)
        ttk.Button(popup, text='使用此群', command=select).pack(pady=(0, 14))
        listing.bind('<Double-1>', lambda _: select())

    def toggle(self):
        if self.enabled:
            self.enabled = False
            self.badges.clear()
            self.toggle_button.config(text='开启标签')
            self.tray.enabled = False
            self.status.set('标签已暂停。')
        else:
            self.enable()

    def enable(self):
        if not self.chat.get('chat_id'):
            self.status.set('请先输入群名并点击“选择群聊”。')
            return
        try:
            self.scroll.start()
        except RuntimeError as exc:
            self.status.set(str(exc))
            return
        self.enabled = True
        self.tray.enabled = True
        self.toggle_button.config(text='暂停标签')
        self.refresh(incremental=self.config.get('history_format') == HISTORY_FORMAT)

    def refresh(self, *, incremental=False):
        if not self.chat.get('chat_id'):
            self.status.set('请先选择群聊。')
            return
        if self.loading:
            return
        self.loading = True
        self.failed.clear()
        start = self.poll_since if incremental else None
        self.last_fetch = time.monotonic()
        self.status.set('正在同步新消息，已有标签继续显示…' if start else '正在补充当前群近 90 天文本，最多 1,000 条…')
        chat_id = self.chat['chat_id']
        self.background(lambda: dict(fetch_history(chat_id, days=90, pages=20, start=start), full=start is None), 'history')

    def open_chat(self):
        if not self.chat.get('chat_id'):
            self.status.set('请先选择群聊。')
            return
        os.startfile('feishu://applink.feishu.cn/client/chat/open?openChatId=' + self.chat['chat_id'])

    def request_profile(self, uid, priority=0):
        if uid in self.pending:
            if priority < self.pending[uid]:
                # A prewarm job scrolled into view: queue it again ahead of others.
                self.pending[uid] = priority
                self.job_seq += 1
                self.jobs.put((priority, self.job_seq, self.pending_jobs[uid]))
            return
        if uid in self.failed:
            if time.monotonic() - self.failed[uid] < RETRY_FAILED_SECONDS:
                return
            del self.failed[uid]
        existing = self.profiles.get(uid, {})
        person = self.people.get(uid)
        hydrate = person and not person['messages'] and uid not in self.hydrated
        if not hydrate and not self.messages.needs_classification(self.chat['chat_id'], uid, existing):
            return
        if hydrate:
            self.hydrated.add(uid)
        self.pending[uid] = priority
        job = (self.generation, self.chat['chat_id'], uid, existing)
        self.pending_jobs[uid] = job
        self.job_seq += 1
        self.jobs.put((priority, self.job_seq, job))

    def prewarm(self):
        """Analyze the most recent speakers before they appear on screen."""
        def latest(person):
            return max((str(message.get('timestamp', '')) for message in person['messages']), default='')
        recent = sorted((person for person in self.people.values() if person['messages']),
                        key=latest, reverse=True)
        for person in recent[:PREWARM]:
            self.request_profile(person['id'], priority=1)

    def classify_cached(self, chat_id, uid):
        snapshot = self.messages.snapshot(chat_id, uid)
        result = classify(snapshot['person']['messages'], config={'timeout': 15, 'retries': 0})
        result.update({'id': uid, 'name': snapshot['person']['name'],
                       'cache_baseline': {key: snapshot[key] for key in ('through_seq', 'latest_timestamp')}})
        return result, snapshot

    def classifier_worker(self):
        while not self.closed.is_set():
            try:
                _, _, (generation, chat_id, uid, existing) = self.jobs.get(timeout=.4)
            except queue.Empty:
                continue
            if generation != self.generation:
                continue
            with self.cache_lock:
                # A member re-queued at a higher priority leaves a stale copy.
                if uid in self.running or uid not in self.pending:
                    continue
                self.running.add(uid)
            existing = self.profiles.get(uid, existing)
            try:
                self.analyze(generation, chat_id, uid, existing)
            except Exception as exc:
                self.events.put(('profile_error', generation, (uid, type(exc).__name__)))
            finally:
                with self.cache_lock:
                    self.running.discard(uid)

    def analyze(self, generation, chat_id, uid, existing):
        person = self.messages.person(chat_id, uid)
        if not person:
            person = {'id': uid, 'name': existing.get('name', uid), 'messages': []}
        quick = None
        # Bootstrap an unseen member with a wider history. Subsequent
        # judgments use the accumulated cache without another sender query.
        if not is_current_profile(existing) or not person['messages']:
            if person['messages']:
                # The sender search takes seconds; answer from the group
                # history first and refine once the wider sample arrives.
                quick = self.classify_cached(chat_id, uid)
                if generation != self.generation or self.closed.is_set():
                    return
                self.events.put(('profile', generation, quick + (False,)))
            samples = fetch_person_history(chat_id, uid)
            with self.cache_lock:
                if generation != self.generation or self.closed.is_set():
                    return
                self.messages.merge_people(chat_id, {uid: dict(person, messages=samples)})
        if quick:
            grown = len(self.messages.snapshot(chat_id, uid)['person']['messages']) - len(quick[1]['person']['messages'])
            if grown < 10:
                self.events.put(('profile_skipped', generation, uid))
                return
        elif not self.messages.needs_classification(chat_id, uid, existing):
            self.events.put(('profile_skipped', generation, uid))
            return
        if generation != self.generation or self.closed.is_set():
            return
        result = self.classify_cached(chat_id, uid)
        if not self.closed.is_set():
            self.events.put(('profile', generation, result + (True,)))

    def scanner(self):
        import uiautomation as auto
        from .accessibility import clear_thread_cache
        with auto.UIAutomationInitializerInThread():
            try:
                self._scanner_loop()
            finally:
                clear_thread_cache()

    def _scanner_loop(self):
        from .accessibility import track_positions
        from .motion import WheelPredictor
        predictor = WheelPredictor()  # Keeps its learned notch distance.
        last, moving_until = None, 0
        while not self.closed.is_set():
            revision, settled = self.scroll.state()
            now = time.monotonic()
            # While scrolling (and briefly after, for inertia) re-read only the
            # label anchors so labels move with their names instead of hiding.
            if self.enabled and (not settled or now < moving_until):
                if self.scan_state:
                    predictor.scale = self.scan_state.get('scale', predictor.scale)
                positions = track_positions(self.scroll.wheels_since, predictor)
                if positions is not None and positions != last:
                    last, moving_until = positions, now + .3
                    self.badges.move_native(positions)
                    self.events.put(('track', self.generation, positions))
                time.sleep(.004)
                continue
            # Short wait: the first wheel notch must start tracking at once.
            if not self.scan_request.wait(.01):
                continue
            self.scan_request.clear()
            revision, settled = self.scroll.state()
            if not settled or screen_overlay():
                continue
            with self.cache_lock:
                if not self.enabled or not self.people:
                    continue
                generation = self.generation
                chat_id, title = self.chat['chat_id'], self.chat['name']
                people = dict(self.people)
                aliases = self.config.get('aliases', {}).get(chat_id, {})
            try:
                snapshot = scan_chat(title, people, aliases, monitor=self.scroll)
                snapshot['chat_id'] = chat_id
                snapshot['scroll_revision'] = revision
                self.events.put(('scan', generation, snapshot))
            except Exception as exc:
                self.events.put(('scan_error', generation, str(exc)[:160]))

    def tick(self):
        if self.closed.is_set():
            return
        try:
            while True:
                self.tray_command(self.commands.get_nowait())
                if self.closed.is_set():
                    return
        except queue.Empty:
            pass
        revision, settled = self.scroll.state()
        tracking = bool(self.scan_state and self.scan_state.get('backend') == 'msaa'
                        and self.scan_state.get('state') == 'ready')
        if revision != self.scroll_revision:
            self.scroll_revision = revision
            self.last_snapshot = 0
            if not tracking:
                # Screenshot mode cannot follow motion; hide until settled.
                self.badges.clear()
                self.scan_state = None
        try:
            while True:
                event, generation, data = self.events.get_nowait()
                if generation != self.generation:
                    continue
                if event == 'history':
                    self.loading = False
                    self.messages.merge_people(self.chat['chat_id'], data['people'])
                    self.people = self.cached_people()
                    self.last_fetch = time.monotonic()
                    # Older history being truncated does not block forward polling.
                    self.poll_since = (datetime.fromisoformat(data['started_at']) - timedelta(minutes=2)).isoformat(timespec='seconds')
                    self.config.setdefault('poll_cursors', {})[self.chat['chat_id']] = self.poll_since
                    if data.get('full'):
                        self.config['history_format'] = HISTORY_FORMAT
                    write_json('settings.json', self.config)
                    suffix = '（还有更早的消息）' if data['has_more'] else ''
                    total = sum(len(person['messages']) for person in self.people.values())
                    self.status.set(f'已缓存 {total:,} 条文本、{len(self.people)} 位发言者{suffix}；新增第 11 条时更新判定。')
                    self.render_table()
                    if self.enabled:
                        self.prewarm()
                elif event == 'search':
                    self.choose_chat(data)
                elif event == 'profile':
                    data, snapshot, final = data
                    if final:
                        self.pending.pop(data['id'], None)
                    self.profiles[data['id']] = data
                    self.cache[self.chat['chat_id']] = self.profiles
                    write_json('profiles.json', self.cache)
                    self.messages.mark_classified(self.chat['chat_id'], data['id'], snapshot)
                    self.refresh_person(data['id'])
                elif event == 'profile_skipped':
                    self.pending.pop(data, None)
                    self.refresh_person(data)
                elif event == 'profile_error':
                    uid, reason = data
                    self.pending.pop(uid, None)
                    self.failed[uid] = time.monotonic()
                    self.status.set('Jev 暂未完成分析（' + reason + '）。检查服务后点击刷新聊天重试。')
                elif event == 'scan':
                    if data.get('chat_id') != self.chat['chat_id']:
                        continue
                    if not self.scroll.accepts(data.get('scroll_revision')):
                        continue
                    if getattr(self, 'capture_label', None) is not None and data.get('backend'):
                        self.capture_label.config(text='姓名定位：桌面无障碍（无需截图）' if data['backend'] == 'msaa'
                                                  else '姓名定位：截图识别（兼容模式）')
                    if data['state'] == 'ready' and self.enabled and foreground_feishu() == data.get('hwnd'):
                        self.scan_state = data
                        self.learn_aliases(data['anchors'])
                        for anchor in data['anchors']:
                            self.request_profile(anchor['id'], priority=0 if anchor.get('visible', True) else 1)
                        display_profiles = {}
                        for anchor in data['anchors']:
                            uid = anchor['id']
                            profile = self.profiles.get(uid)
                            if is_current_profile(profile):
                                display_profiles[uid] = profile
                            elif uid in self.failed:
                                display_profiles[uid] = {'display_status': '分析失败'}
                            else:
                                display_profiles[uid] = {'display_status': '分析中'}
                        self.badges.show(data['anchors'], display_profiles, data['scale'])
                    elif data['state'] in ('moving', 'accessibility_busy') or screen_overlay():
                        # Transient: keep the verified labels and read again soon.
                        self.last_snapshot = 0
                    else:
                        self.scan_state = data
                        self.badges.clear()
                elif event == 'track':
                    # Only the newest positions matter; skip older frames.
                    if tracking and self.enabled:
                        latest = data
                        try:
                            while True:
                                kind, generation, value = self.events.queue[0]
                                if kind != 'track':
                                    break
                                self.events.get_nowait()
                                if generation == self.generation:
                                    latest = value
                        except IndexError:
                            pass
                        self.badges.move(latest)
                        self.scan_state['captured_at'] = time.monotonic()
                elif event == 'scan_error':
                    self.badges.clear()
                    self.status.set('文字定位暂不可用：' + data)
                elif event == 'history_error':
                    self.loading = False
                    self.status.set(data)
                elif event == 'search_error':
                    self.status.set(data)
        except queue.Empty:
            pass
        # Hide on focus change, movement, clicking and expiry between OCR snapshots.
        if self.badges.windows:
            import win32gui
            overlay = screen_overlay()
            if overlay and self.enabled and self.scan_state:
                # A screenshot tool is on top: keep labels exactly where they are.
                self.scan_state['captured_at'] = time.monotonic()
            elif not self.enabled or not self.scan_state or foreground_feishu() != self.scan_state.get('hwnd'):
                self.badges.clear()
            elif win32gui.GetWindowRect(self.scan_state['hwnd']) != self.scan_state.get('window_rect'):
                self.badges.clear()
            elif not tracking and ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000:
                self.badges.clear()
            elif time.monotonic() - self.scan_state.get('captured_at', 0) > (3 if tracking else 1.7):
                self.badges.clear()
        now = time.monotonic()
        # Accessibility reads are cheap and reuse the previous layout; OCR is not.
        interval = 1.2 if self.scan_state and self.scan_state.get('backend') == 'ocr' else .4
        if self.enabled and self.scroll.state()[1] and now-self.last_snapshot > interval:
            self.last_snapshot = now
            self.scan_request.set()
        if self.enabled and now-self.last_fetch > 60 and not self.loading:
            self.refresh(incremental=True)
        moving = not self.scroll.state()[1]
        self.root.after(15 if moving and tracking else 33 if self.badges.windows else 80, self.tick)

    def refresh_person(self, uid):
        # Re-read one member instead of the whole cache: this runs on the Tk
        # thread, which also moves the labels.
        person = self.messages.person(self.chat['chat_id'], uid)
        if person:
            self.people[uid] = person
        self.render_table()

    def render_table(self):
        if self.root.state() == 'withdrawn':
            self.table_stale = True  # Rebuilt when the panel is opened.
            return
        self.table_stale = False
        selection = self.table.selection()
        self.table.delete(*self.table.get_children())
        combined = dict(self.profiles)
        for uid, person in self.people.items():
            combined[uid] = dict(person, **{k:v for k,v in self.profiles.get(uid, {}).items() if k not in ('name', 'messages')})
        for uid, person in sorted(combined.items(), key=lambda pair: (not bool(pair[1].get('label')), pair[1].get('name', ''))):
            display = self.format(person) if 'label' in person else '看到时分析'
            try:
                updated = datetime.fromisoformat(person.get('updated_at', '').replace('Z', '+00:00')).astimezone().strftime('%m-%d %H:%M')
            except ValueError:
                updated = ''
            self.table.insert('', 'end', iid=uid, values=(person.get('name', uid), display, len(person.get('messages', [])), updated))
        if selection and self.table.exists(selection[0]):
            self.table.selection_set(selection[0])

    def detail(self):
        selected = self.table.selection()
        if not selected:
            return
        uid = selected[0]
        result = self.profiles.get(uid)
        if not is_current_profile(result):
            self.request_profile(uid)
            self.status.set('正在分析选中成员…')
            return
        rows = [result.get('name', ''), 'MBTI 推测：' + format_profile(result)]
        person = character(self.themes.get(self.theme_id), result.get('label'))
        if person:
            rows.append(f'{self.themes[self.theme_id]["name"]}主题：{person["name"]}'
                        + (f'（{person["series"]}）' if person['series'] else ''))
        rows.extend([f'有效文本样本：{result["sample_count"]} 条', ''])
        rows.extend([f'本群已缓存：{len(self.people.get(uid, {}).get("messages", []))} 条文本',
                     f'上次判定后新增：{self.messages.new_count(self.chat["chat_id"], uid, result)} 条（第 11 条更新）', ''])
        if result['status'] == 'insufficient':
            rows.append('暂未找到可用的文本聊天记录。')
        elif result['status'] == 'uncertain':
            rows.append('当前证据较弱，仍显示最可能的候选类型和模型概率。')
        for key, title in [('EI','外向 / 内向'),('SN','实感 / 直觉'),('TF','思考 / 情感'),('JP','判断 / 感知')]:
            dim = result.get('dimensions', {}).get(key, {})
            distribution = dim.get('probabilities', {})
            rows.append(title + '：' + '  '.join(f'{"待定" if option == "unknown" else option} {probability:.0%}' for option, probability in distribution.items()))
        rows.extend(['', '标签百分比来自 Jev 对 16 种 MBTI 的概率分布，不是四维概率的平均值。', '这是聊天风格推测，概率不是人格测量准确率。'])
        if person:
            rows.append('主题角色按 MBTI 类型对应，是娱乐性的归类。')
        messagebox.showinfo('四个维度', '\n'.join(rows), parent=self.root)

    def learn_aliases(self, anchors):
        """Remember a group nickname once its message text identified the sender."""
        aliases = self.config.setdefault('aliases', {}).setdefault(self.chat['chat_id'], {})
        changed = False
        for anchor in anchors:
            name = (anchor.get('display_name') or '').strip()
            if anchor.get('via_text') and name and name not in aliases.get(anchor['id'], []):
                aliases.setdefault(anchor['id'], []).append(name)
                changed = True
        if changed:
            write_json('settings.json', self.config)

    def add_alias(self):
        selected = self.table.selection()
        if not selected:
            self.status.set('先在列表中选中对应的成员。')
            return
        uid = selected[0]
        alias = simpledialog.askstring('补充群昵称', '输入这个人在当前群里的完整昵称：', parent=self.root)
        if alias and alias.strip():
            aliases = self.config.setdefault('aliases', {}).setdefault(self.chat['chat_id'], {}).setdefault(uid, [])
            if alias.strip() not in aliases:
                aliases.append(alias.strip())
            write_json('settings.json', self.config)
            self.status.set('昵称已保存，只用于当前群的姓名定位。')

    def clear(self):
        if not self.chat.get('chat_id'):
            self.status.set('还没有选择群聊，无需清除缓存。')
            return
        with self.cache_lock:
            self.generation += 1
            # Save removal first: interruption cannot revive a deleted label.
            self.cache.pop(self.chat['chat_id'], None)
            write_json('profiles.json', self.cache)
            self.messages.clear_chat(self.chat['chat_id'])
            self.people = {}
            self.poll_since = None
            self.profiles = {}
            self.enabled = False
        self.config.setdefault('poll_cursors', {}).pop(self.chat['chat_id'], None)
        write_json('settings.json', self.config)
        self.pending.clear()
        self.failed.clear()
        self.hydrated.clear()
        self.loading = False
        self.toggle_button.config(text='开启标签')
        self.badges.clear()
        self.render_table()
        self.status.set('当前群的推测结果与文本缓存已清除，标签已暂停。')

    def tray_command(self, command):
        if command == 'show':
            self.show_panel()
        elif command == 'toggle':
            self.toggle()
        elif command == 'refresh':
            self.refresh()
        elif command == 'quit':
            self.close()
        elif command.startswith('theme:'):
            self.set_theme(command[len('theme:'):])

    def format(self, profile, **options):
        return format_profile(profile, theme=self.themes.get(self.theme_id), **options)

    def fill_theme_choice(self):
        self.theme_ids = [''] + list(self.themes)
        self.theme_choice['values'] = ['MBTI（默认）'] + [
            theme['name'] + ('（自定义）' if key.startswith('custom:') else '') for key, theme in self.themes.items()]
        self.theme_choice.current(self.theme_ids.index(self.theme_id))
        theme = self.themes.get(self.theme_id)
        self.table.heading('label', text=theme['name'] + '角色' if theme else 'MBTI 推测')

    def reload_themes(self):
        # Pick up edits in the theme folder whenever the list is opened.
        self.themes = load_themes()
        if self.theme_id not in self.themes:
            self.theme_id = ''
        self.fill_theme_choice()
        self.sync_tray_themes()

    def sync_tray_themes(self):
        self.tray.themes = [('', 'MBTI（默认）')] + [(key, theme['name']) for key, theme in self.themes.items()]
        self.tray.theme = self.theme_id

    def set_theme(self, theme_id):
        if theme_id and theme_id not in self.themes:
            self.reload_themes()
            if theme_id not in self.themes:
                return
        self.theme_id = theme_id
        self.config['theme'] = theme_id
        write_json('settings.json', self.config)
        self.fill_theme_choice()
        self.sync_tray_themes()
        self.badges.signature = None  # Relabel visible names on the next read.
        self.last_snapshot = 0
        self.render_table()
        self.status.set('标签显示：' + (self.themes[theme_id]['name'] + ' 主题角色' if theme_id else 'MBTI'))

    def open_theme_folder(self):
        folder = ensure_custom_folder()
        self.status.set('在此文件夹中复制示例文件、改名并填写角色，保存后在“标签显示”中选择。')
        os.startfile(folder)

    def show_panel(self):
        self.root.deiconify()
        if getattr(self, 'table_stale', False):
            self.render_table()
        self.root.lift()
        self.root.focus_force()

    def hide_panel(self):
        self.root.withdraw()

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self.scroll.close()
        cancel_reads()
        self.badges.clear()
        self.tray.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--auto', action='store_true', help='Load selected group and begin labels')
    parser.add_argument('--smoke-seconds', type=int, default=0)
    args = parser.parse_args()
    dpi_aware()
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    mutex = kernel.CreateMutexW(None, False, 'Local\\FeishuMbti_' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16])
    already_running = ctypes.get_last_error() == 183
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    if already_running:
        # A second launch opens the running instance's panel from the tray.
        show_running_panel()
        kernel.CloseHandle(mutex)
        return
    root = tk.Tk()
    app = App(root, auto=args.auto)
    if args.smoke_seconds:
        root.after(args.smoke_seconds * 1000, app.close)
    try:
        root.mainloop()
    finally:
        if mutex:
            kernel.CloseHandle(mutex)


if __name__ == '__main__':
    main()
