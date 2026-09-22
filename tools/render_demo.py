"""Render a shareable UI illustration using fictional data only. No screen capture."""
from pathlib import Path
from functools import lru_cache
import argparse
import os
import sys
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from feishu_mbti.presentation import format_profile

SCALE = 2
WIDTH, HEIGHT = 1460, 603
ORIGIN_X, ORIGIN_Y = 70, 255
INK, MUTED, BLUE = '#252b37', '#768295', '#3370ff'


def render(output, font_path=None):
    candidates = [Path(font_path)] if font_path else [
        Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc',
        Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
        Path('/System/Library/Fonts/PingFang.ttc'),
    ]
    chosen = next((path for path in candidates if path.is_file()), None)
    if not chosen:
        raise SystemExit('Pass --font with a Chinese-capable .ttf or .ttc file.')
    latin_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/segoeui.ttf'
    image = Image.new('RGB', (WIDTH * SCALE, HEIGHT * SCALE), 'white')
    draw = ImageDraw.Draw(image)

    @lru_cache(maxsize=30)
    def font(size, latin=False):
        return ImageFont.truetype(str(latin_path if latin and latin_path.is_file() else chosen), round(size * SCALE))

    def rect(box, fill, radius=0, outline=None, width=1):
        translated = tuple(round((value - (ORIGIN_X if index % 2 == 0 else ORIGIN_Y)) * SCALE)
                           for index, value in enumerate(box))
        draw.rounded_rectangle(translated, radius=radius * SCALE,
                               fill=fill, outline=outline, width=width * SCALE)

    def text(x, cy, value, size=22, color=INK, latin=False):
        face = font(size, latin)
        box = draw.textbbox((0, 0), value, font=face)
        draw.text(((x-ORIGIN_X) * SCALE, (cy-ORIGIN_Y) * SCALE - (box[1] + box[3]) / 2), value, font=face, fill=color)
        return draw.textlength(value, font=face) / SCALE

    def line(x1, y1, x2, y2, color='#e9edf3', width=1):
        draw.line(((x1-ORIGIN_X)*SCALE, (y1-ORIGIN_Y)*SCALE,
                   (x2-ORIGIN_X)*SCALE, (y2-ORIGIN_Y)*SCALE), fill=color, width=width*SCALE)

    def avatar(x, y, letter, background, foreground=BLUE):
        rect((x, y, x+42, y+42), background, 15)
        face = font(23)
        span = draw.textlength(letter, font=face) / SCALE
        text(x+(42-span)/2, y+21, letter, 23, foreground)

    rect((70, 255, 171, 858), '#eef2f8')
    avatar(101, 278, '我', '#dce6f8')
    for index, item in enumerate(('消息', '日历', '文档', '工作台')):
        y = 367 + index * 74
        if index == 0:
            rect((82, y-24, 160, y+25), 'white', 11)
        text(101, y, item, 19, BLUE if index == 0 else MUTED)
    line(172, 255, 172, 858)

    text(196, 284, '消息', 24)
    rect((190, 316, 416, 353), '#f3f5f8', 8)
    text(205, 335, '搜索', 18, '#98a1b0')
    rect((184, 375, 426, 468), '#e7efff', 10)
    avatar(199, 394, '周', '#d3e1ff')
    text(253, 405, '周五灵感社', 20)
    text(253, 438, '一起把想法做出来', 15, MUTED)
    for y, initial, title, summary in [(505, '设', '设计漫游', '新的灵感已收藏'), (606, '读', '午后读书会', '下次聊聊这本书')]:
        avatar(199, y, initial, '#edf1f7', MUTED)
        text(253, y+10, title, 20)
        text(253, y+42, summary, 15, MUTED)
    line(438, 255, 438, 858)
    text(468, 285, '周五灵感社', 25)
    text(653, 286, '虚构演示群', 17, MUTED)
    rect((1321, 271, 1498, 304), '#eff8f4', 16)
    text(1340, 287, '●  本机标签已开启', 16, '#358365')
    line(438, 324, 1530, 324)

    samples = [
        ('米娅', '米', '#e6e0fb', '#7963b2', 'INTP', .65, 367, '先把问题拆开，再看哪一步最值得验证。', 685, '14:20'),
        ('林间', '林', '#e2efe6', '#5c8569', 'ENFP', .72, 499, '我画了两个方向，大家更想试哪个？', 612, '14:21'),
        ('程墨', '程', '#f9eadb', '#af8459', 'ISTJ', .68, 631, '先约好验证标准，明天一起看结果。', 612, '14:22'),
    ]
    for name, initial, background, foreground, label, probability, y, message, width, timestamp in samples:
        avatar(465, y-8, initial, background, foreground)
        name_width = text(523, y+5, name, 22, '#646a73')
        value = format_profile({'label': label, 'probability': probability, 'status': 'uncertain', 'result_version': 2})
        badge_width = draw.textlength(value, font=font(22, True)) / SCALE + 12
        left = 523 + name_width + 12
        rect((left, y-10, left+badge_width, y+20), '#f2f4f7', 0)
        text(left+6, y+5, value, 22, '#646a73', True)
        text(1416, y+5, timestamp, 16, '#a1a9b5', True)
        rect((522, y+36, 522+width, y+98), '#f2f4f6', 12)
        text(544, y+67, message, 24)
    line(438, 757, 1530, 757)
    text(468, 790, '＋    @    ↗', 22, '#9aa4b3')
    text(468, 828, '输入消息…', 19, '#b0b7c2')
    rect((1408, 801, 1501, 837), '#eef2f8', 8)
    text(1433, 819, '发送', 18, '#8b98ab')

    output.parent.mkdir(parents=True, exist_ok=True)
    image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS).save(output)
    print(f'Rendered {output.name}: {WIDTH} x {HEIGHT}, fictional data only.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', help='Path to a Chinese-capable .ttf or .ttc font')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/demo.png')
    args = parser.parse_args()
    render(args.output, args.font)
