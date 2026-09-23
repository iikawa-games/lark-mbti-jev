import json
import tempfile
import unittest
from pathlib import Path

from feishu_mbti.classifier import RESULT_VERSION
from feishu_mbti.presentation import format_profile
from feishu_mbti.themes import TYPES, ensure_custom_folder, load_themes


def profile(label='INTP', probability=.65):
    return {'label': label, 'probability': probability, 'status': 'estimated', 'result_version': RESULT_VERSION}


class ThemeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.custom = Path(self.temp.name) / 'themes'

    def test_builtin_themes_cover_all_sixteen_types_with_distinct_characters(self):
        themes = load_themes(custom=self.custom)
        self.assertEqual({theme['name'] for theme in themes.values()}, {'魔法少女', '少女乐队', '修仙者'})
        for theme in themes.values():
            self.assertEqual(set(theme['characters']), set(TYPES), theme['name'])
            names = [person['name'] for person in theme['characters'].values()]
            self.assertEqual(len(names), len(set(names)), theme['name'])
            self.assertTrue(all(person['series'] for person in theme['characters'].values()))

    def test_theme_replaces_type_and_keeps_probability(self):
        theme = load_themes(custom=self.custom)['magical_girls']
        self.assertEqual(format_profile(profile('ISFJ'), theme=theme), '鹿目圆 65%')
        self.assertEqual(format_profile(profile('ISFJ')), 'ISFJ 65%')
        # Status text is never themed.
        self.assertEqual(format_profile({'display_status': '分析中'}, theme=theme), '分析中')

    def test_custom_theme_template_broken_file_and_missing_types(self):
        ensure_custom_folder(self.custom)
        self.assertEqual([key for key in load_themes(custom=self.custom) if key.startswith('custom:')], [])
        (self.custom / 'broken.json').write_text('{not json', encoding='utf-8')
        (self.custom / 'mine.json').write_text(json.dumps(
            {'name': '示例作品', 'characters': {'intp': {'name': '示例角色甲', 'series': '示例作品'}}},
            ensure_ascii=False), encoding='utf-8')
        themes = load_themes(custom=self.custom)
        self.assertNotIn('custom:broken', themes)
        mine = themes['custom:mine']
        self.assertEqual(format_profile(profile('INTP'), theme=mine), '示例角色甲 65%')
        self.assertEqual(format_profile(profile('ENFP'), theme=mine), 'ENFP 65%')  # Not mapped: MBTI.


if __name__ == '__main__':
    unittest.main()
