import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from godot_dev_mcp.core import GodotTools, ToolError
from godot_dev_mcp.server import CORE_TOOLS, dispatch, dispatch_message


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "project.godot").write_text(
            '[application]\nconfig/name="Test"\n\n[autoload]\nState="*res://state.gd"\n', encoding="utf-8"
        )
        (self.root / "state.gd").write_text("extends Node\n", encoding="utf-8")
        (self.root / "main.tscn").write_text(
            '[gd_scene load_steps=2 format=3]\n\n'
            '[ext_resource path="res://state.gd" type="Script" id="1"]\n\n'
            '[node name="Main" type="Node"]\nscript = ExtResource("1")\n'
            '[node name="Label" type="Label" parent="."]\ntext = "Old"\n'
            'metadata/config = {\n"enabled": true,\n"items": [1, 2]\n}\n'
            '[connection signal="ready" from="." to="." method="_ready"]\n', encoding="utf-8"
        )
        self.tools = GodotTools(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_scene_inspection_includes_authoring_metadata(self):
        result = self.tools.scene_inspect("main.tscn")
        self.assertEqual(result["node_count"], 2)
        self.assertEqual(result["resource_count"], 1)
        self.assertEqual(result["connection_count"], 1)
        self.assertEqual(result["scripts"], ["res://state.gd"])
        self.assertEqual(result["structured_nodes"][0]["scene_path"], "Main")
        self.assertEqual(result["structured_nodes"][1]["scene_path"], "Main/Label")

        selected = self.tools.scene_inspect("main.tscn", "Main/Label")["selected_node"]
        self.assertEqual(selected["type"], "Label")
        self.assertEqual(selected["parent"], ".")
        self.assertEqual(selected["properties"]["text"], '"Old"')
        self.assertEqual(selected["properties"]["metadata/config"], '{\n"enabled": true,\n"items": [1, 2]\n}')
        self.assertFalse(selected["properties_truncated"])
        with self.assertRaises(ToolError):
            self.tools.scene_inspect("main.tscn", "Missing")

    def test_scene_inspection_bounds_large_property_values(self):
        scene = self.root / "main.tscn"
        contents = scene.read_text(encoding="utf-8")
        large_properties = "".join(f'large_{index} = "{"x" * 5000}"\n' for index in range(9))
        contents = contents.replace("[connection ", large_properties + "[connection ")
        scene.write_text(contents, encoding="utf-8")
        selected = self.tools.scene_inspect("main.tscn", "Main/Label")["selected_node"]
        self.assertEqual(len(selected["properties"]["large_0"]), 4096)
        self.assertTrue(selected["properties_truncated"])
        self.assertIn("large_0", selected["truncated_properties"])
        self.assertIn("large_8", selected["truncated_properties"])
        self.assertNotIn("large_8", selected["properties"])
        self.assertEqual(selected["property_chars"], 32768)

    def test_scene_inspection_bounds_total_property_payload(self):
        lines = ['[node name="Root" type="Node"]']
        for node_index in range(5):
            lines.append(f'[node name="Node{node_index}" type="Node" parent="."]')
            for property_index in range(8):
                lines.append(f'data_{property_index} = "{"x" * 5000}"')

        nodes = GodotTools._structured_scene_nodes(lines)
        self.assertEqual(sum(node["property_chars"] for node in nodes), 131072)
        self.assertEqual(nodes[-1]["properties"], {})
        self.assertTrue(nodes[-1]["properties_truncated"])
        self.assertEqual(len(nodes[-1]["truncated_properties"]), 8)

    def test_scene_patch_dry_run_and_apply(self):
        result = self.tools.scene_patch("main.tscn", "Label", "text", '"New"')
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["scene_path"], "Main/Label")
        self.assertNotIn('text = "New"', (self.root / "main.tscn").read_text(encoding="utf-8"))
        result = self.tools.scene_patch("main.tscn", "Label", "text", '"New"', dry_run=False)
        self.assertFalse(result["dry_run"])
        self.assertIn('text = "New"', (self.root / "main.tscn").read_text(encoding="utf-8"))

    def test_scene_patch_replaces_complete_multiline_property(self):
        scene = self.root / "main.tscn"
        scene.write_text(
            scene.read_text(encoding="utf-8").replace(
                '[connection signal="ready"', '; keep this comment\n[connection signal="ready"'
            ),
            encoding="utf-8",
        )
        result = self.tools.scene_patch(
            "main.tscn", "Main/Label", "metadata/config", '{"enabled": false}', dry_run=False
        )
        self.assertEqual(result["before"], '{\n"enabled": true,\n"items": [1, 2]\n}')
        contents = scene.read_text(encoding="utf-8")
        self.assertIn('metadata/config = {"enabled": false}', contents)
        self.assertNotIn('"items": [1, 2]', contents)
        self.assertIn('; keep this comment', contents)
        self.assertIn('[connection signal="ready"', contents)

    def test_scene_patch_preserves_indented_property_assignments(self):
        scene = self.root / "main.tscn"
        scene.write_text(
            scene.read_text(encoding="utf-8").replace(
                'text = "Old"', '    text = "Old"\n    tooltip_text = "Keep"'
            ),
            encoding="utf-8",
        )
        result = self.tools.scene_patch("main.tscn", "Main/Label", "text", '"New"', dry_run=False)
        self.assertEqual(result["before"], '"Old"')
        contents = scene.read_text(encoding="utf-8")
        self.assertIn('    text = "New"', contents)
        self.assertIn('    tooltip_text = "Keep"', contents)

    def test_scene_patch_rejects_ambiguous_name_and_accepts_scene_path(self):
        (self.root / "duplicate.tscn").write_text(
            '[gd_scene format=3]\n\n'
            '[node name="Main" type="Node"]\n'
            '[node name="PanelA" type="Control" parent="."]\n'
            '[node name="Label" type="Label" parent="PanelA"]\ntext = "A"\n'
            '[node name="PanelB" type="Control" parent="."]\n'
            '[node name="Label" type="Label" parent="PanelB"]\ntext = "B"\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ToolError, "Ambiguous node selector"):
            self.tools.scene_patch("duplicate.tscn", "Label", "text", '"Updated"')

        result = self.tools.scene_patch(
            "duplicate.tscn", "Main/PanelB/Label", "text", '"Updated"', dry_run=False
        )
        self.assertEqual(result["scene_path"], "Main/PanelB/Label")
        contents = (self.root / "duplicate.tscn").read_text(encoding="utf-8")
        self.assertEqual(contents.count('text = "A"'), 1)
        self.assertEqual(contents.count('text = "Updated"'), 1)

    def test_scene_selector_preserves_significant_trailing_whitespace(self):
        (self.root / "whitespace.tscn").write_text(
            '[gd_scene format=3]\n\n'
            '[node name="Root" type="Node"]\n'
            '[node name="Label " type="Label" parent="."]\ntext = "Old"\n',
            encoding="utf-8",
        )
        result = self.tools.scene_patch(
            "whitespace.tscn", "Root/Label ", "text", '"New"', dry_run=False
        )
        self.assertEqual(result["scene_path"], "Root/Label ")
        self.assertIn(
            'text = "New"', (self.root / "whitespace.tscn").read_text(encoding="utf-8")
        )

    def test_scene_selector_prefers_exact_scene_path_for_root(self):
        (self.root / "same-name.tscn").write_text(
            '[gd_scene format=3]\n\n'
            '[node name="Main" type="Node"]\nprocess_mode = 0\n'
            '[node name="Main" type="Node" parent="."]\nprocess_mode = 1\n',
            encoding="utf-8",
        )
        root_result = self.tools.scene_patch(
            "same-name.tscn", "Main", "process_mode", "2", dry_run=False
        )
        child_result = self.tools.scene_patch(
            "same-name.tscn", "Main/Main", "process_mode", "3", dry_run=False
        )
        self.assertEqual(root_result["scene_path"], "Main")
        self.assertEqual(child_result["scene_path"], "Main/Main")
        contents = (self.root / "same-name.tscn").read_text(encoding="utf-8")
        self.assertEqual(contents.count("process_mode = 2"), 1)
        self.assertEqual(contents.count("process_mode = 3"), 1)

    def test_path_escape_and_bad_suffix_are_rejected(self):
        with self.assertRaises(ToolError):
            self.tools.scene_inspect("../escape.tscn")
        with self.assertRaises(ToolError):
            self.tools.script_diagnostics("project.godot")

    def test_bridge_must_be_loopback(self):
        with self.assertRaises(ToolError):
            GodotTools(self.root, "http://example.com:7331")

    def test_allowlisted_check_and_domain_mapping(self):
        config = {"checks": {"ok": [sys.executable, "-c", "print('PASS')"],
                             "auction": [sys.executable, "-c", "print('AUCTION PASS')"]}}
        config["extensions"] = ["grand_sire"]
        (self.root / "godot-dev-mcp.json").write_text(json.dumps(config), encoding="utf-8")
        self.assertEqual(self.tools.run_check("ok")["exit_code"], 0)
        called = dispatch(self.tools, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                       "params": {"name": "gs_test_auction", "arguments": {}}})
        self.assertIn("AUCTION PASS", called["result"]["content"][0]["text"])

    def test_unknown_check_and_malformed_config_are_rejected(self):
        (self.root / "godot-dev-mcp.json").write_text(json.dumps({"checks": {}}), encoding="utf-8")
        with self.assertRaises(ToolError):
            self.tools.run_check("anything")
        (self.root / "godot-dev-mcp.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(ToolError):
            self.tools.run_check("anything")

    def test_timeout_is_structured(self):
        (self.root / "godot-dev-mcp.json").write_text(json.dumps({"checks": {
            "slow": [sys.executable, "-c", "import time; time.sleep(2)"]
        }}), encoding="utf-8")
        result = self.tools.run_check("slow", timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit_code"])

    def test_stagehand_junit_and_visual_diff_are_parsed(self):
        (self.root / "scenario.json").write_text('{"name":"auction","target":{"mode":"connect","port":1234},"steps":[]}', encoding="utf-8")
        (self.root / "junit.xml").write_text('<testsuite tests="2" failures="1" errors="0" skipped="0"/>', encoding="utf-8")
        (self.root / "visual.json").write_text('{"changed_pixels": 12}', encoding="utf-8")
        (self.root / "report.json").write_text('{"status":"failed","exit_code":5}', encoding="utf-8")
        config = {"stagehand": {"command": [sys.executable, "-c", "print('scenario complete')", "--"],
                                "junit": "junit.xml", "visual_diff": "visual.json", "report": "report.json"}}
        (self.root / "godot-dev-mcp.json").write_text(json.dumps(config), encoding="utf-8")
        result = self.tools.stagehand_scenario("scenario.json")
        self.assertEqual(result["junit"]["tests"], 2)
        self.assertEqual(result["visual_diff"]["changed_pixels"], 12)
        self.assertEqual(result["stagehand_report"]["exit_code"], 5)

    def test_resource_script_and_autoload_diagnostics(self):
        self.assertEqual(self.tools.resource_inspect("main.tscn")["ext_resources"][0].startswith("[ext_resource"), True)
        self.assertEqual(self.tools.script_diagnostics("state.gd")["language"], "GDScript")
        self.assertTrue(self.tools.validate_autoload()["valid"])

    def test_mcp_protocol_and_tool_catalog(self):
        init = dispatch(self.tools, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init["result"]["serverInfo"], {"name": "godot-dev-mcp", "version": "0.2.0"})
        listed = dispatch(self.tools, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(listed["result"]["tools"]), len(CORE_TOOLS))
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertNotIn("gs_test_auction", names)
        self.assertTrue({"godot_runtime_errors", "godot_validate_autoload"} <= names)

    def test_mcp_batch_executes_notifications_without_responding(self):
        result = dispatch_message(self.tools, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "tools/call", "params": {
                "name": "godot_scene_patch",
                "arguments": {"path": "main.tscn", "node": "Label", "property": "text", "value": '"Notification"', "dry_run": False},
            }},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertIsInstance(result, list)
        self.assertEqual([item["id"] for item in result], [1, 2])
        self.assertIn('text = "Notification"', (self.root / "main.tscn").read_text(encoding="utf-8"))
        self.assertIsNone(dispatch_message(self.tools, [
            {"jsonrpc": "2.0", "method": "notifications/cancelled"}
        ]))
        self.assertEqual(dispatch_message(self.tools, [])["error"]["code"], -32600)
        self.assertEqual(dispatch_message(self.tools, {"id": 9, "method": "tools/list"})["error"]["code"], -32600)

    def test_runtime_snapshot_forwards_bounded_pagination(self):
        with patch.object(self.tools, "_bridge_json", return_value={"nodes": []}) as bridge:
            self.tools.runtime_snapshot(["fps"], offset=20, limit=25, max_depth=4)
        bridge.assert_called_once_with("/snapshot", {
            "offset": "20", "limit": "25", "max_depth": "4", "monitors": "fps"
        })
        for kwargs in ({"offset": -1}, {"limit": 0}, {"limit": 501}, {"max_depth": 17}):
            with self.assertRaises(ToolError):
                self.tools.runtime_snapshot(**kwargs)

    def test_runtime_errors_and_resource_leaks_filter_structured_logs(self):
        entries = [
            {"level": "info", "message": "ready"},
            {"level": "warning", "message": "RID allocations still in use"},
            {"level": "error", "message": "script failed"},
        ]
        with patch.object(self.tools, "runtime_logs", return_value={"entries": entries}):
            errors = self.tools.runtime_errors()
            leaks = self.tools.resource_leaks()
        self.assertEqual([entry["level"] for entry in errors["entries"]], ["warning", "error"])
        self.assertEqual(leaks, {"entries": [entries[1]], "count": 1})

    def test_grand_sire_tools_are_opt_in(self):
        (self.root / "godot-dev-mcp.json").write_text(json.dumps({"extensions": ["grand_sire"], "checks": {}}), encoding="utf-8")
        listed = dispatch(self.tools, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        extension_names = {tool["name"] for tool in listed["result"]["tools"] if tool["name"].startswith("gs_")}
        self.assertEqual(extension_names, {"gs_test_auction", "gs_run_m2", "gs_run_pvp",
                                           "gs_run_replay_determinism", "gs_runtime_errors",
                                           "gs_resource_leaks", "gs_validate_scene", "gs_validate_autoload"})

    def test_runtime_observer_addon_contract_is_packaged(self):
        repository = Path(__file__).resolve().parents[1]
        runtime_script = repository / "addons" / "godot_dev_mcp" / "runtime_observer.gd"
        logger_script = repository / "addons" / "godot_dev_mcp" / "runtime_logger.gd"
        self.assertTrue(runtime_script.is_file())
        self.assertTrue(logger_script.is_file())
        runtime_source = runtime_script.read_text(encoding="utf-8")
        logger_source = logger_script.read_text(encoding="utf-8")
        self.assertIn('_server.listen(_port, "127.0.0.1")', runtime_source)
        self.assertIn('"scope": "game"', runtime_source)
        self.assertIn("OS.is_debug_build()", runtime_source)
        self.assertIn("OS.add_logger(_runtime_logger)", runtime_source)
        self.assertIn("OS.remove_logger(_runtime_logger)", runtime_source)
        self.assertIn("extends Logger", logger_source)
        self.assertIn("Mutex.new()", logger_source)
        self.assertIn("MAX_PENDING_ENTRIES", logger_source)
        project = (repository / "project.godot").read_text(encoding="utf-8")
        self.assertIn('GodotDevMCPRuntimeObserver="*res://addons/godot_dev_mcp/runtime_observer.gd"', project)

class MCPStdioIntegrationTests(unittest.TestCase):
    def test_server_process_completes_stdio_json_rpc_lifecycle(self):
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "project.godot").write_text('[application]\nconfig/name="stdio-test"\n', encoding="utf-8")
            scene = project / "main.tscn"
            scene.write_text('[gd_scene format=3]\n\n[node name="Main" type="Node"]\n', encoding="utf-8")
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}
                }},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "method": "tools/call", "params": {
                    "name": "godot_scene_patch", "arguments": {
                        "path": "main.tscn", "node": "Main", "property": "process_mode",
                        "value": "3", "dry_run": False,
                    }
                }},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": "godot_project_info", "arguments": {}
                }},
                {"jsonrpc": "2.0", "id": 4, "method": "unknown/method"},
            ]
            input_lines = [json.dumps(request) for request in requests]
            input_lines.append("{malformed json")
            completed = subprocess.run(
                [sys.executable, "-m", "godot_dev_mcp.server", "--project", str(project)],
                cwd=repository,
                input="\n".join(input_lines) + "\n",
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            scene_text = scene.read_text(encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([item["id"] for item in responses], [1, 2, 3, 4, None])
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "godot-dev-mcp")
        self.assertEqual(len(responses[1]["result"]["tools"]), len(CORE_TOOLS))
        project_info = json.loads(responses[2]["result"]["content"][0]["text"])
        self.assertEqual(project_info["config_preview"], '[application]\nconfig/name="stdio-test"\n')
        self.assertEqual(responses[3]["error"]["code"], -32601)
        self.assertEqual(responses[4]["error"]["code"], -32700)
        self.assertIn("process_mode = 3", scene_text)


if __name__ == "__main__":
    unittest.main()
