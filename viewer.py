#!/usr/bin/env python3
import functools
import json
import locale
import os
import re
import sqlite3
import subprocess
import threading
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.getenv("HOST", "127.0.0.1")
PORT = 8766
MAX_LIST = 300
MAX_EVENTS = 3000
SEARCH_TEXT_LIMIT = 50000
SEARCH_INDEX_TEXT_LIMIT = 0
SEARCH_INDEX_SCHEMA_VERSION = 3
SEARCH_INDEX_DB_PATH = Path(__file__).resolve().parent / ".cache" / "search_index.sqlite3"
MAX_JSON_BODY_BYTES = 1_000_000
_SESSION_CACHE = {}
_SESSION_CACHE_LOCK = threading.Lock()
_SEARCH_INDEX_LOCK = threading.Lock()

LABEL_COLOR_PRESETS = {
    "red": "#ef4444",
    "blue": "#3b82f6",
    "green": "#22c55e",
    "yellow": "#eab308",
    "purple": "#a855f7",
}
LABEL_COLOR_FAMILY_LABELS = {
    "red": "赤系",
    "blue": "青系",
    "green": "緑系",
    "yellow": "黄色系",
    "purple": "紫系",
}


def _unique_paths(paths):
    out = []
    seen = set()
    for p in paths:
        key = session_path_key(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _path_exists_safe(path: Path) -> bool:
    try:
        return path.exists()
    except Exception:
        return False


def windows_path_to_wsl(path_str: str):
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path_str)
    if not m:
        return None
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/").lstrip("/")
    return Path("/mnt") / drive / rest


def canonicalize_path(path) -> Path:
    candidate = Path(path).expanduser()
    if isinstance(path, str):
        converted = windows_path_to_wsl(path)
        if converted is not None and not _path_exists_safe(candidate):
            candidate = converted
    try:
        return candidate.resolve(strict=False)
    except TypeError:
        return candidate.resolve()
    except Exception:
        return candidate.absolute()


def session_path_key(path) -> str:
    return str(canonicalize_path(path))


def _decode_process_stdout(raw: bytes) -> str:
    if not raw:
        return ""
    encodings = (
        ["utf-16le", "utf-8", locale.getpreferredencoding(False)]
        if b"\x00" in raw
        else ["utf-8", locale.getpreferredencoding(False), "utf-16le"]
    )
    seen = set()
    for enc in encodings:
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            return raw.decode(enc).replace("\x00", "").strip()
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace").replace("\x00", "").strip()


def _run_command_capture(cmd, timeout=5):
    try:
        completed = subprocess.run(cmd, capture_output=True, check=False, timeout=timeout)
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return _decode_process_stdout(completed.stdout)


def _split_simple_list(raw: str):
    if not isinstance(raw, str):
        return []
    return [x.strip() for x in re.split(r"[;,\r\n]+", raw) if x.strip()]


def _append_vscode_user_roots(candidates, user_dir: Path):
    candidates.append(user_dir / "workspaceStorage")


def _append_wsl_home_roots(candidates, home_dir: Path):
    candidates.append(home_dir / ".copilot" / "session-state")
    _append_vscode_user_roots(candidates, home_dir / ".vscode-server" / "data" / "User")
    _append_vscode_user_roots(candidates, home_dir / ".vscode-server-insiders" / "data" / "User")


def _wsl_unc_path(distro: str, posix_path: str):
    if not distro or not isinstance(posix_path, str) or not posix_path.startswith("/"):
        return None
    suffix = posix_path.strip("/").replace("/", "\\")
    base = rf"\\wsl.localhost\{distro}"
    return Path(base if not suffix else f"{base}\\{suffix}")


@functools.lru_cache(maxsize=1)
def _get_wsl_distros_on_windows():
    if os.name != "nt":
        return []

    override = os.getenv("COPILOT_WSL_DISTROS")
    if override:
        return _split_simple_list(override)

    raw = _run_command_capture(["wsl.exe", "-l", "-q"], timeout=6)
    if not raw:
        return []
    return _split_simple_list(raw)


def _get_wsl_home_on_windows(distro: str) -> str:
    if os.name != "nt" or not distro:
        return ""
    raw = _run_command_capture(["wsl.exe", "-d", distro, "sh", "-lc", "printf '%s' \"$HOME\""], timeout=8)
    return raw if raw.startswith("/") else ""


@functools.lru_cache(maxsize=1)
def _get_wsl_session_roots_on_windows():
    distros = _get_wsl_distros_on_windows()
    if not distros:
        return []

    candidates = []
    for distro in distros:
        actual_home = _get_wsl_home_on_windows(distro)
        actual_home_root = _wsl_unc_path(distro, actual_home) if actual_home else None
        if actual_home_root:
            _append_wsl_home_roots(candidates, actual_home_root)

        home_root = _wsl_unc_path(distro, "/home")
        if home_root and _path_exists_safe(home_root):
            try:
                for d in home_root.iterdir():
                    try:
                        if d.is_dir():
                            _append_wsl_home_roots(candidates, d)
                    except Exception:
                        continue
            except Exception:
                pass

        root_home = _wsl_unc_path(distro, "/root")
        if root_home:
            _append_wsl_home_roots(candidates, root_home)

    candidates = _unique_paths(candidates)
    existing = [p for p in candidates if _path_exists_safe(p)]
    return existing if existing else candidates


@functools.lru_cache(maxsize=1)
def get_session_roots():
    raw = os.getenv("SESSIONS_DIR") or os.getenv("COPILOT_SESSIONS_DIR")
    if raw:
        parts = [x.strip() for x in raw.split(os.pathsep) if x.strip()]
        return _unique_paths([Path(x).expanduser() for x in parts])

    candidates = []
    userprofile = os.getenv("USERPROFILE")
    appdata = os.getenv("APPDATA")
    win_home = os.getenv("WIN_HOME")
    home = Path.home()

    if userprofile:
        up = Path(userprofile)
        candidates.append(up / ".copilot" / "session-state")
        if not appdata:
            appdata = str(up / "AppData" / "Roaming")

    if appdata:
        code_user = Path(appdata) / "Code" / "User"
        _append_vscode_user_roots(candidates, code_user)

    _append_wsl_home_roots(candidates, home)

    if win_home:
        wh = Path(win_home)
        candidates.append(wh / ".copilot" / "session-state")

    users_root = Path("/mnt/c/Users")
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
            candidates.append(d / ".copilot" / "session-state")
            _append_vscode_user_roots(candidates, d / "AppData" / "Roaming" / "Code" / "User")

    candidates.extend(_get_wsl_session_roots_on_windows())

    candidates = _unique_paths(candidates)
    existing = [p for p in candidates if _path_exists_safe(p)]
    return existing if existing else candidates


@functools.lru_cache(maxsize=1)
def get_canonical_session_roots():
    return _unique_paths(canonicalize_path(root) for root in get_session_roots())


def get_sessions_dir() -> Path:
    roots = get_session_roots()
    return roots[0]


