from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from .core import GodotTools, ToolError


def _tool(name: str, description: str, properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


TIMEOUT = {"timeout": {"type": "integer", "minimum": 1, "maximum": 900}}
CORE_TOOLS = [
    _tool("godot_project_info", "Inspect the configured Godot project."),
    _tool("godot_scene_inspect", "Inspect structured nodes, resources, scripts, and signals in a project scene.", {"path": {"type": "string"}, "node": {"type": "string"}}, ["path"]),
    _tool("godot_scene_patch", "Preview or apply one controlled scene property change; dry-run defaults to true.", {"path": {"type": "string"}, "node": {"type": "string"}, "property": {"type": "string"}, "value": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, ["path", "node", "property", "value"]),
    _tool("godot_resource_inspect", "Inspect a project resource without modifying it.", {"path": {"type": "string"}}, ["path"]),
    _tool("godot_script_diagnostics", "Return bounded static diagnostics for a GDScript or C# file.", {"path": {"type": "string"}}, ["path"]),
    _tool("godot_runtime_snapshot", "Read one bounded page of runtime nodes and selected performance monitors.", {
        "monitors": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
        "offset": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
        "max_depth": {"type": "integer", "minimum": 0, "maximum": 16, "default": 8},
    }),
    _tool("godot_runtime_property", "Read one runtime Node property from the read-only bridge.", {"node": {"type": "string"}, "property": {"type": "string"}}, ["node", "property"]),
    _tool("godot_runtime_logs", "Read the bounded observer log ring buffer."),
    _tool("godot_runtime_screenshot", "Capture a runtime viewport PNG as base64."),
    _tool("godot_run_check", "Run a command from the explicit project allowlist.", {"name": {"type": "string"}, **TIMEOUT}, ["name"]),
    _tool("godot_stagehand_scenario", "Run a configured Stagehand scenario and parse JUnit/visual diff outputs.", {"scenario": {"type": "string"}, **TIMEOUT}, ["scenario"]),
    _tool("godot_runtime_errors", "Return warning and error entries from the observer."),
    _tool("godot_resource_leaks", "Find RID, ObjectDB, and resource teardown warnings."),
    _tool("godot_validate_scene", "Validate a project scene.", {"path": {"type": "string"}}, ["path"]),
    _tool("godot_validate_autoload", "Validate project autoload paths."),
]


def _enabled_extensions(tools: GodotTools) -> list[str]:
    try:
        configured = tools._load_config().get("extensions", [])
    except ToolError:
        return []
    return configured if isinstance(configured, list) else []


def tool_catalog(tools: GodotTools) -> list[dict[str, Any]]:
    result = [*CORE_TOOLS]
    if "grand_sire" in _enabled_extensions(tools):
        from .extensions.grand_sire import tool_specs
        result.extend(tool_specs())
    return result


def response(request_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": request_id}
    payload["error" if error else "result"] = error if error else result
    return payload


def dispatch(tools: GodotTools, message: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return response(None, error={"code": -32600, "message": "Invalid Request"})
    method, request_id = message.get("method"), message.get("id")
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return response(request_id, error={"code": -32600, "message": "Invalid Request"})
    is_notification = "id" not in message
    if method == "initialize":
        result = response(request_id, {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "godot-dev-mcp", "version": "0.2.0"}})
        return None if is_notification else result
    if method == "tools/list":
        result = response(request_id, {"tools": tool_catalog(tools)})
        return None if is_notification else result
    if method != "tools/call":
        result = response(request_id, error={"code": -32601, "message": f"Method not found: {method}"})
        return None if is_notification else result
    params = message.get("params", {})
    arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
    name = params.get("name") if isinstance(params, dict) else None
    try:
        handlers: dict[str, Callable[[], Any]] = {
            "godot_project_info": tools.project_info,
            "godot_scene_inspect": lambda: tools.scene_inspect(arguments["path"], arguments.get("node")),
            "godot_scene_patch": lambda: tools.scene_patch(arguments["path"], arguments["node"], arguments["property"], arguments["value"], dry_run=arguments.get("dry_run", True)),
            "godot_resource_inspect": lambda: tools.resource_inspect(arguments["path"]),
            "godot_script_diagnostics": lambda: tools.script_diagnostics(arguments["path"]),
            "godot_runtime_snapshot": lambda: tools.runtime_snapshot(
                arguments.get("monitors"),
                arguments.get("offset", 0),
                arguments.get("limit", 100),
                arguments.get("max_depth", 8),
            ),
            "godot_runtime_property": lambda: tools.runtime_property(arguments["node"], arguments["property"]),
            "godot_runtime_logs": tools.runtime_logs,
            "godot_runtime_screenshot": tools.runtime_screenshot,
            "godot_run_check": lambda: tools.run_check(arguments["name"], arguments.get("timeout", 300)),
            "godot_stagehand_scenario": lambda: tools.stagehand_scenario(arguments["scenario"], arguments.get("timeout", 300)),
            "godot_runtime_errors": tools.runtime_errors,
            "godot_resource_leaks": tools.resource_leaks,
            "godot_validate_scene": lambda: tools.validate_scene(arguments["path"]),
            "godot_validate_autoload": tools.validate_autoload,
        }
        if "grand_sire" in _enabled_extensions(tools):
            from .extensions.grand_sire import handlers as extension_handlers
            handlers.update(extension_handlers(tools, arguments))
        if name not in handlers:
            raise ToolError(f"Unknown or disabled tool: {name}")
        content = json.dumps(handlers[name](), ensure_ascii=False, indent=2)
        result = response(request_id, {"content": [{"type": "text", "text": content}], "isError": False})
        return None if is_notification else result
    except (ToolError, KeyError, TypeError, ValueError) as exc:
        result = response(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        return None if is_notification else result


def dispatch_message(tools: GodotTools, message: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
    if not isinstance(message, list):
        return dispatch(tools, message)
    if not message:
        return response(None, error={"code": -32600, "message": "Invalid Request"})
    results = [result for item in message if (result := dispatch(tools, item)) is not None]
    return results or None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--bridge-url", default="http://127.0.0.1:7331")
    args = parser.parse_args()
    tools = GodotTools(args.project, args.bridge_url)
    for line in sys.stdin:
        try:
            result = dispatch_message(tools, json.loads(line))
            if result is not None:
                print(json.dumps(result, separators=(",", ":")), flush=True)
        except json.JSONDecodeError as exc:
            print(json.dumps(response(None, error={"code": -32700, "message": str(exc)})), flush=True)


if __name__ == "__main__":
    main()
