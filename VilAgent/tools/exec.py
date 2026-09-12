"""命令执行工具"""
import subprocess
import sys

from . import tool
from ..i18n import L
from ..safety import match_dangerous_command


@tool(
    name="run_command",
    description=L("执行 shell 命令并返回输出（需要 --trust）", "Run a shell command and return its output (requires --trust)"),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": L("要执行的 shell 命令", "Shell command to run")},
            "timeout": {"type": "number", "description": L("超时秒数", "Timeout in seconds"), "default": 30},
        },
        "required": ["command"],
    },
    requires_trust=True,
)
def run_command(command: str, timeout: float = 30, _ctx=None) -> str:
    ws = str(_ctx["workspace"])

    # 危险命令黑名单（与 L2 权限层硬底线共用同一匹配器，避免两套口径不一致）
    hit = match_dangerous_command(command)
    if hit:
        return f"Error: blocked dangerous command pattern: {hit}"

    # Windows 下 shell=True 走 cmd.exe，默认 codepage 936 (GBK)
    # 先 chcp 65001 切到 UTF-8 codepage，让命令输出 UTF-8 字节
    # 这样我们用 encoding="utf-8" 解码才正确（否则中文乱码）
    if sys.platform == "win32":
        command = f"chcp 65001 >nul 2>&1 && {command}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=ws,
            capture_output=True,
            text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        if not output:
            return "(no output)"
        # 截断过长输出
        if len(output) > 5000:
            return output[:5000] + f"\n... (truncated, {len(output)} total chars)"
        return output
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