def iter_session_files(root: Path):
    if not _path_exists_safe(root):
        return []
    files = []
    root_l = str(root).lower()
    if "workspacestorage" in root_l:
        files.extend(root.glob("*/chatSessions/*.jsonl"))
    else:
        files.extend(root.glob("*.jsonl"))
        files.extend(root.glob("*/events.jsonl"))
    files = [p for p in files if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def iter_all_session_files(roots):
    files = []
    for root in roots:
        files.extend(iter_session_files(root))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    unique = {}
    for path in files:
        unique[session_path_key(path)] = canonicalize_path(path)
    return list(unique.values())


def extract_text(raw):
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                txt = item.get("text") or item.get("content")
                if isinstance(txt, str):
                    parts.append(txt)
        return "\n".join(parts).strip()
    return ""


def derive_session_id(path: Path) -> str:
    if path.name == "events.jsonl":
        return path.parent.name
    return path.stem


def detect_log_format(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict):
                    if "type" in obj and "data" in obj:
                        return "copilot_cli"
                    if "kind" in obj and "v" in obj:
                        return "vscode_chat"
                break
    except Exception:
        pass
    return "unknown"


def _extract_wsl_distro_from_path(path_like) -> str:
    if path_like is None:
        return ""
    m = re.match(r"^\\\\wsl(?:\.localhost)?\\([^\\]+)(?:\\|$)", str(path_like), re.IGNORECASE)
    return m.group(1) if m else ""


def _normalize_display_path(path_str: str, wsl_distro: str = "") -> str:
    if not isinstance(path_str, str):
        return ""
    s = path_str.strip()
    if not s:
        return ""

    if s.startswith("vscode-remote://"):
        parsed = urllib.parse.urlparse(s)
        if parsed.netloc.startswith("wsl+"):
            remote_distro = urllib.parse.unquote(parsed.netloc[len("wsl+") :])
            return _normalize_display_path(urllib.parse.unquote(parsed.path), remote_distro)
        return urllib.parse.unquote(parsed.path or s)

    if s.startswith("file:///"):
        return _normalize_display_path(urllib.parse.unquote(s[len("file:///") :]), wsl_distro)
    if s.startswith("file://"):
        return _normalize_display_path(urllib.parse.unquote(s[len("file://") :]), wsl_distro)

    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", s)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2).replace("/", "\\")
        return f"{drive}:\\{rest}" if rest else f"{drive}:\\"

    if s.startswith("/"):
        if wsl_distro:
            rest = s.lstrip("/").replace("/", "\\")
            base = f"\\\\wsl.localhost\\{wsl_distro}"
            return f"{base}\\{rest}" if rest else base
        return s

    if re.match(r"^[a-zA-Z]:/", s):
        return s.replace("/", "\\")
    return s


def _match_session_root(path: Path):
    canonical_path = canonicalize_path(path)
    for root in get_canonical_session_roots():
        try:
            canonical_path.relative_to(root)
            return root
        except Exception:
            continue
    return None


def read_workspace_json_for_chat(path: Path) -> str:
    ws_json = path.parent.parent / "workspace.json"
    if not ws_json.exists():
        return ""
    try:
        obj = json.loads(ws_json.read_text(encoding="utf-8"))
        folder = obj.get("folder", "")
        if not isinstance(folder, str):
            return ""
        return _normalize_display_path(folder, _extract_wsl_distro_from_path(path))
    except Exception:
        return ""


def _iso_from_epoch_ms(epoch_ms):
    if not isinstance(epoch_ms, (int, float)):
        return ""
    try:
        return datetime.fromtimestamp(epoch_ms / 1000).isoformat()
    except Exception:
        return ""


def find_workspace_yaml(path: Path):
    if path.name == "events.jsonl":
        candidate = path.parent / "workspace.yaml"
        return candidate if candidate.exists() else None

    root = _match_session_root(path)
    if root is None:
        return None
    session_id = derive_session_id(path)
    candidate = root / session_id / "workspace.yaml"
    return candidate if candidate.exists() else None


def parse_workspace_yaml(path: Path):
    data = {}
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw or raw.startswith("#") or ":" not in raw:
                    continue
                key, value = raw.split(":", 1)
                data[key.strip()] = value.strip().strip("\"'")
    except Exception:
        return {}
    return data


def to_relative_path(path: Path) -> str:
    canonical_path = canonicalize_path(path)
    root = _match_session_root(canonical_path)
    if root is None:
        return str(canonical_path)
    try:
        return str(canonical_path.relative_to(root))
    except Exception:
        return str(canonical_path)


def stringify_search_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def normalize_search_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def is_safe_css_color(value: str) -> bool:
    candidate = (value or "").strip()
    if not candidate or len(candidate) > 64:
        return False
    if not re.fullmatch(r"[#(),.%/\-\sa-zA-Z0-9]+", candidate):
        return False
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", candidate):
        return True
    lowered = candidate.lower()
    if re.fullmatch(r"rgba?\([^()]+\)", lowered):
        return True
    if re.fullmatch(r"oklch\([^()]+\)", lowered):
        return True
    return False


def normalize_label_color(color_value: str, color_family: str):
    family = (color_family or "").strip().lower()
    if family not in LABEL_COLOR_PRESETS:
        family = ""
    value = (color_value or "").strip()
    if value:
        if not is_safe_css_color(value):
            raise ValueError("色コードの形式が不正です")
        return value, family
    if family:
        return LABEL_COLOR_PRESETS[family], family
    raise ValueError("色コードを入力してください")


def parse_optional_int(raw):
    try:
        if raw is None or raw == "":
            return None
        return int(raw)
    except (TypeError, ValueError):
        return None


def parse_json_body(handler):
    length = parse_optional_int(handler.headers.get("Content-Length"))
    if not length or length < 0:
        return {}
    if length > MAX_JSON_BODY_BYTES:
        raise ValueError("request body too large")
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def append_search_chunk(chunks, text: str, current_len: int, limit: int):
    normalized = normalize_search_text(text)
    unlimited = limit <= 0
    if not normalized or (not unlimited and current_len >= limit):
        return current_len
    if not unlimited:
        remaining = limit - current_len
        if len(normalized) > remaining:
            normalized = normalized[:remaining]
    chunks.append(normalized)
    return current_len + len(normalized)


def set_cached_summary(path_key: str, signature, summary):
    with _SESSION_CACHE_LOCK:
        entry = _SESSION_CACHE.get(path_key)
        if not entry or entry.get("signature") != signature:
            entry = {"signature": signature, "summary": None, "events": None}
            _SESSION_CACHE[path_key] = entry
        entry["summary"] = summary


def get_session_signature(path: Path, stat_result=None, signature=None):
    st = stat_result if stat_result is not None else path.stat()
    sig = signature if signature is not None else (st.st_mtime_ns, st.st_size)
    return st, sig


def open_search_index_connection():
    SEARCH_INDEX_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SEARCH_INDEX_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
    current_version = parse_optional_int(row["value"]) if row is not None else 0
    if current_version is None:
        current_version = 0
    if current_version < 2:
        with conn:
            conn.execute("DROP TABLE IF EXISTS session_index")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_index (
            path TEXT PRIMARY KEY,
            id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            mtime_iso TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            cwd TEXT NOT NULL,
            model TEXT NOT NULL,
            source TEXT NOT NULL,
            first_user_text TEXT NOT NULL,
            first_real_user_text TEXT NOT NULL,
            search_text TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_index_mtime_ns ON session_index (mtime_ns DESC)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            color_value TEXT NOT NULL,
            color_family TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_label_links (
            session_path TEXT NOT NULL,
            label_id INTEGER NOT NULL,
            PRIMARY KEY (session_path, label_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS event_label_links (
            session_path TEXT NOT NULL,
            event_id TEXT NOT NULL,
            label_id INTEGER NOT NULL,
            PRIMARY KEY (session_path, event_id, label_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_label_links_label ON session_label_links (label_id, session_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_label_links_label ON event_label_links (label_id, session_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_label_links_session ON event_label_links (session_path, event_id)")
    if current_version != SEARCH_INDEX_SCHEMA_VERSION:
        with conn:
            conn.execute(
                """
                INSERT INTO app_meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("schema_version", str(SEARCH_INDEX_SCHEMA_VERSION)),
            )
    return conn


def summary_from_index_row(row):
    return {
        "id": row["id"],
        "path": row["path"],
        "relative_path": row["relative_path"],
        "mtime": row["mtime_iso"],
        "session_id": row["session_id"],
        "started_at": row["started_at"],
        "cwd": row["cwd"],
        "model": row["model"],
        "source": row["source"],
        "first_user_text": row["first_user_text"],
        "first_real_user_text": row["first_real_user_text"],
    }


def _search_prefix_from_summary(summary):
    values = [
        summary.get("relative_path", ""),
        summary.get("cwd", ""),
        summary.get("session_id", ""),
        summary.get("source", ""),
        summary.get("first_user_text", ""),
        summary.get("first_real_user_text", ""),
    ]
    out = []
    for value in values:
        normalized = normalize_search_text(value)
        if normalized:
            out.append(normalized)
    return out


def build_copilot_cli_search_record(path: Path, stat_result=None):
    path = canonicalize_path(path)
    st = stat_result if stat_result is not None else path.stat()
    matched_root = _match_session_root(path)
    summary = {
        "id": derive_session_id(path),
        "path": session_path_key(path),
        "relative_path": to_relative_path(path),
        "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
        "session_id": "",
        "started_at": "",
        "cwd": "",
        "model": "",
        "source": "cli",
        "first_user_text": "",
        "first_real_user_text": "",
    }
    search_chunks = []
    search_len = 0
    workspace_summary = ""

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                t = obj.get("type", "")
                payload = obj.get("data", {})
                if t == "session.start":
                    summary["session_id"] = payload.get("sessionId", "")
                    summary["started_at"] = payload.get("startTime", "")
                    summary["model"] = payload.get("copilotVersion", "")
                    ctx = payload.get("context", {})
                    if isinstance(ctx, dict):
                        summary["cwd"] = ctx.get("cwd", "")
                elif t == "user.message":
                    text = extract_text(payload.get("content", "")) or extract_text(payload.get("transformedContent", ""))
                    if text:
                        if not summary["first_user_text"]:
                            summary["first_user_text"] = text.replace("\n", " ")[:180]
                        if not summary["first_real_user_text"]:
                            summary["first_real_user_text"] = summary["first_user_text"]
                        search_len = append_search_chunk(search_chunks, text, search_len, SEARCH_INDEX_TEXT_LIMIT)
                elif t == "assistant.message":
                    text = extract_text(payload.get("content", ""))
                    if text:
                        search_len = append_search_chunk(search_chunks, text, search_len, SEARCH_INDEX_TEXT_LIMIT)
                    tool_requests = payload.get("toolRequests", [])
                    if isinstance(tool_requests, list):
                        for req in tool_requests:
                            if not isinstance(req, dict):
                                continue
                            tool_text = "\n".join(
                                part
                                for part in (
                                    stringify_search_value(req.get("name", "")),
                                    stringify_search_value(req.get("arguments", {})),
                                )
                                if part
                            )
                            search_len = append_search_chunk(search_chunks, tool_text, search_len, SEARCH_INDEX_TEXT_LIMIT)
                elif t == "tool.execution_start":
                    tool_text = "\n".join(
                        part
                        for part in (
                            stringify_search_value(payload.get("toolName", "")),
                            stringify_search_value(payload.get("arguments", {})),
                        )
                        if part
                    )
                    search_len = append_search_chunk(search_chunks, tool_text, search_len, SEARCH_INDEX_TEXT_LIMIT)
                elif t == "tool.execution_complete":
                    search_len = append_search_chunk(
                        search_chunks,
                        stringify_search_value(payload.get("result", {})),
                        search_len,
                        SEARCH_INDEX_TEXT_LIMIT,
                    )
                elif t in ("session.info", "session.error"):
                    search_len = append_search_chunk(
                        search_chunks,
                        stringify_search_value(payload.get("message", "")),
                        search_len,
                        SEARCH_INDEX_TEXT_LIMIT,
                    )
    except Exception:
        pass

    workspace_yaml = find_workspace_yaml(path)
    if workspace_yaml:
        ws = parse_workspace_yaml(workspace_yaml)
        if not summary["cwd"]:
            summary["cwd"] = ws.get("cwd", "")
        if not summary["started_at"]:
            summary["started_at"] = ws.get("created_at", "")
        workspace_summary = ws.get("summary", "")

    if not summary["session_id"]:
        summary["session_id"] = summary["id"]
    if not summary["first_real_user_text"]:
        summary["first_real_user_text"] = summary["first_user_text"]

    summary["cwd"] = _normalize_display_path(summary["cwd"], _extract_wsl_distro_from_path(matched_root or path))
    if workspace_summary:
        append_search_chunk(search_chunks, workspace_summary, search_len, SEARCH_INDEX_TEXT_LIMIT)
    search_text = " ".join(_search_prefix_from_summary(summary) + search_chunks)
    return summary, search_text


def build_vscode_chat_search_record(path: Path, stat_result=None):
    path = canonicalize_path(path)
    st = stat_result if stat_result is not None else path.stat()
    matched_root = _match_session_root(path)
    summary = {
        "id": derive_session_id(path),
        "path": session_path_key(path),
        "relative_path": to_relative_path(path),
        "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
        "session_id": derive_session_id(path),
        "started_at": "",
        "cwd": read_workspace_json_for_chat(path),
        "model": "",
        "source": "vscode",
        "first_user_text": "",
        "first_real_user_text": "",
    }
    search_chunks = []
    search_len = 0
    title = ""

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                kind = obj.get("kind")
                key_path = obj.get("k", [])
                val = obj.get("v")
                if kind == 0 and isinstance(val, dict):
                    summary["session_id"] = val.get("sessionId", summary["session_id"])
                    created = val.get("creationDate")
                    if isinstance(created, (int, float)):
                        summary["started_at"] = datetime.fromtimestamp(created / 1000).isoformat()
                    model = val.get("inputState", {}).get("selectedModel", {}).get("identifier", "")
                    if isinstance(model, str):
                        summary["model"] = model
                elif kind == 1 and key_path == ["customTitle"] and isinstance(val, str):
                    title = val
                    search_len = append_search_chunk(search_chunks, val, search_len, SEARCH_INDEX_TEXT_LIMIT)
                elif kind == 2 and key_path == ["requests"] and isinstance(val, list):
                    for req in val:
                        if not isinstance(req, dict):
                            continue
                        msg = req.get("message", {})
                        text = msg.get("text", "") if isinstance(msg, dict) else ""
                        if text:
                            if not summary["first_user_text"]:
                                summary["first_user_text"] = text.replace("\n", " ")[:180]
                                summary["first_real_user_text"] = summary["first_user_text"]
                            search_len = append_search_chunk(search_chunks, text, search_len, SEARCH_INDEX_TEXT_LIMIT)
                        resp = req.get("response", [])
                        if isinstance(resp, list):
                            for part in resp:
                                if not isinstance(part, dict):
                                    continue
                                value = part.get("value", "")
                                if value:
                                    search_len = append_search_chunk(search_chunks, value, search_len, SEARCH_INDEX_TEXT_LIMIT)
                elif kind == 2 and len(key_path) >= 3 and key_path[0] == "requests" and key_path[2] == "response":
                    if isinstance(val, list):
                        for part in val:
                            if not isinstance(part, dict):
                                continue
                            value = part.get("value", "")
                            if value:
                                search_len = append_search_chunk(search_chunks, value, search_len, SEARCH_INDEX_TEXT_LIMIT)
                elif kind == 1 and len(key_path) >= 3 and key_path[0] == "requests" and key_path[2] == "result":
                    if isinstance(val, dict):
                        rounds = val.get("metadata", {}).get("toolCallRounds", [])
                        if isinstance(rounds, list):
                            for round_entry in rounds:
                                if not isinstance(round_entry, dict):
                                    continue
                                response = round_entry.get("response", "")
                                if response:
                                    search_len = append_search_chunk(search_chunks, response, search_len, SEARCH_INDEX_TEXT_LIMIT)
    except Exception:
        pass

    summary["cwd"] = _normalize_display_path(summary["cwd"], _extract_wsl_distro_from_path(matched_root or path))
    if title and not summary["first_user_text"]:
        summary["first_user_text"] = title[:180]
        summary["first_real_user_text"] = summary["first_user_text"]
    search_text = " ".join(_search_prefix_from_summary(summary) + search_chunks)
    return summary, search_text


def build_file_search_index_record(path: Path, stat_result=None):
    fmt = detect_log_format(path)
    if fmt == "vscode_chat":
        return build_vscode_chat_search_record(path, stat_result=stat_result)
    return build_copilot_cli_search_record(path, stat_result=stat_result)


def sync_search_index(file_paths, prune_missing=True):
    current = {}
    for raw_path in file_paths:
        path = canonicalize_path(raw_path)
        try:
            stat_result, signature = get_session_signature(path)
        except FileNotFoundError:
            continue
        path_key = session_path_key(path)
        if path_key in current:
            continue
        current[path_key] = {
            "kind": "file",
            "path": path,
            "stat_result": stat_result,
            "signature": signature,
        }

    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            rows = conn.execute("SELECT path, mtime_ns, size FROM session_index").fetchall()
            existing = {row["path"]: (row["mtime_ns"], row["size"]) for row in rows}
        finally:
            conn.close()

    stale_paths = (
        [
            path_key
            for path_key in existing
            if path_key not in current and not _path_exists_safe(canonicalize_path(path_key))
        ]
        if prune_missing
        else []
    )
    changed = []
    for path_key, item in current.items():
        if existing.get(path_key) != item["signature"]:
            changed.append(item)

    changed_records = []
    for item in changed:
        summary, search_text = build_file_search_index_record(item["path"], stat_result=item["stat_result"])
        changed_records.append((item["path"], item["signature"], summary, search_text))

    if not stale_paths and not changed_records:
        return

    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                if stale_paths:
                    conn.executemany("DELETE FROM session_index WHERE path = ?", ((path_key,) for path_key in stale_paths))
                    conn.executemany("DELETE FROM session_label_links WHERE session_path = ?", ((path_key,) for path_key in stale_paths))
                    conn.executemany("DELETE FROM event_label_links WHERE session_path = ?", ((path_key,) for path_key in stale_paths))

                for _, signature, summary, search_text in changed_records:
                    conn.execute(
                        """
                        INSERT INTO session_index (
                            path, id, relative_path, mtime_iso, mtime_ns, size,
                            session_id, started_at, cwd, model, source,
                            first_user_text, first_real_user_text, search_text
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(path) DO UPDATE SET
                            id = excluded.id,
                            relative_path = excluded.relative_path,
                            mtime_iso = excluded.mtime_iso,
                            mtime_ns = excluded.mtime_ns,
                            size = excluded.size,
                            session_id = excluded.session_id,
                            started_at = excluded.started_at,
                            cwd = excluded.cwd,
                            model = excluded.model,
                            source = excluded.source,
                            first_user_text = excluded.first_user_text,
                            first_real_user_text = excluded.first_real_user_text,
                            search_text = excluded.search_text
                        """,
                        (
                            summary["path"],
                            summary["id"],
                            summary["relative_path"],
                            summary["mtime"],
                            signature[0],
                            signature[1],
                            summary["session_id"],
                            summary["started_at"],
                            summary["cwd"],
                            summary["model"],
                            summary["source"],
                            summary["first_user_text"],
                            summary["first_real_user_text"],
                            search_text,
                        ),
                    )
        finally:
            conn.close()

    for path, signature, summary, _ in changed_records:
        set_cached_summary(session_path_key(path), signature, summary)


def fetch_sessions_from_search_index(query: str, mode: str, limit: int, session_label_id=None, event_label_id=None):
    normalized_terms = [normalize_search_text(term) for term in query.split() if normalize_search_text(term)]
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            columns = (
                "id, path, relative_path, mtime_iso, session_id, started_at, "
                "cwd, model, source, first_user_text, first_real_user_text"
            )
            where_clauses = []
            params = []
            if normalized_terms:
                joiner = " OR " if mode == "or" else " AND "
                keyword_clause = joiner.join("instr(search_text, ?) > 0" for _ in normalized_terms)
                where_clauses.append(f"({keyword_clause})")
                params.extend(normalized_terms)
            if session_label_id is not None:
                where_clauses.append(
                    "EXISTS (SELECT 1 FROM session_label_links sl WHERE sl.session_path = session_index.path AND sl.label_id = ?)"
                )
                params.append(session_label_id)
            if event_label_id is not None:
                where_clauses.append(
                    "EXISTS (SELECT 1 FROM event_label_links el WHERE el.session_path = session_index.path AND el.label_id = ?)"
                )
                params.append(event_label_id)
            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            sql = f"SELECT {columns} FROM session_index {where_sql} ORDER BY mtime_ns DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            sessions = [summary_from_index_row(row) for row in rows]
            label_map = fetch_session_labels_map([session["path"] for session in sessions], conn)
            for session in sessions:
                session["session_labels"] = label_map.get(session["path"], [])
            return sessions
        finally:
            conn.close()


def fetch_session_summary_from_index(path_key: str):
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            row = conn.execute(
                """
                SELECT id, path, relative_path, mtime_iso, session_id, started_at,
                       cwd, model, source, first_user_text, first_real_user_text
                FROM session_index
                WHERE path = ?
                """,
                (path_key,),
            ).fetchone()
            if row is None:
                return None
            summary = summary_from_index_row(row)
            summary["session_labels"] = fetch_session_labels_map([summary["path"]], conn).get(summary["path"], [])
            return summary
        finally:
            conn.close()


def label_row_to_dict(row):
    family = row["color_family"] or ""
    return {
        "id": row["id"],
        "name": row["name"],
        "color_value": row["color_value"],
        "color_family": family,
        "color_family_label": LABEL_COLOR_FAMILY_LABELS.get(family, ""),
    }


def list_labels():
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, color_value, color_family FROM labels ORDER BY name COLLATE NOCASE ASC, id ASC"
            ).fetchall()
            return [label_row_to_dict(row) for row in rows]
        finally:
            conn.close()


def save_label(label_id, name: str, color_value: str, color_family: str):
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("ラベル名を入力してください")
    if len(clean_name) > 60:
        raise ValueError("ラベル名が長すぎます")
    normalized_color, normalized_family = normalize_label_color(color_value, color_family)
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                if label_id is None:
                    cur = conn.execute(
                        "INSERT INTO labels (name, color_value, color_family) VALUES (?, ?, ?)",
                        (clean_name, normalized_color, normalized_family),
                    )
                    saved_id = cur.lastrowid
                else:
                    conn.execute(
                        "UPDATE labels SET name = ?, color_value = ?, color_family = ? WHERE id = ?",
                        (clean_name, normalized_color, normalized_family, label_id),
                    )
                    saved_id = label_id
                row = conn.execute(
                    "SELECT id, name, color_value, color_family FROM labels WHERE id = ?",
                    (saved_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("ラベルが見つかりません")
                return label_row_to_dict(row)
        except sqlite3.IntegrityError:
            raise ValueError("同名のラベルは既に存在します")
        finally:
            conn.close()


def delete_label(label_id):
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                conn.execute("DELETE FROM session_label_links WHERE label_id = ?", (label_id,))
                conn.execute("DELETE FROM event_label_links WHERE label_id = ?", (label_id,))
                conn.execute("DELETE FROM labels WHERE id = ?", (label_id,))
        finally:
            conn.close()


def fetch_session_labels_map(paths, conn):
    unique_paths = [session_path_key(path) for path in paths if path]
    if not unique_paths:
        return {}
    placeholders = ", ".join("?" for _ in unique_paths)
    rows = conn.execute(
        f"""
        SELECT sl.session_path, l.id, l.name, l.color_value, l.color_family
        FROM session_label_links sl
        JOIN labels l ON l.id = sl.label_id
        WHERE sl.session_path IN ({placeholders})
        ORDER BY l.name COLLATE NOCASE ASC, l.id ASC
        """,
        unique_paths,
    ).fetchall()
    mapping = {path: [] for path in unique_paths}
    for row in rows:
        mapping.setdefault(row["session_path"], []).append(label_row_to_dict(row))
    return mapping


def fetch_event_labels_map(session_path, conn):
    rows = conn.execute(
        """
        SELECT el.event_id, l.id, l.name, l.color_value, l.color_family
        FROM event_label_links el
        JOIN labels l ON l.id = el.label_id
        WHERE el.session_path = ?
        ORDER BY l.name COLLATE NOCASE ASC, l.id ASC
        """,
        (session_path_key(session_path),),
    ).fetchall()
    mapping = {}
    for row in rows:
        mapping.setdefault(row["event_id"], []).append(label_row_to_dict(row))
    return mapping


def assign_session_label(session_path, label_id: int):
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO session_label_links (session_path, label_id)
                    SELECT ?, id FROM labels WHERE id = ?
                    """,
                    (session_path_key(session_path), label_id),
                )
        finally:
            conn.close()


def remove_session_label(session_path, label_id: int):
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM session_label_links WHERE session_path = ? AND label_id = ?",
                    (session_path_key(session_path), label_id),
                )
        finally:
            conn.close()


def assign_event_label(session_path, event_id: str, label_id: int):
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO event_label_links (session_path, event_id, label_id)
                    SELECT ?, ?, id FROM labels WHERE id = ?
                    """,
                    (session_path_key(session_path), event_id, label_id),
                )
        finally:
            conn.close()


def remove_event_label(session_path, event_id: str, label_id: int):
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM event_label_links WHERE session_path = ? AND event_id = ? AND label_id = ?",
                    (session_path_key(session_path), event_id, label_id),
                )
        finally:
            conn.close()


def resolve_session_path(raw_path: str):
    if not raw_path:
        raise ValueError("path is required")
    p = canonicalize_path(raw_path)
    allowed = False
    for root in get_canonical_session_roots():
        try:
            p.relative_to(root)
            allowed = True
            break
        except Exception:
            continue
    if not allowed:
        raise ValueError("path is outside sessions dir")
    if not _path_exists_safe(p) or not p.is_file():
        raise ValueError("session file not found")
    return p


def summarize_session(path: Path, stat_result=None, signature=None):
    st, sig = get_session_signature(path, stat_result, signature)
    key = session_path_key(path)
    with _SESSION_CACHE_LOCK:
        entry = _SESSION_CACHE.get(key)
        if entry and entry.get("signature") == sig and entry.get("summary") is not None:
            return entry["summary"]
    summary, _ = build_file_search_index_record(path, stat_result=st)
    set_cached_summary(key, sig, summary)
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            summary["session_labels"] = fetch_session_labels_map([summary["path"]], conn).get(summary["path"], [])
        finally:
            conn.close()
    return summary


def load_copilot_cli_events(path: Path):
    events = []
    raw_count = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw_count += 1
            try:
                obj = json.loads(line)
            except Exception:
                continue
            t = obj.get("type", "")
            ts = obj.get("timestamp", "")
            data = obj.get("data", {})
            if t == "user.message":
                text = extract_text(data.get("content", "")) or extract_text(data.get("transformedContent", ""))
                if text:
                    events.append(
                        {
                            "event_id": f"line-{raw_count}",
                            "timestamp": ts,
                            "kind": "message",
                            "role": "user",
                            "text": text,
                        }
                    )
            elif t == "assistant.message":
                text = extract_text(data.get("content", ""))
                if text:
                    events.append(
                        {
                            "event_id": f"line-{raw_count}",
                            "timestamp": ts,
                            "kind": "message",
                            "role": "assistant",
                            "text": text,
                        }
                    )
                tool_requests = data.get("toolRequests", [])
                if isinstance(tool_requests, list):
                    for idx, req in enumerate(tool_requests):
                        if not isinstance(req, dict):
                            continue
                        events.append(
                            {
                                "event_id": f"line-{raw_count}-tool-{idx}",
                                "timestamp": ts,
                                "kind": "function_call",
                                "role": "assistant",
                                "name": req.get("name", ""),
                                "arguments": json.dumps(req.get("arguments", {}), ensure_ascii=False, indent=2),
                            }
                        )
            elif t == "tool.execution_start":
                events.append(
                    {
                        "event_id": f"line-{raw_count}",
                        "timestamp": ts,
                        "kind": "tool_start",
                        "role": "system",
                        "name": data.get("toolName", ""),
                        "arguments": json.dumps(data.get("arguments", {}), ensure_ascii=False, indent=2),
                    }
                )
            elif t == "tool.execution_complete":
                result = data.get("result", {})
                text = json.dumps(result, ensure_ascii=False, indent=2) if result else ""
                events.append(
                    {
                        "event_id": f"line-{raw_count}",
                        "timestamp": ts,
                        "kind": "tool_output",
                        "role": "system",
                        "success": data.get("success", None),
                        "text": text,
                    }
                )
            elif t == "session.info":
                events.append(
                    {
                        "event_id": f"line-{raw_count}",
                        "timestamp": ts,
                        "kind": "info",
                        "role": "system",
                        "text": data.get("message", ""),
                    }
                )
            elif t == "session.error":
                events.append(
                    {
                        "event_id": f"line-{raw_count}",
                        "timestamp": ts,
                        "kind": "error",
                        "role": "system",
                        "text": data.get("message", ""),
                    }
                )
            elif t.startswith("assistant.turn_"):
                events.append(
                    {
                        "event_id": f"line-{raw_count}",
                        "timestamp": ts,
                        "kind": t,
                        "role": "system",
                        "text": "",
                    }
                )
            if len(events) >= MAX_EVENTS:
                break
    return {"events": events, "raw_line_count": raw_count}


def load_vscode_chat_events(path: Path):
    events = []
    raw_count = 0
    requests = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                raw_count += 1
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                kind = obj.get("kind")
                key_path = obj.get("k", [])
                val = obj.get("v")
                if kind == 2 and key_path == ["requests"] and isinstance(val, list):
                    requests = []
                    for req in val:
                        if isinstance(req, dict):
                            req["_responses"] = []
                            requests.append(req)
                elif kind == 2 and len(key_path) >= 3 and key_path[0] == "requests" and key_path[2] == "response":
                    idx = key_path[1]
                    if isinstance(idx, int) and 0 <= idx < len(requests) and isinstance(val, list):
                        chunks = []
                        for part in val:
                            if isinstance(part, dict):
                                text = part.get("value")
                                if isinstance(text, str) and text:
                                    chunks.append(text)
                        merged = "".join(chunks).strip()
                        if merged:
                            requests[idx]["_responses"].append(merged)
                elif kind == 1 and len(key_path) >= 3 and key_path[0] == "requests":
                    idx = key_path[1]
                    if isinstance(idx, int) and 0 <= idx < len(requests):
                        if key_path[2] == "result" and isinstance(val, dict):
                            requests[idx]["_result"] = val
    except Exception:
        return {"events": events, "raw_line_count": raw_count}

    for idx, req in enumerate(requests):
        if len(events) >= MAX_EVENTS:
            break
        ts = _iso_from_epoch_ms(req.get("timestamp"))
        msg = req.get("message", {})
        user_text = msg.get("text", "") if isinstance(msg, dict) else ""
        if user_text:
            events.append(
                {
                    "event_id": f"req-{idx}-user",
                    "timestamp": ts,
                    "kind": "message",
                    "role": "user",
                    "text": user_text,
                }
            )
        assistant_text = ""
        result = req.get("_result", {})
        rounds = result.get("metadata", {}).get("toolCallRounds", [])
        if isinstance(rounds, list) and rounds:
            last_round = rounds[-1]
            if isinstance(last_round, dict):
                candidate = last_round.get("response", "")
                if isinstance(candidate, str) and candidate.strip():
                    assistant_text = candidate
        if not assistant_text and req.get("_responses"):
            assistant_text = max(req["_responses"], key=lambda text: len(text or ""))
        if assistant_text and len(events) < MAX_EVENTS:
            events.append(
                {
                    "event_id": f"req-{idx}-assistant",
                    "timestamp": ts,
                    "kind": "message",
                    "role": "assistant",
                    "text": assistant_text,
                }
            )
    return {"events": events, "raw_line_count": raw_count}


def build_session_events(path: Path):
    fmt = detect_log_format(path)
    if fmt == "vscode_chat":
        return load_vscode_chat_events(path)
    return load_copilot_cli_events(path)


def load_session_events(path: Path, stat_result=None, signature=None):
    _, sig = get_session_signature(path, stat_result, signature)
    key = session_path_key(path)
    with _SESSION_CACHE_LOCK:
        entry = _SESSION_CACHE.get(key)
        if entry and entry.get("signature") == sig and entry.get("events") is not None:
            data = entry["events"]
        else:
            data = None
    if data is None:
        data = build_session_events(path)
        with _SESSION_CACHE_LOCK:
            entry = _SESSION_CACHE.get(key)
            if not entry or entry.get("signature") != sig:
                entry = {"signature": sig, "summary": None, "events": None}
                _SESSION_CACHE[key] = entry
            entry["events"] = data
    with _SEARCH_INDEX_LOCK:
        conn = open_search_index_connection()
        try:
            label_map = fetch_event_labels_map(path, conn)
        finally:
            conn.close()
    decorated = []
    for event in data["events"]:
        cloned = dict(event)
        cloned["labels"] = label_map.get(event.get("event_id", ""), [])
        decorated.append(cloned)
    return {"events": decorated, "raw_line_count": data["raw_line_count"]}


HTML_PAGE = """<!doctype html>
<html lang=\"ja\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>GitHub Copilot Sessions Viewer</title>
<style>
:root {
  --bg: #f2f6fb;
  --panel: #ffffff;
  --line: #ccd8e4;
  --text: #18232f;
  --muted: #57697c;
  --accent: #0d6d77;
  --user: #1b5fd6;
  --assistant: #0f7c4f;
  --dev: #8a5a00;
  --system: #4b5563;
  --sidebar-width: 360px;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  font-family: "Segoe UI", "Yu Gothic UI", sans-serif;
  background: radial-gradient(circle at top right, #e6f4ff 0%, var(--bg) 45%);
  color: var(--text);
  overflow: hidden;
}
header {
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
  background: rgba(255,255,255,0.9);
  backdrop-filter: blur(4px);
}
header h1 { margin: 0; font-size: 18px; }
header small { color: var(--muted); }
.header-bar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
#toggle_session_list_mobile {
  display: none;
}
.container {
  position: relative;
  height: calc(100vh - 64px);
  overflow: hidden;
}
.left {
  position: absolute;
  top: 0;
  left: 0;
  bottom: 0;
  width: var(--sidebar-width);
  background: #f9fcff;
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
  transition: transform 0.16s ease, opacity 0.12s ease;
  will-change: transform;
}
.right {
  height: 100%;
  margin-left: var(--sidebar-width);
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.toolbar {
  padding: 10px;
  border-bottom: 1px solid var(--line);
  display: grid;
  gap: 8px;
}
.toolbar-fields,
.toolbar-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}
.toolbar-actions {
  justify-content: flex-start;
}
.toolbar.collapsed {
  grid-template-columns: 1fr;
}
.toolbar.collapsed .toolbar-fields,
.toolbar.collapsed #clear {
  display: none;
}
input, select, button {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
}
#cwd_q, #q { flex: 1 1 220px; }
#date_from, #date_to { flex: 1 1 185px; }
#mode { flex: 0 0 auto; }
button {
  --button-shadow: rgba(13, 109, 119, 0.12);
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  white-space: nowrap;
  box-shadow: 0 4px 12px var(--button-shadow);
  transition: transform 0.18s ease, box-shadow 0.18s ease, filter 0.18s ease, opacity 0.18s ease;
}
button:hover:not(:disabled):not(.label-remove-button) {
  transform: translateY(-1px);
  box-shadow: 0 8px 18px var(--button-shadow);
  filter: saturate(1.03);
}
button:active:not(:disabled):not(.label-remove-button) {
  transform: translateY(0);
  box-shadow: 0 3px 10px var(--button-shadow);
}
button:disabled {
  box-shadow: none;
  transform: none;
  filter: none;
}
#reload {
  background: #0f766e;
  --button-shadow: rgba(15, 118, 110, 0.16);
}
#reload:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
#clear {
  --button-shadow: rgba(71, 85, 105, 0.08);
  background: #f8fafc;
  color: #475569;
  border-color: #94a3b8;
}
#clear:hover {
  background: #eef2f7;
}
.secondary-button {
  --button-shadow: rgba(53, 92, 125, 0.14);
  background: #355c7d;
}
.content-shell,
.events-shell {
  position: relative;
  flex: 1;
  min-height: 0;
}
#sessions {
  overflow: auto;
  height: 100%;
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
.session-cwd {
  color: #0b5f3d;
  font-weight: 700;
  background: #e8f7ef;
  border: 1px solid #bfe8cf;
  border-radius: 6px;
  padding: 2px 6px;
  display: inline-block;
  max-width: 100%;
}
.session-time {
  color: #6b4300;
  font-weight: 700;
  background: #fff3de;
  border: 1px solid #f0d3a1;
  border-radius: 6px;
  padding: 2px 6px;
  display: inline-block;
  max-width: 100%;
  font-variant-numeric: tabular-nums;
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
.session-badge {
  display: inline-block;
  font-size: 11px;
  border-radius: 6px;
  padding: 2px 6px;
  border: 1px solid #c7d8ea;
  background: #f2f8ff;
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
.session-source.source-cli {
  color: #0b3a67;
  background: #e6f1ff;
  border-color: #bdd9f7;
}
.session-label-row {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
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
.meta code.path-code {
  color: #0b4a52;
  background: #e5f4f6;
  border: 1px solid #b8dee3;
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 700;
}
.meta code.cwd-code {
  color: #0b5f3d;
  background: #e8f7ef;
  border: 1px solid #bfe8cf;
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 700;
}
.meta .ts {
  color: #6b4300;
  font-weight: 700;
  background: #fff3de;
  border-radius: 4px;
  padding: 0 4px;
}
.meta code.time-code {
  color: #6b4300;
  background: #fff3de;
  border: 1px solid #f0d3a1;
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 700;
}
.meta code.source-code {
  border: 1px solid #bdd9f7;
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 700;
}
.meta code.source-code.source-vscode {
  color: #0f5a5a;
  background: #e5f7f7;
  border-color: #bfe8e8;
}
.meta code.source-code.source-cli {
  color: #0b3a67;
  background: #e6f1ff;
  border-color: #bdd9f7;
}
.meta-note {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 700;
  border: 1px solid #d4dde8;
  background: #eef2f7;
  color: #334155;
}
.meta-note.error {
  color: #991b1b;
  background: #fee2e2;
  border-color: #fecaca;
}
.detail-toolbar {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  display: grid;
  gap: 8px;
  background: #f8fbff;
}
.detail-toolbar-row {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  align-items: center;
}
.detail-toolbar-row.secondary {
  padding-top: 8px;
  border-top: 1px solid rgba(204, 216, 228, 0.72);
}
.detail-toolbar-row.keyword {
  padding-top: 8px;
  border-top: 1px solid rgba(204, 216, 228, 0.56);
}
.detail-toolbar-row.range {
  padding-top: 8px;
  border-top: 1px solid rgba(204, 216, 228, 0.56);
}
.detail-toolbar-row.hidden {
  display: none;
}
.detail-toolbar label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #324255;
  user-select: none;
}
.detail-toolbar-spacer {
  flex: 1 1 auto;
}
.detail-toolbar #copy_resume_command {
  --button-shadow: rgba(15, 118, 110, 0.15);
  background: #0f766e;
}
.detail-toolbar #copy_resume_command:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
.detail-toolbar #refresh_detail {
  --button-shadow: rgba(29, 78, 216, 0.17);
  background: #1d4ed8;
}
.detail-toolbar #clear_detail {
  --button-shadow: rgba(71, 85, 105, 0.08);
  background: #f8fafc;
  color: #475569;
  border-color: #94a3b8;
}
.detail-toolbar #clear_detail:hover:not(:disabled) {
  background: #eef2f7;
}
.detail-toolbar #clear_detail:disabled,
.detail-toolbar #refresh_detail:disabled {
  background: #94a3b8;
  color: #ffffff;
  border-color: #94a3b8;
  cursor: not-allowed;
}
.detail-toolbar #toggle_detail_actions {
  --button-shadow: rgba(53, 92, 125, 0.14);
  background: #355c7d;
}
.detail-toolbar #copy_displayed_messages {
  --button-shadow: rgba(71, 85, 105, 0.12);
  background: #475569;
}
.detail-toolbar #copy_displayed_messages:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
.detail-toolbar #event_selection_mode {
  --button-shadow: rgba(8, 145, 178, 0.14);
  background: #0891b2;
}
.detail-toolbar #event_selection_mode.selection-active {
  background: #0f766e;
}
.detail-toolbar #event_selection_mode:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
.detail-toolbar #copy_selected_messages {
  --button-shadow: rgba(37, 99, 235, 0.13);
  background: #2563eb;
}
.detail-toolbar #copy_selected_messages:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
.detail-toolbar #message_range_selection_mode {
  --button-shadow: rgba(14, 116, 144, 0.14);
  background: #0e7490;
}
.detail-toolbar #message_range_selection_mode.selection-active {
  background: #155e75;
}
.detail-toolbar #clear_message_range_selection {
  --button-shadow: rgba(71, 85, 105, 0.08);
  background: #f8fafc;
  color: #475569;
  border-color: #94a3b8;
}
.detail-toolbar #clear_message_range_selection:hover:not(:disabled) {
  background: #eef2f7;
}
.detail-toolbar #detail_message_range_after {
  --button-shadow: rgba(8, 145, 178, 0.14);
  background: #0891b2;
}
.detail-toolbar #detail_message_range_before {
  --button-shadow: rgba(21, 128, 61, 0.14);
  background: #15803d;
}
.detail-toolbar #detail_message_range_after.contrast-dim,
.detail-toolbar #detail_message_range_before.contrast-dim {
  opacity: 0.62;
  filter: saturate(0.55) brightness(0.96);
  transform: none;
  box-shadow: 0 2px 8px rgba(71, 85, 105, 0.1);
}
.detail-toolbar #detail_message_range_after.contrast-dim:hover:not(:disabled),
.detail-toolbar #detail_message_range_before.contrast-dim:hover:not(:disabled) {
  transform: none;
  filter: saturate(0.65) brightness(0.98);
  box-shadow: 0 4px 10px rgba(71, 85, 105, 0.12);
}
.detail-toolbar #detail_message_range_after.active {
  background: linear-gradient(135deg, #0f9fc2 0%, #0e7490 100%);
  border-color: rgba(224, 242, 254, 0.92);
  color: #f8fdff;
  font-weight: 800;
  letter-spacing: 0.01em;
  text-shadow: 0 1px 0 rgba(8, 47, 73, 0.28);
  transform: translateY(-1px) scale(1.02);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.34),
    0 0 0 3px rgba(14, 116, 144, 0.24),
    0 12px 24px rgba(14, 116, 144, 0.24);
}
.detail-toolbar #detail_message_range_before.active {
  background: linear-gradient(135deg, #16a34a 0%, #166534 100%);
  border-color: rgba(220, 252, 231, 0.92);
  color: #fbfffc;
  font-weight: 800;
  letter-spacing: 0.01em;
  text-shadow: 0 1px 0 rgba(20, 83, 45, 0.28);
  transform: translateY(-1px) scale(1.02);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.34),
    0 0 0 3px rgba(22, 101, 52, 0.24),
    0 12px 24px rgba(22, 101, 52, 0.24);
}
.detail-toolbar #detail_message_range_after.active:hover:not(:disabled),
.detail-toolbar #detail_message_range_before.active:hover:not(:disabled) {
  transform: translateY(-1px) scale(1.02);
  filter: none;
}
.detail-toolbar #message_range_selection_mode:disabled,
.detail-toolbar #clear_message_range_selection:disabled,
.detail-toolbar #detail_message_range_after:disabled,
.detail-toolbar #detail_message_range_before:disabled {
  background: #94a3b8;
  color: #ffffff;
  border-color: #94a3b8;
  cursor: not-allowed;
}
.detail-toolbar #detail_keyword_filter {
  --button-shadow: rgba(21, 128, 61, 0.14);
  background: #15803d;
}
.detail-toolbar #detail_keyword_filter.active {
  background: #166534;
}
.detail-toolbar #detail_keyword_search {
  --button-shadow: rgba(217, 119, 6, 0.16);
  background: #d97706;
}
.detail-toolbar #detail_keyword_search.active {
  background: #b45309;
}
.detail-toolbar #detail_keyword_prev,
.detail-toolbar #detail_keyword_next {
  --button-shadow: rgba(71, 85, 105, 0.11);
  background: #475569;
}
.detail-toolbar #detail_keyword_clear {
  --button-shadow: rgba(71, 85, 105, 0.08);
  background: #f8fafc;
  color: #475569;
  border-color: #94a3b8;
}
.detail-toolbar #detail_keyword_clear:hover:not(:disabled) {
  background: #eef2f7;
}
.detail-toolbar #detail_keyword_filter:disabled,
.detail-toolbar #detail_keyword_search:disabled,
.detail-toolbar #detail_keyword_prev:disabled,
.detail-toolbar #detail_keyword_next:disabled,
.detail-toolbar #detail_keyword_clear:disabled {
  background: #94a3b8;
  color: #ffffff;
  border-color: #94a3b8;
  cursor: not-allowed;
}
#detail_keyword_q {
  flex: 0 1 clamp(220px, 30%, 380px);
  width: clamp(220px, 30%, 380px);
}
#detail_keyword_q:disabled {
  background: #eef2f7;
  color: #94a3b8;
  border-color: #cbd5e1;
  cursor: not-allowed;
}
#detail_keyword_q:disabled::placeholder {
  color: #94a3b8;
}
#add_session_label {
  --button-shadow: rgba(124, 58, 237, 0.18);
  background: #7c3aed;
}
#add_session_label:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
.session-label-strip {
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  background: #fcfdff;
  min-height: 44px;
}
.session-label-strip.empty {
  color: var(--muted);
  font-size: 12px;
}
#events {
  padding: 14px;
  overflow: auto;
  height: 100%;
}
.status-wrap {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.status-layer {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(248, 251, 255, 0.78);
  backdrop-filter: blur(3px);
  z-index: 5;
}
.status-layer.hidden {
  display: none;
}
.status-card {
  width: min(100%, 360px);
  border: 1px solid #d7e4ef;
  border-radius: 18px;
  padding: 18px 20px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 20px 40px rgba(15, 23, 42, 0.1);
  display: grid;
  gap: 10px;
  justify-items: center;
  text-align: center;
}
.status-card.empty {
  border-style: dashed;
  box-shadow: none;
}
.status-card.error {
  border-color: #fecaca;
  background: rgba(255, 245, 245, 0.98);
}
.status-title {
  color: #0f172a;
  font-size: 14px;
  font-weight: 700;
}
.status-copy {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}
.status-spinner,
.status-icon {
  width: 28px;
  height: 28px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}
.status-spinner {
  border: 3px solid #cfe3f5;
  border-top-color: var(--accent);
  animation: status-spin 0.9s linear infinite;
}
.status-icon {
  background: #e2e8f0;
  color: #475569;
  font-size: 14px;
  font-weight: 800;
}
.status-icon.error {
  background: #fee2e2;
  color: #b91c1c;
}
@keyframes status-spin {
  to {
    transform: rotate(360deg);
  }
}
.ev {
  border: 1px solid var(--line);
  border-left-width: 5px;
  border-radius: 10px;
  padding: 10px;
  margin-bottom: 10px;
  background: #fbfdff;
}
.ev.user { border-left-color: var(--user); background: #e7f1ff; }
.ev.user_context { border-left-color: #7f8ea0; background: #f5f7fa; }
.ev.assistant { border-left-color: var(--assistant); background: #e8f8f0; }
.ev.developer { border-left-color: var(--dev); background: #fff4e2; }
.ev.system { border-left-color: var(--system); background: #f0f3f7; }
.ev.label-match {
  box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.15);
}
.ev.copy-selected {
  outline: 2px solid rgba(37, 99, 235, 0.24);
  outline-offset: 1px;
}
.ev.range-anchor-selected {
  box-shadow: 0 0 0 2px rgba(14, 116, 144, 0.2);
}
.detail-keyword-hit {
  background: #fde68a;
  color: inherit;
  padding: 0 1px;
  border-radius: 3px;
}
.detail-keyword-hit.current {
  background: #f59e0b;
  color: #1f2937;
}
.ev-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 8px;
}
.event-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.event-select-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid #bfdbfe;
  background: rgba(255, 255, 255, 0.8);
  color: #1e3a8a;
  font-size: 11px;
  font-weight: 700;
}
.event-select-toggle input {
  margin: 0;
  accent-color: #2563eb;
}
.event-range-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid #a5f3fc;
  background: rgba(255, 255, 255, 0.8);
  color: #155e75;
  font-size: 11px;
  font-weight: 700;
}
.event-range-toggle input {
  margin: 0;
  accent-color: #0e7490;
}
.event-label-add-button {
  --button-shadow: rgba(124, 58, 237, 0.14);
  background: #7c3aed;
  padding: 6px 9px;
  font-size: 12px;
}
.event-label-add-button:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
.event-copy-button {
  --button-shadow: rgba(71, 85, 105, 0.1);
  background: #475569;
  padding: 6px 9px;
  font-size: 12px;
}
.event-copy-button:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
.badge-kind,
.badge-role,
.badge-time {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 2px 8px;
  border: 1px solid transparent;
  font-weight: 700;
}
.badge-kind {
  color: #334155;
  background: #edf2f7;
  border-color: #d4dde8;
}
.badge-time {
  color: #5a6673;
  background: #f6f8fb;
  border-color: #dce4ee;
  font-variant-numeric: tabular-nums;
}
.badge-role.user {
  color: #0f4fbe;
  background: #dbeafe;
  border-color: #b6d3ff;
}
.badge-role.user_context {
  color: #334155;
  background: #e5e9ef;
  border-color: #c7d0da;
}
.badge-role.assistant {
  color: #0b6a41;
  background: #d8f4e3;
  border-color: #a8debe;
}
.badge-role.developer {
  color: #7a4b00;
  background: #ffe7bf;
  border-color: #f4c97f;
}
.badge-role.system {
  color: #44505d;
  background: #e8edf3;
  border-color: #ccd8e4;
}
.data-label-badge {
  --label-color: #94a3b8;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: 999px;
  border: 1px solid var(--label-color);
  background: #ffffff;
  color: #1f2937;
  padding: 3px 8px;
  font-size: 11px;
  line-height: 1;
  font-weight: 700;
}
.data-label-badge .label-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--label-color);
  flex: 0 0 auto;
}
.data-label-badge .label-remove-button {
  border: 0;
  background: transparent;
  color: #475569;
  padding: 0;
  line-height: 1;
  font-size: 12px;
  cursor: pointer;
  box-shadow: none;
  transition: color 0.18s ease, opacity 0.18s ease;
}
.data-label-badge .label-remove-button:hover {
  color: #0f172a;
}
.label-picker {
  position: fixed;
  z-index: 9999;
  min-width: 220px;
  max-width: 280px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #ffffff;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.16);
  padding: 8px;
  display: grid;
  gap: 6px;
}
.label-picker.hidden {
  display: none;
}
.label-picker-option {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: flex-start;
  background: #ffffff;
  color: #18232f;
}
.label-picker-empty {
  font-size: 12px;
  color: var(--muted);
  padding: 6px 8px;
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
  #toggle_session_list_mobile {
    display: inline-flex;
  }
  .container {
    display: grid;
    grid-template-columns: 1fr;
    grid-template-rows: 40vh 1fr;
  }
  .container.sidebar-collapsed {
    grid-template-columns: 1fr;
    grid-template-rows: 0 1fr;
  }
  .left,
  .right {
    position: static;
    top: auto;
    left: auto;
    bottom: auto;
    width: auto;
    margin-left: 0;
    transition: none;
    will-change: auto;
  }
  .left {
    grid-column: 1;
    grid-row: 1;
    transform: none;
    opacity: 1;
  }
  .right {
    grid-column: 1;
    grid-row: 2;
    height: auto;
  }
  .container.sidebar-collapsed .left {
    transform: none;
    opacity: 0;
    pointer-events: none;
  }
  .container.sidebar-collapsed .right {
    margin-left: 0;
  }
}
</style>
</head>
<body>
<header>
  <div class="header-bar">
    <div>
      <h1>GitHub Copilot Sessions Viewer</h1>
      <small id="root"></small>
    </div>
    <div class="header-actions">
      <button id="toggle_session_list_mobile" class="secondary-button">一覧を隠す</button>
      <button id="open_label_manager" class="secondary-button">ラベル管理</button>
    </div>
  </div>
