#!/usr/bin/env python3
import json
import os
import sqlite3
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.getenv('HOST', '127.0.0.1')
PORT = 8766
MAX_LIST = 300
MAX_EVENTS = 3000


def _unique_paths(paths):
    out = []
    seen = set()
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _path_exists_safe(path: Path) -> bool:
    try:
        return path.exists()
    except Exception:
        return False


def get_session_roots():
    raw = os.getenv('SESSIONS_DIR') or os.getenv('COPILOT_SESSIONS_DIR')
    if raw:
        parts = [x.strip() for x in raw.split(os.pathsep) if x.strip()]
        return _unique_paths([Path(x).expanduser() for x in parts])

    candidates = []
    userprofile = os.getenv('USERPROFILE')
    appdata = os.getenv('APPDATA')
    win_home = os.getenv('WIN_HOME')
    home = Path.home()

    if userprofile:
        up = Path(userprofile)
        candidates.append(up / '.copilot' / 'session-state')
        if not appdata:
            appdata = str(up / 'AppData' / 'Roaming')

    if appdata:
        code_user = Path(appdata) / 'Code' / 'User'
        candidates.append(code_user / 'workspaceStorage')
        candidates.append(code_user / 'globalStorage' / 'github.copilot-chat')

    candidates.append(home / '.copilot' / 'session-state')

    if win_home:
        wh = Path(win_home)
        candidates.append(wh / '.copilot' / 'session-state')

    # WSL fallback: discover Windows profiles when USERPROFILE/APPDATA are not available.
    users_root = Path('/mnt/c/Users')
    if _path_exists_safe(users_root):
        try:
            dirs = list(users_root.iterdir())
        except Exception:
            dirs = []
        for d in dirs:
            try:
                if not d.is_dir():
                    continue
            except Exception:
                continue
            candidates.append(d / '.copilot' / 'session-state')
            candidates.append(d / 'AppData' / 'Roaming' / 'Code' / 'User' / 'workspaceStorage')
            candidates.append(d / 'AppData' / 'Roaming' / 'Code' / 'User' / 'globalStorage' / 'github.copilot-chat')

    candidates = _unique_paths(candidates)
    existing = [p for p in candidates if _path_exists_safe(p)]
    return existing if existing else candidates


def get_sessions_dir() -> Path:
    roots = get_session_roots()
    return roots[0]


def iter_session_files(root: Path):
    if not _path_exists_safe(root):
        return []
    files = []
    root_l = str(root).lower()
    if 'workspacestorage' in root_l:
        files.extend(root.glob('*/chatSessions/*.jsonl'))
    elif 'github.copilot-chat' in root_l:
        files.extend(root.glob('*.jsonl'))
    else:
        files.extend(root.glob('*.jsonl'))
        files.extend(root.glob('*/events.jsonl'))
    files = [p for p in files if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def iter_all_session_files(roots):
    files = []
    for root in roots:
        files.extend(iter_session_files(root))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


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


def detect_log_format(path: Path) -> str:
    try:
        with path.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict):
                    if 'type' in obj and 'data' in obj:
                        return 'copilot_cli'
                    if 'kind' in obj and 'v' in obj:
                        return 'vscode_chat'
                break
    except Exception:
        pass
    return 'unknown'


def read_workspace_json_for_chat(path: Path) -> str:
    # workspaceStorage/<hash>/chatSessions/<id>.jsonl
    ws_json = path.parent.parent / 'workspace.json'
    if not ws_json.exists():
        return ''
    try:
        obj = json.loads(ws_json.read_text(encoding='utf-8'))
        folder = obj.get('folder', '')
        if not isinstance(folder, str):
            return ''
        if folder.startswith('file:///'):
            raw = urllib.parse.unquote(folder[len('file:///'):])
            return raw.replace('/', '\\')
        if folder.startswith('file://'):
            return urllib.parse.unquote(folder[len('file://'):])
        return folder
    except Exception:
        return ''


