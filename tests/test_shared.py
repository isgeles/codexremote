"""Shared-server transport/lifecycle tests; install the [shared] extra to run."""
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codexremote.server import Bridge, RpcError

try:
    from websockets.sync.client import connect
    from websockets.sync.server import serve
    from websockets.exceptions import ConnectionClosed
except ImportError:
    serve = None


@unittest.skipUnless(serve, 'requires codexremote[shared]')
class SharedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.connections = set()
        self.lock = threading.Lock()
        self.turns = 0
        self.headers = []
        self.backend = serve(self.handle, '127.0.0.1', 0)
        self.endpoint = 'ws://127.0.0.1:' + str(self.backend.socket.getsockname()[1])
        self.worker = threading.Thread(target=self.backend.serve_forever, daemon=True)
        self.worker.start()
        self.bridges = []
        self.environment = dict(os.environ, CODEXREMOTE_STATE_DIR=str(self.root / 'state'),
                                PYTHONPATH=str(Path(__file__).resolve().parents[1]))

    def tearDown(self):
        self.cli('stop')
        for bridge in self.bridges:
            bridge.close()
        self.backend.shutdown()
        self.worker.join(timeout=5)
        self.temp.cleanup()

    def broadcast(self, message):
        with self.lock:
            peers = list(self.connections)
        for peer in peers:
            try:
                peer.send(json.dumps(message))
            except ConnectionClosed:
                pass

    def handle(self, connection):
        with self.lock:
            self.connections.add(connection)
            self.headers.append(connection.request.headers.get('Authorization'))
        try:
            for raw in connection:
                msg = json.loads(raw)
                method = msg.get('method')
                if method == 'initialized':
                    continue
                if method == 'test/error':
                    connection.send(json.dumps({'id': msg['id'], 'error': {'message': 'test error'}}))
                    continue
                if method == 'turn/start':
                    self.turns += 1
                    result = {'turn': {'id': str(self.turns), 'status': 'inProgress', 'items': []}}
                    self.broadcast({'method': 'turn/started', 'params': {'threadId': 'shared', **result}})
                    self.broadcast({'id': 'approval', 'method': 'item/commandExecution/requestApproval',
                                    'params': {'threadId': 'shared', 'turnId': str(self.turns)}})
                elif method == 'thread/resume':
                    result = {'thread': {'id': 'shared', 'turns': [], 'status': {'type': 'active'}}}
                elif method == 'thread/read':
                    result = {'thread': {'id': 'shared', 'turnCount': self.turns}}
                elif method is None:
                    self.broadcast({'method': 'serverRequest/resolved', 'params': {'requestId': msg['id']}})
                    continue
                else:
                    result = {'ok': True}
                connection.send(json.dumps({'id': msg['id'], 'result': result}))
        except ConnectionClosed:
            pass
        finally:
            with self.lock:
                self.connections.discard(connection)

    def bridge(self, token_file=None):
        # Attachment must not launch any Codex subprocess, even during cleanup.
        with patch('codexremote.server.subprocess.Popen', side_effect=AssertionError('Unexpected process launch')):
            bridge = Bridge(None, self.root, self.root / 'codex.log', self.endpoint, token_file)
        self.bridges.append(bridge)
        return bridge

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'codexremote', *args], cwd=self.root,
                              env=self.environment, capture_output=True, text=True, timeout=50)

    def test_shared_history_events_approvals_and_disconnect(self):
        first, second = self.bridge(), self.bridge()
        self.assertEqual(first.call('thread/resume', {'threadId': 'shared'})['thread']['id'], 'shared')
        second.call('thread/resume', {'threadId': 'shared'})
        first.call('turn/start', {'threadId': 'shared'})
        deadline = time.monotonic() + 5
        while not second.pending and time.monotonic() < deadline:
            second.poll(second.sequence, wait=.1)
        self.assertIn('approval', second.pending)
        second.respond('approval', {'decision': 'decline'})
        while first.pending and time.monotonic() < deadline:
            first.poll(first.sequence, wait=.1)
        self.assertFalse(first.pending)
        with self.assertRaises(RpcError):
            second.call('test/error', {})
        second.close()
        first.call('turn/start', {'threadId': 'shared'})
        self.assertEqual(first.call('thread/read', {})['thread']['turnCount'], 2)

    def test_authenticated_connection_token_is_not_logged(self):
        token = self.root / 'backend-token'
        token.write_text('test-secret-bearer-token')
        self.bridge(str(token))
        self.assertIn('Bearer test-secret-bearer-token', self.headers)
        self.assertFalse((self.root / 'codex.log').exists())

    def test_web_restart_and_stop_leave_other_client_and_backend_alive(self):
        owner = self.bridge()
        result = self.cli('--connect', self.endpoint, '--codex', '/missing/codex', '--port', '0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('only this web client', result.stdout)
        self.assertIn('codexremote stop --state-dir ' + str(self.root / 'state'), result.stdout)
        for action in ['restart', 'stop']:
            self.assertEqual(self.cli(action).returncode, 0)
            owner.call('turn/start', {'threadId': 'shared'})
        self.assertEqual(owner.call('thread/read', {})['thread']['turnCount'], 2)

    def test_validation_does_not_restart_running_client(self):
        self.assertEqual(self.cli('--connect', self.endpoint, '--port', '0').returncode, 0)
        record = (self.root / 'state/runtime.json').read_text()
        for endpoint in ['http://127.0.0.1:4500', 'ws://192.168.1.3:4500', 'wss://user:secret@example.com', 'ws://localhost:4500/?token=secret']:
            result = self.cli('restart', '--connect', endpoint)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('user:secret', result.stderr)
            self.assertEqual((self.root / 'state/runtime.json').read_text(), record)

    def test_http_client_controls_the_shared_thread(self):
        owner = self.bridge()
        owner.call('turn/start', {'threadId': 'shared'})
        result = self.cli('--connect', self.endpoint, '--port', '0')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.root / 'state'
        record = json.loads((state / 'runtime.json').read_text())
        base = 'http://127.0.0.1:' + str(record['config']['port'])
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def post(path, data):
            request = urllib.request.Request(base + path, json.dumps(data).encode(), {'Content-Type': 'application/json'})
            return json.load(client.open(request, timeout=10))
        post('/api/login', {'token': (state / 'access-token').read_text().strip()})
        data = post('/api/rpc', {'method': 'thread/resume', 'params': {'threadId': 'shared'}})
        self.assertEqual(data['result']['thread']['id'], 'shared')
        post('/api/rpc', {'method': 'turn/start', 'params': {'threadId': 'shared'}})
        self.assertEqual(owner.call('thread/read', {})['thread']['turnCount'], 2)


if __name__ == '__main__':
    unittest.main()
