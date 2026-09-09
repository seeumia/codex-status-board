"""Read Codex metadata and lifecycle records without changing its files."""
import json
import sqlite3
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
    def __init__(self, codex_home):
        self.home = Path(codex_home).expanduser().resolve()
        self.started_at = time.time()
        self.readers = {}
        self.shown = {}
        self.held_completions = {}
        self.initialized = False

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
            # Use the app's task origin, not its title: automation runs can have
            # the same display name as an ordinary development conversation.
            ordinary = " AND COALESCE(thread_source, '') != 'automation'" if 'thread_source' in columns else ''
            rows = conn.execute(f'SELECT id, {name}, title, source, rollout_path FROM threads WHERE archived=0{ordinary}').fetchall()
            return [dict(r) for r in rows if not str(r['source']).startswith('{') and r['source'] != 'subagent']

    def unread_ids(self):
        """Follow the desktop app's persisted unread flag; never mark tasks read here."""
        try:
            state = json.loads((self.home / '.codex-global-state.json').read_text(encoding='utf-8'))
            hosts = state['electron-persisted-atom-state']['unread-thread-ids-by-host-v1']
            if not isinstance(hosts, dict):
                raise ValueError
            ids = hosts.get('local', [])
            if not isinstance(ids, list) or not all(isinstance(key, str) for key in ids):
                raise ValueError
            return set(ids)
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError('暂时无法读取 Codex 已读状态，恢复后自动更新') from None

    def snapshot(self):
        now = time.time()
        try:
            rows = self.metadata()
            unread = self.unread_ids()
        except (sqlite3.Error, OSError, ValueError) as exc:
            note = str(exc) if isinstance(exc, ValueError) else '暂时无法读取 Codex 会话列表'
            return {'updated_at': now, 'error': note, 'sessions': [], 'warnings': 0}
        live_ids = {row['id'] for row in rows}
        self.shown = {k: v for k, v in self.shown.items() if k in live_ids}
        self.readers = {k: v for k, v in self.readers.items() if k in live_ids}
        self.held_completions = {k: v for k, v in self.held_completions.items() if k in live_ids}
        warnings = 0
        for row in rows:
            key = row['id']
            reader = self.readers.get(key)
            prior_turn = reader.turn_id if reader else None
            prior_status = reader.status if reader else None
            if not reader or reader.path != Path(row['rollout_path']):
                reader = Rollout(row['rollout_path'])
                self.readers[key] = reader
            status, note = reader.read(now)
            new_turn = self.initialized and reader.turn_id and reader.turn_id != prior_turn
            # Codex's started_at is second-resolution; compare at the same precision.
            fresh = isinstance(reader.started_at, (int, float)) and reader.started_at >= int(self.started_at)
            if key in self.held_completions and (
                reader.turn_id != self.held_completions[key] or reader.status != 'completed'
            ):
                self.held_completions.pop(key)
            new_completion = self.initialized and status == 'completed' and (
                prior_status == 'running' or (new_turn and fresh)
            )
            if new_completion and key not in unread:
                # The desktop auto-marks a visible task read on completion. That
                # flag alone cannot prove the user came back to read the result.
                # Keep it until the next lifecycle state; reliable re-entry events
                # are not available from the read-only files used by this service.
                self.held_completions[key] = reader.turn_id
            if status == 'unknown':
                warnings += 1
            if key in self.shown or status == 'running' or (new_turn and fresh) or (
                status == 'completed' and key in unread
            ):
                title = (row['name'] or row['title'] or '未命名会话').strip().split('\n')[0][:120]
                self.shown[key] = {
                    'id': key, 'title': title, 'status': status, 'label': LABELS[status],
                    'note': '暂时保留，避免漏读' if key in self.held_completions else note,
                    'started_at': reader.started_at,
                }
        self.initialized = True
        sessions = [s for s in self.shown.values() if s['status'] == 'running'
                    or (s['status'] == 'completed' and (
                        s['id'] in unread or s['id'] in self.held_completions))]
        return {'updated_at': now, 'error': None, 'warnings': warnings, 'sessions': sessions}
