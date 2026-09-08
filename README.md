# Grand Sire Dev MCP

一個針對 Grand Sire 的 Godot 開發 MCP（Model Context Protocol）入口。第一版刻意維持三層分工：

- **AUTHOR**：安全地檢查專案與場景來源。
- **OBSERVE**：透過 Godot Editor 外掛讀取執行期狀態。
- **VERIFY**：以明確允許清單執行既有測試，並預留 Stagehand 情境測試介面。

本專案是乾淨室（clean-room）骨架，尚未複製 Godot AI、Beckett 或 Stagehand 的程式碼。因此目前只受本專案 MIT 授權約束；若日後移植上游程式碼，請同步更新 `THIRD_PARTY_NOTICES.md`。

## 快速開始

需求：Python 3.11+、Godot 4.5+（建議 4.7 Mono）。

```bash
cd grand-sire-dev-mcp
python3 -m unittest discover -s tests -v
python3 -m gs_mcp.server --project /path/to/grand-sire
```

Codex MCP 設定範例：

```toml
[mcp_servers.grand_sire]
command = "python3"
args = ["-m", "gs_mcp.server", "--project", "/absolute/path/to/grand-sire"]
cwd = "/absolute/path/to/grand-sire-dev-mcp"
```

Godot 外掛安裝：把 `addons/grand_sire_dev_mcp` 複製到 Grand Sire 專案的 `addons/`，再於 Project Settings → Plugins 啟用。預設僅監聽 `127.0.0.1:7331`。

## 第一版工具

| MCP 工具 | 層 | 說明 |
|---|---|---|
| `gs_project_info` | AUTHOR | 讀取 `project.godot` 與專案能力摘要 |
| `gs_scene_inspect` | AUTHOR | 安全讀取專案內 `.tscn` 場景摘要 |
| `gs_runtime_snapshot` | OBSERVE | 取得 Godot 外掛提供的 scene tree、錯誤與效能快照 |
| `gs_run_check` | VERIFY | 執行 `gs-mcp.json` 中明確允許的測試命令 |
| `gs_stagehand_scenario` | VERIFY | 呼叫設定好的 Stagehand runner |

## 專案設定

在 Grand Sire 根目錄新增 `gs-mcp.json`：

```json
{
  "checks": {
    "smoke": ["godot", "--headless", "--path", ".", "--script", "res://tests/smoke_ui.gd"],
    "dotnet": ["dotnet", "test"]
  },
  "stagehand": {
    "command": ["godot-stagehand", "run"]
  }
}
```

命令不經 shell 執行；工具名稱必須存在於設定檔，避免任意命令注入（command injection）。

## 路線圖

1. 對接 Godot AI 的 editor authoring 能力（保留其 MIT attribution）。
2. 擴充唯讀 runtime property、log ring buffer 與 performance monitors。
3. 對接 Stagehand 的 scenario/JUnit 結果，而不將其合併成單體。
4. 增加 Grand Sire 專用的 `auction`、M2、PvP 與 replay checks。

