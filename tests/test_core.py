import json
import tempfile
import unittest
from pathlib import Path

from gs_mcp.core import GrandSireTools, ToolError
from gs_mcp.server import dispatch


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "project.godot").write_text('[application]\nconfig/name="Test"\n', encoding="utf-8")
        (self.root / "main.tscn").write_text('[gd_scene]\n[node name="Main" type="Node"]\n', encoding="utf-8")
        self.tools = GrandSireTools(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_scene_inspection(self):
        result = self.tools.scene_inspect("main.tscn")
        self.assertEqual(result["node_count"], 1)

    def test_path_escape_is_rejected(self):
        with self.assertRaises(ToolError):
            self.tools.scene_inspect("../escape.tscn")

    def test_allowlisted_check(self):
        (self.root / "gs-mcp.json").write_text(json.dumps({"checks": {"ok": ["python3", "-c", "print('PASS')"]}}), encoding="utf-8")
        result = self.tools.run_check("ok")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("PASS", result["stdout"])

    def test_unknown_check_is_rejected(self):
        (self.root / "gs-mcp.json").write_text(json.dumps({"checks": {}}), encoding="utf-8")
        with self.assertRaises(ToolError):
            self.tools.run_check("anything")

    def test_mcp_tools_list(self):
        result = dispatch(self.tools, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertGreaterEqual(len(result["result"]["tools"]), 5)


if __name__ == "__main__":
    unittest.main()

