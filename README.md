<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# GitHub Copilot Sessions Viewer

Windows / WSL 上の GitHub Copilot セッションを一覧・詳細表示するローカル Viewer です。  
GitHub Copilot CLI、VS Code 拡張のチャット履歴、補助データから生成した Cloud セッションをまとめて表示できます。

## 画面構成

### メイン画面

![image](/image/00001.jpg)

### ラベル管理画面

![image](/image/00002.jpg)

## ディレクトリ構成

```text
.
├─ viewer.py
└─ scripts
   └─ windows
      ├─ launch_viewer.bat
      └─ stop_viewer.bat
```

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

## 起動方法

### Windows からワンクリック起動（バッチ）

- `scripts\windows\launch_viewer.bat`
- `scripts\windows\stop_viewer.bat`

`launch_viewer.bat` は Windows 上で `viewer.py` を直接起動し、待ち受け確認後にブラウザを開きます。

起動後、ブラウザで以下を開きます。

```text
http://127.0.0.1:8766
```

### 直接起動（Python）

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

## オプション

デフォルト以外のセッションディレクトリを使う場合は `SESSIONS_DIR` を設定します。  
`COPILOT_SESSIONS_DIR` でも上書きできます。複数指定は `os.pathsep` 区切り（Windows は `;`, Unix/WSL は `:`）です。

```powershell
$env:SESSIONS_DIR = 'C:\path\to\session-state'
python viewer.py
```

待ち受けアドレスを変更する場合は `HOST` を設定します。

```powershell
$env:HOST = '0.0.0.0'
python viewer.py
```

## 画面機能

- 左ペイン: セッション一覧（最新順）
  - 一覧にセッション `source` ラベル（`CLI` / `VS Code` / `Cloud`）とセッションラベルを表示
  - `Reload` ボタンで一覧を再読み込み
  - `Clear` ボタンで左ペインの検索条件を初期化
  - `Hide` / `Show` ボタンで検索条件欄を折りたたみ / 展開可能
- 左上 filter
  - `cwd` / 日付範囲 / キーワード / `source` / セッションラベル / イベントラベルで絞り込み
  - キーワード検索は SQLite インデックスを使う全文検索
  - `message` に加えて、`function_call.arguments` / `tool_output` / `assistant.turn_*` / `info` / `error` なども検索対象
  - `cwd` / 日付範囲 / `source` / ラベル条件は常に AND 条件で評価
  - `AND/OR` 切替はキーワード欄内のみ
    - `AND`: スペース区切りキーワードをすべて含む
    - `OR`: スペース区切りキーワードのどれかを含む
- 右ペイン: 選択セッションのイベント時系列表示
  - 詳細ヘッダーに `source` ラベル（`CLI` / `VS Code` / `Cloud`）を表示
  - 表示オプション
    - 「ユーザー指示のみ表示」
    - 「AIレスポンスのみ表示」
    - 「表示順を逆にする」
    - `event label: all` フィルタ
  - `Refresh` ボタンで選択中セッションだけを再取得
  - 「セッション再開コマンドコピー」ボタンで `copilot --resume <セッションID>` をコピー
  - セッションラベル表示と「セッションにラベル追加」
  - イベントごとのラベル表示 / 追加 / 削除
  - `message`（`user` / `assistant` / `system`）
  - `function_call` / `tool_start` / `tool_output`
  - `info` / `error` / `assistant.turn_*`
- ラベル管理
  - 右上の「ラベル管理」ボタンから別ウィンドウで開く
  - セッションラベル / イベントラベルを共通管理
  - ラベル色は `#hex` / `rgb(...)` / `oklch(...)` を直接入力、または色プリセットから選択可能

## 補足

- 検索インデックスは `.cache/search_index.sqlite3` に保存され、変更のあったセッションだけ差分更新します。
- Windows 版 `viewer.py` は `wsl.exe -l -q` を使って WSL ディストリを列挙し、各ディストリのホーム配下にある Copilot / VS Code Server のセッションを探索します。
- 自動検出対象のディストリを絞る場合は `COPILOT_WSL_DISTROS` を指定できます（例: `Ubuntu;Debian`）。
- 大量ログ対策で一覧最大 `300` 件、イベント最大 `3000` 件に制限しています。
- Viewer はデフォルトでローカル専用 (`127.0.0.1`) で待ち受けます。

## ❗このプロジェクトは MIT ライセンスの下で提供されています。詳細は LICENSE ファイルをご覧ください。