</header>
  <div class="container">
  <aside class="left">
    <div class="toolbar">
      <div class="toolbar-fields" id="toolbar_fields">
        <input id="cwd_q" placeholder="cwd (部分一致)" />
        <input id="date_from" type="date" />
        <input id="date_to" type="date" />
        <input id="q" placeholder="keyword filter" />
        <select id="mode">
          <option value="and">keyword AND</option>
          <option value="or">keyword OR</option>
        </select>
        <select id="source_filter">
          <option value="all">source: all</option>
          <option value="cli">source: CLI</option>
          <option value="vscode">source: VS Code</option>
        </select>
        <select id="session_label_filter">
          <option value="">session label: all</option>
        </select>
        <select id="event_label_filter">
          <option value="">event label: all</option>
        </select>
      </div>
      <div class="toolbar-actions">
        <button id="reload">Reload</button>
        <button id="clear">Clear</button>
        <button id="toggle_filters" class="secondary-button">Hide</button>
      </div>
    </div>
    <div class="content-shell">
      <div id="sessions"></div>
      <div id="sessions_status" class="status-layer hidden" aria-live="polite"></div>
    </div>
  </aside>
  <main class="right">
    <div class="meta" id="meta">セッションを選択してください</div>
    <div class="detail-toolbar">
      <div class="detail-toolbar-row primary">
        <label><input type="checkbox" id="only_user_instruction" /> ユーザー指示のみ表示</label>
        <label><input type="checkbox" id="only_ai_response" /> AIレスポンスのみ表示</label>
        <label title="各ターンの user 入力と、その直後の最後の assistant 応答だけを表示"><input type="checkbox" id="turn_boundary_only" /> 各入力と最終応答のみ</label>
        <label><input type="checkbox" id="reverse_order" /> 表示順を逆にする</label>
        <select id="detail_event_label_filter">
          <option value="">event label: all</option>
        </select>
        <button id="clear_detail" disabled>Clear</button>
        <button id="refresh_detail" disabled>Refresh</button>
        <span class="detail-toolbar-spacer"></span>
        <button id="toggle_detail_actions" class="secondary-button">Hide</button>
      </div>
      <div id="detail_action_row" class="detail-toolbar-row secondary">
        <button id="copy_resume_command" disabled>セッション再開コマンドコピー</button>
        <button id="add_session_label" disabled>セッションにラベル追加</button>
        <button id="copy_displayed_messages" disabled>表示中メッセージコピー</button>
        <button id="event_selection_mode" disabled>選択モード</button>
        <button id="copy_selected_messages" disabled>選択コピー</button>
      </div>
      <div id="detail_keyword_row" class="detail-toolbar-row keyword">
        <input id="detail_keyword_q" placeholder="detail keyword" />
        <button id="detail_keyword_filter" disabled>フィルター</button>
        <button id="detail_keyword_search" disabled>検索</button>
        <button id="detail_keyword_prev" disabled>前へ</button>
        <button id="detail_keyword_next" disabled>次へ</button>
        <button id="detail_keyword_clear" disabled>Keyword Clear</button>
      </div>
      <div id="detail_message_range_row" class="detail-toolbar-row range">
        <button id="message_range_selection_mode" disabled>起点選択モード</button>
        <button id="clear_message_range_selection" disabled>起点解除</button>
        <button id="detail_message_range_after" disabled>起点以降のみ表示</button>
        <button id="detail_message_range_before" disabled>起点以前のみ表示</button>
      </div>
    </div>
    <div class="session-label-strip empty" id="session_label_strip">セッションラベルはまだありません</div>
    <div class="events-shell">
      <div id="events"></div>
      <div id="detail_status" class="status-layer hidden" aria-live="polite"></div>
    </div>
  </main>
