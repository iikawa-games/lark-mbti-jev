"""Read-only, argv-based access to the user's existing lark-cli login."""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone


class LarkError(RuntimeError):
    pass


_processes = set()
_lock = threading.Lock()
_closed = threading.Event()


def cancel_reads():
    with _lock:
        _closed.set()
        for process in tuple(_processes):
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass


def command_prefix():
    override = os.environ.get('LARK_CLI_EXE')
    if override:
        return [override]
    binary = shutil.which('lark-cli.exe')
    if binary:
        return [binary]
    # npm's Windows .cmd shim is intentionally avoided: no shell command assembly.
    native = Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/@larksuite/cli/bin/lark-cli.exe'
    if native.is_file():
        return [str(native)]
    binary = shutil.which('lark-cli')
    if binary and not binary.lower().endswith(('.cmd', '.ps1', '.bat')):
        return [binary]
    raise LarkError('未找到 lark-cli。请先安装并登录，或设置 LARK_CLI_EXE 为实际可执行文件。')


def run_cli(*args):
    env = dict(os.environ, LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1', LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
    try:
        with _lock:
            if _closed.is_set():
                raise LarkError('读取已停止。')
            process = subprocess.Popen(command_prefix() + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       encoding='utf-8', errors='replace', env=env,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            _processes.add(process)
        try:
            stdout, stderr = process.communicate(timeout=55)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        finally:
            with _lock:
                _processes.discard(process)
    except subprocess.TimeoutExpired as exc:
        raise LarkError('飞书读取超时，请稍后重试。') from exc
    stream = stdout if process.returncode == 0 else stderr
    try:
        payload = json.loads(stream)
    except ValueError as exc:
        raise LarkError('lark-cli 未返回有效 JSON，请检查登录状态。') from exc
    if process.returncode or payload.get('ok') is not True:
        error = payload.get('error', {})
        # Do not surface arbitrary CLI output, tokens, or message bodies in logs/UI.
        code = error.get('subtype', error.get('type', 'unknown'))
        if code in ('missing_scope', 'authorization', 'token_expired', 'not_authenticated'):
            raise LarkError('飞书登录或聊天读取权限不可用，请运行 lark-cli auth status --json 检查。')
        raise LarkError('飞书读取失败（' + str(code)[:80] + '）。')
    return payload


def search_chats(query):
    if not query.strip():
        return []
    result = run_cli('im', '+chat-search', '--query', query.strip(), '--as', 'user', '--format', 'json')
    return result.get('data', {}).get('chats', [])


_MEDIA = re.compile(r'!\[[^\]]*\]\([^)]*\)|\[(?:Image|Media|File|Sticker|Video|Audio)(?::[^\]]*)?\]')


def text_content(message):
    if message.get('deleted'):
        return ''
    if message.get('msg_type') == 'post':
        # Rich text (often an image plus a caption): keep only what was typed.
        content = message.get('content', '')
        if not isinstance(content, str):
            return ''
        lines = (line.strip() for line in _MEDIA.sub('', content).splitlines())
        return '\n'.join(line for line in lines if line)
    if message.get('msg_type') != 'text':
        return ''
    content = message.get('content', '')
    if isinstance(content, dict):
        return str(content.get('text', '')).strip()
    if not isinstance(content, str):
        return ''
    # CLI normalizes content to readable text; accept raw API text JSON as well.
    if content.startswith('{'):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and isinstance(parsed.get('text'), str):
                return parsed['text'].strip()
        except ValueError:
            pass
    return content.strip()


def fetch_history(chat_id, *, days=90, pages=20, start=None):
    if not isinstance(chat_id, str) or not chat_id.startswith('oc_') or not chat_id[3:].isalnum():
        raise LarkError('无效的群 ID。')
    started_at = datetime.now(timezone.utc)
    start = start or (started_at - timedelta(days=days)).isoformat(timespec='seconds')
    result = run_cli('im', '+chat-messages-list', '--chat-id', chat_id, '--as', 'user',
                     '--start', start, '--order', 'desc', '--page-size', '50',
                     '--page-all', '--page-limit', str(min(max(pages, 1), 20)),
                     '--no-reactions', '--format', 'json')
    data = result.get('data', {})
    people = {}
    for message in data.get('messages', []):
        if message.get('chat_id') != chat_id:
            continue
        sender = message.get('sender', {})
        if sender.get('sender_type') != 'user' or not sender.get('id'):
            continue
        text = text_content(message)
        if not text:
            continue
        person = people.setdefault(sender['id'], {'id': sender['id'], 'name': sender.get('name') or sender['id'], 'messages': []})
        person['messages'].append({'id': message.get('message_id', ''), 'text': text,
                                   'timestamp': message.get('create_time', '')})
    # CLI returns newest first; inference sees chronological samples.
    for person in people.values():
        person['messages'].reverse()
    pagination = result.get('meta', {}).get('pagination', {})
    return {'people': people, 'message_count': len(data.get('messages', [])),
            'has_more': bool(data.get('has_more')) or pagination.get('complete') is False,
            'days': days, 'fetched_at': datetime.now(timezone.utc).isoformat(),
            'started_at': started_at.isoformat()}


def fetch_person_history(chat_id, user_id, *, days=90, pages=4):
    if not chat_id.startswith('oc_') or not user_id.startswith('ou_'):
        raise LarkError('群或用户 ID 无效。')
    start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec='seconds')
    result = run_cli('im', '+messages-search', '--chat-id', chat_id, '--sender', user_id,
                     '--start', start, '--as', 'user', '--page-size', '50', '--page-all',
                     '--page-limit', str(min(max(pages, 1), 20)), '--no-reactions', '--format', 'json')
    messages = []
    for message in result.get('data', {}).get('messages', []):
        # Independently enforce sender and chat even if a CLI/server ignores a filter.
        if message.get('chat_id') != chat_id or message.get('sender', {}).get('id') != user_id:
            continue
        text = text_content(message)
        if text:
            messages.append({'id': message.get('message_id', ''), 'text': text,
                             'timestamp': message.get('create_time', '')})
    return sorted(messages, key=lambda message: message['timestamp'])
