"""Installation entry point and single-user background service lifecycle."""
import argparse
from contextlib import contextmanager
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from . import __version__
from .server import RpcError, create_server, ensure_token


def default_state_dir():
    override = os.environ.get('CODEXREMOTE_STATE_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'codexremote'
    return Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local' / 'state'))) / 'codexremote'


def parser():
    result = argparse.ArgumentParser(
        prog='codexremote', description='Use your local Codex from a browser on your laptop or phone.',
        epilog='Examples:\n  codexremote\n  codexremote --root ~/projects\n  codexremote stop\n  codexremote restart\n  codexremote status\n  codexremote --host 127.0.0.1 --foreground',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument('command', nargs='?', default='start', choices=['start', 'stop', 'restart', 'status', 'help'], help='Action (default: start in the background)')
    result.add_argument('--version', action='version', version='codexremote ' + __version__)
    result.add_argument('--host', help='Listen address (default: 0.0.0.0, all IPv4 interfaces)')
    result.add_argument('--port', type=int, help='Web port (default: 8787; 0 selects an available port)')
    result.add_argument('--root', action='append', help='Share a project folder; repeat for multiple folders (default: current directory)')
    result.add_argument('--state-dir', default=None, help='Runtime data folder (default: per-user state directory)')
    result.add_argument('--codex', help='Codex executable path (default: codex on PATH)')
    result.add_argument('--connect', help='Attach to an existing ws:// or wss:// Codex app server; never manage its process')
    result.add_argument('--connect-token-file', help='Bearer-token file for the shared Codex server (distinct from the browser access token)')
    result.add_argument('--public-url', help='External browser origin, e.g. https://codex.example.com')
    result.add_argument('--tls-cert', help='HTTPS certificate file; use with --tls-key')
    result.add_argument('--tls-key', help='HTTPS private key file; use with --tls-cert')
    result.add_argument('--foreground', action='store_true', help='Run in this terminal instead of the background')
    return result


@contextmanager
def file_lock(path, blocking=True):
    import fcntl
    with os.fdopen(os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600), 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield stream
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def daemon_locked(state):
    try:
        with file_lock(state / 'daemon.lock', blocking=False):
            return False
    except BlockingIOError:
        return True


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def write_json(path, value):
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with os.fdopen(os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
        os.replace(str(temporary), str(path))
    finally:
        temporary.unlink(missing_ok=True)


def control(record, action='status'):
    """Authenticate the instance before controlling it; never signal a stored PID."""
    if not isinstance(record, dict):
        return None
    try:
        port = int(record['control_port'])
        if not 1 <= port <= 65535:
            return None
        with socket.create_connection(('127.0.0.1', port), timeout=2) as connection:
            connection.sendall((json.dumps({'secret': record['secret'], 'action': action}) + '\n').encode())
            with connection.makefile('rb') as stream:
                response = json.loads(stream.readline(8192))
        if response.get('instance') == record['instance'] and response.get('ok') is True:
            return response
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def running(state):
    record = read_json(state / 'runtime.json')
    return record if control(record) else None


def resolve_config(args, saved=None):
    config = {'host': '0.0.0.0', 'port': 8787, 'roots': [str(Path.cwd())],
              'codex': 'codex', 'public_url': None, 'tls_cert': None, 'tls_key': None,
              'connect': None, 'connect_token_file': None}
    if saved:
        config.update({key: saved[key] for key in config if key in saved})
    for name in ['host', 'port', 'codex', 'public_url', 'tls_cert', 'tls_key', 'connect', 'connect_token_file']:
        value = getattr(args, name)
        if value is not None:
            config[name] = None if value == '' and name in {'public_url', 'tls_cert', 'tls_key', 'connect', 'connect_token_file'} else value
    if args.root:
        config['roots'] = args.root
    config['roots'] = [str(Path(p).expanduser().resolve(strict=True)) for p in config['roots']]
    if not all(Path(p).is_dir() for p in config['roots']):
        raise ValueError('Each --root must be a directory.')
    if not isinstance(config['port'], int) or not 0 <= config['port'] <= 65535:
        raise ValueError('--port must be between 0 and 65535.')
    if config['host'] != 'localhost':
        try:
            ipaddress.ip_address(config['host'])
        except ValueError:
            raise ValueError('--host must be an IP address or localhost. Use --public-url for a browser hostname.')
    if config['connect']:
        u = urlsplit(config['connect'])
        if u.scheme not in {'ws', 'wss'} or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('--connect must be a ws:// or wss:// endpoint without embedded credentials, query, or fragment.')
        _ = u.port  # Validate the port before stopping a running web client.
        if u.scheme == 'ws':
            try:
                loopback = ipaddress.ip_address(u.hostname).is_loopback
            except ValueError:
                loopback = u.hostname == 'localhost'
            if not loopback:
                raise ValueError('Use wss:// for a remote Codex server, or ws://127.0.0.1 through an SSH tunnel.')
    else:
        executable = shutil.which(os.path.expanduser(config['codex']))
        if not executable:
            raise ValueError('Codex CLI was not found. Install Codex, run codex login, then retry (or use --codex /path/to/codex).')
        config['codex'] = str(Path(executable).resolve())
        try:
            probe = subprocess.run([config['codex'], 'app-server', 'daemon', 'start', '--help'],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=10)
        except subprocess.TimeoutExpired:
            raise ValueError('Checking Codex daemon support timed out; no running server was stopped.') from None
        if probe.returncode:
            raise ValueError('Update Codex: the default terminal/phone connection requires app-server daemon support.')
    try:
        import websockets.sync.client
    except ImportError:
        raise ValueError('Reinstall Codex Remote with its websockets dependency.') from None
    config['local_daemon'] = not bool(config['connect'])
    if config['connect_token_file']:
        if not config['connect']:
            raise ValueError('--connect-token-file requires --connect.')
        config['connect_token_file'] = str(Path(config['connect_token_file']).expanduser().resolve(strict=True))
    if bool(config['tls_cert']) != bool(config['tls_key']):
        raise ValueError('--tls-cert and --tls-key must be provided together.')
    for name in ['tls_cert', 'tls_key']:
        if config[name]:
            config[name] = str(Path(config[name]).expanduser().resolve(strict=True))
    if config['public_url']:
        u = urlsplit(config['public_url'])
        if u.scheme not in {'http', 'https'} or not u.netloc or u.path not in {'', '/'} or u.query or u.fragment or u.username:
            raise ValueError('--public-url must be an HTTP(S) origin without a path.')
        config['public_url'] = config['public_url'].rstrip('/')
    return config


def browser_urls(config):
    if config.get('public_url'):
        return [config['public_url']]
    host, port = config['host'], config['port']
    addresses = []
    if host == '0.0.0.0':
        # UDP connect selects the outgoing interface; it sends no packets.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
                connection.connect(('192.0.2.1', 80))
                addresses.append(connection.getsockname()[0])
        except OSError:
            pass
        try:
            addresses.extend(socket.gethostbyname_ex(socket.gethostname())[2])
        except OSError:
            pass
        addresses = [ip for ip in addresses if not ipaddress.ip_address(ip).is_loopback and ip != '0.0.0.0']
        addresses.append('127.0.0.1')
    elif host == '::':
        try:
            addresses.extend(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6))
        except OSError:
            pass
        addresses.append('::1')
    else:
        addresses = [host]
    scheme = 'https' if config.get('tls_cert') else 'http'
    return ['{}://{}:{}'.format(scheme, '[' + ip + ']' if ':' in ip else ip, port) for ip in dict.fromkeys(addresses)]


def print_status(record, state, title='Codex Remote is running'):
    print('\n' + title)
    for url in browser_urls(record['config']):
        print('  Open:  ' + url)
    print('  Token: ' + (state / 'access-token').read_text().strip())
    print('  Folders: ' + ', '.join(record['config']['roots']))
    if record['config'].get('connect'):
        print('  Shared Codex: ' + record['config']['connect'])
        print('  Stop/restart controls only this web client; the shared Codex server keeps running.')
    elif record['config'].get('local_daemon'):
        print('  Shared Codex: local terminal daemon')
        print('  Open the same chat on your phone; keep using codex normally in your terminal.')
        print('  Stop/restart controls only this web client; terminal sessions keep running.')
    else:
        print('  This running instance uses the older private-server mode. Restart applies the new shared default, but ends work owned by this old instance.')
    print('  Log: ' + str(state / 'server.log'))
    if not record['config'].get('connect'):
        print('  Codex log: ' + str(state / 'codex.log'))
    suffix = ' --state-dir ' + shlex.quote(str(state))
    executable = shlex.quote(sys.argv[0]) if Path(sys.argv[0]).name == 'codexremote' else 'codexremote'
    print('\n  Stop: ' + executable + ' stop' + suffix + '   |   Restart: ' + executable + ' restart' + suffix + '\n')


class ControlHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        try:
            request = json.loads(self.rfile.readline(8192))
            secret = request.get('secret')
            if not isinstance(secret, str) or not hmac.compare_digest(secret.encode(), self.server.record['secret'].encode()):
                self.wfile.write(b'{"ok":false}\n')
                return
            action = request.get('action')
            response = {'ok': action in {'status', 'stop'}, 'instance': self.server.record['instance']}
            self.wfile.write((json.dumps(response) + '\n').encode())
            self.wfile.flush()
            if action == 'stop':
                self.server.web.shutdown()
        except (OSError, ValueError, TypeError, AttributeError):
            return


class ControlServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False


def serve(config, state, foreground=False, on_ready=None):
    with file_lock(state / 'daemon.lock', blocking=False):
        web = controller = None
        record = {'instance': secrets.token_hex(16), 'secret': secrets.token_urlsafe(32), 'pid': os.getpid()}
        old_handlers = {}
        try:
            web = create_server(config, state)
            controller = ControlServer(('127.0.0.1', 0), ControlHandler)
            config = dict(config, port=web.server_address[1])
            record.update({'control_port': controller.server_address[1], 'config': config})
            controller.record, controller.web = record, web
            threading.Thread(target=controller.serve_forever, daemon=True).start()
            for sig in [signal.SIGTERM, signal.SIGINT]:
                old_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, lambda *_: threading.Thread(target=web.shutdown, daemon=True).start())
            write_json(state / 'runtime.json', record)
            if on_ready:
                on_ready()
            if foreground:
                print_status(record, state, 'Codex Remote is running in the foreground (Ctrl+C to stop)')
            web.serve_forever(poll_interval=0.2)
        finally:
            if controller:
                controller.shutdown()
                controller.server_close()
            if web:
                web.server_close()
                web.app.bridge.close()
            current = read_json(state / 'runtime.json')
            if current and current.get('instance') == record['instance']:
                (state / 'runtime.json').unlink(missing_ok=True)
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)


