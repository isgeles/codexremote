"""Opt-in attachment test on an isolated real Codex server.

Copies only login credentials into a private temporary Codex home. No existing
daemon, session, configuration, or web server is controlled. Uses two short model
turns. All subprocesses and temporary credentials are cleaned up on exit.

Run: uv run --python 3.12 --no-project --with 'websockets>=15,<18' python tests/shared_session_smoke.py
"""
import json
import http.cookiejar
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import sys
import urllib.request
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codexremote.server import create_server


class WebClient:
    """Use the real web server's HTTP bridge as the second client."""
    def __init__(self, endpoint, root, workspace):
        state = root / 'web-state'
        self.server = create_server({'host': '127.0.0.1', 'port': 0, 'roots': [str(workspace)],
                                     'codex': '/unused/codex', 'connect': endpoint}, state)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.cursor = 0
        self.api('/api/login', {'token': (state / 'access-token').read_text().strip()})

    def api(self, path, data=None):
        request = urllib.request.Request(self.base + path, None if data is None else json.dumps(data).encode(),
                                         {'Content-Type': 'application/json'})
        return json.load(self.opener.open(request, timeout=45))

    def call(self, method, params):
        return self.api('/api/rpc', {'method': method, 'params': params})['result']

    def wait_turn(self, ident):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            events = self.api('/api/events?after=' + str(self.cursor))
            self.cursor = events['cursor']
            for event in events['events']:
                message = event['message']
                if message.get('method') == 'turn/completed' and message['params']['turn']['id'] == ident:
                    assert message['params']['turn']['status'] == 'completed', message['params']['turn'].get('error')
                    return
        raise TimeoutError('Web client did not receive turn completion')

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.app.bridge.close()
        self.worker.join(timeout=5)


class Client:
    def __init__(self, endpoint):
        self.context = connect(endpoint, proxy=None, max_size=32 * 1024 * 1024, close_timeout=2)
        self.connection = self.context.__enter__()
        self.messages = []
        self.pending = {}
        self.serial = 0
        self.lock = threading.Lock()
        self.closed = False
        threading.Thread(target=self.read, daemon=True).start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'codexremote_attachment_test', 'version': '0.1.0'},
                                     'capabilities': {'experimentalApi': True}})
            self.send({'method': 'initialized', 'params': {}})
        except BaseException:
            self.close()
            raise

    def send(self, message):
        with self.lock:
            self.connection.send(json.dumps(message))

    def read(self):
        try:
            for line in self.connection:
                message = json.loads(line)
                if 'method' not in message and message.get('id') in self.pending:
                    self.pending[message['id']].put(message)
                elif message.get('method') == 'currentTime/read' and 'id' in message:
                    self.send({'id': message['id'], 'result': {'currentTimeAt': int(time.time())}})
                else:
                    self.messages.append(message)
        except ConnectionClosed:
            if not self.closed:
                for waiter in list(self.pending.values()):
                    waiter.put({'error': 'Test connection closed unexpectedly'})

    def call(self, method, params):
        self.serial += 1
        ident = self.serial
        waiter = self.pending[ident] = queue.Queue()
        try:
            self.send({'id': ident, 'method': method, 'params': params})
            result = waiter.get(timeout=40)
            if 'error' in result:
                raise RuntimeError(str(result['error']))
            return result['result']
        finally:
            self.pending.pop(ident, None)

    def wait_turn(self, ident):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            for message in list(self.messages):
                if message.get('method') == 'turn/completed' and message['params']['turn']['id'] == ident:
                    turn = message['params']['turn']
                    assert turn['status'] == 'completed', turn.get('error')
                    return
            time.sleep(.1)
        raise TimeoutError('Test turn did not complete')

    def close(self):
        self.closed = True
        self.context.__exit__(None, None, None)


def terminate(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def main():
    binary = shutil.which('codex')
    assert binary
    clients = []
    with tempfile.TemporaryDirectory(prefix='codexremote-attach-') as directory:
        root = Path(directory)
        task_codex_home, workspace = root / 'codex-home', root / 'workspace'
        task_codex_home.mkdir(mode=0o700)
        workspace.mkdir()
        original = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
        shutil.copyfile(original / 'auth.json', task_codex_home / 'auth.json')
        (task_codex_home / 'auth.json').chmod(0o600)
        environment = dict(os.environ, CODEX_HOME=str(task_codex_home))
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        endpoint = f'ws://127.0.0.1:{port}'
        with (root / 'server.log').open('wb') as log:
            server = subprocess.Popen([binary, 'app-server', '--listen', endpoint],
                                      cwd=workspace, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 30
                while True:
                    assert server.poll() is None, 'Isolated server exited'
                    assert time.monotonic() < deadline, 'Isolated server did not start'
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2):
                            break
                    except OSError:
                        time.sleep(.1)
                first = Client(endpoint)
                clients.append(first)
                models = first.call('model/list', {})['data']
                model = next((m['model'] for m in models if m['model'] == 'gpt-5.6-terra'),
                             next(m['model'] for m in models if m.get('isDefault')))
                thread = first.call('thread/start', {'cwd': str(workspace), 'model': model,
                    'sandbox': 'read-only', 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'})['thread']
                ident = thread['id']
                print('Created isolated test thread; model:', model, flush=True)
                first_turn = first.call('turn/start', {'threadId': ident, 'input': [
                    {'type': 'text', 'text': 'Reply with exactly ATTACH_FIRST_OK. Do not use tools or modify files.', 'text_elements': []}]})['turn']
                second = WebClient(endpoint, root, workspace)
                clients.append(second)
                resumed = second.call('thread/resume', {'threadId': ident})['thread']
                assert resumed['id'] == ident
                print('Real web client attached to the same thread while the first remained connected; state:', resumed['status'], flush=True)
                first.wait_turn(first_turn['id'])
                second.wait_turn(first_turn['id'])
                print('Both clients received completion of the same turn.', flush=True)
                second_turn = second.call('turn/start', {'threadId': ident, 'input': [
                    {'type': 'text', 'text': 'Reply with exactly ATTACH_SECOND_OK. Do not use tools or modify files.', 'text_elements': []}]})['turn']
                first.close()
                clients.remove(first)
                second.wait_turn(second_turn['id'])
                assert server.poll() is None
                history = second.call('thread/read', {'threadId': ident, 'includeTurns': True})['thread']
                if history.get('historyMode') == 'paginated':
                    history['turns'] = second.call('thread/turns/list', {'threadId': ident, 'itemsView': 'full', 'limit': 10})['data']
                replies = '\n'.join(item.get('text', '') for turn in history['turns'] for item in turn['items'] if item['type'] == 'agentMessage')
                assert 'ATTACH_FIRST_OK' in replies and 'ATTACH_SECOND_OK' in replies
                print('PASS: same live thread, two attached clients, shared events, second-client continuation, first-client disconnect preserves work.', flush=True)
            finally:
                for client in clients:
                    client.close()
                terminate(server)
        print('Cleaned up only the isolated test server and its temporary state.', flush=True)


if __name__ == '__main__':
    main()
