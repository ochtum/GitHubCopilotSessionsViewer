<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# GitHub Copilot Sessions Viewer

Windows / WSL 上の GitHub Copilot セッションを一覧・詳細表示するローカル Viewer です。  
GitHub Copilot CLI と VS Code 拡張のチャット履歴をまとめて表示できます。

## 画面構成

### メイン画面

![image](/image/00001.jpg)

### ラベル管理画面

![image](/image/00002.jpg)

## 主要ファイル

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
- `~/.copilot/session-state`（WSL / Linux の GitHub Copilot CLI）
- `~/.vscode-server/data/User/workspaceStorage`（WSL 上の VS Code Server）
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
  - 一覧にセッション `source` ラベル（`CLI` / `VS Code`）とセッションラベルを表示
  - 初回起動時は一覧のローディング状態を表示
  - `Reload` ボタンで一覧を再読み込み
    - 手動 `Reload` 時は一覧の更新中オーバーレイとボタン状態を表示
  - `Clear` ボタンで左ペインの検索条件を初期化
  - `Hide` / `Show` ボタンで検索条件欄を折りたたみ / 展開可能
  - 縦表示時はヘッダー右上の「一覧を隠す / 一覧を表示」ボタンで左ペイン全体を切り替え可能
- 左上 filter
  - `cwd` / 日付範囲 / キーワード / `source` / セッションラベル / イベントラベルで絞り込み
  - キーワード検索は SQLite インデックスを使う全文検索
  - `message` に加えて、`function_call.arguments` / `tool_start.arguments` / `tool_output` / `assistant.turn_*` / `info` / `error` も検索対象
  - `cwd` / 日付範囲 / `source` / ラベル条件は常に AND 条件で評価
  - `AND/OR` 切替はキーワード欄内のみ
    - `AND`: スペース区切りキーワードをすべて含む
    - `OR`: スペース区切りキーワードのどれかを含む
- 右ペイン: 選択セッションのイベント時系列表示
  - 初回詳細読み込み時はローディング表示、手動 `Refresh` 時は詳細更新中オーバーレイを表示
  - 詳細ヘッダーに `source` ラベル（`CLI` / `VS Code`）を表示
  - 詳細ヘッダーは 4 段構成
    - 1 段目: 表示フィルター群、`Clear`、`Refresh`、2 段目 / 3 段目 / 4 段目をまとめて畳む `Hide` / `Show`
    - 2 段目: コピー、ラベル追加、選択コピー関連の操作ボタン
    - 3 段目: キーワード検索欄、`フィルター`、`検索`、`前へ`、`次へ`、`Keyword Clear`
    - 4 段目: 単一 `message` を起点として選ぶモード、起点解除、起点以前 / 以降のメッセージ表示
  - 表示オプション
    - 「ユーザー指示のみ表示」
    - 「AIレスポンスのみ表示」
    - 「各入力と最終応答のみ」
      - 各ターンで `user` の入力 1 件と、次の `user` が来るまでの最後の `assistant` 1 件だけを表示
    - 「表示順を逆にする」
    - `event label: all` フィルタ
  - キーワード検索
    - `フィルター`: キーワードを含むイベントだけを表示
    - `検索`: 一致箇所をハイライトし、`前へ` / `次へ` で候補間を移動
    - `Keyword Clear`: 入力欄、フィルター、検索状態をまとめて解除
    - AND / OR ではなく、入力した文字列そのままの部分一致で判定
    - 検索対象は `message` / `function_call` / `tool_start` / `tool_output` / `info` / `error` / `assistant.turn_*`
  - `Clear` ボタンで詳細側の表示フィルターをクリア
  - `Refresh` ボタンで選択中セッションだけを再取得
  - 「セッション再開コマンドコピー」ボタンで `copilot --resume <セッションID>` をコピー
  - 「表示中メッセージコピー」ボタンで、現在の表示フィルター結果をまとめてコピー
  - セッションラベル表示と「セッションにラベル追加」
  - イベントごとのラベル表示 / 追加 / 削除
  - 各 `message` イベントに「コピー」ボタンを表示
  - 「選択モード」で `message` イベントごとにチェックを付けて、「選択コピー」でまとめてコピー可能
    - フィルター適用中でも、すでに選択済みの `message` は保持される
  - 「起点選択モード」で単一の `message` を選び、「起点以降のみ表示」 / 「起点以前のみ表示」でメッセージを絞り込み可能
  - `message`（`user` / `assistant` / `system`）
  - `function_call` / `tool_start` / `tool_output`
  - `info` / `error` / `assistant.turn_*`
- ラベル管理
  - 右上の「ラベル管理」ボタンから別ウィンドウで開く
  - セッションラベル / イベントラベルを共通管理
  - ラベル色は `#hex` / `rgb(...)` / `oklch(...)` を直接入力、または色プリセットから選択可能


## ショートカットキー

ヘッダー右上の「ショートカット」ボタンから一覧ダイアログを開けます。

| キー | 動作 |
| --- | --- |
| `F5` | 表示中の一覧またはセッション詳細を更新 |
| `Shift + F` | 左ペインのフィルタ表示を切り替え |
| `Shift + L` | 左ペインの `Clear` を実行 |
| `/` | 検索入力欄にフォーカス |
| `N` | 詳細検索の次のヒットへ移動 |
| `P` | 詳細検索の前のヒットへ移動 |
| `M` | `path / cwd / time` のメタ表示を切り替え |
| `[` | 前のセッションを開く |
| `]` | 次のセッションを開く |
| `1` | 「ユーザー指示のみ表示」を切り替え |
| `2` | 「AIレスポンスのみ表示」を切り替え |
| `3` | 「各入力と最終応答のみ」を切り替え |
| `4` | 「表示順を逆にする」を切り替え |
| `Shift + D` | 右ペインの表示条件と操作状態をクリア |
| `Shift + T` | 詳細操作の表示と非表示を切り替え |
| `Shift + R` | セッション再開コマンドをコピー（`copilot --resume <セッションID>`） |
| `Shift + C` | 表示中メッセージをコピー |
| `Shift + S` | 選択モードの開始と終了を切り替え |
| `Shift + X` | 選択中メッセージをコピー |
| `Shift + G` | 起点選択モードの開始と終了を切り替え |
| `Shift + H` | 起点を解除 |
| `,` | 起点以前のみ表示 |
| `.` | 起点以降のみ表示 |
| `Esc` | ショートカット一覧やラベル追加ポップアップを閉じ、検索入力欄からカーソルを外す |

## 補足

- 検索インデックスは `.cache/search_index.sqlite3` に保存され、変更のあったセッションだけ差分更新します。
- Windows 版 `viewer.py` は `wsl.exe -l -q` を使って WSL ディストリを列挙し、各ディストリのホーム配下にある Copilot / VS Code Server のセッションを探索します。
- 自動検出対象のディストリを絞る場合は `COPILOT_WSL_DISTROS` を指定できます（例: `Ubuntu;Debian`）。
- 大量ログ対策で一覧最大 `300` 件、イベント最大 `3000` 件に制限しています。
- Viewer はデフォルトでローカル専用 (`127.0.0.1`) で待ち受けます。

## ❗このプロジェクトは MIT ライセンスの下で提供されています。詳細は LICENSE ファイルをご覧ください。
