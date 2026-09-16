"""Install the wheel in a clean venv and exercise it outside the source checkout."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import urllib.request
import venv
import zipfile
from websockets.sync.server import unix_serve
from test_cli import LifecycleTests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main():
    from codexremote import __version__
    wheel = REPO / 'dist' / f'codexremote-{__version__}-py3-none-any.whl'
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert 'codexremote/web/vendor/katex/fonts/KaTeX_Main-Regular.woff2' in names
        assert 'codexremote/web/markdown.js' in names
        assert not any('.remote/' in n or 'access-token' in n or n.endswith('.log') for n in names)
    with tempfile.TemporaryDirectory(prefix='codexremote-wheel-') as folder:
        root = Path(folder)
        envdir = root / 'venv'
        venv.EnvBuilder(with_pip=True).create(envdir)
        python = envdir / 'bin/python'
        subprocess.run([str(python), '-m', 'pip', 'install', str(wheel)], check=True, capture_output=True)
        executable = envdir / 'bin/codexremote'
        fake = root / 'fake-codex'
        fake.write_text(f'#!{python}\n' + (REPO / 'tests/fake_codex.py').read_text())
        fake.chmod(0o700)
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        environment['CODEXREMOTE_STATE_DIR'] = str(root / 'state')
        environment['CODEXREMOTE_TEST_SOCKET'] = str(root / 'daemon.sock')
        fixture = LifecycleTests()
        fixture.fake = fake
        backend = unix_serve(fixture.handle_peer, path=environment['CODEXREMOTE_TEST_SOCKET'])
        worker = threading.Thread(target=backend.serve_forever, daemon=True)
        worker.start()
        def run(*args, expected=0):
            result = subprocess.run([str(executable), *args], cwd=root, env=environment, capture_output=True, text=True, timeout=40)
            assert result.returncode == expected, result.stderr
            return result.stdout
        try:
            assert 'usage:' in run('-h')
            output = run('--codex', str(fake), '--port', '0')
            token = (root / 'state/access-token').read_text().strip()
            assert token in output and 'Open:' in output and '0.0.0.0:' not in output
            for _ in range(2):
                record = json.loads((root / 'state/runtime.json').read_text())
                port = record['config']['port']
                for asset in ['/', '/markdown.js', '/vendor/markdown-it/markdown-it.min.js', '/vendor/katex/katex.min.css', '/vendor/katex/fonts/KaTeX_Main-Regular.woff2']:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}{asset}') as response:
                        assert response.status == 200 and len(response.read()) > 50
                assert token in run('restart')
            assert token in run('status')
        finally:
            run('stop')
            backend.shutdown()
            worker.join(timeout=5)
        assert 'not running' in run('status', expected=1).lower()
    print('PASS: clean pip wheel installation, bundled assets, background start/status/restart/stop outside checkout')


if __name__ == '__main__':
    main()