</div>
<div id="label_picker" class="label-picker hidden"></div>
<script>
const state = {
  sessions: [],
  filtered: [],
  activePath: null,
  activeSession: null,
  activeEvents: [],
  activeRawLineCount: 0,
  labels: [],
  isSessionsLoading: false,
  hasLoadedSessions: false,
  sessionsError: '',
  sessionsLoadMode: '',
  isDetailLoading: false,
  detailError: '',
  detailLoadMode: '',
  isEventSelectionMode: false,
  selectedEventIds: new Set(),
  isMessageRangeSelectionMode: false,
  selectedMessageRangeEventId: '',
  detailMessageRangeMode: '',
};

const FILTER_STORAGE_KEY = 'github_copilot_sessions_viewer_filters_v3';
const SEARCH_DEBOUNCE_MS = 180;
const BUTTON_FEEDBACK_MS = 1200;
const DETAIL_INTERACTION_LOCK_MS = 4000;
let loadSessionsTimer = null;
let loadSessionsRequestSeq = 0;
let loadSessionDetailRequestSeq = 0;
let saveFiltersFrame = 0;
let deferredDetailSyncTimer = 0;
let labelManagerWindow = null;
let labelPickerHandler = null;
let filtersVisible = true;
let detailActionsVisible = true;
let leftPaneVisible = true;
let pendingAutomaticDetailSync = false;
let detailPointerDown = false;
let detailInteractionLockUntil = 0;
let detailKeywordFilterTerm = '';
let detailKeywordSearchTerm = '';
let detailKeywordCurrentMatchIndex = -1;
let pendingDetailKeywordFocusIndex = -1;
let detailKeywordSearchTotal = 0;

function esc(s){
  return (s ?? '').toString().replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
}

function renderColorStyle(colorValue){
  return `--label-color:${esc(colorValue || '#94a3b8')}`;
}

function buildStatusCard(title, copy, tone){
  const kind = tone || 'loading';
  const indicator = kind === 'loading'
    ? '<span class="status-spinner" aria-hidden="true"></span>'
    : `<span class="status-icon ${kind === 'error' ? 'error' : ''}" aria-hidden="true">${kind === 'error' ? '!' : 'i'}</span>`;
  return `<div class="status-card ${esc(kind)}">${indicator}<div class="status-title">${esc(title || '')}</div>${copy ? `<div class="status-copy">${esc(copy)}</div>` : ''}</div>`;
}

function renderInlineStatus(title, copy, tone){
  return `<div class="status-wrap">${buildStatusCard(title, copy, tone)}</div>`;
}

function setStatusLayer(id, title, copy, tone){
  const layer = document.getElementById(id);
  if(!layer){
    return;
  }
  if(!title){
    layer.classList.add('hidden');
    layer.innerHTML = '';
    return;
  }
  layer.innerHTML = buildStatusCard(title, copy, tone);
  layer.classList.remove('hidden');
}

function updateReloadButtonState(){
  const button = document.getElementById('reload');
  if(!button){
    return;
  }
  const isManualReload = state.isSessionsLoading && state.sessionsLoadMode === 'reload';
  button.disabled = isManualReload;
  button.textContent = isManualReload ? 'Reloading...' : 'Reload';
}

function updateFilterVisibility(){
  const toolbar = document.querySelector('.toolbar');
  const button = document.getElementById('toggle_filters');
  if(filtersVisible){
    toolbar.classList.remove('collapsed');
    button.textContent = 'Hide';
  } else {
    toolbar.classList.add('collapsed');
    button.textContent = 'Show';
  }
}

function setFiltersVisible(nextVisible){
  filtersVisible = !!nextVisible;
  updateFilterVisibility();
  saveFiltersSoon();
}

function updateDetailActionsVisibility(){
  const actionRow = document.getElementById('detail_action_row');
  const keywordRow = document.getElementById('detail_keyword_row');
  const messageRangeRow = document.getElementById('detail_message_range_row');
  const button = document.getElementById('toggle_detail_actions');
  if(!actionRow || !keywordRow || !messageRangeRow || !button){
    return;
  }
  actionRow.classList.toggle('hidden', !detailActionsVisible);
  keywordRow.classList.toggle('hidden', !detailActionsVisible);
  messageRangeRow.classList.toggle('hidden', !detailActionsVisible);
  button.textContent = detailActionsVisible ? 'Hide' : 'Show';
}

function setDetailActionsVisible(nextVisible){
  detailActionsVisible = !!nextVisible;
  updateDetailActionsVisibility();
  saveFiltersSoon();
}

function updateLeftPaneVisibility(){
  const container = document.querySelector('.container');
  const mobileButton = document.getElementById('toggle_session_list_mobile');
  const isMobileLayout = window.matchMedia('(max-width: 900px)').matches;
  if(!container){
    return;
  }
  container.classList.toggle('sidebar-collapsed', isMobileLayout && !leftPaneVisible);
  const label = leftPaneVisible ? '左ペインを隠す' : '左ペインを表示';
  if(mobileButton){
    mobileButton.textContent = leftPaneVisible ? '一覧を隠す' : '一覧を表示';
    mobileButton.setAttribute('aria-label', label);
    mobileButton.title = label;
  }
}

function setLeftPaneVisible(nextVisible){
  leftPaneVisible = !!nextVisible;
  updateLeftPaneVisibility();
  saveFiltersSoon();
}

function saveFiltersSoon(){
  if(saveFiltersFrame){
    cancelAnimationFrame(saveFiltersFrame);
  }
  saveFiltersFrame = requestAnimationFrame(() => {
    saveFiltersFrame = 0;
    setTimeout(() => {
      saveFilters();
    }, 0);
  });
}

function cancelScheduledSaveFilters(){
  if(saveFiltersFrame){
    cancelAnimationFrame(saveFiltersFrame);
    saveFiltersFrame = 0;
  }
}

function postJson(url, payload){
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  }).then(r => r.json());
}

function getSelectedSessionLabelFilter(){
  return document.getElementById('session_label_filter').value || '';
}

function getSelectedListEventLabelFilter(){
  return document.getElementById('event_label_filter').value || '';
}

function getSelectedDetailEventLabelFilter(){
  return document.getElementById('detail_event_label_filter').value || '';
}

function isTurnBoundaryFilterEnabled(){
  const checkbox = document.getElementById('turn_boundary_only');
  return !!(checkbox && checkbox.checked);
}

function filterEventsToTurnBoundaries(events){
  if(!Array.isArray(events) || events.length === 0){
    return Array.isArray(events) ? events : [];
  }
  const filtered = [];
  let pendingUser = null;
  let lastAssistant = null;

  function flushTurn(){
    if(!pendingUser){
      return;
    }
    filtered.push(pendingUser);
    if(lastAssistant){
      filtered.push(lastAssistant);
    }
    pendingUser = null;
    lastAssistant = null;
  }

  events.forEach(ev => {
    if(ev.kind !== 'message'){
      return;
    }
    if(ev.role === 'user'){
      flushTurn();
      pendingUser = ev;
      return;
    }
    if(ev.role === 'assistant' && pendingUser){
      lastAssistant = ev;
    }
  });

  flushTurn();
  return filtered;
}

function populateLabelSelect(selectId, allLabel){
  const select = document.getElementById(selectId);
  const current = select.value;
  const options = [`<option value="">${esc(allLabel)}</option>`].concat(
    state.labels.map(label => `<option value="${esc(label.id)}">${esc(label.name)}</option>`)
  );
  select.innerHTML = options.join('');
  const hasCurrent = state.labels.some(label => String(label.id) === current);
  select.value = hasCurrent ? current : '';
}

function populateLabelControls(){
  populateLabelSelect('session_label_filter', 'session label: all');
  populateLabelSelect('event_label_filter', 'event label: all');
  populateLabelSelect('detail_event_label_filter', 'event label: all');
  ['session_label_filter', 'event_label_filter', 'detail_event_label_filter'].forEach(id => {
    const select = document.getElementById(id);
    const pending = select.dataset.pendingValue;
    if(pending && Array.from(select.options).some(option => option.value === pending)){
      select.value = pending;
    }
    delete select.dataset.pendingValue;
  });
  renderSessionList();
  renderSessionLabelStrip();
  renderActiveSession();
  updateSessionLabelButtonState();
}

function renderAssignedLabels(labels, removeType, extra){
  if(!Array.isArray(labels) || labels.length === 0) return '';
  return labels.map(label => {
    const attrs = removeType ? (
      ` data-remove-type="${esc(removeType)}"` +
      ` data-label-id="${esc(label.id)}"` +
      (extra && extra.eventId ? ` data-event-id="${esc(extra.eventId)}"` : '')
    ) : '';
    const removeButton = removeType
      ? `<button class="label-remove-button" title="ラベル解除"${attrs}>×</button>`
      : '';
    return `<span class="data-label-badge" style="${renderColorStyle(label.color_value)}"><span class="label-dot"></span><span>${esc(label.name)}</span>${removeButton}</span>`;
  }).join('');
}

function updateSessionLabelButtonState(){
  const button = document.getElementById('add_session_label');
  button.disabled = !state.activePath || state.labels.length === 0;
}

function renderSessionLabelStrip(){
  const strip = document.getElementById('session_label_strip');
  if(!state.activeSession){
    strip.classList.add('empty');
    strip.textContent = state.isDetailLoading && state.activePath
      ? 'セッションラベルを読み込み中...'
      : 'セッションラベルはまだありません';
    updateSessionLabelButtonState();
    return;
  }
  const labels = state.activeSession.session_labels || [];
  if(!labels.length){
    strip.classList.add('empty');
    strip.textContent = 'セッションラベルはまだありません';
    updateSessionLabelButtonState();
    return;
  }
  strip.classList.remove('empty');
  strip.innerHTML = renderAssignedLabels(labels, 'session');
  strip.querySelectorAll('.label-remove-button').forEach(button => {
    button.onclick = async () => {
      const labelId = Number(button.dataset.labelId);
      await removeSessionLabel(labelId);
    };
  });
  updateSessionLabelButtonState();
}

function getDetailEventKey(ev, fallbackIndex){
  if(ev && ev.event_id){
    return String(ev.event_id);
  }
  return `${ev && ev.kind ? ev.kind : 'event'}:${ev && ev.timestamp ? ev.timestamp : ''}:${fallbackIndex}`;
}

function buildEventCardHtml(ev, selectedEventLabelId, fallbackIndex, searchMeta){
  const role = ev.role || 'system';
  const roleLabel = role.replace('_', ' ');
  const labels = ev.labels || [];
  const matchesSelectedLabel = selectedEventLabelId && labels.some(label => String(label.id) === selectedEventLabelId);
  const eventKey = getDetailEventKey(ev, fallbackIndex);
  const bodyText = getEventBodyText(ev);
  const eventMatches = searchMeta && searchMeta.matchesByEvent ? (searchMeta.matchesByEvent.get(eventKey) || []) : [];
  const body = `<pre>${renderHighlightedEventBody(bodyText, eventMatches)}</pre>`;
  const selectionKey = getEventSelectionKey(ev);
  const isSelectable = state.isEventSelectionMode && isSelectableMessageEvent(ev);
  const isSelected = selectionKey && state.selectedEventIds.has(selectionKey);
  const isRangeSelectable = state.isMessageRangeSelectionMode && isSelectableMessageEvent(ev);
  const isRangeSelected = selectionKey && state.selectedMessageRangeEventId === selectionKey;
  const selectionCheckboxHtml = isSelectable
    ? `<label class="event-select-toggle"><input type="checkbox" class="event-select-checkbox" data-event-id="${esc(selectionKey)}" ${isSelected ? 'checked' : ''} />選択</label>`
    : '';
  const rangeSelectionHtml = isRangeSelectable
    ? `<label class="event-range-toggle"><input type="radio" name="message-range-selection" class="event-range-radio" data-event-id="${esc(selectionKey)}" ${isRangeSelected ? 'checked' : ''} />起点</label>`
    : '';
  const labelsHtml = renderAssignedLabels(labels, 'event', { eventId: ev.event_id });
  const copyButtonHtml = ev.kind === 'message'
    ? `<button class="event-copy-button" data-event-id="${esc(ev.event_id || '')}">コピー</button>`
    : '';
  return `<div class="ev ${role} ${matchesSelectedLabel ? 'label-match' : ''} ${isSelected ? 'copy-selected' : ''} ${isRangeSelected ? 'range-anchor-selected' : ''}"><div class="ev-head">${selectionCheckboxHtml}${rangeSelectionHtml}<span class="badge-kind">${esc(ev.kind || 'event')}</span><span class="badge-role ${role}">${esc(roleLabel)}</span><span class="badge-time">${esc(fmt(ev.timestamp))}</span><span class="event-actions">${labelsHtml}<button class="event-label-add-button" data-event-id="${esc(ev.event_id || '')}" ${state.labels.length ? '' : 'disabled'}>ラベル追加</button>${copyButtonHtml}</span></div>${body}</div>`;
}

