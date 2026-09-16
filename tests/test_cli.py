import concurrent.futures
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))
from codexremote.cli import control
from websockets.sync.server import unix_serve


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.state = self.base / 'state'
        self.project = self.base / 'project'
        self.project.mkdir()
        self.other = self.base / 'other'
        self.other.mkdir()
        self.fake = self.base / 'codex'
        self.fake.write_text('#!' + sys.executable + '\n' + Path(__file__).with_name('fake_codex.py').read_text())
        self.fake.chmod(0o700)
        self.environment = dict(os.environ, PYTHONPATH=str(PACKAGE_ROOT), CODEXREMOTE_STATE_DIR=str(self.state))
        self.command_log = self.base / 'commands.jsonl'
        self.environment['CODEXREMOTE_TEST_COMMAND_LOG'] = str(self.command_log)
        self.environment['CODEXREMOTE_TEST_SOCKET'] = str(self.base / 'daemon.sock')
        self.backend = unix_serve(self.handle_peer, path=self.environment['CODEXREMOTE_TEST_SOCKET'])
        self.worker = threading.Thread(target=self.backend.serve_forever, daemon=True)
        self.worker.start()

    def handle_peer(self, connection):
        process = subprocess.Popen([sys.executable, str(self.fake)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True)
        def read():
            try:
                for line in process.stdout:
                    connection.send(line)
            except Exception:
                pass
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            for message in connection:
                process.stdin.write(message + '\n')
                process.stdin.flush()
        finally:
            process.terminate()
            process.wait(timeout=5)
            reader.join(timeout=5)
            process.stdin.close()
            process.stdout.close()

    def tearDown(self):
        self.run_cli('stop')
        self.backend.shutdown()
        self.worker.join(timeout=5)
        self.temp.cleanup()

    def run_cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, '-m', 'codexremote', *args], cwd=str(cwd or self.project),
                              env=self.environment, text=True, capture_output=True, timeout=50)

    def start(self):
        result = self.run_cli('--codex', str(self.fake), '--port', '0')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def record(self):
        return json.loads((self.state / 'runtime.json').read_text())

    def test_background_start_restart_preserves_config_and_stop_from_any_cwd(self):
        output = self.start().stdout
        first = self.record()
        self.assertIn((self.state / 'access-token').read_text().strip(), output)
        self.assertIn('http://', output)
        self.assertEqual(first['config']['host'], '0.0.0.0')
        self.assertEqual(first['config']['roots'], [str(self.project)])
        self.assertEqual((self.state / 'runtime.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.state / 'access-token').stat().st_mode & 0o777, 0o600)
        url = 'http://127.0.0.1:' + str(first['config']['port'])
        with urllib.request.urlopen(url) as response:
            self.assertIn(b'Slash commands', response.read())
        with urllib.request.urlopen(url + '/app.js') as response:
            self.assertIn(b'executeSlash', response.read())
        self.assertEqual(self.run_cli(cwd=self.other).returncode, 0)
        self.assertEqual(self.record()['instance'], first['instance'])
        restarted = self.run_cli('restart', cwd=self.other)
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        second = self.record()
        self.assertNotEqual(second['instance'], first['instance'])
        self.assertEqual(second['config']['roots'], [str(self.project)])
        self.assertEqual(self.run_cli('stop', cwd=self.other).returncode, 0)
        self.assertFalse((self.state / 'runtime.json').exists())
        self.assertIsNone(control(second))
        self.assertEqual(self.run_cli('status').returncode, 1)
        self.assertEqual(self.run_cli('stop').returncode, 0)

    def test_default_attaches_local_daemon_and_never_stops_it(self):
        output = self.start().stdout
        self.assertIn('local terminal daemon', output)
        self.assertTrue(self.record()['config']['local_daemon'])
        self.assertEqual(self.run_cli('restart').returncode, 0)
        self.assertEqual(self.run_cli('stop').returncode, 0)
        commands = [json.loads(line) for line in self.command_log.read_text().splitlines()]
        self.assertEqual(commands.count(['app-server', 'daemon', 'start']), 2)
        self.assertNotIn(['app-server', '--listen', 'stdio://'], commands)
        self.assertFalse(any('stop' in c or 'restart' in c for c in commands))

    def test_unsupported_codex_restart_leaves_running_web_client_alive(self):
        self.start()
        before = self.record()
        old_codex = self.base / 'old-codex'
        old_codex.write_text('#!/bin/sh\nexit 2\n')
        old_codex.chmod(0o700)
        result = self.run_cli('restart', '--codex', str(old_codex))
        self.assertEqual(result.returncode, 1)
        self.assertIn('Update Codex', result.stderr)
        self.assertEqual(self.record()['instance'], before['instance'])
        self.assertIsNotNone(control(before))

    def test_restart_overrides_and_validation_before_stopping(self):
        self.start()
        initial = self.record()
        result = self.run_cli('restart', '--root', str(self.base / 'missing'))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.record()['instance'], initial['instance'])
        result = self.run_cli('restart', '--host', '127.0.0.1', '--root', str(self.other))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record()['config']['host'], '127.0.0.1')
        self.assertEqual(self.record()['config']['roots'], [str(self.other)])

    def test_simultaneous_start_manages_one_instance(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.run_cli('--codex', str(self.fake), '--port', '0'), range(2)))
        self.assertEqual([r.returncode for r in results], [0, 0])
        self.assertTrue(any('already running' in r.stdout for r in results))
        self.assertIsNotNone(control(self.record()))

    def test_bad_control_credentials_cannot_stop_server(self):
        self.start()
        record = self.record()
        self.assertIsNone(control(dict(record, secret='wrong'), 'stop'))
        self.assertIsNotNone(control(record))

    def test_stale_pid_is_never_signalled(self):
        self.state.mkdir()
        (self.state / 'runtime.json').write_text(json.dumps({'pid':os.getpid(), 'control_port':0, 'instance':'stale', 'secret':'stale'}))
        result = self.run_cli('stop')
        self.assertEqual(result.returncode, 0)
        self.assertIn('already stopped', result.stdout)

    def test_missing_codex_and_busy_port_fail_cleanly(self):
        result = self.run_cli('--codex', str(self.base / 'missing'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('Codex CLI was not found', result.stderr)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            result = self.run_cli('--codex', str(self.fake), '--host', '127.0.0.1', '--port', str(listener.getsockname()[1]))
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.state / 'runtime.json').exists())

    def test_help_does_not_start_server(self):
        for arg in ['-h', 'help', '--version']:
            result = self.run_cli(arg)
            self.assertEqual(result.returncode, 0)
            self.assertIn('codexremote', result.stdout)
        self.assertFalse(self.state.exists())

    def test_foreground_can_be_stopped_from_another_terminal(self):
        import time
        process = subprocess.Popen([sys.executable, '-m', 'codexremote', '--foreground', '--codex', str(self.fake), '--port', '0'],
                                   env=self.environment, cwd=str(self.project), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not (self.state / 'runtime.json').exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue((self.state / 'runtime.json').exists())
            self.assertEqual(self.run_cli('stop').returncode, 0)
            out, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, err)
            self.assertIn('foreground', out)
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
