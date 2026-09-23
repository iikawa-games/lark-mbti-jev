"""Character themes: show an anime character in place of the MBTI type.

Each theme maps the 16 types to characters. The mapping is a fan-style
association for fun, not a claim about the characters. Built-in themes live in
the package; users add their own JSON files under .data/themes.
"""
import json
from pathlib import Path

from .store import DATA

BUILTIN = Path(__file__).with_name('theme_packs')
CUSTOM = DATA / 'themes'
TYPES = ('ISTJ', 'ISFJ', 'INFJ', 'INTJ', 'ISTP', 'ISFP', 'INFP', 'INTP',
         'ESTP', 'ESFP', 'ENFP', 'ENTP', 'ESTJ', 'ESFJ', 'ENFJ', 'ENTJ')
EXAMPLE = {
    'name': '我的主题',
    'description': '把下面每种类型换成你想要的角色；缺少的类型会继续显示 MBTI。',
    'characters': {kind: {'name': '角色名', 'series': '作品名'} for kind in TYPES},
}


def _read(path, key):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get('name', '')).strip():
        return None
    characters = {}
    for kind, value in (data.get('characters') or {}).items():
        kind = str(kind).upper()
        if kind in TYPES and isinstance(value, dict) and str(value.get('name', '')).strip():
            characters[kind] = {'name': str(value['name']).strip(), 'series': str(value.get('series', '')).strip()}
    if not characters:
        return None
    return {'id': key, 'name': str(data['name']).strip(), 'description': str(data.get('description', '')),
            'characters': characters}


def load_themes(builtin=BUILTIN, custom=CUSTOM):
    """Built-in themes first, then the user's; broken files are skipped."""
    themes = {}
    for folder, prefix in ((builtin, ''), (custom, 'custom:')):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob('*.json')):
            if path.stem.startswith('_'):
                continue  # Templates are not themes until copied and renamed.
            theme = _read(path, prefix + path.stem)
            if theme:
                themes[theme['id']] = theme
    return themes


def ensure_custom_folder(custom=CUSTOM):
    """Create the user's theme folder with a fill-in example on first use."""
    custom.mkdir(parents=True, exist_ok=True)
    if not any(custom.glob('*.json')):
        (custom / '_示例-复制后改名.json').write_text(json.dumps(EXAMPLE, ensure_ascii=False, indent=2), encoding='utf-8')
    return custom


def character(theme, label):
    return (theme or {}).get('characters', {}).get(str(label or '').upper())
