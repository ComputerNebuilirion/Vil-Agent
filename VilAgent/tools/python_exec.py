"""Python 代码执行工具：串联 L1 分类 → L2 权限 → L3 沙箱"""
from . import tool
from ..safety import classify, sandbox_run


@tool(
    name="run_python",
    description="在沙箱里执行 Python 代码（需 --trust；高危代码会被 L1 预检拦截）",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 Python 源码"},
            "timeout": {"type": "number", "description": "超时秒数", "default": 15},
            "mem_mb": {"type": "integer", "description": "内存上限 MB", "default": 256},
        },
        "required": ["code"],
    },
    requires_trust=True,
)
def run_python(code: str, timeout: float = 15, mem_mb: int = 256, _ctx=None) -> str:
    # L1 预检：高危直接拒（ctypes/exec/eval 这类能逃逸 Job Object）
    cls = classify(code)
    if cls["risk"] == "high":
        return (f"Error: L1 预检拦截（risk=high, type={cls['cmd_type']}）: "
                + "; ".join(cls["reasons"]))

    # L3 沙箱执行（Windows Job Object + 环境清理 + TerminateJobObject）
    rc, out = sandbox_run(code, timeout=timeout, mem_mb=mem_mb)
    return f"[exit={rc} risk={cls['risk']} type={cls['cmd_type']}]\n{out}"
