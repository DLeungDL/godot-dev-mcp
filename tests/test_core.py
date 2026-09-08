import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from godot_dev_mcp.core import GodotTools, ToolError
from godot_dev_mcp.server import CORE_TOOLS, dispatch


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

    def test_scene_patch_dry_run_and_apply(self):
        result = self.tools.scene_patch("main.tscn", "Label", "text", '"New"')
        self.assertTrue(result["dry_run"])
        self.assertNotIn('text = "New"', (self.root / "main.tscn").read_text(encoding="utf-8"))
        result = self.tools.scene_patch("main.tscn", "Label", "text", '"New"', dry_run=False)
        self.assertFalse(result["dry_run"])
        self.assertIn('text = "New"', (self.root / "main.tscn").read_text(encoding="utf-8"))

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

    def test_runtime_snapshot_forwards_bounded_pagination(self):
        with patch.object(self.tools, "_bridge_json", return_value={"nodes": []}) as bridge:
            self.tools.runtime_snapshot(["fps"], offset=20, limit=25, max_depth=4)
        bridge.assert_called_once_with("/snapshot", {
            "offset": "20", "limit": "25", "max_depth": "4", "monitors": "fps"
        })
        for kwargs in ({"offset": -1}, {"limit": 0}, {"limit": 501}, {"max_depth": 17}):
            with self.assertRaises(ToolError):
                self.tools.runtime_snapshot(**kwargs)

    def test_grand_sire_tools_are_opt_in(self):
        (self.root / "godot-dev-mcp.json").write_text(json.dumps({"extensions": ["grand_sire"], "checks": {}}), encoding="utf-8")
        listed = dispatch(self.tools, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        extension_names = {tool["name"] for tool in listed["result"]["tools"] if tool["name"].startswith("gs_")}
        self.assertEqual(extension_names, {"gs_test_auction", "gs_run_m2", "gs_run_pvp",
                                           "gs_run_replay_determinism", "gs_runtime_errors",
                                           "gs_resource_leaks", "gs_validate_scene", "gs_validate_autoload"})


if __name__ == "__main__":
    unittest.main()
