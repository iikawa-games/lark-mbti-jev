import unittest
from unittest.mock import patch
from feishu_mbti.lark import fetch_history, fetch_person_history, text_content


class LarkTests(unittest.TestCase):
    def test_sender_and_chat_boundaries_survive_bad_upstream_filter(self):
        payload = {'ok': True, 'data': {'messages': [
            {'chat_id':'oc_a', 'sender':{'id':'ou_a'}, 'msg_type':'text', 'content':'mine', 'message_id':'1'},
            {'chat_id':'oc_b', 'sender':{'id':'ou_a'}, 'msg_type':'text', 'content':'other chat'},
            {'chat_id':'oc_a', 'sender':{'id':'ou_b'}, 'msg_type':'text', 'content':'other person'},
            {'chat_id':'oc_a', 'sender':{'id':'ou_a'}, 'msg_type':'text', 'deleted':True, 'content':'recalled'},
        ]}}
        with patch('feishu_mbti.lark.run_cli', return_value=payload) as command:
            result = fetch_person_history('oc_a', 'ou_a')
        self.assertEqual([message['text'] for message in result], ['mine'])
        args = command.call_args.args
        self.assertEqual(args[args.index('--chat-id')+1], 'oc_a')
        self.assertEqual(args[args.index('--sender')+1], 'ou_a')
        self.assertEqual(args[args.index('--page-limit')+1], '10')

    def test_incremental_history_keeps_the_requested_start(self):
        start = '2026-09-22T10:00:00+00:00'
        with patch('feishu_mbti.lark.run_cli', return_value={'data': {'messages': []}}) as command:
            result = fetch_history('oc_a', start=start)
        args = command.call_args.args
        self.assertEqual(args[args.index('--start') + 1], start)
        self.assertIn('started_at', result)

    def test_group_history_excludes_a_different_chat(self):
        messages = [{'chat_id': chat, 'sender': {'id': 'ou_a', 'sender_type': 'user'},
                     'msg_type': 'text', 'content': chat, 'message_id': chat} for chat in ('oc_a', 'oc_b')]
        with patch('feishu_mbti.lark.run_cli', return_value={'data': {'messages': messages}}):
            result = fetch_history('oc_a')
        self.assertEqual([message['text'] for message in result['people']['ou_a']['messages']], ['oc_a'])

    def test_only_original_text_is_eligible(self):
        self.assertEqual(text_content({'msg_type':'merge_forward','content':'forwarded'}), '')
        self.assertEqual(text_content({'msg_type':'image','content':'image-key'}), '')
        self.assertEqual(text_content({'msg_type':'text','content':'{"text":"hello"}'}), 'hello')


if __name__ == '__main__':
    unittest.main()
