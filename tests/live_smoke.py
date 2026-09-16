"""Opt-in real Codex integration test: one short read-only turn, then archive.

Run only with a live server: python3 tests/live_smoke.py
Uses the local access token without printing it. Consumes one model request.
"""
import http.cookiejar
import json
from pathlib import Path
import os
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codexremote.cli import default_state_dir

BASE = os.environ.get('CODEXREMOTE_TEST_URL', 'http://127.0.0.1:8787')
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def api(path, data=None):
    request = urllib.request.Request(BASE + path, data=None if data is None else json.dumps(data).encode(),
                                     headers={'Content-Type':'application/json'})
    with opener.open(request, timeout=130) as response: return json.load(response)


def rpc(method, params): return api('/api/rpc',{'method':method,'params':params})['result']


def main():
    token = (default_state_dir() / 'access-token').read_text().strip()
    api('/api/login',{'token':token})
    status = api('/api/status')
    assert status['alive']
    models = rpc('model/list',{})['data']
    account = rpc('account/read',{})
    print('Codex connected; account signed in:',bool(account.get('account')),'models:',len(models))
    result = rpc('thread/start', {'cwd':status['roots'][0], 'sandbox':'read-only', 'approvalPolicy':'on-request', 'approvalsReviewer':'user'})
    ident = result['thread']['id']
    try:
        rpc('thread/name/set',{'threadId':ident,'name':'Codex Remote integration smoke test'})
        turn = rpc('turn/start',{'threadId':ident,'input':[{'type':'text','text':'Reply with exactly REMOTE_SMOKE_OK. Do not use tools or change files.','text_elements':[]}]})['turn']
        deadline = time.time()+180
        cursor = status['cursor']
        finished = False
        methods = set()
        while time.time()<deadline:
            events = api('/api/events?after='+str(cursor)); cursor=events['cursor']
            for e in events['events']:
                m,p = e['message']['method'],e['message'].get('params',{})
                if p.get('threadId') != ident: continue
                methods.add(m)
                if m=='turn/completed':
                    assert p['turn']['status']=='completed',p['turn'].get('error')
                    finished=True
            if finished: break
        assert finished,'Turn timed out'
        thread = rpc('thread/read',{'threadId':ident,'includeTurns':True})['thread']
        if thread.get('historyMode')=='paginated':
            thread['turns'] = rpc('thread/turns/list',{'threadId':ident,'itemsView':'full','limit':10})['data']
        text = '\n'.join(i.get('text','') for t in thread['turns'] for i in t['items'] if i['type']=='agentMessage')
        assert 'REMOTE_SMOKE_OK' in text, 'Expected response missing'
        print('PASS: real Codex turn completed, response persisted, history readable. Event types:',sorted(methods))
    finally:
        rpc('thread/archive',{'threadId':ident})
        print('Archived the smoke-test conversation.')


if __name__=='__main__':main()
