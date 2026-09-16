#!/usr/bin/env python3
"""Single-user Codex web client with owned-process and shared-server transports."""
import collections
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import queue
import secrets
import socket
import ssl
import subprocess
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
from . import __version__

BASE = Path(__file__).resolve().parent
MAX_UPLOAD = 25 * 1024 * 1024
RASTER = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}


class RpcError(Exception):
    def __init__(self, error):
        self.error = error
        super().__init__(error.get('message', str(error)))


class Bridge:
    """One connection; closing a shared transport never stops its server."""
    def __init__(self, command, cwd, log_path, endpoint=None, token_file=None):
        self.condition = threading.Condition(threading.RLock())
        self.write_lock = threading.Lock()
        self.waiters = {}
        self.pending = {}
        self.events = collections.deque(maxlen=4000)
        self.sequence = 0
        self.serial = 0
        self.alive = True
        self.proc = self.connection = self.connection_context = self.log = None
        try:
            if endpoint:
                try:
                    from websockets.sync.client import connect, unix_connect
                except ImportError:
                    raise RuntimeError('Install Codex Remote with its websockets dependency to connect to Codex.') from None
                headers = {}
                if token_file:
                    token = Path(token_file).read_text().strip()
                    if not token or '\n' in token or '\r' in token:
                        raise ValueError('The connection token file must contain one nonempty token.')
                    headers['Authorization'] = 'Bearer ' + token
                try:
                    options = dict(additional_headers=headers, max_size=64 * 1024 * 1024,
                                   open_timeout=15, close_timeout=2, compression=None)
                    if endpoint.startswith('unix://'):
                        context = unix_connect(endpoint[len('unix://'):], **options)
                    else:
                        context = connect(endpoint, proxy=None, **options)
                    self.connection = context.__enter__()
                    self.connection_context = context
                except Exception:
                    raise RuntimeError('Could not connect to the shared Codex server. Check its endpoint, TLS, and token file. The shared server was not stopped.') from None
            else:
                self.log = open(str(log_path), 'ab', buffering=0)
                self.proc = subprocess.Popen(command, cwd=str(cwd), stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=self.log)
            threading.Thread(target=self._read, daemon=True).start()
            self.info = self.call('initialize', {
                'clientInfo': {'name': 'codex_remote_web', 'title': 'Codex Remote', 'version': __version__},
                'capabilities': {'experimentalApi': True}}, timeout=30)
            self.send({'method': 'initialized', 'params': {}})
        except Exception:
            self.close()
            raise

    def send(self, message):
        with self.write_lock:
            if not self.alive:
                raise RuntimeError('The Codex connection closed. Check its server, then restart the web client.')
            try:
                if self.connection:
                    self.connection.send(json.dumps(message))
                else:
                    self.proc.stdin.write((json.dumps(message) + '\n').encode())
                    self.proc.stdin.flush()
            except Exception:
                raise RuntimeError('The Codex connection closed; the request outcome is unknown. Reconnect before retrying.') from None

    def call(self, method, params, timeout=120, envelope=False):
        waiter = queue.Queue(maxsize=1)
        with self.condition:
            self.serial += 1
            ident = 'web-' + str(self.serial)
            self.waiters[ident] = waiter
        try:
            self.send({'id': ident, 'method': method, 'params': params})
            try:
                response = waiter.get(timeout=timeout)
            except queue.Empty:
                raise RuntimeError('Codex request timed out. Its outcome is unknown; refresh before retrying.')
            if 'error' in response:
                raise RpcError(response['error'])
            result = response.get('result', {})
            return {'result': result, 'cursor': response.get('_cursor', 0)} if envelope else result
        finally:
            with self.condition:
                self.waiters.pop(ident, None)

    def publish(self, message):
        with self.condition:
            self.sequence += 1
            self.events.append({'seq': self.sequence, 'message': message})
            self.condition.notify_all()

    def _read(self):
        try:
            for line in self.connection if self.connection else self.proc.stdout:
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if 'method' not in message:
                    with self.condition:
                        waiter = self.waiters.get(message.get('id'))
                        if waiter:
                            message['_cursor'] = self.sequence
                            waiter.put_nowait(message)
                    continue
                if 'id' in message:
                    if message['method'] == 'currentTime/read':
                        self.send({'id': message['id'], 'result': {'currentTimeAt': int(time.time())}})
                        continue
                    with self.condition:
                        self.pending[str(message['id'])] = message
                if message['method'] == 'serverRequest/resolved':
                    with self.condition:
                        self.pending.pop(str(message.get('params', {}).get('requestId')), None)
                if message['method'] == 'turn/completed':
                    p = message.get('params', {})
                    with self.condition:
                        for key, request in list(self.pending.items()):
                            rp = request.get('params', {})
                            if rp.get('threadId') == p.get('threadId') and rp.get('turnId') == p.get('turn', {}).get('id'):
                                self.pending.pop(key, None)
                # Legacy raw notifications duplicate v2 events and can be enormous.
                if not message['method'].startswith('codex/event/'):
                    self.publish(message)
        except Exception:
            # Transport failure is reported through pending RPCs and an event.
            # Never dump WebSocket headers or authentication material to logs.
            pass
        finally:
            with self.condition:
                self.alive = False
                self.pending.clear()
                for waiter in self.waiters.values():
                    if waiter.empty():
                        waiter.put_nowait({'error': {'message': 'The Codex connection closed. Check its server, then restart the web client.'}})
            self.publish({'method': 'remote/disconnected', 'params': {}})

    def respond(self, ident, result=None, error=None):
        with self.condition:
            request = self.pending.get(str(ident))
            if not request:
                raise ValueError('This request has already been resolved.')
            message = {'id': request['id']}
            message['error' if error else 'result'] = error if error else result
            self.send(message)
            self.pending.pop(str(ident), None)
            self.publish({'method': 'serverRequest/resolved', 'params': {'requestId': request['id']}})

    def poll(self, after, wait=20):
        with self.condition:
            if after == self.sequence and self.alive:
                self.condition.wait(timeout=wait)
            reset = after > self.sequence or (bool(self.events) and after < self.events[0]['seq'] - 1)
            return {'cursor': self.sequence, 'reset': reset, 'alive': self.alive,
                    'events': [e for e in self.events if e['seq'] > after] if not reset else [],
                    'pending': list(self.pending.values())}

    def close(self):
        if self.connection:
            self.connection.close()
        if self.connection_context:
            self.connection_context.__exit__(None, None, None)
            self.connection_context = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.proc:
            self.proc.stdin.close()
            self.proc.stdout.close()
        if self.log:
            self.log.close()


def inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class Application:
    def __init__(self, bridge, roots, state, token, public_url=None, secure=False):
        self.bridge, self.roots, self.state = bridge, roots, state
        self.token_hash = hashlib.sha256(token.encode()).digest()
        self.public_url = public_url.rstrip('/') if public_url else None
        self.secure = secure or bool(public_url and public_url.startswith('https://'))
        self.sessions = {}
        self.attempts = {}
        self.lock = threading.Lock()
        self.uploads = state / 'uploads'
        self.uploads.mkdir(mode=0o700, exist_ok=True)

    def path(self, raw):
        if not isinstance(raw, str) or '\x00' in raw:
            raise ValueError('Invalid path')
        p = Path(raw).expanduser()
        p = (p if p.is_absolute() else self.roots[0] / p).resolve(strict=True)
        if inside(p, self.state) and not inside(p, self.uploads):
            raise PermissionError('Private app data is not shared.')
        if not any(inside(p, root) for root in self.roots + [self.uploads]):
            raise PermissionError('Outside shared folders. Add this folder with --root when starting the server.')
        if not inside(p, self.uploads) and any(part in {'.ssh', '.gnupg', '.codex', '.aws', '.azure', '.git', '.remote'} for part in p.parts):
            raise PermissionError('Private configuration directories are not shared.')
        return p


