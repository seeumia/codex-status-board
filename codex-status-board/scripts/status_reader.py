"""Read Codex metadata and lifecycle records without changing its files."""
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path


LABELS = {
    'running': '正在开发', 'completed': '本轮完成 · 待阅读', 'waiting': '等待你处理',
    'interrupted': '已中断', 'failed': '运行出错', 'unknown': '状态待确认',
}
BOUNDARIES = {'task_started', 'task_complete', 'turn_aborted'}
MAX_TAIL = 16 * 1024 * 1024


def selected_title():
    """Keep a slow/inaccessible desktop from blocking lifecycle polling."""
    if os.name != 'nt':
        return None
    try:
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', str(Path(__file__).with_name('window_selection.py'))],
            capture_output=True, encoding='utf-8', timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        value = json.loads(result.stdout) if result.returncode == 0 else None
        return value if isinstance(value, str) and value else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def event_time(event):
    try:
        return datetime.fromisoformat(event.get('timestamp', '').replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return None


class Rollout:
    def __init__(self, path):
        self.path = Path(path)
        self.identity = None
        self.offset = 0
        self.signature = None
        self.reset()

    def reset(self):
        self.status = 'unknown'
        self.turn_id = None
        self.started_at = None
        self.pending = set()
        self.problem = None

    def apply(self, event):
        payload = event.get('payload', {})
        if not isinstance(payload, dict):
            return
        typ = payload.get('type')
        if event.get('type') == 'event_msg':
            if typ == 'task_started':
                self.reset()
                self.status = 'running'
                self.turn_id = payload.get('turn_id')
                self.started_at = payload.get('started_at') or event_time(event)
            elif typ in ('task_complete', 'turn_aborted'):
                turn_id = payload.get('turn_id')
                if self.turn_id and turn_id and turn_id != self.turn_id:
                    return
                self.turn_id = turn_id or self.turn_id
                self.started_at = payload.get('started_at') or self.started_at
                self.status = 'completed' if typ == 'task_complete' else 'interrupted'
                if payload.get('status') == 'failed':
                    self.status = 'failed'
                self.pending.clear()
        elif event.get('type') == 'response_item' and self.status == 'running':
            call_id = payload.get('call_id')
            if typ in ('function_call', 'custom_tool_call'):
                # Only synchronous input requests imply a wait. Async questions do not.
                if str(payload.get('name', '')).split('.')[-1] == 'request_user_input' and call_id:
                    self.pending.add(call_id)
            elif typ in ('function_call_output', 'custom_tool_call_output'):
                self.pending.discard(call_id)

    def parse_lines(self, data):
        for line in data.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError('not an object')
                self.apply(event)
            except (ValueError, TypeError):
                self.problem = '会话记录格式异常'

    def bootstrap(self, file, size):
        """Bound startup work; scan backward to the latest start, then replay in order."""
        length = min(size, 256 * 1024)
        while True:
            start = size - length
            file.seek(start)
            data = file.read(length)
            if start:
                newline = data.find(b'\n')
                if newline < 0:
                    data = b''
                else:
                    start += newline + 1
                    data = data[newline + 1:]
            complete = data.rfind(b'\n') + 1
            lines = data[:complete].splitlines(keepends=True)
            found = None
            for i in range(len(lines) - 1, -1, -1):
                try:
                    event = json.loads(lines[i])
                    if event.get('type') == 'event_msg' and event.get('payload', {}).get('type') == 'task_started':
                        found = i
                        break
                except (ValueError, AttributeError):
                    pass
            if found is not None or length == size or length >= MAX_TAIL:
                self.parse_lines(b''.join(lines[found or 0:]))
                self.offset = start + complete
                return
            length = min(length * 2, size, MAX_TAIL)

    def read(self, now):
        try:
            with self.path.open('rb') as file:
                stat = self.path.stat()
                identity = (stat.st_dev, stat.st_ino)
                signature = (identity, stat.st_size, stat.st_mtime_ns)
                if signature != self.signature:
                    if identity != self.identity or stat.st_size < self.offset or (
                        self.signature and stat.st_size == self.signature[1]
                    ):
                        self.reset()
                        self.bootstrap(file, stat.st_size)
                    else:
                        file.seek(self.offset)
                        data = file.read(MAX_TAIL)
                        complete = data.rfind(b'\n') + 1
                        self.parse_lines(data[:complete])
                        self.offset += complete
                        if len(data) == MAX_TAIL and complete == 0:
                            self.problem = '会话记录过长，暂时无法确认状态'
                    self.identity = identity
                    # Only cache a fully consumed snapshot; a large append is drained next poll.
                    self.signature = signature if stat.st_size - self.offset < MAX_TAIL else None
                status = 'waiting' if self.pending and self.status == 'running' else self.status
                note = ''
                if self.problem:
                    status, note = 'unknown', self.problem
                elif status == 'running' and now - stat.st_mtime > 300:
                    status, note = 'unknown', '超过 5 分钟未见新记录'
                return status, note
        except OSError:
            return 'unknown', '暂时无法读取会话记录'


class Monitor:
    def __init__(self, codex_home, selection_reader=None):
        self.home = Path(codex_home).expanduser().resolve()
        self.readers = {}
        self.selection_reader = selection_reader or selected_title
        self.pending_path = self.home / 'cache/status-board/pending-reading.json'
        try:
            pending = json.loads(self.pending_path.read_text(encoding='utf-8'))
            if not isinstance(pending, dict) or any(not isinstance(v, dict) for v in pending.values()):
                raise ValueError('invalid pending reading state')
            self.pending_reading = pending
        except (OSError, ValueError):
            self.pending_reading = {}
        self.saved_pending = json.dumps(self.pending_reading, sort_keys=True)

    def save_pending(self):
        serialized = json.dumps(self.pending_reading, sort_keys=True)
        if serialized == self.saved_pending:
            return
        self.pending_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.pending_path.with_suffix('.tmp')
        temporary.write_text(serialized, encoding='utf-8')
        temporary.replace(self.pending_path)
        self.saved_pending = serialized

    def unread_ids(self):
        """Use the desktop's local unread flags; never infer reading from inactivity."""
        try:
            data = json.loads((self.home / '.codex-global-state.json').read_text(encoding='utf-8'))
            hosts = data['electron-persisted-atom-state']['unread-thread-ids-by-host-v1']
            if not isinstance(hosts, dict):
                raise ValueError('invalid unread hosts')
            ids = hosts.get('local', [])
            if not isinstance(ids, list) or any(not isinstance(key, str) for key in ids):
                raise ValueError('invalid unread ids')
            return set(ids), None
        except (OSError, ValueError, KeyError, TypeError):
            return set(), '暂时无法读取 Codex 已读状态；仅显示运行项和看板已记住的待阅读项。'

    def metadata(self):
        candidates = [p for p in self.home.glob('state_*.sqlite') if p.stem[6:].isdigit()]
        if not candidates:
            raise ValueError('找不到本机 Codex 会话目录')
        path = max(candidates, key=lambda p: int(p.stem[6:]))
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as conn:
            conn.execute('PRAGMA query_only=ON')
            conn.row_factory = sqlite3.Row
            columns = {r['name'] for r in conn.execute('PRAGMA table_info(threads)')}
            if not {'id', 'title', 'source', 'archived', 'rollout_path'} <= columns:
                raise ValueError('当前 Codex 记录格式尚不支持')
            name = 'name' if 'name' in columns else 'NULL AS name'
            rows = conn.execute(f'SELECT id, {name}, title, source, rollout_path FROM threads WHERE archived=0').fetchall()
            return [dict(r) for r in rows if not str(r['source']).startswith('{') and r['source'] != 'subagent']

    def snapshot(self):
        now = time.time()
        try:
            rows = self.metadata()
        except (sqlite3.Error, OSError, ValueError) as exc:
            note = str(exc) if isinstance(exc, ValueError) else '暂时无法读取 Codex 会话列表'
            return {'updated_at': now, 'error': note, 'sessions': [], 'warnings': 0}
        unread, visibility_warning = self.unread_ids()
        live_ids = {row['id'] for row in rows}
        self.readers = {k: v for k, v in self.readers.items() if k in live_ids}
        self.pending_reading = {k: v for k, v in self.pending_reading.items() if k in live_ids}
        sessions = []
        warnings = 0
        selection_fetched = False
        selected_id = None

        def current_selection():
            nonlocal selection_fetched, selected_id
            if not selection_fetched:
                selection_fetched = True
                title = self.selection_reader()
                matches = [r['id'] for r in rows if title and
                           (r['name'] or r['title'] or '').strip().split('\n')[0] == title]
                # Accessibility exposes the title, not the UUID. Never guess on duplicates.
                selected_id = matches[0] if len(matches) == 1 else None
            return selected_id

        for row in rows:
            key = row['id']
            reader = self.readers.get(key)
            prior = (reader.status, reader.turn_id) if reader else None
            if not reader or reader.path != Path(row['rollout_path']):
                reader = Rollout(row['rollout_path'])
                self.readers[key] = reader
            status, note = reader.read(now)
            identity = {'turn': reader.turn_id, 'started_at': reader.started_at}
            if reader.status != 'completed' or (
                key in self.pending_reading and any(self.pending_reading[key].get(k) != v for k, v in identity.items())
            ):
                self.pending_reading.pop(key, None)
            newly_completed = status == 'completed' and prior is not None and prior != ('completed', reader.turn_id)
            if newly_completed and (key not in unread or current_selection() == key):
                self.pending_reading[key] = identity
            pending = self.pending_reading.get(key)
            if status == 'completed' and pending is not None and not pending.get('acknowledged'):
                current_id = current_selection()
                if current_id is not None:
                    if current_id != key:
                        pending['left'] = True
                    elif pending.get('left'):
                        pending['acknowledged'] = True
            if status == 'unknown':
                warnings += 1
            completed_visible = (not pending.get('acknowledged', False)) if pending is not None else key in unread
            if status == 'running' or (status == 'completed' and completed_visible):
                title = (row['name'] or row['title'] or '未命名会话').strip().split('\n')[0][:120]
                sessions.append({
                    'id': key, 'title': title, 'status': status, 'label': LABELS[status],
                    'note': note, 'started_at': reader.started_at,
                })
        try:
            self.save_pending()
        except OSError:
            visibility_warning = '待阅读状态暂时无法保存，重启看板后可能丢失。'
        return {'updated_at': now, 'error': None, 'warnings': warnings,
                'visibility_warning': visibility_warning, 'sessions': sessions}
