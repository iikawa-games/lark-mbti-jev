"""Shared display of the model's full-type probability, including weak evidence."""
import math
import re
from .classifier import RESULT_VERSION
from .themes import character


def is_current_profile(profile):
    if not isinstance(profile, dict) or profile.get('result_version') != RESULT_VERSION:
        return False
    if profile.get('status') == 'insufficient' and profile.get('sample_count') == 0:
        return True
    probability = profile.get('probability')
    return bool(re.fullmatch(r'[EI][SN][TF][JP]', str(profile.get('label', '')))) and (
        not isinstance(probability, bool) and isinstance(probability, (int, float))
        and math.isfinite(probability) and 0 <= probability <= 1
    )


def format_profile(profile, *, empty='分析中', theme=None):
    if not profile:
        return empty
    if profile.get('display_status'):
        return profile['display_status']
    if not is_current_profile(profile):
        return '待更新'
    if profile.get('status') == 'insufficient':
        return '暂无文本'
    percentage = math.floor(profile['probability'] * 100 + 0.5)
    # A theme shows its character for the type; the probability is unchanged.
    person = character(theme, profile['label'])
    return f'{person["name"] if person else profile["label"]} {percentage}%'
