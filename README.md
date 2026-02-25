<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# GitHub Copilot Sessions Viewer

Windows ユーザープロファイル配下の `C:\Users\<User>\.copilot\session-state` を読み込み、GitHub Copilot CLI のセッションを一覧・詳細表示するローカル Viewer です。

![image](/image/00001.jpg)

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

- `%USERPROFILE%\.copilot\session-state`
- 見つからない場合は `~/.copilot/session-state`

任意のディレクトリを使う場合:

```powershell
$env:SESSIONS_DIR = 'C:\path\to\session-state'
python viewer.py
```
