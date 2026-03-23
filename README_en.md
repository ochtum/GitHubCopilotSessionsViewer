<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# GitHub Copilot Sessions Viewer

A local viewer for listing, inspecting, and searching the history of GitHub Copilot CLI sessions, including sessions from the GitHubCopilot VS Code extension. You can also attach labels to content you want to remember and search for it later.

- This tool supports Japanese / English / Simplified Chinese / Traditional Chinese.
- Feedback and feature requests are always welcome. Feel free to open an issue.
- The first launch is relatively heavy, but after the initial load completes, the app runs quickly thanks to caching.
  - Lazy loading is planned soon to improve startup speed further.

## Screens

### Main Screen

![image](/image/00001.jpg)

### Label Management Screen

![image](/image/00002.jpg)

### Shortcut List Screen

![image](/image/00003.jpg)

⭐ If this project is useful to you, starring it would be appreciated!

👀 If you want to follow updates, please consider watching the repository as well.

## How to Start

Download the `app-framework-dependent` folder from Releases, extract it, and then run `run.cmd` inside it.

Note: Running this tool requires either the .NET 10 SDK or the .NET 10 Runtime. If you are not sure whether it is installed, or you prefer not to install it, download the `app-self-contained` folder instead.

---

Note: If you want to build from source, run the following PowerShell script.

- Framework-dependent build (if the .NET 10 SDK or .NET 10 Runtime is already installed)

```powershell
.\publish.ps1 -CleanOutput
```

- Self-contained build (if the .NET 10 SDK or .NET 10 Runtime may not be installed, or you do not want to install it)

```powershell
.\publish.ps1 -SelfContained -CleanOutput
```

## Default Scan Paths

- `%USERPROFILE%\.copilot\session-state` (GitHub Copilot CLI)
- `%APPDATA%\Code\User\workspaceStorage\*\chatSessions\*.jsonl` (chat history from the VS Code extension)
- `~/.copilot/session-state` (GitHub Copilot CLI on WSL / Linux)
- `~/.vscode-server/data/User/workspaceStorage` (VS Code Server on WSL)
- `\\wsl.localhost\<distro>\home\<user>\...` (WSL distributions are auto-detected when launched on Windows)

## UI Features

- Header
  - Language switcher (`日本語` / `English` / `简体中文` / `繁體中文`) in the upper-right corner
  - Buttons for `Label Management`, `Cost View`, `Meta View`, `Shortcuts`, and toggling the list view on mobile
  - A "Today's usage" summary below the header so you can quickly check `REQUEST` and `PREMIUM REQUEST`
  - `Meta View` is hidden by default. It can show the selected session's `session root`, `path`, `cwd`, `time`, `source`, `events`, and `raw lines`
- Left pane: session list
  - Two tabs: `Session List` and `Label List`
  - Displays sessions with `source` (`CLI` / `VS Code`), session labels, and last updated time
  - Shows the count as `sessions: filtered/total` at the top of the list
  - Sorting tabs for `Newest First`, `Oldest First`, and `Last Updated`
  - `Clear` resets the search and filter conditions in the left pane
  - `Show Filters` / `Hide Filters` collapses or expands the search and filter area
  - In vertical layout, `Hide List` / `Show List` in the upper-right header toggles the entire left pane
- Left pane search and filters
  - Filter by `cwd` / `Start Date` / `End Date` / `Event Start DateTime` / `Event End DateTime` / keyword / `source` / session label / event label
  - In the keyword field, text wrapped in double quotes is treated as a single phrase
    - Example: search `"Working Space"` as one phrase
  - `cwd` / date-time / `source` / label conditions are always combined with AND
  - The `AND/OR` toggle applies only to the keyword field
    - `AND`: must contain all space-separated keywords
    - `OR`: must contain at least one of the space-separated keywords
  - The time field for event date-time becomes enabled after the corresponding date is entered
  - Filter conditions are preserved for the next launch
- Left pane label list
  - Labeled sessions and labeled events are grouped by label
  - Distinguishes `message`, `function_call`, `function_output`, and `agent_update`
  - Clicking an item jumps to the target session or event
- Right pane: chronological event view for the selected session
  - Shows a loading indicator on the first detail load, and an updating overlay during manual `Refresh`
  - The detail toolbar is organized into `Display`, `Actions`, `Search`, and `Range Selection`
  - `Detail Actions`, `Search`, and `Range Selection` can each be expanded or collapsed independently
  - When no session is selected, display, search, and range-selection controls are disabled
- Right pane display and actions
  - Display options: "Show Only User Instructions" / "Show Only AI Responses" / "Show Only Each Input and Final Response" / "Reverse Display Order" / label filter
  - `Refresh` reloads only the selected session
  - `Clear` resets the entire state of the right pane
    - Display filters
    - Detail keyword input and `Filter` / `Search` state
    - Selection mode and selected events
    - Anchor selection mode, anchor point, and before/after anchor display state
    - Any open label picker
  - "Copy Session Resume Command" copies `codex resume <session ID>`
  - "Copy Displayed Messages" copies all currently displayed `message` items
  - Session label display and "Add Label to Session"
  - Label display / add / remove for each event
  - Each `message` event has its own `Copy` button
