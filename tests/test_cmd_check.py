"""安全边界测试：命令危险模式匹配（cmd_check）"""
import unittest

from VilAgent.safety.cmd_check import (
    classify_command,
    match_dangerous_command,
    normalize_command,
)


class TestNormalize(unittest.TestCase):
    def test_lowercase_and_collapse_whitespace(self):
        self.assertEqual(normalize_command("  RM\n  -RF  /  "), "rm -rf /")


class TestMatchDangerousCommand(unittest.TestCase):
    def test_plain_safe_commands(self):
        for cmd in ("ls -la", "git status", "echo hello",
                    "git log --format=%H", "python -c 'print(1)'"):
            self.assertIsNone(match_dangerous_command(cmd), cmd)

    def test_rm_recursive(self):
        self.assertIsNotNone(match_dangerous_command("rm -rf /"))

    def test_rm_case_insensitive(self):
        self.assertIsNotNone(match_dangerous_command("RM -RF /tmp"))

    def test_prefixed_sudo(self):
        self.assertIsNotNone(match_dangerous_command("sudo rm -rf /"))

    def test_chained_commands(self):
        self.assertIsNotNone(match_dangerous_command("echo hi && rm -rf /"))

    def test_windows_del(self):
        self.assertIsNotNone(match_dangerous_command("del important.txt"))

    def test_windows_del_after_chain(self):
        self.assertIsNotNone(match_dangerous_command("mkdir x && del x"))

    def test_format_not_confused_with_argument(self):
        # `--format` 不是命令起始，不应误伤
        self.assertIsNone(match_dangerous_command("git log --format=oneline"))

    def test_empty(self):
        self.assertIsNone(match_dangerous_command(""))


class TestClassifyCommand(unittest.TestCase):
    def test_low(self):
        r = classify_command("ls -la")
        self.assertEqual(r["risk"], "low")

    def test_network_medium(self):
        r = classify_command("curl https://example.com")
        self.assertEqual(r["risk"], "medium")
        self.assertEqual(r["cmd_type"], "network")

    def test_dangerous_high(self):
        r = classify_command("rm -rf /")
        self.assertEqual(r["risk"], "high")
        self.assertEqual(r["cmd_type"], "exec")


if __name__ == "__main__":
    unittest.main()
