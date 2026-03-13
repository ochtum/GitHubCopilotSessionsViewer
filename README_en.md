<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# GitHub Copilot Sessions Viewer

A local viewer for browsing GitHub Copilot sessions on Windows and WSL.  
It can display GitHub Copilot CLI sessions, VS Code chat history, and Cloud sessions generated from auxiliary data in a single list.

## Screen Layout

### Main Screen

![image](/image/00001.jpg)

### Label Manager Screen

![image](/image/00002.jpg)

## Directory Layout

```text
.
├─ viewer.py
└─ scripts
   └─ windows
      ├─ launch_viewer.bat
      └─ stop_viewer.bat
```

## Prerequisites

- Python 3 (`py -3` or `python` must be available)
- A web browser (Edge, Chrome, etc.)

If Python 3 is not installed (Windows / winget):

```powershell
winget install -e --id Python.Python.3.12
```

Verify installation:

```powershell
py -3 --version
```

## Launch

### One-click Launch on Windows

- `scripts\windows\launch_viewer.bat`
- `scripts\windows\stop_viewer.bat`

`launch_viewer.bat` starts `viewer.py` directly on Windows, waits for the server to become ready, and then opens the browser.

Open the following URL after launch:

```text
http://127.0.0.1:8766
```

### Launch Directly with Python

```powershell
python viewer.py
```

## Default Scan Paths

- `%USERPROFILE%\.copilot\session-state` (GitHub Copilot CLI)
- `%APPDATA%\Code\User\workspaceStorage\*\chatSessions\*.jsonl` (VS Code extension chat history)
- `%APPDATA%\Code\User\globalStorage\github.copilot-chat` (auxiliary data)
- `~/.copilot/session-state` (GitHub Copilot CLI on WSL / Linux)
- `~/.vscode-server/data/User/workspaceStorage` (VS Code Server on WSL)
- `~/.vscode-server/data/User/globalStorage/github.copilot-chat` (VS Code Server auxiliary data on WSL)
- `\\wsl.localhost\<distro>\home\<user>\...` (auto-detected when launched on Windows)

## Options

To use custom session directories, set `SESSIONS_DIR`.  
You can also override with `COPILOT_SESSIONS_DIR`. Multiple roots are separated with `os.pathsep` (`;` on Windows, `:` on Unix/WSL).

```powershell
$env:SESSIONS_DIR = 'C:\path\to\session-state'
python viewer.py
```

To change the bind address, set `HOST`.

```powershell
$env:HOST = '0.0.0.0'
python viewer.py
```

## UI Features

- Left pane: session list in reverse chronological order
  - Shows session `source` labels (`CLI` / `VS Code` / `Cloud`) and session labels in the list
  - `Reload` reloads the session list
  - `Clear` resets the search conditions in the left pane
  - `Hide` / `Show` collapses or expands the filter area
- Top-left filters
  - Filter by `cwd`, date range, keyword, `source`, session label, and event label
  - Keyword search uses a SQLite-backed search index
  - Search targets include `message`, `function_call.arguments`, `tool_output`, `assistant.turn_*`, `info`, and `error`
  - `cwd`, date range, `source`, and label filters are always evaluated with AND
  - `AND/OR` only affects the keyword field
    - `AND`: all space-separated keywords must match
    - `OR`: any space-separated keyword may match
- Right pane: event timeline for the selected session
  - The detail header shows the `source` label (`CLI` / `VS Code` / `Cloud`)
  - Display options:
    - `Show only user instructions`
    - `Show only AI responses`
    - `Reverse display order`
    - `event label: all` filter
  - `Refresh` reloads only the selected session
  - `Copy Resume Command` copies `copilot --resume <session_id>`
  - Shows session labels and `Add Session Label`
  - Shows, adds, and removes labels for each event
  - Displays `message` (`user` / `assistant` / `system`)
  - Displays `function_call`, `tool_start`, and `tool_output`
  - Displays `info`, `error`, and `assistant.turn_*`
- Label manager
  - Opens in a separate window from the `Label Manager` button in the top-right corner
  - Manages session labels and event labels in one place
  - Label colors can be entered directly as `#hex`, `rgb(...)`, or `oklch(...)`, or selected from color presets

## Notes

- The search index is stored in `.cache/search_index.sqlite3` and only changed sessions are re-indexed.
- On Windows, `viewer.py` runs `wsl.exe -l -q` and scans Copilot / VS Code Server session data under each distro home.
- To limit which distros are auto-detected, set `COPILOT_WSL_DISTROS` (for example: `Ubuntu;Debian`).
- To keep the UI responsive with large logs, the list is capped at `300` sessions and the detail view is capped at `3000` events.
- By default, the viewer listens only on `127.0.0.1` for local use.

## License

This project is provided under the MIT License. See the `LICENSE` file for details.
