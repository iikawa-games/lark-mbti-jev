"""Persist labels and UI preferences; message_cache owns the local text database."""
from pathlib import Path
import json
import os
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / '.data'


def read_json(name, default):
    path = DATA / name
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(name, value):
    DATA.mkdir(exist_ok=True)
    target = DATA / name
    fd, path = tempfile.mkstemp(dir=DATA, prefix='.write-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
        os.replace(path, target)
    finally:
        if os.path.exists(path):
            os.unlink(path)
