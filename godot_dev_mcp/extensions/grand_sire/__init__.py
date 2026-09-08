from __future__ import annotations

from typing import Any, Callable

from ...core import GodotTools


CHECKS = {
    "gs_test_auction": "auction",
    "gs_run_m2": "m2",
    "gs_run_pvp": "pvp",
    "gs_run_replay_determinism": "replay_determinism",
}


def _spec(name: str, description: str, properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


def tool_specs() -> list[dict[str, Any]]:
    timeout = {"timeout": {"type": "integer", "minimum": 1, "maximum": 900}}
    specs = [_spec(name, f"Run the allowlisted Grand Sire {check} check.", timeout) for name, check in CHECKS.items()]
    specs.extend([
        _spec("gs_runtime_errors", "Return Grand Sire runtime warnings and errors."),
        _spec("gs_resource_leaks", "Find Grand Sire RID, ObjectDB, and resource teardown warnings."),
        _spec("gs_validate_scene", "Validate a Grand Sire scene.", {"path": {"type": "string"}}, ["path"]),
        _spec("gs_validate_autoload", "Validate Grand Sire autoload paths."),
    ])
    return specs


def handlers(tools: GodotTools, arguments: dict[str, Any]) -> dict[str, Callable[[], Any]]:
    result: dict[str, Callable[[], Any]] = {
        name: (lambda check_name=check: tools.run_check(check_name, arguments.get("timeout", 300)))
        for name, check in CHECKS.items()
    }
    result.update({
        "gs_runtime_errors": tools.runtime_errors,
        "gs_resource_leaks": tools.resource_leaks,
        "gs_validate_scene": lambda: tools.validate_scene(arguments["path"]),
        "gs_validate_autoload": tools.validate_autoload,
    })
    return result
