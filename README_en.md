<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# GitHub Copilot Sessions Viewer

A local viewer that reads GitHub Copilot CLI session data from `C:\Users\<User>\.copilot\session-state` and displays session lists and details.

![image](/image/00001.jpg)

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

## Default Session Directory

- `%USERPROFILE%\.copilot\session-state`
- If not found, `~/.copilot/session-state`

To use a custom directory:

```powershell
$env:SESSIONS_DIR = 'C:\path\to\session-state'
python viewer.py
```
