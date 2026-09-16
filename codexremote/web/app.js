import {createMarkdown} from '/markdown.js';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const basename = p => (p || '').split('/').filter(Boolean).pop() || '/';
const fileURL = (path, download = false) => '/api/file?path=' + encodeURIComponent(path) + (download ? '&download=1' : '');
const imagePath = p => /\.(png|jpe?g|webp|gif)$/i.test(p || '');
const sizeLabel = n => n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
const state = {thread: null, threads: [], models: [], attachments: [], pending: [], cursor: 0, roots: [], cwd: '', activeTurn: null,
  model: '', effort: '', permission: '', mode: 'default', nextCursor: null, archived: false, epoch: 0, generation: 0,
  loading: false, buffered: [], sending: false, commandBusy: false, uploadCount: 0, events: [], turnCursor: null, snapshotCursor: 0};
let toastTimer, renderTimer, searchTimer;

function toast(message) { $('#toast').textContent = message; $('#toast').classList.remove('hidden'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), 6500); }
function banner(message) { $('#banner').textContent = message || ''; $('#banner').classList.toggle('hidden', !message); }
function connected(ok, text) { $('#connection-dot').classList.toggle('offline', !ok); $('#connection-text').textContent = text || (ok ? 'Connected · local workspace' : 'Reconnecting…'); }
async function api(path, data, extra = {}) {
  const response = await fetch(path, {method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin',
    ...(data === undefined ? {} : {body: JSON.stringify(data), headers: {'Content-Type': 'application/json'}}), ...extra});
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') showLogin();
    throw new Error(result.error || 'Request failed');
  }
  return result;
}
const rpcFull = (method, params = {}) => api('/api/rpc', {method, params});
const rpc = async (method, params = {}) => (await rpcFull(method, params)).result;
function showLogin() { state.epoch++; $('#login').classList.remove('hidden'); $('#app').classList.add('hidden'); }
function run(fn) { return (...args) => Promise.resolve().then(() => fn(...args)).catch(e => toast(e.message)); }