class Handler(BaseHTTPRequestHandler):
    server_version = 'CodexRemote'
    protocol_version = 'HTTP/1.1'

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        # Avoid logging query strings, file names, tokens or conversation content.
        pass

    def reply(self, status, data, mime='application/json', extra=None):
        if mime == 'application/json':
            data = json.dumps(data).encode()
        elif isinstance(data, str):
            data = data.encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; media-src 'self' blob:; frame-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def origin_check(self):
        host = self.headers.get('Host', '')
        parsed = urlsplit('http://' + host)
        if self.app.public_url:
            if host.lower() != urlsplit(self.app.public_url).netloc.lower():
                raise PermissionError('Unrecognized host. Use the configured --public-url.')
            origin = self.app.public_url
        else:
            hostname = parsed.hostname or ''
            try:
                ipaddress.ip_address(hostname)
                valid = True
            except ValueError:
                valid = hostname == 'localhost'
            if not valid:
                raise PermissionError('Use localhost, an IP address, or configure --public-url.')
            origin = ('https://' if self.app.secure else 'http://') + host
        if self.headers.get('Origin') and self.headers['Origin'] != origin:
            raise PermissionError('Cross-origin requests are not allowed.')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise PermissionError('Cross-site requests are not allowed.')

    def authorized(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            sid = cookie['remote_session'].value if 'remote_session' in cookie else ''
        except Exception:
            return False
        with self.app.lock:
            return self.app.sessions.get(sid, 0) > time.time()

    def body(self, limit=2 * 1024 * 1024):
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('Chunked uploads are not supported.')
        length = int(self.headers.get('Content-Length', '0'))
        if length < 0 or length > limit:
            raise ValueError('Request too large (file limit: 25 MB).')
        self.connection.settimeout(60)
        value = self.rfile.read(length)
        if len(value) != length:
            raise ValueError('Incomplete request')
        return value

    def do_GET(self):
        self.handle_route('GET')

    def do_POST(self):
        self.handle_route('POST')

    def handle_route(self, method):
        try:
            self.origin_check()
            url = urlsplit(self.path)
            path, query = url.path, parse_qs(url.query)
            if method == 'GET' and path.startswith('/vendor/'):
                vendor = (BASE / 'web' / 'vendor').resolve()
                file = (BASE / 'web' / path[1:]).resolve()
                if not file.is_relative_to(vendor) or not file.is_file():
                    return self.reply(404, {'error': 'Asset not found.'})
                mime = {'.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf'}.get(file.suffix, 'text/plain')
                return self.reply(200, file.read_bytes(), mime)
            if method == 'GET' and path in {'/', '/app.js', '/markdown.js', '/style.css', '/icon.svg', '/manifest.webmanifest'}:
                file = BASE / 'web' / ('index.html' if path == '/' else path[1:])
                mime = {'/': 'text/html; charset=utf-8', '/app.js': 'text/javascript; charset=utf-8', '/markdown.js': 'text/javascript; charset=utf-8', '/style.css': 'text/css; charset=utf-8', '/icon.svg': 'image/svg+xml', '/manifest.webmanifest': 'application/manifest+json'}[path]
                return self.reply(200, file.read_bytes(), mime)
            if method == 'POST' and path == '/api/login':
                return self.login()
            if not self.authorized():
                self.close_connection = True
                return self.reply(401, {'error': 'Sign in to your machine.'})
            if method == 'GET' and path == '/api/status':
                return self.reply(200, {'alive': self.app.bridge.alive, 'roots': [str(p) for p in self.app.roots],
                    'cursor': self.app.bridge.sequence, 'pending': list(self.app.bridge.pending.values()),
                    'host': socket.gethostname(), 'info': self.app.bridge.info})
            if method == 'GET' and path == '/api/events':
                return self.reply(200, self.app.bridge.poll(int(query.get('after', ['0'])[0])))
            if method == 'POST' and path == '/api/logout':
                self.body()
                c = SimpleCookie(self.headers.get('Cookie', ''))
                with self.app.lock:
                    self.app.sessions.pop(c['remote_session'].value, None)
                return self.reply(200, {}, extra={'Set-Cookie': self.cookie('', 0)})
            if method == 'POST' and path == '/api/rpc':
                data = json.loads(self.body())
                name, params = data.get('method'), data.get('params', {})
                if not isinstance(name, str) or name in {'initialize', 'initialized'} or not isinstance(params, dict):
                    raise ValueError('Invalid RPC request')
                # The authenticated owner has their Codex account's full protocol access.
                return self.reply(200, self.app.bridge.call(name, params, envelope=True))
            if method == 'POST' and path == '/api/respond':
                data = json.loads(self.body())
                self.app.bridge.respond(data['id'], data.get('result'), data.get('error'))
                return self.reply(200, {})
            if method == 'GET' and path == '/api/files':
                folder = self.app.path(query.get('path', [str(self.app.roots[0])])[0])
                if not folder.is_dir():
                    raise ValueError('Choose a folder')
                entries = []
                for child in folder.iterdir():
                    try:
                        safe = self.app.path(str(child))
                        stat = safe.stat()
                        entries.append({'name': child.name, 'path': str(safe), 'directory': safe.is_dir(), 'size': stat.st_size})
                    except (OSError, ValueError):
                        continue
                entries.sort(key=lambda x: (not x['directory'], x['name'].lower()))
                return self.reply(200, {'path': str(folder), 'parent': str(folder.parent), 'entries': entries[:2000], 'truncated': len(entries) > 2000})
            if method == 'GET' and path == '/api/file':
                return self.file(query)
            if method == 'POST' and path == '/api/upload':
                name = Path(unquote(self.headers.get('X-File-Name', 'attachment')).replace('\\', '/')).name
                name = ''.join(c for c in name if c.isalnum() or c in ' ._-')[:150] or 'attachment'
                content = self.body(MAX_UPLOAD)
                folder = self.app.uploads / secrets.token_hex(12)
                folder.mkdir(mode=0o700)
                target = folder / name
                with os.fdopen(os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
                    f.write(content)
                return self.reply(200, {'path': str(target), 'name': name, 'size': len(content), 'image': target.suffix.lower() in RASTER})
            self.close_connection = True
            self.reply(404, {'error': 'Not found'})
        except RpcError as exc:
            self.reply(400, {'error': str(exc), 'rpcError': exc.error})
        except PermissionError as exc:
            self.close_connection = True
            self.reply(403, {'error': str(exc)})
        except FileNotFoundError:
            self.reply(404, {'error': 'File not found'})
        except (ValueError, KeyError, TypeError) as exc:
            self.close_connection = True
            self.reply(400, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception as exc:
            self.close_connection = True
            self.reply(503, {'error': str(exc)})

    def cookie(self, value, age):
        return 'remote_session={}; HttpOnly; SameSite=Strict; Path=/; Max-Age={}{}'.format(value, age, '; Secure' if self.app.secure else '')

    def login(self):
        data = json.loads(self.body(8192))
        token = data.get('token', '')
        if not isinstance(token, str):
            raise ValueError('Invalid token')
        now, ip = time.time(), self.client_address[0]
        with self.app.lock:
            attempts = [t for t in self.app.attempts.get(ip, []) if now - t < 60]
            self.app.attempts[ip] = attempts
            if len(attempts) >= 10:
                return self.reply(429, {'error': 'Too many attempts. Try again in a minute.'})
            if not hmac.compare_digest(hashlib.sha256(token.encode()).digest(), self.app.token_hash):
                attempts.append(now)
                return self.reply(401, {'error': 'Incorrect access token.'})
            self.app.sessions = {s: t for s, t in self.app.sessions.items() if t > now}
            sid = secrets.token_urlsafe(32)
            self.app.sessions[sid] = now + 30 * 86400
        self.reply(200, {}, extra={'Set-Cookie': self.cookie(sid, 30 * 86400)})

    def file(self, query):
        target = self.app.path(query.get('path', [''])[0])
        if not target.is_file():
            raise ValueError('Not a regular file')
        # Open the resolved path without following a last-component symlink.
        descriptor = os.open(str(target), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            download = query.get('download') == ['1']
            image = target.suffix.lower() in RASTER
            if download or image:
                if size > 250 * 1024 * 1024:
                    raise ValueError('Browser downloads are limited to 250 MB. Use SSH for larger files.')
                data = stream.read()
            else:
                data = stream.read(1024 * 1024)
                try:
                    value = data.decode('utf-8')
                    if '\x00' in value:
                        raise UnicodeError()
                except UnicodeError:
                    return self.reply(200, {'binary': True, 'size': size, 'name': target.name})
                return self.reply(200, {'text': value, 'truncated': size > len(data), 'size': size, 'name': target.name})
        mime = RASTER.get(target.suffix.lower(), 'application/octet-stream')
        disposition = 'attachment' if download else 'inline'
        # Uploaded HTML/SVG are always downloads; never execute untrusted workspace content.
        filename = ''.join(c for c in target.name if 32 <= ord(c) < 127 and c not in {'"', '\\'}) or 'download'
        self.reply(200, data, mime, {'Content-Disposition': disposition + '; filename="' + filename + '"'})


def ensure_token(state):
    """Create private runtime data independently of the installed package."""
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(str(state), 0o700)
    token_path = state / 'access-token'
    try:
        descriptor = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(secrets.token_urlsafe(32) + '\n')
    os.chmod(str(token_path), 0o600)
    token = token_path.read_text().strip()
    if len(token) < 32:
        raise ValueError('Access token must contain at least 32 characters: ' + str(token_path))
    return token


def create_server(config, state):
    """Bind and initialize Codex; callers own serving and shutdown."""
    token = ensure_token(state)
    roots = [Path(p).resolve(strict=True) for p in config['roots']]
    if not all(p.is_dir() for p in roots):
        raise ValueError('Each shared root must be a directory')
    server_class = ThreadingHTTPServer
    if ':' in config['host']:
        class IPv6Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6
        server_class = IPv6Server
    server = server_class((config['host'], config['port']), Handler)
    bridge = None
    try:
        endpoint = config.get('connect')
        if not endpoint:
            # start is idempotent: never restart or take ownership of the daemon.
            # The terminal uses this same per-user daemon. The control socket
            # speaks WebSocket (the CLI proxy forwards raw bytes, not JSONL).
            with open(state / 'codex.log', 'ab', buffering=0) as log:
                try:
                    result = subprocess.run([config['codex'], 'app-server', 'daemon', 'start'],
                                            cwd=str(roots[0]), stdin=subprocess.DEVNULL,
                                            stdout=subprocess.PIPE, stderr=log, timeout=30)
                except subprocess.TimeoutExpired:
                    raise RuntimeError('Starting the local Codex daemon timed out. Check codex.log; no daemon was stopped.') from None
                if result.returncode:
                    raise RuntimeError('Could not start or attach to the local Codex daemon. Check the managed Codex installation and codex.log; no existing session was stopped.')
                try:
                    socket_path = json.loads(result.stdout)['socketPath']
                    if not isinstance(socket_path, str) or not Path(socket_path).is_absolute():
                        raise ValueError('Expected an absolute socket path')
                except (ValueError, KeyError, TypeError):
                    raise RuntimeError('Codex daemon start did not return a valid socketPath. Update Codex; no existing session was stopped.') from None
                endpoint = 'unix://' + socket_path
        bridge = Bridge(None, roots[0], state / 'codex.log',
                        endpoint=endpoint, token_file=config.get('connect_token_file'))
        server.daemon_threads = True
        server.app = Application(bridge, roots, state, token, config.get('public_url'), bool(config.get('tls_cert')))
        if config.get('tls_cert'):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(config['tls_cert'], config['tls_key'])
            server.socket = context.wrap_socket(server.socket, server_side=True)
        return server
    except BaseException:
        server.server_close()
        if bridge:
            bridge.close()
        raise
