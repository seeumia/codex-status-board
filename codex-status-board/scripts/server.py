"""Loopback-only HTTP server for the Codex status board. No third-party dependencies."""
import argparse
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer

from status_reader import Monitor

SERVICE = 'codex-status-board'
VERSION = '0.2.0'
PAGE = Path(__file__).resolve().parents[1] / 'assets/index.html'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def trusted(self):
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        origin = self.headers.get('Origin')
        return (self.headers.get('Host') in hosts
                and (not origin or origin in {'http://' + host for host in hosts})
                and self.headers.get('Sec-Fetch-Site') not in {'cross-site'})

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.trusted():
            return self.send(403, {'error': '只接受本机页面请求'})
        if self.path == '/health':
            return self.send(200, {'service': SERVICE, 'version': VERSION, 'pid': os.getpid()})
        if self.path == '/api/snapshot':
            return self.send(200, self.server.snapshot())
        if self.path in ('/', '/index.html'):
            return self.send(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
        if self.path == '/favicon.ico':
            return self.send(204, b'')
        self.send(404, {'error': '页面不存在'})

    def do_POST(self):
        if not self.trusted():
            return self.send(403, {'error': '请求来源不匹配'})
        if self.path != '/api/stop':
            return self.send(404, {'error': '接口不存在'})
        token = self.headers.get('X-Board-Token', '')
        if not hmac.compare_digest(token, self.server.token):
            return self.send(403, {'error': '需要本机管理令牌'})
        self.send(200, {'stopped': True})
        threading.Thread(target=self.server.shutdown, daemon=True).start()


class BoardServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # This loopback-only service needs no reverse DNS lookup. HTTPServer's
        # default getfqdn can block startup while a system resolver times out.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def __init__(self, codex_home, token, port=17329):
        self.token = token
        self.monitor = Monitor(codex_home)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._snapshot = self.monitor.snapshot()
        super().__init__(('127.0.0.1', port), Handler)
        self._worker = threading.Thread(target=self.poll, daemon=True)
        self._worker.start()

    def poll(self):
        while not self._stop.wait(2):
            try:
                snapshot = self.monitor.snapshot()
            except Exception:
                # Never expose source paths, data, or a misleading old success state.
                snapshot = self.snapshot()
                snapshot = {**snapshot, 'error': '状态读取异常，需要检查本机记录'}
            with self._lock:
                self._snapshot = snapshot

    def snapshot(self):
        with self._lock:
            snapshot = dict(self._snapshot)
        if time.time() - snapshot['updated_at'] > 10:
            snapshot['error'] = '状态更新已暂停'
        if snapshot.get('error'):
            snapshot['sessions'] = []
            snapshot['attention'] = []
        return snapshot

    def server_close(self):
        self._stop.set()
        super().server_close()
        self._worker.join(timeout=3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--codex-home', required=True)
    parser.add_argument('--runtime-dir', required=True)
    parser.add_argument('--port', type=int, default=17329)
    args = parser.parse_args()
    token = os.environ.pop('CODEX_BOARD_INSTANCE_TOKEN', '')
    if not token:
        raise SystemExit('缺少看板实例令牌，请通过 board.py start 启动')
    runtime = Path(args.runtime_dir)
    state_path = runtime / 'instance.json'
    server = BoardServer(Path(args.codex_home), token, args.port)
    state = {'service': SERVICE, 'pid': os.getpid(), 'port': server.server_address[1], 'token': token}
    temporary = runtime / f'instance-{os.getpid()}.tmp'
    temporary.write_text(json.dumps(state))
    temporary.chmod(0o600)
    temporary.replace(state_path)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        try:
            if json.loads(state_path.read_text()).get('token') == token:
                state_path.unlink()
        except (OSError, ValueError):
            pass


if __name__ == '__main__':
    main()