def read_workspace_json_for_storage_dir(storage_dir: Path) -> str:
    ws_json = storage_dir / 'workspace.json'
    if not ws_json.exists():
        return ''
    try:
        obj = json.loads(ws_json.read_text(encoding='utf-8'))
        folder = obj.get('folder', '')
        if not isinstance(folder, str):
            return ''
        if folder.startswith('file:///'):
            raw = urllib.parse.unquote(folder[len('file:///'):])
            return raw.replace('/', '\\')
        if folder.startswith('file://'):
            return urllib.parse.unquote(folder[len('file://'):])
        return folder
    except Exception:
        return ''


def _iso_from_epoch_ms(epoch_ms):
    if not isinstance(epoch_ms, (int, float)):
        return ''
    try:
        return datetime.fromtimestamp(epoch_ms / 1000).isoformat()
    except Exception:
        return ''


def load_vscode_cloud_sessions(roots):
    sessions = []
    seen = set()
    ws_roots = [r for r in roots if 'workspacestorage' in str(r).lower()]

    for ws_root in ws_roots:
        if not _path_exists_safe(ws_root):
            continue
        try:
            storage_dirs = list(ws_root.iterdir())
        except Exception:
            continue

        for storage_dir in storage_dirs:
            try:
                if not storage_dir.is_dir():
                    continue
            except Exception:
                continue

            db_path = storage_dir / 'state.vscdb'
            if not db_path.exists():
                continue

            cwd = read_workspace_json_for_storage_dir(storage_dir)
            try:
                con = sqlite3.connect(str(db_path))
                cur = con.cursor()
                cur.execute("SELECT value FROM ItemTable WHERE key='chat.ChatSessionStore.index'")
                row = cur.fetchone()
            except Exception:
                row = None
            finally:
                try:
                    con.close()
                except Exception:
                    pass

            if not row:
                continue

            raw = row[0]
            if isinstance(raw, bytes):
                try:
                    raw = raw.decode('utf-8', errors='replace')
                except Exception:
                    raw = ''
            if not isinstance(raw, str) or not raw:
                continue

            try:
                index_obj = json.loads(raw)
            except Exception:
                continue
            entries = index_obj.get('entries', {})
            if not isinstance(entries, dict):
                continue

            for resource, entry in entries.items():
                if not isinstance(resource, str) or not isinstance(entry, dict):
                    continue
                if not resource.startswith('copilot-cloud-agent:/'):
                    continue

                session_id = entry.get('sessionId', '') if isinstance(entry.get('sessionId', ''), str) else ''
                key = (str(storage_dir), resource, session_id)
                if key in seen:
                    continue
                seen.add(key)

                title = entry.get('title', '') if isinstance(entry.get('title', ''), str) else ''
                timing = entry.get('timing', {}) if isinstance(entry.get('timing', {}), dict) else {}
                created_ms = timing.get('created')
                last_message_ms = entry.get('lastMessageDate')
                started_at = _iso_from_epoch_ms(created_ms)
                mtime = _iso_from_epoch_ms(last_message_ms) or started_at or datetime.fromtimestamp(db_path.stat().st_mtime).isoformat()
                rel = f"{storage_dir.name}/cloud/{resource}"

                sessions.append({
                    'id': session_id or resource,
                    'path': f"vscode-cloud://{storage_dir.name}/{urllib.parse.quote(resource, safe='/:')}",
                    'relative_path': rel,
                    'source': 'cloud',
                    'mtime': mtime,
                    'session_id': session_id or resource,
                    'started_at': started_at,
                    'cwd': cwd,
                    'model': '',
                    'summary': title,
                    'first_user_text': title or resource,
                    'search_text': f"{title} {resource}".strip(),
                })
    return sessions


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


