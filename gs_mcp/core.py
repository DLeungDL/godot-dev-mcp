from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class ToolError(RuntimeError):
    pass


class GrandSireTools:
    def __init__(self, project: Path, bridge_url: str = "http://127.0.0.1:7331") -> None:
        self.project = project.resolve()
        self.bridge_url = bridge_url.rstrip("/")
        if not (self.project / "project.godot").is_file():
            raise ToolError(f"Not a Godot project: {self.project}")

    def _project_file(self, relative: str, suffix: str | None = None) -> Path:
        candidate = (self.project / relative).resolve()
        if candidate != self.project and self.project not in candidate.parents:
            raise ToolError("Path escapes the project root")
        if suffix and candidate.suffix.lower() != suffix:
            raise ToolError(f"Expected a {suffix} file")
        return candidate

    def project_info(self) -> dict[str, Any]:
        config = (self.project / "project.godot").read_text(encoding="utf-8")
        return {
            "project": str(self.project),
            "has_csharp": any(self.project.glob("*.sln")) or any(self.project.glob("*.csproj")),
            "addons": sorted(p.name for p in (self.project / "addons").glob("*") if p.is_dir())
            if (self.project / "addons").is_dir() else [],
            "config_preview": config[:4000],
        }

    def scene_inspect(self, path: str) -> dict[str, Any]:
        scene = self._project_file(path, ".tscn")
        if not scene.is_file():
            raise ToolError(f"Scene not found: {path}")
        text = scene.read_text(encoding="utf-8")
        nodes = [line for line in text.splitlines() if line.startswith("[node ")]
        return {"path": path, "node_count": len(nodes), "nodes": nodes[:250], "truncated": len(nodes) > 250}

    def runtime_snapshot(self) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(f"{self.bridge_url}/snapshot", timeout=3) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ToolError(f"Godot observation bridge unavailable: {exc}") from exc

    def _load_config(self) -> dict[str, Any]:
        path = self.project / "gs-mcp.json"
        if not path.is_file():
            raise ToolError("Missing gs-mcp.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def _run(self, argv: list[str], timeout: int) -> dict[str, Any]:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ToolError("Configured command must be a non-empty string array")
        try:
            result = subprocess.run(argv, cwd=self.project, text=True, capture_output=True, timeout=timeout, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolError(str(exc)) from exc
        return {"exit_code": result.returncode, "stdout": result.stdout[-20000:], "stderr": result.stderr[-20000:]}

    def run_check(self, name: str, timeout: int = 300) -> dict[str, Any]:
        checks = self._load_config().get("checks", {})
        if name not in checks:
            raise ToolError(f"Unknown check: {name}; allowed: {', '.join(sorted(checks))}")
        return {"check": name, **self._run(checks[name], min(max(timeout, 1), 900))}

    def stagehand_scenario(self, scenario: str, timeout: int = 300) -> dict[str, Any]:
        command = self._load_config().get("stagehand", {}).get("command")
        if not command:
            raise ToolError("Stagehand command is not configured")
        scenario_path = self._project_file(scenario)
        if not scenario_path.is_file():
            raise ToolError(f"Scenario not found: {scenario}")
        return {"scenario": scenario, **self._run([*command, str(scenario_path)], min(max(timeout, 1), 900))}

