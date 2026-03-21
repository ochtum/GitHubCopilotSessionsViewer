<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# GitHub Copilot Sessions Viewer

A local viewer that lets you browse, search, and review the history of GitHub Copilot CLI (including the VS Code GitHub Copilot extension). You can attach labels to important content and search for it later.

- This tool supports Japanese / English / Simplified Chinese / Traditional Chinese.
- Feedback and feature requests are welcome — please feel free to open an issue.

## Screen Layout

### Main Screen

![image](/image/00001.jpg)

### Label Manager Screen

![image](/image/00002.jpg)

### Shortcut Key List Screen

![image](/image/00003.jpg)

⭐ If you find this project useful, a Star would be appreciated!

👀 Want to stay up to date? Watch the repo!

## Getting Started

Download the `app-framework-dependent` folder from Releases, extract it, and run `AI-CLI-Watcher.exe`.

※ Running this tool requires .NET 10 SDK or .NET 10 Runtime. If you are unsure whether it is installed, or prefer not to install it, download the `app-self-contained` folder instead.

---

※ To build from source, run the following PowerShell script:

- Framework-dependent build (when .NET 10 SDK or .NET 10 Runtime is already installed)

```
.\publish.ps1 -CleanOutput
```

- Self-contained build (when .NET 10 SDK or .NET 10 Runtime installation status is unknown, or you do not wish to install it)

```
.\publish.ps1 -SelfContained -CleanOutput
```

## Default Scan Paths

- `%USERPROFILE%\.copilot\session-state` (GitHub Copilot CLI)
- `%APPDATA%\Code\User\workspaceStorage\*\chatSessions\*.jsonl` (VS Code extension chat history)
- `~/.copilot/session-state` (GitHub Copilot CLI on WSL / Linux)
- `~/.vscode-server/data/User/workspaceStorage` (VS Code Server on WSL)
- `\\wsl.localhost\<distro>\home\<user>\...` (auto-detected when launched on Windows)

## UI Features

- Left pane: session list (newest first)
  - Shows session `source` labels (`CLI` / `VS Code`) and session labels in the list
  - Displays a loading state on initial startup
  - `Reload` button reloads the session list
    - During a manual `Reload`, shows an updating overlay and button state feedback
  - `Clear` button resets the left-pane search conditions
  - `Hide` / `Show` button collapses or expands the search filter area
  - In vertical layout, the `Hide List` / `Show List` button in the upper-right header toggles the entire left pane
- Top-left filters
  - Filter by `cwd` / `Start date` / `End date` / `Event start datetime` / `Event end datetime` / keyword / `source` / session label / event label
  - `Start date` / `End date` use native browser `date` inputs; event datetimes use split `date + time` inputs
  - The time field for event datetime becomes enabled after a corresponding date is entered
  - Keyword search uses a SQLite-backed full-text search index
  - In addition to `message`, search covers `function_call.arguments` / `tool_start.arguments` / `tool_output` / `assistant.turn_*` / `info` / `error`
  - `cwd` / datetime / `source` / label conditions are always evaluated with AND
  - The `AND/OR` toggle applies only within the keyword field
    - `AND`: must contain all space-separated keywords
    - `OR`: must contain at least one space-separated keyword
- Right pane: chronological event view for the selected session
  - Shows a loading state on the first detail load, and an updating overlay during manual `Refresh`
  - Detail header shows the `source` label (`CLI` / `VS Code`)
  - Detail header has a 4-row layout
    - Row 1: display filters, `Clear`, `Refresh`, and a `Hide` / `Show` button that collapses rows 2–4 together
    - Row 2: copy, label, and selection-copy action buttons
    - Row 3: keyword search field, `Filter`, `Search`, `Previous`, `Next`, `Keyword Clear`
    - Row 4: single-message anchor selection mode, clear anchor, and before/after message filtering
  - Display options
    - "Show only user instructions"
    - "Show only AI responses"
    - "Show only each input and final response"
      - For each turn, shows one `user` input and only the last `assistant` response before the next `user`
    - "Reverse display order"
    - `event label: all` filter
  - Keyword search
    - `Filter`: shows only events containing the keyword
    - `Search`: highlights matches and lets you navigate with `Previous` / `Next`
    - `Keyword Clear`: clears the input, filter state, and search state all at once
    - Matching is a literal substring match, not AND/OR parsing
    - Search targets: `message` / `function_call` / `tool_start` / `tool_output` / `info` / `error` / `assistant.turn_*`
  - `Event start datetime` / `Event end datetime` can narrow the event timeline shown in the right pane
  - Right-pane event datetime filters also use split `date + time` inputs; the time field becomes enabled after a date is entered
  - `Clear` button resets the detail-side display filters
  - `Refresh` button reloads only the currently selected session
  - "Copy Resume Command" copies `copilot --resume <session_id>`
  - "Copy Displayed Messages" copies all messages currently visible under the active display filters
  - Session label display and "Add Session Label"
  - Per-event label display / add / remove
  - Each `message` event has a `Copy` button
  - "Selection Mode" lets you check individual `message` events and copy them with "Copy Selected"
    - Already-selected `message` events are preserved even when filters are applied
  - "Anchor Selection Mode" lets you pick a single `message` as an anchor and filter to messages before or after that anchor
  - `message` (`user` / `assistant` / `system`)
  - `function_call` / `tool_start` / `tool_output`
  - `info` / `error` / `assistant.turn_*`
