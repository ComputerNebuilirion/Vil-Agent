"""Git 操作工具"""
import subprocess

from . import tool
from ..i18n import L


def _git(args: list[str], workspace: str, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",      # 强制 utf-8：git 输出含中文/emoji，默认 GBK 会崩
            errors="replace",      # 极端情况用 ? 替换，避免整体失败
            timeout=timeout,
        )
        if result.returncode != 0 and result.stderr:
            return f"git error: {result.stderr.strip()}"
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "Error: git timed out"
    except FileNotFoundError:
        return "Error: git not available"


@tool(
    name="git_status",
    description=L("查看 git 工作区状态", "Show git working-tree status"),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    readonly=True,
)
def git_status(_ctx=None) -> str:
    ws = str(_ctx["workspace"])
    status = _git(["status", "--porcelain"], ws)
    if not status:
        return "working tree clean"
    return status


@tool(
    name="git_diff",
    description=L("查看当前修改的 diff", "Show the diff of current changes"),
    parameters={
        "type": "object",
        "properties": {
            "staged": {"type": "boolean", "description": L("查看已暂存的修改", "Show staged changes"), "default": False},
            "file": {"type": "string", "description": L("指定文件路径", "Limit to a specific file path"), "default": ""},
        },
        "required": [],
    },
    readonly=True,
)
def git_diff(staged: bool = False, file: str = "", _ctx=None) -> str:
    ws = str(_ctx["workspace"])
    args = ["diff"]
    if staged:
        args.append("--cached")
    if file:
        args.append(file)
    diff = _git(args, ws)
    if not diff:
        return "no changes"
    # 截断防止过长
    lines = diff.splitlines()
    if len(lines) > 200:
        return "\n".join(lines[:200]) + f"\n... ({len(lines)} total lines)"
    return diff
