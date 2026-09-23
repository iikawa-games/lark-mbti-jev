import unittest
from unittest.mock import Mock, patch
from feishu_mbti.scroll import ScrollMonitor
from feishu_mbti.typography import badge_top
import test_app_cache


class ScrollTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.scroll = ScrollMonitor(clock=lambda: self.now)
        self.scroll.watch(42, (100, 100, 500, 500))

    def test_wheel_hides_until_settled_and_old_ocr_stays_invalid(self):
        before, _ = self.scroll.state()
        self.scroll.observe(0x020A, 200, 200, 42)
        self.assertFalse(self.scroll.state()[1])
        self.assertFalse(self.scroll.accepts(before))
        self.now += .2
        self.scroll.observe(0x020A, 200, 200, 42)
        self.now += .2
        self.assertFalse(self.scroll.state()[1])
        self.now += .2
        current, stable = self.scroll.state()
        self.assertTrue(stable)
        self.assertTrue(self.scroll.accepts(current))
        self.assertFalse(self.scroll.accepts(before))

    def test_other_windows_and_scroll_outside_chat_do_not_invalidate(self):
        self.scroll.observe(0x020A, 200, 200, 43)
        self.scroll.observe(0x020A, 50, 200, 42)
        self.scroll.observe(0x0200, 200, 200, 42)
        self.assertEqual(self.scroll.state(), (0, True))

    def test_scrollbar_drag_stays_hidden_until_release_even_outside_pane(self):
        self.scroll.observe(0x0201, 499, 200, 42)
        self.now += 1
        self.assertFalse(self.scroll.state()[1])
        self.scroll.observe(0x0200, 499, 250, 42)
        self.scroll.observe(0x0202, 510, 250, 42)
        self.now += .4
        self.assertTrue(self.scroll.state()[1])

    def test_horizontal_wheel_invalidates(self):
        self.scroll.observe(0x020E, 200, 200, 42)
        self.assertEqual(self.scroll.state(), (1, False))

    def test_center_alignment_also_applies_to_names_with_status(self):
        for height in (18, 21, 27):
            for badge_height in (24, 30, 36):
                anchor = {'y': 200, 'height': height, 'above': True}
                top = badge_top(anchor, badge_height, 1.5)
                self.assertLessEqual(abs(top + badge_height / 2 - (200 + height / 2)), .5)


class ScrollAppTests(unittest.TestCase):
    def test_delayed_scan_cannot_restore_labels_or_enqueue_classification(self):
        harness = test_app_cache.AppCacheTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        harness.prepare_tick()
        app = harness.app
        app.scroll.watch(42, (100, 100, 500, 500))
        app.scroll.observe(0x020A, 200, 200, 42)
        app.request_profile = Mock()
        app.events.put(('scan', 1, {'chat_id': 'oc_test', 'scroll_revision': 0,
                                   'state': 'ready', 'hwnd': 42, 'anchors': [{'id': 'ou_a'}]}))
        with patch('feishu_mbti.app.foreground_feishu', return_value=42):
            app.tick()
        app.badges.clear.assert_called_once()
        app.badges.show.assert_not_called()
        app.request_profile.assert_not_called()

    def test_scrolling_keeps_msaa_labels_and_follows_newest_position(self):
        harness = test_app_cache.AppCacheTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        harness.prepare_tick()
        app = harness.app
        app.enabled = True
        app.last_snapshot = app.last_fetch = 10**9
        app.scan_request = Mock()
        app.scan_state = {'backend': 'msaa', 'state': 'ready', 'hwnd': 42, 'captured_at': 0}
        app.scroll.watch(42, (100, 100, 500, 500))
        app.scroll.observe(0x020A, 200, 200, 42)
        app.events.put(('track', 1, [{'index': 0, 'y': 1}]))
        app.events.put(('track', 1, [{'index': 0, 'y': 2}]))
        app.tick()
        app.badges.clear.assert_not_called()
        app.badges.move.assert_called_once_with([{'index': 0, 'y': 2}])


if __name__ == '__main__':
    unittest.main()
