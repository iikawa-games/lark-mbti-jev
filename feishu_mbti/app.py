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

from .desktop import dpi_aware, foreground_feishu, scan_chat
from .lark import search_chats, fetch_history, fetch_person_history, cancel_reads, LarkError
from .store import read_json, write_json, ROOT
from .classifier import classify
from .presentation import format_profile, is_current_profile
from .message_cache import MessageCache
from .typography import badge_font_pixels, badge_top
from .scroll import ScrollMonitor

DEFAULT_CHAT = {'chat_id': '', 'name': ''}
BG, FG, MUTED, BLUE = '#f6f8fb', '#202633', '#677286', '#3370ff'


class Badges:
    def __init__(self, root):
        self.root = root
        self.windows = []
        self.signature = None
        self.pool = {}

    def clear(self):
        for win in self.windows:
            win.withdraw()
        self.windows.clear()
        self.signature = None

    def show(self, anchors, profiles, scale):
        import win32gui
        import win32con
        signature = tuple((a['id'], round(a['x']), round(a['y']), round(a['height']),
                           a.get('right_limit'), a.get('font_pixels'), scale,
                           format_profile(profiles.get(a['id']))) for a in anchors)
        if self.signature == signature:
            return
        self.signature = signature
        visible = []
        used = set()
        occurrences = {}
        for anchor in anchors:
            occurrence = occurrences.get(anchor['id'], 0)
            occurrences[anchor['id']] = occurrence + 1
            key = (anchor['id'], occurrence)
            used.add(key)
            profile = profiles.get(anchor['id'])
            text = format_profile(profile)
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
                                       padx=max(3, round(pixels * .25)))
                win.update_idletasks()
                entry['style'] = (text, pixels)
            if anchor['x'] + label_widget.winfo_reqwidth() > anchor.get('right_limit', float('inf')):
                win.withdraw()
                continue
            top = badge_top(anchor, label_widget.winfo_reqheight(), scale)
            position = (int(anchor['x']), top)
            if entry.get('position') != position:
                win.geometry(f'+{position[0]}+{position[1]}')
                entry['position'] = position
            hwnd = win32gui.GetParent(win.winfo_id()) or win.winfo_id()
            if not entry.get('native_ready'):
                styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, styles | win32con.WS_EX_LAYERED |
                                      win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOOLWINDOW | 0x08000000)
                ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)
                entry['native_ready'] = True
            if win not in self.windows:
                win.deiconify()
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                     win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
            visible.append(win)
        for win in self.windows:
            if win not in visible:
                win.withdraw()
        self.windows = visible
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
        self.jobs = queue.Queue()
        self.pending = set()
        self.failed = set()
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
        self.build_ui()
        threading.Thread(target=self.scanner, daemon=True, name='desktop-ocr').start()
        threading.Thread(target=self.classifier_worker, daemon=True, name='jev-classifier').start()
        self.root.after(100, self.tick)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        if auto:
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
        self.root.title('飞书 MBTI · 本机标签')
        self.root.geometry('960x800')
        self.root.minsize(880, 740)
        self.root.configure(bg=BG)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background=BG)
        style.configure('TLabel', background=BG, foreground=FG, font=('Microsoft YaHei UI', 10))
        style.configure('TButton', font=('Microsoft YaHei UI', 10), padding=(12, 7))
        style.configure('Treeview', rowheight=31, font=('Microsoft YaHei UI', 10), background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'), padding=7)
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='聊天里，多一点理解', font=('Microsoft YaHei UI', 19, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='姓名旁显示 MBTI + 模型概率；点击下方成员查看四个维度。', foreground=MUTED).pack(anchor='w', pady=(5, 18))
        search = ttk.Frame(outer)
        search.pack(fill='x')
        self.query = tk.StringVar(value=self.chat['name'])
        entry = ttk.Entry(search, textvariable=self.query, font=('Microsoft YaHei UI', 11))
        entry.pack(side='left', fill='x', expand=True, ipady=5)
        entry.bind('<Return>', lambda _: self.search())
        ttk.Button(search, text='选择群聊', command=self.search).pack(side='left', padx=(8, 0))
        self.chat_label = ttk.Label(outer, text='当前群：' + (self.chat['name'] or '尚未选择'), foreground=MUTED)
        self.chat_label.pack(anchor='w', pady=(9, 15))
        self.capture_label = ttk.Label(outer, text='姓名定位：等待打开群聊', foreground=MUTED)
        self.capture_label.pack(anchor='w', pady=(0, 8))
        actions = ttk.Frame(outer)
        actions.pack(fill='x')
        self.toggle_button = ttk.Button(actions, text='开启标签', command=self.toggle)
        self.toggle_button.pack(side='left')
        ttk.Button(actions, text='刷新聊天', command=self.refresh).pack(side='left', padx=8)
        ttk.Button(actions, text='在飞书打开', command=self.open_chat).pack(side='left')
        ttk.Button(actions, text='清除缓存', command=self.clear).pack(side='right')
        self.status = tk.StringVar(value='输入群名并点击“选择群聊”，选择后再开启标签。' if not self.chat.get('chat_id')
                                  else '准备就绪。开启后切换到飞书群聊即可看到标签。')
        ttk.Label(outer, textvariable=self.status, foreground=BLUE, wraplength=700).pack(anchor='w', pady=14)
        columns = ('name', 'label', 'sample', 'updated')
        self.table = ttk.Treeview(outer, columns=columns, show='headings', selectmode='browse', height=5)
        for key, title, width in [('name', '成员', 220), ('label', 'MBTI 推测', 115), ('sample', '缓存文本', 85), ('updated', '更新', 125)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=60)
        self.table.pack(fill='both', expand=True)
        self.table.bind('<Double-1>', lambda _: self.detail())
        bottom = ttk.Frame(outer)
        bottom.pack(fill='x', pady=(12, 0))
        ttk.Button(bottom, text='查看维度', command=self.detail).pack(side='left')
        ttk.Button(bottom, text='补充群昵称', command=self.add_alias).pack(side='left', padx=8)
        ttk.Label(outer, text='结果自动缓存 · 同一成员新增超过 10 条文本才重新判定\n每人保留最多 1,000 条文本，仅你可见；判定样本发往已配置的 Jev。', foreground=MUTED,
                  font=('Microsoft YaHei UI', 9), wraplength=700).pack(anchor='w', pady=(12, 0))
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
        self.toggle_button.config(text='暂停标签')
        self.refresh(incremental=True)

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
        self.background(lambda: fetch_history(chat_id, days=90, pages=20, start=start), 'history')

    def open_chat(self):
        if not self.chat.get('chat_id'):
            self.status.set('请先选择群聊。')
            return
        os.startfile('feishu://applink.feishu.cn/client/chat/open?openChatId=' + self.chat['chat_id'])

    def request_profile(self, uid):
        if uid in self.pending or uid in self.failed:
            return
        existing = self.profiles.get(uid, {})
        person = self.people.get(uid)
        hydrate = person and not person['messages'] and uid not in self.hydrated
        if not hydrate and not self.messages.needs_classification(self.chat['chat_id'], uid, existing):
            return
        if hydrate:
            self.hydrated.add(uid)
        self.pending.add(uid)
        self.jobs.put((self.generation, self.chat['chat_id'], uid, existing))

    def classifier_worker(self):
        while not self.closed.is_set():
            try:
                generation, chat_id, uid, existing = self.jobs.get(timeout=.4)
            except queue.Empty:
                continue
            if generation != self.generation:
                continue
            try:
                person = self.messages.person(chat_id, uid)
                if not person:
                    person = {'id': uid, 'name': existing.get('name', uid), 'messages': []}
                # Bootstrap an unseen member with a wider history. Subsequent
                # judgments use the accumulated cache without another sender query.
                if not is_current_profile(existing) or not person['messages']:
                    samples = fetch_person_history(chat_id, uid)
                    with self.cache_lock:
                        if generation != self.generation or self.closed.is_set():
                            continue
                        self.messages.merge_people(chat_id, {uid: dict(person, messages=samples)})
                snapshot = self.messages.snapshot(chat_id, uid)
                if not self.messages.needs_classification(chat_id, uid, existing):
                    self.events.put(('profile_skipped', generation, uid))
                    continue
                if generation != self.generation or self.closed.is_set():
                    continue
                result = classify(snapshot['person']['messages'], config={'timeout': 15, 'retries': 0})
                result.update({'id': uid, 'name': snapshot['person']['name'],
                               'cache_baseline': {key: snapshot[key] for key in ('through_seq', 'latest_timestamp')}})
                if not self.closed.is_set():
                    self.events.put(('profile', generation, (result, snapshot)))
            except Exception as exc:
                self.events.put(('profile_error', generation, (uid, type(exc).__name__)))

    def scanner(self):
        import uiautomation as auto
        from .accessibility import clear_thread_cache
        with auto.UIAutomationInitializerInThread():
            try:
                self._scanner_loop()
            finally:
                clear_thread_cache()

    def _scanner_loop(self):
        while not self.closed.is_set():
            if not self.scan_request.wait(.3):
                continue
            self.scan_request.clear()
            revision, settled = self.scroll.state()
            if not settled:
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
        revision, settled = self.scroll.state()
        if revision != self.scroll_revision:
            self.scroll_revision = revision
            self.badges.clear()
            self.scan_state = None
            self.last_snapshot = 0
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
                    write_json('settings.json', self.config)
                    suffix = '（还有更早的消息）' if data['has_more'] else ''
                    total = sum(len(person['messages']) for person in self.people.values())
                    self.status.set(f'已缓存 {total:,} 条文本、{len(self.people)} 位发言者{suffix}；新增第 11 条时更新判定。')
                    self.render_table()
                elif event == 'search':
                    self.choose_chat(data)
                elif event == 'profile':
                    data, snapshot = data
                    self.pending.discard(data['id'])
                    self.profiles[data['id']] = data
                    self.cache[self.chat['chat_id']] = self.profiles
                    write_json('profiles.json', self.cache)
                    self.messages.mark_classified(self.chat['chat_id'], data['id'], snapshot)
                    self.people = self.cached_people()
                    self.render_table()
                elif event == 'profile_skipped':
                    self.pending.discard(data)
                    self.people = self.cached_people()
                    self.render_table()
                elif event == 'profile_error':
                    uid, reason = data
                    self.pending.discard(uid)
                    self.failed.add(uid)
                    self.status.set('Jev 暂未完成分析（' + reason + '）。检查服务后点击刷新聊天重试。')
                elif event == 'scan':
                    if data.get('chat_id') != self.chat['chat_id']:
                        continue
                    if not self.scroll.accepts(data.get('scroll_revision')):
                        continue
                    self.scan_state = data
                    if getattr(self, 'capture_label', None) is not None and data.get('backend'):
                        self.capture_label.config(text='姓名定位：桌面无障碍（无需截图）' if data['backend'] == 'msaa'
                                                  else '姓名定位：截图识别（兼容模式）')
                    if data['state'] == 'ready' and self.enabled and foreground_feishu() == data.get('hwnd'):
                        for anchor in data['anchors']:
                            self.request_profile(anchor['id'])
                        display_profiles = {}
                        for anchor in data['anchors']:
                            uid = anchor['id']
                            profile = self.profiles.get(uid)
                            if is_current_profile(profile):
                                display_profiles[uid] = profile
                            elif uid in self.failed:
                                display_profiles[uid] = {'display_status': '分析失败'}
                        self.badges.show(data['anchors'], display_profiles, data['scale'])
                    else:
                        self.badges.clear()
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
            if not self.enabled or not self.scan_state or foreground_feishu() != self.scan_state.get('hwnd'):
                self.badges.clear()
            elif win32gui.GetWindowRect(self.scan_state['hwnd']) != self.scan_state.get('window_rect'):
                self.badges.clear()
            elif ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000:
                self.badges.clear()
            elif time.monotonic() - self.scan_state.get('captured_at', 0) > (3 if self.scan_state.get('backend') == 'msaa' else 1.7):
                self.badges.clear()
        now = time.monotonic()
        if self.enabled and self.scroll.state()[1] and now-self.last_snapshot > 1.2:
            self.last_snapshot = now
            self.scan_request.set()
        if self.enabled and now-self.last_fetch > 60 and not self.loading:
            self.refresh(incremental=True)
        self.root.after(33 if self.badges.windows else 150, self.tick)

    def render_table(self):
        selection = self.table.selection()
        self.table.delete(*self.table.get_children())
        combined = dict(self.profiles)
        for uid, person in self.people.items():
            combined[uid] = dict(person, **{k:v for k,v in self.profiles.get(uid, {}).items() if k not in ('name', 'messages')})
        for uid, person in sorted(combined.items(), key=lambda pair: (not bool(pair[1].get('label')), pair[1].get('name', ''))):
            display = format_profile(person) if 'label' in person else '看到时分析'
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
        rows = [result.get('name', ''), 'MBTI 推测：' + format_profile(result), f'有效文本样本：{result["sample_count"]} 条', '']
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
        messagebox.showinfo('四个维度', '\n'.join(rows), parent=self.root)

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

    def close(self):
        self.closed.set()
        self.scroll.close()
        cancel_reads()
        self.badges.clear()
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
        import win32gui
        def restore(hwnd, _):
            if win32gui.GetWindowText(hwnd) == '飞书 MBTI · 本机标签':
                win32gui.ShowWindow(hwnd, 9)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass
        win32gui.EnumWindows(restore, None)
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
