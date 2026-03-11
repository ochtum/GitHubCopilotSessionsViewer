<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# GitHub Copilot Sessions Viewer

A local viewer that reads GitHub Copilot session data on Windows and WSL, then displays session lists and details.

![image](/image/00001.jpg)

## Prerequisites

- Python 3 (`py -3` or `python` command must be available)
- A web browser (Edge, Chrome, etc.)

If Python 3 is not installed (Windows / winget):

```powershell
winget install -e --id Python.Python.3.12
```

Verify installation:

```powershell
py -3 --version
```

## Launch on Windows

- `scripts\windows\launch_viewer.bat`
- `scripts\windows\stop_viewer.bat`

`launch_viewer.bat` starts `viewer.py` directly on Windows, waits until the server is ready, and then opens your browser automatically.

Default URL:

```text
http://127.0.0.1:8766
```

## Launch Directly with Python

```powershell
python viewer.py
```

## UI Features

- Left pane: session list (newest first)
- Session `source` labels (`CLI` / `VS Code` / `Cloud`) are shown in the list
- Top-left filters: narrow down by `cwd`, date range, keyword, and `source`
- Search is partial match against `relative_path`, first user input, and summary/search text
- `cwd` / date range / keyword / `source` are always combined with AND
- `AND/OR` switch applies only within the keyword field
  - `AND`: must include all space-separated keywords
  - `OR`: must include at least one space-separated keyword
- Right pane: timeline of events for the selected session
  - The detail header also shows the `source` label (`CLI` / `VS Code` / `Cloud`)
  - Display options
    - `Show only user instructions`
    - `Show only AI responses`
    - `Reverse display order`
  - `Copy Resume Command` copies `copilot --resume session-id`
  - On successful copy, the button text temporarily changes to `コピーしました`
  - `message` (`user` / `assistant` / `system`)
  - Includes `function_call` / `tool_start` / `tool_output` / `info` / `error` / `assistant.turn_*`

## Default Session Directory

- `%USERPROFILE%\.copilot\session-state` (GitHub Copilot CLI)
- `%APPDATA%\Code\User\workspaceStorage\*\chatSessions\*.jsonl` (VS Code extension chat history)
- `%APPDATA%\Code\User\globalStorage\github.copilot-chat` (auxiliary data)
- `~/.copilot/session-state` (GitHub Copilot CLI on WSL / Linux)
- `~/.vscode-server/data/User/workspaceStorage` (VS Code Server on WSL)
- `~/.vscode-server/data/User/globalStorage/github.copilot-chat` (VS Code Server auxiliary data on WSL)
- `\\wsl.localhost\<distro>\home\<user>\...` (auto-detected when launched on Windows)

To use a custom directory:

```powershell
$env:SESSIONS_DIR = 'C:\path\to\session-state'
python viewer.py
```

Notes:

- `COPILOT_SESSIONS_DIR` can also override the roots.
- Multiple paths are separated by `os.pathsep` (`;` on Windows, `:` on Unix/WSL).
- On Windows, `viewer.py` also runs `wsl.exe -l -q` and scans Copilot / VS Code Server session data under each distro home.
- Set `COPILOT_WSL_DISTROS` to limit which distros are scanned (example: `Ubuntu;Debian`).

## ❗This project is licensed under the MIT License, see the LICENSE file for details
