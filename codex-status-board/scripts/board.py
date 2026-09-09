"""Start, inspect or stop one persistent local board; opening it never uses model polling."""
import argparse
import http.client
import json
import os
import secrets
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from contextlib import contextmanager

SERVICE = 'codex-status-board'


@contextmanager
def instance_lock(path):
    with path.open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            # Windows byte-range locks may extend past EOF; no content write is needed.
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            # On POSIX closing the file releases flock.


def request(port, path, token=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        headers = {'X-Board-Token': token} if token else {}
        conn.request('POST' if token else 'GET', path, headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError(f'看板返回 HTTP {response.status}')
        return json.loads(response.read())
    finally:
        conn.close()


def live_instance(runtime):
    try:
        state = json.loads((runtime / 'instance.json').read_text())
        if state.get('service') != SERVICE or not isinstance(state.get('port'), int):
            return None
        health = request(state['port'], '/health')
        if health.get('service') == SERVICE and health.get('pid') == state.get('pid'):
            return state
    except (OSError, ValueError, http.client.HTTPException):
        pass
    return None


def main():
    # JSON output must also work with Chinese paths under Windows redirected output.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Codex 本机开发状态看板')
    parser.add_argument('action', choices=['start', 'status', 'stop'])
    parser.add_argument('--open', action='store_true', help='在浏览器打开看板')
    parser.add_argument('--codex-home', default=os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))
    parser.add_argument('--port', type=int, default=17329)
    args = parser.parse_args()
    home = Path(args.codex_home).expanduser().resolve()
    runtime = home / 'cache/status-board'
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    with instance_lock(runtime/'launcher.lock'):
        state = live_instance(runtime)
        if args.action == 'stop':
            if state:
                request(state['port'], '/api/stop', state['token'])
                for _ in range(30):
                    if not live_instance(runtime):
                        break
                    time.sleep(.1)
                else:
                    raise SystemExit('停止请求已发送，但尚未确认退出')
            print(json.dumps({'running': False, 'message': '看板已停止' if state else '看板未运行'}, ensure_ascii=False))
            return
        if args.action == 'start' and not state:
            env = os.environ.copy()
            env['CODEX_BOARD_INSTANCE_TOKEN'] = secrets.token_urlsafe(32)
            server = Path(__file__).with_name('server.py')
            detached = ({'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                        if os.name == 'nt' else {'start_new_session': True})
            with (runtime/'server.log').open('ab') as log:
                process = subprocess.Popen(
                    [sys.executable, str(server), '--codex-home', str(home), '--runtime-dir', str(runtime), '--port', str(args.port)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env,
                    cwd=str(server.parent), **detached,
                )
            for _ in range(100):
                state = live_instance(runtime)
                if state:
                    break
                if process.poll() is not None:
                    raise SystemExit(f'看板启动失败；请检查端口 {args.port} 和日志 {runtime / "server.log"}')
                time.sleep(.1)
            if not state:
                # Only stop the child just created by this invocation, never an unrelated PID.
                process.terminate()
                process.wait(timeout=5)
                raise SystemExit('看板启动超时，请检查本机记录读取速度')
        result = {'running': bool(state)}
        if state:
            url = f'http://127.0.0.1:{state["port"]}'
            snapshot = request(state['port'], '/api/snapshot')
            result.update(url=url, pid=state['pid'], sessions=len(snapshot['sessions']), error=snapshot['error'])
            result.update(read_state_error=snapshot.get('read_state_error'), attention=len(snapshot.get('attention', [])))
            if args.open:
                result['browser_open_requested'] = bool(webbrowser.open(url))
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise SystemExit(f'看板操作失败：{exc}')
