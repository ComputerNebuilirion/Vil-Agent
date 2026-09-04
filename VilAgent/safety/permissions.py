"""L2 权限层：工具调用前的 allow/ask/deny 决策（Windows 上的实际安全边界）

设计哲学（参考 Claude Code 在 Windows 上的做法）：没有 OS 级沙箱时，
per-action 权限评估就是安全边界。trust 控制默认姿态，规则文件细化。
"""
import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path

ALLOW = "allow"
ASK = "ask"
DENY = "deny"


@dataclass
class Rule:
    tool: str | None = None      # None = 匹配任意工具
    pattern: str | None = None   # fnmatch 模式，匹配 args["path"] 或 args["command"]
    action: str = ASK            # allow / ask / deny


# 内置默认规则（空表示全走 default 分支）
_BUILTIN_RULES: list[Rule] = [
    # 例：run_command 匹配 rm/del/format 一律拒（兜底，exec.py 已有黑名单，这里再加一层）
    Rule(tool="run_command", pattern="rm *", action=DENY),
    Rule(tool="run_command", pattern="del *", action=DENY),
    Rule(tool="run_command", pattern="format *", action=DENY),
    Rule(tool="run_command", pattern="mkfs*", action=DENY),
]


class PermissionSystem:
    """
    权限决策器

    决策顺序：
      1. readonly 工具 → allow
      2. 规则文件命中 → 该规则的 action
      3. 内置规则命中 → 该 action
      4. 默认：
         - risk=high → deny
         - trust=False → deny（写/执行类）
         - trust=True  → ask
    """

    def __init__(self, rules_file: Path | str | None = None, trust: bool = False):
        self.trust = trust
        self.rules: list[Rule] = list(_BUILTIN_RULES)
        if rules_file:
            p = Path(rules_file)
            if p.exists():
                self._load_file(p)

    def _load_file(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        # 用户规则插在内置规则之前，优先级更高
        user_rules: list[Rule] = []
        for r in data.get("rules", []):
            user_rules.append(Rule(
                tool=r.get("tool"),
                pattern=r.get("pattern"),
                action=r.get("action", ASK),
            ))
        self.rules = user_rules + self.rules

    def evaluate(self, tool_name: str, args: dict,
                 readonly: bool, risk: str | None = None) -> str:
        # 1. 只读工具放行
        if readonly:
            return ALLOW

        # 2 & 3. 规则匹配（用户规则先，内置兜底）
        for rule in self.rules:
            if rule.tool and rule.tool != tool_name:
                continue
            if rule.pattern:
                target = args.get("path") or args.get("command") or args.get("code") or ""
                if not fnmatch.fnmatch(str(target), rule.pattern):
                    continue
            return rule.action

        # 4. 默认
        if risk == "high":
            return DENY
        if not self.trust:
            return DENY
        return ASK
