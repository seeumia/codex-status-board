import gc
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'codex-status-board/scripts'))
from status_reader import Monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.db = sqlite3.connect(self.home / 'state_5.sqlite')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, title TEXT, source TEXT, archived INTEGER, rollout_path TEXT)')
        self.paths = {}
        self.unread('one')

    def unread(self, *ids, remote=()):
        state = {'electron-persisted-atom-state': {'unread-thread-ids-by-host-v1': {
            'local': list(ids), 'remote-control:test': list(remote),
        }}}
        (self.home / '.codex-global-state.json').write_text(json.dumps(state), encoding='utf-8')

    def thread(self, name='one', source='vscode', archived=0):
        p = self.home / (name + '.jsonl')
        p.touch()
        self.paths[name] = p
        self.db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)', (name, name, 'private prompt', source, archived, str(p)))
        self.db.commit()
        return p

    def event(self, typ, name='one', turn='t1', **extra):
        payload = {'type': typ, 'turn_id': turn, 'started_at': time.time(), **extra}
        self.write({'type': 'event_msg', 'payload': payload}, name)

    def write(self, event, name='one'):
        with self.paths[name].open('a') as f:
            f.write(json.dumps(event) + '\n')

    def status(self, monitor):
        snapshot = monitor.snapshot()
        tasks = snapshot['sessions'] or snapshot.get('attention', [])
        self.assertTrue(tasks, 'Expected the task in tiles or the attention notice')
        return tasks[0]['status']

    def test_running_finished_and_running_again(self):
        self.thread(); self.event('task_started')
        m = Monitor(self.home)
        self.assertEqual(self.status(m), 'running')
        self.event('task_complete')
        self.assertEqual(self.status(m), 'completed')
        self.event('task_started', turn='t2')
        self.assertEqual(self.status(m), 'running')
        self.event('task_complete', turn='t1')
        self.assertEqual(self.status(m), 'running', 'An old turn must not finish the new turn')

    def test_old_finished_hidden_and_fast_new_turn_retained(self):
        self.thread(); self.event('task_started'); self.event('task_complete')
        self.unread()
        m = Monitor(self.home)
        self.assertEqual(m.snapshot()['sessions'], [])
        self.event('task_started', turn='t2'); self.event('task_complete', turn='t2')
        self.unread('one')
        self.assertEqual(self.status(m), 'completed')

    def test_new_short_session_in_startup_second_is_not_missed(self):
        m = Monitor(self.home); m.snapshot()
        self.thread()
        self.event('task_started', started_at=int(time.time()))
        self.event('task_complete', started_at=int(time.time()))
        self.assertEqual([s['status'] for s in m.snapshot()['sessions']], ['completed'])

    def test_existing_unread_completed_task_is_shown_on_startup(self):
        self.thread(); self.event('task_started'); self.event('task_complete')
        self.assertEqual([s['id'] for s in Monitor(self.home).snapshot()['sessions']], ['one'])

    def test_codex_read_state_removes_and_can_restore_finished_task(self):
        self.thread(); self.event('task_started')
        m = Monitor(self.home); m.snapshot()
        self.event('task_complete')
        self.assertEqual(self.status(m), 'completed')
        log_before = self.paths['one'].read_bytes()
        self.unread()
        self.assertEqual(m.snapshot()['sessions'], [])
        self.assertEqual(Monitor(self.home).snapshot()['sessions'], [])
        self.unread('one')
        self.assertEqual(self.status(m), 'completed')
        self.assertEqual(self.paths['one'].read_bytes(), log_before)

    def test_running_task_ignores_read_flag_then_follows_codex_on_completion(self):
        self.thread(); self.event('task_started'); self.unread()
        m = Monitor(self.home)
        self.assertEqual(self.status(m), 'running')
        self.event('task_complete')
        self.assertEqual(m.snapshot()['sessions'], [])
        self.event('task_started', turn='t2')
        self.assertEqual(self.status(m), 'running')

    def test_remote_unread_flag_does_not_include_local_task(self):
        self.thread(); self.event('task_started'); self.event('task_complete')
        self.unread(remote=('one',))
        m = Monitor(self.home)
        self.assertEqual(m.snapshot()['sessions'], [])
        self.unread('one')
        self.assertEqual(self.status(m), 'completed')

    def test_unread_failure_hides_green_reports_problem_and_recovers(self):
        self.thread(); self.event('task_started'); self.event('task_complete')
        self.thread('active'); self.event('task_started', 'active')
        m = Monitor(self.home); m.snapshot()
        path = self.home / '.codex-global-state.json'
        for invalid in ('{broken', '{}', '[]', '{"electron-persisted-atom-state":{"unread-thread-ids-by-host-v1":{"local":"one"}}}'):
            with self.subTest(invalid=invalid):
                path.write_text(invalid, encoding='utf-8')
                snapshot = m.snapshot()
                self.assertTrue(snapshot.get('read_state_error'))
                self.assertEqual([s['id'] for s in snapshot['sessions']], ['active'])
        path.unlink()
        self.assertTrue(m.snapshot().get('read_state_error'))
        self.unread('one')
        self.assertIsNone(m.snapshot().get('read_state_error'))
        self.assertEqual({s['id'] for s in m.snapshot()['sessions']}, {'one', 'active'})

    def test_only_running_and_completed_use_tiles(self):
        self.thread(); self.event('task_started')
        m = Monitor(self.home); m.snapshot()
        self.event('turn_aborted')
        snapshot = m.snapshot()
        self.assertEqual(snapshot['sessions'], [])
        self.assertEqual([s['status'] for s in snapshot.get('attention', [])], ['interrupted'])
        self.unread()
        self.assertEqual(m.snapshot().get('attention'), [])

    def test_reader_never_writes_codex_read_state(self):
        self.thread(); self.event('task_started')
        path = self.home / '.codex-global-state.json'
        before, stamp = path.read_bytes(), path.stat().st_mtime_ns
        m = Monitor(self.home); m.snapshot(); self.event('task_complete'); m.snapshot()
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), (before, stamp))

    def test_subagents_and_archives_not_displayed(self):
        self.thread('root'); self.event('task_started', 'root')
        self.thread('child', source='{"subagent":{"thread_spawn":{}}}'); self.event('task_started', 'child')
        self.thread('archive', archived=1); self.event('task_started', 'archive')
        m = Monitor(self.home)
        self.assertEqual([s['id'] for s in m.snapshot()['sessions']], ['root'])

    def test_interruption_is_not_success(self):
        self.thread(); self.event('task_started')
        m = Monitor(self.home); m.snapshot()
        self.event('turn_aborted')
        self.assertEqual(self.status(m), 'interrupted')

    def test_incomplete_line_waits_until_newline(self):
        p = self.thread(); self.event('task_started')
        m = Monitor(self.home); m.snapshot()
        line = json.dumps({'type':'event_msg','payload':{'type':'task_complete','turn_id':'t1'}})
        with p.open('a') as f: f.write(line[:25])
        self.assertEqual(self.status(m), 'running')
        with p.open('a') as f: f.write(line[25:] + '\n')
        self.assertEqual(self.status(m), 'completed')

    def test_initial_partial_line_is_not_lost(self):
        p = self.thread(); self.event('task_started')
        line = json.dumps({'type':'event_msg','payload':{'type':'task_complete','turn_id':'t1'}})
        with p.open('a') as f: f.write(line[:20])
        m = Monitor(self.home)
        self.assertEqual(self.status(m), 'running')
        with p.open('a') as f: f.write(line[20:] + '\n')
        self.assertEqual(self.status(m), 'completed')

    def test_replaced_file_restarts_reader(self):
        p = self.thread(); self.event('task_started')
        m = Monitor(self.home); m.snapshot()
        replacement = p.with_suffix('.new')
        replacement.write_text(json.dumps({'type':'event_msg','payload':{'type':'turn_aborted','turn_id':'t1'}})+'\n')
        replacement.replace(p)
        self.assertEqual(self.status(m), 'interrupted')

    def test_corrupt_or_missing_data_never_keeps_green(self):
        p = self.thread(); self.event('task_started')
        m = Monitor(self.home); m.snapshot(); self.event('task_complete')
        self.assertEqual(self.status(m), 'completed')
        with p.open('a') as f: f.write('{bad json}\n')
        self.assertEqual(self.status(m), 'unknown')
        p.unlink()
        self.assertEqual(self.status(m), 'unknown')

    def test_stale_running_is_unknown_and_recovers(self):
        p = self.thread(); self.event('task_started')
        m = Monitor(self.home)
        old = time.time() - 400
        os.utime(p, (old, old))
        self.assertEqual(self.status(m), 'unknown')
        self.write({'type':'event_msg','payload':{'type':'token_count'}})
        self.assertEqual(self.status(m), 'running')

    def test_waiting_input_clears_on_matching_response(self):
        self.thread(); self.event('task_started')
        m = Monitor(self.home); m.snapshot()
        self.write({'type':'response_item','payload':{'type':'function_call','name':'request_user_input','call_id':'ask'}})
        self.assertEqual(self.status(m), 'waiting')
        self.write({'type':'response_item','payload':{'type':'function_call_output','call_id':'ask'}})
        self.assertEqual(self.status(m), 'running')

    def test_long_tail_finds_start_and_never_exposes_content(self):
        self.thread(); self.event('task_started')
        self.write({'type':'response_item','payload':{'type':'message','content':'PRIVATE' * 100000}})
        m = Monitor(self.home); snapshot = m.snapshot()
        self.assertEqual(snapshot['sessions'][0]['status'], 'running')
        self.assertNotIn('PRIVATE', json.dumps(snapshot))
        self.assertNotIn('private prompt', json.dumps(snapshot))

    def test_database_failure_masks_existing_status(self):
        self.thread(); self.event('task_started')
        m = Monitor(self.home); m.snapshot()
        self.db.execute('DROP TABLE threads'); self.db.commit()
        snapshot = m.snapshot()
        self.assertTrue(snapshot['error'])
        self.assertEqual(snapshot['sessions'], [])

    @unittest.skipUnless(Path('/dev/fd').exists(), 'POSIX descriptor check')
    def test_repeated_reads_close_database_connections(self):
        self.thread()
        m = Monitor(self.home)
        gc.collect()
        enabled = gc.isenabled()
        gc.disable()
        try:
            before = len(os.listdir('/dev/fd'))
            for _ in range(30):
                m.metadata()
            self.assertLessEqual(len(os.listdir('/dev/fd')), before + 2)
        finally:
            if enabled:
                gc.enable()
            gc.collect()


if __name__ == '__main__':
    unittest.main()