// Markdown rendering never accepts raw HTML. Only explicit safe elements/URLs are emitted.
function linkMarkup(label, target, image = false) {
  target = target.replace(/^<|>$/g, '');
  if (/^(https?:\/\/)/i.test(target)) return image ? `<a href="${esc(target)}" target="_blank" rel="noopener noreferrer">${esc(label || 'View remote image')} ↗</a>` : `<a href="${esc(target)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;
  if (/^data:image\/(png|jpeg|gif|webp);base64,[a-z0-9+/=]+$/i.test(target) && image) return `<img class="message-image" src="${esc(target)}" alt="${esc(label)}">`;
  if (/^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith('//')) return esc(label);
  let local = target.replace(/:\d+(?::\d+)?$/, '');
  if (!local.startsWith('/')) local = state.cwd + '/' + local;
  return image && imagePath(local) ? `<a href="#" data-file="${esc(local)}"><img class="message-image" src="${esc(fileURL(local))}" alt="${esc(label)}" loading="lazy"></a>` : `<a href="#" data-file="${esc(local)}">${esc(label)}</a>`;
}
const markdown = createMarkdown({linkMarkup, escape: esc});

function threadTitle(t) { return t.name || t.preview || 'Untitled conversation'; }
function renderThreads() {
  $('#threads').innerHTML = state.threads.length ? state.threads.map(t => `<button class="thread-row ${t.id === state.thread?.id ? 'active' : ''}" data-thread="${esc(t.id)}"><strong>${esc(threadTitle(t))}</strong><small><span>${esc(basename(t.cwd))}</span>${new Date((t.updatedAt || t.createdAt) * 1000).toLocaleDateString(undefined, {month:'short', day:'numeric'})}</small></button>`).join('') : '<div class="empty">No conversations here yet.</div>';
  $('#more-threads').classList.toggle('hidden', !state.nextCursor);
  $('#archive-filter').textContent = state.archived ? 'Active' : 'Archived';
}
let listGeneration = 0;
async function loadThreads(more = false) {
  const generation = ++listGeneration;
  const data = await rpc('thread/list', {limit:50, sourceKinds:['cli', 'vscode', 'appServer'], sortKey:'updated_at', archived:state.archived, cursor:more ? state.nextCursor : null, searchTerm:$('#search').value || null});
  if (generation !== listGeneration) return;
  state.threads = more ? [...state.threads, ...data.data.filter(t => !state.threads.some(old => old.id === t.id))] : data.data;
  state.nextCursor = data.nextCursor; renderThreads();
}
function closeSidebar() { $('#sidebar').classList.remove('open'); $('#scrim').classList.add('hidden'); }
function header() {
  $('#thread-title').textContent = state.thread ? threadTitle(state.thread) : 'New conversation';
  $('#thread-path').textContent = state.cwd || 'Choose a project and start something.';
  $('#project-name').textContent = basename(state.cwd);
  $('#model-button').textContent = (state.models.find(m => m.model === state.model)?.displayName || state.model || 'Codex defaults') + (state.effort ? ' · ' + state.effort : '') + ' ⌄';
  $('#stop').classList.toggle('hidden', !state.activeTurn);
  $('#send').title = state.activeTurn ? 'Send a follow-up to the active turn' : 'Send message';
  $('#turn-status').textContent = state.activeTurn ? 'Codex is working · send to steer' : 'Runs on your machine';
  $('#send').disabled = state.sending || state.commandBusy || state.loading || state.uploadCount > 0;
}
const welcomeHTML = $('#messages').innerHTML;
function newChat() {
  state.generation++; state.thread = null; state.activeTurn = null; state.attachments = []; state.turnCursor = null; state.loading = false; state.buffered = []; state.diff = null; state.snapshotCursor = 0;
  $('#messages').innerHTML = welcomeHTML; $('#prompt').value = ''; sessionStorage.removeItem('codex-remote-draft'); history.replaceState(null, '', '/');
  banner(''); header(); renderThreads(); renderAttachments(); renderRequests(); closeSidebar(); hideSlashMenu();
}
async function openThread(id, refresh = false) {
  const generation = ++state.generation; state.loading = true; state.buffered = []; header(); closeSidebar();
  banner('Loading conversation…');
  try {
    const envelope = await rpcFull('thread/resume', {threadId:id});
    if (generation !== state.generation) return;
    const r = envelope.result;
    let turnCursor = r.turnsBackwardsCursor || null;
    if (r.thread.historyMode === 'paginated') {
      const page = await rpcFull('thread/turns/list', {threadId:id, limit:30, sortDirection:'desc', itemsView:'full'});
      if (generation !== state.generation) return;
      r.thread.turns = page.result.data.reverse(); turnCursor = page.result.nextCursor;
      envelope.cursor = page.cursor;
    }
    state.thread = r.thread; state.cwd = r.cwd || r.thread.cwd; state.turnCursor = turnCursor;
    if (!refresh) { state.model = r.model || r.thread.model || ''; state.effort = r.reasoningEffort || ''; state.permission = ''; state.diff = null; state.threadArchived = state.archived && state.threads.some(t => t.id === id); }
    state.activeTurn = state.thread.turns.find(t => t.status === 'inProgress')?.id || null;
    state.snapshotCursor = envelope.cursor;
    const buffered = state.buffered.filter(e => e.seq > envelope.cursor); state.buffered = []; state.loading = false;
    buffered.forEach(e => applyEvent(e.message));
    if (!refresh) state.attachments = []; renderAttachments();
    history.replaceState(null, '', '/#thread=' + encodeURIComponent(id));
    banner(''); header(); renderThreads(); renderConversation(true); renderRequests();
  } catch (e) { if (generation === state.generation) { state.loading = false; banner(e.message); header(); } throw e; }
}
async function olderTurns() {
  const id = state.thread?.id, cursor = state.turnCursor; if (!id || !cursor) return;
  const r = await rpc('thread/turns/list', {threadId:id, cursor, limit:30, sortDirection:'desc', itemsView:'full'});
  if (state.thread?.id !== id) return;
  state.thread.turns = [...r.data.reverse().filter(t => !state.thread.turns.some(x => x.id === t.id)), ...state.thread.turns]; state.turnCursor = r.nextCursor; renderConversation();
}
function contentHTML(content) {
  return (content || []).map(c => c.type === 'text' ? markdown(c.text) : c.type === 'localImage' ? linkMarkup(basename(c.path), c.path, true) : c.type === 'image' ? linkMarkup('Attached image', c.url, true) : c.path ? `<a class="file-link" href="#" data-file="${esc(c.path)}">▱ ${esc(c.name || basename(c.path))}</a>` : '<p>' + esc(c.type) + ' attachment</p>').join('');
}
function itemHTML(item) {
  if (item.type === 'userMessage') return `<article class="message user"><div class="message-label">You</div><div class="message-body">${contentHTML(item.content)}</div></article>`;
  if (item.type === 'agentMessage') return `<article class="message"><div class="message-label"><span class="agent-dot"></span>Codex ${item.phase === 'commentary' ? '· update' : ''}</div><div class="message-body">${markdown(item.text)}</div></article>`;
  if (item.type === 'imageView' || item.type === 'imageGeneration') {
    const path = item.savedPath || item.path;
    const view = path ? linkMarkup('Generated image', path, true) : item.result && /^[a-z0-9+/=]+$/i.test(item.result) ? linkMarkup('Generated image', 'data:image/png;base64,' + item.result, true) : esc(item.failure || item.status || 'Generating image…');
    return `<article class="message">${view}</article>`;
  }
  const titles = {reasoning:'Thinking', plan:'Plan', commandExecution:'Terminal', fileChange:'File changes', mcpToolCall:'Tool', dynamicToolCall:'Tool', webSearch:'Web search', collabAgentToolCall:'Agent activity', contextCompaction:'Conversation compacted'};
  let title = titles[item.type] || item.type, body;
  if (item.type === 'commandExecution') { title += ' · ' + (item.command || '').slice(0,110); body = '<pre>' + esc(item.command) + '</pre><pre>' + esc(item.aggregatedOutput || 'Running…') + '</pre>'; }
  else if (item.type === 'fileChange') body = (item.changes || []).map(c => `<a href="#" data-file="${esc(c.path)}">${esc(c.path)}</a><pre>${esc(c.diff || pretty(c.kind))}</pre>`).join('');
  else if (item.type === 'reasoning') body = markdown((item.summary || []).join('\n'));
  else if (item.type === 'plan') body = markdown(item.text);
  else { if (item.tool) title += ' · ' + item.tool; if (item.query) title += ' · ' + item.query; body = '<pre>' + esc(pretty(item)) + '</pre>'; }
  return `<details class="tool" data-item="${esc(item.id)}"><summary><span class="status">${esc(item.status || '')}</span>${esc(title)}</summary><div class="tool-content">${body}</div></details>`;
}
function renderConversation(bottom = false) {
  if (!state.thread) return;
  const box = $('#messages'), nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  const open = new Set($$('details[open]', box).map(d => d.dataset.item)); const top = box.scrollTop;
  box.innerHTML = (state.turnCursor ? '<button id="older-turns" class="quiet">↑ Load earlier messages</button>' : '') + (state.thread.turns || []).map(turn => (turn.items || []).map(itemHTML).join('') + (turn.error ? '<div class="turn-error">' + esc(turn.error.message || pretty(turn.error)) + '</div>' : '')).join('') + (state.activeTurn ? '<div class="activity">● Codex is working…</div>' : '');
  $$('details', box).forEach(d => { d.open = open.has(d.dataset.item); });
  if (bottom || nearBottom) box.scrollTop = box.scrollHeight; else box.scrollTop = top;
}
function scheduleRender() { if (!renderTimer) renderTimer = setTimeout(() => { renderTimer = null; renderConversation(); header(); }, 70); }
function turnFor(id) { if (!state.thread) return null; let turn = state.thread.turns.find(t => t.id === id); if (!turn) { turn = {id, status:'inProgress', items:[]}; state.thread.turns.push(turn); } return turn; }
function applyEvent(message) {
  const m = message.method, p = message.params || {};
  if (m === 'remote/disconnected') { connected(false, 'Codex disconnected'); banner('The Codex connection closed. Check its server, then restart the web client.'); return; }
  if (m === 'account/login/completed') { toast(p.success ? 'Codex login completed.' : p.error || 'Login failed'); return; }
  if (m === 'thread/name/updated') { const t = state.threads.find(t => t.id === p.threadId); if (t) t.name = p.threadName || p.name; if (state.thread?.id === p.threadId) state.thread.name = p.threadName || p.name; renderThreads(); header(); }
  if (p.threadId !== state.thread?.id) return;
  if (m === 'turn/started' || m === 'turn/completed') {
    const turn = turnFor(p.turn.id); const oldItems = turn.items; Object.assign(turn, p.turn); if (!turn.items?.length) turn.items = oldItems;
    state.activeTurn = m === 'turn/started' ? turn.id : null;
    if (m === 'turn/completed') { run(loadThreads)(); if (document.hidden && 'Notification' in window && Notification.permission === 'granted') new Notification('Codex finished', {body:threadTitle(state.thread), icon:'/icon.svg'}); }
    scheduleRender(); return;
  }
  if (m === 'item/started' || m === 'item/completed') { const turn = turnFor(p.turnId), index = turn.items.findIndex(i => i.id === p.item.id); if (index < 0) turn.items.push(p.item); else turn.items[index] = p.item; scheduleRender(); return; }
  if (m.includes('/delta') || m.includes('Delta')) {
    const turn = turnFor(p.turnId); if (!turn) return;
    let item = turn.items.find(i => i.id === p.itemId);
    if (!item) { item = {id:p.itemId, type:m.includes('agentMessage') ? 'agentMessage' : m.includes('reasoning') ? 'reasoning' : m.includes('/plan/') ? 'plan' : 'commandExecution', text:'', summary:[], aggregatedOutput:''}; turn.items.push(item); }
    if (m === 'item/agentMessage/delta' || m === 'item/plan/delta') item.text = (item.text || '') + p.delta;
    else if (m === 'item/reasoning/summaryTextDelta') { item.summary ||= []; const index = p.summaryIndex || 0; item.summary[index] = (item.summary[index] || '') + p.delta; }
    else if (m === 'item/commandExecution/outputDelta') item.aggregatedOutput = ((item.aggregatedOutput || '') + p.delta).slice(-200000);
    scheduleRender(); return;
  }
  if (m === 'turn/diff/updated') state.diff = p.diff;
  if (m === 'turn/plan/updated') {
    const turn = turnFor(p.turnId), plan = {id:'remote-plan-' + p.turnId, type:'plan', text:(p.explanation || '') + '\n\n' + (p.plan || []).map(x => '- ' + (x.status === 'completed' ? '✓ ' : '') + x.step).join('\n')};
    const index = turn.items.findIndex(i => i.id === plan.id); if (index < 0) turn.items.push(plan); else turn.items[index] = plan; scheduleRender();
  }
  if (m === 'error') banner(p.error?.message || pretty(p));
}
async function poll(epoch) {
  let failed = false;
  while (epoch === state.epoch) {
    try {
      const result = await api('/api/events?after=' + state.cursor); if (epoch !== state.epoch) return;
      state.cursor = result.cursor; state.pending = result.pending;
      if (result.reset || failed) { if (state.thread && !state.loading) await openThread(state.thread.id, true); failed = false; }
      for (const event of result.events) { state.events.push(event); if (state.events.length > 200) state.events.shift(); if (state.loading) state.buffered.push(event); else if (event.seq > state.snapshotCursor) applyEvent(event.message); }
      renderRequests(); connected(result.alive, result.alive ? undefined : 'Codex stopped');
      if (!result.alive) { banner('Codex stopped. Restart the web server to continue.'); return; }
    } catch (e) { if (epoch !== state.epoch) return; failed = true; connected(false); await new Promise(r => setTimeout(r, 2500)); }
  }
}
async function boot() {
  const status = await api('/api/status');
  state.roots = status.roots; state.cwd = status.roots[0]; state.cursor = status.cursor; state.pending = status.pending;
  $('#machine-name').textContent = status.host; $('#login').classList.add('hidden'); $('#app').classList.remove('hidden'); connected(status.alive);
  const epoch = ++state.epoch; poll(epoch);
  await Promise.allSettled([loadThreads().catch(e => banner(e.message)), rpc('model/list', {}).then(r => { state.models = r.data.filter(m => !m.hidden); header(); }).catch(e => toast(e.message))]);
  const match = location.hash.match(/^#thread=(.+)$/); if (match) await openThread(decodeURIComponent(match[1])); else header();
}

async function sendMessage(event) {
  event.preventDefault();
  let text = $('#prompt').value.trim(); if ((!text && !state.attachments.length) || state.sending || state.commandBusy || state.loading || state.uploadCount) return;
  if (text.startsWith('//')) text = text.slice(1);
  else if (/^\/(?:[a-z][a-z0-9:_-]*(?:\s|$)|$)/i.test(text)) {
    state.commandBusy = true; header(); hideSlashMenu();
    try {
      const prompt = await executeSlash(text);
      if (prompt === undefined) { setPrompt(''); return; }
      text = prompt; setPrompt(prompt);
    } catch (error) { toast(error.message); return; }
    finally { state.commandBusy = false; header(); }
  }
  hideSlashMenu();
  state.sending = true; header(); const generation = state.generation;
  const input = text ? [{type:'text', text, text_elements:[]}] : [];
  for (const attachment of state.attachments) {
    if (attachment.skill) input.push({type:'skill', name:attachment.name, path:attachment.path});
    else if (attachment.image) input.push({type:'localImage', path:attachment.path});
    else input.push({type:'text', text:'Attached file on this machine: ' + JSON.stringify(attachment.path) + '\nRead this file if it is relevant to my request.', text_elements:[]});
  }
  try {
    if (!state.thread) {
      const params = {cwd:state.cwd, ...(state.model ? {model:state.model} : {})};
      if (state.permission) { params.sandbox = state.permission; params.approvalPolicy = 'on-request'; params.approvalsReviewer = 'user'; }
      const r = await rpc('thread/start', params);
      if (generation !== state.generation) { toast('Conversation created; select it from the sidebar to continue.'); await loadThreads(); return; }
      state.thread = r.thread; state.model = r.model; state.effort ||= r.reasoningEffort || ''; state.cwd = r.cwd || state.cwd;
      history.replaceState(null, '', '/#thread=' + encodeURIComponent(state.thread.id));
    }
    const id = state.thread.id;
    let params = {threadId:id, input};
    let method = state.activeTurn ? 'turn/steer' : 'turn/start';
    if (state.activeTurn) params.expectedTurnId = state.activeTurn;
    else {
      if (state.model) params.model = state.model; if (state.effort) params.effort = state.effort;
      if (state.model) params.collaborationMode = {mode:$('#mode').value, settings:{model:state.model, reasoning_effort:state.effort || null, developer_instructions:null}};
      if (state.permission) { params.sandboxPolicy = state.permission === 'read-only' ? {type:'readOnly'} : {type:'workspaceWrite', writableRoots:[state.cwd], networkAccess:false, excludeTmpdirEnvVar:false, excludeSlashTmp:false}; params.approvalPolicy = 'on-request'; params.approvalsReviewer = 'user'; }
    }
    const r = await rpc(method, params);
    if (state.thread?.id === id) {
      // Notifications own turn state; do not resurrect a turn that finished before this HTTP response.
      if (r.turn && !state.thread.turns.some(t => t.id === r.turn.id)) { state.thread.turns.push(r.turn); state.activeTurn = r.turn.id; }
      $('#prompt').value = ''; $('#prompt').style.height = ''; sessionStorage.removeItem('codex-remote-draft'); state.attachments = []; renderAttachments(); renderConversation(true);
    }
    await loadThreads();
  } catch (e) { banner(e.message); toast(e.message); }
  finally { state.sending = false; header(); }
}
async function upload(files) {
  state.uploadCount++; header();
  try {
    for (const file of files) {
      if (file.size > 25 * 1024 * 1024) { toast(file.name + ' exceeds the 25 MB limit.'); continue; }
      const response = await fetch('/api/upload', {method:'POST', body:file, headers:{'Content-Type':'application/octet-stream', 'X-File-Name':encodeURIComponent(file.name)}});
      const result = await response.json(); if (!response.ok) throw new Error(result.error);
      state.attachments.push(result); renderAttachments();
    }
  } finally { state.uploadCount--; header(); $('#file-input').value = ''; }
}
function renderAttachments() {
  $('#attachments').innerHTML = state.attachments.map((a, i) => `<div class="attachment">${a.image ? `<img src="${esc(fileURL(a.path))}" alt="">` : '▱'}<span>${esc(a.name)}</span><button data-remove="${i}" aria-label="Remove ${esc(a.name)}">×</button></div>`).join('');
}

let requestSignature = '';
let selectedRequestId = null;
function renderRequests() {
  const requests = state.pending.filter(r => !r.params?.threadId || r.params.threadId === state.thread?.id);
  const signature = JSON.stringify([state.thread?.id, state.pending.map(r => r.id)]); if (signature === requestSignature) return; requestSignature = signature;
  const other = state.pending.filter(r => r.params?.threadId && r.params.threadId !== state.thread?.id);
  $('#requests').replaceChildren();
  if (other.length) { const b = document.createElement('button'); b.className = 'quiet'; b.textContent = other.length + ' request(s) waiting in another conversation →'; b.onclick = run(() => openThread(other[0].params.threadId)); $('#requests').append(b); }
  if (!requests.some(r => r.id === selectedRequestId)) selectedRequestId = requests[0]?.id;
  if (requests.length > 1) {
    const index = requests.findIndex(r => r.id === selectedRequestId);
    const nav = document.createElement('div'); nav.className = 'request-navigation';
    const previous = document.createElement('button'); previous.textContent = '← Previous'; previous.disabled = index === 0;
    const count = document.createElement('span'); count.textContent = `Request ${index + 1} of ${requests.length}`;
    const next = document.createElement('button'); next.textContent = 'Next →'; next.disabled = index === requests.length - 1;
    const select = offset => { selectedRequestId = requests[index + offset].id; requestSignature = ''; renderRequests(); };
    previous.onclick = () => select(-1); next.onclick = () => select(1);
    nav.append(previous, count, next); $('#requests').append(nav);
  }
  for (const request of requests.filter(r => r.id === selectedRequestId)) {
    const card = document.createElement('section'); card.className = 'request-card'; const p = request.params || {}, m = request.method;
    const title = m.includes('requestUserInput') ? 'Codex has a question' : m.includes('Approval') ? 'Your approval is needed' : 'Codex needs your input';
    card.innerHTML = `<h3>${title}</h3><p>${esc(p.reason || p.message || '')}</p>`;
    if (p.command) card.innerHTML += '<pre>' + esc(pretty(p.command)) + '</pre>';
    if (p.cwd) card.innerHTML += '<p>Folder: ' + esc(p.cwd) + '</p>';
    const actions = document.createElement('div'); actions.className = 'request-actions';
    const respond = async result => { await api('/api/respond', {id:request.id, result}); state.pending = state.pending.filter(r => r.id !== request.id); renderRequests(); };
    const action = (label, value, primary = false) => { const b = document.createElement('button'); b.textContent = label; if (primary) b.className = 'primary'; b.onclick = run(async () => { b.disabled = true; try { await respond(typeof value === 'function' ? value() : value); } finally { b.disabled = false; } }); actions.append(b); };
    if (m === 'item/tool/requestUserInput') {
      const inputs = [];
      for (const q of p.questions || []) {
        const label = document.createElement('label'); label.textContent = q.question; card.append(label);
        const input = document.createElement('input'); input.type = q.isSecret ? 'password' : 'text'; input.placeholder = 'Your answer'; input.autocomplete = 'off'; inputs.push([q.id, input]);
        for (const o of q.options || []) { const b = document.createElement('button'); b.className = 'option'; b.textContent = o.label + (o.description ? ' — ' + o.description : ''); b.onclick = () => { input.value = o.label; }; card.append(b); }
        card.append(input);
      }
      action('Send answers', () => { if (inputs.some(([, i]) => !i.value.trim())) throw new Error('Answer each question before sending.'); return {answers:Object.fromEntries(inputs.map(([id,i]) => [id,{answers:[i.value]}]))}; }, true);
    } else if (m === 'item/commandExecution/requestApproval' || m === 'item/fileChange/requestApproval') {
      const item = state.thread?.turns.flatMap(t => t.items).find(i => i.id === p.itemId);
      if (item?.changes) card.innerHTML += '<pre>' + esc(pretty(item.changes)) + '</pre>';
      if (p.additionalPermissions || p.grantRoot) card.innerHTML += '<pre>' + esc(pretty(p.additionalPermissions || {grantRoot:p.grantRoot})) + '</pre>';
      const decisions = p.availableDecisions || ['accept', 'acceptForSession', 'decline', 'cancel'];
      const labels = {accept:'Allow once', acceptForSession:'Allow for session', decline:'Decline', cancel:'Stop turn'};
      for (const decision of decisions) { if (typeof decision === 'object') card.innerHTML += '<pre>' + esc(pretty(decision)) + '</pre>'; action(labels[decision] || 'Apply displayed rule', {decision}, decision === 'accept'); }
    } else if (m === 'execCommandApproval' || m === 'applyPatchApproval') {
      if (p.fileChanges) card.innerHTML += '<pre>' + esc(pretty(p.fileChanges)) + '</pre>';
      action('Allow once', {decision:'approved'}, true); action('Decline', {decision:'denied'});
    } else if (m === 'item/permissions/requestApproval') {
      card.innerHTML += '<pre>' + esc(pretty(p.permissions)) + '</pre>';
      action('Allow for this turn', {permissions:p.permissions, scope:'turn'}, true); action('Decline', {permissions:{}, scope:'turn'});
    } else if (m === 'mcpServer/elicitation/request') {
      if (p.url && /^https?:\/\//.test(p.url)) card.innerHTML += `<a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">Open authorization page ↗</a>`;
      const inputs = [], schema = p.requestedSchema || {};
      for (const [key, spec] of Object.entries(schema.properties || {})) {
        const label = document.createElement('label'); label.textContent = spec.title || key; card.append(label);
        const input = document.createElement(spec.enum ? 'select' : 'input');
        if (spec.enum) input.innerHTML = spec.enum.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
        else input.type = spec.type === 'boolean' ? 'checkbox' : spec.type === 'number' || spec.type === 'integer' ? 'number' : 'text';
        card.append(input); inputs.push([key, spec, input]);
      }
      action('Submit', () => ({action:'accept', content:Object.fromEntries(inputs.map(([key,s,i]) => { if ((schema.required || []).includes(key) && !i.value && s.type !== 'boolean') throw new Error('Complete ' + key); return [key,s.type === 'boolean' ? i.checked : ['integer','number'].includes(s.type) ? Number(i.value) : i.value]; }))}), true);
      action('Decline', {action:'decline'}); action('Cancel', {action:'cancel'});
    } else {
      card.innerHTML += '<p>This request needs a protocol response. Review the payload below.</p><pre>' + esc(pretty(request)) + '</pre>';
      const input = document.createElement('textarea'); input.placeholder = 'JSON response'; card.append(input);
      action('Send JSON response', () => JSON.parse(input.value));
      const decline = document.createElement('button'); decline.textContent = 'Unsupported request'; decline.onclick = run(() => api('/api/respond', {id:request.id, error:{code:-32601, message:'This web client does not implement this request.'}})); actions.append(decline);
    }
    const details = document.createElement('div'); details.className = 'request-details';
    details.tabIndex = 0; details.setAttribute('aria-label', 'Request details');
    while (card.children.length > 1) details.append(card.children[1]);
    card.append(details, actions); $('#requests').append(card);
  }
}

