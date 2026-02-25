#!/usr/bin/env python3
import json
import os
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.getenv('HOST', '127.0.0.1')
PORT = 8766
MAX_LIST = 300
MAX_EVENTS = 3000


def get_sessions_dir() -> Path:
    raw = os.getenv('SESSIONS_DIR') or os.getenv('COPILOT_SESSIONS_DIR')
    if raw:
        return Path(raw).expanduser()
    candidates = []

    userprofile = os.getenv('USERPROFILE')
    if userprofile:
        candidates.append(Path(userprofile) / '.copilot' / 'session-state')

    candidates.append(Path.home() / '.copilot' / 'session-state')

    # Fallback for WSL access to Windows profile.
    win_home = os.getenv('WIN_HOME')
    if win_home:
        candidates.append(Path(win_home) / '.copilot' / 'session-state')

    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def iter_session_files(root: Path):
    if not root.exists():
        return []
    files = []
    files.extend(root.glob('*.jsonl'))
    files.extend(root.glob('*/events.jsonl'))
    files = [p for p in files if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def extract_text(raw):
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                txt = item.get('text') or item.get('content')
                if isinstance(txt, str):
                    parts.append(txt)
        return '\n'.join(parts).strip()
    return ''


def derive_session_id(path: Path) -> str:
    if path.name == 'events.jsonl':
        return path.parent.name
    return path.stem


def find_workspace_yaml(path: Path) -> Path | None:
    if path.name == 'events.jsonl':
        p = path.parent / 'workspace.yaml'
        return p if p.exists() else None

    root = get_sessions_dir()
    session_id = derive_session_id(path)
    p = root / session_id / 'workspace.yaml'
    if p.exists():
        return p
    return None


def parse_workspace_yaml(path: Path) -> dict:
    data = {}
    try:
        with path.open('r', encoding='utf-8') as f:
            for line in f:
                raw = line.strip()
                if not raw or raw.startswith('#'):
                    continue
                if ':' not in raw:
                    continue
                k, v = raw.split(':', 1)
                data[k.strip()] = v.strip().strip('"\'')
    except Exception:
        return {}
    return data


def summarize_session(path: Path):
    summary = {
        'id': derive_session_id(path),
        'path': str(path),
        'relative_path': str(path),
        'mtime': datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        'session_id': '',
        'started_at': '',
        'cwd': '',
        'model': '',
        'summary': '',
        'first_user_text': '',
        'search_text': '',
    }

    try:
        summary['relative_path'] = str(path.relative_to(get_sessions_dir()))
    except Exception:
        pass

    search_chunks = []
    search_len = 0
    search_limit = 2500

    try:
        with path.open('r', encoding='utf-8') as f:
            for line in f:
                obj = json.loads(line)
                t = obj.get('type', '')
                payload = obj.get('data', {})

                if t == 'session.start':
                    summary['session_id'] = payload.get('sessionId', '')
                    summary['started_at'] = payload.get('startTime', '')
                    summary['model'] = payload.get('copilotVersion', '')
                    ctx = payload.get('context', {})
                    if isinstance(ctx, dict):
                        summary['cwd'] = ctx.get('cwd', '')
                elif t == 'user.message':
                    text = extract_text(payload.get('content', '')) or extract_text(payload.get('transformedContent', ''))
                    if text and not summary['first_user_text']:
                        summary['first_user_text'] = text.replace('\n', ' ')[:180]
                    if text and search_len < search_limit:
                        cut = text.replace('\n', ' ')[:300]
                        search_chunks.append(cut)
                        search_len += len(cut)
                elif t == 'assistant.message':
                    text = extract_text(payload.get('content', ''))
                    if text and search_len < search_limit:
                        cut = text.replace('\n', ' ')[:300]
                        search_chunks.append(cut)
                        search_len += len(cut)
                elif t in ('session.info', 'session.error'):
                    msg = payload.get('message', '')
                    if msg and search_len < search_limit:
                        cut = msg.replace('\n', ' ')[:220]
                        search_chunks.append(cut)
                        search_len += len(cut)
    except Exception:
        pass

    workspace_yaml = find_workspace_yaml(path)
    if workspace_yaml:
        ws = parse_workspace_yaml(workspace_yaml)
        if not summary['cwd']:
            summary['cwd'] = ws.get('cwd', '')
        if not summary['started_at']:
            summary['started_at'] = ws.get('created_at', '')
        summary['summary'] = ws.get('summary', '')

    if not summary['session_id']:
        summary['session_id'] = summary['id']

    summary['search_text'] = ' '.join(search_chunks)
    return summary


def load_session_events(path: Path):
    events = []
    raw_count = 0

    with path.open('r', encoding='utf-8') as f:
        for line in f:
            raw_count += 1
            obj = json.loads(line)
            t = obj.get('type', '')
            ts = obj.get('timestamp', '')
            data = obj.get('data', {})

            if t == 'user.message':
                text = extract_text(data.get('content', '')) or extract_text(data.get('transformedContent', ''))
                if text:
                    events.append({'timestamp': ts, 'kind': 'message', 'role': 'user', 'text': text})

            elif t == 'assistant.message':
                text = extract_text(data.get('content', ''))
                if text:
                    events.append({'timestamp': ts, 'kind': 'message', 'role': 'assistant', 'text': text})

                tool_requests = data.get('toolRequests', [])
                if isinstance(tool_requests, list):
                    for req in tool_requests:
                        if not isinstance(req, dict):
                            continue
                        events.append({
                            'timestamp': ts,
                            'kind': 'function_call',
                            'role': 'assistant',
                            'name': req.get('name', ''),
                            'arguments': json.dumps(req.get('arguments', {}), ensure_ascii=False, indent=2),
                        })

            elif t == 'tool.execution_start':
                events.append({
                    'timestamp': ts,
                    'kind': 'tool_start',
                    'role': 'system',
                    'name': data.get('toolName', ''),
                    'arguments': json.dumps(data.get('arguments', {}), ensure_ascii=False, indent=2),
                })

            elif t == 'tool.execution_complete':
                result = data.get('result', {})
                text = json.dumps(result, ensure_ascii=False, indent=2) if result else ''
                events.append({
                    'timestamp': ts,
                    'kind': 'tool_output',
                    'role': 'system',
                    'success': data.get('success', None),
                    'text': text,
                })

            elif t == 'session.info':
                events.append({'timestamp': ts, 'kind': 'info', 'role': 'system', 'text': data.get('message', '')})

            elif t == 'session.error':
                events.append({'timestamp': ts, 'kind': 'error', 'role': 'system', 'text': data.get('message', '')})

            elif t.startswith('assistant.turn_'):
                events.append({'timestamp': ts, 'kind': t, 'role': 'system', 'text': ''})

            if len(events) >= MAX_EVENTS:
                break

    return {'events': events, 'raw_line_count': raw_count}


HTML_PAGE = """<!doctype html>
<html lang=\"ja\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>GitHub Copilot Sessions Viewer</title>
<style>
:root {
  --bg: #eff4f7;
  --panel: #ffffff;
  --line: #c8d4df;
  --text: #1a2430;
  --muted: #596b7d;
  --accent: #0b6d6d;
  --user: #1b64d6;
  --assistant: #0f7c4f;
  --system: #5a6673;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  font-family: "Segoe UI", "Yu Gothic UI", sans-serif;
  color: var(--text);
  background: radial-gradient(circle at top right, #e5f1fb 0%, var(--bg) 45%);
  overflow: hidden;
}
header {
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
  background: rgba(255,255,255,0.9);
}
header h1 { margin: 0; font-size: 18px; }
header small { color: var(--muted); }
.container {
  display: grid;
  grid-template-columns: 360px 1fr;
  height: calc(100vh - 64px);
  overflow: hidden;
}
.left {
  border-right: 1px solid var(--line);
  background: #f8fbff;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.toolbar {
  padding: 10px;
  border-bottom: 1px solid var(--line);
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}
input, select, button {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
}
#cwd_q, #q { flex: 1 1 220px; }
#date_from, #date_to { flex: 1 1 185px; }
button {
  background: var(--accent);
  color: #fff;
  cursor: pointer;
}
#sessions {
  overflow: auto;
  flex: 1;
}
.session-item {
  padding: 10px 12px;
  border-bottom: 1px solid #e7eef6;
  cursor: pointer;
}
.session-item:hover { background: #eef7ff; }
.session-item.active { background: #dff0ff; }
.session-path {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-preview {
  margin-top: 4px;
  font-size: 12px;
  color: #34414f;
}
.badge {
  display: inline-block;
  margin-top: 6px;
  margin-right: 6px;
  font-size: 11px;
  border-radius: 6px;
  padding: 2px 6px;
  border: 1px solid #c7d8ea;
  background: #f2f8ff;
}
.right {
  background: var(--panel);
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.meta {
  padding: 12px;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
  color: var(--muted);
}
code.path-code {
  color: #0b4a52;
  background: #e5f4f6;
  border: 1px solid #b8dee3;
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 700;
}
.detail-toolbar {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  align-items: center;
  background: #f8fbff;
}
label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #324255;
  user-select: none;
}
#events {
  padding: 14px;
  overflow: auto;
  flex: 1;
}
.ev {
  border: 1px solid var(--line);
  border-left-width: 5px;
  border-radius: 10px;
  padding: 10px;
  margin-bottom: 10px;
  background: #fff;
}
.ev.user { border-left-color: var(--user); background: #eaf3ff; }
.ev.assistant { border-left-color: var(--assistant); }
.ev.system { border-left-color: var(--system); background: #f7f9fb; }
.ev-head {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 8px;
}
pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
}
@media (max-width: 900px) {
  .container {
    grid-template-columns: 1fr;
    grid-template-rows: 42vh 1fr;
  }
}
</style>
</head>
<body>
<header>
  <h1>GitHub Copilot Sessions Viewer</h1>
  <small id=\"root\"></small>
</header>
<div class=\"container\">
  <aside class=\"left\">
    <div class=\"toolbar\">
      <input id=\"cwd_q\" placeholder=\"cwd (部分一致)\" />
      <input id=\"date_from\" type=\"date\" />
      <input id=\"date_to\" type=\"date\" />
      <input id=\"q\" placeholder=\"keyword filter\" />
      <select id=\"mode\">
        <option value=\"and\">keyword AND</option>
        <option value=\"or\">keyword OR</option>
      </select>
      <button id=\"reload\">Reload</button>
    </div>
    <div id=\"sessions\"></div>
  </aside>
  <main class=\"right\">
    <div class=\"meta\" id=\"meta\">セッションを選択してください</div>
    <div class=\"detail-toolbar\">
      <label><input type=\"checkbox\" id=\"only_user_instruction\" /> ユーザー指示のみ表示</label>
      <label><input type=\"checkbox\" id=\"reverse_order\" /> 表示順を逆にする</label>
    </div>
    <div id=\"events\"></div>
  </main>
</div>
<script>
const state = {
  sessions: [],
  filtered: [],
  activePath: null,
  activeSession: null,
  activeEvents: [],
  activeRawLineCount: 0,
};

function esc(s){
  return (s ?? '').toString().replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
}

function fmt(ts){
  if(!ts) return '';
  const d = new Date(ts);
  return isNaN(d) ? ts : d.toLocaleString();
}

function toTimestamp(ts){
  if(!ts) return NaN;
  const d = new Date(ts);
  return d.getTime();
}

function parseOptionalDateStart(raw){
  if(!raw) return null;
  const ts = toTimestamp(`${raw}T00:00:00`);
  return Number.isNaN(ts) ? null : ts;
}

function parseOptionalDateEnd(raw){
  if(!raw) return null;
  const ts = toTimestamp(`${raw}T23:59:59.999`);
  return Number.isNaN(ts) ? null : ts;
}

async function loadSessions(){
  const r = await fetch('/api/sessions');
  const data = await r.json();
  state.sessions = data.sessions || [];
  document.getElementById('root').textContent = data.root || '';
  applyFilter();
}

function applyFilter(){
  const cwdQ = document.getElementById('cwd_q').value.toLowerCase().trim();
  const q = document.getElementById('q').value.toLowerCase().trim();
  const fromTs = parseOptionalDateStart(document.getElementById('date_from').value);
  const toTs = parseOptionalDateEnd(document.getElementById('date_to').value);
  const mode = document.getElementById('mode').value;
  const terms = q.split(new RegExp('\\\\s+')).filter(Boolean);

  state.filtered = state.sessions.filter(s => {
    const cwdMatched = !cwdQ || (s.cwd || '').toLowerCase().includes(cwdQ);

    let dateMatched = true;
    if(fromTs !== null || toTs !== null){
      const sessionTs = toTimestamp(s.started_at || s.mtime);
      if(Number.isNaN(sessionTs)){
        dateMatched = false;
      } else {
        if(fromTs !== null && sessionTs < fromTs){ dateMatched = false; }
        if(toTs !== null && sessionTs > toTs){ dateMatched = false; }
      }
    }

    let keywordMatched = true;
    if(terms.length > 0){
      const target = (
        (s.relative_path || '') + ' ' +
        (s.first_user_text || '') + ' ' +
        (s.summary || '') + ' ' +
        (s.search_text || '')
      ).toLowerCase();
      keywordMatched = mode === 'or'
        ? terms.some(t => target.includes(t))
        : terms.every(t => target.includes(t));
    }

    return cwdMatched && dateMatched && keywordMatched;
  });

  renderSessionList();
}

function renderSessionList(){
  const box = document.getElementById('sessions');
  box.innerHTML = state.filtered.map(s => `
    <div class="session-item ${state.activePath === s.path ? 'active' : ''}" data-path="${esc(s.path)}">
      <div class="session-path">${esc(s.relative_path || '')}</div>
      <div class="session-preview">${esc(s.first_user_text || '(previewなし)')}</div>
      <div class="badge">cwd: ${esc(s.cwd || '-')}</div>
      <div class="badge">time: ${esc(fmt(s.started_at || s.mtime))}</div>
      <div class="badge">id: ${esc(s.session_id || s.id || '')}</div>
    </div>
  `).join('');

  box.querySelectorAll('.session-item').forEach(el => {
    el.onclick = () => openSession(el.dataset.path);
  });
}

function getDisplayEvents(){
  let events = state.activeEvents || [];
  if(document.getElementById('only_user_instruction').checked){
    events = events.filter(ev => ev.kind === 'message' && ev.role === 'user');
  }
  if(document.getElementById('reverse_order').checked){
    events = [...events].reverse();
  }
  return events;
}

function renderActiveSession(){
  const meta = document.getElementById('meta');
  const eventsBox = document.getElementById('events');
  if(!state.activeSession){
    meta.textContent = 'セッションを選択してください';
    eventsBox.innerHTML = '';
    return;
  }

  const displayEvents = getDisplayEvents();
  meta.innerHTML = `path: <code class="path-code">${esc(state.activeSession.relative_path || '')}</code> | cwd: ${esc(state.activeSession.cwd || '-')} | events: ${displayEvents.length}/${state.activeEvents.length} | raw lines: ${state.activeRawLineCount}`;

  eventsBox.innerHTML = displayEvents.map(ev => {
    const role = ev.role || 'system';
    let body = '';
    if(ev.kind === 'message' || ev.kind === 'info' || ev.kind === 'error' || ev.kind.startsWith('assistant.turn_')){
      body = `<pre>${esc(ev.text || '')}</pre>`;
    } else if(ev.kind === 'function_call' || ev.kind === 'tool_start'){
      body = `<pre>name: ${esc(ev.name || '')}\n${esc(ev.arguments || '')}</pre>`;
    } else if(ev.kind === 'tool_output'){
      const ok = ev.success === null || ev.success === undefined ? '' : `success: ${ev.success}\n`;
      body = `<pre>${esc(ok + (ev.text || ''))}</pre>`;
    } else {
      body = `<pre>${esc(JSON.stringify(ev, null, 2))}</pre>`;
    }
    return `<div class="ev ${esc(role)}"><div class="ev-head">${esc(ev.kind)} | ${esc(role)} | ${esc(fmt(ev.timestamp))}</div>${body}</div>`;
  }).join('');
}

async function openSession(path){
  state.activePath = path;
  renderSessionList();

  const r = await fetch('/api/session?path=' + encodeURIComponent(path));
  const data = await r.json();
  if(data.error){
    state.activeSession = null;
    state.activeEvents = [];
    state.activeRawLineCount = 0;
    document.getElementById('meta').textContent = data.error;
    document.getElementById('events').innerHTML = '';
    return;
  }

  state.activeSession = data.session;
  state.activeEvents = data.events || [];
  state.activeRawLineCount = data.raw_line_count || 0;
  renderActiveSession();
}

document.getElementById('cwd_q').addEventListener('input', applyFilter);
document.getElementById('date_from').addEventListener('change', applyFilter);
document.getElementById('date_to').addEventListener('change', applyFilter);
document.getElementById('q').addEventListener('input', applyFilter);
document.getElementById('mode').addEventListener('change', applyFilter);
document.getElementById('reload').addEventListener('click', loadSessions);
document.getElementById('only_user_instruction').addEventListener('change', renderActiveSession);
document.getElementById('reverse_order').addEventListener('change', renderActiveSession);

loadSessions();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, text, status=200):
        raw = text.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/':
            self._send_html(HTML_PAGE)
            return

        if parsed.path == '/api/sessions':
            root = get_sessions_dir()
            files = iter_session_files(root)[:MAX_LIST]
            sessions = [summarize_session(p) for p in files]
            self._send_json({'root': str(root), 'sessions': sessions})
            return

        if parsed.path == '/api/session':
            root = get_sessions_dir().resolve()
            q = urllib.parse.parse_qs(parsed.query)
            raw_path = q.get('path', [''])[0]
            if not raw_path:
                self._send_json({'error': 'path is required'}, 400)
                return

            p = Path(raw_path).expanduser().resolve()
            try:
                p.relative_to(root)
            except Exception:
                self._send_json({'error': 'path is outside sessions dir'}, 400)
                return

            if not p.exists() or not p.is_file():
                self._send_json({'error': 'session file not found'}, 404)
                return

            session = summarize_session(p)
            data = load_session_events(p)
            data['session'] = session
            self._send_json(data)
            return

        self._send_html('<h1>404</h1>', 404)


def main():
    root = get_sessions_dir()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'Viewer: http://{HOST}:{PORT}')
    print(f'Sessions dir: {root}')
    if not root.exists():
        print('WARNING: sessions dir does not exist. Set SESSIONS_DIR or COPILOT_SESSIONS_DIR.')
    server.serve_forever()


if __name__ == '__main__':
    main()