function attachVisibleEventCardHandlers(eventsBox){
  eventsBox.querySelectorAll('.event-label-add-button').forEach(button => {
    button.onclick = async () => {
      await addEventLabelFromButton(button, button.dataset.eventId);
    };
  });
  eventsBox.querySelectorAll('.event-copy-button').forEach(button => {
    button.onclick = async () => {
      await copyEventMessage(button, button.dataset.eventId);
    };
  });
  eventsBox.querySelectorAll('.event-select-checkbox').forEach(input => {
    input.onchange = () => {
      updateEventSelection(input.dataset.eventId, input.checked, input.closest('.ev'));
    };
  });
  eventsBox.querySelectorAll('.event-range-radio').forEach(input => {
    input.onchange = () => {
      if(input.checked){
        updateMessageRangeSelection(input.dataset.eventId);
      }
    };
  });
  eventsBox.querySelectorAll('.label-remove-button[data-remove-type="event"]').forEach(button => {
    button.onclick = async () => {
      await removeEventLabel(button.dataset.eventId, Number(button.dataset.labelId));
    };
  });
}

function renderEventList(eventsBox, displayEvents, selectedEventLabelId, searchMeta){
  const targetMatch = searchMeta && pendingDetailKeywordFocusIndex >= 0
    ? searchMeta.matches[pendingDetailKeywordFocusIndex] || null
    : null;
  const previousScrollTop = eventsBox.scrollTop;
  eventsBox.innerHTML = displayEvents.map((ev, index) => buildEventCardHtml(ev, selectedEventLabelId, index, searchMeta)).join('');
  eventsBox.scrollTop = previousScrollTop;
  attachVisibleEventCardHandlers(eventsBox);
  if(targetMatch){
    requestAnimationFrame(() => {
      focusDetailKeywordMatch(eventsBox, pendingDetailKeywordFocusIndex);
      pendingDetailKeywordFocusIndex = -1;
    });
  }
}

function hideLabelPicker(){
  const picker = document.getElementById('label_picker');
  picker.classList.add('hidden');
  picker.innerHTML = '';
  labelPickerHandler = null;
}

function showLabelPicker(anchor, onSelect){
  const picker = document.getElementById('label_picker');
  if(!state.labels.length){
    alert('ラベルがありません。先にラベル管理から作成してください。');
    return;
  }
  labelPickerHandler = onSelect;
  picker.innerHTML = state.labels.map(label =>
    `<button class="label-picker-option" data-label-id="${esc(label.id)}" style="${renderColorStyle(label.color_value)}"><span class="label-dot"></span><span>${esc(label.name)}</span></button>`
  ).join('');
  picker.querySelectorAll('.label-picker-option').forEach(button => {
    button.onclick = async () => {
      const labelId = Number(button.dataset.labelId);
      const handler = labelPickerHandler;
      hideLabelPicker();
      if(!handler){
        return;
      }
      await handler(labelId);
    };
  });
  const rect = anchor.getBoundingClientRect();
  picker.style.top = `${Math.round(rect.bottom + 8)}px`;
  picker.style.left = `${Math.round(Math.min(rect.left, window.innerWidth - 300))}px`;
  picker.classList.remove('hidden');
}

async function loadLabels(reloadSessions){
  const r = await fetch('/api/labels?ts=' + Date.now(), { cache: 'no-store' });
  const data = await r.json();
  const prev = JSON.stringify(state.labels);
  state.labels = data.labels || [];
  populateLabelControls();
  if(reloadSessions && prev !== JSON.stringify(state.labels)){
    await loadSessions({ mode: 'labels' });
  }
}

function openLabelManagerWindow(){
  const features = 'width=720,height=680,resizable=yes,scrollbars=yes';
  if(labelManagerWindow && !labelManagerWindow.closed){
    labelManagerWindow.focus();
    return;
  }
  labelManagerWindow = window.open('/labels', 'copilot_label_manager', features);
}

function highlightSessionPath(s){
  const safe = esc(s);
  return safe.replace(/(\\d{4}-\\d{2}-\\d{2}T\\d{2}[-:]\\d{2}[-:]\\d{2}(?:[-:]\\d{3,6})?)/g, '<span class="ts">$1</span>');
}

function normalizeSource(source){
  const raw = (source || '').toLowerCase();
  if(raw === 'vscode') return 'vscode';
  return 'cli';
}

function sourceLabel(source){
  const key = normalizeSource(source);
  if(key === 'vscode') return 'VS Code';
  return 'CLI';
}

function normalizeSourceFilter(source){
  const raw = (source || '').toLowerCase();
  if(raw === 'all') return 'all';
  if(raw === 'cli' || raw === 'vscode'){
    return raw;
  }
  return 'all';
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
  // raw is expected as YYYY-MM-DD from <input type="date">.
  const ts = toTimestamp(`${raw}T00:00:00`);
  return Number.isNaN(ts) ? null : ts;
}

function parseOptionalDateEnd(raw){
  if(!raw) return null;
  // Inclusive end-of-day for date-range filtering.
  const ts = toTimestamp(`${raw}T23:59:59.999`);
  return Number.isNaN(ts) ? null : ts;
}

function getActiveSessionId(){
  if(!state.activeSession) return '';
  return (state.activeSession.session_id || state.activeSession.id || '').toString().trim();
}

function getButtonLabel(button, fallback){
  if(!button) return fallback || '';
  if(!button.dataset.defaultLabel){
    button.dataset.defaultLabel = button.textContent;
  }
  return button.dataset.defaultLabel || fallback || '';
}

function flashButtonLabel(button, temporaryLabel, fallback, duration){
  if(!button) return;
  const defaultLabel = getButtonLabel(button, fallback);
  button.textContent = temporaryLabel;
  if(button._labelTimer){
    clearTimeout(button._labelTimer);
  }
  button._labelTimer = setTimeout(() => {
    button.textContent = defaultLabel;
  }, duration || BUTTON_FEEDBACK_MS);
}

function waitForUiFeedback(duration){
  return new Promise(resolve => {
    setTimeout(resolve, duration || BUTTON_FEEDBACK_MS);
  });
}

function getDetailKeywordInputValue(){
  const input = document.getElementById('detail_keyword_q');
  return input ? input.value : '';
}

function stringifyEventBodyValue(value){
  if(value == null){
    return '';
  }
  if(typeof value === 'string'){
    return value;
  }
  if(typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint'){
    return String(value);
  }
  try {
    return JSON.stringify(value, (key, currentValue) => {
      if(typeof currentValue === 'string' && currentValue.startsWith('data:image/')){
        return '[image data omitted]';
      }
      return currentValue;
    }, 2) || '';
  } catch (error) {
    return String(value);
  }
}

function containsLiteralKeyword(text, keyword){
  if(!keyword){
    return false;
  }
  return stringifyEventBodyValue(text).toLocaleLowerCase().includes(keyword.toLocaleLowerCase());
}

function findLiteralKeywordRanges(text, keyword){
  if(!keyword){
    return [];
  }
  const source = stringifyEventBodyValue(text);
  const haystack = source.toLocaleLowerCase();
  const needle = keyword.toLocaleLowerCase();
  const ranges = [];
  let cursor = 0;
  while(cursor <= haystack.length - needle.length){
    const nextIndex = haystack.indexOf(needle, cursor);
    if(nextIndex === -1){
      break;
    }
    ranges.push({ start: nextIndex, end: nextIndex + keyword.length });
    cursor = nextIndex + Math.max(keyword.length, 1);
  }
  return ranges;
}

function getEventBodyText(ev){
  if(!ev){
    return '';
  }
  if(ev.kind === 'message' || ev.kind === 'info' || ev.kind === 'error' || String(ev.kind || '').startsWith('assistant.turn_')){
    return stringifyEventBodyValue(ev.text);
  }
  if(ev.kind === 'function_call' || ev.kind === 'tool_start'){
    return `name: ${stringifyEventBodyValue(ev.name)}\n${stringifyEventBodyValue(ev.arguments)}`;
  }
  if(ev.kind === 'tool_output'){
    const ok = ev.success === null || ev.success === undefined ? '' : `success: ${ev.success}\n`;
    return `${ok}${stringifyEventBodyValue(ev.text)}`;
  }
  try {
    return JSON.stringify(ev, null, 2) || '';
  } catch (error) {
    return '';
  }
}

function buildDetailKeywordSearchMeta(displayEvents, keyword){
  const matches = [];
  const matchesByEvent = new Map();
  const rawKeyword = keyword || '';
  if(!rawKeyword){
    return { keyword: '', matches, matchesByEvent, total: 0 };
  }
  displayEvents.forEach((ev, eventIndex) => {
    const eventKey = getDetailEventKey(ev, eventIndex);
    const ranges = findLiteralKeywordRanges(getEventBodyText(ev), rawKeyword);
    if(!ranges.length){
      return;
    }
    const eventMatches = ranges.map(range => {
      const match = {
        eventKey,
        eventIndex,
        start: range.start,
        end: range.end,
        globalIndex: matches.length,
      };
      matches.push(match);
      return match;
    });
    matchesByEvent.set(eventKey, eventMatches);
  });
  return {
    keyword: rawKeyword,
    matches,
    matchesByEvent,
    total: matches.length,
  };
}

function normalizeDetailKeywordSearchPosition(searchMeta){
  if(!searchMeta.total){
    detailKeywordCurrentMatchIndex = -1;
    pendingDetailKeywordFocusIndex = -1;
    return;
  }
  if(detailKeywordCurrentMatchIndex < 0 || detailKeywordCurrentMatchIndex >= searchMeta.total){
    detailKeywordCurrentMatchIndex = 0;
  }
  if(pendingDetailKeywordFocusIndex >= searchMeta.total){
    pendingDetailKeywordFocusIndex = -1;
  }
}

function renderHighlightedEventBody(text, eventMatches){
  if(!Array.isArray(eventMatches) || !eventMatches.length){
    return esc(text || '');
  }
  let cursor = 0;
  let html = '';
  const source = text || '';
  eventMatches.forEach(match => {
    html += esc(source.slice(cursor, match.start));
    const currentClass = match.globalIndex === detailKeywordCurrentMatchIndex ? ' current' : '';
    html += `<mark class="detail-keyword-hit${currentClass}" data-search-match-index="${match.globalIndex}">${esc(source.slice(match.start, match.end))}</mark>`;
    cursor = match.end;
  });
  html += esc(source.slice(cursor));
  return html;
}

function updateDetailKeywordControls(searchMeta){
  const input = document.getElementById('detail_keyword_q');
  const filterButton = document.getElementById('detail_keyword_filter');
  const searchButton = document.getElementById('detail_keyword_search');
  const prevButton = document.getElementById('detail_keyword_prev');
  const nextButton = document.getElementById('detail_keyword_next');
  const clearButton = document.getElementById('detail_keyword_clear');
  if(!input || !filterButton || !searchButton || !prevButton || !nextButton || !clearButton){
    return;
  }
  const hasActiveSession = !!state.activeSession;
  const hasInputValue = getDetailKeywordInputValue() !== '';
  const searchTotal = searchMeta && typeof searchMeta.total === 'number' ? searchMeta.total : detailKeywordSearchTotal;
  const hasSearchMatches = searchTotal > 0;
  const hasKeywordState = hasInputValue || detailKeywordFilterTerm !== '' || detailKeywordSearchTerm !== '';
  input.disabled = !hasActiveSession;
  filterButton.disabled = !hasActiveSession || !hasInputValue;
  searchButton.disabled = !hasActiveSession || !hasInputValue;
  prevButton.disabled = !hasSearchMatches;
  nextButton.disabled = !hasSearchMatches;
  clearButton.disabled = !hasKeywordState;
  filterButton.classList.toggle('active', hasActiveSession && detailKeywordFilterTerm !== '');
  searchButton.classList.toggle('active', hasActiveSession && detailKeywordSearchTerm !== '');
  updateClearDetailButtonState();
}

function resetDetailKeywordState(){
  detailKeywordFilterTerm = '';
  detailKeywordSearchTerm = '';
  detailKeywordCurrentMatchIndex = -1;
  pendingDetailKeywordFocusIndex = -1;
  detailKeywordSearchTotal = 0;
}

function focusDetailKeywordMatch(eventsBox, matchIndex){
  if(matchIndex < 0){
    return;
  }
  const target = eventsBox.querySelector(`.detail-keyword-hit[data-search-match-index="${matchIndex}"]`);
  if(target){
    target.scrollIntoView({ block: 'center', inline: 'nearest' });
  }
}

function isAutomaticSessionsLoadMode(mode){
  return mode === 'auto' || mode === 'focus' || mode === 'labels';
}

function shouldSyncActiveSessionAfterListLoad(mode){
  return mode !== 'auto';
}

function clearDeferredDetailSyncTimer(){
  if(deferredDetailSyncTimer){
    clearTimeout(deferredDetailSyncTimer);
    deferredDetailSyncTimer = 0;
  }
}

function noteDetailInteraction(){
  detailInteractionLockUntil = Date.now() + DETAIL_INTERACTION_LOCK_MS;
}

function hasDetailTextSelection(){
  const eventsBox = document.getElementById('events');
  const selection = window.getSelection ? window.getSelection() : null;
  if(!eventsBox || !selection || selection.isCollapsed || selection.rangeCount === 0){
    return false;
  }
  const anchorNode = selection.anchorNode;
  const focusNode = selection.focusNode;
  return Boolean(
    (anchorNode && eventsBox.contains(anchorNode)) ||
    (focusNode && eventsBox.contains(focusNode))
  );
}

function hasRecentDetailInteraction(){
  return detailPointerDown || hasDetailTextSelection() || Date.now() < detailInteractionLockUntil;
}

function syncActiveSessionSummaryFromList(path){
  if(!path){
    return;
  }
  const summary = (state.sessions || []).find(session => session.path === path);
  if(!summary){
    return;
  }
  state.activeSession = {
    ...(state.activeSession || {}),
    ...summary,
  };
}

async function maybeRunDeferredAutomaticDetailSync(){
  if(!pendingAutomaticDetailSync){
    return;
  }
  if(!document.hasFocus() || hasRecentDetailInteraction() || state.isDetailLoading || !state.activePath){
    scheduleDeferredAutomaticDetailSync();
    return;
  }
  pendingAutomaticDetailSync = false;
  clearDeferredDetailSyncTimer();
  await openSession(state.activePath, { mode: 'sync' });
}

function scheduleDeferredAutomaticDetailSync(){
  clearDeferredDetailSyncTimer();
  if(!pendingAutomaticDetailSync){
    return;
  }
  const waitMs = Math.max(0, detailInteractionLockUntil - Date.now()) + 80;
  deferredDetailSyncTimer = setTimeout(() => {
    deferredDetailSyncTimer = 0;
    void maybeRunDeferredAutomaticDetailSync();
  }, waitMs);
}

async function copyTextToClipboard(text){
  if(!text) return false;
  let copied = false;
  try {
    if(navigator.clipboard && navigator.clipboard.writeText){
      await navigator.clipboard.writeText(text);
      copied = true;
    }
  } catch (e) {
    copied = false;
  }
  if(copied){
    return true;
  }
  const helper = document.createElement('textarea');
  helper.value = text;
  helper.setAttribute('readonly', '');
  helper.style.position = 'fixed';
  helper.style.opacity = '0';
  document.body.appendChild(helper);
  helper.select();
  try {
    copied = document.execCommand('copy');
  } finally {
    document.body.removeChild(helper);
  }
  return copied;
}

function getDisplayMessageEvents(){
  return getDisplayEvents().filter(ev => ev.kind === 'message' && (ev.text || '').trim());
}

function getEventSelectionKey(ev){
  return ev && ev.event_id ? String(ev.event_id) : '';
}

function isSelectableMessageEvent(ev){
  return ev && ev.kind === 'message' && (ev.text || '').trim() && getEventSelectionKey(ev);
}

function getSelectableDisplayMessageEvents(){
  return getDisplayEvents().filter(isSelectableMessageEvent);
}

function getSelectedMessageEvents(){
  const selectedIds = state.selectedEventIds || new Set();
  return (state.activeEvents || []).filter(ev => isSelectableMessageEvent(ev) && selectedIds.has(getEventSelectionKey(ev)));
}

function getSelectedMessageRangeEvent(){
  const selectedId = state.selectedMessageRangeEventId || '';
  if(!selectedId){
    return null;
  }
  return (state.activeEvents || []).find(ev => isSelectableMessageEvent(ev) && getEventSelectionKey(ev) === selectedId) || null;
}

function clearSelectedEventIds(){
  state.selectedEventIds = new Set();
}

function syncSelectedEventIdsToActiveEvents(){
  const validIds = new Set((state.activeEvents || []).filter(isSelectableMessageEvent).map(getEventSelectionKey));
  state.selectedEventIds = new Set(Array.from(state.selectedEventIds || []).filter(id => validIds.has(id)));
}

function clearMessageRangeSelection(){
  state.isMessageRangeSelectionMode = false;
  state.selectedMessageRangeEventId = '';
  state.detailMessageRangeMode = '';
}

function syncSelectedMessageRangeToActiveEvents(){
  if(!state.selectedMessageRangeEventId){
    return;
  }
  if(getSelectedMessageRangeEvent()){
    return;
  }
  state.selectedMessageRangeEventId = '';
  state.detailMessageRangeMode = '';
}

function updateDisplayedMessagesCopyButtonState(){
  const button = document.getElementById('copy_displayed_messages');
  if(!state.activeSession){
    button.disabled = true;
    return;
  }
  const hasMessages = !!getDisplayMessageEvents().length;
  button.disabled = state.isDetailLoading || !hasMessages;
}

function updateCopyResumeButtonState(){
  const button = document.getElementById('copy_resume_command');
  button.disabled = !getActiveSessionId();
}

function updateEventSelectionModeButtonState(){
  const button = document.getElementById('event_selection_mode');
  if(!button){
    return;
  }
  const hasSelectableMessages = !!getSelectableDisplayMessageEvents().length;
  const hasSelectedMessages = !!getSelectedMessageEvents().length;
  button.disabled = !state.activeSession || (!hasSelectableMessages && !hasSelectedMessages && !state.isEventSelectionMode);
  button.textContent = state.isEventSelectionMode ? '選択終了' : '選択モード';
  button.classList.toggle('selection-active', state.isEventSelectionMode);
}

function updateCopySelectedMessagesButtonState(){
  const button = document.getElementById('copy_selected_messages');
  if(!button){
    return;
  }
  const selectedMessages = getSelectedMessageEvents();
  const defaultLabel = selectedMessages.length ? `選択コピー (${selectedMessages.length}件)` : '選択コピー';
  button.disabled = state.isDetailLoading || selectedMessages.length === 0;
  button.textContent = defaultLabel;
  button.dataset.defaultLabel = defaultLabel;
}

