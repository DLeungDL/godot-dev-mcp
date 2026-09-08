from __future__ import annotations

import base64
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


class ToolError(RuntimeError):
    pass


class GodotTools:
    def __init__(self, project: Path, bridge_url: str = "http://127.0.0.1:7331") -> None:
        self.project = project.resolve()
        parsed = urllib.parse.urlsplit(bridge_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ToolError("Observation bridge must use HTTP on loopback")
        self.bridge_url = bridge_url.rstrip("/")
        if not (self.project / "project.godot").is_file():
            raise ToolError(f"Not a Godot project: {self.project}")

    def _project_file(self, relative: str, suffix: str | tuple[str, ...] | None = None) -> Path:
        if not isinstance(relative, str) or not relative or "\x00" in relative:
            raise ToolError("Path must be a non-empty string")
        candidate = (self.project / relative).resolve()
        if candidate != self.project and self.project not in candidate.parents:
            raise ToolError("Path escapes the project root")
        if suffix:
            allowed = (suffix,) if isinstance(suffix, str) else suffix
            if candidate.suffix.lower() not in allowed:
                raise ToolError(f"Expected one of: {', '.join(allowed)}")
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
        lines = text.splitlines()
        nodes = [line for line in lines if line.startswith("[node ")]
        resources = [line for line in lines if line.startswith(("[ext_resource ", "[sub_resource "))]
        connections = [line for line in lines if line.startswith("[connection ")]
        scripts = sorted(set(re.findall(r'path="([^"]+\.(?:gd|cs))"', text)))
        return {
            "path": path,
            "node_count": len(nodes),
            "resource_count": len(resources),
            "connection_count": len(connections),
            "nodes": nodes[:250],
            "resources": resources[:250],
            "connections": connections[:250],
            "scripts": scripts,
            "truncated": any(len(items) > 250 for items in (nodes, resources, connections)),
        }

    def scene_patch(self, path: str, node: str, property_name: str, value: str, *, dry_run: bool = True) -> dict[str, Any]:
        scene = self._project_file(path, ".tscn")
        if not scene.is_file():
            raise ToolError(f"Scene not found: {path}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./:]*", property_name):
            raise ToolError("Invalid property name")
        if not isinstance(value, str) or "\n" in value or "\r" in value or len(value) > 4096:
            raise ToolError("Value must be a single Godot resource line value (max 4096 chars)")
        pattern = re.compile(r'^\[node\s+.*name="' + re.escape(node) + r'"(?:\s|\])')
        lines = scene.read_text(encoding="utf-8").splitlines(keepends=True)
        start = next((i for i, line in enumerate(lines) if pattern.match(line.rstrip("\r\n"))), None)
        if start is None:
            raise ToolError(f"Node not found: {node}")
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
        assignment = re.compile(r"^" + re.escape(property_name) + r"\s*=")
        index = next((i for i in range(start + 1, end) if assignment.match(lines[i])), None)
        newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
        replacement = f"{property_name} = {value}{newline}"
        old_value = None
        if index is None:
            index = end
            updated = lines[:index] + [replacement] + lines[index:]
        else:
            old_value = lines[index].split("=", 1)[1].strip()
            updated = [*lines]
            updated[index] = replacement
        summary = {"path": path, "node": node, "property": property_name, "before": old_value, "after": value}
        if not dry_run:
            scene.write_text("".join(updated), encoding="utf-8", newline="")
        return {**summary, "dry_run": dry_run, "changed": old_value != value}

    def resource_inspect(self, path: str) -> dict[str, Any]:
        resource = self._project_file(path, (".tres", ".res", ".tscn"))
        if not resource.is_file():
            raise ToolError(f"Resource not found: {path}")
        if resource.suffix.lower() == ".res":
            return {"path": path, "binary": True, "size": resource.stat().st_size}
        text = resource.read_text(encoding="utf-8")
        return {"path": path, "binary": False, "size": resource.stat().st_size,
                "ext_resources": re.findall(r'^\[ext_resource .*$', text, re.MULTILINE)[:250],
                "sub_resources": re.findall(r'^\[sub_resource .*$', text, re.MULTILINE)[:250]}

    def script_diagnostics(self, path: str) -> dict[str, Any]:
        script = self._project_file(path, (".gd", ".cs"))
        if not script.is_file():
            raise ToolError(f"Script not found: {path}")
        text = script.read_text(encoding="utf-8")
        diagnostics = []
        for number, line in enumerate(text.splitlines(), 1):
            if line.rstrip() != line:
                diagnostics.append({"line": number, "severity": "style", "message": "trailing whitespace"})
            if "TODO" in line or "FIXME" in line:
                diagnostics.append({"line": number, "severity": "info", "message": "unfinished marker"})
        return {"path": path, "language": "GDScript" if script.suffix.lower() == ".gd" else "C#",
                "line_count": len(text.splitlines()), "diagnostics": diagnostics[:250], "truncated": len(diagnostics) > 250}

    def _bridge_json(self, endpoint: str, query: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{self.bridge_url}{endpoint}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ToolError(f"Godot observation bridge unavailable: {exc}") from exc

    def runtime_snapshot(
        self,
        monitors: list[str] | None = None,
        offset: int = 0,
        limit: int = 100,
        max_depth: int = 8,
    ) -> dict[str, Any]:
        if not 0 <= offset <= 100000:
            raise ToolError("Snapshot offset must be between 0 and 100000")
        if not 1 <= limit <= 500:
            raise ToolError("Snapshot limit must be between 1 and 500")
        if not 0 <= max_depth <= 16:
            raise ToolError("Snapshot max_depth must be between 0 and 16")
        query = {"offset": str(offset), "limit": str(limit), "max_depth": str(max_depth)}
        if monitors:
            query["monitors"] = ",".join(monitors)
        return self._bridge_json("/snapshot", query)

    def runtime_property(self, node_path: str, property_name: str) -> dict[str, Any]:
        if not node_path.startswith("/") or not property_name:
            raise ToolError("A rooted node path and property name are required")
        return self._bridge_json("/property", {"node": node_path, "property": property_name})

    def runtime_logs(self) -> dict[str, Any]:
        return self._bridge_json("/logs")

    def runtime_screenshot(self) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(f"{self.bridge_url}/screenshot", timeout=5) as response:
                payload = response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ToolError(f"Godot screenshot unavailable: {exc}") from exc
        return {"mime_type": "image/png", "bytes": len(payload), "base64": base64.b64encode(payload).decode("ascii")}

    def _load_config(self) -> dict[str, Any]:
        path = self.project / "godot-dev-mcp.json"
        if not path.is_file():
            raise ToolError("Missing godot-dev-mcp.json")
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ToolError(f"Malformed {path.name}: {exc}") from exc
        if not isinstance(config, dict):
            raise ToolError(f"{path.name} must contain an object")
        return config

    def _run(self, argv: list[str], timeout: int) -> dict[str, Any]:
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise ToolError("Configured command must be a non-empty string array")
        try:
            result = subprocess.run(argv, cwd=self.project, text=True, capture_output=True, timeout=timeout, shell=False)
        except subprocess.TimeoutExpired as exc:
            return {"exit_code": None, "timed_out": True, "stdout": (exc.stdout or "")[-20000:], "stderr": (exc.stderr or "")[-20000:]}
        except OSError as exc:
            raise ToolError(str(exc)) from exc
        return {"exit_code": result.returncode, "timed_out": False, "stdout": result.stdout[-20000:], "stderr": result.stderr[-20000:]}

    def run_check(self, name: str, timeout: int = 300) -> dict[str, Any]:
        checks = self._load_config().get("checks", {})
        if not isinstance(checks, dict) or name not in checks:
            allowed = ", ".join(sorted(checks)) if isinstance(checks, dict) else ""
            raise ToolError(f"Unknown check: {name}; allowed: {allowed}")
        return {"check": name, **self._run(checks[name], min(max(timeout, 1), 900))}

    def stagehand_scenario(self, scenario: str, timeout: int = 300) -> dict[str, Any]:
        settings = self._load_config().get("stagehand", {})
        command = settings.get("command") if isinstance(settings, dict) else None
        if not command:
            raise ToolError("Stagehand command is not configured")
        scenario_path = self._project_file(scenario, ".json")
        if not scenario_path.is_file():
            raise ToolError(f"Scenario not found: {scenario}")
        result = {"scenario": scenario, **self._run([*command, str(scenario_path)], min(max(timeout, 1), 900))}
        junit = settings.get("junit")
        if junit:
            junit_path = self._project_file(junit, ".xml")
            if junit_path.is_file():
                try:
                    root = ET.parse(junit_path).getroot()
                    result["junit"] = {key: int(root.attrib.get(key, 0)) for key in ("tests", "failures", "errors", "skipped")}
                except (ET.ParseError, ValueError) as exc:
                    result["junit_error"] = str(exc)
        visual = settings.get("visual_diff")
        if visual:
            visual_path = self._project_file(visual, ".json")
            if visual_path.is_file():
                try:
                    result["visual_diff"] = json.loads(visual_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    result["visual_diff_error"] = str(exc)
        report = settings.get("report")
        if report:
            report_path = self._project_file(report, ".json")
            if report_path.is_file():
                try:
                    result["stagehand_report"] = json.loads(report_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    result["stagehand_report_error"] = str(exc)
        return result

    def runtime_errors(self) -> dict[str, Any]:
        return {"entries": [entry for entry in self.runtime_logs().get("entries", [])
                            if str(entry.get("level", "")).lower() in {"error", "warning"}]}

    def resource_leaks(self) -> dict[str, Any]:
        markers = ("rid", "objectdb", "resources still in use", "leak")
        entries = [entry for entry in self.runtime_logs().get("entries", [])
                   if any(marker in str(entry).lower() for marker in markers)]
        return {"entries": entries, "count": len(entries)}

    def validate_scene(self, path: str) -> dict[str, Any]:
        result = self.scene_inspect(path)
        return {**result, "valid": result["node_count"] > 0}

    def validate_autoload(self) -> dict[str, Any]:
        text = (self.project / "project.godot").read_text(encoding="utf-8")
        section = re.search(r"(?ms)^\[autoload\]\s*(.*?)(?=^\[|\Z)", text)
        entries = []
        if section:
            for name, enabled, path in re.findall(r'^([^=\n]+)="(\*?)(res://[^"]+)"', section.group(1), re.MULTILINE):
                local = self._project_file(path.removeprefix("res://"))
                entries.append({"name": name.strip(), "path": path, "enabled": enabled == "*", "exists": local.is_file()})
        return {"entries": entries, "valid": all(item["exists"] for item in entries)}
