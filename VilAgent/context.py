"""上下文采集：workspace + git 状态（精简，不放文件树）

文件探索交给 list_dir / search_code 工具按需进行，避免 system prompt 臃胀
（参考 Claude Code：systemContext 只放 git status / 分支，不预加载文件）。
"""
import platform
import subprocess
from pathlib import Path


class Context:
    """
    采集工作区上下文信息

    Phase 0（终端版）：workspace 路径、git 分支、git 状态
    Phase 2（插件版）：当前文件、光标位置、诊断信息（通过插件获取）
    """

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).resolve()

    def collect(self) -> dict:
        return {
            "workspace": str(self.workspace),
            "platform": platform.system(),
            "git_branch": self._git_branch(),
            "git_status": self._git_status(),
        }

    def render_for_prompt(self) -> str:
        ctx = self.collect()
        lines = [
            "## 工作区上下文",
            f"- 路径: {ctx['workspace']}",
            f"- 平台: {ctx['platform']}",
            f"- Git 分支: {ctx['git_branch'] or '(无)'}",
        ]

        gs = ctx["git_status"]
        if gs:
            gs_lines = gs.splitlines()
            # 改动多时只取前 30 行，避免 status 本身臃胀
            if len(gs_lines) > 30:
                lines.append(f"- Git 状态 ({len(gs_lines)} 项，仅列前 30):")
                lines.append("```\n" + "\n".join(gs_lines[:30]) + "\n```")
            else:
                lines.append(f"- Git 状态:\n```\n{gs}\n```")
        else:
            lines.append("- Git 状态: clean")

        lines.append("- 提示: 用 list_dir / search_code 工具按需浏览文件，不要假定文件结构。")
        return "\n".join(lines)

    def _git_status(self) -> str:
        try:
            r = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.workspace),
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=5,
            )
            return r.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def _git_branch(self) -> str:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(self.workspace),
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=5,
            )
            return r.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""