function updateMessageRangeSelectionModeButtonState(){
  const button = document.getElementById('message_range_selection_mode');
  if(!button){
    return;
  }
  const hasSelectableMessages = !!getSelectableDisplayMessageEvents().length;
  const hasSelectedMessage = !!getSelectedMessageRangeEvent();
  button.disabled = !state.activeSession || (!hasSelectableMessages && !hasSelectedMessage && !state.isMessageRangeSelectionMode);
  button.textContent = state.isMessageRangeSelectionMode ? '起点選択終了' : '起点選択モード';
  button.classList.toggle('selection-active', state.isMessageRangeSelectionMode);
}

function updateClearMessageRangeSelectionButtonState(){
  const button = document.getElementById('clear_message_range_selection');
  if(!button){
    return;
  }
  button.disabled = !state.activeSession || (!getSelectedMessageRangeEvent() && !state.detailMessageRangeMode);
}

function updateMessageRangeFilterButtonsState(){
  const afterButton = document.getElementById('detail_message_range_after');
  const beforeButton = document.getElementById('detail_message_range_before');
  if(!afterButton || !beforeButton){
    return;
  }
  const hasSelectedMessage = !!getSelectedMessageRangeEvent();
  const isAfterActive = state.detailMessageRangeMode === 'after';
  const isBeforeActive = state.detailMessageRangeMode === 'before';
  const hasActiveRangeMode = isAfterActive || isBeforeActive;
  afterButton.disabled = state.isDetailLoading || !hasSelectedMessage;
  beforeButton.disabled = state.isDetailLoading || !hasSelectedMessage;
  afterButton.classList.toggle('active', isAfterActive);
  beforeButton.classList.toggle('active', isBeforeActive);
  afterButton.classList.toggle('contrast-dim', hasActiveRangeMode && !isAfterActive);
  beforeButton.classList.toggle('contrast-dim', hasActiveRangeMode && !isBeforeActive);
  afterButton.textContent = isAfterActive ? '起点以降のみ表示中' : '起点以降のみ表示';
  beforeButton.textContent = isBeforeActive ? '起点以前のみ表示中' : '起点以前のみ表示';
  afterButton.setAttribute('aria-pressed', isAfterActive ? 'true' : 'false');
  beforeButton.setAttribute('aria-pressed', isBeforeActive ? 'true' : 'false');
}

function updateRefreshDetailButtonState(){
  const button = document.getElementById('refresh_detail');
  const isManualRefresh = state.isDetailLoading && state.detailLoadMode === 'refresh';
  button.disabled = !state.activePath || isManualRefresh;
  if(!isManualRefresh){
    button.textContent = 'Refresh';
    return;
  }
  button.textContent = 'Refreshing...';
}

function hasDetailFilter(){
  return Boolean(
    document.getElementById('only_user_instruction').checked ||
    document.getElementById('only_ai_response').checked ||
    document.getElementById('turn_boundary_only').checked ||
    document.getElementById('reverse_order').checked ||
    getSelectedDetailEventLabelFilter() ||
    state.detailMessageRangeMode
  );
}

function updateClearDetailButtonState(){
  const button = document.getElementById('clear_detail');
  if(!button){
    return;
  }
  button.disabled = !hasDetailFilter();
}

function hasListFilter(){
  return Boolean(
    document.getElementById('cwd_q').value.trim() ||
    document.getElementById('date_from').value ||
    document.getElementById('date_to').value ||
    document.getElementById('q').value.trim() ||
    normalizeSourceFilter(document.getElementById('source_filter').value || 'all') !== 'all' ||
    getSelectedSessionLabelFilter() ||
    getSelectedListEventLabelFilter()
  );
}

async function copyResumeCommand(){
  const sessionId = getActiveSessionId();
  if(!sessionId) return;

  const commandText = 'copilot --resume ' + sessionId;
  const copied = await copyTextToClipboard(commandText);

  if(copied){
    const button = document.getElementById('copy_resume_command');
    flashButtonLabel(button, 'コピーしました', 'セッション再開コマンドコピー');
  }
}

function scheduleLoadSessions(){
  saveFilters();
  if(loadSessionsTimer){
    clearTimeout(loadSessionsTimer);
  }
  loadSessionsTimer = setTimeout(() => {
    loadSessionsTimer = null;
    loadSessions();
  }, SEARCH_DEBOUNCE_MS);
}

function normalizeRequestError(error, fallback){
  if(error && typeof error.message === 'string' && error.message.trim()){
    return error.message.trim();
  }
  return fallback;
}

async function loadSessions(options){
  saveFilters();
  const requestId = ++loadSessionsRequestSeq;
  const loadMode = options && options.mode ? options.mode : 'auto';
  state.isSessionsLoading = true;
  state.sessionsError = '';
  state.sessionsLoadMode = loadMode;
  renderSessionList();
  const params = new URLSearchParams();
  params.set('ts', Date.now().toString());
  const q = document.getElementById('q').value.trim();
  if(q){
    params.set('q', q);
    params.set('mode', document.getElementById('mode').value);
  }
  const sessionLabelId = getSelectedSessionLabelFilter();
  const eventLabelId = getSelectedListEventLabelFilter();
  if(sessionLabelId){
    params.set('session_label_id', sessionLabelId);
  }
  if(eventLabelId){
    params.set('event_label_id', eventLabelId);
  }
  try {
    const r = await fetch('/api/sessions?' + params.toString(), { cache: 'no-store' });
    const data = await r.json();
    if(requestId !== loadSessionsRequestSeq){
      return;
    }
    state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
    state.sessionsError = data.error || '';
    document.getElementById('root').textContent = data.root || '';
    applyFilter();
    if(state.activePath){
      const exists = state.sessions.some(s => s.path === state.activePath);
      if(exists){
        syncActiveSessionSummaryFromList(state.activePath);
        if(shouldSyncActiveSessionAfterListLoad(loadMode)){
          if(isAutomaticSessionsLoadMode(loadMode) && hasRecentDetailInteraction()){
            pendingAutomaticDetailSync = true;
            renderSessionList();
            renderActiveSession();
            scheduleDeferredAutomaticDetailSync();
          } else {
            pendingAutomaticDetailSync = false;
            clearDeferredDetailSyncTimer();
            await openSession(state.activePath, { mode: 'sync' });
          }
        } else {
          renderSessionList();
          renderActiveSession();
        }
      } else {
        state.activePath = null;
        state.activeSession = null;
        state.activeEvents = [];
        state.activeRawLineCount = 0;
        state.detailError = '';
        state.detailLoadMode = '';
        clearSelectedEventIds();
        pendingAutomaticDetailSync = false;
        clearDeferredDetailSyncTimer();
        renderSessionList();
        renderActiveSession();
      }
    }
  } catch (error) {
    if(requestId !== loadSessionsRequestSeq){
      return;
    }
    state.sessionsError = normalizeRequestError(error, 'セッション一覧の取得に失敗しました');
    renderSessionList();
  } finally {
    if(requestId === loadSessionsRequestSeq){
      state.isSessionsLoading = false;
      state.hasLoadedSessions = true;
      state.sessionsLoadMode = '';
      renderSessionList();
    }
  }
}

function saveFilters(){
  const payload = {
    cwd_q: document.getElementById('cwd_q').value,
    date_from: document.getElementById('date_from').value,
    date_to: document.getElementById('date_to').value,
    q: document.getElementById('q').value,
    mode: document.getElementById('mode').value,
    source_filter: document.getElementById('source_filter').value,
    session_label_filter: getSelectedSessionLabelFilter(),
    event_label_filter: getSelectedListEventLabelFilter(),
    detail_event_label_filter: getSelectedDetailEventLabelFilter(),
    detail_actions_visible: detailActionsVisible,
    left_pane_visible: leftPaneVisible,
  };
  try {
    localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(payload));
  } catch (e) {
    // Ignore storage write errors.
  }
}

function restoreFilters(){
  let raw = null;
  try {
    raw = localStorage.getItem(FILTER_STORAGE_KEY);
  } catch (e) {
    raw = null;
  }
  if(!raw) return;
  try {
    const data = JSON.parse(raw);
    if(typeof data.cwd_q === 'string') document.getElementById('cwd_q').value = data.cwd_q;
    if(typeof data.date_from === 'string') document.getElementById('date_from').value = data.date_from;
    if(typeof data.date_to === 'string') document.getElementById('date_to').value = data.date_to;
    if(typeof data.q === 'string') document.getElementById('q').value = data.q;
    if(data.mode === 'and' || data.mode === 'or') document.getElementById('mode').value = data.mode;
    const source = normalizeSourceFilter(data.source_filter || 'all');
    document.getElementById('source_filter').value = source;
    if(typeof data.session_label_filter === 'string') document.getElementById('session_label_filter').dataset.pendingValue = data.session_label_filter;
    if(typeof data.event_label_filter === 'string') document.getElementById('event_label_filter').dataset.pendingValue = data.event_label_filter;
    if(typeof data.detail_event_label_filter === 'string') document.getElementById('detail_event_label_filter').dataset.pendingValue = data.detail_event_label_filter;
    if(typeof data.detail_actions_visible === 'boolean') detailActionsVisible = data.detail_actions_visible;
    if(typeof data.left_pane_visible === 'boolean') leftPaneVisible = data.left_pane_visible;
  } catch (e) {
    // Ignore invalid saved filters.
  }
}

function clearFilters(){
  cancelScheduledSaveFilters();
  document.getElementById('cwd_q').value = '';
  document.getElementById('date_from').value = '';
  document.getElementById('date_to').value = '';
  document.getElementById('q').value = '';
  document.getElementById('mode').value = 'and';
  document.getElementById('source_filter').value = 'all';
  document.getElementById('session_label_filter').value = '';
  document.getElementById('event_label_filter').value = '';
  document.getElementById('detail_event_label_filter').value = '';
  try {
    localStorage.removeItem(FILTER_STORAGE_KEY);
  } catch (e) {
    // Ignore storage delete errors.
  }
  if(loadSessionsTimer){
    clearTimeout(loadSessionsTimer);
    loadSessionsTimer = null;
  }
  loadSessions({ mode: 'clear' });
}

function applyFilter(){
  const cwdQ = document.getElementById('cwd_q').value.toLowerCase().trim();
  const sourceFilter = normalizeSourceFilter(document.getElementById('source_filter').value || 'all');
  const fromRaw = document.getElementById('date_from').value;
  const toRaw = document.getElementById('date_to').value;
  const fromTs = parseOptionalDateStart(fromRaw);
  const toTs = parseOptionalDateEnd(toRaw);
  state.filtered = state.sessions.filter(s => {
    const cwdMatched = !cwdQ || (s.cwd || '').toLowerCase().includes(cwdQ);
    const sourceMatched = sourceFilter === 'all' || normalizeSource(s.source) === sourceFilter;

    let dateMatched = true;
    if(fromTs !== null || toTs !== null){
      const sessionTs = toTimestamp(s.started_at || s.mtime);
      if(Number.isNaN(sessionTs)){
        dateMatched = false;
      } else {
        if(fromTs !== null && sessionTs < fromTs){
          dateMatched = false;
        }
        if(toTs !== null && sessionTs > toTs){
          dateMatched = false;
        }
      }
    }

    return cwdMatched && sourceMatched && dateMatched;
  });
  saveFilters();
  renderSessionList();
}

function renderSessionList(){
  const box = document.getElementById('sessions');
  updateReloadButtonState();
  if(state.isSessionsLoading && !state.hasLoadedSessions){
    box.innerHTML = renderInlineStatus(
      'セッション一覧を読み込み中...',
      '最新のセッションを確認しています。',
      'loading'
    );
  } else if(state.sessionsError && !state.sessions.length){
    box.innerHTML = renderInlineStatus(
      '一覧の取得に失敗しました',
      state.sessionsError,
      'error'
    );
  } else if(!state.filtered.length){
    box.innerHTML = hasListFilter()
      ? renderInlineStatus(
          '条件に一致するセッションはありません',
          'フィルタ条件を見直すか、Reload を実行してください。',
          'empty'
        )
      : renderInlineStatus(
          'セッションがまだ見つかりません',
          '読み込み対象ディレクトリにセッションがあるか確認してください。',
          'empty'
        );
  } else {
    box.innerHTML = state.filtered.map(s => `
      <div class="session-item ${state.activePath === s.path ? 'active' : ''}" data-path="${esc(s.path)}">
        <div class="session-path">${highlightSessionPath(s.relative_path || '')}</div>
        <div class="session-preview">${esc(s.first_real_user_text || s.first_user_text || '(previewなし)')}</div>
        <div class="session-meta-row">
          <div class="session-badge session-time">${esc(fmt(s.started_at || s.mtime))}</div>
          <div class="session-badge session-source source-${esc(normalizeSource(s.source))}">${esc(sourceLabel(s.source))}</div>
        </div>
        <div class="session-label-row">${renderAssignedLabels(s.session_labels || [])}</div>
        <div class="session-meta-row">
          <div class="session-badge session-cwd">cwd: ${esc(s.cwd || '-')}</div>
          <div class="session-badge session-id">id: ${esc(s.session_id || s.id || '')}</div>
        </div>
      </div>
    `).join('');
  }
  if(state.isSessionsLoading && state.hasLoadedSessions && state.sessionsLoadMode === 'reload'){
    setStatusLayer(
      'sessions_status',
      '一覧を更新中...',
      '最新のセッションを再取得しています。',
      'loading'
    );
  } else {
    setStatusLayer('sessions_status');
  }
  box.querySelectorAll('.session-item').forEach(el => {
    el.onclick = () => openSession(el.dataset.path);
  });
}

function getDisplayEvents(){
  let events = state.activeEvents || [];
  if(isTurnBoundaryFilterEnabled()){
    events = filterEventsToTurnBoundaries(events);
  }
  const selectedEventLabelId = getSelectedDetailEventLabelFilter();
  if(selectedEventLabelId){
    events = events.filter(ev => (ev.labels || []).some(label => String(label.id) === selectedEventLabelId));
  }
  const showOnlyUser = document.getElementById('only_user_instruction').checked;
  const showOnlyAssistant = document.getElementById('only_ai_response').checked;
  if(showOnlyUser || showOnlyAssistant){
    events = events.filter(ev => {
      if(ev.kind !== 'message') return false;
      return (showOnlyUser && ev.role === 'user') || (showOnlyAssistant && ev.role === 'assistant');
    });
  }
  if(state.detailMessageRangeMode){
    const selectedMessage = getSelectedMessageRangeEvent();
    if(selectedMessage){
      const activeEvents = state.activeEvents || [];
      const selectedIndex = activeEvents.findIndex(ev => ev === selectedMessage);
      const rawIndexByEvent = new Map(activeEvents.map((ev, index) => [ev, index]));
      if(selectedIndex >= 0){
        events = events.filter(ev => {
          if(ev.kind !== 'message'){
            return false;
          }
          const rawIndex = rawIndexByEvent.get(ev);
          if(typeof rawIndex !== 'number'){
            return false;
          }
          if(state.detailMessageRangeMode === 'after'){
            return rawIndex >= selectedIndex;
          }
          if(state.detailMessageRangeMode === 'before'){
            return rawIndex <= selectedIndex;
          }
          return true;
        });
      }
    }
  }
  if(detailKeywordFilterTerm !== ''){
    events = events.filter(ev => containsLiteralKeyword(getEventBodyText(ev), detailKeywordFilterTerm));
  }
  if(document.getElementById('reverse_order').checked){
    events = [...events].reverse();
  }
  return events;
}

function formatCopiedMessages(events){
  return events.map(ev => {
    const role = ev.role || 'system';
    const timestamp = fmt(ev.timestamp) || ev.timestamp || '-';
    return `[${role}] ${timestamp}\n${ev.text || ''}`;
  }).join('\\n\\n-----\\n\\n');
}

async function removeSessionLabel(labelId){
  if(!state.activePath) return;
  const data = await postJson('/api/session-label/remove', {
    path: state.activePath,
    label_id: labelId,
  });
  if(data.error){
    alert(data.error);
    return;
  }
  await loadSessions({ mode: 'labels' });
}

async function addSessionLabelFromButton(button){
  if(!state.activePath) return;
  showLabelPicker(button, async (labelId) => {
    const data = await postJson('/api/session-label/add', {
      path: state.activePath,
      label_id: labelId,
    });
    if(data.error){
      alert(data.error);
      return;
    }
    await loadSessions({ mode: 'labels' });
  });
}

async function addEventLabelFromButton(button, eventId){
  if(!state.activePath || !eventId) return;
  showLabelPicker(button, async (labelId) => {
    const data = await postJson('/api/event-label/add', {
      path: state.activePath,
      event_id: eventId,
      label_id: labelId,
    });
    if(data.error){
      alert(data.error);
      return;
    }
    await loadSessions({ mode: 'labels' });
  });
}

async function removeEventLabel(eventId, labelId){
  if(!state.activePath || !eventId) return;
  const data = await postJson('/api/event-label/remove', {
    path: state.activePath,
    event_id: eventId,
    label_id: labelId,
  });
  if(data.error){
    alert(data.error);
    return;
  }
  await loadSessions({ mode: 'labels' });
}

async function copyDisplayedMessages(){
  const messages = getDisplayMessageEvents();
  if(!messages.length){
    return;
  }
  const copied = await copyTextToClipboard(formatCopiedMessages(messages));
  if(copied){
    const button = document.getElementById('copy_displayed_messages');
    flashButtonLabel(button, `${messages.length}件コピー`, '表示中メッセージコピー');
  }
}

async function copySelectedMessages(){
  const messages = getSelectedMessageEvents();
  if(!messages.length){
    return;
  }
  const copied = await copyTextToClipboard(formatCopiedMessages(messages));
  if(copied){
    const copiedCount = messages.length;
    const button = document.getElementById('copy_selected_messages');
    flashButtonLabel(button, `${copiedCount}件コピー`, '選択コピー', BUTTON_FEEDBACK_MS);
    await waitForUiFeedback(BUTTON_FEEDBACK_MS);
    state.isEventSelectionMode = false;
    clearSelectedEventIds();
    renderActiveSession();
  }
}

async function copyEventMessage(button, eventId){
  const event = (state.activeEvents || []).find(ev => ev.event_id === eventId && ev.kind === 'message');
  if(!event || !event.text){
    return;
  }
  const copied = await copyTextToClipboard(event.text);
  if(copied){
    flashButtonLabel(button, 'コピーしました', 'コピー');
  }
}

function toggleEventSelectionMode(){
  const nextEnabled = !state.isEventSelectionMode;
  state.isEventSelectionMode = nextEnabled;
  if(nextEnabled){
    state.isMessageRangeSelectionMode = false;
  } else {
    clearSelectedEventIds();
  }
  renderActiveSession();
}

function updateEventSelection(eventId, checked, card){
  const key = String(eventId || '');
  if(!key){
    return;
  }
  if(checked){
    state.selectedEventIds.add(key);
  } else {
    state.selectedEventIds.delete(key);
  }
  if(card){
    card.classList.toggle('copy-selected', checked);
  }
  updateCopySelectedMessagesButtonState();
}

