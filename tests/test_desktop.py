import unittest
from feishu_mbti.desktop import Line, resolve_people, title_matches


class MatchingTests(unittest.TestCase):
    def test_duplicate_names_never_choose_arbitrarily(self):
        people = {str(i): {'name': '小明', 'messages': []} for i in (1, 2)}
        self.assertEqual(resolve_people([Line('小明', 61, 220, 25, 12)], people, scale=1), [])

    def test_member_count_and_wrong_group(self):
        line = Line('Demo | 虚构讨论组 120', 60, 16, 280, 21)
        self.assertTrue(title_matches([line], 'Demo | 虚构讨论组', 1))
        self.assertFalse(title_matches([line], '其他群', 1))
        self.assertFalse(title_matches([Line('AI Lab', 60, 16, 100, 21)], 'AI', 1))

    def test_nickname_from_unique_message(self):
        people = {'a': {'name': '张某', 'messages': [{'text': '这个是足够长的唯一消息正文测试'}]}}
        lines = [Line('群里的别名', 61, 220, 72, 12), Line('这个是足够长的唯一消息正文测试', 74, 255, 260, 16)]
        result = resolve_people(lines, people, scale=1)
        self.assertEqual([item['id'] for item in result], ['a'])

    def test_message_text_name_outside_header_geometry_is_not_name(self):
        people = {'a': {'name': '小明', 'messages': []}}
        self.assertEqual(resolve_people([Line('小明', 74, 255, 30, 19)], people, scale=1), [])

    def test_body_at_body_indent_cannot_match_later_sender(self):
        people = {'a': {'name': '小明', 'messages': [{'text': '后面另一位成员的完整消息正文'}]}}
        lines = [Line('这是一段虚构的普通聊天内容', 74, 255, 220, 14), Line('后面另一位成员的完整消息正文', 74, 320, 260, 14)]
        self.assertEqual(resolve_people(lines, people, scale=1), [])

    def test_same_body_two_senders_does_not_resolve_nickname(self):
        people = {str(i): {'name': str(i), 'messages': [{'text': '这个是足够长的重复消息正文测试'}]} for i in (1, 2)}
        lines = [Line('别名', 61, 220, 24, 12), Line('这个是足够长的重复消息正文测试', 74, 255, 260, 16)]
        self.assertEqual(resolve_people(lines, people, scale=1), [])

    def test_label_keeps_name_row_and_does_not_cover_custom_status(self):
        people = {'a': {'name': '小明', 'messages': []}}
        line = Line('小 明 | 开 会 中', 61, 220, 150, 12,
                    (('小',61,12),('明',73,12),('|',90,3),('开会中',100,45)))
        result = resolve_people([line], people, scale=1)
        self.assertEqual(result[0]['id'], 'a')
        self.assertEqual(result[0]['x'], 218)
        self.assertEqual(result[0]['display_name'], '小 明')
        self.assertEqual(result[0]['y'], line.y)

    def test_base_match_trims_parenthetical_name_before_inline_status(self):
        people = {'a': {'name': '示例甲', 'messages': []}}
        line = Line('示 例 甲 （ 昵 称 甲 ） 固 示例状态文 〔 示 例 甲 〕', 61, 220, 310, 20, (
            ('示', 61, 12), ('例', 75, 12), ('甲', 89, 12), ('（', 103, 7),
            ('昵', 110, 12), ('称', 124, 12), ('甲', 138, 12), ('）', 152, 7),
            ('固', 174, 12), ('示例状态文', 190, 70), ('〔示例甲〕', 270, 75),
        ))

        result = resolve_people([line], people, scale=1)
        self.assertEqual(result[0]['id'], 'a')
        self.assertEqual(result[0]['display_name'], '示 例 甲 （ 昵 称 甲 ）')
        self.assertEqual(result[0]['x'], 378)
        self.assertEqual(result[0]['y'], line.y)

    def test_separate_same_row_status_places_badge_after_status(self):
        people = {'a': {'name': '小林', 'messages': []}}
        name = Line('小 林 （ 示 例 ）', 61, 220, 100, 19, (
            ('小', 61, 12), ('林', 75, 12), ('（', 89, 7),
            ('示', 96, 12), ('例', 110, 12), ('）', 124, 7),
        ))
        status = Line('个人状态链接', 145, 221, 90, 18)

        result = resolve_people([name, status], people, scale=1)
        self.assertEqual(result[0]['display_name'], '小 林 （ 示 例 ）')
        self.assertEqual(result[0]['x'], 242)
        self.assertEqual(result[0]['y'], name.y)

    def test_larger_feishu_sender_text_is_still_located(self):
        people = {'a': {'name': '小明', 'messages': []}}
        for scale in (1, 1.5, 2):
            line = Line('小明', 61 * scale, 220 * scale, 48 * scale, 22 * scale)
            result = resolve_people([line], people, scale=scale)
            self.assertEqual([item['id'] for item in result], ['a'])
            self.assertEqual(result[0]['height'], 22 * scale)


if __name__ == '__main__':
    unittest.main()
