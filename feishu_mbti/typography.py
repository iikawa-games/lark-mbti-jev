"""Size labels from the measured sender text, in physical screen pixels."""
from functools import lru_cache
from pathlib import Path
import os


@lru_cache(maxsize=2)
def _ink_ratio(cjk):
    # OCR returns glyph ink, not the font's em square. Calibrate that difference
    # with the platform font so changing Feishu's text size follows naturally.
    from PIL import ImageFont
    folder = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    try:
        font = ImageFont.truetype(str(folder / ('msyh.ttc' if cjk else 'segoeui.ttf')), 100)
        bounds = font.getbbox('姓名' if cjk else 'Ag')
        return (bounds[3] - bounds[1]) / 100
    except OSError:
        return .9 if cjk else .8


# Labels sit a little smaller than the sender name so they read as secondary.
BADGE_RATIO = .82


def badge_font_pixels(name_height, scale=1, display_name=''):
    cjk = any('\u3400' <= char <= '\u9fff' for char in display_name)
    # A small floor handles marginal OCR; actual name height controls normal use.
    return max(round(8 * scale), round(name_height / _ink_ratio(cjk) * BADGE_RATIO))


def accessible_font_pixels(line_height, scale=1):
    """Accessibility boxes span the line height (about 1.2x the font size)."""
    return max(round(8 * scale), round(line_height / 1.2 * BADGE_RATIO))


def badge_top(anchor, widget_height, scale=1):
    return round(anchor['y'] + (anchor['height'] - widget_height) / 2)
