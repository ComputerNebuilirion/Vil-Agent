"""安全边界测试：路径穿越防护（is_path_safe）"""
import tempfile
import unittest
from pathlib import Path

from VilAgent.safety import is_path_safe


class TestIsPathSafe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_relative_inside(self):
        self.assertTrue(is_path_safe("a/b.txt", self.ws))

    def test_workspace_itself(self):
        self.assertTrue(is_path_safe(".", self.ws))

    def test_absolute_inside(self):
        p = self.ws / "sub" / "f.txt"
        self.assertTrue(is_path_safe(str(p), self.ws))

    def test_parent_escape(self):
        self.assertFalse(is_path_safe("../evil.txt", self.ws))

    def test_deep_parent_escape(self):
        self.assertFalse(is_path_safe("a/../../evil.txt", self.ws))

    def test_absolute_outside(self):
        outside = Path(self._tmp.name).parent / "definitely_outside.txt"
        self.assertFalse(is_path_safe(str(outside), self.ws))

    def test_sibling_prefix_not_treated_as_inside(self):
        # ws = .../x，.../xevil 不能被 ws in target.parents 误判为内部
        evil = self.ws.parent / (self.ws.name + "evil.txt")
        self.assertFalse(is_path_safe(str(evil), self.ws))


if __name__ == "__main__":
    unittest.main()
