import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'codex-status-board/scripts'))
from server import BoardServer


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = BoardServer(Path(self.tmp.name), 'test-secret', port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.port = self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, method='GET', headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        conn.request(method, path, headers=headers or {})
        res = conn.getresponse()
        result = res.status, dict(res.getheaders()), res.read()
        conn.close()
        return result

    def test_missing_codex_is_error_not_empty_success(self):
        status, headers, data = self.request('/api/snapshot')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)['error'])
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_health_identifies_service_and_page_is_served(self):
        self.assertEqual(json.loads(self.request('/health')[2])['service'], 'codex-status-board')
        self.assertEqual(self.request('/')[0], 200)
        self.assertEqual(self.request('/../SKILL.md')[0], 404)

    def test_rejects_foreign_host_and_origin(self):
        self.assertEqual(self.request('/api/snapshot', headers={'Host':'attacker.invalid'})[0], 403)
        self.assertEqual(self.request('/api/snapshot', headers={'Origin':'https://attacker.invalid'})[0], 403)
        self.assertEqual(self.request('/api/snapshot', headers={'Sec-Fetch-Site':'cross-site'})[0], 403)

    def test_stop_requires_instance_secret(self):
        self.assertEqual(self.request('/api/stop', 'POST')[0], 403)
        self.assertEqual(self.request('/api/stop', 'POST', {'X-Board-Token':'wrong'})[0], 403)
        self.assertEqual(self.request('/health')[0], 200)


if __name__ == '__main__':
    unittest.main()
