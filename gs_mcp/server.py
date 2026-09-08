from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .core import GrandSireTools, ToolError


TOOLS = [
    {"name": "gs_project_info", "description": "Inspect the configured Godot project.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "gs_scene_inspect", "description": "Inspect a .tscn scene inside the project.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "gs_runtime_snapshot", "description": "Read a runtime snapshot from the Godot observation bridge.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "gs_run_check", "description": "Run an allowlisted project check.", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["name"]}},
    {"name": "gs_stagehand_scenario", "description": "Run a configured Stagehand scenario.", "inputSchema": {"type": "object", "properties": {"scenario": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["scenario"]}},
]


def response(request_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": request_id}
    payload["error" if error else "result"] = error if error else result
    return payload


def dispatch(tools: GrandSireTools, message: dict[str, Any]) -> dict[str, Any] | None:
    method, request_id = message.get("method"), message.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return response(request_id, {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "grand-sire-dev-mcp", "version": "0.1.0"}})
    if method == "tools/list":
        return response(request_id, {"tools": TOOLS})
    if method != "tools/call":
        return response(request_id, error={"code": -32601, "message": f"Method not found: {method}"})
    params, arguments = message.get("params", {}), message.get("params", {}).get("arguments", {})
    name = params.get("name")
    try:
        handlers = {
            "gs_project_info": lambda: tools.project_info(),
            "gs_scene_inspect": lambda: tools.scene_inspect(arguments["path"]),
            "gs_runtime_snapshot": lambda: tools.runtime_snapshot(),
            "gs_run_check": lambda: tools.run_check(arguments["name"], arguments.get("timeout", 300)),
            "gs_stagehand_scenario": lambda: tools.stagehand_scenario(arguments["scenario"], arguments.get("timeout", 300)),
        }
        if name not in handlers:
            raise ToolError(f"Unknown tool: {name}")
        content = json.dumps(handlers[name](), ensure_ascii=False, indent=2)
        return response(request_id, {"content": [{"type": "text", "text": content}], "isError": False})
    except (ToolError, KeyError, TypeError, ValueError) as exc:
        return response(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--bridge-url", default="http://127.0.0.1:7331")
    args = parser.parse_args()
    tools = GrandSireTools(args.project, args.bridge_url)
    for line in sys.stdin:
        try:
            result = dispatch(tools, json.loads(line))
            if result is not None:
                print(json.dumps(result, separators=(",", ":")), flush=True)
        except json.JSONDecodeError as exc:
            print(json.dumps(response(None, error={"code": -32700, "message": str(exc)})), flush=True)


if __name__ == "__main__":
    main()

