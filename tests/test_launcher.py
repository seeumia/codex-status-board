import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

SCRIPT = Path(__file__).resolve().parents[1] / 'codex-status-board/scripts/board.py'


class LauncherTests(unittest.TestCase):
    def test_portable_copy_and_simultaneous_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / '另一个用户 Skill'
            shutil.copytree(SCRIPT.parent.parent, package, ignore=shutil.ignore_patterns('__pycache__'))
            home = root / '用户资料 Codex'
            home.mkdir()
            with sqlite3.connect(home/'state_5.sqlite') as db:
                db.execute('CREATE TABLE threads (id TEXT, name TEXT, title TEXT, source TEXT, archived INTEGER, rollout_path TEXT)')
                log = home/'session.jsonl'
                log.write_text(json.dumps({'type':'event_msg','payload':{'type':'task_started','turn_id':'t1'}})+'\n', encoding='utf-8')
                db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)', ('test','可移植会话','','vscode',0,str(log)))
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]

            def call(action):
                result = subprocess.run([sys.executable, str(package/'scripts/board.py'), action, '--codex-home', str(home), '--port', str(port)], capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
                return json.loads(result.stdout)

            try:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    starts = list(pool.map(call, ['start'] * 3))
                self.assertEqual(len({r['pid'] for r in starts}), 1)
                self.assertTrue(all(r['running'] and r['error'] is None and r['sessions'] == 1 for r in starts))
            finally:
                call('stop')

    def test_start_reuses_process_and_stop_only_its_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with sqlite3.connect(home/'state_5.sqlite') as db:
                db.execute('CREATE TABLE threads (id TEXT, name TEXT, title TEXT, source TEXT, archived INTEGER, rollout_path TEXT)')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]

            def call(action):
                result = subprocess.run([sys.executable, str(SCRIPT), action, '--codex-home', str(home), '--port', str(port)], capture_output=True, text=True, encoding='utf-8', timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            try:
                first = call('start')
                self.assertTrue(first['running'])
                self.assertIsNone(first['error'])
                self.assertEqual(first['pid'], call('start')['pid'])
                self.assertTrue(call('status')['running'])
                self.assertFalse(call('stop')['running'])
                self.assertFalse(call('status')['running'])
            finally:
                call('stop')


if __name__ == '__main__':
    unittest.main()