- Right pane search and selection
  - Detail keywords separate `Filter` and `Search`
    - `Filter`: shows only events that contain the keyword
    - `Search`: highlights matches and lets you move with `Previous` / `Next`
    - Hit count is shown as `current / total`
    - `Clear Search`: clears the input field, filter, and search state together
  - Detail keyword matching is literal substring matching, not AND/OR parsing
  - Search targets are `message`, `function_call`, `function_output`, and `agent_update`
  - Pressing `Enter` in the search field starts a search, then releases focus so you can move with `N` / `P`
  - `Event Start DateTime` / `Event End DateTime` can narrow the event timeline shown in the right pane
  - Right-pane event date-time filters also use separate `date + time` inputs, and the time field is enabled after a date is entered
  - In `Selection Mode`, you can check events individually and copy them together with `Copy Selected`
    - Even when a filter is applied, events that were already selected remain selected
  - `Show Only Selected Events` filters the view down to selected events only
  - In `Anchor Selection Mode`, you can choose a single `message` and filter with `Show Only After Anchor` / `Show Only Before Anchor`
- Event display
  - `message` (`user` / `assistant` / `developer`)
  - `user` uses a light blue background, while execution context such as `AGENTS.md` and `environment_context` uses a gray background
  - `function_call` / `function_output`
  - `agent_update`
- Label management
  - Opens in a separate window from the `Label Management` button in the upper-right corner
  - Shares the same language setting as the main window
  - Manages session labels and event labels together
  - Label colors can be entered directly as `#hex`, `rgb(...)`, or `oklch(...)`, or selected from preset colors
  - Label-addition UIs also show candidates with their colors applied
- Cost view
  - Opens in a separate window from the `Cost View` button in the upper-right corner
  - Lets you review usage totals while switching cost display based on the selected currency setting

## Keyboard Shortcuts

While an input field has focus, shortcuts are disabled. Press `Esc` to close the shortcut list or label picker, or to remove focus from a search input.

| Key         | Action                                                                                |
| ----------- | ------------------------------------------------------------------------------------- |
| `F5`        | Refresh the currently displayed list or session details                               |
| `Shift + F` | Toggle the left-pane filter display                                                   |
| `Shift + L` | Run `Clear` in the left pane                                                          |
| `/`         | Focus the search input field                                                          |
| `N`         | Move to the next detail-search hit                                                    |
| `P`         | Move to the previous detail-search hit                                                |
| `M`         | Toggle meta display for `path / cwd / time / request / premium request / model`      |
| `[`         | Open the previous session                                                             |
| `]`         | Open the next session                                                                 |
| `1`         | Toggle "Show Only User Instructions"                                                  |
| `2`         | Toggle "Show Only AI Responses"                                                       |
| `3`         | Toggle "Show Only Each Input and Final Response"                                      |
| `4`         | Toggle "Reverse Display Order"                                                        |
| `Shift + D` | Clear right-pane display conditions and action state                                  |
| `Shift + T` | Toggle the visibility of detail actions                                               |
| `Shift + R` | Copy the session resume command (`copilot --resume <session ID>`)                     |
| `Shift + C` | Copy displayed messages                                                               |
| `Shift + S` | Toggle selection mode on and off                                                      |
| `Shift + X` | Copy selected messages                                                                |
| `Shift + G` | Toggle anchor selection mode on and off                                               |
| `Shift + H` | Clear the anchor                                                                      |
| `,`         | Show only items before the anchor                                                     |
| `.`         | Show only items after the anchor                                                      |
| `Esc`       | Close the shortcut list or add-label popup, and remove focus from the search input    |

## Notes

- On Windows, `wsl.exe -l -q` is used to enumerate WSL distributions, and sessions under each distro's home directory are scanned for Copilot / VS Code Server data.
- To handle large logs, the list is limited to `300` items and events are limited to `2000` items.
- By default, the viewer listens on localhost only (`127.0.0.1`).

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
│   └── 00003.jpg                      # Shortcut screen sample for README
└── src/
    ├── .cache/
    │   └── label-store.json           # Storage for label definitions and mappings
    ├── GitHubCopilotSessionsViewer.sln      # Solution
    ├── GitHubCopilotSessionsViewer.csproj   # ASP.NET Core / Blazor project definition
    ├── Program.cs                     # App startup, URL configuration, API endpoint definitions
    ├── appsettings.json               # Production settings
    ├── appsettings.Development.json   # Development settings
    ├── Components/
    │   ├── App.razor                  # HTML root and shared script loading
    │   ├── Routes.razor               # Routing definitions
    │   ├── _Imports.razor             # Shared Razor usings
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
    │   └── ViewerDtos.cs              # DTOs for API responses/requests
    ├── Properties/
    │   ├── AssemblyInfo.cs            # Version information
    │   └── launchSettings.json        # Local development launch settings
    ├── Services/
    │   ├── LabelStore.cs              # Label persistence and validation logic
    │   └── ViewerService.cs           # Session discovery, loading, and search logic
    └── wwwroot/
        ├── app.css                    # Global shared styles
        ├── css/
        │   ├── labels.css             # Styles for the label management page
        │   └── viewer.css             # Styles for the main page
        ├── icons/
        │   └── github-copilot-sessions-viewer.svg # App icon
        └── js/
            ├── labels.js              # Script for the label management page
            └── viewer.js              # Script for the main page
```

## ❗ This project is provided under the MIT License. See the LICENSE file for details.
