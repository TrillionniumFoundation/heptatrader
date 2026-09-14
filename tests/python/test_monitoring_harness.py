"""Use actual HTTP responses to exercise monitoring harness readiness semantics."""
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
import urllib.error

PATH = Path(__file__).resolve().parents[1] / 'monitoring/process_smoke.py'
spec = importlib.util.spec_from_file_location('monitoring_process_smoke', PATH)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class MonitoringHarnessTests(unittest.TestCase):
    def test_readiness_uses_status_even_with_empty_body_and_rejects_503(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                code = 503 if self.path == '/not-ready' else 200
                self.send_response(code)
                if self.path == '/json':
                    self.send_header('Content-Type', 'application/json')
                self.end_headers()
                if self.path == '/json':
                    self.wfile.write(b'{"ready":true}')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            root = f'http://127.0.0.1:{server.server_port}'
            self.assertIs(smoke.get(root + '/ready'), True)
            self.assertEqual(smoke.get(root + '/json'), {'ready': True})
            with self.assertRaises(urllib.error.HTTPError):
                smoke.get(root + '/not-ready')
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
