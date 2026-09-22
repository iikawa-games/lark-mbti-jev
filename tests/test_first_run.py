import unittest
from unittest.mock import Mock
from feishu_mbti.app import App, DEFAULT_CHAT


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        self.app = App.__new__(App)
        self.app.chat = dict(DEFAULT_CHAT)
        self.app.messages = Mock()
        self.app.status = Mock()
        self.app.scroll = Mock()

    def test_fresh_install_has_no_group_or_member(self):
        self.assertEqual(DEFAULT_CHAT, {'chat_id': '', 'name': ''})
        self.assertEqual(self.app.cached_people(), {})
        self.app.messages.people.assert_not_called()

    def test_empty_selection_does_not_start_reads_or_mouse_listener(self):
        self.app.enable()
        self.app.refresh()
        self.app.open_chat()
        self.app.clear()
        self.app.scroll.start.assert_not_called()
        self.app.messages.clear_chat.assert_not_called()


if __name__ == '__main__':
    unittest.main()
