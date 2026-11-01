#!/usr/bin/env python3
"""Contract tests for the one-connection MCP cutover."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "cutover", Path(__file__).with_name("route_mcp_to_linux.py"))
cutover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cutover)


class MCPMigrationTests(unittest.TestCase):
    def test_preserves_unrelated_servers_and_permissions(self):
        old = [{
            "url": "https://development-server-dev-02.abit.ai/mcp?token=placeholder",
            "path": "", "type": "mcp", "auth_type": "none",
            "config": {"enable": True, "access_grants": [{"group_id": "test", "access": "read"}]},
            "info": {"id": "development-server-dev-02", "name": "Development Server Dev-02"}
        }, {"url": "http://127.0.0.1:9998/mcp", "info": {"id": "unrelated"}}]
        new, changed = cutover.switch_connection(old)
        self.assertTrue(changed)
        self.assertEqual(new[0]["url"], cutover.LOCAL_MCP)
        self.assertEqual(new[0]["info"]["id"], "abit-dev-linux")
        self.assertEqual(new[0]["config"], old[0]["config"])
        self.assertEqual(new[1], old[1])
        self.assertEqual(old[0]["info"]["id"], "development-server-dev-02")
        self.assertFalse(cutover.switch_connection(new)[1])

    def test_refuses_unexpected_hostname_or_duplicate(self):
        item = {"url": "https://unexpected.example/mcp", "type": "mcp",
                "auth_type": "none", "info": {"id": cutover.OLD_ID}}
        with self.assertRaises(ValueError):
            cutover.switch_connection([item])
        item["url"] = "https://development-server-dev-02.abit.ai/mcp"
        with self.assertRaises(ValueError):
            cutover.switch_connection([item, {"info": {"id": cutover.NEW_ID}}])


if __name__ == "__main__":
    unittest.main()