function toggleMessageRangeSelectionMode(){
  const nextEnabled = !state.isMessageRangeSelectionMode;
  state.isMessageRangeSelectionMode = nextEnabled;
  if(nextEnabled){
    state.isEventSelectionMode = false;
    clearSelectedEventIds();
  }
  renderActiveSession();
}

function updateMessageRangeSelection(eventId){
  const key = String(eventId || '');
  if(!key){
    return;
  }
  noteDetailInteraction();
  state.selectedMessageRangeEventId = key;
  renderActiveSession();
}

function applyDetailMessageRange(mode){
  if(!getSelectedMessageRangeEvent()){
    return;
  }
  noteDetailInteraction();
  state.detailMessageRangeMode = mode === 'before' ? 'before' : 'after';
  const eventsBox = document.getElementById('events');
  if(eventsBox){
    eventsBox.scrollTop = 0;
  }
  renderActiveSession();
}

function clearDetailMessageRangeSelection(){
  noteDetailInteraction();
  clearMessageRangeSelection();
  renderActiveSession();
}

function applyDetailKeywordFilter(){
  noteDetailInteraction();
  detailKeywordFilterTerm = getDetailKeywordInputValue();
  const eventsBox = document.getElementById('events');
  if(eventsBox){
    eventsBox.scrollTop = 0;
  }
  renderActiveSession();
}

function runDetailKeywordSearch(){
  noteDetailInteraction();
  detailKeywordSearchTerm = getDetailKeywordInputValue();
  const searchMeta = buildDetailKeywordSearchMeta(getDisplayEvents(), detailKeywordSearchTerm);
  detailKeywordSearchTotal = searchMeta.total;
  detailKeywordCurrentMatchIndex = searchMeta.total ? 0 : -1;
  pendingDetailKeywordFocusIndex = detailKeywordCurrentMatchIndex;
  renderActiveSession();
}

function moveDetailKeywordSearch(step){
  noteDetailInteraction();
  const searchMeta = buildDetailKeywordSearchMeta(getDisplayEvents(), detailKeywordSearchTerm);
  detailKeywordSearchTotal = searchMeta.total;
  if(!searchMeta.total){
    detailKeywordCurrentMatchIndex = -1;
    pendingDetailKeywordFocusIndex = -1;
    renderActiveSession();
    return;
  }
  if(detailKeywordCurrentMatchIndex < 0 || detailKeywordCurrentMatchIndex >= searchMeta.total){
    detailKeywordCurrentMatchIndex = 0;
  } else {
    detailKeywordCurrentMatchIndex = (detailKeywordCurrentMatchIndex + step + searchMeta.total) % searchMeta.total;
  }
  pendingDetailKeywordFocusIndex = detailKeywordCurrentMatchIndex;
  renderActiveSession();
}

function clearDetailKeyword(){
  noteDetailInteraction();
  const input = document.getElementById('detail_keyword_q');
  if(input){
    input.value = '';
  }
  resetDetailKeywordState();
  renderActiveSession();
}

function clearDetailFilters(){
  noteDetailInteraction();
  document.getElementById('only_user_instruction').checked = false;
  document.getElementById('only_ai_response').checked = false;
  document.getElementById('turn_boundary_only').checked = false;
  document.getElementById('reverse_order').checked = false;
  state.detailMessageRangeMode = '';
  const detailEventLabelFilter = document.getElementById('detail_event_label_filter');
  detailEventLabelFilter.value = '';
  delete detailEventLabelFilter.dataset.pendingValue;
  saveFilters();
  renderActiveSession();
}

function renderActiveSession(){
  const meta = document.getElementById('meta');
  const eventsBox = document.getElementById('events');
  updateRefreshDetailButtonState();
  if(!state.activeSession){
    detailKeywordSearchTotal = 0;
    normalizeDetailKeywordSearchPosition({ total: 0 });
    if(state.isDetailLoading && state.activePath){
      meta.textContent = 'セッション詳細を読み込み中...';
      eventsBox.innerHTML = renderInlineStatus(
        'セッション詳細を読み込み中...',
        'イベントを取得しています。',
        'loading'
      );
    } else if(state.detailError){
      meta.textContent = state.detailError;
      eventsBox.innerHTML = renderInlineStatus(
        '詳細の取得に失敗しました',
        state.detailError,
        'error'
      );
    } else {
      meta.textContent = 'セッションを選択してください';
      eventsBox.innerHTML = '';
    }
    setStatusLayer('detail_status');
    updateCopyResumeButtonState();
    updateDisplayedMessagesCopyButtonState();
    updateEventSelectionModeButtonState();
    updateCopySelectedMessagesButtonState();
    updateMessageRangeSelectionModeButtonState();
    updateClearMessageRangeSelectionButtonState();
    updateMessageRangeFilterButtonsState();
    updateDetailKeywordControls({ total: 0 });
    renderSessionLabelStrip();
    updateSessionLabelButtonState();
    return;
  }

  syncSelectedEventIdsToActiveEvents();
  syncSelectedMessageRangeToActiveEvents();
  const displayEvents = getDisplayEvents();
  const searchMeta = buildDetailKeywordSearchMeta(displayEvents, detailKeywordSearchTerm);
  detailKeywordSearchTotal = searchMeta.total;
  normalizeDetailKeywordSearchPosition(searchMeta);
  const source = normalizeSource(state.activeSession.source);
  const eventsSummary = state.isDetailLoading && state.activeEvents.length === 0
    ? 'events: loading...'
    : `events: ${displayEvents.length}/${state.activeEvents.length}`;
  const rawSummary = state.isDetailLoading && state.activeEvents.length === 0
    ? '...'
    : state.activeRawLineCount;
  const errorNote = state.detailError
    ? ` | status: <span class="meta-note error">${esc(state.detailError)}</span>`
    : '';
  meta.innerHTML =
    `path: <code class="path-code">${highlightSessionPath(state.activeSession.relative_path)}</code> | cwd: <code class="cwd-code">${esc(state.activeSession.cwd || '-')}</code> | time: <code class="time-code">${esc(fmt(state.activeSession.started_at || state.activeSession.mtime))}</code> | source: <code class="source-code source-${esc(source)}">${esc(sourceLabel(source))}</code> | ${eventsSummary} | raw lines: ${rawSummary}${errorNote}`;

  if(state.isDetailLoading && state.activeEvents.length === 0){
    eventsBox.innerHTML = renderInlineStatus(
      'セッション詳細を読み込み中...',
      'イベントを取得しています。',
      'loading'
    );
  } else if(state.detailError && state.activeEvents.length === 0){
    eventsBox.innerHTML = renderInlineStatus(
      '詳細の取得に失敗しました',
      state.detailError,
      'error'
    );
  } else if(displayEvents.length === 0){
    eventsBox.innerHTML = state.activeEvents.length === 0
      ? renderInlineStatus(
          '表示できるイベントはありません',
          'このセッションには表示対象のイベントがありません。',
          'empty'
        )
      : renderInlineStatus(
          '条件に一致するイベントはありません',
          '表示条件を変更するとイベントが表示される可能性があります。',
          'empty'
        );
  } else {
    renderEventList(eventsBox, displayEvents, getSelectedDetailEventLabelFilter(), searchMeta);
  }
  if(state.isDetailLoading && state.activeEvents.length > 0 && state.detailLoadMode === 'refresh'){
    setStatusLayer(
      'detail_status',
      'セッション詳細を更新中...',
      '最新のイベントを再取得しています。',
      'loading'
    );
  } else {
    setStatusLayer('detail_status');
  }
  renderSessionLabelStrip();
  updateSessionLabelButtonState();
  updateDisplayedMessagesCopyButtonState();
  updateEventSelectionModeButtonState();
  updateCopySelectedMessagesButtonState();
  updateMessageRangeSelectionModeButtonState();
  updateClearMessageRangeSelectionButtonState();
  updateMessageRangeFilterButtonsState();
  updateDetailKeywordControls(searchMeta);
  updateCopyResumeButtonState();
}

async function openSession(path, options){
  const requestId = ++loadSessionDetailRequestSeq;
  const nextSession = state.sessions.find(s => s.path === path) || null;
  const previousPath = state.activeSession && state.activeSession.path ? state.activeSession.path : state.activePath;
  const loadMode = options && options.mode ? options.mode : 'open';
  if(loadMode !== 'sync'){
    pendingAutomaticDetailSync = false;
    clearDeferredDetailSyncTimer();
  }
  state.activePath = path;
  state.isDetailLoading = true;
  state.detailError = '';
  state.detailLoadMode = loadMode;
  if(nextSession){
    state.activeSession = nextSession;
  }
  if(!state.activeSession || state.activeSession.path !== path){
    state.activeSession = nextSession;
  }
  if(previousPath !== path){
    state.activeEvents = [];
    state.activeRawLineCount = 0;
    clearSelectedEventIds();
    clearMessageRangeSelection();
  }
  renderSessionList();
  renderActiveSession();
  try {
    const r = await fetch('/api/session?path=' + encodeURIComponent(path) + '&ts=' + Date.now(), { cache: 'no-store' });
    const data = await r.json();
    if(requestId !== loadSessionDetailRequestSeq){
      return;
    }
    if(data.error){
      state.detailError = data.error;
      if(!state.activeEvents.length){
        state.activeRawLineCount = 0;
      }
      return;
    }
    state.activeSession = data.session || nextSession;
    state.activeEvents = data.events || [];
    state.activeRawLineCount = data.raw_line_count || 0;
    state.detailError = '';
    syncSelectedEventIdsToActiveEvents();
    syncSelectedMessageRangeToActiveEvents();
  } catch (error) {
    if(requestId !== loadSessionDetailRequestSeq){
      return;
    }
    state.detailError = normalizeRequestError(error, 'セッション詳細の取得に失敗しました');
  } finally {
    if(requestId === loadSessionDetailRequestSeq){
      state.isDetailLoading = false;
      state.detailLoadMode = '';
      renderActiveSession();
    }
  }
}

async function refreshActiveSession(){
  if(!state.activePath) return;
  await openSession(state.activePath, { mode: 'refresh' });
}

document.getElementById('cwd_q').addEventListener('input', applyFilter);
document.getElementById('date_from').addEventListener('change', applyFilter);
document.getElementById('date_to').addEventListener('change', applyFilter);
document.getElementById('q').addEventListener('input', scheduleLoadSessions);
document.getElementById('mode').addEventListener('change', scheduleLoadSessions);
document.getElementById('source_filter').addEventListener('change', applyFilter);
document.getElementById('session_label_filter').addEventListener('change', scheduleLoadSessions);
document.getElementById('event_label_filter').addEventListener('change', scheduleLoadSessions);
document.getElementById('detail_event_label_filter').addEventListener('change', () => {
  saveFilters();
  renderActiveSession();
});
document.getElementById('toggle_filters').addEventListener('click', () => {
  setFiltersVisible(!filtersVisible);
});
document.getElementById('toggle_session_list_mobile').addEventListener('click', () => {
  setLeftPaneVisible(!leftPaneVisible);
});
document.getElementById('toggle_detail_actions').addEventListener('click', () => {
  setDetailActionsVisible(!detailActionsVisible);
});
document.getElementById('reload').addEventListener('click', () => {
  if(loadSessionsTimer){
    clearTimeout(loadSessionsTimer);
    loadSessionsTimer = null;
  }
  loadSessions({ mode: 'reload' });
});
document.getElementById('clear').addEventListener('click', clearFilters);
document.getElementById('only_user_instruction').addEventListener('change', () => {
  renderActiveSession();
});
document.getElementById('only_ai_response').addEventListener('change', () => {
  renderActiveSession();
});
document.getElementById('turn_boundary_only').addEventListener('change', () => {
  renderActiveSession();
});
document.getElementById('reverse_order').addEventListener('change', () => {
  renderActiveSession();
});
document.getElementById('clear_detail').addEventListener('click', clearDetailFilters);
document.getElementById('refresh_detail').addEventListener('click', refreshActiveSession);
document.getElementById('copy_resume_command').addEventListener('click', copyResumeCommand);
document.getElementById('copy_displayed_messages').addEventListener('click', copyDisplayedMessages);
document.getElementById('event_selection_mode').addEventListener('click', toggleEventSelectionMode);
document.getElementById('copy_selected_messages').addEventListener('click', copySelectedMessages);
document.getElementById('message_range_selection_mode').addEventListener('click', toggleMessageRangeSelectionMode);
document.getElementById('clear_message_range_selection').addEventListener('click', clearDetailMessageRangeSelection);
document.getElementById('detail_message_range_after').addEventListener('click', () => {
  applyDetailMessageRange('after');
});
document.getElementById('detail_message_range_before').addEventListener('click', () => {
  applyDetailMessageRange('before');
});
document.getElementById('detail_keyword_q').addEventListener('input', () => {
  updateDetailKeywordControls();
});
document.getElementById('detail_keyword_q').addEventListener('keydown', (event) => {
  if(event.key === 'Enter' && !event.isComposing){
    event.preventDefault();
    runDetailKeywordSearch();
  }
});
document.getElementById('detail_keyword_filter').addEventListener('click', applyDetailKeywordFilter);
document.getElementById('detail_keyword_search').addEventListener('click', runDetailKeywordSearch);
document.getElementById('detail_keyword_prev').addEventListener('click', () => {
  moveDetailKeywordSearch(-1);
});
document.getElementById('detail_keyword_next').addEventListener('click', () => {
  moveDetailKeywordSearch(1);
});
document.getElementById('detail_keyword_clear').addEventListener('click', clearDetailKeyword);
document.getElementById('add_session_label').addEventListener('click', async (event) => {
  await addSessionLabelFromButton(event.currentTarget);
});
document.getElementById('events').addEventListener('pointerdown', (event) => {
  if(event.target.closest('pre')){
    detailPointerDown = true;
    noteDetailInteraction();
  }
});
window.addEventListener('pointerup', () => {
  if(!detailPointerDown){
    return;
  }
  detailPointerDown = false;
  noteDetailInteraction();
  scheduleDeferredAutomaticDetailSync();
});
document.addEventListener('selectionchange', () => {
  if(hasDetailTextSelection()){
    noteDetailInteraction();
    return;
  }
  scheduleDeferredAutomaticDetailSync();
});
document.getElementById('open_label_manager').addEventListener('click', openLabelManagerWindow);
document.addEventListener('click', (event) => {
  const picker = document.getElementById('label_picker');
  if(picker.classList.contains('hidden')) return;
  if(picker.contains(event.target)) return;
  if(event.target.closest('.event-label-add-button')) return;
  if(event.target.closest('#add_session_label')) return;
  hideLabelPicker();
});
window.addEventListener('message', async (event) => {
  if(!event.data || event.data.type !== 'labels-updated') return;
  await loadLabels(false);
  await loadSessions({ mode: 'labels' });
});
window.addEventListener('focus', async () => {
  await loadLabels(false);
  await loadSessions({ mode: 'focus' });
});
window.addEventListener('resize', () => {
  updateLeftPaneVisibility();
});
updateCopyResumeButtonState();
updateDisplayedMessagesCopyButtonState();
updateEventSelectionModeButtonState();
updateCopySelectedMessagesButtonState();
updateMessageRangeSelectionModeButtonState();
updateClearMessageRangeSelectionButtonState();
updateMessageRangeFilterButtonsState();
updateDetailKeywordControls({ total: 0 });
updateRefreshDetailButtonState();
updateFilterVisibility();
restoreFilters();
updateLeftPaneVisibility();
updateDetailActionsVisibility();
state.isSessionsLoading = true;
renderSessionList();
loadLabels(false)
  .catch(() => {})
  .finally(() => loadSessions({ mode: 'initial' }));