def start(config, state):
    ensure_token(state)
    write_json(state / 'config.json', config)
    environment = os.environ.copy()
    # Also supports running directly from a source checkout, from any cwd.
    package_parent = str(Path(__file__).resolve().parent.parent)
    environment['PYTHONPATH'] = package_parent + (os.pathsep + environment['PYTHONPATH'] if environment.get('PYTHONPATH') else '')
    with os.fdopen(os.open(str(state / 'server.log'), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'ab') as log:
        process = subprocess.Popen([sys.executable, '-m', 'codexremote', '_serve', '--state-dir', str(state)],
                                   cwd=str(state), env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True, close_fds=True)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        record = running(state)
        if record:
            return record
        if process.poll() is not None:
            raise RuntimeError('Startup failed. See ' + str(state / 'server.log') + ' and ' + str(state / 'codex.log'))
        time.sleep(0.1)
    raise RuntimeError('Startup is still pending. Check codexremote status and ' + str(state / 'server.log'))


def stop(state):
    record = running(state)
    if not record:
        if daemon_locked(state):
            raise RuntimeError('The managed process is starting or unresponsive. Check ' + str(state / 'server.log') + '; no unrelated process was stopped.')
        return False
    if not control(record, 'stop'):
        raise RuntimeError('Could not authenticate the managed process; no process was stopped.')
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not daemon_locked(state):
            return True
        time.sleep(0.1)
    raise RuntimeError('Shutdown is still pending. Check ' + str(state / 'server.log'))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.name != 'posix':
        print('codexremote currently supports Linux and macOS. On Windows, run it inside WSL.', file=sys.stderr)
        return 1
    if argv and argv[0] == '_serve':
        worker = argparse.ArgumentParser()
        worker.add_argument('--state-dir', required=True)
        state = Path(worker.parse_args(argv[1:]).state_dir)
        try:
            serve(read_json(state / 'config.json'), state)
            return 0
        except Exception as exc:
            print('Codex Remote startup failed: ' + str(exc), file=sys.stderr, flush=True)
            return 1
    command_parser = parser()
    args = command_parser.parse_args(argv)
    if args.command == 'help':
        command_parser.print_help()
        return 0
    state = Path(args.state_dir).expanduser().resolve() if args.state_dir else default_state_dir().resolve()
    try:
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(str(state), 0o700)
        with file_lock(state / 'cli.lock') as cli_lock:
            if args.command == 'status':
                record = running(state)
                if record:
                    print_status(record, state)
                    return 0
                print('Codex Remote is not running. Start it with: codexremote')
                print('Runtime data: ' + str(state))
                return 1
            if args.command == 'stop':
                print('Codex Remote stopped.' if stop(state) else 'Codex Remote is already stopped.')
                return 0
            record = running(state)
            if args.command == 'start' and record:
                print_status(record, state, 'Codex Remote is already running (use restart to change settings)')
                return 0
            if args.command == 'start' and daemon_locked(state):
                raise RuntimeError('A managed process is already starting. Check codexremote status shortly.')
            saved = read_json(state / 'config.json') if args.command == 'restart' else None
            config = resolve_config(args, saved)
            if args.command == 'restart':
                stop(state)
            if args.foreground:
                import fcntl
                ensure_token(state)
                write_json(state / 'config.json', config)
                serve(config, state, foreground=True, on_ready=lambda: fcntl.flock(cli_lock, fcntl.LOCK_UN))
            else:
                print_status(start(config, state), state)
        return 0
    except (OSError, ValueError, RuntimeError, RpcError) as exc:
        print('codexremote: ' + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nInterrupted. Run codexremote status to check the managed server.', file=sys.stderr)
        return 130
