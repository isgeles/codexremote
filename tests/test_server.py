import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codexremote.server import Application, Bridge, Handler, ThreadingHTTPServer, RpcError


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.state = cls.root / '.remote'
        cls.state.mkdir()
        cls.token = 'test-token-with-more-than-32-characters'
        (cls.state / 'access-token').write_text(cls.token)
        (cls.root / 'hello.txt').write_text('hello remote')
        (cls.root / 'unsafe.html').write_text('<script>alert(1)</script>')
        (cls.root / 'escape').symlink_to('/etc/passwd')
        (cls.root / '.ssh').mkdir()
        (cls.root / '.ssh' / 'key').write_text('secret')
        cls.bridge = Bridge([sys.executable, str(Path(__file__).with_name('fake_codex.py'))],cls.root,cls.state / 'log')
        cls.http = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.http.app = Application(cls.bridge,[cls.root],cls.state,cls.token)
        cls.port = cls.http.server_address[1]
        threading.Thread(target=cls.http.serve_forever,daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.bridge.close(); cls.temp.cleanup()

    def request(self,path,body=None,headers=None,cookie=None):
        h = dict(headers or {})
        if cookie: h['Cookie'] = cookie
        if body is not None and not isinstance(body,bytes):
            body = json.dumps(body).encode(); h['Content-Type'] = 'application/json'
        connection = http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        connection.request('GET' if body is None else 'POST',path,body,h)
        response = connection.getresponse(); payload = response.read(); status = response.status; response_headers = dict(response.getheaders()); connection.close()
        if 'json' in response_headers.get('Content-Type',''): payload = json.loads(payload)
        return status,payload,response_headers

    def login(self):
        status,_,headers = self.request('/api/login',{'token':self.token})
        self.assertEqual(status,200)
        self.assertIn('HttpOnly',headers['Set-Cookie']); self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        return headers['Set-Cookie'].split(';')[0]

    def test_auth_required_and_logout_revokes(self):
        for path in ['/api/status','/api/events','/api/files','/api/file?path=hello.txt']:
            self.assertEqual(self.request(path)[0],401)
        self.assertEqual(self.request('/api/login',{'token':'wrong'})[0],401)
        cookie = self.login(); self.assertEqual(self.request('/api/status',cookie=cookie)[0],200)
        self.assertEqual(self.request('/api/logout',{},cookie=cookie)[0],200)
        self.assertEqual(self.request('/api/status',cookie=cookie)[0],401)

    def test_csrf_and_dns_rebinding(self):
        cookie = self.login()
        self.assertEqual(self.request('/api/rpc',{'method':'model/list'},cookie=cookie,headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/status',cookie=cookie,headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('/api/status',cookie=cookie,headers={'Sec-Fetch-Site':'cross-site'})[0],403)
        self.assertEqual(self.request('/api/status',cookie=cookie,headers={'Origin':'http://127.0.0.1:'+str(self.port)})[0],200)

    def test_file_boundaries_and_inert_preview(self):
        cookie = self.login()
        for path in ['/etc/passwd','../outside','escape','.remote/access-token','.ssh/key']:
            self.assertIn(self.request('/api/file?path='+quote(path),cookie=cookie)[0],[403,404])
        status,data,_ = self.request('/api/file?path=hello.txt',cookie=cookie)
        self.assertEqual(status,200); self.assertEqual(data['text'],'hello remote')
        status,data,headers = self.request('/api/file?path=unsafe.html&download=1',cookie=cookie)
        self.assertEqual(status,200); self.assertEqual(headers['Content-Type'],'application/octet-stream'); self.assertTrue(headers['Content-Disposition'].startswith('attachment'))

    def test_upload_does_not_overwrite_and_download_roundtrip(self):
        cookie = self.login()
        paths = []
        for _ in range(2):
            status,data,_ = self.request('/api/upload',b'uploaded bytes',cookie=cookie,headers={'X-File-Name':'../../hello.txt'})
            self.assertEqual(status,200); paths.append(data['path'])
            self.assertTrue(data['path'].startswith(str(self.state / 'uploads')))
            status,body,_ = self.request('/api/file?download=1&path='+quote(data['path']),cookie=cookie)
            self.assertEqual(body,b'uploaded bytes')
        self.assertNotEqual(paths[0],paths[1]); self.assertEqual((self.root / 'hello.txt').read_text(),'hello remote')

    def test_parallel_rpc_and_error(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _:self.bridge.call('test/delayed',{}),range(8)))
        self.assertTrue(all(r['delayed'] for r in responses))
        with self.assertRaises(RpcError): self.bridge.call('test/error',{})

    def test_pending_approval_survives_disconnect_and_one_response(self):
        self.bridge.call('test/request',{})
        first = self.bridge.poll(0,wait=0)
        self.assertTrue(any(r['id']=='test-approval' for r in first['pending']))
        second = self.bridge.poll(first['cursor'],wait=0)
        self.assertTrue(any(r['id']=='test-approval' for r in second['pending']))
        self.bridge.respond('test-approval',{'decision':'decline'})
        with self.assertRaises(ValueError): self.bridge.respond('test-approval',{'decision':'accept'})

    def test_replay_gap_and_response_cursor(self):
        for i in range(4002): self.bridge.publish({'method':'test/event','params':{'i':i}})
        self.assertTrue(self.bridge.poll(0,wait=0)['reset'])
        envelope = self.bridge.call('model/list',{},envelope=True)
        self.assertIn('cursor',envelope); self.assertIn('result',envelope)
        self.assertFalse(self.bridge.poll(envelope['cursor'],wait=0)['reset'])

    def test_static_security_headers(self):
        status,_,headers = self.request('/')
        self.assertEqual(status,200); self.assertEqual(headers['X-Frame-Options'],'DENY')
        self.assertIn("script-src 'self'",headers['Content-Security-Policy'])

    def test_bundled_assets_are_bounded(self):
        for path in ['/markdown.js', '/vendor/katex/katex.min.js', '/vendor/katex/fonts/KaTeX_Main-Regular.woff2']:
            self.assertEqual(self.request(path)[0], 200)
        for path in ['/vendor/../../server.py', '/vendor/../../../.remote/access-token', '/vendor/missing.js']:
            self.assertEqual(self.request(path)[0], 404)


if __name__ == '__main__': unittest.main()
