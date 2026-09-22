import unittest
from feishu_mbti.presentation import format_profile, is_current_profile
from feishu_mbti.classifier import RESULT_VERSION


class ProbabilityDisplayTests(unittest.TestCase):
    def profile(self, probability=.65, status='uncertain'):
        return {'label':'INTP', 'probability':probability, 'status':status,
                'sample_count':2, 'result_version':RESULT_VERSION}

    def test_low_evidence_keeps_complete_type_and_real_probability(self):
        self.assertEqual(format_profile(self.profile()), 'INTP 65%')
        self.assertEqual(format_profile(self.profile(.13)), 'INTP 13%')

    def test_probability_updates_even_when_type_is_unchanged(self):
        self.assertNotEqual(format_profile(self.profile(.65)), format_profile(self.profile(.75)))

    def test_legacy_cache_requires_reclassification(self):
        legacy = {'label':'I??P', 'sample_count':12, 'status':'uncertain'}
        self.assertFalse(is_current_profile(legacy))
        self.assertEqual(format_profile(legacy), '待更新')

    def test_no_text_or_transport_error_never_gets_fabricated_probability(self):
        self.assertEqual(format_profile({'label':'????', 'probability':None, 'status':'insufficient',
                                         'sample_count':0, 'result_version':RESULT_VERSION}), '暂无文本')
        self.assertEqual(format_profile({'display_status':'分析失败'}), '分析失败')
        self.assertEqual(format_profile(None), '分析中')


if __name__ == '__main__':
    unittest.main()
