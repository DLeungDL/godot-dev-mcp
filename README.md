# godot-dev-mcp

`godot-dev-mcp` 是一個安全、可組合的 Godot MCP（Model Context Protocol）閘道。第 2 版維持三個清楚邊界：

伺服器使用標準 MCP stdio transport：每行一個 UTF-8 JSON-RPC 訊息，支援 request、notification 與 batch，且 stdout 不輸出非協定內容。

- **AUTHOR**：檢查 scene、node、resource、signal、script，並以預設 dry-run 的受控操作修改場景屬性。
- **OBSERVE**：只在 loopback 上提供唯讀 runtime tree、Node property、效能 monitor、log ring buffer 與 screenshot。
- **VERIFY**：只執行 `godot-dev-mcp.json` 明確允許的命令，並解析 Stagehand exit code、JUnit 與 visual diff。

目前版本：`0.2.0`。Python 需求為 3.11+；Godot 外掛支援 Godot 4.7 Mono。

## 安裝與啟動

```powershell
python -m unittest discover -s tests -v
python -m godot_dev_mcp.server --project "C:\path\to\godot-project"
```

Codex 設定：

```toml
[mcp_servers.godot_dev]
command = "python"
args = ["-m", "godot_dev_mcp.server", "--project", "C:\\path\\to\\godot-project"]
cwd = "C:\\path\\to\\godot-dev-mcp"
```

將 `addons/godot_dev_mcp` 複製到目標專案的 `addons/`，再於 Project Settings → Plugins 啟用。Observer 只接受 `127.0.0.1:7331` 上的 `GET`，不提供 runtime 寫入介面。

啟用外掛時會加入 `GodotDevMCPRuntimeObserver` autoload。Editor Observer 使用 `127.0.0.1:7331`；遊戲執行時的 Runtime Observer 使用 `127.0.0.1:7332`，可透過 `godot_dev_mcp/runtime_port` 專案設定更改。觀察真正的遊戲 scene tree 時，啟動 MCP Server 加上 `--bridge-url http://127.0.0.1:7332`。Runtime Observer 預設只在 debug build 啟動；release build 必須明確設定 `godot_dev_mcp/allow_release_observer=true`，避免意外隨正式遊戲啟用。

Runtime Observer 會透過 Godot 自訂 `Logger` 擷取引擎訊息、`push_warning()`、`push_error()`、script error 與 shader error。Logger 回呼使用 `Mutex` 保護待處理佇列，再於主執行緒寫入最多 500 筆的 ring buffer；`godot_runtime_errors` 與 `godot_resource_leaks` 因此可讀取真實遊戲診斷，而非只看到 Observer 自身訊息。

## 工具

| 分層 | 工具 |
|---|---|
| AUTHOR | `godot_project_info`, `godot_scene_inspect`, `godot_scene_patch`, `godot_resource_inspect`, `godot_script_diagnostics` |
| OBSERVE | `godot_runtime_snapshot`, `godot_runtime_property`, `godot_runtime_logs`, `godot_runtime_screenshot`, `godot_runtime_errors`, `godot_resource_leaks` |
| VERIFY | `godot_run_check`, `godot_stagehand_scenario`, `godot_validate_scene`, `godot_validate_autoload` |
| Grand Sire 選用工具 | `gs_test_auction`, `gs_run_m2`, `gs_run_pvp`, `gs_run_replay_determinism` |

`godot_scene_patch` 的 `dry_run` 預設為 `true`。只有明確傳入 `false` 才會寫入單一 `.tscn` property，回傳值包含 before/after 變更摘要。

`godot_runtime_snapshot` 回傳深度優先的平面 `nodes` 頁面與 `pagination`。預設 `limit=100`、最大 `500`；使用 `next_offset` 取得下一頁，並可用 `max_depth`（最大 `16`）限制巡覽深度。這可避免大型 Editor／runtime tree 產生無界限回應。

## 專案設定

參考 [`examples/godot-dev-mcp.example.json`](examples/godot-dev-mcp.example.json)。目標專案使用 `godot-dev-mcp.json`；命令以 argument array 直接啟動，不經 shell，timeout 上限為 900 秒。

Grand Sire 擴充預設關閉。只有設定 `"extensions": ["grand_sire"]` 後，四個 `gs_*` 工具才會加入 catalog；核心套件與公開介面不依賴該擴充。

Stagehand 保持為獨立 adapter。`stagehand.command` 後會附加 scenario 絕對路徑；若設定 `junit`、`report` 或自訂 `visual_diff` JSON，執行後會解析並回傳結構化結果。情境遵循官方嚴格 JSON schema；Auction 範例在 [`examples/auction-ui.stagehand.json`](examples/auction-ui.stagehand.json)，其中 `project_path` 與 selectors 需按目標專案調整。

## 安全邊界

- project path 以 resolved path 驗證，拒絕 path traversal。
- observation bridge 必須是 HTTP loopback。
- runtime property 只讀取 engine property，不暴露 script-defined property。
- 所有列表、輸出、tree depth 與 log buffer 均有界限。
- 不提供任意 shell command 或任意檔案讀取。

本專案維持 clean-room 實作；授權與上游評估見 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
