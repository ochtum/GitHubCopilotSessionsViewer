<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# GitHub Copilot Sessions Viewer

Windows / WSL 上の GitHub Copilot セッションを読み込み、一覧・詳細表示するローカル Viewer です。

![image](/image/00001.jpg)

## 前提条件

- Python 3（`py -3` または `python` コマンドが利用可能）
- Webブラウザ（Edge / Chrome など）

Python 3 が未インストールの場合（Windows / winget）:

```powershell
winget install -e --id Python.Python.3.12
```

インストール確認:

```powershell
py -3 --version
```

## Windows から起動

- `scripts\windows\launch_viewer.bat`
- `scripts\windows\stop_viewer.bat`

`launch_viewer.bat` は Windows 上で `viewer.py` を直接起動し、待ち受け確認後にブラウザを開きます。

既定URL:

```text
http://127.0.0.1:8766
```

## 直接起動（Python）

```powershell
python viewer.py
```

## デフォルト参照先

- `%USERPROFILE%\.copilot\session-state`（GitHub Copilot CLI）
- `%APPDATA%\Code\User\workspaceStorage\*\chatSessions\*.jsonl`（VS Code 拡張のチャット履歴）
- `%APPDATA%\Code\User\globalStorage\github.copilot-chat`（補助データ）
- `~/.copilot/session-state`（WSL / Linux の GitHub Copilot CLI）
- `~/.vscode-server/data/User/workspaceStorage`（WSL 上の VS Code Server）
- `~/.vscode-server/data/User/globalStorage/github.copilot-chat`（WSL 上の VS Code Server 補助データ）
- `\\wsl.localhost\<distro>\home\<user>\...`（Windows 起動時に WSL ディストリを自動検出）

任意のディレクトリを使う場合:

```powershell
$env:SESSIONS_DIR = 'C:\path\to\session-state'
python viewer.py
```

補足:

- `COPILOT_SESSIONS_DIR` でも上書きできます。
- 複数指定は `os.pathsep` 区切り（Windows は `;`, Unix/WSL は `:`）です。
- Windows 版 `viewer.py` は `wsl.exe -l -q` を使って WSL ディストリを列挙し、各ディストリのホーム配下にある Copilot / VS Code Server のセッションを探索します。
- 自動検出対象のディストリを絞る場合は `COPILOT_WSL_DISTROS` を指定できます（例: `Ubuntu;Debian`）。