- Label Manager
  - Opens in a separate window via the "Label Manager" button in the upper-right
  - Manages session labels and event labels in a shared UI
  - Label colors can be entered directly as `#hex`, `rgb(...)`, or `oklch(...)`, or selected from color presets

## Keyboard Shortcuts

Shortcuts are disabled while an input field is focused. Press `Esc` to close the shortcut list or label picker, or to remove focus from a search field.

| Key         | Action                                                                       |
| ----------- | ---------------------------------------------------------------------------- |
| `F5`        | Refresh the current list or session detail                                   |
| `Shift + F` | Toggle the left-pane filter visibility                                       |
| `Shift + L` | Run `Clear` on the left pane                                                 |
| `/`         | Focus the search input field                                                 |
| `N`         | Move to the next detail-search match                                         |
| `P`         | Move to the previous detail-search match                                     |
| `M`         | Toggle the `path / cwd / time` meta display                                  |
| `[`         | Open the previous session                                                    |
| `]`         | Open the next session                                                        |
| `1`         | Toggle "Show only user instructions"                                         |
| `2`         | Toggle "Show only AI responses"                                              |
| `3`         | Toggle "Show only each input and final response"                             |
| `4`         | Toggle "Reverse display order"                                               |
| `Shift + D` | Clear right-pane display conditions and operation states                     |
| `Shift + T` | Toggle detail actions visibility                                             |
| `Shift + R` | Copy the session resume command (`copilot --resume <session_id>`)            |
| `Shift + C` | Copy displayed messages                                                      |
| `Shift + S` | Toggle selection mode                                                        |
| `Shift + X` | Copy selected messages                                                       |
| `Shift + G` | Toggle anchor selection mode                                                 |
| `Shift + H` | Clear the anchor                                                             |
| `,`         | Show only messages before the anchor                                         |
| `.`         | Show only messages after the anchor                                          |
| `Esc`       | Close the shortcut list or label picker, and remove focus from search fields |

## Notes

- On Windows, `wsl.exe -l -q` is used to enumerate WSL distros and scan Copilot / VS Code Server session data under each distro's home directory.
- To handle large logs, the list is capped at 300 sessions and the detail view at 2,000 events.
- The viewer listens on localhost only (`127.0.0.1`) by default.

## File Structure

```text
.
├── .gitignore                         # Root exclusion settings
├── LICENSE                            # License
├── README.md                          # Japanese README
├── README_en.md                       # English README
├── publish.ps1                        # Distribution publish script
├── .vscode/
│   └── settings.json                  # VS Code editor settings
├── image/
│   ├── 00001.jpg                      # Main screen sample for README
│   ├── 00002.jpg                      # Label management screen sample for README
│   └── 00003.jpg                      # Shortcuts screen sample for README
└── src/
    ├── .cache/
    │   └── label-store.json           # Label definitions and bindings storage
    ├── GitHubCopilotSessionsViewer.sln      # Solution
    ├── GitHubCopilotSessionsViewer.csproj   # ASP.NET Core / Blazor project definition
    ├── Program.cs                     # App startup, URL config, API endpoint definitions
    ├── appsettings.json               # Production settings
    ├── appsettings.Development.json   # Development settings
    ├── Components/
    │   ├── App.razor                  # HTML root and shared script loading
    │   ├── Routes.razor               # Routing definition
    │   ├── _Imports.razor             # Razor shared usings
    │   ├── Layout/
    │   │   ├── MainLayout.razor       # Shared layout
    │   │   ├── MainLayout.razor.css   # Shared layout styles
    │   │   ├── ReconnectModal.razor   # Reconnect modal UI
    │   │   ├── ReconnectModal.razor.css # Reconnect modal styles
    │   │   └── ReconnectModal.razor.js  # Reconnect modal script
    │   └── Pages/
    │       ├── Error.razor            # Error page
    │       ├── Home.razor             # Main page
    │       ├── Labels.razor           # Label management page
    │       └── NotFound.razor         # 404 page
    ├── Models/
    │   └── ViewerDtos.cs              # API response/request DTOs
    ├── Properties/
    │   ├── AssemblyInfo.cs            # Version information
    │   └── launchSettings.json        # Local development launch settings
    ├── Services/
    │   ├── LabelStore.cs              # Label persistence and validation logic
    │   └── ViewerService.cs           # Session discovery, loading, and search logic
    └── wwwroot/
        ├── app.css                    # Global shared styles
        ├── css/
        │   ├── labels.css             # Label management page styles
        │   └── viewer.css             # Main page styles
        ├── icons/
        │   └── github-copilot-sessions-viewer.svg # App icon
        └── js/
            ├── labels.js              # Label management page script
            └── viewer.js              # Main page script
```

※ `app/`, `src/bin/`, and `src/obj/` are generated during publish/build and are omitted.

## ❗ This project is provided under the MIT License. See the LICENSE file for details.
