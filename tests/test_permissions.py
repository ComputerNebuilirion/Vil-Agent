"""安全边界测试：L2 权限决策（PermissionSystem.evaluate）"""
import json
import tempfile
import unittest
from pathlib import Path

from VilAgent.safety.permissions import (
    ALLOW,
    ASK,
    DENY,
    PermissionSystem,
    Rule,
)


class TestDefaultPosture(unittest.TestCase):
    def test_readonly_allow_even_untrusted(self):
        ps = PermissionSystem(trust=False)
        self.assertEqual(ps.evaluate("read_file", {}, readonly=True), ALLOW)

    def test_write_untrusted_deny(self):
        ps = PermissionSystem(trust=False)
        self.assertEqual(
            ps.evaluate("write_file", {"path": "a"}, readonly=False), DENY)

    def test_write_trusted_allow(self):
        ps = PermissionSystem(trust=True)
        self.assertEqual(
            ps.evaluate("write_file", {"path": "a"}, readonly=False), ALLOW)

    def test_exec_trusted_ask(self):
        ps = PermissionSystem(trust=True)
        self.assertEqual(
            ps.evaluate("run_command", {"command": "ls"}, readonly=False), ASK)

    def test_delete_trusted_ask(self):
        ps = PermissionSystem(trust=True)
        self.assertEqual(
            ps.evaluate("delete_file", {"path": "a"}, readonly=False), ASK)

    def test_high_risk_trusted_ask_untrusted_deny(self):
        self.assertEqual(
            PermissionSystem(trust=True).evaluate(
                "run_python", {"code": "x"}, readonly=False, risk="high"),
            ASK)
        self.assertEqual(
            PermissionSystem(trust=False).evaluate(
                "run_python", {"code": "x"}, readonly=False, risk="high"),
            DENY)


class TestBuiltinDenyHardFloor(unittest.TestCase):
    def test_dangerous_command_denied_even_trusted(self):
        ps = PermissionSystem(trust=True)
        self.assertEqual(
            ps.evaluate("run_command", {"command": "rm -rf /"},
                        readonly=False),
            DENY)

    def test_allow_rule_cannot_override_builtin_deny(self):
        # 用户规则即使 allow *，也不能放行内置硬底线
        ps = PermissionSystem(trust=True)
        ps.user_rules.append(
            Rule(tool="run_command", pattern="*", action=ALLOW))
        self.assertEqual(
            ps.evaluate("run_command", {"command": "rm -rf /"},
                        readonly=False),
            DENY)


class TestUserRules(unittest.TestCase):
    def test_rules_file_allow(self):
        with tempfile.TemporaryDirectory() as d:
            rf = Path(d) / "permissions.json"
            rf.write_text(json.dumps({
                "rules": [
                    {"tool": "run_command", "pattern": "git *",
                     "action": "allow"},
                ]
            }), encoding="utf-8")
            ps = PermissionSystem(rules_file=rf, trust=True)
            self.assertIsNone(ps.load_error)
            self.assertEqual(
                ps.evaluate("run_command", {"command": "git status"},
                            readonly=False),
                ALLOW)
            # 未命中规则 → 回落到默认姿态 ask
            self.assertEqual(
                ps.evaluate("run_command", {"command": "ls"}, readonly=False),
                ASK)

    def test_broken_rules_file_records_error_without_raising(self):
        with tempfile.TemporaryDirectory() as d:
            rf = Path(d) / "permissions.json"
            rf.write_text("{ not valid json", encoding="utf-8")
            ps = PermissionSystem(rules_file=rf, trust=True)
            self.assertIsNotNone(ps.load_error)
            # 规则文件坏了不应拖垮决策
            self.assertEqual(
                ps.evaluate("write_file", {"path": "a"}, readonly=False),
                ALLOW)


if __name__ == "__main__":
    unittest.main()
