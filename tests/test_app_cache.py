"""Exercise repeated visibility and worker dispatch without a desktop or network."""
from datetime import datetime, timezone
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from feishu_mbti.app import App
from feishu_mbti.message_cache import MessageCache
from feishu_mbti.classifier import RESULT_VERSION
from feishu_mbti.scroll import ScrollMonitor


class AppCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = App.__new__(App)
        self.app.messages = MessageCache(Path(self.temp.name) / 'messages.sqlite3')
        self.app.chat = {'chat_id': 'oc_test'}
        self.app.profiles = {'ou_a': {'label': 'INTP', 'probability': .65, 'status': 'estimated',
                                    'result_version': RESULT_VERSION,
                                    'updated_at': datetime.now(timezone.utc).isoformat()}}
        self.app.pending = {}
        self.app.pending_jobs = {}
        self.app.running = set()
        self.app.job_seq = 0
        self.app.failed = {}
        self.app.hydrated = set()
        self.app.generation = 1
        self.app.jobs = queue.PriorityQueue()
        self.app.events = queue.Queue()
        self.app.commands = queue.Queue()
        self.app.closed = threading.Event()
        self.app.cache_lock = threading.RLock()
        self.app.scroll = ScrollMonitor()
        self.app.scroll_revision = 0
        self.base = int(datetime.now(timezone.utc).timestamp()) - 1000
        self.merge(0, 3)
        self.app.messages.mark_classified('oc_test', 'ou_a', self.app.messages.snapshot('oc_test', 'ou_a'))
        self.app.people = self.app.cached_people()

    def merge(self, start, stop):
        self.app.messages.merge_people('oc_test', {'ou_a': {'id': 'ou_a', 'name': '测试成员', 'messages': [
            {'id': str(index), 'text': f'第 {index} 条独立消息', 'timestamp': self.base + index * 2}
            for index in range(start, stop)]}})

    def test_repeated_visibility_and_ten_messages_do_not_enqueue(self):
        for _ in range(50):
            self.app.request_profile('ou_a')
        self.assertTrue(self.app.jobs.empty())
        self.merge(3, 13)
        for _ in range(50):
            self.app.request_profile('ou_a')
        self.assertTrue(self.app.jobs.empty())
        self.merge(13, 14)
        for _ in range(50):
            self.app.request_profile('ou_a')
        self.assertEqual(self.app.jobs.qsize(), 1)

    def test_refresh_uses_cached_history_and_leaves_old_profile_visible(self):
        self.merge(3, 14)
        original = dict(self.app.profiles['ou_a'])
        self.app.request_profile('ou_a')
        with patch('feishu_mbti.app.fetch_person_history') as fetch, patch(
                'feishu_mbti.app.classify', return_value=dict(original)) as classify:
            worker = threading.Thread(target=self.app.classifier_worker)
            worker.start()
            try:
                event, generation, (result, snapshot, final) = self.app.events.get(timeout=3)
            finally:
                self.app.closed.set()
                worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        fetch.assert_not_called()
        self.assertEqual(classify.call_count, 1)
        self.assertEqual(len(classify.call_args.args[0]), 14)
        self.assertEqual(self.app.profiles['ou_a'], original)
        self.assertEqual(event, 'profile')
        self.assertEqual(result['cache_baseline']['through_seq'], snapshot['through_seq'])

    def test_saved_profile_identity_is_available_without_cached_text(self):
        self.app.messages.clear_chat('oc_test')
        self.app.people = self.app.cached_people()
        self.assertIn('ou_a', self.app.people)
        self.app.request_profile('ou_a')
        self.assertEqual(self.app.jobs.qsize(), 1)
        self.app.pending.clear()
        self.app.request_profile('ou_a')
        self.assertEqual(self.app.jobs.qsize(), 1)

    def prepare_tick(self):
        self.app.config = {}
        self.app.root = Mock()
        self.app.status = Mock()
        self.app.render_table = Mock()
        self.app.badges = Mock(windows=[])
        self.app.enabled = False
        self.app.loading = True
        self.app.scan_state = None

    def test_truncated_old_history_still_advances_forward_cursor(self):
        self.prepare_tick()
        self.app.events.put(('history', 1, {'people': {}, 'has_more': True,
                                          'message_count': 1000, 'started_at': '2026-09-22T12:00:00+00:00'}))
        with patch('feishu_mbti.app.write_json') as save:
            self.app.tick()
        self.assertEqual(self.app.poll_since, '2026-09-22T11:58:00+00:00')
        self.assertEqual(self.app.config['poll_cursors']['oc_test'], self.app.poll_since)
        self.assertFalse(self.app.loading)
        save.assert_called_once()

    def test_search_error_does_not_release_an_active_history_request(self):
        self.prepare_tick()
        self.app.events.put(('search_error', 1, 'Search unavailable'))
        self.app.tick()
        self.assertTrue(self.app.loading)

    def scan(self, state, anchors):
        self.prepare_tick()
        self.app.enabled = True
        self.app.last_snapshot = self.app.last_fetch = 10**9
        self.app.request_profile = Mock()
        self.app.scan_request = Mock()
        self.app.events.put(('scan', 1, {'chat_id': 'oc_test', 'scroll_revision': 0, 'state': state,
                                        'hwnd': 42, 'scale': 1, 'anchors': anchors}))
        with patch('feishu_mbti.app.foreground_feishu', return_value=42):
            self.app.tick()

    def test_unanalyzed_member_shows_progress_instead_of_nothing(self):
        self.scan('ready', [{'id': 'ou_a'}, {'id': 'ou_new'}])
        profiles = self.app.badges.show.call_args.args[1]
        self.assertEqual(profiles['ou_a']['label'], 'INTP')
        self.assertEqual(profiles['ou_new'], {'display_status': '分析中'})

    def test_transient_scan_keeps_labels(self):
        for state in ('moving', 'accessibility_busy'):
            self.scan(state, [])
            self.app.badges.clear.assert_not_called()
            self.app.scan_request.set.assert_called_once()  # read again right away
        self.scan('other_chat', [])
        self.app.badges.clear.assert_called_once()

    def test_failed_member_retries_after_cooldown(self):
        self.app.failed['ou_a'] = 0
        self.app.request_profile('ou_a')
        self.assertNotIn('ou_a', self.app.failed)

    def test_tray_close_hides_panel_and_quit_exits(self):
        self.prepare_tick()
        self.app.close = Mock()
        self.app.commands.put('show')
        self.app.commands.put('quit')
        self.app.tick()
        self.app.root.deiconify.assert_called_once()
        self.app.close.assert_called_once()
        self.app.hide_panel()
        self.app.root.withdraw.assert_called_once()

    def test_nickname_found_by_text_is_remembered(self):
        self.app.config = {}
        with patch('feishu_mbti.app.write_json') as save:
            self.app.learn_aliases([{'id': 'ou_a', 'display_name': '示例群昵称', 'via_text': True},
                                    {'id': 'ou_b', 'display_name': '直接匹配', 'via_text': False}])
            self.app.learn_aliases([{'id': 'ou_a', 'display_name': '示例群昵称', 'via_text': True}])
        self.assertEqual(self.app.config['aliases']['oc_test'], {'ou_a': ['示例群昵称']})
        save.assert_called_once()

    def run_worker(self, count):
        events = []
        worker = threading.Thread(target=self.app.classifier_worker)
        worker.start()
        try:
            for _ in range(count):
                events.append(self.app.events.get(timeout=3))
        finally:
            self.app.closed.set()
            worker.join(timeout=2)
        return events

    def test_new_member_answers_from_cache_then_refines_with_sender_history(self):
        self.app.messages.merge_people('oc_test', {'ou_b': {'id': 'ou_b', 'name': '新成员', 'messages': [
            {'id': f'b{index}', 'text': f'群里的第 {index} 句', 'timestamp': self.base + index} for index in range(3)]}})
        self.app.people = self.app.cached_people()
        wider = [{'id': f'w{index}', 'text': f'更早的第 {index} 句', 'timestamp': self.base - 500 + index} for index in range(15)]
        self.app.request_profile('ou_b')
        with patch('feishu_mbti.app.fetch_person_history', return_value=wider), patch(
                'feishu_mbti.app.classify', side_effect=lambda messages, **_: {'label': 'INTP', 'probability': .5,
                                                                                'status': 'estimated', 'result_version': RESULT_VERSION}) as classify:
            events = self.run_worker(2)
        self.assertEqual([(event, data[2]) for event, _, data in events], [('profile', False), ('profile', True)])
        self.assertEqual([len(call.args[0]) for call in classify.call_args_list], [3, 18])

    def test_visible_member_jumps_ahead_of_prewarm(self):
        self.app.profiles = {}
        self.app.request_profile('ou_a', priority=1)
        self.app.request_profile('ou_a')
        self.assertEqual(self.app.jobs.get_nowait()[0], 0)
        self.assertEqual(self.app.jobs.qsize(), 1)  # Stale copy is skipped by the worker.


if __name__ == '__main__':
    unittest.main()
