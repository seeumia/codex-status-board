import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from status_reader import Monitor


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.db = self.home / 'state_5.sqlite'
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('CREATE TABLE threads (id TEXT, name TEXT, title TEXT, source TEXT, archived INTEGER, rollout_path TEXT)')
        self.unread([])
        self.selected_title = None

    def monitor(self):
        return Monitor(self.home, selection_reader=lambda: self.selected_title)

    def unread(self, ids, **hosts):
        (self.home / '.codex-global-state.json').write_text(json.dumps({
            'electron-persisted-atom-state': {
                'unread-thread-ids-by-host-v1': {'local': ids, **hosts}
            }
        }), encoding='utf-8')

    def event(self, key, typ, turn='turn-1', **fields):
        with (self.home / f'{key}.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'event_msg', 'payload': {
                'type': typ, 'turn_id': turn, **fields
            }}) + '\n')

    def thread(self, key, status='running', source='cli', archived=0):
        path = self.home / f'{key}.jsonl'
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('INSERT INTO threads VALUES (?, NULL, ?, ?, ?, ?)',
                         (key, key, source, archived, str(path)))
        self.event(key, 'task_started', started_at=time.time() - 3600)
        if status == 'completed':
            self.event(key, 'task_complete')
        elif status == 'failed':
            self.event(key, 'task_complete', status='failed')
        elif status == 'interrupted':
            self.event(key, 'turn_aborted')
        elif status == 'stale':
            os.utime(path, (time.time() - 600, time.time() - 600))
        elif status == 'waiting':
            with path.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'type': 'response_item', 'payload': {
                    'type': 'function_call', 'name': 'request_user_input', 'call_id': 'ask'
                }}) + '\n')

    def statuses(self, monitor):
        return {s['id']: s['status'] for s in monitor.snapshot()['sessions']}

    def test_startup_only_shows_running_and_completed_unread(self):
        for key, status in [('active', 'running'), ('unread', 'completed'), ('read', 'completed'),
                            ('stale', 'stale'), ('failed', 'failed'), ('waiting', 'waiting'),
                            ('aborted', 'interrupted'), ('remote-only', 'completed')]:
            self.thread(key, status)
        self.thread('archived', 'completed', archived=1)
        self.thread('child', 'completed', source='subagent')
        self.thread('json-child', 'running', source='{"subagent":{}}')
        self.unread(['unread', 'archived', 'child', 'failed', 'waiting', 'aborted'], remote=['remote-only'])
        self.assertEqual(self.statuses(self.monitor()), {'active': 'running', 'unread': 'completed'})

    def test_read_removes_completed_and_next_turn_reappears(self):
        self.thread('task')
        monitor = self.monitor()
        self.assertEqual(self.statuses(monitor), {'task': 'running'})
        self.event('task', 'task_complete')
        self.unread(['task'])
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})
        self.unread([])
        self.assertEqual(self.statuses(monitor), {})
        self.assertEqual(self.statuses(self.monitor()), {})
        self.event('task', 'task_started', turn='turn-2', started_at=time.time())
        self.assertEqual(self.statuses(monitor), {'task': 'running'})

    def test_completion_while_staying_open_is_kept_without_unread_flag(self):
        self.thread('task')
        monitor = self.monitor()
        self.statuses(monitor)
        self.event('task', 'task_complete')
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})

    def test_staying_open_completion_survives_board_restart(self):
        self.thread('task')
        monitor = self.monitor()
        self.statuses(monitor)
        self.event('task', 'task_complete')
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})
        self.assertEqual(self.statuses(self.monitor()), {'task': 'completed'})

    def test_new_turn_replaces_staying_open_completion(self):
        self.thread('task')
        monitor = self.monitor()
        self.statuses(monitor)
        self.event('task', 'task_complete')
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})
        self.event('task', 'task_started', turn='turn-2', started_at=time.time())
        self.assertEqual(self.statuses(monitor), {'task': 'running'})
        self.event('task', 'turn_aborted', turn='turn-2')
        self.assertEqual(self.statuses(monitor), {})

    def test_completion_is_kept_when_previous_running_record_was_stale(self):
        self.thread('task', 'stale')
        monitor = self.monitor()
        self.assertEqual(self.statuses(monitor), {})
        self.event('task', 'task_complete')
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})

    def test_failed_and_stale_tasks_are_removed_after_display(self):
        self.thread('failed')
        self.thread('stale')
        monitor = self.monitor()
        self.assertEqual(len(self.statuses(monitor)), 2)
        self.event('failed', 'task_complete', status='failed')
        path = self.home / 'stale.jsonl'
        os.utime(path, (time.time() - 600, time.time() - 600))
        self.assertEqual(self.statuses(monitor), {})

    def test_staying_selected_does_not_read_but_leave_and_return_does(self):
        self.thread('task')
        self.thread('other', 'completed')
        self.selected_title = 'task'
        monitor = self.monitor()
        self.statuses(monitor)
        self.event('task', 'task_complete')
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})
        self.selected_title = 'other'
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})
        self.selected_title = 'task'
        self.unread(['task'])  # Desktop disk state may lag behind the return.
        self.assertEqual(self.statuses(monitor), {})
        self.assertEqual(self.statuses(self.monitor()), {})

    def test_unknown_selection_does_not_count_as_leaving(self):
        self.thread('task')
        self.selected_title = 'task'
        monitor = self.monitor()
        self.statuses(monitor)
        self.event('task', 'task_complete')
        self.statuses(monitor)
        self.selected_title = None
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})
        self.selected_title = 'task'
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})

    def test_duplicate_titles_never_acknowledge_wrong_task(self):
        self.thread('task')
        self.thread('duplicate', 'completed')
        self.thread('other', 'completed')
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE threads SET title='task' WHERE id='duplicate'")
        monitor = self.monitor()
        self.statuses(monitor)
        self.event('task', 'task_complete')
        self.selected_title = 'other'
        self.statuses(monitor)
        self.selected_title = 'task'
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})

    def test_native_auto_read_cannot_clear_special_completion(self):
        self.thread('task')
        self.selected_title = 'task'
        monitor = self.monitor()
        self.statuses(monitor)
        self.event('task', 'task_complete')
        self.statuses(monitor)
        self.unread(['task'])
        self.statuses(monitor)
        self.unread([])
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})

    def test_selected_completion_with_transient_unread_flag_is_kept(self):
        self.thread('task')
        self.selected_title = 'task'
        monitor = self.monitor()
        self.statuses(monitor)
        self.unread(['task'])
        self.event('task', 'task_complete')
        self.statuses(monitor)
        self.unread([])
        self.assertEqual(self.statuses(monitor), {'task': 'completed'})

    def test_confirmed_departure_survives_restart(self):
        self.thread('task')
        self.thread('other', 'completed')
        monitor = self.monitor()
        self.statuses(monitor)
        self.event('task', 'task_complete')
        self.statuses(monitor)
        self.selected_title = 'other'
        self.statuses(monitor)
        self.selected_title = 'task'
        self.assertEqual(self.statuses(self.monitor()), {})

    def test_unread_state_error_hides_completed_but_keeps_running(self):
        self.thread('active')
        self.thread('done', 'completed')
        self.unread(['done'])
        monitor = self.monitor()
        self.statuses(monitor)
        (self.home / '.codex-global-state.json').write_text('{', encoding='utf-8')
        result = monitor.snapshot()
        self.assertEqual({s['id']: s['status'] for s in result['sessions']}, {'active': 'running'})
        self.assertTrue(result.get('visibility_warning'))
        self.unread(['done'])
        self.assertEqual(self.statuses(monitor), {'active': 'running', 'done': 'completed'})

    def test_missing_and_invalid_unread_state_never_guesses(self):
        self.thread('active')
        self.thread('done', 'completed')
        path = self.home / '.codex-global-state.json'
        for data in [None, [], {}, {'electron-persisted-atom-state': None},
                     {'electron-persisted-atom-state': {'unread-thread-ids-by-host-v1': {'local': 'done'}}}]:
            with self.subTest(data=data):
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(json.dumps(data), encoding='utf-8')
                result = self.monitor().snapshot()
                self.assertEqual([s['id'] for s in result['sessions']], ['active'])
                self.assertTrue(result.get('visibility_warning'))

    def test_metadata_error_clears_tiles_instead_of_showing_unknown(self):
        self.thread('active')
        monitor = self.monitor()
        self.statuses(monitor)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DROP TABLE threads')
        result = monitor.snapshot()
        self.assertEqual(result['sessions'], [])
        self.assertTrue(result['error'])


if __name__ == '__main__':
    unittest.main()
