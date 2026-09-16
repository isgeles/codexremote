"""Opt-in real-daemon attachment check, using temporary Codex state.

Run with the project's Python: python tests/local_daemon_smoke.py
Requires a managed standalone Codex installation. Copies no login credentials.
Starts an unauthenticated turn only to persist a thread; no successful model
response is required. Stops only the daemon created in the temporary home.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codexremote.server import Bridge, create_server


def main():
    executable = Path(shutil.which('codex') or '/missing/codex').resolve(strict=True)
    installation = executable.parent.parent if executable.parent.name == 'bin' else executable.parent
    previous_home = os.environ.get('CODEX_HOME')
    try:
        with tempfile.TemporaryDirectory(prefix='codexremote-daemon-') as temporary:
            root = Path(temporary)
            home = root / 'home'
            home.mkdir()
            os.environ['CODEX_HOME'] = str(home)
            managed = home / 'packages/standalone/current'
            managed.parent.mkdir(parents=True)
            managed.symlink_to(installation, target_is_directory=True)
            state = root / 'web'
            config = {'host': '127.0.0.1', 'port': 0, 'roots': [temporary], 'codex': str(executable)}
            web = peer = None
            try:
                web = create_server(config, state)
                peer = Bridge(None, root, root / 'peer.log',
                              endpoint='unix://' + str(home / 'app-server-control/app-server-control.sock'))
                thread_id = peer.call('thread/start', {'cwd': temporary})['thread']['id']
                peer.call('turn/start', {'threadId': thread_id,
                                        'input': [{'type': 'text', 'text': 'Test session persistence.'}]})
                assert web.app.bridge.call('thread/resume', {'threadId': thread_id})['thread']['id'] == thread_id
                web.server_close()
                web.app.bridge.close()
                web = None
                assert peer.call('thread/read', {'threadId': thread_id})['thread']['id'] == thread_id
                web = create_server(config, state)
                assert web.app.bridge.call('thread/resume', {'threadId': thread_id})['thread']['id'] == thread_id
                print('PASS: default attachment shares a thread; peer survives web shutdown and reattachment')
            finally:
                if web:
                    web.server_close()
                    web.app.bridge.close()
                if peer:
                    peer.close()
                subprocess.run([str(executable), 'app-server', 'daemon', 'stop'],
                               capture_output=True, timeout=20, check=True)
    finally:
        if previous_home is None:
            os.environ.pop('CODEX_HOME', None)
        else:
            os.environ['CODEX_HOME'] = previous_home


if __name__ == '__main__':
    main()
