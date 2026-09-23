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

    def test_label_sits_right_after_name_before_custom_status(self):
        people = {'a': {'name': '小明', 'messages': []}}
        line = Line('小 明 | 开 会 中', 61, 220, 150, 12,
                    (('小',61,12),('明',73,12),('|',90,3),('开会中',100,45)))
        result = resolve_people([line], people, scale=1)
        self.assertEqual(result[0]['id'], 'a')
        self.assertEqual(result[0]['x'], 89)
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
        self.assertEqual(result[0]['x'], 163)
        self.assertEqual(result[0]['y'], line.y)

    def test_long_same_row_status_does_not_push_badge_away(self):
        people = {'a': {'name': '小林', 'messages': []}}
        name = Line('小 林 （ 示 例 ）', 61, 220, 100, 19, (
            ('小', 61, 12), ('林', 75, 12), ('（', 89, 7),
            ('示', 96, 12), ('例', 110, 12), ('）', 124, 7),
        ))
        status = Line('很长的个人签名和链接 https://example.com/very/long', 145, 221, 900, 18)

        result = resolve_people([name, status], people, scale=1)
        self.assertEqual(result[0]['display_name'], '小 林 （ 示 例 ）')
        self.assertEqual(result[0]['x'], 135)
        self.assertEqual(result[0]['y'], name.y)

    def test_larger_feishu_sender_text_is_still_located(self):
        people = {'a': {'name': '小明', 'messages': []}}
        for scale in (1, 1.5, 2):
            line = Line('小明', 61 * scale, 220 * scale, 48 * scale, 22 * scale)
            result = resolve_people([line], people, scale=scale)
            self.assertEqual([item['id'] for item in result], ['a'])
            self.assertEqual(result[0]['height'], 22 * scale)

    def test_nickname_resolved_from_caption_below_an_image(self):
        people = {'a': {'name': '张某', 'messages': [{'text': '先看图\n这是图片下面的一句说明'}]},
                  'b': {'name': '李某', 'messages': [{'text': '别的内容'}]}}
        lines = [Line('示例群昵称', 62, 220, 98, 15), Line('这是图片下面的一句说明', 74, 318, 168, 17)]
        result = resolve_people(lines, people, scale=1)
        self.assertEqual([item['id'] for item in result], ['a'])
        self.assertTrue(result[0]['via_text'])

    def test_reply_quote_does_not_identify_the_replier(self):
        people = {'a': {'name': '张某', 'messages': [{'text': '被引用的那条原始消息内容'}]}}
        lines = [Line('群昵称', 62, 220, 50, 15), Line('回复 张某', 80, 240, 64, 15),
                 Line('被引用的那条原始消息内容', 155, 240, 200, 15)]
        self.assertEqual(resolve_people(lines, people, scale=1), [])

    def test_same_name_resolved_from_mention_split_across_pieces(self):
        people = {'a': {'name': '陈某', 'messages': [{'text': '@李某周五一起看看方案'}]},
                  'b': {'name': '陈某', 'messages': [{'text': '别的话'}]}}
        lines = [Line('陈某（ABC）', 62, 220, 70, 15), Line('个人签名', 150, 220, 90, 15),
                 Line('回复 李某', 80, 255, 64, 15), Line('示例引用内容', 155, 255, 80, 15),
                 Line('@李某', 76, 282, 50, 15), Line('周五一起看看方案', 133, 282, 80, 15)]
        result = resolve_people(lines, people, scale=1)
        self.assertEqual([(item['id'], item['via_text']) for item in result], [('a', True)])


if __name__ == '__main__':
    unittest.main()