def summarize_copilot_cli_session(path: Path):
    summary = {
        'id': derive_session_id(path),
        'path': str(path),
        'relative_path': str(path),
        'source': 'cli',
        'mtime': datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        'session_id': '',
        'started_at': '',
        'cwd': '',
        'model': '',
        'summary': '',
        'first_user_text': '',
        'search_text': '',
    }

    summary['relative_path'] = str(path)
    for root in get_session_roots():
        try:
            summary['relative_path'] = str(path.relative_to(root))
            break
        except Exception:
            continue

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


def summarize_vscode_chat_session(path: Path):
    summary = {
        'id': derive_session_id(path),
        'path': str(path),
        'relative_path': str(path),
        'source': 'vscode',
        'mtime': datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        'session_id': derive_session_id(path),
        'started_at': '',
        'cwd': read_workspace_json_for_chat(path),
        'model': '',
        'summary': '',
        'first_user_text': '',
        'search_text': '',
    }
    for root in get_session_roots():
        try:
            summary['relative_path'] = str(path.relative_to(root))
            break
        except Exception:
            continue

    search_chunks = []
    search_len = 0
    search_limit = 2500

    try:
        with path.open('r', encoding='utf-8') as f:
            for line in f:
                obj = json.loads(line)
                kind = obj.get('kind')
                key_path = obj.get('k', [])
                val = obj.get('v')

                if kind == 0 and isinstance(val, dict):
                    summary['session_id'] = val.get('sessionId', summary['session_id'])
                    created = val.get('creationDate')
                    if isinstance(created, (int, float)):
                        summary['started_at'] = datetime.fromtimestamp(created / 1000).isoformat()
                    model = val.get('inputState', {}).get('selectedModel', {}).get('identifier', '')
                    if isinstance(model, str):
                        summary['model'] = model
                elif kind == 1 and key_path == ['customTitle'] and isinstance(val, str):
                    summary['summary'] = val
                elif kind == 2 and key_path == ['requests'] and isinstance(val, list):
                    for req in val:
                        if not isinstance(req, dict):
                            continue
                        msg = req.get('message', {})
                        text = msg.get('text', '') if isinstance(msg, dict) else ''
                        if text and not summary['first_user_text']:
                            summary['first_user_text'] = text.replace('\n', ' ')[:180]
                        if text and search_len < search_limit:
                            cut = text.replace('\n', ' ')[:300]
                            search_chunks.append(cut)
                            search_len += len(cut)

                        resp = req.get('response', [])
                        if isinstance(resp, list):
                            for part in resp:
                                if not isinstance(part, dict):
                                    continue
                                val_text = part.get('value', '')
                                if val_text and search_len < search_limit:
                                    cut = str(val_text).replace('\n', ' ')[:300]
                                    search_chunks.append(cut)
                                    search_len += len(cut)
                elif kind == 2 and len(key_path) >= 3 and key_path[0] == 'requests' and key_path[2] == 'response':
                    if isinstance(val, list):
                        for part in val:
                            if not isinstance(part, dict):
                                continue
                            val_text = part.get('value', '')
                            if val_text and search_len < search_limit:
                                cut = str(val_text).replace('\n', ' ')[:300]
                                search_chunks.append(cut)
                                search_len += len(cut)
    except Exception:
        pass

    summary['search_text'] = ' '.join(search_chunks)
    return summary


def summarize_session(path: Path):
    fmt = detect_log_format(path)
    if fmt == 'vscode_chat':
        return summarize_vscode_chat_session(path)
    return summarize_copilot_cli_session(path)


def load_copilot_cli_events(path: Path):
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


