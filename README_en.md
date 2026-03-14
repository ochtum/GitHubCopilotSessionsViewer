<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# GitHub Copilot Sessions Viewer

A local viewer for browsing GitHub Copilot sessions on Windows and WSL.  
It can display GitHub Copilot CLI sessions and VS Code chat history in a single list.

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
- `~/.copilot/session-state` (GitHub Copilot CLI on WSL / Linux)
- `~/.vscode-server/data/User/workspaceStorage` (VS Code Server on WSL)
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

- Left pane: session list, sorted newest first
  - Shows session `source` labels (`CLI` / `VS Code`) and session labels in the list
  - Shows a loading state during the initial load
  - `Reload` reloads the session list
    - During a manual `Reload`, the list shows an updating overlay and button state feedback
  - `Clear` resets the left-pane search conditions
  - `Hide` / `Show` collapses or expands the search filter area
  - In vertical layout, the header button `Hide List` / `Show List` can hide or show the entire left pane
- Top-left filters
  - Filter by `cwd` / date / keyword / `source` / session label / event label
  - Keyword search uses a SQLite-backed search index
  - Search covers not only `message`, but also `function_call.arguments`, `tool_start.arguments`, `tool_output`, `assistant.turn_*`, `info`, and `error`
  - `cwd`, date, `source`, and label conditions are always evaluated with AND
  - The `AND/OR` switch applies only to the keyword field
    - `AND`: must include all space-separated keywords
    - `OR`: must include at least one space-separated keyword
- Right pane: chronological event view for the selected session
  - Shows a loading state during the first detail load, and an updating overlay during manual `Refresh`
  - The detail header shows the `source` label (`CLI` / `VS Code`)
  - The detail header uses a 3-row layout
    - Row 1: display filters, `Refresh`, and `Hide` / `Show` to collapse rows 2 and 3 together
    - Row 2: copy actions, label actions, and selection-copy actions
    - Row 3: keyword input, `Filter`, `Search`, `Previous`, `Next`, and `Keyword Clear`
  - Display options
    - `Show only user instructions`
    - `Show only AI responses`
    - `Show only each input and final response`
      - For each turn, keeps one `user` message and only the last `assistant` message before the next `user`
    - `Reverse display order`
    - `event label: all` filter
  - Keyword search
    - `Filter`: shows only events that contain the keyword
    - `Search`: highlights matches and lets you move through them with `Previous` / `Next`
    - `Keyword Clear`: clears the input, filter state, and search state together
    - Matching is a literal substring match, not AND / OR parsing
    - Search targets include `message`, `function_call`, `tool_start`, `tool_output`, `info`, `error`, and `assistant.turn_*`
  - `Refresh` reloads only the currently selected session
  - `Copy Resume Command` copies `copilot --resume <session_id>`
  - `Copy Displayed Messages` copies all messages currently visible under the active display filters
  - Session label display and `Add Session Label`
  - Per-event label display / add / remove
  - Each `message` event has its own `Copy` button
  - `Selection Mode` lets you check individual `message` events and copy them together with `Copy Selected`
    - Even when filters are applied, already selected `message` events remain selected
  - `message` (`user` / `assistant` / `system`)
  - `function_call` / `tool_start` / `tool_output`
  - `info` / `error` / `assistant.turn_*`
- Label Manager
  - Opens in a separate window from the `Label Manager` button in the upper-right
  - Manages session labels and event labels in one shared UI
  - Label colors can be entered directly as `#hex`, `rgb(...)`, or `oklch(...)`, or selected from color presets

## Notes

- The search index is stored in `.cache/search_index.sqlite3` and only changed sessions are re-indexed.
- On Windows, `viewer.py` runs `wsl.exe -l -q` and scans Copilot / VS Code Server session data under each distro home.
- To limit which distros are auto-detected, set `COPILOT_WSL_DISTROS` (for example: `Ubuntu;Debian`).
- To keep the UI responsive with large logs, the list is capped at `300` sessions and the detail view is capped at `3000` events.
- By default, the viewer listens only on `127.0.0.1` for local use.

## License

This project is provided under the MIT License. See the `LICENSE` file for details.