function panel(title, html) { hideSlashMenu(); $('#panel-title').textContent = title; $('#panel-content').innerHTML = html; if (!$('#panel').open) $('#panel').showModal(); }
async function filesPanel(path = state.cwd, choose = false) {
  panel(choose ? 'Choose a working folder' : 'Workspace files', '<p>Loading files…</p>');
  const data = await api('/api/files?path=' + encodeURIComponent(path));
  panel(choose ? 'Choose a working folder' : 'Workspace files', `<div class="file-toolbar"><button id="file-up" class="icon-button" aria-label="Parent folder">↑</button><input id="file-path" aria-label="Folder path" value="${esc(data.path)}"><button id="file-go" class="quiet">Go</button></div><div class="panel-actions">${state.roots.map((r,i) => `<button class="quiet" data-root="${i}">${esc(basename(r))}</button>`).join('')}${choose ? '<button id="choose-folder" class="primary">Use this folder</button>' : ''}</div><div id="file-list">${data.entries.map((f,i) => `<div class="file-entry"><span>${f.directory ? '▱' : imagePath(f.name) ? '▧' : '≡'}</span><button class="file-name" data-entry="${i}">${esc(f.name)}</button><small>${f.directory ? '' : sizeLabel(f.size)}</small>${f.directory ? '' : `<button class="quiet" data-attach-entry="${i}" title="Attach to conversation">＋</button><a class="quiet" href="${esc(fileURL(f.path, true))}" download title="Download">↓</a>`}</div>`).join('') || '<p class="empty">This folder is empty.</p>'}</div>${data.truncated ? '<p>Showing the first 2,000 entries.</p>' : ''}`);
  $('#file-up').onclick = run(() => filesPanel(data.parent, choose)); $('#file-go').onclick = run(() => filesPanel($('#file-path').value, choose));
  $('#file-path').onkeydown = e => { if (e.key === 'Enter') run(() => filesPanel(e.target.value, choose))(); };
  $$('[data-root]').forEach(b => b.onclick = run(() => filesPanel(state.roots[Number(b.dataset.root)], choose)));
  $$('[data-entry]').forEach(b => b.onclick = run(() => { const f = data.entries[Number(b.dataset.entry)]; return f.directory ? filesPanel(f.path, choose) : previewFile(f.path); }));
  $$('[data-attach-entry]').forEach(b => b.onclick = () => { const f = data.entries[Number(b.dataset.attachEntry)]; state.attachments.push({...f,image:imagePath(f.path)}); renderAttachments(); toast('Attached ' + f.name); });
  if (choose) $('#choose-folder').onclick = () => { if (state.thread) { toast('Start a new conversation to change its working folder.'); return; } state.cwd = data.path; header(); $('#panel').close(); };
}
async function previewFile(path) {
  panel(basename(path), `<div class="panel-actions"><a class="primary" href="${esc(fileURL(path,true))}" download>Download file ↓</a><button id="preview-attach" class="quiet">＋ Attach to message</button></div><div id="preview-body"><p>Loading preview…</p></div>`);
  $('#preview-attach').onclick = () => { state.attachments.push({path,name:basename(path),image:imagePath(path)}); renderAttachments(); $('#panel').close(); };
  if (imagePath(path)) $('#preview-body').innerHTML = `<img class="message-image" src="${esc(fileURL(path))}" alt="${esc(basename(path))}">`;
  else { const data = await api(fileURL(path)); $('#preview-body').innerHTML = data.binary ? '<p class="empty">Binary file · ' + sizeLabel(data.size) + '. Download it to open on your device.</p>' : '<pre class="preview-text">' + esc(data.text) + '</pre>' + (data.truncated ? '<p>Preview limited to 1 MB. Download for the complete file.</p>' : ''); }
}
function modelPanel() {
  panel('Model & behavior', `<p>Models come from your installed Codex and account. Changes apply to the next turn.</p><label for="model-select">Model</label><select id="model-select"><option value="">Codex default</option>${state.models.map(m => `<option value="${esc(m.model)}" ${m.model === state.model ? 'selected' : ''}>${esc(m.displayName)}</option>`).join('')}</select><p id="model-description" class="setting-detail"></p><label for="effort-select">Reasoning effort</label><select id="effort-select"></select><label for="permission-select">Permissions</label><select id="permission-select"><option value="">Use Codex configuration</option><option value="workspace-write">Workspace writes · ask for escalation</option><option value="read-only">Read only · ask for escalation</option></select><p class="setting-detail">Your Codex configuration supplies tools, skills, MCP servers, and permission defaults. Approval requests appear in the conversation.</p><button id="model-save" class="primary">Save settings</button>`);
  const update = () => { const model = state.models.find(m => m.model === $('#model-select').value); $('#model-description').textContent = model?.description || 'Use the default configured on your machine.'; $('#effort-select').innerHTML = '<option value="">Default</option>' + (model?.supportedReasoningEfforts || []).map(e => `<option value="${esc(e.reasoningEffort)}" ${e.reasoningEffort === state.effort ? 'selected' : ''}>${esc(e.reasoningEffort)}</option>`).join(''); };
  $('#model-select').onchange = update; update(); $('#permission-select').value = state.permission;
  $('#model-save').onclick = () => { state.model = $('#model-select').value; state.effort = $('#effort-select').value; state.permission = $('#permission-select').value; header(); $('#panel').close(); };
}
async function settingsPanel() {
  panel('Your machine', '<p>Loading Codex account…</p>');
  const results = await Promise.allSettled([rpc('account/read', {}), rpc('account/rateLimits/read', {})]);
  const account = results[0].status === 'fulfilled' ? results[0].value.account : null;
  panel('Your machine', `<div class="info-card"><strong>${esc($('#machine-name').textContent)}</strong><small>${esc(account ? (account.email || account.type) + (account.planType ? ' · ' + account.planType : '') : 'Codex is not signed in. Run codex login on the machine.')}</small></div><label>Shared folders</label><pre>${esc(state.roots.join('\n'))}</pre><div class="action-grid"><button id="settings-model">Model & behavior</button><button id="settings-skills">Skills</button><button id="settings-mcp">MCP servers</button><button id="settings-notifications">Enable notifications</button><button id="settings-events">Recent protocol events</button><button id="settings-rpc">Advanced protocol console</button></div><details class="tool"><summary>Usage limits</summary><div class="tool-content"><pre>${esc(pretty(results[1].status === 'fulfilled' ? results[1].value : results[1].reason.message))}</pre></div></details><p class="setting-detail">On your phone, use the browser’s “Add to Home Screen” action. Keep this server running for background work. Sessions survive browser disconnects; restarting the server requires signing in again.</p><button id="logout" class="quiet">Sign out of this browser</button>`);
  $('#settings-model').onclick = modelPanel;
  $('#settings-skills').onclick = run(skillsPanel);
  $('#settings-mcp').onclick = run(mcpPanel);
  $('#settings-notifications').onclick = run(async () => { if (!('Notification' in window)) throw new Error('Notifications are unavailable in this browser.'); const p = await Notification.requestPermission(); toast('Notifications: ' + p); });
  $('#settings-events').onclick = () => panel('Recent protocol events', '<p>All event types remain inspectable here.</p><pre class="preview-text">' + esc(pretty(state.events)) + '</pre>');
  $('#settings-rpc').onclick = protocolPanel;
  $('#logout').onclick = run(async () => { await api('/api/logout', {}); $('#panel').close(); showLogin(); });
}
async function skillsPanel() {
  panel('Skills', '<p>Loading…</p>'); const r = await rpc('skills/list', {cwds:[state.cwd]});
  const skills = (r.data || []).flatMap(d => d.skills || []);
  panel('Skills', '<p>Attach a skill to your next message.</p><div class="info-list">' + skills.map((s,i) => `<button class="info-card" data-skill="${i}"><strong>${esc(s.name)}</strong><small>${esc(s.description)}</small></button>`).join('') + '</div>');
  $$('[data-skill]').forEach(b => b.onclick = () => { const s = skills[Number(b.dataset.skill)]; state.attachments.push({name:s.name,path:s.path,skill:true}); renderAttachments(); $('#panel').close(); });
}
function protocolPanel() {
  panel('Advanced protocol console', '<p>Send methods supported by your installed Codex. Requests execute with your Codex account and can change its state.</p><label>Method</label><input id="rpc-method" value="thread/loaded/list"><label>Parameters (JSON)</label><textarea id="rpc-params" rows="6">{}</textarea><button id="rpc-run" class="primary">Send request</button><pre id="rpc-output"></pre>');
  $('#rpc-run').onclick = run(async () => { const r = await rpc($('#rpc-method').value, JSON.parse($('#rpc-params').value)); $('#rpc-output').textContent = pretty(r); });
}
function currentThreadId() {
  if (!state.thread) throw new Error('Start or open a conversation first.');
  return state.thread.id;
}
function requireIdle() {
  if (state.activeTurn) throw new Error('Wait for the current turn to finish, or use the Stop button first.');
}
async function mcpPanel() {
  panel('MCP servers', '<p>Loading…</p>');
  const r = await rpc('mcpServerStatus/list', {});
  panel('MCP servers', '<div class="info-list">' + r.data.map(s => `<div class="info-card"><strong>${esc(s.name)}</strong><small>${esc(s.authStatus)}</small><details class="tool"><summary>Tools and resources</summary><pre>${esc(pretty(s))}</pre></details></div>`).join('') + '</div>');
}
async function renameConversation(name) {
  const id = currentThreadId();
  if (!name) {
    panel('Rename conversation', `<label for="thread-name">Name</label><input id="thread-name" value="${esc(threadTitle(state.thread))}"><button id="rename-save" class="primary">Save</button>`);
    $('#rename-save').onclick = run(async () => { const value = $('#thread-name').value.trim(); if (!value) throw new Error('Enter a conversation name.'); await rpc('thread/name/set', {threadId:id,name:value}); if (state.thread?.id === id) state.thread.name = value; header(); await loadThreads(); $('#panel').close(); });
    return;
  }
  await rpc('thread/name/set', {threadId:id,name});
  if (state.thread?.id === id) state.thread.name = name;
  header(); await loadThreads(); toast('Conversation renamed.');
}
async function forkConversation() {
  requireIdle();
  const r = await rpc('thread/fork', {threadId:currentThreadId()});
  $('#panel').close(); await loadThreads(); await openThread(r.thread.id);
}
async function compactConversation() {
  requireIdle();
  await rpc('thread/compact/start', {threadId:currentThreadId()});
  $('#panel').close(); toast('Context compaction started.');
}
async function reviewConversation() {
  requireIdle();
  await rpc('review/start', {threadId:currentThreadId(),target:{type:'uncommittedChanges'},delivery:'inline'});
  $('#panel').close(); toast('Code review started.');
}
function diffPanel() {
  currentThreadId();
  panel('Latest turn diff', '<pre class="preview-text">' + esc(state.diff || 'No live diff captured yet. Expand File changes in the conversation to see persisted patches.') + '</pre>');
}
function threadActions() {
  if (!state.thread) { toast('Start or open a conversation first.'); return; }
  panel('Conversation actions', `<div class="action-grid"><button id="rename-thread">Rename</button><button id="fork-thread">Fork conversation</button><button id="archive-thread">${state.threadArchived ? 'Restore' : 'Archive'}</button><button id="compact-thread">Compact context</button><button id="diff-thread">View latest diff</button><button id="export-thread">Export conversation</button><button id="review-thread">Review changes</button><button id="goal-thread">Long-running goal</button></div>`);
  const id = state.thread.id;
  $('#rename-thread').onclick = run(() => renameConversation());
  $('#fork-thread').onclick = run(forkConversation);
  $('#archive-thread').onclick = run(async () => { await rpc(state.threadArchived ? 'thread/unarchive' : 'thread/archive', {threadId:id}); $('#panel').close(); newChat(); await loadThreads(); });
  $('#compact-thread').onclick = run(compactConversation);
  $('#diff-thread').onclick = run(diffPanel);
  $('#export-thread').onclick = run(async () => {
    const thread = (await rpc('thread/read', {threadId:id, includeTurns:true})).thread;
    if (thread.historyMode === 'paginated') {
      thread.turns = []; let cursor = null;
      do { const page = await rpc('thread/turns/list', {threadId:id, cursor, limit:50, sortDirection:'asc', itemsView:'full'}); thread.turns.push(...page.data); cursor = page.nextCursor; } while (cursor);
    }
    const blob = new Blob([pretty(thread)], {type:'application/json'}); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'codex-' + id + '.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $('#review-thread').onclick = run(reviewConversation);
  $('#goal-thread').onclick = run(() => goalPanel(id));
}
async function goalPanel(id = currentThreadId()) {
    const r = await rpc('thread/goal/get', {threadId:id});
    panel('Long-running goal', `<p>Give Codex an objective to pursue in this conversation. Availability follows your installed Codex version.</p><label for="goal-objective">Objective</label><textarea id="goal-objective" rows="4">${esc(r.goal?.objective || '')}</textarea><label for="goal-budget">Token budget (optional)</label><input id="goal-budget" type="number" min="1" step="1" value="${esc(r.goal?.tokenBudget || '')}" placeholder="Use Codex default"><div class="panel-actions"><button id="goal-save" class="primary">Set goal</button>${r.goal ? '<button id="goal-clear" class="quiet">Clear goal</button>' : ''}</div>${r.goal ? '<pre>' + esc(pretty(r.goal)) + '</pre>' : ''}`);
    $('#goal-save').onclick = run(async () => {
      const objective = $('#goal-objective').value.trim(); if (!objective) throw new Error('Enter a goal objective.');
      const params = {threadId:id, objective};
      if ($('#goal-budget').value) { params.tokenBudget = Number($('#goal-budget').value); if (!Number.isSafeInteger(params.tokenBudget) || params.tokenBudget <= 0) throw new Error('Use a positive whole-number token budget.'); }
      await rpc('thread/goal/set', params); $('#panel').close(); toast('Goal set. Send a message to start or continue work.');
    });
    if ($('#goal-clear')) $('#goal-clear').onclick = run(async () => { await rpc('thread/goal/clear', {threadId:id}); $('#panel').close(); toast('Goal cleared.'); });
}

const slashCommands = [
  {name:'help', description:'Show supported commands', execute:slashHelp},
  {name:'new', description:'Start a new conversation', execute:newChat},
  {name:'clear', description:'Clear the view and start a new conversation', execute:newChat},
  {name:'resume', description:'Find and open a saved conversation', execute:async () => { state.archived = false; await loadThreads(); $('#sidebar').classList.add('open'); $('#scrim').classList.toggle('hidden', !matchMedia('(max-width:700px)').matches); $('#search').focus(); }},
  {name:'model', args:'[model]', description:'Choose a model and reasoning effort', execute:arg => {
    if (!arg) return modelPanel();
    const model = state.models.find(m => m.model === arg || m.id === arg);
    if (!model) throw new Error('Unknown model. Use /model to choose an available model.');
    state.model = model.model; state.effort = ''; header(); toast('Model: ' + model.displayName);
  }},
  {name:'reasoning', args:'[effort]', description:'Choose reasoning effort', execute:arg => {
    if (!arg) { modelPanel(); $('#effort-select').focus(); return; }
    const model = state.models.find(m => m.model === state.model) || state.models.find(m => m.isDefault);
    if (arg !== 'default' && !model?.supportedReasoningEfforts.some(e => e.reasoningEffort === arg)) throw new Error('Unsupported effort. Use /reasoning to see the available choices.');
    state.effort = arg === 'default' ? '' : arg; header(); toast('Reasoning: ' + arg);
  }},
  {name:'permissions', aliases:['approvals'], args:'[default|read-only|workspace-write]', description:'Choose Codex permissions', execute:arg => {
    if (!arg) { modelPanel(); $('#permission-select').focus(); return; }
    if (!['default','read-only','workspace-write'].includes(arg)) throw new Error('Use /permissions default, read-only, or workspace-write.');
    state.permission = arg === 'default' ? '' : arg; toast('Permissions apply to the next turn: ' + arg);
  }},
  {name:'plan', args:'[prompt]', description:'Enter Plan mode; optionally send a planning request', execute:arg => { requireIdle(); $('#mode').value = 'plan'; header(); if (arg) return arg; toast('Plan mode enabled for your next message.'); }},
  {name:'code', description:'Return to Code mode', execute:() => { requireIdle(); $('#mode').value = 'default'; header(); toast('Code mode enabled for your next message.'); }},
  {name:'review', description:'Review uncommitted changes', execute:reviewConversation},
  {name:'compact', description:'Compact the current conversation’s context', execute:compactConversation},
  {name:'fork', description:'Branch the current conversation', execute:forkConversation},
  {name:'rename', args:'[name]', description:'Rename the current conversation', execute:renameConversation},
  {name:'diff', description:'View the latest Codex turn diff', execute:diffPanel},
  {name:'goal', description:'View or edit the conversation’s long-running goal', execute:() => goalPanel()},
  {name:'status', description:'View account, machine, and usage limits', execute:settingsPanel},
  {name:'skills', description:'Browse and attach installed skills', execute:skillsPanel},
  {name:'mcp', description:'Inspect MCP servers and tools', execute:mcpPanel},
  {name:'files', args:'[folder]', description:'Browse workspace files and attachments', execute:arg => filesPanel(arg || state.cwd)},
];
let slashMatches = [], slashIndex = 0, slashDismissed = false;
function setPrompt(value) {
  const input = $('#prompt'); input.value = value; input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 180) + 'px';
  if (value) sessionStorage.setItem('codex-remote-draft', value); else sessionStorage.removeItem('codex-remote-draft');
  hideSlashMenu();
}
function hideSlashMenu() {
  $('#slash-menu').classList.add('hidden'); $('#prompt').setAttribute('aria-expanded', 'false'); $('#prompt').removeAttribute('aria-activedescendant');
}
function updateSlashMenu(reset = true) {
  const match = $('#prompt').value.match(/^\/([a-z-]*)$/i);
  if (!match || slashDismissed || $('#panel').open) { hideSlashMenu(); return; }
  const query = match[1].toLowerCase();
  slashMatches = slashCommands.filter(c => c.name.startsWith(query) || c.aliases?.some(a => a.startsWith(query)));
  if (reset) slashIndex = 0;
  slashIndex = Math.min(slashIndex, Math.max(0, slashMatches.length - 1));
  $('#slash-menu').innerHTML = '<div class="slash-heading">Commands <span>↑ ↓ choose · Enter run · Esc close</span></div>' + (slashMatches.length ? slashMatches.map((c,i) => `<button type="button" role="option" id="slash-option-${i}" data-slash="${esc(c.name)}" aria-selected="${i === slashIndex}" class="slash-option ${i === slashIndex ? 'selected' : ''}"><span><strong>/${esc(c.name)}</strong>${c.args ? `<small> ${esc(c.args)}</small>` : ''}</span><small>${esc(c.description)}</small></button>`).join('') : '<div class="empty">No supported command. Use /help, or // to send a literal slash.</div>');
  $('#slash-menu').classList.remove('hidden'); $('#prompt').setAttribute('aria-expanded', 'true');
  if (slashMatches.length) { $('#prompt').setAttribute('aria-activedescendant', 'slash-option-' + slashIndex); $('#slash-option-' + slashIndex).scrollIntoView({block:'nearest'}); }
  else $('#prompt').removeAttribute('aria-activedescendant');
}
async function executeSlash(text) {
  const match = text.match(/^\/([a-z][a-z0-9:_-]*)?(?:\s+([\s\S]*))?$/i);
  const name = (match?.[1] || 'help').toLowerCase(), argument = match?.[2]?.trim() || '';
  const command = slashCommands.find(c => c.name === name || c.aliases?.includes(name));
  if (!command) throw new Error('Unsupported command /' + name + '. Use /help for this app’s commands, or // to send literal text.');
  if (argument && !command.args) throw new Error('/' + name + ' takes no arguments here. Run it by itself to open its controls.');
  return command.execute(argument);
}
function selectSlash(name, execute = true) {
  setPrompt('/' + name); $('#prompt').focus();
  if (execute) $('#composer').requestSubmit();
}
function slashHelp() {
  panel('Slash commands', '<p>Type / to choose a command. Tap a suggestion or press Enter to run it. These commands control this web app and its connected Codex session.</p><div class="slash-help">' + slashCommands.map(c => `<button type="button" data-help-command="${esc(c.name)}"><strong>/${esc(c.name)} ${esc(c.args || '')}</strong><small>${esc(c.description)}</small></button>`).join('') + '</div><p class="setting-detail">/approvals is an alias for /permissions. /code, /files, and /help are web-client shortcuts. Commands not listed here, including terminal-only and custom /prompts: commands, are not implemented. Prefix a message with // to send a literal slash, for example //model.</p>');
  $$('[data-help-command]').forEach(button => { button.onclick = () => { $('#panel').close(); selectSlash(button.dataset.helpCommand, false); }; });
}

$('#login-form').onsubmit = async e => { e.preventDefault(); const b = $('button', e.target); b.disabled = true; $('#login-error').textContent = ''; try { await api('/api/login', {token:$('#token').value.trim()}); $('#token').value = ''; await boot(); } catch (e) { $('#login-error').textContent = e.message; } finally { b.disabled = false; } };
$('#new-chat').onclick = newChat;
document.addEventListener('keydown', e => { if (e.key.toLowerCase() === 'n' && !e.ctrlKey && !e.metaKey && !e.altKey && !/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) && !$('#panel').open && !$('#app').classList.contains('hidden')) { e.preventDefault(); newChat(); $('#prompt').focus(); } });
$('#menu').onclick = () => { $('#sidebar').classList.add('open'); $('#scrim').classList.remove('hidden'); };
$('#scrim').onclick = closeSidebar;
$('#search').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(run(() => loadThreads()), 300); };
$('#more-threads').onclick = run(() => loadThreads(true));
$('#archive-filter').onclick = run(async () => { state.archived = !state.archived; await loadThreads(); });
$('#threads').onclick = run(e => { const b = e.target.closest('[data-thread]'); if (b) return openThread(b.dataset.thread); });
$('#composer').onsubmit = sendMessage;
$('#prompt').oninput = e => { e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight,180) + 'px'; sessionStorage.setItem('codex-remote-draft', e.target.value); slashDismissed = false; updateSlashMenu(); };
$('#prompt').value = sessionStorage.getItem('codex-remote-draft') || '';
$('#prompt').onkeydown = e => {
  if (e.isComposing) return;
  if (!$('#slash-menu').classList.contains('hidden')) {
    if (e.key === 'Escape') { e.preventDefault(); slashDismissed = true; hideSlashMenu(); return; }
    if (['ArrowDown','ArrowUp'].includes(e.key) && slashMatches.length) { e.preventDefault(); slashIndex = (slashIndex + (e.key === 'ArrowDown' ? 1 : -1) + slashMatches.length) % slashMatches.length; updateSlashMenu(false); return; }
    if (e.key === 'Tab' && slashMatches.length) { e.preventDefault(); selectSlash(slashMatches[slashIndex].name, false); return; }
    if (e.key === 'Enter' && !e.shiftKey && slashMatches.length) { e.preventDefault(); selectSlash(slashMatches[slashIndex].name); return; }
  }
  if (e.key === 'Enter' && !e.shiftKey && (e.ctrlKey || e.metaKey || matchMedia('(pointer:fine)').matches || /^\/[a-z-]+(?:\s|$)/i.test(e.target.value))) { e.preventDefault(); $('#composer').requestSubmit(); }
};
$('#slash-menu').onmousedown = e => e.preventDefault();
$('#slash-menu').onclick = e => { const button = e.target.closest('[data-slash]'); if (button) selectSlash(button.dataset.slash); };
$('#stop').onclick = run(() => rpc('turn/interrupt', {threadId:state.thread.id,turnId:state.activeTurn}));
$('#attach').onclick = () => $('#file-input').click(); $('#file-input').onchange = run(e => upload([...e.target.files]));
$('#prompt').onpaste = e => { const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); run(() => upload(files))(); } };
$('#composer').ondragover = e => e.preventDefault(); $('#composer').ondrop = e => { e.preventDefault(); run(() => upload([...e.dataTransfer.files]))(); };
$('#attachments').onclick = e => { const b = e.target.closest('[data-remove]'); if (b) { state.attachments.splice(Number(b.dataset.remove),1); renderAttachments(); } };
$('#files-open').onclick = run(() => filesPanel()); $('#project-button').onclick = run(() => filesPanel(state.cwd,true));
$('#model-button').onclick = modelPanel; $('#settings-open').onclick = run(settingsPanel); $('#thread-menu').onclick = threadActions;
$('#panel-close').onclick = () => $('#panel').close();
document.addEventListener('click', run(async e => {
  const file = e.target.closest('[data-file]'); if (file) { e.preventDefault(); return previewFile(file.dataset.file); }
  const suggestion = e.target.closest('[data-prompt]'); if (suggestion) { $('#prompt').value = suggestion.dataset.prompt; $('#prompt').focus(); }
  const copy = e.target.closest('.copy-code'); if (copy) { if (!navigator.clipboard) throw new Error('Copy requires HTTPS or localhost. Select the code to copy manually.'); await navigator.clipboard.writeText($('code',copy.parentElement).textContent); toast('Copied.'); }
  if (e.target.id === 'older-turns') return olderTurns();
}));
document.addEventListener('visibilitychange', () => { if (!document.hidden && state.thread && !state.loading) run(() => openThread(state.thread.id, true))(); });
boot().catch(e => { if (!$('#app').classList.contains('hidden')) banner(e.message); });
