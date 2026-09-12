"""工具注册中心：@tool 装饰器 + execute_tool 执行器"""
import json
from typing import Callable

from ..i18n import resolve as _resolve_i18n

_REGISTRY: dict[str, dict] = {}


def tool(name: str, description: str, parameters: dict,
         requires_trust: bool = False, readonly: bool = False):
    """注册一个工具到全局 registry"""
    def decorator(func: Callable):
        _REGISTRY[name] = {
            "func": func,
            "requires_trust": requires_trust,
            "readonly": readonly,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                }
            }
        }
        return func
    return decorator


def get_tool_schemas(readonly_only: bool = False) -> list[dict]:
    """返回 OpenAI tool 格式的 schema 列表（description/参数描述按当前语言解析）"""
    return [
        _resolve_i18n(v["schema"]) for v in _REGISTRY.values()
        if not readonly_only or v["readonly"]
    ]


def tool_is_readonly(name: str) -> bool:
    """查询单个工具是否只读（供 L2 权限层决策）"""
    entry = _REGISTRY.get(name)
    return bool(entry and entry["readonly"])


def get_tool_entry(name: str) -> dict | None:
    """获取工具注册项（含 requires_trust / readonly）"""
    return _REGISTRY.get(name)


def execute_tool(name: str, args: dict, ctx: dict | None = None) -> str:
    """
    执行工具，返回字符串结果

    :param name: 工具名
    :param args: 参数 dict（来自 LLM 的 JSON arguments）
    :param ctx: 运行时上下文 (workspace, trust, mode)
    :return: 工具执行结果字符串
    """
    if name not in _REGISTRY:
        return f"Error: unknown tool '{name}'"

    entry = _REGISTRY[name]
    ctx = ctx or {}

    # trust 检查
    if entry["requires_trust"] and not ctx.get("trust"):
        return f"Error: tool '{name}' requires --trust (current mode is read-only)"

    try:
        result = entry["func"](**args, _ctx=ctx)
        return str(result)
    except TypeError as e:
        return f"Error: bad arguments for '{name}': {e}"
    except Exception as e:
        return f"Error executing '{name}': {type(e).__name__}: {e}"


# 导入子模块触发 @tool 注册
from . import file as _file  # noqa: E402,F401
from . import exec as _exec  # noqa: E402,F401
from . import git as _git  # noqa: E402,F401
from . import python_exec as _python_exec  # noqa: E402,F401
from . import search as _search  # noqa: E402,F401
from . import edit as _edit  # noqa: E402,F401
from . import todo as _todo  # noqa: E402,F401