def load_vscode_chat_events(path: Path):
    events = []
    raw_count = 0
    requests = []

    try:
        with path.open('r', encoding='utf-8') as f:
            for line in f:
                raw_count += 1
                obj = json.loads(line)
                kind = obj.get('kind')
                key_path = obj.get('k', [])
                val = obj.get('v')

                if kind == 2 and key_path == ['requests'] and isinstance(val, list):
                    for req in val:
                        if isinstance(req, dict):
                            req['_responses'] = []
                            requests.append(req)
                elif kind == 2 and len(key_path) >= 3 and key_path[0] == 'requests' and key_path[2] == 'response':
                    idx = key_path[1]
                    if isinstance(idx, int) and 0 <= idx < len(requests) and isinstance(val, list):
                        chunks = []
                        for part in val:
                            if isinstance(part, dict):
                                text = part.get('value')
                                if isinstance(text, str) and text:
                                    chunks.append(text)
                        merged = ''.join(chunks).strip()
                        if merged:
                            requests[idx]['_responses'].append(merged)
                elif kind == 1 and len(key_path) >= 3 and key_path[0] == 'requests':
                    idx = key_path[1]
                    if isinstance(idx, int) and 0 <= idx < len(requests):
                        if key_path[2] == 'result' and isinstance(val, dict):
                            requests[idx]['_result'] = val
    except Exception:
        return {'events': events, 'raw_line_count': raw_count}

    for req in requests:
        if len(events) >= MAX_EVENTS:
            break

        ts = _iso_from_epoch_ms(req.get('timestamp'))
        msg = req.get('message', {})
        user_text = msg.get('text', '') if isinstance(msg, dict) else ''
        if user_text:
            events.append({'timestamp': ts, 'kind': 'message', 'role': 'user', 'text': user_text})

        assistant_text = ''
        result = req.get('_result', {})
        rounds = result.get('metadata', {}).get('toolCallRounds', [])
        if isinstance(rounds, list) and rounds:
            last_round = rounds[-1]
            if isinstance(last_round, dict):
                candidate = last_round.get('response', '')
                if isinstance(candidate, str) and candidate.strip():
                    assistant_text = candidate

        if not assistant_text and req.get('_responses'):
            # Stream updates can include partial chunks; keep the most complete text.
            assistant_text = max(req['_responses'], key=lambda t: len(t or ''))

        if assistant_text and len(events) < MAX_EVENTS:
            events.append({'timestamp': ts, 'kind': 'message', 'role': 'assistant', 'text': assistant_text})

    return {'events': events, 'raw_line_count': raw_count}