</script>
</body>
</html>
"""


LABELS_PAGE = """<!doctype html>
<html lang=\"ja\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>ラベル管理</title>
<style>
:root {
  --bg: #f5f8ff;
  --panel: rgba(255, 255, 255, 0.78);
  --panel-strong: rgba(255, 255, 255, 0.94);
  --line: rgba(148, 163, 184, 0.28);
  --line-strong: rgba(148, 163, 184, 0.52);
  --text: #0f172a;
  --muted: #546277;
  --accent: #0f766e;
  --accent-strong: #0b5c57;
  --accent-soft: rgba(15, 118, 110, 0.12);
  --danger: #be123c;
  --shadow: 0 28px 70px rgba(15, 23, 42, 0.14);
  --shadow-soft: 0 16px 36px rgba(15, 23, 42, 0.1);
}
* { box-sizing: border-box; }
html, body { min-height: 100%; }
body {
  margin: 0;
  position: relative;
  overflow-x: hidden;
  font-family: "Aptos", "Segoe UI", "Yu Gothic UI", sans-serif;
  background:
    radial-gradient(circle at 12% 18%, rgba(59, 130, 246, 0.18), transparent 24%),
    radial-gradient(circle at 88% 14%, rgba(15, 118, 110, 0.16), transparent 22%),
    linear-gradient(180deg, #eef6ff 0%, #f8fbff 54%, #eef4fb 100%);
  color: var(--text);
}
body::before,
body::after {
  content: "";
  position: fixed;
  width: 320px;
  height: 320px;
  border-radius: 999px;
  filter: blur(36px);
  pointer-events: none;
  opacity: 0.55;
}
body::before {
  top: -120px;
  left: -90px;
  background: rgba(96, 165, 250, 0.22);
}
body::after {
  right: -120px;
  bottom: -140px;
  background: rgba(16, 185, 129, 0.18);
}
.page {
  position: relative;
  z-index: 1;
  max-width: 980px;
  margin: 0 auto;
  padding: 40px 20px 52px;
}
.page-header {
  margin-bottom: 20px;
}
.eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.78);
  background: rgba(255, 255, 255, 0.72);
  color: #0f5a73;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.08);
}
.hero-title {
  margin: 14px 0 0;
  font-size: 38px;
  line-height: 1.08;
  letter-spacing: -0.03em;
}
.hero-copy {
  margin-top: 12px;
  max-width: 760px;
  color: var(--muted);
  font-size: 15px;
  line-height: 1.7;
}
.panel {
  position: relative;
  overflow: hidden;
  background: var(--panel);
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: 28px;
  padding: 24px;
  box-shadow: var(--shadow);
  backdrop-filter: blur(18px);
}
.panel::before {
  content: "";
  position: absolute;
  inset: 0 0 auto 0;
  height: 110px;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.42), transparent);
  pointer-events: none;
}
.panel + .panel {
  margin-top: 20px;
}
.editor-panel {
  padding: 20px 20px 18px;
}
.list-panel {
  padding: 18px 18px 12px;
}
.panel-head,
.list-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}
.editor-panel .panel-head {
  align-items: flex-start;
  margin-bottom: 12px;
}
.editor-panel .panel-title {
  margin-top: 4px;
  font-size: 22px;
}
.editor-panel .panel-copy {
  margin-top: 4px;
  max-width: 520px;
  font-size: 13px;
  line-height: 1.55;
}
.editor-panel .panel-chip {
  align-self: flex-start;
  margin-top: 2px;
  padding: 6px 10px;
  font-size: 11px;
}
.list-head {
  align-items: center;
  margin-bottom: 10px;
}
.list-head > div:first-child {
  min-width: 0;
}
.list-head .panel-title {
  margin-top: 4px;
  font-size: 22px;
}
.list-head .panel-chip {
  padding: 6px 10px;
  font-size: 11px;
  align-self: center;
}
.panel-kicker {
  color: #0f5a73;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.panel-title {
  margin-top: 8px;
  font-size: 24px;
  line-height: 1.15;
  letter-spacing: -0.02em;
}
.panel-copy,
.muted {
  color: var(--muted);
  font-size: 14px;
  line-height: 1.7;
}
.panel-chip {
  flex: 0 0 auto;
  align-self: center;
  padding: 8px 12px;
  border-radius: 999px;
  border: 1px solid rgba(15, 118, 110, 0.12);
  background: rgba(15, 118, 110, 0.08);
  color: var(--accent-strong);
  font-size: 12px;
  font-weight: 700;
}
.form-grid {
  display: grid;
  grid-template-columns: 1.4fr 1fr 1.1fr auto;
  gap: 14px;
  align-items: end;
}
.editor-panel .form-grid {
  gap: 10px;
}
label {
  display: grid;
  gap: 8px;
  font-size: 12px;
  color: #475569;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
input, button {
  font-family: inherit;
  font-size: 14px;
}
input {
  min-height: 48px;
  border: 1px solid var(--line-strong);
  border-radius: 16px;
  padding: 12px 14px;
  background: rgba(255, 255, 255, 0.86);
  color: var(--text);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
}
input::placeholder {
  color: #94a3b8;
}
input:focus {
  outline: none;
  border-color: rgba(15, 118, 110, 0.5);
  box-shadow: 0 0 0 4px rgba(15, 118, 110, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.8);
}
button {
  min-height: 48px;
  border: 0;
  border-radius: 16px;
  padding: 0 20px;
  background: linear-gradient(135deg, var(--accent) 0%, #16938a 100%);
  color: #ffffff;
  cursor: pointer;
  font-weight: 700;
  letter-spacing: 0.01em;
  box-shadow: 0 8px 18px rgba(15, 118, 110, 0.16);
  transition: transform 0.18s ease, box-shadow 0.18s ease, opacity 0.18s ease;
}
button:hover {
  transform: translateY(-1px);
  box-shadow: 0 12px 24px rgba(15, 118, 110, 0.18);
}
button:active {
  transform: translateY(0);
  box-shadow: 0 5px 12px rgba(15, 118, 110, 0.14);
}
.secondary {
  background: linear-gradient(135deg, #64748b 0%, #475569 100%);
  box-shadow: 0 8px 18px rgba(71, 85, 105, 0.14);
}
.danger {
  background: linear-gradient(135deg, var(--danger) 0%, #e11d48 100%);
  box-shadow: 0 8px 18px rgba(190, 18, 60, 0.14);
}
.preset-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.preset-field {
  display: grid;
  gap: 8px;
  align-self: stretch;
}
.preset-field-title {
  font-size: 12px;
  color: #475569;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.badge {
  --label-color: #94a3b8;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: 1px solid rgba(148, 163, 184, 0.3);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.9);
  padding: 6px 10px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.78);
}
.preset-list.inline {
  margin-top: 0;
}
.preset-badge {
  min-height: 28px;
  color: #334155;
  background: rgba(255, 255, 255, 0.72);
  border-color: rgba(148, 163, 184, 0.24);
  border-radius: 10px;
  padding: 0 8px;
  font-weight: 600;
  box-shadow: none;
}
.preset-badge.active {
  border-color: var(--label-color);
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.1), 0 10px 18px rgba(15, 23, 42, 0.06);
}
.preset-badge .dot {
  width: 7px;
  height: 7px;
  box-shadow: none;
}
.badge .dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--label-color);
  box-shadow: 0 0 0 3px rgba(148, 163, 184, 0.14);
}
.label-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
  padding: 0 22px 0 8px;
}
.label-row {
  border: 1px solid rgba(226, 232, 240, 0.92);
  border-radius: 18px;
  padding: 12px 14px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.96), rgba(247, 250, 255, 0.92));
  box-shadow: var(--shadow-soft);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.label-row:hover {
  transform: translateY(-1px);
  box-shadow: 0 20px 40px rgba(15, 23, 42, 0.12);
}
.label-main {
  display: block;
  min-width: 0;
}
.label-topline {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
}
.label-badge {
  width: fit-content;
  max-width: 100%;
  color: #1e293b;
  background: #ffffff;
  border-color: var(--label-color);
  padding: 6px 10px 6px 9px;
  font-size: 13px;
}
.label-badge .dot {
  width: 10px;
  height: 10px;
  flex: 0 0 auto;
  box-shadow: none;
  opacity: 1;
  filter: none;
}
.label-meta {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-left: 4px;
  font-size: 14px;
  color: var(--muted);
}
.label-meta-prefix {
  color: #64748b;
  font-size: 12px;
}
.label-code {
  display: inline-flex;
  align-items: center;
  padding: 5px 10px;
  margin-left: 0;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, 0.24);
  background: rgba(238, 246, 255, 0.9);
  color: #0f3d57;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  font-size: 13px;
}
.label-row-actions {
  display: flex;
  gap: 6px;
  flex-wrap: nowrap;
  align-items: center;
  justify-content: flex-end;
}
.label-row-actions button {
  min-height: 34px;
  border-radius: 12px;
  padding: 0 12px;
  font-size: 12px;
  box-shadow: none;
}
.label-row-actions button:hover {
  box-shadow: 0 6px 12px rgba(15, 23, 42, 0.08);
}
.dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(15, 23, 42, 0.48);
  backdrop-filter: blur(12px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}
.dialog-backdrop.hidden {
  display: none;
}
.dialog {
  position: relative;
  overflow: hidden;
  z-index: 1;
  width: min(420px, 100%);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.97), rgba(248, 251, 255, 0.94));
  border: 1px solid rgba(255, 255, 255, 0.78);
  border-radius: 26px;
  box-shadow: 0 30px 70px rgba(15, 23, 42, 0.28);
  padding: 24px;
}
.dialog::before {
  content: "";
  position: absolute;
  inset: 0 0 auto 0;
  height: 6px;
  background: linear-gradient(90deg, #fb7185 0%, #f59e0b 52%, #22c55e 100%);
}
.dialog-kicker {
  color: #be123c;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.dialog-title {
  margin: 8px 0 0;
  font-size: 24px;
  letter-spacing: -0.02em;
}
.dialog-message {
  margin-top: 12px;
  color: #334155;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.dialog-actions {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
}
.empty-state {
  border: 1px dashed rgba(148, 163, 184, 0.4);
  border-radius: 22px;
  padding: 26px;
  text-align: center;
  background: rgba(255, 255, 255, 0.56);
  color: var(--muted);
}
@media (max-width: 760px) {
  .page {
    padding: 28px 16px 40px;
  }
  .hero-title {
    font-size: 32px;
  }
  .panel {
    padding: 20px;
  }
  .form-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 560px) {
  .panel-head {
    flex-direction: column;
    align-items: flex-start;
  }
  .label-row {
    grid-template-columns: 1fr;
    align-items: start;
  }
  .label-row-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
</head>
<body>
<div class="page">
  <div class="page-header">
    <div class="eyebrow">GitHub Copilot Sessions Viewer</div>
    <h1 class="hero-title">ラベル管理</h1>
    <div class="hero-copy">セッションとイベントに共通で使うラベルをここで整えます。色コードを直接入力するか、プリセットをクリックして素早く設定できます。</div>
  </div>
  <div class="panel editor-panel">
    <div class="panel-head">
      <div>
        <div class="panel-kicker">Label Editor</div>
        <div class="panel-title">新規作成 / 編集</div>
        <div class="panel-copy">保存すると一覧フィルタと詳細画面の両方にすぐ反映されます。</div>
      </div>
      <div class="panel-chip">即時反映</div>
    </div>
    <div class="form-grid">
      <label>
        ラベル名
        <input id="label_name" placeholder="例: README / 画像 / 再確認" />
      </label>
      <label>
        色コード
        <input id="label_color" placeholder="#3b82f6 / rgb(...) / oklch(...)" />
      </label>
      <div class="preset-field">
        <div class="preset-field-title">色プリセット</div>
        <div class="preset-list inline" id="preset_preview"></div>
      </div>
      <button id="save_label">保存</button>
    </div>
    <input id="label_id" type="hidden" />
    <input id="label_family" type="hidden" />
  </div>

  <div class="panel list-panel">
    <div class="list-head">
      <div>
        <div class="panel-kicker">Registered Labels</div>
        <div class="panel-title">既存ラベル</div>
      </div>
      <div class="panel-chip" id="label_count_badge">0 labels</div>
    </div>
    <div class="label-list" id="label_list"></div>
  </div>
</div>
<div id="error_dialog" class="dialog-backdrop hidden">
  <div class="dialog" role="alertdialog" aria-modal="true" aria-labelledby="error_dialog_title">
    <div class="dialog-kicker" id="error_dialog_kicker">入力チェック</div>
    <h2 class="dialog-title" id="error_dialog_title">入力エラー</h2>
    <div class="dialog-message" id="error_dialog_message"></div>
    <div class="dialog-actions">
      <button id="error_dialog_close" type="button">閉じる</button>
    </div>
  </div>
</div>
<script>
const PRESETS = {
  red: { label: '赤系', color: '#ef4444' },
  blue: { label: '青系', color: '#3b82f6' },
  green: { label: '緑系', color: '#22c55e' },
  yellow: { label: '黄色系', color: '#eab308' },
  purple: { label: '紫系', color: '#a855f7' },
};

function esc(s){
  return (s ?? '').toString().replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
}

function badgeHtml(label){
  return `<span class="badge label-badge" style="--label-color:${esc(label.color_value)}"><span class="dot"></span><span>${esc(label.name)}</span></span>`;
}

function showErrorDialog(message, title){
  document.getElementById('error_dialog_title').textContent = title || '入力エラー';
  document.getElementById('error_dialog_kicker').textContent = title === 'エラー' ? 'エラーメッセージ' : '入力チェック';
  document.getElementById('error_dialog_message').textContent = message || '';
  document.getElementById('error_dialog').classList.remove('hidden');
}

function hideErrorDialog(){
  document.getElementById('error_dialog').classList.add('hidden');
}

function notifyParent(){
  if(window.opener && !window.opener.closed){
    window.opener.postMessage({ type: 'labels-updated' }, '*');
  }
}

async function postJson(url, payload){
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  return r.json();
}

function renderPresetPreview(){
  const box = document.getElementById('preset_preview');
  const selectedFamily = document.getElementById('label_family').value || '';
  box.innerHTML = Object.entries(PRESETS).map(([key, value]) =>
    `<button type="button" class="badge preset-badge ${selectedFamily === key ? 'active' : ''}" data-family="${esc(key)}" data-color="${esc(value.color)}" style="--label-color:${esc(value.color)}"><span class="dot"></span><span>${esc(value.label)}</span></button>`
  ).join('');
  box.querySelectorAll('.preset-badge').forEach(button => {
    button.onclick = () => {
      document.getElementById('label_color').value = button.dataset.color || '';
      document.getElementById('label_family').value = button.dataset.family || '';
      renderPresetPreview();
    };
  });
}

function resetForm(){
  document.getElementById('label_id').value = '';
  document.getElementById('label_name').value = '';
  document.getElementById('label_color').value = '';
  document.getElementById('label_family').value = '';
  renderPresetPreview();
}

function editLabel(label){
  document.getElementById('label_id').value = label.id;
  document.getElementById('label_name').value = label.name;
  document.getElementById('label_color').value = label.color_value;
  document.getElementById('label_family').value = label.color_family || '';
  renderPresetPreview();
}

async function deleteLabel(id){
  if(!confirm('このラベルを削除しますか？')) return;
  const data = await postJson('/api/labels/delete', { id });
  if(data.error){
    showErrorDialog(data.error, 'エラー');
    return;
  }
  notifyParent();
  await loadLabels();
  resetForm();
}

async function loadLabels(){
  const r = await fetch('/api/labels?ts=' + Date.now(), { cache: 'no-store' });
  const data = await r.json();
  const list = document.getElementById('label_list');
  const countBadge = document.getElementById('label_count_badge');
  const count = (data.labels || []).length;
  countBadge.textContent = `${count} label${count === 1 ? '' : 's'}`;
  if(!data.labels || !data.labels.length){
    list.innerHTML = '<div class="empty-state">ラベルはまだありません。上のフォームから最初のラベルを作成してください。</div>';
    return;
  }
  list.innerHTML = data.labels.map(label => `
    <div class="label-row">
      <div class="label-main">
        <div class="label-topline">
          ${badgeHtml(label)}
          <div class="label-meta"><span class="label-meta-prefix">color</span><span class="label-code">${esc(label.color_value)}</span>${label.color_family_label ? ' / ' + esc(label.color_family_label) : ''}</div>
        </div>
      </div>
      <div class="label-row-actions">
        <button class="secondary edit-label" data-label-id="${esc(label.id)}">編集</button>
        <button class="danger delete-label" data-label-id="${esc(label.id)}">削除</button>
      </div>
    </div>
  `).join('');
  list.querySelectorAll('.edit-label').forEach(button => {
    button.onclick = () => {
      const label = data.labels.find(item => String(item.id) === button.dataset.labelId);
      if(label) editLabel(label);
    };
  });
  list.querySelectorAll('.delete-label').forEach(button => {
    button.onclick = async () => {
      await deleteLabel(Number(button.dataset.labelId));
    };
  });
}

document.getElementById('save_label').addEventListener('click', async () => {
  const payload = {
    id: document.getElementById('label_id').value || null,
    name: document.getElementById('label_name').value,
    color_value: document.getElementById('label_color').value,
    color_family: document.getElementById('label_family').value,
  };
  const data = await postJson('/api/labels/save', payload);
  if(data.error){
    showErrorDialog(data.error, '入力エラー');
    return;
  }
  notifyParent();
  await loadLabels();
  resetForm();
});

document.getElementById('error_dialog_close').addEventListener('click', hideErrorDialog);
document.getElementById('error_dialog').addEventListener('click', (event) => {
  if(event.target.id === 'error_dialog'){
    hideErrorDialog();
  }
});
document.addEventListener('keydown', (event) => {
  if(event.key === 'Escape'){
    hideErrorDialog();
  }
});
document.getElementById('label_color').addEventListener('input', () => {
  const color = document.getElementById('label_color').value.trim().toLowerCase();
  const matched = Object.entries(PRESETS).find(([, value]) => value.color.toLowerCase() === color);
  document.getElementById('label_family').value = matched ? matched[0] : '';
  renderPresetPreview();
});
renderPresetPreview();
loadLabels();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send_raw(self, raw: bytes, content_type: str, status=200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def _send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send_raw(raw, "application/json; charset=utf-8", status)

    def _send_html(self, text, status=200):
        raw = text.encode("utf-8")
        self._send_raw(raw, "text/html; charset=utf-8", status)

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_html(HTML_PAGE)
            return
        if parsed.path == "/labels":
            self._send_html(LABELS_PAGE)
            return
        if parsed.path == "/api/labels":
            self._send_json({"labels": list_labels()})
            return
        if parsed.path == "/api/sessions":
            roots = get_session_roots()
            files = iter_all_session_files(roots)[:MAX_LIST]
            q = urllib.parse.parse_qs(parsed.query)
            raw_query = (q.get("q", [""])[0] or "").strip()
            mode = q.get("mode", ["and"])[0]
            if mode not in ("and", "or"):
                mode = "and"
            session_label_id = parse_optional_int(q.get("session_label_id", [""])[0])
            event_label_id = parse_optional_int(q.get("event_label_id", [""])[0])
            sync_search_index(files, prune_missing=True)
            sessions = fetch_sessions_from_search_index(
                raw_query,
                mode,
                MAX_LIST,
                session_label_id=session_label_id,
                event_label_id=event_label_id,
            )
            self._send_json({"root": " | ".join(str(x) for x in roots), "sessions": sessions})
            return
        if parsed.path == "/api/session":
            q = urllib.parse.parse_qs(parsed.query)
            raw_path = q.get("path", [""])[0]
            try:
                p = resolve_session_path(raw_path)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
                return
            if not p.exists() or not p.is_file():
                self._send_json({"error": "session file not found"}, 404)
                return

            sync_search_index([p], prune_missing=False)
            stat_result, signature = get_session_signature(p)
            session = fetch_session_summary_from_index(session_path_key(p)) or summarize_session(
                p,
                stat_result=stat_result,
                signature=signature,
            )
            data = load_session_events(p, stat_result=stat_result, signature=signature)
            data["session"] = session
            self._send_json(data)
            return

        self._send_html("<h1>404</h1>", 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = parse_json_body(self)
            if parsed.path == "/api/labels/save":
                label_id = parse_optional_int(body.get("id"))
                label = save_label(label_id, body.get("name", ""), body.get("color_value", ""), body.get("color_family", ""))
                self._send_json({"label": label})
                return
            if parsed.path == "/api/labels/delete":
                label_id = parse_optional_int(body.get("id"))
                if label_id is None:
                    self._send_json({"error": "label id is required"}, 400)
                    return
                delete_label(label_id)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/session-label/add":
                raw_path = (body.get("path", "") or "").strip()
                label_id = parse_optional_int(body.get("label_id"))
                if not raw_path or label_id is None:
                    self._send_json({"error": "path and label id are required"}, 400)
                    return
                assign_session_label(resolve_session_path(raw_path), label_id)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/session-label/remove":
                raw_path = (body.get("path", "") or "").strip()
                label_id = parse_optional_int(body.get("label_id"))
                if not raw_path or label_id is None:
                    self._send_json({"error": "path and label id are required"}, 400)
                    return
                remove_session_label(resolve_session_path(raw_path), label_id)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/event-label/add":
                raw_path = (body.get("path", "") or "").strip()
                event_id = (body.get("event_id", "") or "").strip()
                label_id = parse_optional_int(body.get("label_id"))
                if not raw_path or not event_id or label_id is None:
                    self._send_json({"error": "path, event id and label id are required"}, 400)
                    return
                assign_event_label(resolve_session_path(raw_path), event_id, label_id)
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/event-label/remove":
                raw_path = (body.get("path", "") or "").strip()
                event_id = (body.get("event_id", "") or "").strip()
                label_id = parse_optional_int(body.get("label_id"))
                if not raw_path or not event_id or label_id is None:
                    self._send_json({"error": "path, event id and label id are required"}, 400)
                    return
                remove_event_label(resolve_session_path(raw_path), event_id, label_id)
                self._send_json({"ok": True})
                return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
            return

        self._send_json({"error": "not found"}, 404)


def main():
    roots = get_session_roots()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Viewer: http://{HOST}:{PORT}", flush=True)
    for root in roots:
        print(f"Sessions dir: {root}", flush=True)
    if not any(root.exists() for root in roots):
        print("WARNING: sessions dirs do not exist. Set SESSIONS_DIR or COPILOT_SESSIONS_DIR.", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
