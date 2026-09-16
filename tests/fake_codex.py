"""Deterministic app-server fixture, never used by the production launcher."""
import json
import os
import copy
import sys
import threading
import time

threads = {}
requests = {}
lock = threading.Lock()
active = {}
counter = 0


def send(message):
    with lock:
        print(json.dumps(message), flush=True)


def event(method, **params):
    send({'method': method, 'params': params})


def finish(tid, turn):
    if turn['status'] != 'inProgress':
        return
    item = {'id': 'answer-' + turn['id'], 'type': 'agentMessage', 'text': '', 'phase': 'final'}
    turn['items'].append(item)
    event('item/started', threadId=tid, turnId=turn['id'], item=item.copy())
    for chunk in ['The workspace is ready.\n\n', '**Verified** from your phone.\n\n', '```python\nprint("hello")\n```']:
        item['text'] += chunk
        event('item/agentMessage/delta', threadId=tid, turnId=turn['id'], itemId=item['id'], delta=chunk)
        time.sleep(.08)
    event('item/completed', threadId=tid, turnId=turn['id'], item=item)
    turn['status'] = 'completed'
    event('turn/completed', threadId=tid, turn=turn)


def main():
    global counter
    for line in sys.stdin:
        data = json.loads(line)
        ident, method, p = data.get('id'), data.get('method'), data.get('params', {})
        if not method:
            pending = requests.pop(str(ident), None)
            if pending:
                tid, turn = pending
                event('serverRequest/resolved', threadId=tid, requestId=ident)
                threading.Thread(target=finish, args=(tid, turn), daemon=True).start()
            continue
        if ident is None:
            continue
        result = {}
        if method == 'initialize': result = {'userAgent': 'fake-codex/1', 'platformOs':'linux'}
        elif method == 'account/read': result = {'account':{'type':'chatgpt','email':'demo@example.test','planType':'pro'}}
        elif method == 'model/list': result = {'data':[{'model':'test-model','id':'test-model','displayName':'Test model','isDefault':True,'hidden':False,'description':'Deterministic test model','defaultReasoningEffort':'medium','supportedReasoningEfforts':[{'reasoningEffort':'medium'},{'reasoningEffort':'high'}]}]}
        elif method == 'thread/list': result = {'data': [t for t in threads.values() if t.get('archived',False) == p.get('archived',False)], 'nextCursor':None}
        elif method == 'thread/start':
            counter += 1
            t = {'id':'thread-' + str(counter),'name':None,'preview':'','cwd':p.get('cwd','/tmp'),'model':'test-model','createdAt':int(time.time()),'updatedAt':int(time.time()),'turns':[], 'historyMode':'legacy','status':{'type':'idle'}}
            threads[t['id']] = t
            result = {'thread':t,'model':'test-model','reasoningEffort':'medium','cwd':t['cwd']}
        elif method in {'thread/resume','thread/read'}:
            t = threads[p['threadId']]
            result = {'thread':t,'model':'test-model','reasoningEffort':'medium','cwd':t['cwd']}
        elif method == 'thread/fork':
            counter += 1
            t = copy.deepcopy(threads[p['threadId']])
            t['id'] = 'thread-' + str(counter)
            threads[t['id']] = t
            result = {'thread':t}
        elif method == 'thread/name/set': threads[p['threadId']]['name'] = p['name']
        elif method == 'thread/archive': threads[p['threadId']]['archived'] = True
        elif method == 'thread/unarchive': threads[p['threadId']]['archived'] = False
        elif method == 'skills/list': result = {'data':[{'cwd':p['cwds'][0],'skills':[{'name':'test-skill','path':'/tmp/SKILL.md','description':'An example skill.'}]}]}
        elif method == 'mcpServerStatus/list': result = {'data':[{'name':'test-mcp','authStatus':'notLoggedIn','tools':{}}]}
        elif method == 'turn/start':
            t = threads[p['threadId']]
            counter += 1
            turn = {'id':'turn-' + str(counter),'status':'inProgress','items':[{'id':'user-' + str(counter),'type':'userMessage','content':p['input']}]}
            t['turns'].append(turn)
            text = '\n'.join(x.get('text','') for x in p['input'])
            t['preview'] = t['preview'] or text[:80]
            result = {'turn':turn}
            send({'id':ident,'result':result})
            event('turn/started',threadId=t['id'],turn=turn)
            if 'approval' in text:
                reqid = 'approval-' + str(counter)
                requests[reqid] = (t['id'],turn)
                send({'id':reqid,'method':'item/commandExecution/requestApproval','params':{'threadId':t['id'],'turnId':turn['id'],'itemId':'cmd-1','command':'echo approved','reason':('Long request details. ' * 300 if 'long approval' in text else 'Test approval'),'availableDecisions':['accept','acceptForSession','decline','cancel'],'startedAtMs':int(time.time()*1000)}})
            elif 'question' in text:
                reqid = 'question-' + str(counter)
                requests[reqid] = (t['id'],turn)
                send({'id':reqid,'method':'item/tool/requestUserInput','params':{'threadId':t['id'],'turnId':turn['id'],'itemId':'q-1','isBlocking':True,'questions':[{'id':'color','header':'Color','question':'Which color?','options':[{'label':'Green','description':'Use green'}]}]}})
            elif 'slow' in text:
                threading.Timer(4, finish, (t['id'],turn)).start()
            else: threading.Thread(target=finish,args=(t['id'],turn),daemon=True).start()
            continue
        elif method == 'turn/interrupt':
            t = threads[p['threadId']]
            turn = next(x for x in t['turns'] if x['id'] == p['turnId'])
            turn['status'] = 'interrupted'
            event('turn/completed',threadId=t['id'],turn=turn)
        elif method == 'turn/steer': result = {'turnId':p['expectedTurnId']}
        elif method == 'test/request':
            requests['test-approval'] = ('nonexistent', {'id':'test','status':'interrupted'})
            send({'id':'test-approval','method':'item/commandExecution/requestApproval','params':{'threadId':'nonexistent','turnId':'test','itemId':'item','command':'echo test'}})
        elif method == 'test/delayed':
            threading.Timer(.1, lambda i=ident: send({'id':i,'result':{'delayed':True}})).start()
            continue
        elif method == 'test/error':
            send({'id':ident,'error':{'code':123,'message':'Expected fixture error'}})
            continue
        send({'id':ident,'result':result})


if __name__ == '__main__':
    if os.environ.get('CODEXREMOTE_TEST_COMMAND_LOG'):
        with open(os.environ['CODEXREMOTE_TEST_COMMAND_LOG'], 'a') as log:
            log.write(json.dumps(sys.argv[1:]) + '\n')
    if sys.argv[1:] == ['app-server', 'daemon', 'start']:
        print(json.dumps({'socketPath': os.environ['CODEXREMOTE_TEST_SOCKET']}))
        sys.exit(0)
    if '--help' in sys.argv:
        sys.exit(0)
    main()