def load_session_events(path: Path):
    fmt = detect_log_format(path)
    if fmt == 'vscode_chat':
        return load_vscode_chat_events(path)
    return load_copilot_cli_events(path)


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
  font-size: 13px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-path .ts {
  color: #0b4a52;
  font-weight: 600;
  background: #dff5f8;
  border-radius: 4px;
  padding: 0 4px;
}
.session-preview {
  margin-top: 4px;
  font-size: 12px;
  color: #34414f;
}
.session-meta-row {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.badge {
  display: inline-block;
  font-size: 11px;
  border-radius: 6px;
  padding: 2px 6px;
  border: 1px solid #c7d8ea;
  background: #f2f8ff;
}
.session-cwd {
  font-size: 12px;
  color: #0b5f3d;
  font-weight: 700;
  background: #e8f7ef;
  border-color: #bfe8cf;
}
.session-time {
  font-size: 12px;
  color: #6b4300;
  font-weight: 700;
  background: #fff3de;
  border-color: #f0d3a1;
  font-variant-numeric: tabular-nums;
}
.session-id {
  color: #334155;
  background: #eef2f7;
  border-color: #d4dde8;
}
.session-source {
  color: #0b3a67;
  background: #e6f1ff;
  border-color: #bdd9f7;
  font-weight: 700;
}
.session-source.source-vscode {
  color: #0f5a5a;
  background: #e5f7f7;
  border-color: #bfe8e8;
}
.session-source.source-cloud {
  color: #5a3b00;
  background: #fff4df;
  border-color: #f2d9ab;
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
code.cwd-code {
  color: #0b5f3d;
  background: #e8f7ef;
  border: 1px solid #bfe8cf;
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 700;
}
code.time-code {
  color: #6b4300;
  background: #fff3de;
  border: 1px solid #f0d3a1;
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
code.source-code {
  color: #0b3a67;
  background: #e6f1ff;
  border: 1px solid #bdd9f7;
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 700;
}
code.source-code.source-vscode {
  color: #0f5a5a;
  background: #e5f7f7;
  border-color: #bfe8e8;
}
code.source-code.source-cloud {
  color: #5a3b00;
  background: #fff4df;
  border-color: #f2d9ab;
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
.ev.user { border-left-color: var(--user); background: #eaf3ff; border-color: #bad4ff; }
.ev.assistant { border-left-color: var(--assistant); background: #e8f8ef; border-color: #b8e7ca; }
.ev.system { border-left-color: var(--system); background: #f1f4f8; border-color: #d4dee8; }
.ev-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 8px;
}
.ev-kind,
.ev-role,
.ev-time {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 2px 8px;
  border: 1px solid transparent;
  font-weight: 700;
}
.ev-kind {
  color: #334155;
  background: #edf2f7;
  border-color: #d4dde8;
}
.ev-time {
  color: #5a6673;
  background: #f6f8fb;
  border-color: #dce4ee;
  font-variant-numeric: tabular-nums;
}
.ev-role.user {
  color: #0f4fbe;
  background: #dbeafe;
  border-color: #b6d3ff;
}
.ev-role.assistant {
  color: #0b6a41;
  background: #d8f4e3;
  border-color: #a8debe;
}
.ev-role.system {
  color: #44505d;
  background: #e8edf3;
  border-color: #ccd8e4;
}
pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.5;
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  background: rgba(255, 255, 255, 0.65);
  border: 1px solid rgba(148, 163, 184, 0.32);
  border-radius: 8px;
  padding: 10px 12px;
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
      <select id=\"source_filter\">
        <option value=\"all\">source: all</option>
        <option value=\"cli\">source: CLI</option>
        <option value=\"vscode\">source: VS Code</option>
        <option value=\"cloud\">source: Cloud</option>
      </select>
      <button id=\"reload\">Reload</button>
    </div>
    <div id=\"sessions\"></div>
  </aside>
  <main class=\"right\">
    <div class=\"meta\" id=\"meta\">セッションを選択してください</div>
    <div class=\"detail-toolbar\">
      <label><input type=\"checkbox\" id=\"only_user_instruction\" /> ユーザー指示のみ表示</label>
      <label><input type=\"checkbox\" id=\"only_ai_response\" /> AIレスポンスのみ表示</label>
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

function highlightSessionPath(s){
  const safe = esc(s);
  return safe.replace(/(\\d{4}-\\d{2}-\\d{2}T\\d{2}[-:]\\d{2}[-:]\\d{2}(?:[-:]\\d{3,6})?)/g, '<span class="ts">$1</span>');
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
  const sourceFilter = document.getElementById('source_filter').value;
  const terms = q.split(new RegExp('\\\\s+')).filter(Boolean);

  state.filtered = state.sessions.filter(s => {
    const hasPreview = !!(s.first_user_text || '').trim();
    if(!hasPreview){
      return false;
    }

    const cwdMatched = !cwdQ || (s.cwd || '').toLowerCase().includes(cwdQ);
    const source = (s.source || 'cli').toLowerCase();
    const sourceMatched = sourceFilter === 'all' || sourceFilter === source;

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

    return cwdMatched && sourceMatched && dateMatched && keywordMatched;
  });

  renderSessionList();
}

function renderSessionList(){
  const box = document.getElementById('sessions');
  const sourceLabelByKey = { cli: 'CLI', vscode: 'VS Code', cloud: 'Cloud' };
  box.innerHTML = state.filtered.map(s => `
    <div class="session-item ${state.activePath === s.path ? 'active' : ''}" data-path="${esc(s.path)}">
      <div class="session-path">${highlightSessionPath(s.relative_path || '')}</div>
      <div class="session-preview">${esc(s.first_user_text || '')}</div>
      <div class="session-meta-row">
        <div class="badge session-time">${esc(fmt(s.started_at || s.mtime))}</div>
        <div class="badge session-source source-${esc((s.source || 'cli').toLowerCase())}">${esc(sourceLabelByKey[(s.source || 'cli').toLowerCase()] || 'CLI')}</div>
      </div>
      <div class="session-meta-row">
        <div class="badge session-cwd">${esc(s.cwd || '-')}</div>
        <div class="badge session-id">id: ${esc(s.session_id || s.id || '')}</div>
      </div>
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
  if(document.getElementById('only_ai_response').checked){
    events = events.filter(ev => ev.kind === 'message' && ev.role === 'assistant');
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
  const source = (state.activeSession.source || 'cli').toLowerCase();
  const sourceLabel = source === 'vscode' ? 'VS Code' : source === 'cloud' ? 'Cloud' : 'CLI';
  meta.innerHTML = `path: <code class="path-code">${highlightSessionPath(state.activeSession.relative_path || '')}</code> | cwd: <code class="cwd-code">${esc(state.activeSession.cwd || '-')}</code> | time: <code class="time-code">${esc(fmt(state.activeSession.started_at || state.activeSession.mtime))}</code> | source: <code class="source-code source-${esc(source)}">${esc(sourceLabel)}</code> | events: ${displayEvents.length}/${state.activeEvents.length} | raw lines: ${state.activeRawLineCount}`;

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
    const roleLabel = role === 'assistant' ? 'assistant' : role === 'user' ? 'user' : 'system';
    return `<div class="ev ${esc(role)}"><div class="ev-head"><span class="ev-kind">${esc(ev.kind)}</span><span class="ev-role ${esc(roleLabel)}">${esc(roleLabel)}</span><span class="ev-time">${esc(fmt(ev.timestamp))}</span></div>${body}</div>`;
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
document.getElementById('source_filter').addEventListener('change', applyFilter);
document.getElementById('reload').addEventListener('click', loadSessions);
document.getElementById('only_user_instruction').addEventListener('change', renderActiveSession);
document.getElementById('only_ai_response').addEventListener('change', renderActiveSession);
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
            roots = get_session_roots()
            files = iter_all_session_files(roots)[:MAX_LIST]
            sessions = [summarize_session(p) for p in files]
            sessions.extend(load_vscode_cloud_sessions(roots))
            sessions.sort(key=lambda s: s.get('mtime', ''), reverse=True)
            sessions = sessions[:MAX_LIST]
            self._send_json({'root': ' | '.join(str(x) for x in roots), 'sessions': sessions})
            return

        if parsed.path == '/api/session':
            q = urllib.parse.parse_qs(parsed.query)
            raw_path = q.get('path', [''])[0]
            if not raw_path:
                self._send_json({'error': 'path is required'}, 400)
                return

            if raw_path.startswith('vscode-cloud://'):
                roots = get_session_roots()
                session = next((s for s in load_vscode_cloud_sessions(roots) if s.get('path') == raw_path), None)
                if not session:
                    self._send_json({'error': 'cloud session not found'}, 404)
                    return
                events = [{
                    'timestamp': session.get('mtime', ''),
                    'kind': 'info',
                    'role': 'system',
                    'text': 'This is a cloud-indexed VS Code session. Full message transcript is not available from local jsonl logs.',
                }]
                self._send_json({'events': events, 'raw_line_count': 0, 'session': session})
                return

            roots = [x.resolve() for x in get_session_roots()]
            p = Path(raw_path).expanduser().resolve()
            allowed = False
            for root in roots:
                try:
                    p.relative_to(root)
                    allowed = True
                    break
                except Exception:
                    continue
            if not allowed:
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
    roots = get_session_roots()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'Viewer: http://{HOST}:{PORT}', flush=True)
    for root in roots:
        print(f'Sessions dir: {root}', flush=True)
    if not any(root.exists() for root in roots):
        print('WARNING: sessions dirs do not exist. Set SESSIONS_DIR or COPILOT_SESSIONS_DIR.', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
